"""Ordered LIVE market consumer for explicitly existing independent executions."""

import asyncio
import copy
import logging
import re
from dataclasses import dataclass, field, fields
from datetime import datetime

from quantx_contracts import ExecutionEnvironment
from quantx_domain.clock import SHANGHAI
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantEntryReadinessProjection,
  TAssistantExecutionEvent,
)
from quantx_domain.trading.t_assistant_market_state import (
  T_MARKET_GENERATION_CHANGED,
  TickAcceptance,
)
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityReferenceProfile
from quantx_infrastructure.core.data.whole_quote_hub import (
  QuoteDeliveryMode,
  whole_quote_hub,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.repositories.t_assistant_symbol_state_repository import (
  TAssistantSymbolStateRepository,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeInstrumentProfileRepository,
)
from quantx_infrastructure.services.live_t_market_marks import LiveTMarketMarkReader
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from quantx_infrastructure.services.t_trade_opportunity_runtime_service import (
  TTradeOpportunityRuntimeService,
)
from sqlalchemy import select

from .accepted_order_market import accepted_order_market
from .instrument_universe_provider import InstrumentUniverseSnapshot
from .t_allocation_trigger import ALLOCATION_TRIGGER_DELAY_SECONDS, AllocationTrigger
from .t_assistant_candidate_controls import read_candidate_controls
from .t_assistant_decision_runtime import TAssistantLiveDecisionRuntime
from .t_assistant_live_admission import canary_instrument_codes
from .t_assistant_live_allocation_runtime import TAssistantLiveAllocationRuntime
from .t_assistant_live_drain import drain_live_entry_work
from .t_assistant_live_entry_review import (
  LiveEntryMarketWitness,
  LiveEntryReviewAdapter,
)
from .t_assistant_live_entry_runtime import TAssistantLiveEntryRuntime
from .t_assistant_live_readiness import activate_live_canary_ready
from .t_assistant_paper_shadow_supervisor import _accepted_tick, _market_gate_context
from .t_trade_decision_snapshot import (
  TDecisionSnapshotBuilder,
  TMarketCapture,
  TSymbolUniverseEntry,
)


@dataclass
class _Binding:
  execution: object
  builder: TDecisionSnapshotBuilder
  universe: tuple
  sequences: dict = field(default_factory=dict)
  generations: dict = field(default_factory=dict)
  rewarm: set = field(default_factory=set)
  readiness_checked_at: datetime | None = None
  ready_market_identity: tuple[str, str] | None = None


class TAssistantLiveSupervisor:
  """Consumes admitted executions and activates them after verified warmup.

  Lifecycle admission supplies an existing WARMING/RUNNING source. The supervisor
  restores durable symbol state and forces a new warm window after restart;
  ordinary reconciliation retains the matching hot cursor and ring together.
  """

  def __init__(
    self,
    *,
    quote_hub=whole_quote_hub,
    session_factory=AsyncSessionLocal,
    clock=time_utils.now_aware,
    readiness_provider=TTradeOperationsService.readiness,
  ):
    self.hub, self.sessions, self.clock = quote_hub, session_factory, clock
    self.runtime = TAssistantLiveDecisionRuntime(
      session_factory=session_factory, clock=clock
    )
    self.allocation_runtime = TAssistantLiveAllocationRuntime(session_factory=session_factory, clock=clock)
    self.entry_runtime = TAssistantLiveEntryRuntime(session_factory=session_factory, clock=clock, review_adapter_factory=self.entry_review_adapter)
    self.readiness_provider = readiness_provider
    self.market_marks = LiveTMarketMarkReader(quote_hub)
    self._lock = asyncio.Lock()
    self._handle = None
    self._bindings = {}
    self.last_results = {}
    self._allocation_triggers = {}
    self._allocation_timers = {}
    self._last_allocation_at = {}

  async def start(self):
    async with self._lock:
      if self._handle is None:
        self._handle = await self.hub.subscribe_batches(
          self._on_quotes, delivery=QuoteDeliveryMode.CRITICAL
        )

  async def stop(self):
    async with self._lock:
      if self._handle is not None:
        await self.hub.unsubscribe(self._handle)
        self._handle = None
      timers = tuple(self._allocation_timers.values())
      for key in tuple(self._bindings):
        self._unbind(key)
      if timers:
        await asyncio.gather(*timers, return_exceptions=True)
      self.last_results.clear()
      self.runtime = TAssistantLiveDecisionRuntime(
        session_factory=self.sessions, clock=self.clock
      )

  def _clear_allocation_trigger(self, execution_id):
    self._allocation_triggers.pop(execution_id, None)
    self._last_allocation_at.pop(execution_id, None)
    timer = self._allocation_timers.pop(execution_id, None)
    if timer is not None and timer is not asyncio.current_task():
      timer.cancel()

  def _unbind(self, execution_id):
    self._clear_allocation_trigger(execution_id)
    self.runtime.unbind_execution(execution_id)
    self._bindings.pop(execution_id, None)
    self.last_results.pop(execution_id, None)

  async def unbind_account(self, account_id):
    async with self._lock:
      for key, binding in tuple(self._bindings.items()):
        if binding.execution.account_id == account_id:
          self._unbind(key)

  async def owns_config(self, config_id):
    # Durable independent lineage prevents accidental legacy resurrection even
    # while its last execution is stopped or waiting for a successor.
    async with self.sessions() as db:
      return (
        await db.scalar(
          select(TAssistantExecutionRecord.execution_id)
          .where(
            TAssistantExecutionRecord.config_id == config_id,
            TAssistantExecutionRecord.environment == "LIVE",
          )
          .limit(1)
        )
        is not None
      )

  async def _checked_head(self, db, config):
    head = await db.get(
      TTradeGlobalConfig, config.id, with_for_update=True, populate_existing=True
    )
    keys = (
      "account_id",
      "state_version",
      "config_version",
      "active_config_version_id",
      "enabled",
      "desired_environment",
      "strategy_run_id",
    )
    if head is None or any(getattr(head, key) != getattr(config, key) for key in keys):
      raise ValueError("T_ASSISTANT_LIVE_CONFIG_REFRESH_REQUIRED")
    return head

  async def reconcile_config(self, *, config, universe, legacy_active):
    async with self.sessions() as db, db.begin():
      await self._checked_head(db, config)
      rows = list(
        (
          await db.scalars(
            select(TAssistantExecutionRecord)
            .where(
              TAssistantExecutionRecord.config_id == config.id,
              TAssistantExecutionRecord.environment == "LIVE",
              TAssistantExecutionRecord.status.in_(
                ["WARMING", "RUNNING", "DRAINING", "RECONCILE_REQUIRED"]
              ),
            )
            .order_by(TAssistantExecutionRecord.execution_id)
          )
        ).all()
      )
    current = [
      row
      for row in rows
      if row.config_version_id == config.active_config_version_id
      and row.status in {"WARMING", "RUNNING"}
    ]
    if len(current) > 1:
      await self.unbind_account(config.account_id)
      raise ValueError("T_ASSISTANT_LIVE_SOURCE_AMBIGUOUS")
    allowed = bool(
      config.enabled
      and config.desired_environment == "LIVE"
      and not legacy_active
      and current
    )
    selected = current[0].execution_id if allowed else None
    draining = [row.execution_id for row in rows if row.execution_id != selected]
    if selected is None:
      await self.unbind_account(config.account_id)
    else:
      async with self._lock:
        for key in draining:
          self._unbind(key)
    for key in draining:
      async with self.sessions() as db, db.begin():
        await self._checked_head(db, config)
        await drain_live_entry_work(
          db,
          execution_id=key,
          now=self.clock(),
          reason="T_ASSISTANT_LIVE_SOURCE_RECONCILED",
        )
    if selected is not None:
      return await self.reconcile(
        execution_id=selected, universe=universe, legacy_active=False
      )
    return None

  async def reconcile(self, *, execution_id, universe, legacy_active):
    async with self._lock:
      # Revoke memory before validation: a failed refresh cannot keep producing.
      previous = self._bindings.pop(execution_id, None)
      pending_trigger = self._allocation_triggers.get(execution_id)
      last_allocation_at = self._last_allocation_at.get(execution_id)
      self._clear_allocation_trigger(execution_id)
      if legacy_active:
        self.last_results.pop(execution_id, None)
        raise ValueError("T_ASSISTANT_LIVE_LEGACY_PRODUCER_ACTIVE")
      now = self.clock()
      if previous is None or (previous.execution.readiness.readiness.value == "READY" and (
        not self.hub.is_ready or previous.ready_market_identity != (
          self.hub.stream_id, str(self.hub.generation)
        )
      )):
        # Commit revocation before profile/config reconstruction can fail or await I/O.
        async with self.sessions() as db, db.begin():
          execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
          if execution is None:
            raise ValueError("T_ASSISTANT_LIVE_EXECUTION_REQUIRED")
          await self.runtime._lock_live_source(db, execution)
          await self._block_readiness(db, execution, "LIVE_READY_RECOVERY_REQUIRED", now)
      async with self.sessions() as db, db.begin():
        execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
        if execution is None:
          raise ValueError("T_ASSISTANT_LIVE_EXECUTION_REQUIRED")
        await self.runtime._lock_live_source(db, execution)
        version = await db.get(
          TAssistantConfigVersionRecord,
          execution.config_version_id,
        )
        if (
          version is None
          or version.config_snapshot_hash != execution.config_snapshot_hash
        ):
          raise ValueError("T_ASSISTANT_LIVE_FROZEN_CONFIG_REQUIRED")
        frozen = TAssistantConfigVersion(
          **{
            item.name: getattr(version, item.name)
            for item in fields(TAssistantConfigVersion)
          }
        )
        if frozen.rollout_stage.value == "CANARY":
          allowed = set(canary_instrument_codes(frozen.canonical_payload))
          universe = InstrumentUniverseSnapshot.create(
            mode=universe.mode,
            instruments=[code for code in universe.instruments if code in allowed],
            metadata=universe.metadata,
          )
        settings = frozen.canonical_payload.get("legacy_settings_snapshot")
        if not isinstance(settings, dict):
          raise ValueError("T_ASSISTANT_LIVE_FROZEN_PARAMETERS_REQUIRED")
        parameters = {
          **settings,
          "account_id": execution.account_id,
          "global_monitor_id": execution.config_id,
          "global_config_version": execution.frozen_config_version,
          "mode": "live",
        }
        states = await TAssistantSymbolStateRepository(db).load_domains(execution_id)
        profiles = {}
        profile_service = TTradeOpportunityRuntimeService()
        profile_repository = TTradeInstrumentProfileRepository(db)
        for code in universe.instruments:
          profile = await profile_service.load_reference_profile(
            instrument_code=code, evaluated_at=now, repository=profile_repository
          )
          if profile is not None:
            profiles[code] = OpportunityReferenceProfile.from_dict(profile)
      entries = tuple(
        TSymbolUniverseEntry(
          instrument_code=code,
          eligible=bool(universe.metadata.get(code, {}).get("eligible", False)),
          draining=bool(universe.metadata.get(code, {}).get("draining", False)),
          ignored=bool(universe.metadata.get(code, {}).get("ignored", False)),
          blockers=tuple(filter(None, [universe.metadata.get(code, {}).get("reason")])),
          reference_profile=profiles.get(code),
        )
        for code in universe.instruments
      )
      for key, binding in tuple(self._bindings.items()):
        if binding.execution.account_id == execution.account_id:
          self._unbind(key)
      builder = previous.builder if previous else TDecisionSnapshotBuilder()
      changed = set(universe.instruments) if previous is None else set()
      if previous is None:
        builder.seed_restored_states(states)
      else:
        old_entries = {entry.instrument_code: entry for entry in previous.universe}
        changed.update(
          entry.instrument_code
          for entry in entries
          if old_entries.get(entry.instrument_code) != entry
        )
        if previous.execution.universe_revision != execution.universe_revision:
          changed.update(universe.instruments)
        for code, hot in self.runtime.symbol_states(execution_id).items():
          if code in states and states[code].revision > hot.revision:
            changed.add(code)
          else:
            states[code] = hot
        builder.invalidate_symbols(changed, symbol_states=states)
      states = {
        code: state for code, state in states.items() if code in universe.instruments
      }
      self.runtime.bind_execution(
        execution, parameters=parameters, symbol_states=states
      )
      self._bindings[execution_id] = _Binding(
        execution,
        builder,
        entries,
        sequences={
          code: max(
            previous.sequences.get(code, 0) if previous else 0,
            states[code].cursor.accepted_sequence
            if code in states and states[code].cursor
            else 0,
          )
          for code in universe.instruments
        },
        generations={
          code: previous.generations.get(code, "") if previous else ""
          for code in universe.instruments
        },
        ready_market_identity=previous.ready_market_identity if previous and not changed else None,
        rewarm=(changed | (previous.rewarm if previous else set()))
        & set(universe.instruments),
      )
      if previous is not None and changed:
        await self._warming_reason(self._bindings[execution_id], "LIVE_READY_RECOVERY_REQUIRED", now)
      elif pending_trigger is not None and execution.readiness.readiness.value == "READY":
        # An unchanged refresh must neither discard pending work nor extend its window.
        self._allocation_triggers[execution_id] = pending_trigger
        self._last_allocation_at[execution_id] = last_allocation_at
        delay = max(0, last_allocation_at + ALLOCATION_TRIGGER_DELAY_SECONDS - asyncio.get_running_loop().time())
        self._allocation_timers[execution_id] = asyncio.create_task(
          self._flush_allocation_trigger(execution_id, self._bindings[execution_id], delay),
          name=f"t-allocation-trigger:{execution_id}",
        )
      return execution_id

  def entry_review_adapter(self, db):
    return LiveEntryReviewAdapter(db, witness_provider=self.entry_market_witness,
      market_mark_reader=self.market_marks, clock=self.clock)

  async def entry_market_witness(self, execution_id, code):
    # No await/lock: snapshot the ring and hub together on the Engine event loop.
    binding = self._bindings.get(execution_id)
    if binding is None or not self.hub.is_ready:
      return None
    current = binding.builder.entry_market_witness(code)
    raw = self.hub.latest(code)
    if current is None or raw is None:
      return None
    tick, ring_generation, last_sequence = current

    def validate():
      latest = self.hub.latest(code)
      if (self._bindings.get(execution_id) is not binding or not self.hub.is_ready
        or binding.ready_market_identity != (self.hub.stream_id, str(self.hub.generation))
        or self.hub.stream_id != tick.stream_id
        or str(self.hub.generation) != tick.sample.continuity_generation
        or binding.builder.entry_market_witness(code) != current
        or latest is None
        or latest.get("market_stream_id") != tick.stream_id
        or str(latest.get("continuity_generation")) != tick.sample.continuity_generation
        or latest.get("source_time_ms") != tick.sample.source_time_ms
        or latest.get("tick_ordinal") != tick.sample.tick_ordinal
        or latest.get("market_stream_sequence") != tick.market_fence_sequence):
        raise ValueError("LIVE_ENTRY_MARKET_WITNESS_CHANGED")

    try:
      validate()
      _, book = accepted_order_market(code, copy.deepcopy(raw), now=self.clock(), environment=ExecutionEnvironment.LIVE)
    except (ValueError, TypeError):
      return None
    return LiveEntryMarketWitness(tick, ring_generation, last_sequence, book, validate)

  async def _warming_reason(self, binding, reason, now):
    async with self.sessions() as db, db.begin():
      repository = TAssistantExecutionRepository(db)
      execution = await repository.get_domain(binding.execution.execution_id)
      await self.runtime._lock_live_source(db, execution)
      binding.execution = await self._block_readiness(db, execution, reason, now)

  async def _block_readiness(self, db, execution, reason, now):
    if execution.status.value not in {"WARMING", "RUNNING"} or execution.readiness.reasons == (reason,):
      return execution
    updated = execution.with_readiness(
      TAssistantEntryReadinessProjection(
        "DEGRADED" if execution.status.value == "RUNNING" else "WARMING", (reason,), now
      )
    )
    await TAssistantExecutionRepository(db).save_transition_with_event(
      updated, expected_state_version=execution.state_version,
      event=TAssistantExecutionEvent(
        execution.execution_id, f"live-readiness:{updated.state_version}",
        "LIVE_ENTRY_READINESS_BLOCKED", now, {"reason_codes": [reason]},
      ),
    )
    return updated

  async def _try_activate(self, binding, cycle_id, capture):
    execution = binding.execution
    if (
      (execution.status.value != "WARMING" and not (
        execution.status.value == "RUNNING" and execution.readiness.readiness.value == "DEGRADED"
      ))
      or execution.entry_authorization.value != "MANUAL_CONFIRM"
    ):
      return
    codes = [
      entry.instrument_code
      for entry in binding.universe
      if entry.eligible and not entry.draining and not entry.ignored
    ]
    states = self.runtime.symbol_states(execution.execution_id)
    if not codes or not all(
      code in states and states[code].lifecycle.value == "ACTIVE" for code in codes
    ):
      return
    now = self.clock()
    if (
      binding.readiness_checked_at is not None
      and (now - binding.readiness_checked_at).total_seconds() < 1
    ):
      return
    binding.readiness_checked_at = now
    try:
      health = await self.readiness_provider(execution.account_id)
    except Exception:
      await self._warming_reason(binding, "LIVE_READY_HEALTH_UNAVAILABLE", self.clock())
      return
    observed = self.clock()
    if (
      not self.hub.is_ready
      or self.hub.stream_id != capture.stream_id
      or str(self.hub.generation) != capture.continuity_generation
    ):
      await self._warming_reason(binding, "LIVE_READY_MARKET_CHANGED", observed)
      return

    def validate_market():
      # Valuation can await historical I/O while the hub continues ingesting.
      # Roll the transition and its audit back if that invalidated the witness.
      if (
        not self.hub.is_ready
        or self.hub.stream_id != capture.stream_id
        or str(self.hub.generation) != capture.continuity_generation
        or not 0 <= (self.clock() - capture.captured_at).total_seconds() < 90
      ):
        raise ValueError("LIVE_READY_MARKET_CHANGED")

    try:
      async with self.sessions() as db, db.begin():
        activated = await activate_live_canary_ready(
          db,
          execution_id=execution.execution_id,
          cycle_id=cycle_id,
          instrument_codes=codes,
          market_capture=capture,
          market_mark_reader=self.market_marks,
          health=health,
          health_observed_at=observed,
          now=observed,
          validate_before_activation=validate_market,
        )
      binding.execution = activated
      binding.ready_market_identity = (capture.stream_id, capture.continuity_generation)
    except ValueError as exc:
      reason = (
        str(exc)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", str(exc))
        else "LIVE_READY_INPUT_UNAVAILABLE"
      )
      await self._warming_reason(binding, reason, observed)

  async def _dispatch_allocation_trigger(self, key, binding, trigger):
    capture = trigger.capture

    def validate_market():
      if (not self.hub.is_ready or self.hub.stream_id != capture.stream_id
        or str(self.hub.generation) != capture.continuity_generation
        or not 0 <= (self.clock() - capture.captured_at).total_seconds() < 90):
        raise ValueError("LIVE_ALLOCATION_MARKET_CHANGED")

    validate_market()
    if trigger.count > 1:
      async with self.sessions() as db, db.begin():
        await self.runtime._lock_live_source(db, binding.execution)
        await TAssistantExecutionRepository(db).append_event(TAssistantExecutionEvent(
          key, f"allocation-trigger:{key}:{capture.stream_id}:{capture.continuity_generation}:{trigger.first_fence}:{capture.fence_sequence}",
          "ALLOCATION_TRIGGERS_COALESCED", self.clock(), trigger.evidence(),
        ))
    await self.allocation_runtime.dispatch(
      execution_id=key, market_mark_reader=self.market_marks, validate_market=validate_market,
    )
    await self.entry_runtime.dispatch(execution_id=key, validate_market=validate_market)
    self._last_allocation_at[key] = asyncio.get_running_loop().time()

  async def _flush_allocation_trigger(self, key, binding, delay):
    try:
      await asyncio.sleep(delay)
      async with self._lock:
        if self._bindings.get(key) is not binding:
          return
        trigger = self._allocation_triggers.pop(key, None)
        self._allocation_timers.pop(key, None)
        if trigger is None or binding.execution.readiness.readiness.value != "READY":
          return
        try:
          await self._dispatch_allocation_trigger(key, binding, trigger)
        except Exception:
          try:
            await self._warming_reason(binding, "LIVE_ALLOCATION_TRIGGER_FAILED", self.clock())
          finally:
            self._unbind(key)
          logging.getLogger(__name__).warning("Deferred LIVE T allocation failed; source unbound")
    except asyncio.CancelledError:
      raise
    except Exception:
      # A failed readiness write must not leave an unobserved task exception.
      logging.getLogger(__name__).warning("Deferred LIVE T allocation recovery failed; source unbound")

  async def _request_allocation_trigger(self, key, binding, capture, *, material):
    pending = self._allocation_triggers.get(key)
    trigger = pending.merge(capture) if pending else AllocationTrigger(capture, capture.fence_sequence)
    elapsed = asyncio.get_running_loop().time() - self._last_allocation_at.get(key, float("-inf"))
    if material or elapsed >= ALLOCATION_TRIGGER_DELAY_SECONDS:
      self._allocation_triggers.pop(key, None)
      timer = self._allocation_timers.pop(key, None)
      if timer is not None:
        timer.cancel()
      await self._dispatch_allocation_trigger(key, binding, trigger)
    else:
      self._allocation_triggers[key] = trigger
      if key not in self._allocation_timers:
        self._allocation_timers[key] = asyncio.create_task(
          self._flush_allocation_trigger(key, binding, ALLOCATION_TRIGGER_DELAY_SECONDS - elapsed),
          name=f"t-allocation-trigger:{key}",
        )

  async def _on_quotes(self, data):
    async with self._lock:
      now = self.clock()
      if now.tzinfo is None:
        raise ValueError("T_ASSISTANT_LIVE_AWARE_CLOCK_REQUIRED")
      identity = (self.hub.stream_id, str(self.hub.generation))
      for binding in self._bindings.values():
        if binding.execution.readiness.readiness.value == "READY" and (
          not self.hub.is_ready or binding.ready_market_identity != identity
        ):
          await self._warming_reason(binding, "LIVE_READY_MARKET_CHANGED", now)
      if not data or not self.hub.is_ready:
        return
      received_ms = int(now.timestamp() * 1000)
      capture = TMarketCapture(
        stream_id=self.hub.stream_id,
        continuity_generation=str(self.hub.generation),
        fence_sequence=self.hub.sequence,
        captured_at=self.hub.last_captured_at,
        ready=self.hub.is_ready,
        reason_codes=(),
      )
      for key, binding in tuple(self._bindings.items()):
        accepted = False
        for code in sorted(
          {entry.instrument_code for entry in binding.universe} & data.keys()
        ):
          raw = data[code]
          generation = str(raw.get("continuity_generation") or "")
          reset = bool(raw.get("market_stream_reset")) or bool(
            binding.generations.get(code) and binding.generations[code] != generation
          )
          if reset and binding.execution.readiness.readiness.value == "READY":
            await self._warming_reason(binding, "LIVE_READY_MARKET_CHANGED", now)
          sequence = 1 if reset else binding.sequences.get(code, 0) + 1
          tick = _accepted_tick(
            code,
            raw,
            capture_time_ms=received_ms,
            accepted_sequence=sequence,
            discontinuity_reason=T_MARKET_GENERATION_CHANGED
            if reset or code in binding.rewarm
            else None,
          )
          outcome = binding.builder.accept_tick(tick, capture_time_ms=received_ms)
          if outcome.acceptance is not TickAcceptance.ACCEPTED:
            continue
          binding.sequences[code], binding.generations[code] = sequence, generation
          binding.rewarm.discard(code)
          accepted = True
        if not accepted:
          continue
        async with self.sessions() as db:
          controls = await read_candidate_controls(
            db,
            environment=ExecutionEnvironment.LIVE,
            execution_id=key,
            account_id=binding.execution.account_id,
            symbol_states=self.runtime.symbol_states(key),
            as_of=now,
          )
        gate = _market_gate_context(now)
        snapshot = binding.builder.build(
          execution=binding.execution,
          capture=capture,
          symbol_states=self.runtime.symbol_states(key),
          universe=binding.universe,
          decision_time=now,
          trade_date=now.astimezone(SHANGHAI).date().isoformat(),
          market_gate_context=gate,
          candidate_controls=controls,
          market_context={"session": gate.session_code, "paper_shadow_only": False},
        )
        try:
          self.last_results[key] = await self.runtime.run_cycle(
            execution=binding.execution, snapshot=snapshot
          )
          if self.last_results[key].committed:
            await self._try_activate(binding, self.last_results[key].cycle_id, capture)
            if binding.execution.readiness.readiness.value == "READY":
              output = self.last_results[key].output
              await self._request_allocation_trigger(
                key, binding, capture,
                material=bool(output.trade_intents or any(patch.material for patch in output.symbol_state_patches)),
              )
        except Exception as exc:
          try:
            if isinstance(exc, ValueError) and str(exc) in {"LIVE_ALLOCATION_MARKET_CHANGED", "LIVE_ENTRY_MARKET_WITNESS_CHANGED", "LIVE_ENTRY_LATEST_MARKET_EXPIRED"}:
              await self._warming_reason(binding, "LIVE_READY_MARKET_CHANGED", self.clock())
          finally:
            self._unbind(key)
          raise
