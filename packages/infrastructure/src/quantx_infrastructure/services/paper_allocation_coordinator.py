"""PAPER account cut for the shared durable allocation pipeline."""

from quantx_infrastructure.services.paper_portfolio_snapshot import (
  PaperPortfolioSnapshotReader,
)
from quantx_infrastructure.services.t_allocation_coordinator import (
  TAllocationCoordinator,
)


class PaperAllocationCoordinator(TAllocationCoordinator):
  environment = "PAPER"

  async def read_snapshot(self, **kwargs):
    return await PaperPortfolioSnapshotReader(self.db).read(**kwargs)
