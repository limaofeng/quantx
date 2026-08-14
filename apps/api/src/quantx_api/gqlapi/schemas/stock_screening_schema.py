from datetime import date
from typing import List

import strawberry

from ..resolvers.stock_screening import StockScreeningResolver
from ..security import authorized_account_id, principal_from_context
from ..types import (
  IntradayVolumeScreenInput,
  IntradayVolumeScreenPage,
  LimitUpLifecycleSnapshotType,
  LimitUpRadarInput,
  LimitUpRadarPage,
  SignalMeta,
  StockScreenInput,
  StockScreenPage,
  StockScreenSnapshotStatus,
)


@strawberry.type(description="条件选股查询")
class StockScreeningQuery:
  @strawberry.field(description="基于日级信号快照执行条件选股")
  async def stock_screen(self, input: StockScreenInput) -> StockScreenPage:
    return await StockScreeningResolver.stock_screen(input)

  @strawberry.field(description="获取选股日级快照完整性及缺失交易日")
  async def stock_screen_snapshot_status(
    self,
    lookback_days: int = 30,
  ) -> StockScreenSnapshotStatus:
    return await StockScreeningResolver.stock_screen_snapshot_status(
      lookback_days
    )

  @strawberry.field(
    description="基于 QMT Agent 全市场实时行情执行盘中量能筛选"
  )
  async def intraday_volume_screen(
    self, input: IntradayVolumeScreenInput
  ) -> IntradayVolumeScreenPage:
    return await StockScreeningResolver.intraday_volume_screen(input)

  @strawberry.field(description="沪深全市场打板机会雷达")
  async def limit_up_radar(
    self,
    info: strawberry.types.Info,
    input: LimitUpRadarInput,
  ) -> LimitUpRadarPage:
    principal = principal_from_context(info.context)
    return await StockScreeningResolver.limit_up_radar(
      input,
      user_id=principal.user_id,
    )

  @strawberry.field(description="首板晋级工作台（V2 影子模型）")
  async def first_board_promotion_desk(
    self,
    info: strawberry.types.Info,
    account_id: str,
    input: LimitUpRadarInput,
  ) -> LimitUpRadarPage:
    principal = principal_from_context(info.context)
    selected_account = (
      None
      if account_id == "__MARKET_ONLY__"
      else authorized_account_id(info, account_id)
    )
    return await StockScreeningResolver.limit_up_radar(
      input,
      user_id=principal.user_id,
      account_id=selected_account,
    )

  @strawberry.field(description="读取指定交易日的首板生命周期快照")
  async def limit_up_lifecycle(
    self, code: str, trade_date: date
  ) -> List[LimitUpLifecycleSnapshotType]:
    return await StockScreeningResolver.limit_up_lifecycle(code, trade_date)

  @strawberry.field(description="获取可用日级信号及快照元信息")
  async def stock_signal_snapshot_meta(self) -> List[SignalMeta]:
    return await StockScreeningResolver.stock_signal_snapshot_meta()
