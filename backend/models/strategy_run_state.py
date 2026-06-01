"""
策略运行时状态模型 V2

- StrategyRunPosition: 独立持仓表
- StrategyRunState: 资金与自定义状态表
- (TradeIntent 和 Order 使用各自的独立表)
"""

from typing import Any, Dict

from sqlalchemy import JSON, Column, Float, Integer, String
from sqlalchemy.orm import relationship

from database.relational_base import BaseModel, TimestampMixin


class StrategyRunPosition(BaseModel, TimestampMixin):
    """策略运行时持仓表 - 结构化存储"""

    __tablename__ = "strategy_run_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), nullable=False, index=True, comment="运行实例ID")
    instrument_code = Column(String(20), nullable=False, comment="标的代码")

    # 持仓数量
    long_volume = Column(Integer, default=0, comment="多头持仓")
    short_volume = Column(Integer, default=0, comment="空头持仓")

    # 价格信息
    long_avg_price = Column(Float, default=0.0, comment="多头均价")
    short_avg_price = Column(Float, default=0.0, comment="空头均价")
    last_price = Column(Float, default=0.0, comment="最新价格")

    # 盈亏信息
    market_value = Column(Float, default=0.0, comment="市值")
    pnl = Column(Float, default=0.0, comment="浮动盈亏")

    def to_dict(self):
        return {
            "instrument_code": self.instrument_code,
            "long_volume": self.long_volume,
            "short_volume": self.short_volume,
            "long_avg_price": self.long_avg_price,
            "short_avg_price": self.short_avg_price,
            "market_value": self.market_value,
            "pnl": self.pnl,
            "last_price": self.last_price,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class StrategyRunState(BaseModel, TimestampMixin):
    """
    策略运行时状态表（资金与自定义状态）

    用于存储：
    1. 策略当前的资金状况（可用资金、冻结资金、总资产）
    2. 策略的自定义状态（网格配置、中间变量等）- JSON
    """

    __tablename__ = "strategy_run_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), unique=True, nullable=False, index=True, comment="运行实例ID")

    # 资金状态
    cash = Column(Float, default=0.0, comment="可用资金")
    frozen_cash = Column(Float, default=0.0, comment="冻结资金")
    total_asset = Column(Float, default=0.0, comment="总资产")

    # 自定义状态 (JSON)
    custom_state = Column(JSON, default=dict, comment="策略自定义状态")

    # 版本控制（乐观锁）
    version = Column(Integer, default=1, comment="版本号")

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "cash": self.cash,
            "frozen_cash": self.frozen_cash,
            "total_asset": self.total_asset,
            "custom_state": self.custom_state,
            "version": self.version,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
