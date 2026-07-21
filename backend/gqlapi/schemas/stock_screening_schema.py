from typing import List

import strawberry

from ..resolvers.stock_screening import StockScreeningResolver
from ..types import (
  IntradayVolumeScreenInput,
  IntradayVolumeScreenPage,
  SignalMeta,
  StockScreenInput,
  StockScreenPage,
)


@strawberry.type(description="条件选股查询")
class StockScreeningQuery:
  @strawberry.field(description="基于日级信号快照执行条件选股")
  async def stock_screen(self, input: StockScreenInput) -> StockScreenPage:
    return await StockScreeningResolver.stock_screen(input)

  @strawberry.field(description="基于 xtquant 全市场实时行情执行盘中量能筛选")
  async def intraday_volume_screen(
    self, input: IntradayVolumeScreenInput
  ) -> IntradayVolumeScreenPage:
    return await StockScreeningResolver.intraday_volume_screen(input)

  @strawberry.field(description="获取可用日级信号及快照元信息")
  async def stock_signal_snapshot_meta(self) -> List[SignalMeta]:
    return await StockScreeningResolver.stock_signal_snapshot_meta()
