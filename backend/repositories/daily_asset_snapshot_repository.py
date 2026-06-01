"""Repository for daily close asset snapshots."""

from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.daily_asset_snapshot import (
  DailyAssetPositionSnapshot,
  DailyAssetSnapshot,
)


class DailyAssetSnapshotRepository(BaseRepository[DailyAssetSnapshot]):
  """Daily asset snapshot repository."""

  model_class = DailyAssetSnapshot

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  @staticmethod
  def scope_key(scope_type: str, scope_id: str) -> str:
    return f"{str(scope_type or '').lower()}:{scope_id}"

  async def find_by_scope_and_date(
    self, scope_key: str, trade_date: date
  ) -> Optional[DailyAssetSnapshot]:
    stmt = select(DailyAssetSnapshot).where(
      DailyAssetSnapshot.scope_key == scope_key,
      DailyAssetSnapshot.trade_date == trade_date,
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def find_previous(
    self, scope_key: str, trade_date: date
  ) -> Optional[DailyAssetSnapshot]:
    stmt = (
      select(DailyAssetSnapshot)
      .where(
        DailyAssetSnapshot.scope_key == scope_key,
        DailyAssetSnapshot.trade_date < trade_date,
      )
      .order_by(DailyAssetSnapshot.trade_date.desc())
      .limit(1)
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def find_latest_for_account(
    self, account_id: str, scope_type: str = "ACCOUNT"
  ) -> Optional[DailyAssetSnapshot]:
    stmt = (
      select(DailyAssetSnapshot)
      .where(
        DailyAssetSnapshot.account_id == account_id,
        DailyAssetSnapshot.scope_type == scope_type,
      )
      .order_by(
        DailyAssetSnapshot.trade_date.desc(),
        DailyAssetSnapshot.snapshot_at.desc(),
      )
      .limit(1)
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def find_range(
    self,
    *,
    account_id: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = 366,
  ) -> List[DailyAssetSnapshot]:
    stmt = select(DailyAssetSnapshot)
    if account_id:
      stmt = stmt.where(DailyAssetSnapshot.account_id == account_id)
    if strategy_run_id:
      stmt = stmt.where(DailyAssetSnapshot.strategy_run_id == strategy_run_id)
    if scope_type:
      stmt = stmt.where(DailyAssetSnapshot.scope_type == str(scope_type).upper())
    if start_date:
      stmt = stmt.where(DailyAssetSnapshot.trade_date >= start_date)
    if end_date:
      stmt = stmt.where(DailyAssetSnapshot.trade_date <= end_date)

    stmt = stmt.order_by(
      DailyAssetSnapshot.trade_date.asc(),
      DailyAssetSnapshot.scope_type.asc(),
      DailyAssetSnapshot.scope_key.asc(),
    ).limit(max(1, min(int(limit or 366), 2000)))
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def upsert_snapshot(self, values: Dict[str, Any]) -> DailyAssetSnapshot:
    values = dict(values)
    scope_key = str(values["scope_key"])
    trade_date = values["trade_date"]
    snapshot_id = values.pop("id", None) or DailyAssetSnapshot.make_id(
      scope_key, trade_date
    )

    record = await self.find_by_id(snapshot_id)
    if record is None:
      record = await self.find_by_scope_and_date(scope_key, trade_date)

    if record:
      for key, value in values.items():
        setattr(record, key, value)
    else:
      record = DailyAssetSnapshot(id=snapshot_id, **values)
      self.db.add(record)

    await self.db.commit()
    await self.db.refresh(record)
    return record


class DailyAssetPositionSnapshotRepository(
  BaseRepository[DailyAssetPositionSnapshot]
):
  """Repository for daily position detail snapshots."""

  model_class = DailyAssetPositionSnapshot

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def replace_for_snapshot(
    self,
    snapshot_id: str,
    positions: Iterable[Dict[str, Any]],
  ) -> List[DailyAssetPositionSnapshot]:
    await self.db.execute(
      delete(DailyAssetPositionSnapshot).where(
        DailyAssetPositionSnapshot.snapshot_id == snapshot_id
      )
    )

    records: List[DailyAssetPositionSnapshot] = []
    for position in positions:
      data = dict(position or {})
      instrument_code = str(
        data.get("instrument_code") or data.get("stock_code") or ""
      ).strip()
      if not instrument_code:
        continue
      bucket = str(data.get("bucket") or "")
      record = DailyAssetPositionSnapshot(
        id=DailyAssetPositionSnapshot.make_id(snapshot_id, instrument_code, bucket),
        snapshot_id=snapshot_id,
        instrument_code=instrument_code,
        instrument_name=data.get("instrument_name"),
        bucket=bucket,
        volume=int(data.get("volume", data.get("long_volume", 0)) or 0),
        available_volume=int(
          data.get("available_volume", data.get("can_use_volume", 0)) or 0
        ),
        frozen_volume=int(data.get("frozen_volume", 0) or 0),
        avg_price=data.get("avg_price", data.get("long_avg_price")),
        last_price=data.get("last_price"),
        market_value_cny=float(data.get("market_value", 0.0) or 0.0),
        cost_basis_cny=data.get("cost_basis_cny"),
        unrealized_pnl_cny=data.get("pnl", data.get("unrealized_pnl")),
        snapshot_metadata=data.get("metadata") or {},
      )
      self.db.add(record)
      records.append(record)

    await self.db.commit()
    for record in records:
      await self.db.refresh(record)
    return records
