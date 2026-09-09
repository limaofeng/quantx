"""Ordered LIVE market consumer for explicitly existing independent executions."""

import asyncio
from dataclasses import dataclass, field, fields

from quantx_domain.clock import SHANGHAI
from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
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
from quantx_infrastructure.services.t_trade_opportunity_runtime_service import (
  TTradeOpportunityRuntimeService,
)
from sqlalchemy import select

from .t_assistant_candidate_controls import read_candidate_controls
from .t_assistant_decision_runtime import TAssistantLiveDecisionRuntime
from .t_assistant_live_drain import drain_live_entry_work
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


class TAssistantLiveSupervisor:
  """Does not create/activate executions or broker commands.

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
  ):
    self.hub, self.sessions, self.clock = quote_hub, session_factory, clock
    self.runtime = TAssistantLiveDecisionRuntime(
      session_factory=session_factory, clock=clock
    )
    self._lock = asyncio.Lock()
    self._handle = None
    self._bindings = {}
    self.last_results = {}

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
      self._bindings.clear()
      self.last_results.clear()
      self.runtime = TAssistantLiveDecisionRuntime(
        session_factory=self.sessions, clock=self.clock
      )

  def _unbind(self, execution_id):
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
      if legacy_active:
        self.last_results.pop(execution_id, None)
        raise ValueError("T_ASSISTANT_LIVE_LEGACY_PRODUCER_ACTIVE")
      now = self.clock()
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
        rewarm=(changed | (previous.rewarm if previous else set()))
        & set(universe.instruments),
      )
      return execution_id

  async def _on_quotes(self, data):
    async with self._lock:
      if not data or not self.hub.is_ready:
        return
      now = self.clock()
      if now.tzinfo is None:
        raise ValueError("T_ASSISTANT_LIVE_AWARE_CLOCK_REQUIRED")
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
        except Exception:
          self._unbind(key)
          raise
