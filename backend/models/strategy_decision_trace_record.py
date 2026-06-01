"""Persistent StrategyInput -> StrategyOutput audit records."""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String
from sqlalchemy.orm import relationship

from database.relational_base import BaseModel, TimestampMixin


class StrategyDecisionTraceRecord(BaseModel, TimestampMixin):
  """Durable strategy decision trace for UI audit and execution drilldown."""

  __tablename__ = "strategy_decision_traces"

  id = Column(String(36), primary_key=True)
  trace_id = Column(String(64), index=True, nullable=False)
  strategy_run_id = Column(String(36), ForeignKey("strategy_runs.id"), index=True, nullable=False)
  strategy_id = Column(String(64), nullable=True)
  instrument_code = Column(String(20), index=True, nullable=False)
  decided_at = Column(DateTime, nullable=False)
  input_summary = Column(JSON, nullable=False, default=dict)
  output_summary = Column(JSON, nullable=False, default=dict)
  trade_intents = Column(JSON, nullable=False, default=list)
  state_patch = Column(JSON, nullable=False, default=dict)
  decision_trace = Column(JSON, nullable=False, default=dict)

  strategy_run = relationship("StrategyRun", back_populates="decision_traces")

  def to_dict(self):
    return {
      "id": self.id,
      "trace_id": self.trace_id,
      "strategy_run_id": self.strategy_run_id,
      "strategy_id": self.strategy_id,
      "instrument_code": self.instrument_code,
      "decided_at": self.decided_at.isoformat() if self.decided_at else None,
      "input_summary": self.input_summary or {},
      "output_summary": self.output_summary or {},
      "trade_intents": self.trade_intents or [],
      "state_patch": self.state_patch or {},
      "decision_trace": self.decision_trace or {},
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }
