"""交易意图仓储层 - 处理 TradeIntentRecord 相关操作。"""

from typing import Any, Dict, List, Optional

from sqlalchemy import asc, delete, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.execution_owner import validate_owner_environment
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

  @staticmethod
  def _prepare_create_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the closed owner/idempotency projection before persistence.

    The ORM columns intentionally have no Python defaults.  Keeping this
    check at the repository boundary makes omitted owner data fail before a
    session is mutated, and prevents callers from smuggling enum instances or
    non-canonical environments into the durable fact.
    """

    payload = dict(data or {})
    owner_type, owner_id, environment = validate_owner_environment(
      payload.get("owner_type"),
      payload.get("owner_id"),
      payload.get("environment"),
    )
    idempotency_key = payload.get("idempotency_key")
    if (
      not isinstance(idempotency_key, str)
      or not idempotency_key
      or idempotency_key != idempotency_key.strip()
      or len(idempotency_key) > 128
      or any(
        ord(character) <= 31 or ord(character) == 127
        for character in idempotency_key
      )
    ):
      raise ValueError("IDEMPOTENCY_KEY_INVALID")
    payload["owner_type"] = owner_type
    payload["owner_id"] = owner_id
    payload["environment"] = environment
    return payload

  @staticmethod
  def _validate_immutable_owner_update(
    existing: TradeIntentRecord,
    incoming: Dict[str, Any],
  ) -> None:
    owner_fields = {
      field
      for field in ("owner_type", "owner_id", "environment")
      if field in incoming
    }
    if not owner_fields:
      return
    if owner_fields != {"owner_type", "owner_id", "environment"}:
      raise ValueError("OWNER_ENVIRONMENT_IMMUTABLE")
    owner_type, owner_id, environment = validate_owner_environment(
      incoming.get("owner_type"),
      incoming.get("owner_id"),
      incoming.get("environment"),
    )
    incoming["owner_type"] = owner_type
    incoming["owner_id"] = owner_id
    incoming["environment"] = environment
    if (owner_type, owner_id, environment) != (
      existing.owner_type,
      existing.owner_id,
      existing.environment,
    ):
      raise ValueError("OWNER_ENVIRONMENT_IMMUTABLE")

  async def find_by_id(self, intent_id: str) -> Optional[TradeIntentRecord]:
    """根据ID获取交易意图。"""
    result = await self.db.execute(
      select(TradeIntentRecord).filter(TradeIntentRecord.id == intent_id)
    )
    return result.scalar_one_or_none()

  async def delete_for_strategy_run(
    self,
    strategy_run_id: str,
    *,
    commit: bool = True,
  ) -> int:
    """Delete run-local intents before replaying a new backtest version."""

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    result = await self.db.execute(
      delete(TradeIntentRecord).where(
        TradeIntentRecord.strategy_run_id == normalized_run_id
      )
    )
    if commit:
      await self.db.commit()
    return int(result.rowcount or 0)

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
        f"V3 候选恢复查询超过有界上限: run_id={normalized_run_id}, limit={row_limit}"
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
    intent = TradeIntentRecord(
      **self._prepare_create_payload(self._normalize_payload(intent_data))
    )
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

    return (await self.create_intents_idempotent([intent_data]))[0]

  async def create_intents_idempotent(
    self, intent_data: List[Dict[str, Any]]
  ) -> List[TradeIntentRecord]:
    """Accept a complete strategy output in one transaction, or accept none."""
    normalized = [
      self._prepare_create_payload(self._normalize_payload(item))
      for item in intent_data
    ]
    ids = [str(item.get("id") or "").strip() for item in normalized]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
      raise ValueError("交易意图标识不能为空或重复")
    records = []
    for intent_id, payload in zip(ids, normalized, strict=True):
      existing = await self.find_by_id(intent_id)
      if existing is not None:
        self._validate_idempotent_create(existing, payload)
        records.append(existing)
      else:
        records.append(TradeIntentRecord(**payload))
    self.db.add_all(records)
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      # Only a fully committed exact retry may satisfy this batch. A partial
      # overlap must not make the other intents appear accepted.
      records = []
      for intent_id, payload in zip(ids, normalized, strict=True):
        existing = await self.find_by_id(intent_id)
        if existing is None:
          raise
        self._validate_idempotent_create(existing, payload)
        records.append(existing)
    return records

  @staticmethod
  def _validate_idempotent_create(
    existing: TradeIntentRecord,
    incoming: Dict[str, Any],
  ) -> None:
    immutable_fields = (
      "owner_type",
      "owner_id",
      "environment",
      "idempotency_key",
      "strategy_run_id",
      "account_id",
      "strategy_id",
      "instrument_code",
      "direction",
      "bucket",
      "reason",
      "priority",
      "intent_type",
      "target_amount",
      "target_position_pct",
      "target_volume",
      "limit_price_hint",
    )
    mismatched = [
      field
      for field in immutable_fields
      if field in incoming
      and getattr(existing, field, None) != incoming.get(field)
    ]
    existing_metadata = dict(existing.intent_metadata or {})
    incoming_metadata = dict(incoming.get("intent_metadata") or {})
    for field, value in incoming_metadata.items():
      if existing_metadata.get(field) != value:
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
      normalized = self._normalize_payload(intent_data)
      self._validate_immutable_owner_update(intent, normalized)
      if (
        "idempotency_key" in normalized
        and normalized["idempotency_key"] != intent.idempotency_key
      ):
        raise ValueError("IDEMPOTENCY_KEY_IMMUTABLE")
      for key, value in normalized.items():
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
      self._validate_immutable_owner_update(intent, updates)
      if (
        "idempotency_key" in updates
        and updates["idempotency_key"] != intent.idempotency_key
      ):
        raise ValueError("IDEMPOTENCY_KEY_IMMUTABLE")
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
      TradeIntentRecord(
        **self._prepare_create_payload(self._normalize_payload(intent_data))
      )
      for intent_data in intents_data
    ]
    self.db.add_all(intents)
    await self.db.commit()
    for intent in intents:
      await self.db.refresh(intent)
    return intents
