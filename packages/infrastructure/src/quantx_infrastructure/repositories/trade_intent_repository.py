"""交易意图仓储层 - 处理 TradeIntentRecord 相关操作。"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

_T_TRADE_ENTRY_REASONS = (
  "T_TRADE_PULLBACK_REBOUND_ENTRY",
  "T_TRADE_MOMENTUM_ACCELERATION_ENTRY",
)


class TradeIntentRepository(BaseRepository[TradeIntentRecord]):
  """交易意图仓储实现。"""

  model_class = TradeIntentRecord

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  def _normalize_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(data or {})
    if "metadata" in payload:
      payload["intent_metadata"] = payload.pop("metadata")
    return payload

  async def find_by_id(self, intent_id: str) -> Optional[TradeIntentRecord]:
    """根据ID获取交易意图。"""
    result = await self.db.execute(
      select(TradeIntentRecord).filter(TradeIntentRecord.id == intent_id)
    )
    return result.scalar_one_or_none()

  async def find_by_strategy_run(self, strategy_run_id: str) -> List[TradeIntentRecord]:
    """获取策略运行的所有交易意图。"""
    result = await self.db.execute(
      select(TradeIntentRecord)
      .filter(TradeIntentRecord.strategy_run_id == strategy_run_id)
      .order_by(desc(TradeIntentRecord.created_at))
    )
    return list(result.scalars().all())

  async def find_by_trace_id(
    self, strategy_run_id: str, trace_id: str
  ) -> List[TradeIntentRecord]:
    """获取某次 step/trace 关联的交易意图。"""
    result = await self.db.execute(
      select(TradeIntentRecord)
      .filter(TradeIntentRecord.strategy_run_id == strategy_run_id)
      .filter(TradeIntentRecord.trace_id == trace_id)
      .order_by(desc(TradeIntentRecord.created_at))
    )
    return list(result.scalars().all())

  async def find_recent_by_strategy_run(
    self, strategy_run_id: str, limit: int = 50
  ) -> List[TradeIntentRecord]:
    """获取策略运行最近的交易意图。"""
    result = await self.db.execute(
      select(TradeIntentRecord)
      .filter(TradeIntentRecord.strategy_run_id == strategy_run_id)
      .order_by(desc(TradeIntentRecord.created_at))
      .limit(max(1, min(int(limit or 50), 200)))
    )
    return list(result.scalars().all())

  async def find_recent_t_trade_entries(
    self, strategy_run_ids: List[str], limit: int = 50
  ) -> List[TradeIntentRecord]:
    """获取一组做 T 策略运行最近产生的买入确认信号。"""
    normalized_run_ids = [str(run_id) for run_id in strategy_run_ids if run_id]
    if not normalized_run_ids:
      return []
    result = await self.db.execute(
      select(TradeIntentRecord)
      .filter(TradeIntentRecord.strategy_run_id.in_(normalized_run_ids))
      .filter(TradeIntentRecord.direction == "BUY")
      .filter(TradeIntentRecord.reason.in_(_T_TRADE_ENTRY_REASONS))
      .order_by(desc(TradeIntentRecord.created_at))
      .limit(max(1, min(int(limit or 50), 200)))
    )
    return list(result.scalars().all())

  async def find_recent_t_trade_entries_page(
    self,
    strategy_run_ids: List[str],
    *,
    cursor_created_at: Optional[datetime] = None,
    cursor_id: Optional[str] = None,
    first: int = 30,
  ) -> tuple[List[TradeIntentRecord], bool]:
    normalized_run_ids = [str(run_id) for run_id in strategy_run_ids if run_id]
    if not normalized_run_ids:
      return [], False
    safe_first = max(1, min(int(first or 30), 100))
    stmt = (
      select(TradeIntentRecord)
      .filter(TradeIntentRecord.strategy_run_id.in_(normalized_run_ids))
      .filter(TradeIntentRecord.direction == "BUY")
      .filter(TradeIntentRecord.reason.in_(_T_TRADE_ENTRY_REASONS))
    )
    if cursor_created_at is not None and cursor_id:
      stmt = stmt.filter(
        or_(
          TradeIntentRecord.created_at < cursor_created_at,
          and_(
            TradeIntentRecord.created_at == cursor_created_at,
            TradeIntentRecord.id < cursor_id,
          ),
        )
      )
    rows = list(
      (
        await self.db.execute(
          stmt.order_by(
            TradeIntentRecord.created_at.desc(),
            TradeIntentRecord.id.desc(),
          ).limit(safe_first + 1)
        )
      ).scalars().all()
    )
    return rows[:safe_first], len(rows) > safe_first

  async def find_by_status(self, status: str) -> List[TradeIntentRecord]:
    """根据状态获取交易意图。"""
    result = await self.db.execute(
      select(TradeIntentRecord).filter(TradeIntentRecord.status == status)
    )
    return list(result.scalars().all())

  async def find_by_direction(self, direction: str) -> List[TradeIntentRecord]:
    """根据买卖方向获取交易意图。"""
    result = await self.db.execute(
      select(TradeIntentRecord).filter(TradeIntentRecord.direction == direction)
    )
    return list(result.scalars().all())

  async def find_by_instrument(self, instrument_code: str) -> List[TradeIntentRecord]:
    """根据交易标的获取交易意图。"""
    result = await self.db.execute(
      select(TradeIntentRecord).filter(
        TradeIntentRecord.instrument_code == instrument_code
      )
    )
    return list(result.scalars().all())

  async def find_pending_intents(
    self, strategy_run_id: str = None
  ) -> List[TradeIntentRecord]:
    """获取待路由的交易意图。"""
    stmt = select(TradeIntentRecord).filter(TradeIntentRecord.status == "PENDING")
    if strategy_run_id:
      stmt = stmt.filter(TradeIntentRecord.strategy_run_id == strategy_run_id)

    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_pending_approvals(
    self,
    strategy_run_id: str,
    *,
    limit: int = 50,
  ) -> List[TradeIntentRecord]:
    """Return manual-confirm intents that still await an operator decision."""

    result = await self.db.execute(
      select(TradeIntentRecord)
      .filter(TradeIntentRecord.strategy_run_id == strategy_run_id)
      .filter(TradeIntentRecord.status == "AWAITING_APPROVAL")
      .order_by(desc(TradeIntentRecord.created_at))
      .limit(max(1, min(int(limit or 50), 200)))
    )
    return list(result.scalars().all())

  async def create_intent(self, intent_data: Dict[str, Any]) -> TradeIntentRecord:
    """创建交易意图记录。"""
    intent = TradeIntentRecord(**self._normalize_payload(intent_data))
    self.db.add(intent)
    await self.db.commit()
    await self.db.refresh(intent)
    return intent

  async def update_intent(
    self, intent_id: str, intent_data: Dict[str, Any]
  ) -> Optional[TradeIntentRecord]:
    """更新交易意图记录。"""
    intent = await self.find_by_id(intent_id)
    if intent:
      for key, value in self._normalize_payload(intent_data).items():
        setattr(intent, key, value)
      await self.db.commit()
      await self.db.refresh(intent)
    return intent

  async def update_intent_status(
    self, intent_id: str, status: str, **updates: Any
  ) -> Optional[TradeIntentRecord]:
    """更新交易意图状态。"""
    intent = await self.find_by_id(intent_id)
    if intent:
      intent.status = status
      for key, value in updates.items():
        setattr(intent, key, value)
      await self.db.commit()
      await self.db.refresh(intent)
    return intent

  async def mark_as_executed(
    self, intent_id: str, executed_price: float, executed_volume: int, executed_time
  ) -> Optional[TradeIntentRecord]:
    """标记交易意图为已成交。"""
    intent = await self.find_by_id(intent_id)
    if intent:
      intent.status = "FILLED"
      intent.executed_price = executed_price
      intent.executed_volume = executed_volume
      intent.executed_time = executed_time
      await self.db.commit()
      await self.db.refresh(intent)
    return intent

  async def delete_intent(self, intent_id: str) -> bool:
    """删除交易意图。"""
    intent = await self.find_by_id(intent_id)
    if intent:
      await self.db.delete(intent)
      await self.db.commit()
      return True
    return False

  async def bulk_create_intents(
    self, intents_data: List[Dict[str, Any]]
  ) -> List[TradeIntentRecord]:
    """批量创建交易意图。"""
    intents = [
      TradeIntentRecord(**self._normalize_payload(intent_data))
      for intent_data in intents_data
    ]
    self.db.add_all(intents)
    await self.db.commit()
    for intent in intents:
      await self.db.refresh(intent)
    return intents
