"""Dispatch committed LIVE candidates to allocation, stopping before trade approval."""

from dataclasses import dataclass

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.live_allocation_coordinator import (
  LiveAllocationCoordinator,
)
from sqlalchemy import exists, select


@dataclass(frozen=True)
class LiveAllocationDispatchResult:
  status: str
  allocation_ids: tuple[str, ...] = ()


class TAssistantLiveAllocationRuntime:
  def __init__(self, *, session_factory, clock):
    self.sessions, self.clock = session_factory, clock

  async def dispatch(self, *, execution_id, market_mark_reader, validate_market):
    """The witness validator is synchronous; loss during valuation rolls back all writes."""
    validate_market()
    async with self.sessions() as db, db.begin():
      probe = await db.get(TAssistantExecutionRecord, execution_id)
      if probe is None or probe.environment != "LIVE":
        return LiveAllocationDispatchResult("BLOCKED")
      head = await db.get(TTradeGlobalConfig, probe.config_id,
        with_for_update=True, populate_existing=True)
      execution = await db.get(TAssistantExecutionRecord, execution_id,
        with_for_update=True, populate_existing=True)
      if (head is None or not head.enabled or head.strategy_run_id
        or head.account_id != execution.account_id or head.desired_environment != "LIVE"
        or head.active_config_version_id != execution.config_version_id
        or execution.status != "RUNNING" or execution.entry_readiness != "READY"):
        return LiveAllocationDispatchResult("BLOCKED")
      cycles = list(await db.scalars(
        select(TAssistantDecisionCycleRecord).where(
          TAssistantDecisionCycleRecord.execution_id == execution_id,
          TAssistantDecisionCycleRecord.status == "PROPOSALS_COMMITTED",
          exists(select(TradeIntentRecord.id).where(
            TradeIntentRecord.allocation_cycle_id == TAssistantDecisionCycleRecord.cycle_id,
            TradeIntentRecord.status == "ALLOCATION_PENDING",
          )),
        ).order_by(TAssistantDecisionCycleRecord.cycle_sequence)
      ))
      coordinator = LiveAllocationCoordinator(db, market_mark_reader=market_mark_reader)
      batches = []
      for cycle in cycles:
        batch = await coordinator.allocate_cycle(execution_id=execution_id,
          cycle_id=cycle.cycle_id, processing_owner=f"live-allocation:{execution_id}",
          now=aware_time(self.clock()))
        if batch is not None:
          batches.append(batch.allocation_batch_id)
      validate_market()
    return LiveAllocationDispatchResult("PROCESSED" if batches else "IDLE", tuple(batches))
