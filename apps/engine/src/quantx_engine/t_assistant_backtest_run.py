"""Versioned local shared-account BACKTEST entry point and replay recovery."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
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

from quantx_engine.t_assistant_backtest_runtime import (
  TAssistantBacktestRuntime,
  json_value,
)
from quantx_engine.t_assistant_backtest_timeline import tick_frames


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
  ordered = [item for _, frame in tick_frames(events) for item in frame]
  if not ordered or ordered[0].decision_time < request.start_at:
    raise ValueError("BACKTEST_DATA_START_INVALID")
  if not code_manifest:
    raise ValueError("BACKTEST_CODE_MANIFEST_REQUIRED")
  data = {
    "count": len(ordered),
    "hash": stable_manifest_hash({"ticks": json_value(ordered)}),
    "start": ordered[0].decision_time.isoformat(),
    "end": ordered[-1].decision_time.isoformat(),
  }
  frozen = {
    "config": json_value(request),
    "data": data,
    "code": code_manifest,
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
  prior_frames = list(store.frames())
  runtime = request.runtime(store.manifest["material"]["execution_id"])
  previous = store.manifest["hash"]

  def commit(index, frame):
    nonlocal previous
    previous = store.commit_frame(index=index, previous=previous, facts=frame)

  await runtime.run(ordered, on_frame=commit, retain_frames=False)
  if runtime.frame_count < len(prior_frames):
    raise ValueError("BACKTEST_RESUME_TRUNCATED")
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
