"""Engine supervisor for independent PAPER decisions and execution."""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from quantx_application.t_trade_v3.execution_use_cases import (
  TAssistantExecutionLifecycle,
)
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerType
from quantx_domain.clock import SHANGHAI
from quantx_domain.strategies.base import MarketDataSession
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantEntryAuthorization,
  TAssistantEntryReadiness,
  TAssistantEntryReadinessProjection,
  TAssistantExecution,
  TAssistantExecutionEvent,
  TAssistantExecutionStatus,
  TAssistantRolloutStage,
  TAssistantScorerMode,
  TModelRuntimeBinding,
)
from quantx_domain.trading.t_assistant_market_state import (
  T_MARKET_GENERATION_CHANGED,
  AcceptedTMarketTick,
  TickAcceptance,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityGateContext,
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
)
from quantx_infrastructure.config.settings import settings as runtime_settings
from quantx_infrastructure.core.data.whole_quote_hub import (
  QuoteConsumerStatus,
  QuoteDeliveryMode,
  WholeQuoteHub,
  whole_quote_hub,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationConflict,
)
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantCycleConflict,
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.repositories.t_assistant_symbol_state_repository import (
  TAssistantSymbolStateRepository,
)
from quantx_infrastructure.repositories.t_model_registry_repository import (
  TModelRegistryRepository,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeInstrumentProfileRepository,
)
from quantx_infrastructure.services.t_trade_opportunity_runtime_service import (
  TTradeOpportunityRuntimeService,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .instrument_universe_provider import InstrumentUniverseSnapshot
from .paper_market_runtime import PaperMarketRuntime, accepted_paper_market
from .t_allocation_trigger import ALLOCATION_TRIGGER_DELAY_SECONDS, AllocationTrigger
from .t_assistant_candidate_controls import read_candidate_controls
from .t_assistant_decision_runtime import (
  TAssistantPaperShadowRuntime,
  TAssistantShadowCycleResult,
)
from .t_assistant_paper_drain import drain_paper_entry_work
from .t_assistant_paper_entry_runtime import (
  PaperEntryDispatchResult,
  PaperEntryMarketWitness,
  TAssistantPaperEntryRuntime,
)
from .t_assistant_paper_seed import paper_policy_blockers, prepare_paper_seed
from .t_registry_model_batch_runtime import TRegistryModelBatchRuntime
from .t_trade_decision_snapshot import (
  TDecisionSnapshotBuilder,
  TDecisionSnapshotBuildError,
  TMarketCapture,
  TSymbolUniverseEntry,
)

logger = logging.getLogger(__name__)

_RETRYABLE_ENTRY_INPUT_REASONS = frozenset({
  "T_VALUATION_MARK_STALE", "T_VALUATION_OPENING_MARK_REQUIRED",
  "T_VALUATION_CURRENT_MARK_REQUIRED", "PAPER_PORTFOLIO_CURRENT_MARK_REQUIRED",
  "T_PORTFOLIO_TRADING_DAY_UNAVAILABLE", "T_PORTFOLIO_PREVIOUS_TRADING_DAY_UNAVAILABLE",
  "T_ALLOCATION_LEASE_CONFLICT", "T_ALLOCATION_LEASE_EXPIRED",
  "RISK_ADMISSION_LEASE_HELD", "RISK_ADMISSION_LEASE_EXPIRED",
  "RISK_ADMISSION_TTL_EXPIRED", "RISK_ADMISSION_INPUT_CHANGED",
  "RISK_ADMISSION_ACCOUNT_SNAPSHOT_CHANGED",
})


@dataclass
class _PaperShadowBinding:
  execution: TAssistantExecution
  builder: TDecisionSnapshotBuilder
  universe: tuple[TSymbolUniverseEntry, ...]
  legacy_results: Mapping[str, Mapping[str, Any]]
  needs_rewarm: set[str]
  parameters: Mapping[str, Any]
  accepted_sequences: dict[str, int]
  continuity_generations: dict[str, str]
  market_books: dict[str, tuple[AcceptedTMarketTick, int, MarketDataSnapshot]] = field(default_factory=dict)
  model_runtime: TRegistryModelBatchRuntime | None = None


class TAssistantPaperShadowSupervisor:
  """Own accepted market data, source decisions and isolated PAPER execution."""

  def __init__(
    self,
    *,
    quote_hub: WholeQuoteHub = whole_quote_hub,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    clock: Callable[[], datetime] = time_utils.now_aware,
    runtime: Optional[TAssistantPaperShadowRuntime] = None,
    model_artifact_root: Path | None = None,
    legacy_compare_attempts: int = 3,
    legacy_compare_retry_seconds: float = 0.025,
  ) -> None:
    if legacy_compare_attempts < 1 or legacy_compare_retry_seconds < 0:
      raise ValueError("legacy comparison retry policy is invalid")
    configured_root = model_artifact_root if model_artifact_root is not None else runtime_settings.t_model_artifact_root
    self._model_artifact_root = Path(configured_root) if configured_root else None
    self._quote_hub = quote_hub
    self._session_factory = session_factory
    self._clock = clock
    self._paper_market = PaperMarketRuntime(session_factory=session_factory, clock=clock)
    self._entry_runtime = TAssistantPaperEntryRuntime(session_factory=session_factory, clock=clock)
    self._runtime = runtime or TAssistantPaperShadowRuntime(
      session_factory=session_factory,
      clock=clock,
    )
    self._subscription_handle: Optional[str] = None
    self._entry_recovery_task: Optional[asyncio.Task] = None
    self._bindings: dict[str, _PaperShadowBinding] = {}
    self._account_execution_ids: dict[str, str] = {}
    self._last_results: dict[str, TAssistantShadowCycleResult] = {}
    self._lifecycle_lock = asyncio.Lock()
    self._allocation_triggers = {}
    self._allocation_timers = {}
    self._last_allocation_at = {}
    self._legacy_compare_attempts = int(legacy_compare_attempts)
    self._legacy_compare_retry_seconds = float(legacy_compare_retry_seconds)

  @property
  def is_running(self) -> bool:
    handle = self._subscription_handle
    return bool(
      handle is not None
      and self._quote_hub.consumer_status(handle) is QuoteConsumerStatus.READY
      and self._quote_hub.is_ready
    )

  @property
  def subscription_handle(self) -> Optional[str]:
    return self._subscription_handle

  def last_result(self, execution_id: str) -> Optional[TAssistantShadowCycleResult]:
    return self._last_results.get(execution_id)

  async def start(self) -> None:
    if self._subscription_handle is not None:
      return
    self._subscription_handle = await self._quote_hub.subscribe_batches(
      self._on_quote_batch,
      delivery=QuoteDeliveryMode.CRITICAL,
    )
    self._entry_recovery_task = asyncio.create_task(
      self._recover_entries(), name="TAssistantPaperEntryRecovery",
    )
    logger.info("T-assistant PAPER shadow CRITICAL consumer started")

  async def stop(self) -> None:
    if self._entry_recovery_task is not None:
      self._entry_recovery_task.cancel()
      try:
        await self._entry_recovery_task
      except asyncio.CancelledError:
        pass
      self._entry_recovery_task = None
    handle = self._subscription_handle
    self._subscription_handle = None
    if handle is not None:
      await self._quote_hub.unsubscribe(handle)
    async with self._lifecycle_lock:
      timers = tuple(self._allocation_timers.values())
      for key in tuple(self._bindings):
        self._clear_allocation_trigger(key)
      self._bindings.clear()
      self._account_execution_ids.clear()
      if timers:
        await asyncio.gather(*timers, return_exceptions=True)
    logger.info("T-assistant PAPER shadow consumer stopped")

  async def _recover_entries(self):
    while True:
      await asyncio.sleep(1.0)
      try:
        await self._recover_entries_once()
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception("PAPER entry recovery failed; durable intents remain recoverable")

  async def _recover_entries_once(self):
    async with self._lifecycle_lock:
      for binding in tuple(self._bindings.values()):
        if binding.execution.execution_id not in self._allocation_triggers:
          await self._dispatch_entries(binding)

  async def reconcile(
    self,
    *,
    config: TTradeGlobalConfig,
    universe: InstrumentUniverseSnapshot,
    legacy_results: Optional[Mapping[str, Mapping[str, Any]]] = None,
  ) -> Optional[str]:
    async with self._lifecycle_lock:
      return await self._reconcile_locked(
        config=config, universe=universe, legacy_results=legacy_results,
      )

  async def _reconcile_locked(
    self,
    *,
    config: TTradeGlobalConfig,
    universe: InstrumentUniverseSnapshot,
    legacy_results: Optional[Mapping[str, Mapping[str, Any]]] = None,
  ) -> Optional[str]:
    """Ensure and bind the execution for the current immutable config head."""

    if not bool(config.enabled) or not universe.instruments:
      await self._drain_account(config.account_id)
      return None
    now = self._now()
    predecessor_ids: set[str] = set()
    async with self._session_factory() as db:
      async with db.begin():
        config_repository = TAssistantConfigRepository(db)
        execution_repository = TAssistantExecutionRepository(db)
        head = await db.scalar(
          select(TTradeGlobalConfig)
          .where(TTradeGlobalConfig.id == config.id)
          .with_for_update()
        )
        if head is None:
          raise RuntimeError("T_ASSISTANT_CONFIG_NOT_FOUND")
        if (
          head.account_id != config.account_id
          or int(head.config_version or 0) != int(config.config_version or 0)
          or dict(head.settings or {}) != dict(config.settings or {})
          or list(head.ignored_stock_codes or [])
          != list(config.ignored_stock_codes or [])
          or bool(head.enabled) != bool(config.enabled)
          or str(head.mode or "").lower() != str(config.mode or "").lower()
        ):
          raise RuntimeError("T_ASSISTANT_CONFIG_HEAD_STALE")
        try:
          version = self._config_version(head)
        except ValueError:
          # Invalid requested configuration cannot leave the old scorer bound.
          # Durable orders and ExitPlans remain under their original owners.
          self._unbind_account(head.account_id)
          raise
        await config_repository.append_version(version)
        if head.active_config_version_id != version.config_version_id:
          head = await config_repository.activate_version(
            config_id=head.id,
            config_version_id=version.config_version_id,
            desired_environment=(
              "LIVE" if str(config.mode or "").lower() == "live" else "PAPER"
            ),
            expected_state_version=int(head.state_version or 1),
          )
        execution_record = await execution_repository.ensure_paper_shadow(
          account_id=head.account_id,
          version=version,
          now=now,
        )
        for predecessor in await execution_repository.list_active_paper_for_account(
          head.account_id
        ):
          if predecessor.execution_id == execution_record.execution_id:
            continue
          predecessor_domain = await execution_repository.get_domain(
            predecessor.execution_id
          )
          if predecessor_domain is not None:
            await self._terminalize_recoverable_cycles(
              db,
              execution_id=predecessor.execution_id,
              now=now,
              reason="T_CYCLE_CONFIG_SUCCESSOR_REVOKED",
            )
            await self._transition_to_stopped(
              execution_repository,
              predecessor_domain,
              now=now,
              reason="T_ASSISTANT_CONFIG_SUCCESSOR_ACTIVATED",
            )
            predecessor_ids.add(predecessor.execution_id)
        execution = await execution_repository.get_domain(execution_record.execution_id)
        if execution is None:
          raise RuntimeError("T_ASSISTANT_EXECUTION_NOT_FOUND")
        execution = await self._prepare_entry_readiness(
          db, execution=execution, payload=version.canonical_payload,
          required_codes=universe.instruments, now=now,
          book_codes=set(self._bindings[execution.execution_id].market_books)
          if execution.execution_id in self._bindings else set(),
          market_ready=self._quote_hub.is_ready,
        )
        target_universe_revision = max(
          int(execution.universe_revision),
          int(config.universe_revision or 0),
        )
        execution = await TAssistantExecutionLifecycle(
          execution_repository
        ).revise_universe(
          execution,
          universe_revision=target_universe_revision,
          at=now,
          payload={
            "universe_revision": target_universe_revision,
            "instruments": list(universe.instruments),
            "config_snapshot_hash": version.config_snapshot_hash,
            "paper_shadow_only": True,
          },
        )
        states = await TAssistantSymbolStateRepository(db).load_domains(
          execution.execution_id
        )
        if execution.execution_id not in self._bindings:
          await self._abort_recoverable_cycles(
            db,
            execution_id=execution.execution_id,
            now=now,
          )
        profile_repository = TTradeInstrumentProfileRepository(db)
        profile_service = TTradeOpportunityRuntimeService()
        reference_profiles: dict[str, OpportunityReferenceProfile] = {}
        for code in universe.instruments:
          profile = await profile_service.load_reference_profile(
            instrument_code=code,
            evaluated_at=now,
            repository=profile_repository,
          )
          if profile is not None:
            reference_profiles[code] = OpportunityReferenceProfile.from_dict(profile)
        parameters = self._strategy_parameters(head)

    try:
      model_runtime = await self._prepare_model_runtime(execution, version)
    except BaseException:
      self._unbind_account(config.account_id)
      raise
    self._remove_account_bindings(
      config.account_id,
      keep_execution_id=execution.execution_id,
    )
    for predecessor_id in predecessor_ids:
      self._clear_allocation_trigger(predecessor_id)
      self._bindings.pop(predecessor_id, None)
    entries = tuple(
      TSymbolUniverseEntry(
        instrument_code=code,
        eligible=bool(universe.metadata.get(code, {}).get("eligible", False)),
        draining=bool(universe.metadata.get(code, {}).get("draining", False)),
        ignored=bool(universe.metadata.get(code, {}).get("ignored", False)),
        blockers=tuple(
          [str(universe.metadata.get(code, {}).get("reason"))]
          if not bool(universe.metadata.get(code, {}).get("eligible", False))
          and universe.metadata.get(code, {}).get("reason")
          else []
        ),
        reference_profile=reference_profiles.get(code),
      )
      for code in universe.instruments
    )
    previous = self._bindings.get(execution.execution_id)
    builder = previous.builder if previous is not None else TDecisionSnapshotBuilder()
    changed_codes: set[str] = set()
    if previous is None:
      builder.seed_restored_states(states)
      needs_rewarm = set(universe.instruments)
    else:
      # Only material states are durable. The live ring and its hot cursor are
      # one generation and must survive routine reconciliation together.
      hot_states = self._runtime.symbol_states(execution.execution_id)
      previous_entries = {item.instrument_code: item for item in previous.universe}
      changed_codes = {
        item.instrument_code for item in entries
        if previous_entries.get(item.instrument_code) != item
      }
      if previous.execution.universe_revision != execution.universe_revision:
        changed_codes.update(universe.instruments)
      for code, hot_state in hot_states.items():
        durable_state = states.get(code)
        if durable_state is not None and durable_state.revision > hot_state.revision:
          changed_codes.add(code)
        else:
          states[code] = hot_state
      builder.invalidate_symbols(changed_codes, symbol_states=states)
      needs_rewarm = set(previous.needs_rewarm) | changed_codes
    states = {code: state for code, state in states.items() if code in universe.instruments}
    needs_rewarm.intersection_update(universe.instruments)
    self._runtime.bind_execution(
      execution,
      parameters=parameters,
      symbol_states=states,
    )
    self._bindings[execution.execution_id] = _PaperShadowBinding(
      execution=execution,
      builder=builder,
      universe=entries,
      legacy_results=dict(legacy_results or {}),
      needs_rewarm=needs_rewarm,
      parameters=parameters,
      accepted_sequences={
        code: (
          max(
            previous.accepted_sequences[code],
            states[code].cursor.accepted_sequence
            if code in states and states[code].cursor is not None else 0,
          )
          if previous is not None and code in previous.accepted_sequences
          else states[code].cursor.accepted_sequence
          if code in states and states[code].cursor is not None
          else 0
        )
        for code in universe.instruments
      },
      continuity_generations={
        code: (
          previous.continuity_generations[code]
          if previous is not None and code in previous.continuity_generations
          else states[code].cursor.continuity_generation
          if code in states and states[code].cursor is not None
          else ""
        )
        for code in universe.instruments
      },
      model_runtime=model_runtime,
      market_books={
        code: value for code, value in (previous.market_books.items() if previous else ())
        if code in universe.instruments and code not in changed_codes
      },
    )
    key = execution.execution_id
    pending = self._allocation_triggers.get(key)
    last_at = self._last_allocation_at.get(key)
    self._clear_allocation_trigger(key)
    if (
      previous is not None and not changed_codes and previous.universe == entries
      and previous.parameters == parameters
      and all(getattr(previous.execution, field) == getattr(execution, field) for field in (
        "config_version_id", "config_snapshot_hash", "frozen_config_version", "status",
        "entry_authorization", "rollout_stage", "scorer_mode", "model_runtime_binding",
        "policy_version", "feature_schema_version", "universe_revision",
      ))
      and previous.execution.readiness.readiness == execution.readiness.readiness
      and previous.execution.readiness.reasons == execution.readiness.reasons
    ):
      if last_at is not None:
        self._last_allocation_at[key] = last_at
      if pending is not None:
        self._allocation_triggers[key] = pending
        self._schedule_allocation_trigger(key)
    self._account_execution_ids[config.account_id] = execution.execution_id
    return execution.execution_id

  async def _prepare_model_runtime(self, execution, version):
    if execution.scorer_mode is TAssistantScorerMode.RULE_ONLY:
      return None
    if self._model_artifact_root is None or not self._model_artifact_root.is_absolute():
      raise ValueError("T_MODEL_ARTIFACT_ROOT_REQUIRED")
    binding = TModelRuntimeBinding.from_mapping(execution.model_runtime_binding)
    policy = version.canonical_payload.get("legacy_settings_snapshot", {}).get("model_runtime_policy")
    if (
      not isinstance(policy, dict) or set(policy) != {"score_max_age_ms", "inference_budget_ms"}
      or any(type(value) is not int or value <= 0 for value in policy.values())
    ):
      raise ValueError("T_MODEL_RUNTIME_POLICY_REQUIRED")
    async with self._session_factory() as db, db.begin():
      record = await TModelRegistryRepository(db).authorize(
        model_id=binding.model_id, model_version=binding.model_version,
        expected_revision=binding.registry_authorization_revision, mode=binding.registry_stage,
        artifact_sha256=binding.artifact_manifest_sha256,
        policy_compatibility_hash=binding.portfolio_policy_compatibility_hash,
      )
      evidence = copy.deepcopy(record.evidence)
    if (
      evidence.get("runtime_self_test_manifest_hash") != binding.runtime_self_test_manifest_hash
      or evidence.get("self_test_tolerance_policy_version") != binding.self_test_tolerance_policy_version
    ):
      raise ValueError("T_MODEL_SELF_TEST_REGISTRY_EVIDENCE_MISMATCH")
    previous = self._bindings.get(execution.execution_id)
    if (previous is not None and previous.model_runtime is not None
      and previous.execution.config_snapshot_hash == execution.config_snapshot_hash
      and previous.execution.model_runtime_binding == execution.model_runtime_binding):
      return previous.model_runtime
    return await TRegistryModelBatchRuntime.load(
      root=self._model_artifact_root, entry=evidence.get("cpu_artifact_entry"), binding=binding,
      self_test_manifest=evidence.get("runtime_self_test_manifest"), session_factory=self._session_factory,
      clock_ms=lambda: int(self._now().timestamp() * 1000),
      max_age_ms=policy["score_max_age_ms"], inference_budget_ms=policy["inference_budget_ms"],
    )

  async def _on_quote_batch(self, data: dict[str, dict[str, Any]]) -> None:
    async with self._lifecycle_lock:
      try:
        await self._on_quote_batch_locked(data)
      except Exception:
        # No deferred dispatch may outlive a failed CRITICAL market callback.
        for account_id in tuple(self._account_execution_ids):
          self._unbind_account(account_id)
        raise

  async def _on_quote_batch_locked(self, data: dict[str, dict[str, Any]]) -> None:
    if not data:
      return
    now = self._now()
    await self._paper_market.on_quote_batch(
      data,
      active_instruments={
        identity: {item.instrument_code for item in binding.universe}
        for identity, binding in self._bindings.items()
      },
      now=now,
    )
    now = self._now()
    capture_time_ms = int(now.timestamp() * 1000)
    for binding in tuple(self._bindings.values()):
      bound_codes = {item.instrument_code for item in binding.universe}
      affected = bound_codes & data.keys()
      if not affected:
        continue
      accepted_any = False
      for code in sorted(affected):
        raw = data[code]
        generation = str(raw.get("continuity_generation") or "").strip()
        discontinuity_reason = None
        generation_changed = bool(
          binding.continuity_generations.get(code)
          and binding.continuity_generations[code] != generation
        )
        resets_local_sequence = (
          bool(raw.get("market_stream_reset")) or generation_changed
        )
        if (
          code in binding.needs_rewarm
          or bool(raw.get("market_stream_reset"))
          or generation_changed
        ):
          discontinuity_reason = T_MARKET_GENERATION_CHANGED
        next_sequence = (
          1
          if resets_local_sequence
          else int(binding.accepted_sequences.get(code, 0)) + 1
        )
        tick = _accepted_tick(
          code,
          raw,
          accepted_sequence=next_sequence,
          capture_time_ms=capture_time_ms,
          discontinuity_reason=discontinuity_reason,
        )
        acceptance = binding.builder.accept_tick(
          tick,
          capture_time_ms=capture_time_ms,
        )
        if acceptance.acceptance is not TickAcceptance.ACCEPTED:
          logger.debug(
            "T-assistant rejected quote before cycle: execution=%s symbol=%s "
            "acceptance=%s reason=%s",
            binding.execution.execution_id,
            code,
            acceptance.acceptance.value,
            acceptance.reason,
          )
          continue
        binding.accepted_sequences[code] = next_sequence
        binding.continuity_generations[code] = generation
        binding.needs_rewarm.discard(code)
        try:
          _, book = accepted_paper_market(code, raw, now=now)
        except ValueError:
          binding.market_books.pop(code, None)
        else:
          current = binding.builder.entry_market_witness(code)
          binding.market_books[code] = (tick, current[1], book)
        accepted_any = True
      if not accepted_any:
        continue
      capture = self._capture(now)
      gate_context = _market_gate_context(now)
      async with self._session_factory() as db:
        controls = await read_candidate_controls(
          db, environment=ExecutionEnvironment.PAPER, execution_id=binding.execution.execution_id,
          account_id=binding.execution.account_id,
          symbol_states=self._runtime.symbol_states(binding.execution.execution_id),
          as_of=now,
        )
      model_view = None
      if binding.model_runtime is not None:
        model_view = await binding.model_runtime.snapshot_view(
          as_of_ms=capture_time_ms, instrument_codes=tuple(item.instrument_code for item in binding.universe),
          rule_order=(),
        )
      try:
        snapshot = binding.builder.build(
          execution=binding.execution,
          capture=capture,
          symbol_states=self._runtime.symbol_states(binding.execution.execution_id),
          universe=binding.universe,
          decision_time=now,
          trade_date=now.date().isoformat(),
          market_gate_context=gate_context,
          candidate_controls=controls,
          model_view=model_view,
          market_context={
            "session": gate_context.session_code,
            "paper_shadow_only": True,
          },
        )
      except TDecisionSnapshotBuildError as exc:
        self._clear_allocation_trigger(binding.execution.execution_id)
        logger.info(
          "T-assistant PAPER snapshot blocked: execution=%s reason=%s",
          binding.execution.execution_id,
          exc.reason_code,
        )
        continue
      try:
        result = await self._runtime.run_cycle(
          execution=binding.execution,
          snapshot=snapshot,
          legacy_results=await self._load_legacy_results(binding, snapshot),
        )
        self._last_results[binding.execution.execution_id] = result
        await self._activate_if_warm(
          binding,
          capture=capture,
          gate_context=gate_context,
        )
        if result.committed:
          output = result.output
          await self._request_allocation_trigger(
            binding, capture,
            material=bool(output.trade_intents or any(patch.material for patch in output.symbol_state_patches)),
          )
        else:
          self._clear_allocation_trigger(binding.execution.execution_id)
      except Exception:
        self._unbind_account(binding.execution.account_id)
        raise

  def _clear_allocation_trigger(self, key):
    self._allocation_triggers.pop(key, None)
    self._last_allocation_at.pop(key, None)
    timer = self._allocation_timers.pop(key, None)
    if timer is not None and timer is not asyncio.current_task():
      timer.cancel()

  def _schedule_allocation_trigger(self, key):
    elapsed = asyncio.get_running_loop().time() - self._last_allocation_at.get(key, float("-inf"))
    self._allocation_timers[key] = asyncio.create_task(
      self._flush_allocation_trigger(key, self._bindings[key], max(0, ALLOCATION_TRIGGER_DELAY_SECONDS - elapsed)),
      name=f"paper-allocation-trigger:{key}",
    )

  async def _request_allocation_trigger(self, binding, capture, *, material):
    key = binding.execution.execution_id
    pending = self._allocation_triggers.get(key)
    # A reset invalidates the earlier trigger; the new material cycle owns recovery.
    if pending is not None and (
      pending.capture.stream_id != capture.stream_id
      or pending.capture.continuity_generation != capture.continuity_generation
    ):
      self._clear_allocation_trigger(key)
      pending = None
    trigger = pending.merge(capture) if pending else AllocationTrigger(capture, capture.fence_sequence)
    elapsed = asyncio.get_running_loop().time() - self._last_allocation_at.get(key, float("-inf"))
    if material or elapsed >= ALLOCATION_TRIGGER_DELAY_SECONDS:
      self._allocation_triggers.pop(key, None)
      timer = self._allocation_timers.pop(key, None)
      if timer is not None:
        timer.cancel()
      await self._dispatch_allocation_trigger(binding, trigger)
    else:
      self._allocation_triggers[key] = trigger
      if key not in self._allocation_timers:
        self._schedule_allocation_trigger(key)

  async def _flush_allocation_trigger(self, key, binding, delay):
    try:
      await asyncio.sleep(delay)
      async with self._lifecycle_lock:
        if self._bindings.get(key) is not binding:
          return
        trigger = self._allocation_triggers.pop(key, None)
        self._allocation_timers.pop(key, None)
        if trigger is not None:
          try:
            await self._dispatch_allocation_trigger(binding, trigger)
          except Exception:
            self._unbind_account(binding.execution.account_id)
            logger.warning("Deferred PAPER T allocation failed; source unbound")
    except asyncio.CancelledError:
      raise

  async def _dispatch_allocation_trigger(self, binding, trigger):
    key = binding.execution.execution_id
    capture = trigger.capture
    if (
      not self._quote_hub.is_ready
      or self._quote_hub.stream_id != capture.stream_id
      or str(self._quote_hub.generation) != capture.continuity_generation
      or not 0 <= (self._now() - capture.captured_at).total_seconds() < 90
    ):
      raise ValueError("PAPER_ALLOCATION_MARKET_CHANGED")
    if trigger.count > 1:
      async with self._session_factory() as db, db.begin():
        await TAssistantExecutionRepository(db).append_event(TAssistantExecutionEvent(
          key, f"paper-allocation-trigger:{key}:{capture.stream_id}:{capture.continuity_generation}:{trigger.first_fence}:{capture.fence_sequence}",
          "ALLOCATION_TRIGGERS_COALESCED", self._now(), trigger.evidence(),
        ))
    await self._dispatch_entries(binding)
    self._last_allocation_at[key] = asyncio.get_running_loop().time()

  async def _dispatch_entries(self, binding):
    if binding.execution.scorer_mode is TAssistantScorerMode.ACTIVE:
      return PaperEntryDispatchResult("BLOCKED", ("PAPER_MODEL_ENTRY_NOT_ENABLED",))
    async def witness(code):
      if not self._quote_hub.is_ready:
        return None
      current = binding.builder.entry_market_witness(code)
      stored = binding.market_books.get(code)
      if current is None or stored is None:
        return None
      tick, ring_generation, last_sequence = current
      latest = self._quote_hub.latest(code)
      if (
        stored[0] != tick or stored[1] != ring_generation or latest is None
        or latest.get("market_stream_id") != tick.stream_id
        or str(latest.get("continuity_generation")) != tick.sample.continuity_generation
        or latest.get("source_time_ms") != tick.sample.source_time_ms
        or latest.get("tick_ordinal") != tick.sample.tick_ordinal
        or latest.get("market_stream_sequence") != tick.market_fence_sequence
      ):
        return None
      return PaperEntryMarketWitness(tick, ring_generation, last_sequence, copy.deepcopy(stored[2]))

    try:
      return await self._entry_runtime.dispatch(
        execution_id=binding.execution.execution_id, market_witness_provider=witness,
      )
    except (ValueError, TAllocationConflict) as exc:
      reason = str(exc)
      if reason not in _RETRYABLE_ENTRY_INPUT_REASONS:
        raise
      # A missing valuation mark or another valid lease blocks this PAPER lane,
      # not delivery of accepted market data to all CRITICAL consumers.
      async with self._session_factory() as db, db.begin():
        key = f"paper-entry-input-blocked:{reason}"
        existing = await db.scalar(select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.execution_id == binding.execution.execution_id,
          TAssistantExecutionEventRecord.event_key == key,
        ))
        if existing is None:
          await TAssistantExecutionRepository(db).append_event(TAssistantExecutionEvent(
            execution_id=binding.execution.execution_id, event_key=key,
            event_type="PAPER_ENTRY_INPUT_BLOCKED", occurred_at=self._now(),
            payload={"reason_codes": [reason]},
          ))
      return PaperEntryDispatchResult("BLOCKED", (reason,))

  async def _activate_if_warm(
    self,
    binding: _PaperShadowBinding,
    *,
    capture: TMarketCapture,
    gate_context: OpportunityGateContext,
  ) -> None:
    if binding.execution.status is not TAssistantExecutionStatus.WARMING:
      return
    states = self._runtime.symbol_states(binding.execution.execution_id)
    eligible = [item.instrument_code for item in binding.universe if item.eligible]
    if not eligible or any(
      code not in states or states[code].lifecycle.value != "ACTIVE"
      for code in eligible
    ):
      return
    now = self._now()
    async with self._session_factory() as db:
      async with db.begin():
        repository = TAssistantExecutionRepository(db)
        current = await repository.get_domain(binding.execution.execution_id)
        if current is None or current.status is not TAssistantExecutionStatus.WARMING:
          return
        version = self._config_version(await db.get(TTradeGlobalConfig, current.config_id))
        if version.config_version_id != current.config_version_id:
          return
        current = await self._prepare_entry_readiness(
          db, execution=current, payload=version.canonical_payload,
          required_codes=eligible, now=now,
          book_codes=set(binding.market_books), market_ready=capture.ready,
        )
        if current.readiness.reasons != ("T_ASSISTANT_MARKET_WARMING",):
          binding.execution = current
          return
        if not capture.ready or any(code not in binding.market_books for code in eligible):
          return
        activated = await TAssistantExecutionLifecycle(repository).activate_ready(
          current,
          at=now,
          payload={"paper_shadow_only": True},
        )
    binding.execution = activated
    self._runtime.bind_execution(
      activated,
      parameters=binding.parameters,
      symbol_states=states,
    )
    if not any(state.deferred_candidate is not None for state in states.values()):
      return
    replay = binding.builder.build(
      execution=activated,
      capture=capture,
      symbol_states=states,
      universe=binding.universe,
      decision_time=now,
      trade_date=now.date().isoformat(),
      market_gate_context=gate_context,
      market_context={
        "session": gate_context.session_code,
        "paper_shadow_only": True,
        "activation_replay": True,
      },
    )
    result = await self._runtime.run_cycle(
      execution=activated,
      snapshot=replay,
      legacy_results=await self._load_legacy_results(binding, replay),
    )
    self._last_results[activated.execution_id] = result

  @staticmethod
  async def _prepare_entry_readiness(
    db, *, execution, payload, required_codes, now, book_codes, market_ready,
  ):
    blockers = await prepare_paper_seed(
      db, execution=execution, config_payload=payload, now=now,
    )
    blockers += paper_policy_blockers(payload, now=now, required_codes=required_codes)
    if not set(required_codes) <= book_codes:
      blockers += ("PAPER_COMPLETE_BOOK_REQUIRED",)
    if not market_ready:
      blockers += ("T_MARKET_STREAM_NOT_READY",)
    if execution.status is not TAssistantExecutionStatus.WARMING:
      return execution
    readiness = TAssistantEntryReadiness.WARMING
    reasons = blockers or ("T_ASSISTANT_MARKET_WARMING",)
    if execution.readiness.readiness is readiness and execution.readiness.reasons == reasons:
      return execution
    updated = execution.with_readiness(TAssistantEntryReadinessProjection(
      readiness=readiness, reasons=reasons, as_of=now,
    ))
    await TAssistantExecutionRepository(db).save_transition_with_event(
      updated, expected_state_version=execution.state_version,
      event=TAssistantExecutionEvent(
        execution_id=execution.execution_id,
        event_key=f"paper-entry-readiness:{updated.state_version}",
        event_type="PAPER_ENTRY_READINESS_CHANGED", occurred_at=now,
        payload={"readiness": readiness.value, "reason_codes": list(reasons)},
      ),
    )
    return updated

  async def _load_legacy_results(
    self,
    binding: _PaperShadowBinding,
    snapshot,
  ) -> dict[str, Mapping[str, Any]]:
    """Join legacy evidence by the exact causal source/fence identity."""

    run_ids = {
      str(item.get("strategy_run_id") or "").strip()
      for item in binding.legacy_results.values()
      if isinstance(item, Mapping)
    }
    run_ids.discard("")
    if not run_ids:
      return {
        symbol.instrument_code: {
          "_unavailable_reason": "LEGACY_STRATEGY_RUN_UNAVAILABLE"
        }
        for symbol in snapshot.symbols
      }
    expected_by_symbol = {
      symbol.instrument_code: _snapshot_symbol_source_fence(symbol)
      for symbol in snapshot.symbols
    }
    results: dict[str, Mapping[str, Any]] = {}
    pending = {
      code: identity
      for code, identity in expected_by_symbol.items()
      if identity is not None
    }
    for code, identity in expected_by_symbol.items():
      if identity is None:
        results[code] = {"_unavailable_reason": "LEGACY_SOURCE_FENCE_NOT_FOUND"}
    for attempt in range(self._legacy_compare_attempts):
      if not pending:
        break
      async with self._session_factory() as db:
        for code, expected in tuple(pending.items()):
          rows = list(
            (
              await db.execute(
                select(TTradeOpportunityEvaluation)
                .where(
                  TTradeOpportunityEvaluation.account_id
                  == binding.execution.account_id,
                  TTradeOpportunityEvaluation.owner_type
                  == ExecutionOwnerType.STRATEGY_RUN.value,
                  TTradeOpportunityEvaluation.instrument_code == code,
                  TTradeOpportunityEvaluation.evaluated_at
                  <= snapshot.decision_time.replace(tzinfo=None),
                  TTradeOpportunityEvaluation.strategy_run_id.in_(run_ids),
                )
                .order_by(
                  TTradeOpportunityEvaluation.evaluated_at.desc(),
                  TTradeOpportunityEvaluation.id.desc(),
                )
                .limit(128)
              )
            )
            .scalars()
            .all()
          )
          for row in rows:
            payload = dict(row.payload or {})
            raw = payload.get("signal_snapshot")
            candidate = dict(raw) if isinstance(raw, Mapping) else payload
            if _legacy_source_fence(candidate) == expected:
              results[code] = candidate
              pending.pop(code, None)
              break
      if pending and attempt + 1 < self._legacy_compare_attempts:
        await asyncio.sleep(self._legacy_compare_retry_seconds)
    for code in pending:
      results[code] = {
        "_unavailable_reason": "LEGACY_SOURCE_FENCE_NOT_FOUND_AFTER_BARRIER"
      }
    return results

  def _capture(self, now: datetime) -> TMarketCapture:
    captured_at = self._quote_hub.last_captured_at or now
    if captured_at.tzinfo is None:
      captured_at = captured_at.replace(tzinfo=UTC)
    return TMarketCapture(
      stream_id=str(self._quote_hub.stream_id or "unavailable"),
      continuity_generation=str(self._quote_hub.generation or 0),
      fence_sequence=max(0, int(self._quote_hub.sequence or 0)),
      captured_at=captured_at,
      ready=bool(self._quote_hub.is_ready),
      reason_codes=(() if self._quote_hub.is_ready else ("T_MARKET_STREAM_NOT_READY",)),
    )

  def _unbind_account(self, account_id: str) -> None:
    self._remove_account_bindings(account_id, keep_execution_id=None)

  def _remove_account_bindings(
    self,
    account_id: str,
    *,
    keep_execution_id: Optional[str],
  ) -> None:
    normalized = str(account_id).strip()
    for execution_id, binding in tuple(self._bindings.items()):
      if (
        binding.execution.account_id == normalized and execution_id != keep_execution_id
      ):
        self._clear_allocation_trigger(execution_id)
        self._bindings.pop(execution_id, None)
    if keep_execution_id is None:
      self._account_execution_ids.pop(normalized, None)
    else:
      self._account_execution_ids[normalized] = keep_execution_id

  async def _drain_account(self, account_id: str) -> None:
    now = self._now()
    async with self._session_factory() as db:
      async with db.begin():
        repository = TAssistantExecutionRepository(db)
        records = await repository.list_active_paper_for_account(account_id)
        if not records:
          self._unbind_account(account_id)
          return
        for record in records:
          current = await repository.get_domain(record.execution_id)
          if current is not None:
            await self._terminalize_recoverable_cycles(
              db,
              execution_id=record.execution_id,
              now=now,
              reason="T_CYCLE_CONFIG_DISABLED",
            )
            await self._transition_to_stopped(
              repository,
              current,
              now=now,
              reason="T_ASSISTANT_CONFIG_DISABLED",
            )
    self._unbind_account(account_id)

  async def _transition_to_stopped(
    self,
    repository: TAssistantExecutionRepository,
    execution: TAssistantExecution,
    *,
    now: datetime,
    reason: str,
  ) -> None:
    lifecycle = TAssistantExecutionLifecycle(repository)
    current = execution
    if current.status is not TAssistantExecutionStatus.DRAINING:
      current = await lifecycle.transition(
        current,
        target=TAssistantExecutionStatus.DRAINING,
        at=now,
        has_unsettled_buy_work=True,
        event_type="EXECUTION_DRAINING",
        payload={"paper_shadow_only": True, "reason": reason},
      )
    drained = await drain_paper_entry_work(
      repository.db, execution_id=execution.execution_id, now=now, reason=reason,
    )
    if drained.has_unsettled_buy_work:
      await lifecycle.transition(
        current, target=TAssistantExecutionStatus.RECONCILE_REQUIRED, at=now,
        has_unsettled_buy_work=True, event_type="EXECUTION_RECONCILE_REQUIRED",
        payload={"reason": reason, "source_buy_work_unsettled": True,
          "intent_ids": list(drained.unsettled_intent_ids),
          "order_ids": list(drained.unsettled_order_ids)},
      )
      return
    await lifecycle.transition(
      current,
      target=TAssistantExecutionStatus.STOPPED,
      at=now,
      has_unsettled_buy_work=False,
      event_type="EXECUTION_STOPPED",
      payload={
        "paper_shadow_only": True,
        "source_buy_work_unsettled": False,
        "reason": reason,
      },
    )

  @staticmethod
  async def _abort_recoverable_cycles(
    db: AsyncSession,
    *,
    execution_id: str,
    now: datetime,
  ) -> None:
    repository = TAssistantDecisionCycleRepository(db)
    for cycle in await repository.list_recoverable(execution_id=execution_id):
      try:
        claim = await repository.claim(
          cycle_id=cycle.cycle_id,
          processing_owner="t-assistant-paper-shadow-recovery",
          expected_input_manifest_hash=cycle.input_manifest_hash,
          now=now,
        )
      except TAssistantCycleConflict:
        # Active claims remain fenced; expired/stale claims are terminalized by
        # claim() in this same transaction and retried on the next reconcile.
        continue
      await repository.abort_stale(
        cycle_id=cycle.cycle_id,
        expected_fence_token=claim.processing_fence_token,
        now=now,
        reason="T_CYCLE_RECOVERY_SNAPSHOT_UNAVAILABLE",
      )

  @staticmethod
  async def _terminalize_recoverable_cycles(
    db: AsyncSession,
    *,
    execution_id: str,
    now: datetime,
    reason: str,
  ) -> None:
    """Revoke every PREPARED cycle before its execution becomes terminal."""

    repository = TAssistantDecisionCycleRepository(db)
    for item in await repository.list_recoverable(execution_id=execution_id):
      await repository.terminalize_recoverable(
        cycle_id=item.cycle_id,
        now=now,
        reason=reason,
      )

  def _now(self) -> datetime:
    current = self._clock()
    if current.tzinfo is None:
      raise ValueError("T-assistant supervisor clock must be timezone-aware")
    return current

  @staticmethod
  def _strategy_parameters(config: TTradeGlobalConfig) -> dict[str, Any]:
    return {
      **dict(config.settings or {}),
      "account_id": config.account_id,
      "global_monitor_id": config.id,
      "global_config_version": int(config.config_version or 1),
      # The independent execution owns the isolated PAPER order capability.
      "mode": "paper",
    }

  @staticmethod
  def _config_version(config: TTradeGlobalConfig) -> TAssistantConfigVersion:
    settings = dict(config.settings or {})
    raw_policy = settings.get("signal_policy")
    signal_policy_payload = dict(raw_policy) if isinstance(raw_policy, Mapping) else {}
    try:
      policy = OpportunityPolicy.from_dict(signal_policy_payload)
    except (TypeError, ValueError):
      policy = OpportunityPolicy()
    explicit_authorization = str(settings.get("entry_authorization") or "").upper()
    legacy_execution_mode = str(
      settings.get("entry_execution_mode") or settings.get("execution_mode") or ""
    ).upper()
    entry_authorization = TAssistantEntryAuthorization(
      explicit_authorization
      if explicit_authorization in {"MANUAL_CONFIRM", "AUTO"}
      else "AUTO"
      if legacy_execution_mode == "LIVE_AUTO"
      else "MANUAL_CONFIRM"
    )
    try:
      rollout_stage = TAssistantRolloutStage(
        str(settings.get("rollout_stage") or "CANARY").upper()
      )
    except ValueError:
      rollout_stage = TAssistantRolloutStage.CANARY
    try:
      scorer_mode = TAssistantScorerMode(
        str(settings.get("scorer_mode", "RULE_ONLY")).upper()
      )
    except ValueError as exc:
      raise ValueError("T_MODEL_CONFIG_MODE_INVALID") from exc
    binding = settings.get("model_runtime_binding")
    if (
      scorer_mode is TAssistantScorerMode.RULE_ONLY and binding is not None
    ) or (
      scorer_mode is not TAssistantScorerMode.RULE_ONLY
      and (not isinstance(binding, Mapping) or not binding)
    ):
      raise ValueError("T_MODEL_CONFIG_BINDING_INVALID")
    payload = {
      "config_schema_version": "t_assistant_config_v1",
      "universe_policy": {
        "ignored_stock_codes": list(config.ignored_stock_codes or []),
      },
      "symbol_rule_policy": signal_policy_payload,
      "portfolio_policy": _mapping(settings.get("portfolio_policy")),
      "t_trading_envelope_policy": _mapping(settings.get("t_trading_envelope_policy")),
      "entry_execution_gate_policy": _mapping(
        settings.get("entry_execution_gate_policy")
      ),
      "exit_plan_template_policy": _mapping(settings.get("exit_plan_template_policy")),
      "paper_seed": settings.get("paper_seed"),
      "legacy_settings_snapshot": settings,
    }
    version_number = max(1, int(config.config_version or 1))
    provisional = TAssistantConfigVersion.create(
      config_version_id="provisional",
      config_id=config.id,
      version=version_number,
      config_schema_version="t_assistant_config_v1",
      canonical_payload=payload,
      entry_authorization=entry_authorization,
      rollout_stage=rollout_stage,
      policy_version=policy.policy_version,
      feature_schema_version=policy.feature_schema_version,
      scorer_mode=scorer_mode,
      model_runtime_binding=(dict(binding) if isinstance(binding, Mapping) else None),
    )
    version_id = str(
      uuid.uuid5(
        uuid.NAMESPACE_URL,
        "quantx:t-assistant:config:"
        f"{config.id}:{version_number}:{provisional.config_snapshot_hash}",
      )
    )
    return TAssistantConfigVersion.create(
      config_version_id=version_id,
      config_id=config.id,
      version=version_number,
      config_schema_version=provisional.config_schema_version,
      canonical_payload=provisional.canonical_payload,
      entry_authorization=provisional.entry_authorization,
      rollout_stage=provisional.rollout_stage,
      policy_version=provisional.policy_version,
      feature_schema_version=provisional.feature_schema_version,
      scorer_mode=provisional.scorer_mode,
      model_runtime_binding=provisional.model_runtime_binding,
    )


def _accepted_tick(
  instrument_code: str,
  raw: Mapping[str, Any],
  *,
  capture_time_ms: int,
  discontinuity_reason: Optional[str],
  accepted_sequence: int,
) -> AcceptedTMarketTick:
  stream_id = str(raw.get("market_stream_id") or "").strip()
  generation = str(raw.get("continuity_generation") or "").strip()
  sequence = _positive_int(raw.get("market_stream_sequence"), "market sequence")
  source_time_ms = _positive_int(
    raw.get("source_time_ms") or raw.get("time"), "source time"
  )
  ordinal = _positive_int(raw.get("tick_ordinal") or sequence, "tick ordinal")
  last_price = _positive_float(
    raw.get("lastPrice", raw.get("last_price")),
    "last price",
  )
  if not stream_id or not generation:
    raise ValueError("T-assistant Tick requires authoritative market lineage")
  return AcceptedTMarketTick(
    stream_id=stream_id,
    accepted_sequence=accepted_sequence,
    market_fence_sequence=sequence,
    received_at_ms=capture_time_ms,
    discontinuity_reason=discontinuity_reason,
    sample=OpportunitySample(
      instrument_code=instrument_code,
      trade_date=datetime.fromtimestamp(source_time_ms / 1000, tz=UTC)
      .date()
      .isoformat(),
      source_time_ms=source_time_ms,
      tick_ordinal=ordinal,
      price=last_price,
      continuity_generation=generation,
      received_at_ms=capture_time_ms,
      bid_price=_first_number(raw.get("bidPrice", raw.get("bid_price"))),
      ask_price=_first_number(raw.get("askPrice", raw.get("ask_price"))),
      bid_volume=_first_number(raw.get("bidVol", raw.get("bid_vol"))),
      ask_volume=_first_number(raw.get("askVol", raw.get("ask_vol"))),
      cumulative_amount=_optional_float(raw.get("amount")),
      cumulative_volume=_optional_float(raw.get("volume")),
    ),
  )


def _market_gate_context(now: datetime) -> OpportunityGateContext:
  local = now.astimezone(SHANGHAI)
  current = local.time().replace(tzinfo=None)
  if time(9, 30) <= current <= time(11, 30):
    session = MarketDataSession.CONTINUOUS_AM
  elif time(13, 0) <= current <= time(14, 57):
    session = MarketDataSession.CONTINUOUS_PM
  else:
    session = MarketDataSession.UNKNOWN
  return OpportunityGateContext(
    continuous_session=session.is_continuous and local.weekday() < 5,
    session_code=session.value,
    local_second_of_day=(current.hour * 3600 + current.minute * 60 + current.second),
  )


def _first_number(value: Any) -> Optional[float]:
  if isinstance(value, (list, tuple)):
    value = value[0] if value else None
  return _optional_float(value)


def _mapping(value: Any) -> dict[str, Any]:
  return dict(value) if isinstance(value, Mapping) else {}


def _legacy_source_fence(
  raw: Mapping[str, Any],
) -> Optional[tuple[str, int, int, int]]:
  try:
    identity = (
      str(raw.get("continuity_generation") or "").strip(),
      int(raw.get("source_time_ms") or 0),
      int(raw.get("tick_ordinal") or 0),
      int(raw.get("market_fence_sequence") or 0),
    )
  except (TypeError, ValueError, OverflowError):
    return None
  if not identity[0] or min(identity[1:]) <= 0:
    return None
  return identity


def _snapshot_symbol_source_fence(symbol) -> Optional[tuple[str, int, int, int]]:
  ticks = symbol.delta_slice.ticks
  if ticks:
    tick = ticks[-1]
    return (
      tick.sample.continuity_generation,
      tick.sample.source_time_ms,
      tick.sample.tick_ordinal,
      int(tick.market_fence_sequence or 0),
    )
  deferred = symbol.state.deferred_candidate
  if deferred is None:
    return None
  identity = (
    symbol.state.opportunity_state.continuity_generation or "",
    deferred.source_time_ms,
    deferred.tick_ordinal,
    int(symbol.state.deferred_candidate_fence_sequence or 0),
  )
  return identity if identity[0] and min(identity[1:]) > 0 else None


def _optional_float(value: Any) -> Optional[float]:
  if value is None or isinstance(value, bool):
    return None
  try:
    return float(value)
  except (TypeError, ValueError, OverflowError):
    return None


def _positive_float(value: Any, label: str) -> float:
  parsed = _optional_float(value)
  if parsed is None or parsed <= 0:
    raise ValueError(f"T-assistant Tick {label} must be positive")
  return parsed


def _positive_int(value: Any, label: str) -> int:
  if isinstance(value, bool):
    raise ValueError(f"T-assistant Tick {label} must be positive")
  try:
    parsed = int(value)
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError(f"T-assistant Tick {label} must be positive") from exc
  if parsed <= 0:
    raise ValueError(f"T-assistant Tick {label} must be positive")
  return parsed


__all__ = ["TAssistantPaperShadowSupervisor"]
