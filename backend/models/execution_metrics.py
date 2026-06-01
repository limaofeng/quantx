"""
执行指标类型定义
提供类型安全的执行指标访问和验证
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy import JSON, TypeDecorator


class ExecutionMetrics(BaseModel):
  """执行指标的 Pydantic 模型（用于数据库存储）"""

  start_time: datetime = Field(..., description="开始时间")
  end_time: Optional[datetime] = Field(None, description="结束时间")
  trade_intents_generated: int = Field(0, description="生成交易意图数")
  orders_placed: int = Field(0, description="下单数")
  trades_executed: int = Field(0, description="成交数")
  total_pnl: float = Field(0.0, description="总盈亏")
  max_drawdown: float = Field(0.0, description="最大回撤")
  win_rate: float = Field(0.0, description="胜率")
  sharpe_ratio: float = Field(0.0, description="夏普比率")
  error_count: int = Field(0, description="错误计数")
  rejected_orders: int = Field(0, description="拒单数")
  cancelled_orders: int = Field(0, description="撤单数")
  last_heartbeat: datetime = Field(..., description="最后心跳时间")
  initial_capital: float = Field(1000000.0, description="初始资金")
  current_capital: float = Field(1000000.0, description="当前资金")
  total_return_pct: float = Field(0.0, description="累计收益率百分比")
  max_drawdown_pct: float = Field(0.0, description="最大回撤百分比")
  win_rate_pct: float = Field(0.0, description="胜率百分比")
  performance: Dict[str, Any] = Field(default_factory=dict, description="Broker 原始绩效")
  performance_snapshot_path: Optional[str] = Field(
    None, description="回测绩效快照路径"
  )

  class Config:
    json_encoders = {datetime: lambda v: v.isoformat()}


class ExecutionMetricsType(TypeDecorator):
  """自动序列化 ExecutionMetrics 的 SQLAlchemy 类型"""

  impl = JSON
  cache_ok = True

  def process_bind_param(self, value, dialect):
    """写入数据库：dict/Pydantic → JSON"""
    if value is None:
      return None

    if isinstance(value, ExecutionMetrics):
      return value.model_dump(mode="json")

    if isinstance(value, dict):
      # 验证并转换
      metrics = ExecutionMetrics(**value)
      return metrics.model_dump(mode="json")

    raise TypeError(f"Expected ExecutionMetrics or dict, got {type(value)}")

  def process_result_value(self, value, dialect):
    """从数据库读取：JSON → dict"""
    if value is None:
      return None

    # 返回字典而非 Pydantic 对象,方便使用
    return value
