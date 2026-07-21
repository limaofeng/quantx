"""Idempotently backfill closed position cycles from persisted broker trades.

This script never estimates fees or missing cost. Run it explicitly from backend/:
  python scripts/backfill_closed_position_cycles.py
"""

import asyncio
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.connection import get_async_db  # noqa: E402
from models.closed_position_cycle import ClosedPositionCycle  # noqa: E402
from models.enums import OrderType  # noqa: E402
from models.position import Position  # noqa: E402
from models.trade import Trade  # noqa: E402
from services.closed_position_cycle_service import (  # noqa: E402
  ClosedPositionCycleService,
  reconstruct_cycle,
)


async def backfill() -> tuple[int, int]:
  completed = 0
  incomplete = 0
  async for db in get_async_db():
    trade_result = await db.execute(
      select(Trade).order_by(Trade.account_id, Trade.stock_code, Trade.time)
    )
    position_result = await db.execute(select(Position))
    open_positions = {
      (item.account_id, item.stock_code)
      for item in position_result.scalars().all()
      if int(item.volume or 0) > 0
    }
    groups = defaultdict(list)
    for trade in trade_result.scalars().all():
      groups[(trade.account_id, trade.stock_code)].append(trade)

    for (account_id, stock_code), trades in groups.items():
      net_volume = 0
      current = []
      for trade in trades:
        volume = max(int(trade.volume or 0), 0)
        if volume <= 0:
          continue
        current.append(trade)
        if int(trade.order_type) == int(OrderType.BUY):
          net_volume += volume
        elif int(trade.order_type) == int(OrderType.SELL):
          net_volume -= volume
        if net_volume == 0 and current:
          result = reconstruct_cycle(current)
          first_time = current[0].time or datetime.min
          last_time = current[-1].time or first_time
          cycle = ClosedPositionCycle(
            id=ClosedPositionCycleService.cycle_id(
              account_id, stock_code, first_time
            ),
            account_id=account_id,
            account_type=(
              current[0].account_type.value if current[0].account_type else None
            ),
            stock_code=stock_code,
            opened_at=first_time,
            closed_at=last_time,
            source="HISTORICAL_BACKFILL",
            buy_volume=result.buy_volume,
            sell_volume=result.sell_volume,
            average_buy_price=(
              result.gross_buy_amount / result.buy_volume
              if result.buy_volume
              else None
            ),
            average_sell_price=(
              result.gross_sell_amount / result.sell_volume
              if result.sell_volume
              else None
            ),
            gross_buy_amount=result.gross_buy_amount,
            gross_sell_amount=result.gross_sell_amount,
            gross_realized_pnl=(
              result.gross_sell_amount - result.gross_buy_amount
              if result.complete
              else None
            ),
            gross_realized_pnl_percent=(
              (result.gross_sell_amount - result.gross_buy_amount)
              / result.gross_buy_amount
              * 100
              if result.complete
              else None
            ),
            related_trade_ids=result.related_trade_ids,
            pnl_quality=(
              "COMPLETE_GROSS" if result.complete else "INCOMPLETE_HISTORY"
            ),
            quality_flags=result.quality_flags,
          )
          await db.merge(cycle)
          completed += int(result.complete)
          incomplete += int(not result.complete)
          current = []
      if current and (account_id, stock_code) not in open_positions:
        result = reconstruct_cycle(current)
        first_time = current[0].time or datetime.min
        last_time = current[-1].time or first_time
        flags = list(dict.fromkeys([*result.quality_flags, "UNMATCHED_HISTORY"]))
        await db.merge(
          ClosedPositionCycle(
            id=ClosedPositionCycleService.cycle_id(
              account_id, stock_code, first_time
            ),
            account_id=account_id,
            stock_code=stock_code,
            opened_at=first_time,
            closed_at=last_time,
            source="HISTORICAL_BACKFILL",
            buy_volume=result.buy_volume,
            sell_volume=result.sell_volume,
            gross_buy_amount=result.gross_buy_amount,
            gross_sell_amount=result.gross_sell_amount,
            related_trade_ids=result.related_trade_ids,
            pnl_quality="INCOMPLETE_HISTORY",
            quality_flags=flags,
          )
        )
        incomplete += 1
    await db.commit()
    return completed, incomplete
  return completed, incomplete


if __name__ == "__main__":
  complete_count, incomplete_count = asyncio.run(backfill())
  print(
    f"清仓周期回填完成：完整毛盈亏 {complete_count} 条，历史不完整 {incomplete_count} 条"
  )
