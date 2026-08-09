"""
StrategyBacktest 模型定义

回测结果表，支持版本管理和详细数据文件化存储
"""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from quantx_infrastructure.database.relational_base import BaseModel, TimestampMixin


class StrategyBacktest(BaseModel, TimestampMixin):
    """策略回测结果表 - 存储回测元数据，详细数据存储在文件中"""

    __tablename__ = "strategy_backtests"

    id = Column(String(36), primary_key=True, comment="回测ID（UUID）")
    """回测唯一标识"""

    strategy_run_id = Column(
        String(36),
        ForeignKey("strategy_runs.id"),
        nullable=False,
        index=True,
        comment="关联的策略运行实例ID",
    )
    """关联的 StrategyRun（作为配置容器）"""

    version = Column(Integer, nullable=False, default=1, comment="版本号")
    """回测版本号，同一个 StrategyRun 下自增"""

    # 参数快照
    parameters = Column(JSON, comment="回测时使用的参数")
    """回测执行时的参数快照"""

    instruments = Column(JSON, comment="交易标的列表")
    """回测时的交易标的列表"""

    # 回测时间范围
    backtest_start_time = Column(DateTime, comment="回测开始时间")
    """回测数据的起始时间"""

    backtest_end_time = Column(DateTime, comment="回测结束时间")
    """回测数据的结束时间"""

    # 执行时间
    start_time = Column(DateTime, comment="执行开始时间")
    """回测任务开始执行的时间"""

    end_time = Column(DateTime, comment="执行结束时间")
    """回测任务结束的时间"""

    # 结果
    metrics = Column(JSON, comment="回测绩效指标")
    """回测结果指标（收益率、胜率、夏普比率等）"""

    status = Column(String(20), default="PENDING", comment="状态")
    """回测状态: PENDING, RUNNING, COMPLETED, ERROR"""

    error_message = Column(Text, comment="错误信息")
    """回测失败时的错误信息"""

    # 详细数据存储路径
    result_path = Column(String(255), comment="结果文件路径")
    """存储详细数据（信号、订单、成交）的文件路径，如 backtests/{id}.jsonl"""

    # 关联关系
    strategy_run = relationship("StrategyRun", back_populates="backtests")
    """关联的策略运行实例"""

    def to_dict(self):
        """序列化为字典"""
        return {
            "id": self.id,
            "strategy_run_id": self.strategy_run_id,
            "version": self.version,
            "parameters": self.parameters,
            "instruments": self.instruments,
            "backtest_start_time": (
                self.backtest_start_time.isoformat() if self.backtest_start_time else None
            ),
            "backtest_end_time": (
                self.backtest_end_time.isoformat() if self.backtest_end_time else None
            ),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "metrics": self.metrics,
            "status": self.status,
            "error_message": self.error_message,
            "result_path": self.result_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
