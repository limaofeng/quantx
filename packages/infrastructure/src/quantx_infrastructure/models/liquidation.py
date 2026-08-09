"""
清仓管理相关数据模型
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, Numeric, String, Text

from quantx_infrastructure.database.relational_base import Base, TimestampMixin
from quantx_infrastructure.models.enums import AccountType


class LiquidationStatus:
  """清仓状态枚举"""

  PENDING = "PENDING"  # 等待执行
  IN_PROGRESS = "IN_PROGRESS"  # 执行中
  COMPLETED = "COMPLETED"  # 已完成
  PARTIAL_COMPLETED = "PARTIAL_COMPLETED"  # 部分完成
  FAILED = "FAILED"  # 失败
  CANCELLED = "CANCELLED"  # 已取消


class LiquidationType:
  """清仓类型枚举"""

  ALL_POSITIONS = "ALL_POSITIONS"  # 一键清仓
  SINGLE_POSITION = "SINGLE_POSITION"  # 个股清仓
  REDEMPTION = "REDEMPTION"  # 资金赎回


class ConditionalLiquidationStatus:
  """条件清仓单状态"""

  ACTIVE = "ACTIVE"
  SUBMITTED = "SUBMITTED"
  FAILED = "FAILED"
  CANCELLED = "CANCELLED"


class ConditionalLiquidationSellMode:
  """条件清仓卖出数量模式"""

  ALL_AVAILABLE = "ALL_AVAILABLE"
  PERCENT_AVAILABLE = "PERCENT_AVAILABLE"
  FIXED_VOLUME = "FIXED_VOLUME"


class LiquidationOrder(Base, TimestampMixin):
  """清仓订单表"""

  __tablename__ = "liquidation_orders"
  __allow_unmapped__ = True

  id = Column(String(36), primary_key=True, index=True, comment="清仓订单ID")
  """ 清仓订单ID """

  account_id = Column(String(50), nullable=False, comment="资金账号")
  """ 资金账号 """

  account_type = Column(Enum(AccountType), nullable=True, comment="账户类型")
  """ 账户类型 """

  liquidation_type = Column(String(20), nullable=False, comment="清仓类型")
  """ 清仓类型 """

  status = Column(
    String(20), nullable=False, default=LiquidationStatus.PENDING, comment="清仓状态"
  )
  """ 清仓状态 """

  # 股票信息（单股清仓时使用）
  stock_code = Column(String(20), nullable=True, comment="股票代码")
  """ 股票代码 """

  instrument_name = Column(String(50), nullable=True, comment="证券名称")
  """ 证券名称 """

  # 数量和金额
  target_volume = Column(Integer, comment="目标清仓数量")
  """ 目标清仓数量 """

  completed_volume = Column(Integer, default=0, comment="已完成数量")
  """ 已完成清仓数量 """

  target_amount = Column(Numeric(15, 2), comment="目标清仓金额")
  """ 目标清仓金额 """

  completed_amount = Column(Numeric(15, 2), default=0, comment="已完成金额")
  """ 已完成清仓金额 """

  # 关联订单
  related_order_ids = Column(Text, comment="关联的交易订单ID列表")
  """ 关联的交易订单ID列表，JSON格式 """

  # 执行信息
  start_time = Column(DateTime, comment="开始执行时间")
  """ 开始执行时间 """

  end_time = Column(DateTime, comment="结束时间")
  """ 结束时间 """

  retry_count = Column(Integer, default=0, comment="重试次数")
  """ 重试次数 """

  max_retry = Column(Integer, default=3, comment="最大重试次数")
  """ 最大重试次数 """

  # 备注和错误信息
  remark = Column(Text, comment="备注信息")
  """ 备注信息 """

  error_message = Column(Text, comment="错误信息")
  """ 错误信息 """

  def to_dict(self):
    """转换为字典格式"""
    return {
      "id": self.id,
      "account_id": self.account_id,
      "account_type": self.account_type.name if self.account_type else None,
      "liquidation_type": self.liquidation_type,
      "status": self.status,
      "stock_code": self.stock_code,
      "instrument_name": self.instrument_name,
      "target_volume": self.target_volume,
      "completed_volume": self.completed_volume,
      "target_amount": float(self.target_amount) if self.target_amount else None,
      "completed_amount": float(self.completed_amount)
      if self.completed_amount
      else None,
      "related_order_ids": self.related_order_ids,
      "start_time": self.start_time.isoformat() if self.start_time else None,
      "end_time": self.end_time.isoformat() if self.end_time else None,
      "retry_count": self.retry_count,
      "max_retry": self.max_retry,
      "remark": self.remark,
      "error_message": self.error_message,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }

  def __repr__(self):
    return f"<LiquidationOrder(id='{self.id}', type='{self.liquidation_type}', status='{self.status}')>"


class LiquidationLog(Base, TimestampMixin):
  """清仓操作日志表"""

  __tablename__ = "liquidation_logs"
  __allow_unmapped__ = True

  id = Column(String(36), primary_key=True, index=True, comment="日志ID")
  """ 日志ID """

  liquidation_order_id = Column(String(36), nullable=False, comment="清仓订单ID")
  """ 清仓订单ID """

  action = Column(String(50), nullable=False, comment="操作动作")
  """ 操作动作 """

  status = Column(String(20), nullable=False, comment="操作状态")
  """ 操作状态 """

  stock_code = Column(String(20), nullable=True, comment="股票代码")
  """ 股票代码 """

  volume = Column(Integer, comment="操作数量")
  """ 操作数量 """

  price = Column(Numeric(10, 4), comment="价格")
  """ 价格 """

  amount = Column(Numeric(15, 2), comment="金额")
  """ 金额 """

  order_id = Column(String(50), comment="关联订单ID")
  """ 关联订单ID """

  message = Column(Text, comment="操作描述")
  """ 操作描述 """

  error_message = Column(Text, comment="错误信息")
  """ 错误信息 """

  execution_time = Column(DateTime, default=datetime.now, comment="执行时间")
  """ 执行时间 """

  def to_dict(self):
    """转换为字典格式"""
    return {
      "id": self.id,
      "liquidation_order_id": self.liquidation_order_id,
      "action": self.action,
      "status": self.status,
      "stock_code": self.stock_code,
      "volume": self.volume,
      "price": float(self.price) if self.price else None,
      "amount": float(self.amount) if self.amount else None,
      "order_id": self.order_id,
      "message": self.message,
      "error_message": self.error_message,
      "execution_time": self.execution_time.isoformat()
      if self.execution_time
      else None,
      "created_at": self.created_at.isoformat() if self.created_at else None,
    }

  def __repr__(self):
    return f"<LiquidationLog(id='{self.id}', action='{self.action}', status='{self.status}')>"


class ConditionalLiquidationOrder(Base, TimestampMixin):
  """条件清仓单，用于手动持仓的轻量止盈/退出规则。"""

  __tablename__ = "conditional_liquidation_orders"
  __allow_unmapped__ = True

  id = Column(String(36), primary_key=True, index=True, comment="条件清仓单ID")
  account_id = Column(String(50), nullable=False, index=True, comment="资金账号")
  account_type = Column(Enum(AccountType), nullable=True, comment="账户类型")
  stock_code = Column(String(20), nullable=False, index=True, comment="证券代码")
  instrument_name = Column(String(50), nullable=True, comment="证券名称")

  enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")
  status = Column(
    String(20),
    nullable=False,
    default=ConditionalLiquidationStatus.ACTIVE,
    comment="条件单状态",
  )

  target_profit_pct = Column(Numeric(10, 4), nullable=True, comment="目标收益率百分比")
  target_price = Column(Numeric(10, 4), nullable=True, comment="目标触发价")

  sell_mode = Column(
    String(30),
    nullable=False,
    default=ConditionalLiquidationSellMode.ALL_AVAILABLE,
    comment="卖出数量模式",
  )
  sell_ratio_pct = Column(Numeric(10, 4), nullable=True, comment="卖出可卖数量比例")
  sell_volume = Column(Integer, nullable=True, comment="固定卖出股数")

  triggered_at = Column(DateTime, nullable=True, comment="触发时间")
  triggered_price = Column(Numeric(10, 4), nullable=True, comment="触发价格")
  triggered_profit_pct = Column(Numeric(10, 4), nullable=True, comment="触发收益率")
  submitted_order_id = Column(String(50), nullable=True, comment="提交的委托编号")
  submitted_volume = Column(Integer, nullable=True, comment="已提交委托数量")
  last_checked_at = Column(DateTime, nullable=True, comment="最近检查时间")
  last_error = Column(Text, nullable=True, comment="最近错误或保守跳过原因")
  remark = Column(Text, nullable=True, comment="备注")

  def to_dict(self):
    return {
      "id": self.id,
      "account_id": self.account_id,
      "account_type": self.account_type.name if self.account_type else None,
      "stock_code": self.stock_code,
      "instrument_name": self.instrument_name,
      "enabled": bool(self.enabled),
      "status": self.status,
      "target_profit_pct": float(self.target_profit_pct)
      if self.target_profit_pct is not None
      else None,
      "target_price": float(self.target_price)
      if self.target_price is not None
      else None,
      "sell_mode": self.sell_mode,
      "sell_ratio_pct": float(self.sell_ratio_pct)
      if self.sell_ratio_pct is not None
      else None,
      "sell_volume": self.sell_volume,
      "triggered_at": self.triggered_at.isoformat()
      if self.triggered_at
      else None,
      "triggered_price": float(self.triggered_price)
      if self.triggered_price is not None
      else None,
      "triggered_profit_pct": float(self.triggered_profit_pct)
      if self.triggered_profit_pct is not None
      else None,
      "submitted_order_id": self.submitted_order_id,
      "submitted_volume": self.submitted_volume,
      "last_checked_at": self.last_checked_at.isoformat()
      if self.last_checked_at
      else None,
      "last_error": self.last_error,
      "remark": self.remark,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }

  def __repr__(self):
    return (
      f"<ConditionalLiquidationOrder(id='{self.id}', "
      f"stock_code='{self.stock_code}', status='{self.status}')>"
    )


class RedemptionRecord(Base, TimestampMixin):
  """资金赎回记录表"""

  __tablename__ = "redemption_records"
  __allow_unmapped__ = True

  id = Column(String(36), primary_key=True, index=True, comment="赎回记录ID")
  """ 赎回记录ID """

  account_id = Column(String(50), nullable=False, comment="资金账号")
  """ 资金账号 """

  stock_code = Column(String(20), nullable=False, comment="股票代码")
  """ 股票代码 """

  instrument_name = Column(String(50), nullable=True, comment="证券名称")
  """ 证券名称 """

  liquidation_order_id = Column(String(36), nullable=True, comment="关联清仓订单ID")
  """ 关联清仓订单ID """

  # 赎回信息
  redemption_amount = Column(Numeric(15, 2), nullable=False, comment="赎回金额")
  """ 赎回金额 """

  available_amount = Column(Numeric(15, 2), comment="可赎回金额")
  """ 可赎回金额 """

  redeemed_amount = Column(Numeric(15, 2), default=0, comment="已赎回金额")
  """ 已赎回金额 """

  status = Column(String(20), nullable=False, default="PENDING", comment="赎回状态")
  """ 赎回状态: PENDING, PROCESSING, COMPLETED, FAILED """

  # 时间信息
  redemption_date = Column(DateTime, default=datetime.now, comment="赎回日期")
  """ 赎回日期 """

  expected_arrival_date = Column(DateTime, comment="预计到账日期")
  """ 预计到账日期 """

  actual_arrival_date = Column(DateTime, comment="实际到账日期")
  """ 实际到账日期 """

  # 费用信息
  redemption_fee = Column(Numeric(10, 2), default=0, comment="赎回费用")
  """ 赎回费用 """

  # 备注
  remark = Column(Text, comment="备注信息")
  """ 备注信息 """

  error_message = Column(Text, comment="错误信息")
  """ 错误信息 """

  def to_dict(self):
    """转换为字典格式"""
    return {
      "id": self.id,
      "account_id": self.account_id,
      "stock_code": self.stock_code,
      "instrument_name": self.instrument_name,
      "liquidation_order_id": self.liquidation_order_id,
      "redemption_amount": float(self.redemption_amount)
      if self.redemption_amount
      else None,
      "available_amount": float(self.available_amount)
      if self.available_amount
      else None,
      "redeemed_amount": float(self.redeemed_amount) if self.redeemed_amount else None,
      "status": self.status,
      "redemption_date": self.redemption_date.isoformat()
      if self.redemption_date
      else None,
      "expected_arrival_date": self.expected_arrival_date.isoformat()
      if self.expected_arrival_date
      else None,
      "actual_arrival_date": self.actual_arrival_date.isoformat()
      if self.actual_arrival_date
      else None,
      "redemption_fee": float(self.redemption_fee) if self.redemption_fee else None,
      "remark": self.remark,
      "error_message": self.error_message,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }

  def __repr__(self):
    return f"<RedemptionRecord(id='{self.id}', stock_code='{self.stock_code}', amount={self.redemption_amount})>"
