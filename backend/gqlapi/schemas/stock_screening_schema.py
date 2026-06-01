from typing import List

import strawberry

from ..resolvers.stock_screening import StockScreeningResolver
from ..types import SignalMeta, StockScreenInput, StockScreenPage


@strawberry.type(description="条件选股查询")
class StockScreeningQuery:
  @strawberry.field(description="基于日级信号快照执行条件选股")
  async def stock_screen(self, input: StockScreenInput) -> StockScreenPage:
    return await StockScreeningResolver.stock_screen(input)

  @strawberry.field(description="获取可用日级信号及快照元信息")
  async def stock_signal_snapshot_meta(self) -> List[SignalMeta]:
    return await StockScreeningResolver.stock_signal_snapshot_meta()
