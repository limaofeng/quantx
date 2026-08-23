"""交易意图仓储层 - 处理 TradeIntentRecord 相关操作。"""

from typing import Any, Dict, List, Optional

from sqlalchemy import asc, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

_V3_MANUAL_RECOVERY_MAX_ROWS = 4096


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

  async def find_v3_manual_candidate_recovery_intents(
    self,
    strategy_run_id: str,
    *,
    linked_intent_ids: Optional[List[str]] = None,
    max_rows: int = _V3_MANUAL_RECOVERY_MAX_ROWS,
  ) -> List[TradeIntentRecord]:
    """Return active V3 manual-entry rows for one exact strategy run.

    Account ownership is deliberately validated by ``RuntimeStateManager``
    against both the owning StrategyRun and each returned row.  This query is
    run-scoped first so no startup recovery can inspect another runtime.
    """

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    normalized_linked_ids = sorted(
      {
        str(intent_id or "").strip()
        for intent_id in list(linked_intent_ids or [])
        if str(intent_id or "").strip()
      }
    )
    row_limit = max(1, min(int(max_rows or 1), _V3_MANUAL_RECOVERY_MAX_ROWS))
    if len(normalized_linked_ids) > row_limit:
      raise RuntimeError(
        "V3 候选恢复关联意图超过有界上限: "
        f"count={len(normalized_linked_ids)}, limit={row_limit}"
      )
    # Terminal recovery notes are historical audit, not an open-work index.
    # A terminal row is relevant only when the current RuntimeState links its
    # exact primary key; otherwise every restart would reload the run's full
    # recovery history forever.
    recovery_scope = [
      TradeIntentRecord.status.in_(("PENDING", "AWAITING_APPROVAL")),
    ]
    if normalized_linked_ids:
      recovery_scope.append(TradeIntentRecord.id.in_(normalized_linked_ids))
    result = await self.db.execute(
      select(TradeIntentRecord)
      .filter(TradeIntentRecord.strategy_run_id == normalized_run_id)
      .filter(TradeIntentRecord.direction == "BUY")
      .filter(or_(*recovery_scope))
      .order_by(asc(TradeIntentRecord.created_at), asc(TradeIntentRecord.id))
      .limit(row_limit + 1)
    )
    rows = list(result.scalars().all())
    if len(rows) > row_limit:
      raise RuntimeError(
        "V3 候选恢复查询超过有界上限: "
        f"run_id={normalized_run_id}, limit={row_limit}"
      )
    candidates: List[TradeIntentRecord] = []
    for row in rows:
      metadata = dict(row.intent_metadata or {})
      try:
        schema_version = int(metadata.get("opportunity_schema_version") or 0)
      except (TypeError, ValueError, OverflowError):
        schema_version = 0
      if (
        schema_version >= 3
        and str(metadata.get("t_trade_role") or "").strip().lower() == "entry"
        and str(metadata.get("execution_mode") or "").strip().upper()
        == "MANUAL_CONFIRM"
        and str(metadata.get("candidate_id") or "").strip()
      ):
        candidates.append(row)
    return candidates

  async def create_intent(self, intent_data: Dict[str, Any]) -> TradeIntentRecord:
    """创建交易意图记录。"""
    intent = TradeIntentRecord(**self._normalize_payload(intent_data))
    self.db.add(intent)
    await self.db.commit()
    await self.db.refresh(intent)
    return intent

  async def create_intent_idempotent(
    self,
    intent_data: Dict[str, Any],
  ) -> TradeIntentRecord:
    """Append an intent once without ever resetting an existing lifecycle.

    Deterministic intent IDs are retry keys, not permission to upsert mutable
    trading truth. An exact retry may reuse the existing initial record; a
    cross-run collision, identity mismatch, or attempt to reset an advanced
    status fails closed.
    """

    normalized = self._normalize_payload(intent_data)
    intent_id = str(normalized.get("id") or "").strip()
    if not intent_id:
      raise ValueError("交易意图标识不能为空")
    existing = await self.find_by_id(intent_id)
    if existing is not None:
      self._validate_idempotent_create(existing, normalized)
      return existing

    intent = TradeIntentRecord(**normalized)
    self.db.add(intent)
    try:
      await self.db.commit()
    except IntegrityError:
      # A concurrent exact retry may win the unique-key race. Re-read and
      # perform the same immutable identity/status validation.
      await self.db.rollback()
      existing = await self.find_by_id(intent_id)
      if existing is None:
        raise
      self._validate_idempotent_create(existing, normalized)
      return existing
    await self.db.refresh(intent)
    return intent

  @staticmethod
  def _validate_idempotent_create(
    existing: TradeIntentRecord,
    incoming: Dict[str, Any],
  ) -> None:
    immutable_fields = (
      "strategy_run_id",
      "account_id",
      "strategy_id",
      "instrument_code",
      "direction",
      "bucket",
      "reason",
    )
    mismatched = [
      field
      for field in immutable_fields
      if field in incoming
      and str(getattr(existing, field, "") or "")
      != str(incoming.get(field) or "")
    ]
    existing_metadata = dict(existing.intent_metadata or {})
    incoming_metadata = dict(incoming.get("intent_metadata") or {})
    try:
      opportunity_schema_version = int(
        incoming_metadata.get("opportunity_schema_version") or 0
      )
    except (TypeError, ValueError, OverflowError):
      opportunity_schema_version = 0
    if opportunity_schema_version >= 3:
      for field in (
        "candidate_id",
        "candidate_fingerprint",
        "candidate_state_version",
        "config_version",
        "policy_version",
      ):
        if str(existing_metadata.get(field) or "") != str(
          incoming_metadata.get(field) or ""
        ):
          mismatched.append(f"metadata.{field}")
    existing_status = str(existing.status or "").upper()
    incoming_status = str(incoming.get("status") or "").upper()
    if existing_status != incoming_status:
      mismatched.append("status")
    if mismatched:
      raise ValueError(
        "TRADE_INTENT_IDEMPOTENCY_CONFLICT: "
        f"intent_id={existing.id}, fields={','.join(sorted(set(mismatched)))}"
      )

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
