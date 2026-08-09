"""Closed-position lifecycle reconstruction from broker position and trade truth."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import List, Optional, Sequence, Tuple

from quantx_domain.clock import to_naive_utc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.closed_position_cycle import ClosedPositionCycle
from quantx_infrastructure.models.enums import OrderType
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.repositories.closed_position_cycle_repository import (
  ClosedPositionCycleRepository,
)


@dataclass(frozen=True)
class ReconstructedCycle:
  buy_volume: int
  sell_volume: int
  gross_buy_amount: float
  gross_sell_amount: float
  related_trade_ids: List[str]
  complete: bool
  quality_flags: List[str]


def reconstruct_cycle(trades: Sequence[Trade]) -> ReconstructedCycle:
  """Reconstruct a long-only lifecycle without inventing cost or fees."""
  ordered = sorted(
    trades,
    key=lambda item: (item.time or datetime.min, str(item.id or "")),
  )
  buy_volume = 0
  sell_volume = 0
  buy_amount = 0.0
  sell_amount = 0.0
  trade_ids: List[str] = []
  flags: List[str] = []
  running_volume = 0

  for trade in ordered:
    volume = max(int(trade.volume or 0), 0)
    amount = float(trade.amount or 0.0)
    if volume <= 0:
      continue
    if int(trade.order_type) == int(OrderType.BUY):
      buy_volume += volume
      buy_amount += amount
      running_volume += volume
    elif int(trade.order_type) == int(OrderType.SELL):
      sell_volume += volume
      sell_amount += amount
      running_volume -= volume
      if running_volume < 0 and "SELL_BEFORE_OPEN" not in flags:
        flags.append("SELL_BEFORE_OPEN")
    else:
      continue
    trade_ids.append(str(trade.id))

  if not trade_ids:
    flags.append("NO_RELATED_TRADES")
  if buy_volume <= 0:
    flags.append("MISSING_OPEN_TRADES")
  if sell_volume <= 0:
    flags.append("MISSING_CLOSE_TRADES")
  if buy_volume != sell_volume:
    flags.append("VOLUME_MISMATCH")
  if buy_amount <= 0:
    flags.append("MISSING_BUY_AMOUNT")
  if sell_amount <= 0:
    flags.append("MISSING_SELL_AMOUNT")

  complete = not flags
  return ReconstructedCycle(
    buy_volume=buy_volume,
    sell_volume=sell_volume,
    gross_buy_amount=buy_amount,
    gross_sell_amount=sell_amount,
    related_trade_ids=trade_ids,
    complete=complete,
    quality_flags=flags,
  )


class ClosedPositionCycleService:
  """Create, reconcile, and query position-zero lifecycle records."""

  TRADE_CALLBACK_LOOKBACK = timedelta(minutes=10)

  @staticmethod
  def cycle_id(account_id: str, stock_code: str, opened_at: datetime) -> str:
    value = f"{account_id}:{stock_code}:{opened_at.isoformat()}"
    return sha256(value.encode("utf-8")).hexdigest()

  async def record_position_closed(
    self,
    db: AsyncSession,
    position: Position,
    *,
    closed_at: datetime,
    source: str,
  ) -> ClosedPositionCycle:
    normalized_closed_at = to_naive_utc(closed_at)
    opened_at = to_naive_utc(position.created_at or normalized_closed_at)
    cycle = ClosedPositionCycle(
      id=self.cycle_id(position.account_id, position.stock_code, opened_at),
      account_id=position.account_id,
      account_type=position.account_type.value if position.account_type else None,
      stock_code=position.stock_code,
      instrument_name=position.instrument_name,
      opened_at=opened_at,
      closed_at=normalized_closed_at,
      source=source,
    )
    await self._rebuild(db, cycle)
    return await db.merge(cycle)

  @classmethod
  def _trade_window(
    cls, cycle: ClosedPositionCycle
  ) -> Tuple[datetime, datetime]:
    """Build a naive-UTC window for TIMESTAMP-without-time-zone columns."""
    closed_at = to_naive_utc(cycle.closed_at)
    opened_at = to_naive_utc(cycle.opened_at or closed_at)
    return (
      opened_at - cls.TRADE_CALLBACK_LOOKBACK,
      closed_at + cls.TRADE_CALLBACK_LOOKBACK,
    )

  async def reconcile_latest_cycle(
    self, account_id: str, stock_code: str
  ) -> Optional[ClosedPositionCycle]:
    async for db in get_async_db():
      repository = ClosedPositionCycleRepository(db)
      cycle = await repository.find_latest(
        account_id, stock_code, incomplete_only=True
      )
      if cycle is None:
        return None
      await self._rebuild(db, cycle)
      await db.commit()
      await db.refresh(cycle)
      return cycle
    return None

  async def get_page(
    self,
    account_id: str,
    start_date: Optional[date],
    end_date: Optional[date],
    limit: int,
    offset: int,
  ) -> Tuple[List[ClosedPositionCycle], int]:
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    async for db in get_async_db():
      return await ClosedPositionCycleRepository(db).find_page(
        account_id,
        start_date,
        end_date,
        safe_limit,
        safe_offset,
      )
    return [], 0

  async def _rebuild(
    self, db: AsyncSession, cycle: ClosedPositionCycle
  ) -> None:
    start_at, end_at = self._trade_window(cycle)
    result = await db.execute(
      select(Trade)
      .where(
        Trade.account_id == cycle.account_id,
        Trade.stock_code == cycle.stock_code,
        Trade.time >= start_at,
        Trade.time <= end_at,
      )
      .order_by(Trade.time.asc())
    )
    reconstructed = reconstruct_cycle(list(result.scalars().all()))
    cycle.buy_volume = reconstructed.buy_volume
    cycle.sell_volume = reconstructed.sell_volume
    cycle.gross_buy_amount = reconstructed.gross_buy_amount
    cycle.gross_sell_amount = reconstructed.gross_sell_amount
    cycle.average_buy_price = (
      reconstructed.gross_buy_amount / reconstructed.buy_volume
      if reconstructed.buy_volume
      else None
    )
    cycle.average_sell_price = (
      reconstructed.gross_sell_amount / reconstructed.sell_volume
      if reconstructed.sell_volume
      else None
    )
    cycle.related_trade_ids = reconstructed.related_trade_ids
    cycle.pnl_quality = (
      "COMPLETE_GROSS" if reconstructed.complete else "INCOMPLETE_HISTORY"
    )
    cycle.quality_flags = reconstructed.quality_flags
    if reconstructed.complete:
      pnl = reconstructed.gross_sell_amount - reconstructed.gross_buy_amount
      cycle.gross_realized_pnl = pnl
      cycle.gross_realized_pnl_percent = (
        pnl / reconstructed.gross_buy_amount * 100
      )
    else:
      cycle.gross_realized_pnl = None
      cycle.gross_realized_pnl_percent = None
