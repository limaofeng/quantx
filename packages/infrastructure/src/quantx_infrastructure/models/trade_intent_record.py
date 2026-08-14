"""TradeIntentRecord 模型定义。"""

from sqlalchemy import (
  JSON,
  Column,
  DateTime,
  Float,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
)
from sqlalchemy.orm import relationship

from quantx_infrastructure.database.relational_base import BaseModel, TimestampMixin


class TradeIntentRecord(BaseModel, TimestampMixin):
  """策略交易意图记录表 - 记录策略输出意图及后续执行状态。"""

  __tablename__ = "strategy_trade_intents"
  __table_args__ = (
    Index(
      "ix_trade_intent_run_reason_direction_created",
      "strategy_run_id",
      "reason",
      "direction",
      "created_at",
      "id",
    ),
    Index(
      "ix_trade_intent_owner_created",
      "owner_type",
      "owner_id",
      "created_at",
    ),
    Index(
      "ix_trade_intent_account_status_created",
      "account_id",
      "status",
      "created_at",
    ),
  )

  id = Column(String(36), primary_key=True)  # UUID，重写基类的id
  strategy_run_id = Column(String(36), ForeignKey("strategy_runs.id"), nullable=True)
  owner_type = Column(String(32), nullable=False, default="STRATEGY_RUN")
  owner_id = Column(String(128), nullable=False, default="")
  account_id = Column(String(50), nullable=True)
  strategy_id = Column(String(64), nullable=True)
  instrument_code = Column(String(20), nullable=False)  # 标的代码
  direction = Column(String(20), nullable=False)  # BUY, SELL, HOLD
  bucket = Column(String(40), nullable=False, default="core")
  reason = Column(String(120), nullable=False, default="")
  priority = Column(String(40), nullable=False, default="NORMAL")
  intent_type = Column(String(40), nullable=True)
  confidence = Column(Float, nullable=False, default=1.0)
  target_amount = Column(Float, nullable=True)
  target_position_pct = Column(Float, nullable=True)
  target_volume = Column(Integer, nullable=True)
  limit_price_hint = Column(Float, nullable=True)
  trace_id = Column(String(64), nullable=True)
  risk_decision_id = Column(String(64), nullable=True)
  order_id = Column(String(64), nullable=True)
  status = Column(
    String(20), nullable=False, default="PENDING"
  )  # PENDING, ROUTED, REJECTED, DELAYED, FILLED, PARTIAL_FILLED, CANCELLED
  executed_price = Column(Float)  # 实际执行价格
  executed_volume = Column(Integer)  # 实际执行数量
  executed_time = Column(DateTime)  # 执行时间
  intent_metadata = Column("metadata", JSON, nullable=False, default=dict)
  notes = Column(Text)  # 备注信息

  # 关联关系
  strategy_run = relationship("StrategyRun", back_populates="trade_intents")

  def to_dict(self):
    """序列化为字典"""
    return {
      "id": self.id,
      "strategy_run_id": self.strategy_run_id,
      "owner_type": self.owner_type,
      "owner_id": self.owner_id,
      "account_id": self.account_id,
      "strategy_id": self.strategy_id,
      "instrument_code": self.instrument_code,
      "direction": self.direction,
      "bucket": self.bucket,
      "reason": self.reason,
      "priority": self.priority,
      "intent_type": self.intent_type,
      "confidence": self.confidence,
      "target_amount": self.target_amount,
      "target_position_pct": self.target_position_pct,
      "target_volume": self.target_volume,
      "limit_price_hint": self.limit_price_hint,
      "trace_id": self.trace_id,
      "risk_decision_id": self.risk_decision_id,
      "order_id": self.order_id,
      "status": self.status,
      "executed_price": self.executed_price,
      "executed_volume": self.executed_volume,
      "executed_time": self.executed_time.isoformat() if self.executed_time else None,
      "metadata": self.intent_metadata or {},
      "notes": self.notes,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }
