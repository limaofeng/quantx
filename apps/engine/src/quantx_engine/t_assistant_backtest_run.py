"""Versioned local shared-account BACKTEST entry point and replay recovery."""

import hashlib
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantEntryReadinessProjection,
  TAssistantExecution,
  stable_manifest_hash,
)
from quantx_infrastructure.services.t_assistant_backtest_store import (
  TAssistantBacktestStore,
)

from quantx_engine.t_assistant_backtest_data import FrozenBacktestDataset
from quantx_engine.t_assistant_backtest_runtime import (
  TAssistantBacktestRuntime,
  json_value,
)
from quantx_engine.t_assistant_backtest_timeline import tick_frames


def backtest_code_evidence(*additional_modules):
  """Bind the actual loaded QuantX implementation files, including dirty edits."""
  pending = [
    __name__,
    TAssistantBacktestRuntime.__module__,
    TAssistantBacktestStore.__module__,
    tick_frames.__module__,
    *additional_modules,
  ]
  files = {}
  while pending:
    name = pending.pop()
    if name in files:
      continue
    module = sys.modules[name]
    path = Path(module.__file__)
    files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for value in vars(module).values():
      dependency = getattr(value, "__module__", "")
      if (
        isinstance(dependency, str)
        and dependency.startswith("quantx_")
        and dependency not in files
        and dependency in sys.modules
      ):
        pending.append(dependency)
  return {
    "implementation_files": files,
    "implementation_hash": stable_manifest_hash(files),
  }


@dataclass(frozen=True)
class BacktestRequest:
  config: TAssistantConfigVersion
  start_at: datetime
  runtime_options: dict

  def runtime(self, execution_id):
    config = self.config
    if (
      config.scorer_mode != "RULE_ONLY"
      or config.entry_authorization != "MANUAL_CONFIRM"
    ):
      raise ValueError("BACKTEST_FROZEN_RULE_ONLY_MANUAL_CONFIG_REQUIRED")
    if self.start_at.tzinfo is None:
      raise ValueError("BACKTEST_AWARE_START_REQUIRED")
    execution = TAssistantExecution(
      execution_id,
      config.config_id,
      config.config_version_id,
      config.version,
      config.config_snapshot_hash,
      f"backtest:{execution_id}",
      "BACKTEST",
      config.entry_authorization,
      config.rollout_stage,
      "RUNNING",
      TAssistantEntryReadinessProjection("READY", (), self.start_at),
      config.policy_version,
      config.feature_schema_version,
      config.scorer_mode,
      started_at=self.start_at,
    )
    if self.runtime_options["parameters"] != dict(config.canonical_payload):
      raise ValueError("BACKTEST_PARAMETERS_NOT_FROZEN")
    return TAssistantBacktestRuntime(execution=execution, **self.runtime_options)


async def execute_backtest(
  *,
  request: BacktestRequest,
  events,
  code_manifest: dict,
  root: Path,
  resume_directory: Path | None = None,
):
  """Recovery re-executes the frozen prefix and compares before any append.

  The explicit request contains no service handles. A new invocation without
  resume_directory always owns a fresh execution, including identical inputs.
  """
  request = deepcopy(request)
  streamed = isinstance(events, FrozenBacktestDataset)
  if streamed:
    data = events.input_manifest
    if set(events.instruments) != set(request.runtime_options["initial_positions"]):
      raise ValueError("BACKTEST_DATA_UNIVERSE_MISMATCH")
    first = datetime.fromtimestamp((data["first_ms"] + data["latency_ms"]) / 1000, UTC)
    ordered = events.events()
  else:
    ordered = [item for _, frame in tick_frames(events) for item in frame]
    if not ordered:
      raise ValueError("BACKTEST_EMPTY_DATA")
    first = ordered[0].decision_time
    data = {
      "count": len(ordered),
      "hash": stable_manifest_hash({"ticks": json_value(ordered)}),
      "start": first.isoformat(),
      "end": ordered[-1].decision_time.isoformat(),
    }
  if first < request.start_at:
    raise ValueError("BACKTEST_DATA_START_INVALID")
  if not code_manifest:
    raise ValueError("BACKTEST_CODE_MANIFEST_REQUIRED")
  frozen = {
    "config": json_value(request),
    "data": data,
    "code": {"declared": code_manifest, **backtest_code_evidence()},
    "broker": json_value(request.runtime_options["broker_parameters"]),
    "timeline": {
      "version": "backtest-tick-timeline.v1",
      "priority": ["TICK", "RECEIPTS", "EXIT", "SNAPSHOT", "RECEIPTS"],
    },
    "initial_account": {
      k: json_value(request.runtime_options[k])
      for k in ("initial_cash", "initial_positions", "initial_buckets")
    },
  }
  store = (
    TAssistantBacktestStore(resume_directory)
    if resume_directory is not None
    else TAssistantBacktestStore.create(root, frozen=frozen)
  )
  if store.manifest["material"]["frozen"] != frozen:
    raise ValueError("BACKTEST_RESUME_INPUT_CHANGED")
  # Verify the whole stored chain, even if a caller supplied fewer input frames.
  prior_frame_count = sum(1 for _ in store.frames())
  try:
    runtime = request.runtime(store.manifest["material"]["execution_id"])
  except Exception as exc:
    store.record_failure(exc)
    raise
  previous = store.manifest["hash"]

  def commit(index, frame):
    nonlocal previous
    previous = store.commit_frame(index=index, previous=previous, facts=frame)

  try:
    await runtime.run(ordered, on_frame=commit, retain_frames=False, presorted=streamed)
  except Exception as exc:
    store.record_failure(exc)
    raise
  if runtime.frame_count < prior_frame_count:
    raise ValueError("BACKTEST_RESUME_TRUNCATED")
  if frozen["code"] != {"declared": code_manifest, **backtest_code_evidence()}:
    raise ValueError("BACKTEST_CODE_CHANGED_DURING_RUN")
  economic = {
    "cash": runtime.broker.cash,
    "positions": {c: p.long_volume for c, p in runtime.broker.positions.items()},
    "fills": [
      {
        "code": t.instrument_code,
        "side": t.trade_type.value,
        "volume": t.volume,
        "price": t.price,
        "fee": t.commission,
        "at": t.trade_time.isoformat(),
      }
      for t in runtime.broker.trades
    ],
    "remaining": sorted(
      (p.template.instrument_code, p.remaining_volume)
      for p in runtime.plans.plans.values()
    ),
  }
  result = store.finish(
    {
      "economic": json_value(economic),
      "economic_hash": stable_manifest_hash(json_value(economic)),
      "conservation": runtime._conservation(),
      "strategy_admission": "NOT_EVALUATED",
      "p6_allowed": False,
    }
  )
  return store, runtime, result
