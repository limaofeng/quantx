"""LIVE allocation over durable candidate evidence and the current broker facts."""

from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationConflict,
)
from quantx_infrastructure.services.live_portfolio_snapshot import (
  LivePortfolioSnapshotReader,
)
from quantx_infrastructure.services.t_allocation_coordinator import (
  TAllocationCoordinator,
)


class LiveAllocationCoordinator(TAllocationCoordinator):
  environment = "LIVE"

  def __init__(self, db, *, market_mark_reader):
    super().__init__(db)
    self.market_mark_reader = market_mark_reader

  async def allocate_cycle(self, *, execution_id, **kwargs):
    if not self.db.in_transaction():
      raise TAllocationConflict("T_ALLOCATION_LIVE_TRANSACTION_REQUIRED")
    probe = await self.db.get(TAssistantExecutionRecord, execution_id)
    if probe is None or probe.environment != "LIVE":
      raise TAllocationConflict("T_ALLOCATION_LIVE_SOURCE_REQUIRED")
    head = await self.db.get(TTradeGlobalConfig, probe.config_id,
      with_for_update=True, populate_existing=True)
    execution = await self.db.get(TAssistantExecutionRecord, execution_id,
      with_for_update=True, populate_existing=True)
    if (head is None or not head.enabled or head.strategy_run_id
      or head.account_id != execution.account_id or head.desired_environment != "LIVE"
      or head.active_config_version_id != execution.config_version_id
      or execution.status != "RUNNING" or execution.entry_readiness != "READY"):
      raise TAllocationConflict("T_ALLOCATION_EXECUTION_NOT_ENTRY_READY")
    return await super().allocate_cycle(execution_id=execution_id, **kwargs)

  async def read_snapshot(self, **kwargs):
    return await LivePortfolioSnapshotReader(self.db).read(
      **kwargs, market_mark_reader=self.market_mark_reader, account_max_age_seconds=90
    )
