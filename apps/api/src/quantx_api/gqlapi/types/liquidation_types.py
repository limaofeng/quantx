"""
卖出管理与统一退出计划 GraphQL 类型定义
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

import strawberry
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder as ConditionalOrderModel,
)
from strawberry.scalars import JSON


@strawberry.enum(description="移动端清仓范围")
class LiquidationScope(str, Enum):
  SINGLE = "SINGLE"
  SELECTED = "SELECTED"
  ALL = "ALL"


@strawberry.enum(description="清仓完成策略")
class LiquidationCompletionStrategy(str, Enum):
  AVAILABLE_NOW = "AVAILABLE_NOW"
  UNTIL_SNAPSHOT_CLEARED = "UNTIL_SNAPSHOT_CLEARED"


@strawberry.enum(description="清仓计划冲突策略")
class LiquidationConflictStrategy(str, Enum):
  UNALLOCATED_ONLY = "UNALLOCATED_ONLY"
  REPLACE_CANCELLABLE = "REPLACE_CANCELLABLE"


@strawberry.enum(description="清仓执行模式；默认 PAPER，LIVE 需要额外实盘门禁")
class LiquidationExecutionMode(str, Enum):
  PAPER = "PAPER"
  LIVE = "LIVE"


@strawberry.type(description="清仓结果")
class LiquidationResult:
  success: bool = strawberry.field(description="是否成功")
  total_positions: int = strawberry.field(description="总持仓数量")
  liquidated_positions: int = strawberry.field(description="已清仓数量")
  failed_positions: int = strawberry.field(description="失败数量")
  message: str = strawberry.field(description="结果消息")

  @strawberry.field(description="订单列表")
  def orders(self) -> List[str]:
    return []

  @strawberry.field(description="错误列表")
  def errors(self) -> List["LiquidationError"]:
    return []


@strawberry.type(description="清仓错误")
class LiquidationError:
  stock_code: str = strawberry.field(description="股票代码")
  error: str = strawberry.field(description="错误信息")


@strawberry.type(description="个股清仓结果")
class PositionLiquidationResult:
  success: bool = strawberry.field(description="是否成功")
  stock_code: str = strawberry.field(description="股票代码")
  volume: Optional[int] = strawberry.field(description="清仓数量")
  order_id: Optional[str] = strawberry.field(description="订单ID")
  message: str = strawberry.field(description="结果消息")
  error: Optional[str] = strawberry.field(description="错误信息")


@strawberry.type(description="资金赎回结果")
class RedemptionResult:
  success: bool = strawberry.field(description="是否成功")
  stock_code: str = strawberry.field(description="股票代码")
  redeemed_amount: Optional[float] = strawberry.field(description="赎回金额")
  remaining_amount: Optional[float] = strawberry.field(description="剩余金额")
  message: str = strawberry.field(description="结果消息")
  error: Optional[str] = strawberry.field(description="错误信息")


@strawberry.type(description="清仓概况")
class LiquidationSummary:
  total_positions: int = strawberry.field(description="总持仓数量")
  liquidatable_positions: int = strawberry.field(description="可清仓持仓数量")
  total_market_value: float = strawberry.field(description="总市值")

  @strawberry.field(description="持仓列表")
  def positions(self) -> List["LiquidatablePosition"]:
    return []


@strawberry.type(description="可清仓持仓")
class LiquidatablePosition:
  stock_code: str = strawberry.field(description="股票代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  volume: int = strawberry.field(description="持仓数量")
  can_use_volume: int = strawberry.field(description="可用数量")
  market_value: float = strawberry.field(description="市值")
  avg_price: Optional[float] = strawberry.field(description="平均成本价")


@strawberry.type(description="清仓订单")
class LiquidationOrder:
  id: str = strawberry.field(description="清仓订单ID")
  account_id: str = strawberry.field(description="资金账号")
  liquidation_type: str = strawberry.field(description="清仓类型")
  status: str = strawberry.field(description="清仓状态")
  stock_code: Optional[str] = strawberry.field(description="股票代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  target_volume: Optional[int] = strawberry.field(description="目标清仓数量")
  completed_volume: int = strawberry.field(description="已完成数量")
  target_amount: Optional[float] = strawberry.field(description="目标清仓金额")
  completed_amount: float = strawberry.field(description="已完成金额")
  start_time: Optional[str] = strawberry.field(description="开始执行时间")
  end_time: Optional[str] = strawberry.field(description="结束时间")
  retry_count: int = strawberry.field(description="重试次数")
  remark: Optional[str] = strawberry.field(description="备注信息")
  error_message: Optional[str] = strawberry.field(description="错误信息")
  created_at: Optional[str] = strawberry.field(description="创建时间")


@strawberry.type(description="赎回记录")
class RedemptionRecord:
  id: str = strawberry.field(description="赎回记录ID")
  account_id: str = strawberry.field(description="资金账号")
  stock_code: str = strawberry.field(description="股票代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  redemption_amount: float = strawberry.field(description="赎回金额")
  available_amount: Optional[float] = strawberry.field(description="可赎回金额")
  redeemed_amount: float = strawberry.field(description="已赎回金额")
  status: str = strawberry.field(description="赎回状态")
  redemption_date: Optional[str] = strawberry.field(description="赎回日期")
  expected_arrival_date: Optional[str] = strawberry.field(description="预计到账日期")
  actual_arrival_date: Optional[str] = strawberry.field(description="实际到账日期")
  redemption_fee: float = strawberry.field(description="赎回费用")
  remark: Optional[str] = strawberry.field(description="备注信息")
  created_at: Optional[str] = strawberry.field(description="创建时间")


@strawberry.type(description="条件清仓单")
class ConditionalLiquidationOrder:
  id: str = strawberry.field(description="条件清仓单ID")
  account_id: str = strawberry.field(description="资金账号")
  stock_code: str = strawberry.field(description="证券代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  enabled: bool = strawberry.field(description="是否启用")
  status: str = strawberry.field(description="状态")
  target_profit_pct: Optional[float] = strawberry.field(description="目标收益率百分比")
  target_price: Optional[float] = strawberry.field(description="目标触发价")
  strategy: str = strawberry.field(description="触发后的退出策略")
  dynamic_policy: JSON = strawberry.field(description="动态止盈参数")
  exit_plan_id: Optional[str] = strawberry.field(description="统一退出计划ID")
  execution_mode: str = strawberry.field(description="执行模式")
  auto_exit_authorized: bool = strawberry.field(description="是否授权自动卖出")
  sell_mode: str = strawberry.field(description="卖出数量模式")
  sell_ratio_pct: Optional[float] = strawberry.field(description="卖出可卖数量比例")
  sell_volume: Optional[int] = strawberry.field(description="固定卖出股数")
  triggered_at: Optional[datetime] = strawberry.field(description="触发时间")
  triggered_price: Optional[float] = strawberry.field(description="触发价格")
  triggered_profit_pct: Optional[float] = strawberry.field(description="触发收益率")
  submitted_order_id: Optional[str] = strawberry.field(description="提交委托编号")
  submitted_volume: Optional[int] = strawberry.field(description="提交委托数量")
  last_checked_at: Optional[datetime] = strawberry.field(description="最近检查时间")
  last_error: Optional[str] = strawberry.field(description="最近错误")
  remark: Optional[str] = strawberry.field(description="备注")
  created_at: Optional[datetime] = strawberry.field(description="创建时间")
  updated_at: Optional[datetime] = strawberry.field(description="更新时间")
  phase: Optional[str] = strawberry.field(description="动态止盈阶段")
  data_quality: Optional[str] = strawberry.field(description="实时数据质量")
  last_decision: Optional[str] = strawberry.field(description="最近动态决策")
  protected_volume: Optional[int] = strawberry.field(description="固定保护数量")
  exited_volume: Optional[int] = strawberry.field(description="已成交数量")
  remaining_volume: Optional[int] = strawberry.field(description="剩余保护数量")
  peak_price: Optional[float] = strawberry.field(description="激活后峰值价")
  peak_drawdown_pct: Optional[float] = strawberry.field(description="峰值回撤百分比")
  volume_velocity: Optional[float] = strawberry.field(description="实时量速比")
  weak_score: Optional[int] = strawberry.field(description="量价转弱评分")
  trailing_floor_pct: Optional[float] = strawberry.field(description="动态保盈线")
  pending_client_order_id: Optional[str] = strawberry.field(description="待成交委托ID")

  @staticmethod
  def from_model(
    model: ConditionalOrderModel,
    exit_plan: Optional[AutoExitPlanRecord] = None,
  ) -> "ConditionalLiquidationOrder":
    return ConditionalLiquidationOrder(
      id=model.id,
      account_id=model.account_id,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      enabled=bool(model.enabled),
      status=model.status,
      target_profit_pct=float(model.target_profit_pct)
      if model.target_profit_pct is not None
      else None,
      target_price=float(model.target_price)
      if model.target_price is not None
      else None,
      strategy=str(getattr(model, "strategy", None) or "IMMEDIATE"),
      dynamic_policy=dict(getattr(model, "dynamic_policy", None) or {}),
      exit_plan_id=getattr(model, "exit_plan_id", None),
      execution_mode=str(getattr(model, "execution_mode", None) or "paper"),
      auto_exit_authorized=bool(
        getattr(model, "auto_exit_authorized", False)
      ),
      sell_mode=model.sell_mode,
      sell_ratio_pct=float(model.sell_ratio_pct)
      if model.sell_ratio_pct is not None
      else None,
      sell_volume=model.sell_volume,
      triggered_at=model.triggered_at,
      triggered_price=float(model.triggered_price)
      if model.triggered_price is not None
      else None,
      triggered_profit_pct=float(model.triggered_profit_pct)
      if model.triggered_profit_pct is not None
      else None,
      submitted_order_id=model.submitted_order_id,
      submitted_volume=model.submitted_volume,
      last_checked_at=model.last_checked_at,
      last_error=model.last_error,
      remark=model.remark,
      created_at=model.created_at,
      updated_at=model.updated_at,
      phase=getattr(exit_plan, "phase", None),
      data_quality=getattr(exit_plan, "data_quality", None),
      last_decision=getattr(exit_plan, "last_decision", None),
      protected_volume=getattr(exit_plan, "protected_volume", None),
      exited_volume=getattr(exit_plan, "exited_volume", None),
      remaining_volume=getattr(exit_plan, "remaining_volume", None),
      peak_price=getattr(exit_plan, "peak_price", None),
      peak_drawdown_pct=getattr(exit_plan, "peak_drawdown_pct", None),
      volume_velocity=getattr(exit_plan, "volume_velocity", None),
      weak_score=getattr(exit_plan, "weak_score", None),
      trailing_floor_pct=getattr(exit_plan, "trailing_floor_pct", None),
      pending_client_order_id=getattr(
        exit_plan, "pending_client_order_id", None
      ),
    )


@strawberry.type(description="条件清仓单评估结果")
class ConditionalLiquidationEvaluationResult:
  order: ConditionalLiquidationOrder = strawberry.field(description="条件清仓单")
  triggered: bool = strawberry.field(description="是否触发")
  submitted: bool = strawberry.field(description="是否提交委托")
  message: str = strawberry.field(description="评估说明")
  sell_volume: int = strawberry.field(description="计划卖出数量")
  order_id: Optional[str] = strawberry.field(description="提交委托编号")
  latest_price: Optional[float] = strawberry.field(description="评估价格")
  profit_pct: Optional[float] = strawberry.field(description="评估收益率")
  error: Optional[str] = strawberry.field(description="错误信息")


@strawberry.type(description="统一退出计划")
class ExitPlanView:
  plan_id: str
  group_id: Optional[str]
  account_id: str
  instrument_code: str
  bucket: str
  source_type: str
  source_id: str
  strategy_run_id: Optional[str]
  enabled: bool
  status: str
  execution_mode: str
  auto_exit_authorized: bool
  auto_exit_authorization_config_version: Optional[int]
  auto_exit_authorization_expires_at: Optional[datetime]
  config_version: int
  completion_strategy: Optional[str]
  completion_note: Optional[str]
  protected_volume: int
  exited_volume: int
  remaining_volume: int
  entry_avg_price: float
  rules: JSON
  metadata: JSON
  can_edit_rules: bool
  edit_route: Optional[str]
  phase: str
  data_quality: str
  last_decision: Optional[str]
  peak_price: float
  peak_drawdown_pct: float
  trailing_floor_pct: Optional[float]
  pending_client_order_id: Optional[str]
  pending_intent_id: Optional[str]
  last_evaluated_at: Optional[datetime]
  last_error: Optional[str]
  created_at: Optional[datetime]
  updated_at: Optional[datetime]

  @staticmethod
  def from_model(model: AutoExitPlanRecord) -> "ExitPlanView":
    state = dict(model.plan_state or {})
    template = dict(state.get("template") or {})
    source_routes = {
      "LIMIT_UP_BOARD": "/strategies/limit-up-board",
      "T_TRADE_BATCH": "/t-trade",
      "TAKE_PROFIT": f"/stocks/{model.instrument_code}",
    }
    return ExitPlanView(
      plan_id=model.plan_id,
      group_id=getattr(model, "group_id", None),
      account_id=model.account_id,
      instrument_code=model.instrument_code,
      bucket=model.bucket,
      source_type=model.source_type,
      source_id=model.source_id,
      strategy_run_id=model.strategy_run_id,
      enabled=bool(model.enabled),
      status=model.status,
      execution_mode=model.execution_mode,
      auto_exit_authorized=bool(model.auto_exit_authorized),
      auto_exit_authorization_config_version=getattr(
        model, "auto_exit_authorization_config_version", None
      ),
      auto_exit_authorization_expires_at=getattr(
        model, "auto_exit_authorization_expires_at", None
      ),
      config_version=int(model.config_version or 0),
      completion_strategy=getattr(model, "completion_strategy", None),
      completion_note=(
        "本次持仓快照已处理完成；后续新增持仓未纳入本次清仓"
        if model.status == "COMPLETED"
        and model.source_type == "MANUAL_LIQUIDATION"
        else None
      ),
      protected_volume=int(model.protected_volume or 0),
      exited_volume=int(model.exited_volume or 0),
      remaining_volume=int(model.remaining_volume or 0),
      entry_avg_price=float(model.entry_avg_price or 0.0),
      rules=list(template.get("rules") or []),
      metadata=dict(template.get("metadata") or {}),
      can_edit_rules=model.source_type == "MANUAL_POSITION",
      edit_route=source_routes.get(model.source_type),
      phase=str(model.phase or "WAITING_ARM"),
      data_quality=str(model.data_quality or "PRICE_UNAVAILABLE"),
      last_decision=model.last_decision,
      peak_price=float(model.peak_price or 0.0),
      peak_drawdown_pct=float(model.peak_drawdown_pct or 0.0),
      trailing_floor_pct=(
        float(model.trailing_floor_pct)
        if model.trailing_floor_pct is not None
        else None
      ),
      pending_client_order_id=model.pending_client_order_id,
      pending_intent_id=str(state.get("pending_intent_id") or "") or None,
      last_evaluated_at=model.last_evaluated_at,
      last_error=model.last_error,
      created_at=model.created_at,
      updated_at=model.updated_at,
    )


@strawberry.type(description="退出计划审计事件")
class ExitPlanEventView:
  event_id: str
  plan_id: str
  event_type: str
  payload: JSON
  created_at: datetime


@strawberry.type(description="退出规则能力")
class ExitPlanRuleCapability:
  rule_type: str
  label: str
  category: str
  parameters: JSON


@strawberry.type(description="退出计划能力")
class ExitPlanCapabilities:
  rule_types: List[ExitPlanRuleCapability]
  completion_strategies: List[str]
  conflict_strategies: List[str]
  execution_modes: List[str]
  rule_semantics: str


@strawberry.type(description="占用持仓数量的冲突计划")
class ExitPlanCapacityConflict:
  plan_id: str
  source_type: str
  status: str
  remaining_volume: int
  pending: bool


@strawberry.type(description="股票退出计划持仓容量")
class ExitPlanHoldingCapacity:
  account_id: str
  instrument_code: str
  total_volume: int
  available_volume: int
  frozen_volume: int
  protected_volume: int
  pending_volume: int
  unallocated_volume: int
  conflicts: List[ExitPlanCapacityConflict]


@strawberry.type(description="单只股票清仓计划创建结果")
class LiquidationPlanResult:
  instrument_code: str
  success: bool
  plan_id: Optional[str]
  protected_volume: Optional[int]
  conflict_plan_ids: List[str]
  error: Optional[str]


@strawberry.type(description="一组持仓清仓计划")
class LiquidationGroupResult:
  group_id: str
  success: bool
  message: str
  plans: List[LiquidationPlanResult]


@strawberry.type(description="清仓预览中的冲突退出计划")
class LiquidationConflictPreview:
  plan_id: str
  source_type: str
  status: str
  remaining_volume: int
  config_version: int
  pending: bool


@strawberry.type(description="清仓预览中的单只证券固定快照")
class LiquidationItemPreview:
  instrument_code: str
  instrument_name: Optional[str]
  total_volume: int
  available_volume: int
  frozen_volume: int
  t1_unavailable_volume: int
  protected_volume: int
  pending_sell_volume: int
  max_protected_volume: int
  included: bool
  reason_code: str
  reason_detail: str
  position_updated_at: Optional[datetime]
  conflicts: List[LiquidationConflictPreview]


@strawberry.type(description="移动端组级清仓服务器预览")
class LiquidationPreview:
  challenge_id: str
  confirmation_token: str
  group_id: str
  account_id: str
  scope: LiquidationScope
  instrument_codes: List[str]
  completion_strategy: LiquidationCompletionStrategy
  conflict_strategy: LiquidationConflictStrategy
  execution_mode: LiquidationExecutionMode
  idempotency_key: str
  snapshot_version: str
  account_updated_at: datetime
  rollout_snapshot_id: Optional[str]
  rollout_snapshot_hash: Optional[str]
  challenge_expires_at: datetime
  included_count: int
  skipped_count: int
  items: List[LiquidationItemPreview]
  warnings: List[str]


@strawberry.type(description="移动端清仓预览结果")
class LiquidationPreviewResult:
  success: bool
  code: str
  message: str
  preview: Optional[LiquidationPreview] = None


@strawberry.type(description="移动端清仓确认结果；PENDING 仅表示 Engine 命令已排队")
class LiquidationConfirmationResult:
  success: bool
  code: str
  message: str
  challenge_id: Optional[str] = None
  group_id: Optional[str] = None
  command_id: Optional[str] = None
  status: Optional[str] = None
  created_count: int = 0
  failed_count: int = 0
  plans: List[LiquidationPlanResult] = strawberry.field(default_factory=list)


@strawberry.type(description="自动实盘退出授权绑定的持仓与 T+1 快照")
class ExitPlanAuthorizationPositionSnapshot:
  total_volume: int
  available_volume: int
  frozen_volume: int
  yesterday_volume: int
  t1_unavailable_volume: int
  position_updated_at: Optional[datetime]


@strawberry.type(description="既有 LIVE 退出计划的精确自动实盘授权预览")
class ExitPlanAuthorizationPreview:
  challenge_id: str
  confirmation_token: str
  account_id: str
  plan_id: str
  instrument_code: str
  bucket: str
  source_type: str
  execution_mode: str
  config_version: int
  protected_volume: int
  exited_volume: int
  remaining_volume: int
  rules: JSON
  t1_policy: str
  execution_policy: JSON
  position: ExitPlanAuthorizationPositionSnapshot
  other_protections: List[LiquidationConflictPreview]
  readiness: JSON
  authorization_fingerprint: str
  authorization_expires_at: datetime
  challenge_expires_at: datetime
  warnings: List[str]


@strawberry.type(description="退出计划精确自动实盘授权预览结果")
class ExitPlanAuthorizationPreviewResult:
  success: bool
  code: str
  message: str
  preview: Optional[ExitPlanAuthorizationPreview] = None


@strawberry.type(description="退出计划精确自动实盘授权确认结果")
class ExitPlanAuthorizationConfirmationResult:
  success: bool
  code: str
  message: str
  challenge_id: Optional[str] = None
  plan_id: Optional[str] = None
  config_version: Optional[int] = None
  authorized: bool = False
  authorization_expires_at: Optional[datetime] = None
  audit_event_id: Optional[str] = None


@strawberry.input(description="移动端组级清仓预览输入")
class LiquidationPreviewInput:
  account_id: str = strawberry.field(description="必填资金账号")
  scope: LiquidationScope = strawberry.field(description="单只、选中或全部")
  completion_strategy: LiquidationCompletionStrategy = strawberry.field(
    description="处理当前可卖量或持续处理预览持仓快照"
  )
  conflict_strategy: LiquidationConflictStrategy = strawberry.field(
    description="只使用未分配数量或替换可取消计划"
  )
  idempotency_key: str = strawberry.field(description="调用方生成的业务幂等键")
  instrument_codes: Optional[List[str]] = strawberry.field(
    description="SINGLE/SELECTED 必填；ALL 必须为空",
    default=None,
  )
  execution_mode: LiquidationExecutionMode = strawberry.field(
    description="默认 PAPER；LIVE 需要实盘门禁和最新完整对账",
    default=LiquidationExecutionMode.PAPER,
  )


@strawberry.input(description="移动端组级清仓确认输入")
class LiquidationConfirmationInput:
  challenge_id: str = strawberry.field(description="预览返回的确认挑战 ID")
  confirmation_token: str = strawberry.field(description="预览返回的一次性确认凭据")


@strawberry.input(description="预览既有 LIVE 退出计划的精确自动实盘授权")
class ExitPlanAuthorizationPreviewInput:
  account_id: str = strawberry.field(description="当前原生设备会话的唯一主账户")
  plan_id: str = strawberry.field(description="既有退出计划 ID")
  expected_config_version: int = strawberry.field(description="预期配置版本")
  idempotency_key: str = strawberry.field(description="调用方生成的业务幂等键")


@strawberry.input(description="确认既有 LIVE 退出计划的精确自动实盘授权")
class ExitPlanAuthorizationConfirmationInput:
  account_id: str = strawberry.field(description="预览时的主账户")
  plan_id: str = strawberry.field(description="预览时的退出计划 ID")
  expected_config_version: int = strawberry.field(description="预览时的配置版本")
  idempotency_key: str = strawberry.field(description="预览时的业务幂等键")
  challenge_id: str = strawberry.field(description="预览返回的确认挑战 ID")
  confirmation_token: str = strawberry.field(description="预览返回的一次性确认凭据")


# 输入类型
@strawberry.input(description="创建人工计划")
class CreateManualExitPlanInput:
  instrument_code: str
  protected_volume: int
  rules: JSON
  idempotency_key: str = strawberry.field(description="调用方生成的创建请求幂等键")
  account_id: Optional[str] = None
  bucket: str = "manual"
  enabled: bool = True
  execution_mode: str = "paper"
  auto_exit_authorized: bool = False
  remark: Optional[str] = None


@strawberry.input(description="更新人工计划")
class UpdateManualExitPlanInput:
  plan_id: str
  config_version: int
  rules: JSON
  account_id: Optional[str] = None
  protected_volume: Optional[int] = None
  execution_mode: Optional[str] = None
  auto_exit_authorized: Optional[bool] = None
  remark: Optional[str] = None


@strawberry.input(description="批量或一键清仓")
class LiquidatePositionsInput:
  completion_strategy: str
  conflict_strategy: str
  confirm: bool
  account_id: Optional[str] = None
  scope: str = "SELECTED"
  instrument_codes: Optional[List[str]] = None
  execution_mode: str = "paper"
  auto_exit_authorized: bool = False


@strawberry.input(description="一键清仓输入")
class LiquidateAllPositionsInput:
  confirm: bool = strawberry.field(description="风险确认")
  max_retry: int = strawberry.field(default=3, description="最大重试次数")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")


@strawberry.input(description="个股清仓输入")
class LiquidatePositionInput:
  stock_code: str = strawberry.field(description="股票代码")
  confirm: bool = strawberry.field(description="风险确认")
  max_retry: int = strawberry.field(default=3, description="最大重试次数")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")


@strawberry.input(description="条件清仓单输入")
class ConditionalLiquidationOrderInput:
  id: Optional[str] = strawberry.field(default=None, description="条件清仓单ID")
  stock_code: str = strawberry.field(description="证券代码")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")
  instrument_name: Optional[str] = strawberry.field(default=None, description="证券名称")
  enabled: bool = strawberry.field(default=True, description="是否启用")
  target_profit_pct: Optional[float] = strawberry.field(
    default=None, description="目标收益率百分比"
  )
  target_price: Optional[float] = strawberry.field(
    default=None, description="目标触发价"
  )
  strategy: str = strawberry.field(
    default="IMMEDIATE", description="IMMEDIATE 或 ADAPTIVE_VOLUME_PRICE_TRAILING"
  )
  dynamic_policy: Optional[JSON] = strawberry.field(
    default=None, description="动态止盈参数；为空使用平衡型默认值"
  )
  execution_mode: str = strawberry.field(
    default="paper", description="paper 或 live"
  )
  auto_exit_authorized: bool = strawberry.field(
    default=False, description="明确授权自动卖出"
  )
  sell_mode: str = strawberry.field(
    default="ALL_AVAILABLE", description="卖出数量模式"
  )
  sell_ratio_pct: Optional[float] = strawberry.field(
    default=None, description="卖出可卖数量比例"
  )
  sell_volume: Optional[int] = strawberry.field(
    default=None, description="固定卖出股数"
  )
  remark: Optional[str] = strawberry.field(default=None, description="备注")


@strawberry.input(description="资金赎回输入")
class RedeemPositionInput:
  stock_code: str = strawberry.field(description="股票代码")
  amount: Optional[float] = strawberry.field(
    default=None, description="赎回金额，为空则赎回全部"
  )
