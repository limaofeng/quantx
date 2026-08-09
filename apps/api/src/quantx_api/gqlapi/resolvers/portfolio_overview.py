"""Fast, consistent portfolio overview backed only by PostgreSQL snapshots."""

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.account import Account as AccountModel
from quantx_infrastructure.models.broker_position_snapshot import (
  BrokerPositionSnapshot,
)
from quantx_infrastructure.models.daily_asset_snapshot import DailyAssetSnapshot
from quantx_infrastructure.models.position import Position as PositionModel
from sqlalchemy import and_, select

from ..types.portfolio_types import (
  BrokerPositionSnapshotInfo,
  PortfolioOverview,
  PortfolioSummary,
  Position,
)
from .account import AccountResolver
from .portfolio_summary import PortfolioSummaryResolver


class PortfolioOverviewResolver:
  @staticmethod
  async def get_portfolio_overview(account_id: str) -> PortfolioOverview:
    async for db in get_async_db():
      latest_daily_snapshot_id = (
        select(DailyAssetSnapshot.id)
        .where(
          DailyAssetSnapshot.account_id == account_id,
          DailyAssetSnapshot.scope_type == "ACCOUNT",
        )
        .order_by(
          DailyAssetSnapshot.trade_date.desc(),
          DailyAssetSnapshot.snapshot_at.desc(),
        )
        .limit(1)
        .correlate(None)
        .scalar_subquery()
      )
      rows = (
        await db.execute(
          select(
            AccountModel,
            PositionModel,
            BrokerPositionSnapshot,
            DailyAssetSnapshot,
          )
          .select_from(AccountModel)
          .outerjoin(
            PositionModel,
            and_(
              PositionModel.account_id == AccountModel.account_id,
              PositionModel.volume > 0,
            ),
          )
          .outerjoin(
            BrokerPositionSnapshot,
            BrokerPositionSnapshot.account_id == AccountModel.account_id,
          )
          .outerjoin(
            DailyAssetSnapshot,
            DailyAssetSnapshot.id == latest_daily_snapshot_id,
          )
          .where(AccountModel.account_id == account_id)
          .order_by(PositionModel.stock_code.asc())
        )
      ).all()
      if not rows:
        raise ValueError(f"无法获取账户 {account_id} 的信息")

      account, _, snapshot, latest_daily_snapshot = rows[0]
      position_models = [row[1] for row in rows if row[1] is not None]

      account_type = AccountResolver._to_graphql(account)
      positions = [Position.from_model(item) for item in position_models]
      summary_data = PortfolioSummaryResolver._calculate_summary(
        account_id, account_type, positions
      )
      if latest_daily_snapshot is not None:
        summary_data["today_profit_loss"] = (
          round(float(latest_daily_snapshot.daily_pnl_cny), 2)
          if latest_daily_snapshot.daily_pnl_cny is not None
          else None
        )
        summary_data["today_profit_loss_percent"] = (
          round(float(latest_daily_snapshot.daily_return_pct), 2)
          if latest_daily_snapshot.daily_return_pct is not None
          else None
        )
      summary = PortfolioSummary(**summary_data)

      snapshot_type = (
        BrokerPositionSnapshotInfo(
          sequence=str(snapshot.sequence or 0),
          source=str(snapshot.source or "MINIQMT"),
          reported_at=snapshot.reported_at,
          received_at=snapshot.received_at,
          position_count=int(snapshot.position_count or 0),
          is_complete=bool(snapshot.is_complete),
          last_error=snapshot.last_error,
        )
        if snapshot is not None
        else None
      )
      as_of = (
        snapshot.received_at
        if snapshot is not None and snapshot.received_at is not None
        else account.updated_at or time_utils.now()
      )
      return PortfolioOverview(
        account=account_type,
        positions=positions,
        summary=summary,
        position_snapshot=snapshot_type,
        as_of=as_of,
      )
    raise RuntimeError("持仓数据库不可用")
