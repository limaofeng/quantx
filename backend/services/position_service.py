"""
持仓服务
处理持仓相关的业务逻辑
"""

import asyncio
import logging
from datetime import datetime
from hashlib import md5
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from database.connection import get_async_db
from database.relational_base import BulkSaveResult
from models.broker_position_snapshot import BrokerPositionSnapshot
from models.position import Position
from repositories import InstrumentRepository
from repositories.position_repository import PositionRepository
from services.closed_position_cycle_service import ClosedPositionCycleService
from core.utils import time_utils

logger = logging.getLogger(__name__)


class PositionService:
  """持仓服务类"""

  def __init__(self):
    pass

  async def get_positions(
    self, account_id: Optional[str] = None
  ) -> List[Position]:
    """获取用户持仓列表"""
    async for db in get_async_db():
      position_repo = PositionRepository(db)
      return await position_repo.find_all(account_id=account_id)

  async def get_position_by_stock(self, stock_code: str) -> Optional[Position]:
    """获取用户某股票的持仓"""
    async for db in get_async_db():
      instrument_repo = InstrumentRepository(db)
      stock = await instrument_repo.find_by_code(stock_code)
      if not stock:
        return None

      position_repo = PositionRepository(db)
      position_db = await position_repo.find_by_stock_code(stock.id)

      return position_db

  async def get_position_by_account_stock(
    self, account_id: str, stock_code: str
  ) -> Optional[Position]:
    """Return a position scoped to the requested funding account."""
    async for db in get_async_db():
      return await PositionRepository(db).find_by_stock_code(
        stock_code, account_id=account_id
      )
    return None

  async def save_position(self, position: Position) -> Position:
    """创建或更新持仓"""
    async for db in get_async_db():
      position_repo = PositionRepository(db)

      if position.id is None:
        id = md5(
          f"{position.account_id}:{position.stock_code}".encode("utf-8")
        ).hexdigest()
        position.id = id
      position_repo = await position_repo.save(position)
      return position

  async def save_positions(self, positions: List[Position]) -> BulkSaveResult:
    """批量保存持仓数据"""
    async for db in get_async_db():
      cycle_service = ClosedPositionCycleService()
      closed_positions = [pos for pos in positions if pos.volume == 0]
      active_positions = [pos for pos in positions if pos.volume > 0]
      deleted_count = 0
      inserted_count = 0
      updated_count = 0
      saved_entities = []

      for position in closed_positions:
        existing = await db.get(Position, position.id) if position.id else None
        if existing is None:
          continue
        await cycle_service.record_position_closed(
          db,
          existing,
          closed_at=time_utils.now(),
          source="POSITION_BATCH",
        )
        await db.delete(existing)
        deleted_count += 1

      for position in active_positions:
        existing = await db.get(Position, position.id) if position.id else None
        saved_entities.append(await db.merge(position))
        if existing is None:
          inserted_count += 1
        else:
          updated_count += 1
      await db.commit()
      result = BulkSaveResult(
        saved_entities=saved_entities,
        saved_count=len(saved_entities),
        inserted_count=inserted_count,
        updated_count=updated_count,
        deleted_count=deleted_count,
      )

      logger.info(
        f"持仓数据更新完成: 新增/更新 {result.saved_count} 个, "
        f"删除 {result.deleted_count} 个"
      )

      return result

  async def refresh_from_miniqmt(self, account_id: str) -> Dict[str, Any]:
    """Query one complete miniQMT snapshot and atomically persist it."""
    from core.utils import time_utils
    from miniqmt import XTTradingManagerRegistry

    registry = XTTradingManagerRegistry()
    try:
      manager = await asyncio.to_thread(registry.get_manager, account_id, False)
      positions = await asyncio.to_thread(manager.query_positions_snapshot)
      reported_at = time_utils.now()
      sequence = int(reported_at.timestamp() * 1_000_000)
      return await self.apply_full_snapshot(
        account_id=account_id,
        positions=positions,
        sequence=sequence,
        reported_at=reported_at,
        source="MINIQMT",
        is_complete=True,
      )
    except Exception as exc:
      await self.mark_snapshot_failure(account_id, str(exc))
      raise RuntimeError(f"miniQMT 持仓完整快照失败: {exc}") from exc

  async def apply_full_snapshot(
    self,
    *,
    account_id: str,
    positions: List[Any],
    sequence: int,
    reported_at: datetime,
    source: str,
    is_complete: bool,
  ) -> Dict[str, Any]:
    """Apply only a complete, monotonic snapshot; a confirmed [] means empty."""
    if not is_complete:
      raise ValueError("不完整持仓结果不能覆盖当前快照")
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
      raise ValueError("持仓快照缺少账户")
    converted = [
      self._position_from_broker(item, normalized_account) for item in positions
    ]
    incoming = {
      item.stock_code: item for item in converted if int(item.volume or 0) > 0
    }

    async for db in get_async_db():
      status = await db.get(BrokerPositionSnapshot, normalized_account)
      if status and int(sequence) <= int(status.sequence or 0):
        return {**status.to_dict(), "applied": False, "reason": "STALE_SEQUENCE"}
      result = await db.execute(
        select(Position).where(Position.account_id == normalized_account)
      )
      existing = {item.stock_code: item for item in result.scalars().all()}
      cycle_service = ClosedPositionCycleService()
      for code, item in existing.items():
        if code not in incoming:
          await cycle_service.record_position_closed(
            db,
            item,
            closed_at=reported_at,
            source=str(source or "MINIQMT"),
          )
          await db.delete(item)
      for item in incoming.values():
        await db.merge(item)
      received_at = datetime.now(reported_at.tzinfo) if reported_at.tzinfo else datetime.now()
      snapshot = status or BrokerPositionSnapshot(account_id=normalized_account)
      snapshot.sequence = int(sequence)
      snapshot.source = str(source or "MINIQMT")
      snapshot.reported_at = reported_at
      snapshot.received_at = received_at
      snapshot.position_count = len(incoming)
      snapshot.is_complete = True
      snapshot.last_error = None
      await db.merge(snapshot)
      await db.commit()
      return {**snapshot.to_dict(), "applied": True, "reason": "APPLIED"}
    raise RuntimeError("持仓快照数据库不可用")

  async def apply_position_delta(self, position: Any, account_id: str) -> None:
    """Persist an immediate miniQMT callback without pretending it is full."""
    item = self._position_from_broker(position, account_id)
    async for db in get_async_db():
      existing = await db.get(Position, item.id)
      if int(item.volume or 0) <= 0:
        if existing:
          await ClosedPositionCycleService().record_position_closed(
            db,
            existing,
            closed_at=time_utils.now(),
            source="POSITION_CALLBACK",
          )
          await db.delete(existing)
      else:
        await db.merge(item)
      await db.commit()
      return

  async def get_snapshot_status(
    self, account_id: str
  ) -> Optional[Dict[str, Any]]:
    async for db in get_async_db():
      status = await db.get(BrokerPositionSnapshot, account_id)
      return status.to_dict() if status else None
    return None

  async def mark_snapshot_failure(self, account_id: str, error: str) -> None:
    async for db in get_async_db():
      status = await db.get(BrokerPositionSnapshot, account_id)
      if status is None:
        status = BrokerPositionSnapshot(
          account_id=account_id,
          sequence=0,
          source="MINIQMT",
          is_complete=False,
        )
      status.last_error = str(error or "")[:2000]
      await db.merge(status)
      await db.commit()
      return

  @staticmethod
  def _position_from_broker(value: Any, account_id: str) -> Position:
    if isinstance(value, Position):
      data = value.to_dict()
      data["stock_code"] = value.stock_code
      data["account_id"] = account_id
      data["account_type"] = (
        value.account_type.to_int() if value.account_type else None
      )
    elif isinstance(value, dict):
      data = dict(value)
      data["account_id"] = account_id
      account_type = data.get("account_type")
      if hasattr(account_type, "to_int"):
        data["account_type"] = account_type.to_int()
    else:
      fields = {
        "account_type",
        "stock_code",
        "instrument_name",
        "volume",
        "can_use_volume",
        "open_price",
        "market_value",
        "frozen_volume",
        "on_road_volume",
        "yesterday_volume",
        "avg_price",
        "direction",
      }
      data = {key: getattr(value, key, None) for key in fields}
      data["account_id"] = account_id
    if not data.get("stock_code"):
      raise ValueError("持仓快照包含缺少股票代码的记录")
    return Position.from_dict(data)

  async def calculate_portfolio_summary(
    self, account_id: str, positions: List[Position]
  ) -> Dict:
    """计算持仓汇总数据"""

    # 基础统计
    position_count = len(positions)
    profit_position_count = 0
    loss_position_count = 0
    total_market_value = 0
    total_cost = 0

    # 带市值占比的持仓列表
    positions_with_percent = []

    for position in positions:
      market_value = float(position.market_value) if position.market_value else 0
      avg_price = float(position.avg_price) if position.avg_price else 0
      volume = position.volume or 0

      total_market_value += market_value

      # 计算成本
      if avg_price > 0 and volume > 0:
        cost = avg_price * volume
        total_cost += cost

    # 计算每个持仓的市值占比
    for position in positions:
      market_value = float(position.market_value) if position.market_value else 0

      # 计算市值占比
      market_value_percent = (
        (market_value / total_market_value * 100) if total_market_value > 0 else 0
      )

      # 计算盈亏（需要实时价格，这里先用模拟数据）
      avg_price = float(position.avg_price) if position.avg_price else 0
      volume = position.volume or 0

      # TODO: 这里需要获取实时价格
      last_price = avg_price * 1.02  # 模拟价格变动

      profit_loss = 0
      if last_price > 0 and avg_price > 0 and volume > 0:
        profit_loss = (last_price - avg_price) * volume

        if profit_loss > 0:
          profit_position_count += 1
        elif profit_loss < 0:
          loss_position_count += 1

      # 添加到结果列表
      if market_value > 0:  # 只包含有市值的持仓
        positions_with_percent.append(
          {
            "position": position,
            "market_value_percent": round(market_value_percent, 2),
            "last_price": last_price,
            "profit_loss": profit_loss,
            "market_value": market_value,
          }
        )

    # 按市值排序
    positions_with_percent.sort(key=lambda x: x["market_value"], reverse=True)

    # 计算总盈亏
    total_profit_loss = total_market_value - total_cost if total_cost > 0 else 0
    total_profit_loss_percent = (
      (total_profit_loss / total_cost * 100) if total_cost > 0 else 0
    )

    return {
      "position_count": position_count,
      "profit_position_count": profit_position_count,
      "loss_position_count": loss_position_count,
      "total_market_value": round(total_market_value, 2),
      "total_profit_loss": round(total_profit_loss, 2),
      "total_profit_loss_percent": round(total_profit_loss_percent, 2),
      "positions_with_percent": positions_with_percent,
    }
