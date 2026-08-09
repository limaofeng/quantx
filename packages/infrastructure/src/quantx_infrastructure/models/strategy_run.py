"""
StrategyRun 模型定义
"""

from typing import List, Optional

from sqlalchemy import (
  ARRAY,
  JSON,
  Boolean,
  Column,
  DateTime,
  Enum,
  Float,
  ForeignKey,
  Integer,
  String,
  Text,
)
from sqlalchemy.orm import relationship

from quantx_infrastructure.database.relational_base import BaseModel, TimestampMixin
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
from quantx_infrastructure.models.execution_metrics import (
  ExecutionMetrics,
  ExecutionMetricsType,
)


class StrategyRun(BaseModel, TimestampMixin):
  """策略运行实例表"""

  __tablename__ = "strategy_runs"

  id = Column(String(36), primary_key=True, comment="运行实例ID（UUID）")
  """运行实例唯一标识（UUID格式）"""

  name = Column(String(100), nullable=False, comment="运行实例名称")
  """运行实例名称"""

  strategy_id = Column(
    Integer, ForeignKey("strategies.id"), nullable=False, comment="关联的策略模板ID"
  )
  """关联的策略模板ID"""

  parameters = Column(JSON, comment="运行参数")
  """策略运行参数，自动序列化为 JSON"""

  status = Column(
    Enum(
      StrategyRunStatus,
      name="strategy_run_status",
      create_constraint=True,
      native_enum=True,
    ),
    nullable=False,
    comment="运行状态",
  )
  """运行状态: PENDING（待运行）/RUNNING（运行中）/COMPLETED（已完成）/FAILED（失败）/STOPPED（已停止）/ERROR（错误）"""

  start_time = Column(DateTime, comment="开始时间")
  """策略运行开始时间"""

  stop_time = Column(DateTime, comment="停止时间")
  """策略运行停止时间"""

  error_message = Column(Text, comment="错误信息")
  """运行错误信息（状态为ERROR时）"""

  user_id = Column(String(50), index=True, comment="用户ID")
  """创建该运行实例的用户ID"""

  # 运行环境信息
  pid = Column(Integer, comment="进程ID")
  """运行该策略的进程ID"""

  host = Column(String(100), comment="主机名")
  """运行该策略的主机名"""

  mode = Column(
    Enum(
      StrategyRunMode,
      name="strategy_run_mode",
      create_constraint=True,
      native_enum=True,
    ),
    default=StrategyRunMode.BACKTEST,
    nullable=False,
    comment="运行模式",
  )
  """运行模式: BACKTEST（回测）/PAPER（模拟盘，Paper Trading）/LIVE（实盘，Live Trading）"""

  instruments: List[str] = Column(ARRAY(String), default=list, comment="交易标的列表")
  """交易标的列表（如股票代码、期货合约等）"""

  initial_capital = Column(Float, default=1000000.0, comment="初始资金")
  """策略运行的初始资金"""

  metrics: Optional[ExecutionMetrics] = Column(ExecutionMetricsType, comment="执行指标")
  """策略执行过程中的各项指标，使用 ExecutionMetrics 类型"""

  # 版本追踪
  strategy_version = Column(String(20), comment="策略版本")
  """运行时使用的策略代码版本号"""

  upgrade_required = Column(Boolean, default=False, comment="是否需要升级")
  """策略代码已更新，需要用户确认是否升级到新版本"""

  # 关联关系
  strategy = relationship("Strategy", back_populates="runs")
  """关联的策略模板"""

  trade_intents = relationship("TradeIntentRecord", back_populates="strategy_run")
  """该运行实例生成的所有交易意图"""

  decision_traces = relationship("StrategyDecisionTraceRecord", back_populates="strategy_run")
  """该运行实例的策略决策审计记录"""

  backtests = relationship(
      "StrategyBacktest",
      back_populates="strategy_run",
      order_by="desc(StrategyBacktest.version)",
  )
  """该运行实例的所有回测历史"""

  def to_dict(self):
    """序列化为字典"""
    # 从 metrics 中提取指标数据用于向后兼容
    metrics_dict = self.metrics or {}

    return {
      "id": self.id,
      "name": self.name,
      "strategy_id": self.strategy_id,
      "strategy_name": self.strategy.name if self.strategy else None,
      "parameters": self.parameters or {},
      "status": self.status.value if self.status else None,
      "start_time": self.start_time.isoformat() if self.start_time else None,
      "stop_time": self.stop_time.isoformat() if self.stop_time else None,
      "error_message": self.error_message,
      "user_id": self.user_id,
      # 运行环境
      "pid": self.pid,
      "host": self.host,
      "mode": self.mode,
      "instruments": self.instruments or [],
      "initial_capital": self.initial_capital,
      "metrics": metrics_dict,
      # 从 metrics 中提取常用指标
      "profit_loss": metrics_dict.get("total_pnl", 0.0),
      "total_trades": metrics_dict.get("trades_executed", 0),
      "last_heartbeat": metrics_dict.get("last_heartbeat"),
      "trade_intents_count": metrics_dict.get("trade_intents_generated", 0),
      "orders_count": metrics_dict.get("orders_placed", 0),
      "max_drawdown": metrics_dict.get("max_drawdown", 0.0),
      "win_rate": metrics_dict.get("win_rate", 0.0),
      "sharpe_ratio": metrics_dict.get("sharpe_ratio", 0.0),
      # 版本
      "strategy_version": self.strategy_version,
      "upgrade_required": self.upgrade_required,
      # 时间戳
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }
