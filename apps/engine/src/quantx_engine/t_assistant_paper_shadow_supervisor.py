"""Engine supervisor for the isolated P3 T-assistant PAPER shadow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any, Callable, Mapping, Optional

from quantx_application.t_trade_v3.execution_use_cases import (
  TAssistantExecutionLifecycle,
)
from quantx_contracts import ExecutionOwnerType
from quantx_domain.clock import SHANGHAI
from quantx_domain.strategies.base import MarketDataSession
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantEntryAuthorization,
  TAssistantExecution,
  TAssistantExecutionStatus,
  TAssistantRolloutStage,
  TAssistantScorerMode,
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
from quantx_infrastructure.core.data.whole_quote_hub import (
  QuoteConsumerStatus,
  QuoteDeliveryMode,
  WholeQuoteHub,
  whole_quote_hub,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
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
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeInstrumentProfileRepository,
)
from quantx_infrastructure.services.t_trade_opportunity_runtime_service import (
  TTradeOpportunityRuntimeService,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .instrument_universe_provider import InstrumentUniverseSnapshot
from .t_assistant_decision_runtime import (
  TAssistantPaperShadowRuntime,
  TAssistantShadowCycleResult,
)
from .t_trade_decision_snapshot import (
  TDecisionSnapshotBuilder,
  TDecisionSnapshotBuildError,
  TMarketCapture,
  TSymbolUniverseEntry,
)

logger = logging.getLogger(__name__)


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


class TAssistantPaperShadowSupervisor:
  """Own the CRITICAL market consumer and independent PAPER executions.

  There is deliberately no StrategyManager, TradeCommandService, approval,
  PendingTradeOrder, or outbox dependency in this object graph.
  """

  def __init__(
    self,
    *,
    quote_hub: WholeQuoteHub = whole_quote_hub,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    clock: Callable[[], datetime] = time_utils.now_aware,
    runtime: Optional[TAssistantPaperShadowRuntime] = None,
    legacy_compare_attempts: int = 3,
    legacy_compare_retry_seconds: float = 0.025,
  ) -> None:
    if legacy_compare_attempts < 1 or legacy_compare_retry_seconds < 0:
      raise ValueError("legacy comparison retry policy is invalid")
    self._quote_hub = quote_hub
    self._session_factory = session_factory
    self._clock = clock
    self._runtime = runtime or TAssistantPaperShadowRuntime(
      session_factory=session_factory,
      clock=clock,
    )
    self._subscription_handle: Optional[str] = None
    self._bindings: dict[str, _PaperShadowBinding] = {}
    self._account_execution_ids: dict[str, str] = {}
    self._last_results: dict[str, TAssistantShadowCycleResult] = {}
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
    logger.info("T-assistant PAPER shadow CRITICAL consumer started")

  async def stop(self) -> None:
    handle = self._subscription_handle
    self._subscription_handle = None
    if handle is not None:
      await self._quote_hub.unsubscribe(handle)
    self._bindings.clear()
    self._account_execution_ids.clear()
    logger.info("T-assistant PAPER shadow consumer stopped")

  async def reconcile(
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
        version = self._config_version(head)
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

    self._remove_account_bindings(
      config.account_id,
      keep_execution_id=execution.execution_id,
    )
    for predecessor_id in predecessor_ids:
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
    )
    self._account_execution_ids[config.account_id] = execution.execution_id
    return execution.execution_id

  async def _on_quote_batch(self, data: dict[str, dict[str, Any]]) -> None:
    if not data or not self._bindings:
      return
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
        accepted_any = True
      if not accepted_any:
        continue
      capture = self._capture(now)
      gate_context = _market_gate_context(now)
      try:
        snapshot = binding.builder.build(
          execution=binding.execution,
          capture=capture,
          symbol_states=self._runtime.symbol_states(binding.execution.execution_id),
          universe=binding.universe,
          decision_time=now,
          trade_date=now.date().isoformat(),
          market_gate_context=gate_context,
          market_context={
            "session": gate_context.session_code,
            "paper_shadow_only": True,
          },
        )
      except TDecisionSnapshotBuildError as exc:
        logger.info(
          "T-assistant PAPER snapshot blocked: execution=%s reason=%s",
          binding.execution.execution_id,
          exc.reason_code,
        )
        continue
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
        has_unsettled_buy_work=False,
        event_type="EXECUTION_DRAINING",
        payload={"paper_shadow_only": True, "reason": reason},
      )
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
      # P3 evaluates proposals only; this does not grant an order capability.
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
        str(settings.get("scorer_mode") or "RULE_ONLY").upper()
      )
    except ValueError:
      scorer_mode = TAssistantScorerMode.RULE_ONLY
    binding = settings.get("model_runtime_binding")
    if scorer_mode is TAssistantScorerMode.RULE_ONLY or not isinstance(
      binding, Mapping
    ):
      scorer_mode = TAssistantScorerMode.RULE_ONLY
      binding = None
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
