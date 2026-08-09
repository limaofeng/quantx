"""
Strategy 模型定义
"""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import ARRAY, Column, Enum, String, Text
from sqlalchemy.orm import relationship

from quantx_infrastructure.database.relational_base import BaseModel, TimestampMixin
from quantx_infrastructure.models.enums import (
  RiskLevel,
  StrategyCategory,
  StrategyInstrumentScope,
  StrategyInstrumentUniverseMode,
  StrategyStatus,
)
from quantx_infrastructure.models.parameter_schema import (
  ParameterSchema,
  ParameterSchemaType,
  extract_default_parameters,
)


class Strategy(BaseModel, TimestampMixin):
  """策略模板表"""

  __tablename__ = "strategies"
  __allow_unmapped__ = True

  name = Column(String(100), nullable=False, comment="策略名称")
  """策略名称，用于标识和显示"""

  description = Column(Text, comment="策略描述")
  """策略详细描述，说明策略逻辑、适用场景等"""

  file_path = Column(String(255), nullable=False, comment="策略文件路径")
  """策略文件相对路径，如 quantx_domain/strategies/examples/rsi_strategy.py"""

  class_name = Column(String(100), nullable=False, comment="策略类名")
  """策略类名，用于动态加载，如 RSIStrategy"""
  category = Column(
    Enum(
      StrategyCategory,
      name="strategy_category",
      create_constraint=True,
      native_enum=True,
    ),
    nullable=True,
    comment="策略分类",
  )
  """策略分类: trend_following/mean_reversion/momentum/volatility/arbitrage/market_making"""

  risk_level = Column(
    Enum(RiskLevel, name="risk_level", create_constraint=True, native_enum=True),
    nullable=True,
    comment="风险等级",
  )
  """风险等级: low（低风险）/medium（中风险）/high（高风险）/very_high（极高风险）"""

  instrument_scope = Column(
    Enum(
      StrategyInstrumentScope,
      name="strategy_instrument_scope",
      create_constraint=True,
      native_enum=True,
    ),
    nullable=True,
    comment="策略标的范围",
  )
  """策略标的范围: single（单标的）/multi（多标的）"""

  instrument_universe_mode = Column(
    Enum(
      StrategyInstrumentUniverseMode,
      name="strategy_instrument_universe_mode",
      create_constraint=True,
      native_enum=True,
    ),
    nullable=False,
    default=StrategyInstrumentUniverseMode.STATIC,
    comment="策略标的池来源",
  )

  tags: List[str] = Column(ARRAY(String), default=list, comment="策略标签")
  """策略标签列表，用于分类和搜索"""

  # 使用 TypeDecorator 自动处理序列化
  parameter_schema: Optional[ParameterSchema] = Column(
    ParameterSchemaType,
    nullable=True,
    comment="参数配置 Schema（JSON Schema + UI 扩展）",
  )
  """
  参数配置 Schema，基于 JSON Schema 标准
  包含参数名称、类型、默认值、取值范围、描述、UI配置等
  自动序列化/反序列化为 ParameterSchema 对象
  """

  version = Column(String(20), nullable=True, comment="策略版本号")
  """策略版本号，语义化版本格式（如 1.0.0）"""

  code_hash = Column(String(64), nullable=True, comment="代码哈希值（SHA256）")
  """策略代码的 SHA256 哈希值，用于检测代码变更"""
  status = Column(
    Enum(
      StrategyStatus,
      name="strategy_status",
      create_constraint=True,
      native_enum=True,
    ),
    default=StrategyStatus.ACTIVE,
    nullable=False,
    comment="策略状态",
  )
  """策略状态: ACTIVE（激活可用）/UPGRADING（待升级）/DEPRECATED（已弃用）"""

  # 关联关系
  runs = relationship("StrategyRun", back_populates="strategy")
  """该策略的所有运行实例"""

  @property
  def is_active(self) -> bool:
    """
    策略是否激活（仅 ACTIVE 状态为激活）

    Returns:
        是否激活
    """
    return self.status == StrategyStatus.ACTIVE

  @property
  def default_parameters(self) -> Dict[str, Any]:
    """
    从 parameter_schema 中提取默认参数

    Returns:
        默认参数字典
    """
    if self.parameter_schema:
      return extract_default_parameters(self.parameter_schema.model_dump())
    return {}

  def to_dict(self):
    """序列化为字典"""
    return {
      "id": self.id,
      "name": self.name,
      "description": self.description,
      "file_path": self.file_path,
      "class_name": self.class_name,
      "category": self.category.value if self.category else None,
      "risk_level": self.risk_level.value if self.risk_level else None,
      "instrument_scope": self.instrument_scope.value
      if self.instrument_scope
      else None,
      "instrument_universe_mode": self.instrument_universe_mode.value
      if self.instrument_universe_mode
      else StrategyInstrumentUniverseMode.STATIC.value,
      "tags": json.loads(self.tags) if self.tags else [],
      "parameter_schema": self.parameter_schema.model_dump()
      if self.parameter_schema
      else None,
      "default_parameters": self.default_parameters,
      "version": self.version,
      "code_hash": self.code_hash,
      "status": self.status.value if self.status else None,
      "is_active": self.is_active,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }
