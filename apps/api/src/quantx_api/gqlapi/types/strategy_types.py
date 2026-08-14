from datetime import datetime, timedelta
from enum import Enum
from typing import Any, List, Optional

import strawberry
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models import StrategyStatus
from quantx_infrastructure.models.enums import (
  RiskLevel,
  StrategyCategory,
  StrategyInstrumentScope,
  StrategyInstrumentUniverseMode,
  StrategyRunMode,
  StrategyRunStatus,
)
from quantx_infrastructure.models.strategy import Strategy as StrategyRunModel
from quantx_infrastructure.models.strategy_decision_trace_record import (
  StrategyDecisionTraceRecord,
)
from quantx_infrastructure.models.strategy_run import StrategyRun as StrategyRunDbModel
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from strawberry.scalars import JSON

from quantx_api.gqlapi.types.parameter_schema_types import ParameterSchema


def _optional_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  if isinstance(value, str) and value.strip():
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
      return None
  return None


def _optional_float(value: Any) -> Optional[float]:
  try:
    return float(value) if value is not None else None
  except (TypeError, ValueError):
    return None


@strawberry.type(description="策略模板信息")
class Strategy:
  id: int = strawberry.field(description="策略ID")
  name: str = strawberry.field(description="策略名称")
  description: str = strawberry.field(description="策略描述")
  file_path: str = strawberry.field(description="文件路径")
  class_name: str = strawberry.field(description="类名")
  category: Optional[StrategyCategory] = strawberry.field(
    default=None, description="策略分类"
  )
  risk_level: Optional[RiskLevel] = strawberry.field(
    default=None, description="风险等级"
  )
  _instrument_scope: strawberry.Private[Optional[object]] = None
  _instrument_universe_mode: strawberry.Private[Optional[object]] = None
  tags: List[str] = strawberry.field(default_factory=list, description="策略标签列表")
  parameter_schema: Optional[ParameterSchema] = strawberry.field(
    default=None, description="参数 Schema 定义（结构化类型，支持前端表单生成）"
  )
  version: Optional[str] = strawberry.field(default=None, description="策略版本号")
  code_hash: Optional[str] = strawberry.field(
    default=None, description="代码哈希值（SHA256）"
  )
  status: StrategyStatus = strawberry.field(description="策略状态")
  is_active: bool = strawberry.field(description="是否激活（仅 ACTIVE 状态为激活）")
  default_parameters: JSON = strawberry.field(
    description="默认参数配置（从 parameter_schema 提取）"
  )
  created_at: datetime = strawberry.field(description="创建时间")
  updated_at: datetime = strawberry.field(description="更新时间")

  @staticmethod
  def from_model(model: StrategyRunModel) -> "Strategy":
    """从数据库 Model 转换为 GraphQL 类型"""
    return Strategy(
      id=model.id,
      name=model.name,
      description=model.description or "",
      file_path=model.file_path,
      class_name=model.class_name,
      category=model.category,
      risk_level=model.risk_level,
      _instrument_scope=model.instrument_scope,
      _instrument_universe_mode=model.instrument_universe_mode,
      tags=model.tags or [],
      parameter_schema=ParameterSchema.from_pydantic(model.parameter_schema)
      if model.parameter_schema
      else None,
      version=model.version,
      code_hash=model.code_hash,
      status=model.status,
      is_active=model.is_active,
      default_parameters=model.default_parameters,
      created_at=model.created_at,
      updated_at=model.updated_at,
    )

  @strawberry.field(description="标的范围（单标的/多标的）")
  def instrument_scope(self) -> Optional[StrategyInstrumentScope]:
    value = self._instrument_scope
    if value is None:
      return None
    if isinstance(value, StrategyInstrumentScope):
      return value
    if isinstance(value, str):
      try:
        return StrategyInstrumentScope(value)
      except ValueError:
        try:
          return StrategyInstrumentScope[value.upper()]
        except KeyError:
          return None
    try:
      return StrategyInstrumentScope(str(value))
    except ValueError:
      return None

  @strawberry.field(description="标的池来源（固定配置/账户持仓）")
  def instrument_universe_mode(self) -> StrategyInstrumentUniverseMode:
    value = self._instrument_universe_mode
    if isinstance(value, StrategyInstrumentUniverseMode):
      return value
    try:
      return StrategyInstrumentUniverseMode(str(value or "STATIC"))
    except ValueError:
      return StrategyInstrumentUniverseMode.STATIC


@strawberry.type(description="策略运行实例信息")
class StrategyRun:
  id: str = strawberry.field(description="实例ID")
  name: str = strawberry.field(description="运行实例名称")
  strategy: "Strategy" = strawberry.field(description="关联的策略模板")
  mode: StrategyRunMode = strawberry.field(description="运行模式")
  instruments: List[str] = strawberry.field(description="交易标的列表")
  parameters: JSON = strawberry.field(description="策略参数")
  status: StrategyRunStatus = strawberry.field(description="运行状态")
  start_time: Optional[datetime] = strawberry.field(description="启动时间")
  stop_time: Optional[datetime] = strawberry.field(description="停止时间")
  metrics: Optional[JSON] = strawberry.field(description="策略指标", default=None)
  error_message: Optional[str] = strawberry.field(description="错误信息")
  create_time: datetime = strawberry.field(description="创建时间")

  # 兼容旧字段
  @strawberry.field(description="股票代码（已废弃，请使用instruments）")
  def stock_code(self) -> str:
    return self.instruments[0] if self.instruments else ""

  @strawberry.field(description="股票名称（已废弃）")
  def stock_name(self) -> str:
    return ""

  @strawberry.field(description="累计盈亏")
  def profit_loss(self) -> float:
    if self.metrics and isinstance(self.metrics, dict):
      return self.metrics.get("total_pnl", 0.0)
    return 0.0

  @strawberry.field(description="总交易次数")
  def total_trades(self) -> int:
    if self.metrics and isinstance(self.metrics, dict):
      return self.metrics.get("trades_executed", 0)
    return 0


@strawberry.type(description="策略定义，供策略库展示和创建实例使用")
class StrategyDefinition:
  key: str = strawberry.field(description="策略稳定 key")
  strategy_id: int = strawberry.field(description="策略模板 ID")
  display_name: str = strawberry.field(description="策略展示名称")
  market: str = strawberry.field(description="适用市场")
  description: str = strawberry.field(description="策略说明")
  parameter_schema: Optional[ParameterSchema] = strawberry.field(default=None)
  supported_instruments: List[str] = strawberry.field(default_factory=list)
  risk_level: Optional[RiskLevel] = strawberry.field(default=None)
  category: Optional[StrategyCategory] = strawberry.field(default=None)
  instrument_universe_mode: StrategyInstrumentUniverseMode = strawberry.field(
    default=StrategyInstrumentUniverseMode.STATIC
  )

  @staticmethod
  def from_strategy(model: StrategyRunModel) -> "StrategyDefinition":
    scope = model.instrument_scope
    supported = ["单标的"] if scope == StrategyInstrumentScope.SINGLE else ["多标的"]
    identity_text = f"{model.name or ''} {model.class_name or ''}"
    has_ashare = "ashare" in identity_text.lower() or "a股" in identity_text
    return StrategyDefinition(
      key=model.name,
      strategy_id=model.id,
      display_name=model.name,
      market="A股" if has_ashare else "通用",
      description=model.description or "",
      parameter_schema=ParameterSchema.from_pydantic(model.parameter_schema)
      if model.parameter_schema
      else None,
      supported_instruments=supported,
      risk_level=model.risk_level,
      category=model.category,
      instrument_universe_mode=model.instrument_universe_mode
      or StrategyInstrumentUniverseMode.STATIC,
    )


@strawberry.type(description="策略实例中心视图")
class StrategyInstance:
  id: str
  strategy_key: str
  strategy_id: Optional[int] = None
  strategy_name: Optional[str] = None
  instrument_code: str = ""
  display_name: str = ""
  status: StrategyRunStatus = StrategyRunStatus.PENDING
  mode: StrategyRunMode = StrategyRunMode.BACKTEST
  parameters: JSON = strawberry.field(default_factory=dict)
  parameter_version: str = "1"
  created_at: datetime = strawberry.field(default_factory=datetime.now)
  updated_at: datetime = strawberry.field(default_factory=datetime.now)
  last_decision_at: Optional[datetime] = None
  latest_execution_status: Optional[str] = None

  @staticmethod
  def from_run(
    run: StrategyRunDbModel,
    *,
    last_decision_at: Optional[datetime] = None,
    latest_execution_status: Optional[str] = None,
  ) -> "StrategyInstance":
    parameters = _json_object(run.parameters)
    instrument_code = (
      (run.instruments or [None])[0]
      or parameters.get("instrument_code")
      or parameters.get("instrumentCode")
      or ""
    )
    strategy_name = run.strategy.name if run.strategy else None
    status = run.status
    if isinstance(status, str):
      try:
        status = StrategyRunStatus(status.lower())
      except (ValueError, KeyError):
        status = StrategyRunStatus.PENDING
    mode = run.mode
    if isinstance(mode, str):
      try:
        mode = StrategyRunMode(mode.lower())
      except (ValueError, KeyError):
        mode = StrategyRunMode.BACKTEST
    return StrategyInstance(
      id=run.id,
      strategy_key=strategy_name or str(run.strategy_id),
      strategy_id=run.strategy_id,
      strategy_name=strategy_name,
      instrument_code=str(instrument_code or ""),
      display_name=run.name or strategy_name or run.id,
      status=status or StrategyRunStatus.PENDING,
      mode=mode or StrategyRunMode.BACKTEST,
      parameters=parameters,
      parameter_version=str(
        parameters.get("_parameter_version")
        or (run.updated_at.isoformat() if run.updated_at else "1")
      ),
      created_at=time_utils.to_shanghai(run.created_at, keep_tz=True),
      updated_at=time_utils.to_shanghai(run.updated_at, keep_tz=True),
      last_decision_at=(
        time_utils.to_shanghai(last_decision_at, keep_tz=True)
        if last_decision_at
        else None
      ),
      latest_execution_status=latest_execution_status,
    )


@strawberry.type(description="策略 TradeIntent 前端视图")
class TradeIntentView:
  id: str
  side: str
  instrument_code: str
  target_bucket: Optional[str] = None
  price_intent: Optional[JSON] = None
  quantity_intent: Optional[JSON] = None
  reason: Optional[str] = None
  trace_id: Optional[str] = None
  status: Optional[str] = None
  created_at: Optional[datetime] = None
  updated_at: Optional[datetime] = None

  @staticmethod
  def from_record(record: TradeIntentRecord) -> "TradeIntentView":
    quantity = record.target_volume
    if quantity is None:
      quantity = record.target_amount
    if quantity is None:
      quantity = record.target_position_pct
    return TradeIntentView(
      id=record.id,
      side=record.direction,
      instrument_code=record.instrument_code,
      target_bucket=record.bucket,
      price_intent=record.limit_price_hint,
      quantity_intent=quantity,
      reason=record.reason,
      trace_id=record.trace_id,
      status=record.status,
      created_at=record.created_at,
      updated_at=record.updated_at,
    )

  @staticmethod
  def from_summary(summary: dict) -> "TradeIntentView":
    quantity = summary.get("target_volume")
    if quantity is None:
      quantity = summary.get("target_amount")
    if quantity is None:
      quantity = summary.get("target_position_pct")
    return TradeIntentView(
      id=str(summary.get("intent_id") or summary.get("id") or ""),
      side=str(summary.get("direction") or summary.get("side") or ""),
      instrument_code=str(summary.get("instrument_code") or ""),
      target_bucket=summary.get("bucket"),
      price_intent=summary.get("limit_price_hint") or summary.get("price"),
      quantity_intent=quantity,
      reason=summary.get("reason"),
      trace_id=summary.get("trace_id"),
      status=summary.get("status"),
      created_at=_optional_datetime(summary.get("created_at")),
      updated_at=_optional_datetime(summary.get("updated_at")),
    )

  @staticmethod
  def from_backtest_intent(data: dict) -> "TradeIntentView":
    quantity = data.get("target_volume")
    if quantity is None:
      quantity = data.get("target_amount")
    if quantity is None:
      quantity = data.get("target_position_pct")
    return TradeIntentView(
      id=str(data.get("id") or data.get("intent_id") or ""),
      side=str(data.get("direction") or data.get("side") or ""),
      instrument_code=str(data.get("instrument_code") or ""),
      target_bucket=data.get("bucket"),
      price_intent=data.get("limit_price_hint") or data.get("price"),
      quantity_intent=quantity,
      reason=data.get("reason"),
      trace_id=data.get("trace_id"),
      status=data.get("status"),
      created_at=_optional_datetime(data.get("created_at") or data.get("_timestamp")),
      updated_at=_optional_datetime(data.get("updated_at") or data.get("_timestamp")),
    )


@strawberry.type(description="等待人工确认的策略交易意图")
class StrategyApprovalIntent:
  id: str
  run_id: str
  instrument_code: str
  side: str
  bucket: str
  reason: str
  status: str
  execution_mode: str
  confidence: float
  limit_price_hint: Optional[float] = None
  target_position_pct: Optional[float] = None
  target_amount: Optional[float] = None
  target_volume: Optional[int] = None
  signal_price: Optional[float] = None
  limit_up_price: Optional[float] = None
  distance_to_limit_ticks: Optional[float] = None
  approval_expires_at: Optional[datetime] = None
  created_at: Optional[datetime] = None
  metadata: JSON = strawberry.field(default_factory=dict)

  @staticmethod
  def from_record(record: TradeIntentRecord) -> "StrategyApprovalIntent":
    metadata = dict(record.intent_metadata or {})
    try:
      ttl_ms = max(0, int(metadata.get("approval_ttl_ms", 0) or 0))
    except (TypeError, ValueError):
      ttl_ms = 0
    created_at = (
      _optional_datetime(metadata.get("intent_created_at"))
      or record.created_at
    )
    expires_at = (
      created_at + timedelta(milliseconds=ttl_ms)
      if created_at and ttl_ms > 0
      else None
    )
    return StrategyApprovalIntent(
      id=record.id,
      run_id=record.strategy_run_id,
      instrument_code=record.instrument_code,
      side=record.direction,
      bucket=record.bucket,
      reason=record.reason,
      status=record.status,
      execution_mode=str(metadata.get("execution_mode", "") or ""),
      confidence=float(record.confidence or 0.0),
      limit_price_hint=record.limit_price_hint,
      target_position_pct=record.target_position_pct,
      target_amount=record.target_amount,
      target_volume=record.target_volume,
      signal_price=_optional_float(metadata.get("signal_price")),
      limit_up_price=_optional_float(
        metadata.get("limit_up") or metadata.get("entry_limit_up")
      ),
      distance_to_limit_ticks=_optional_float(
        metadata.get("distance_to_limit_ticks")
      ),
      approval_expires_at=expires_at,
      created_at=created_at,
      metadata=metadata,
    )


@strawberry.type(description="Engine 统一自动退出计划投影")
class StrategyExitPlanView:
  id: str
  instrument_code: str
  source_type: str
  bucket: str
  status: str
  entry_filled_volume: int
  entry_avg_price: float
  exited_volume: int
  exit_avg_price: float
  remaining_volume: int
  peak_price: float
  last_price: float
  last_net_profit_pct: float
  peak_net_profit_pct: float
  holding_trading_days: int
  entry_trade_date: Optional[str] = None
  pending_intent_id: Optional[str] = None
  pending_order_id: Optional[str] = None
  last_exit_reason: Optional[str] = None
  t1_policy: str = ""
  execution_mode: str = ""
  auto_exit_authorized: bool = False
  rule_types: List[str] = strawberry.field(default_factory=list)
  raw: JSON = strawberry.field(default_factory=dict)

  @staticmethod
  def from_projection(value: dict) -> "StrategyExitPlanView":
    raw = dict(value or {})
    template = dict(raw.get("template") or {})
    execution = dict(template.get("execution") or {})
    rules = list(template.get("rules") or [])
    return StrategyExitPlanView(
      id=str(template.get("plan_id") or ""),
      instrument_code=str(template.get("instrument_code") or ""),
      source_type=str(template.get("source_type") or ""),
      bucket=str(template.get("bucket") or ""),
      status=str(raw.get("status") or ""),
      entry_filled_volume=int(raw.get("entry_filled_volume", 0) or 0),
      entry_avg_price=float(raw.get("entry_avg_price", 0.0) or 0.0),
      exited_volume=int(raw.get("exited_volume", 0) or 0),
      exit_avg_price=float(raw.get("exit_avg_price", 0.0) or 0.0),
      remaining_volume=int(raw.get("remaining_volume", 0) or 0),
      peak_price=float(raw.get("peak_price", 0.0) or 0.0),
      last_price=float(raw.get("last_price", 0.0) or 0.0),
      last_net_profit_pct=float(raw.get("last_net_profit_pct", 0.0) or 0.0),
      peak_net_profit_pct=float(raw.get("peak_net_profit_pct", 0.0) or 0.0),
      holding_trading_days=int(raw.get("holding_trading_days", 0) or 0),
      entry_trade_date=str(raw.get("entry_trade_date") or "") or None,
      pending_intent_id=str(raw.get("pending_intent_id") or "") or None,
      pending_order_id=str(raw.get("pending_order_id") or "") or None,
      last_exit_reason=str(raw.get("last_exit_reason") or "") or None,
      t1_policy=str(template.get("t1_policy") or ""),
      execution_mode=str(execution.get("execution_mode") or ""),
      auto_exit_authorized=bool(template.get("auto_exit_authorized", False)),
      rule_types=[
        str(dict(rule or {}).get("strategy") or "")
        for rule in rules
        if dict(rule or {}).get("strategy")
      ],
      raw=raw,
    )


@strawberry.type(description="策略决策审计记录")
class StrategyDecision:
  id: str
  instance_id: str
  trace_id: str
  decided_at: datetime
  input_summary: JSON
  output_summary: JSON
  trade_intents: List[TradeIntentView]
  state_patch: JSON
  decision_trace: JSON
  reason: Optional[str] = None
  tags: List[str] = strawberry.field(default_factory=list)

  @staticmethod
  def from_record(record: StrategyDecisionTraceRecord) -> "StrategyDecision":
    trace = dict(record.decision_trace or {})
    return StrategyDecision(
      id=record.id,
      instance_id=record.strategy_run_id,
      trace_id=record.trace_id,
      decided_at=record.decided_at,
      input_summary=record.input_summary or {},
      output_summary=record.output_summary or trace.get("output_summary") or {},
      trade_intents=[
        TradeIntentView.from_summary(item)
        for item in list(record.trade_intents or trace.get("trade_intents") or [])
      ],
      state_patch=record.state_patch or trace.get("state_patch") or {},
      decision_trace=trace,
      reason=trace.get("reason") or "",
      tags=list(trace.get("tags") or []),
    )

  @staticmethod
  def from_backtest_record(record: dict) -> "StrategyDecision":
    trace = dict(record or {})
    input_summary = dict(trace.get("input_summary") or {})
    output_summary = dict(trace.get("output_summary") or {})
    state_patch = dict(trace.get("state_patch") or {})
    decided_at = (
      _optional_datetime(input_summary.get("timestamp"))
      or _optional_datetime((trace.get("environment") or {}).get("timestamp"))
      or _optional_datetime(trace.get("timestamp"))
      or time_utils.now()
    )
    return StrategyDecision(
      id=str(trace.get("id") or trace.get("trace_id") or trace.get("_timestamp") or ""),
      instance_id=str(trace.get("run_id") or trace.get("strategy_run_id") or ""),
      trace_id=str(trace.get("trace_id") or ""),
      decided_at=decided_at,
      input_summary=input_summary,
      output_summary=output_summary,
      trade_intents=[
        TradeIntentView.from_summary(item)
        for item in list(trace.get("trade_intents") or [])
      ],
      state_patch=state_patch,
      decision_trace=trace,
      reason=trace.get("reason") or "",
      tags=list(trace.get("tags") or []),
    )


@strawberry.type(description="策略意图到成交的执行跟踪视图")
class ExecutionTraceView:
  id: str
  intent_id: str
  instrument_code: str
  side: str
  order_id: Optional[str] = None
  risk_decision: Optional[str] = None
  sizing_result: Optional[str] = None
  order_status: Optional[str] = None
  fill_status: Optional[str] = None
  executed_price: Optional[float] = None
  executed_volume: Optional[int] = None
  executed_time: Optional[datetime] = None
  reason: Optional[str] = None
  trace_id: Optional[str] = None
  created_at: Optional[datetime] = None
  updated_at: Optional[datetime] = None

  @staticmethod
  def from_intent(record: TradeIntentRecord) -> "ExecutionTraceView":
    metadata = dict(record.intent_metadata or {})
    risk = (
      metadata.get("risk_reason_code")
      or metadata.get("risk_action")
      or record.risk_decision_id
    )
    sizing = metadata.get("sized_volume")
    size_reasons = metadata.get("order_draft_size_reasons")
    if isinstance(size_reasons, list) and size_reasons:
      sizing = f"{sizing or ''} {'/'.join(str(item) for item in size_reasons)}".strip()
    filled = None
    if record.executed_volume:
      filled = f"FILLED {record.executed_volume}"
      if record.executed_price:
        filled = f"{filled} @ {record.executed_price}"
    return ExecutionTraceView(
      id=f"execution-{record.id}",
      intent_id=record.id,
      instrument_code=record.instrument_code,
      side=record.direction,
      order_id=record.order_id,
      risk_decision=str(risk) if risk else None,
      sizing_result=str(sizing) if sizing is not None else None,
      order_status=record.status,
      fill_status=filled,
      executed_price=record.executed_price,
      executed_volume=record.executed_volume,
      executed_time=record.executed_time,
      reason=record.notes or record.reason,
      trace_id=record.trace_id,
      created_at=record.created_at,
      updated_at=record.updated_at,
    )

  @staticmethod
  def from_backtest_intent(data: dict) -> "ExecutionTraceView":
    metadata = dict(data.get("metadata") or {})
    risk = (
      metadata.get("risk_reason_code")
      or metadata.get("risk_action")
      or data.get("risk_decision_id")
    )
    sizing = metadata.get("sized_volume")
    size_reasons = metadata.get("order_draft_size_reasons")
    if isinstance(size_reasons, list) and size_reasons:
      sizing = f"{sizing or ''} {'/'.join(str(item) for item in size_reasons)}".strip()

    executed_volume = data.get("executed_volume")
    executed_price = data.get("executed_price")
    filled = None
    if executed_volume:
      filled = f"FILLED {executed_volume}"
      if executed_price:
        filled = f"{filled} @ {executed_price}"

    timestamp = data.get("_timestamp")
    return ExecutionTraceView(
      id=f"execution-{data.get('id') or data.get('intent_id') or timestamp or ''}",
      intent_id=str(data.get("id") or data.get("intent_id") or ""),
      instrument_code=str(data.get("instrument_code") or ""),
      side=str(data.get("direction") or data.get("side") or ""),
      order_id=data.get("order_id"),
      risk_decision=str(risk) if risk else None,
      sizing_result=str(sizing) if sizing is not None else None,
      order_status=data.get("status"),
      fill_status=filled,
      executed_price=float(executed_price) if executed_price is not None else None,
      executed_volume=int(executed_volume) if executed_volume is not None else None,
      executed_time=_optional_datetime(data.get("executed_time")),
      reason=data.get("notes") or data.get("reason"),
      trace_id=data.get("trace_id"),
      created_at=_optional_datetime(data.get("created_at") or timestamp),
      updated_at=_optional_datetime(data.get("updated_at") or timestamp),
    )


@strawberry.type(description="策略三仓归因视图")
class BucketLedgerView:
  locked_core: float = 0.0
  core: float = 0.0
  swing: float = 0.0
  updated_at: Optional[datetime] = None
  raw: JSON = strawberry.field(default_factory=dict)


@strawberry.type(description="Pullback Grid 网格簿摘要")
class StrategyGridBookSummary:
  total_levels: int = 0
  enabled_levels: int = 0
  pending_levels: int = 0
  filled_levels: int = 0
  disabled_levels: int = 0
  planned_amount: float = 0.0
  buy_slot_count: int = 0
  sell_waterline_count: int = 0
  open_lot_shares: int = 0
  reserved_lot_shares: int = 0
  waiting_inventory_levels: int = 0
  completed_cycles: int = 0
  release_event_count: int = 0

  @staticmethod
  def from_dict(data: dict) -> "StrategyGridBookSummary":
    data = dict(data or {})
    return StrategyGridBookSummary(
      total_levels=int(data.get("total_levels", 0) or 0),
      enabled_levels=int(data.get("enabled_levels", 0) or 0),
      pending_levels=int(data.get("pending_levels", 0) or 0),
      filled_levels=int(data.get("filled_levels", 0) or 0),
      disabled_levels=int(data.get("disabled_levels", 0) or 0),
      planned_amount=float(data.get("planned_amount", 0.0) or 0.0),
      buy_slot_count=int(data.get("buy_slot_count", 0) or 0),
      sell_waterline_count=int(data.get("sell_waterline_count", 0) or 0),
      open_lot_shares=int(data.get("open_lot_shares", 0) or 0),
      reserved_lot_shares=int(data.get("reserved_lot_shares", 0) or 0),
      waiting_inventory_levels=int(data.get("waiting_inventory_levels", 0) or 0),
      completed_cycles=int(data.get("completed_cycles", 0) or 0),
      release_event_count=int(data.get("release_event_count", 0) or 0),
    )


@strawberry.type(description="Pullback Grid 网格簿档位")
class StrategyGridBookLevel:
  grid_id: str
  level_index: int
  side: str
  role: str = "BUY_SLOT"
  price: float = 0.0
  planned_shares: int = 0
  amount: float = 0.0
  pct_from_base: Optional[float] = None
  expected_profit: Optional[float] = None
  enabled: bool = True
  status: str = "PLANNED"
  monitoring: bool = False
  pending_shares: int = 0
  filled_shares: int = 0
  available_inventory_shares: int = 0
  reserved_inventory_shares: int = 0
  cycle_count: int = 0
  waiting_reason: Optional[str] = None
  order_id: Optional[str] = None
  entry_price: Optional[float] = None
  entry_time: Optional[str] = None
  last_intent_id: Optional[str] = None
  last_trace_id: Optional[str] = None
  reason: Optional[str] = None
  updated_at: Optional[str] = None

  @staticmethod
  def from_dict(data: dict) -> "StrategyGridBookLevel":
    data = dict(data or {})
    return StrategyGridBookLevel(
      grid_id=str(data.get("grid_id") or ""),
      level_index=int(data.get("level_index", 0) or 0),
      side=str(data.get("side") or ""),
      role=str(data.get("role") or ("BUY_SLOT" if data.get("side") == "BUY" else "SELL_WATERLINE")),
      price=float(data.get("price", 0.0) or 0.0),
      planned_shares=int(data.get("planned_shares", 0) or 0),
      amount=float(data.get("amount", 0.0) or 0.0),
      pct_from_base=data.get("pct_from_base"),
      expected_profit=data.get("expected_profit"),
      enabled=bool(data.get("enabled", True)),
      status=str(data.get("status") or "PLANNED"),
      monitoring=bool(data.get("monitoring", False)),
      pending_shares=int(data.get("pending_shares", 0) or 0),
      filled_shares=int(data.get("filled_shares", 0) or 0),
      available_inventory_shares=int(data.get("available_inventory_shares", 0) or 0),
      reserved_inventory_shares=int(data.get("reserved_inventory_shares", 0) or 0),
      cycle_count=int(data.get("cycle_count", 0) or 0),
      waiting_reason=data.get("waiting_reason"),
      order_id=data.get("order_id"),
      entry_price=data.get("entry_price"),
      entry_time=data.get("entry_time"),
      last_intent_id=data.get("last_intent_id"),
      last_trace_id=data.get("last_trace_id"),
      reason=data.get("reason"),
      updated_at=data.get("updated_at"),
    )


@strawberry.type(description="Pullback Grid 库存批次")
class StrategyGridInventoryLot:
  lot_id: str
  source_level_id: Optional[str] = None
  source_level_index: Optional[int] = None
  source: str = "BUY_FILL"
  bucket: str = "swing"
  entry_price: float = 0.0
  original_shares: int = 0
  remaining_shares: int = 0
  reserved_shares: int = 0
  reserved_for_level_id: Optional[str] = None
  reserved_order_id: Optional[str] = None
  status: str = "OPEN"
  created_at: Optional[str] = None
  updated_at: Optional[str] = None

  @staticmethod
  def from_dict(data: dict) -> "StrategyGridInventoryLot":
    data = dict(data or {})
    return StrategyGridInventoryLot(
      lot_id=str(data.get("lot_id") or ""),
      source_level_id=data.get("source_level_id"),
      source_level_index=data.get("source_level_index"),
      source=str(data.get("source") or "BUY_FILL"),
      bucket=str(data.get("bucket") or "swing"),
      entry_price=float(data.get("entry_price", 0.0) or 0.0),
      original_shares=int(data.get("original_shares", 0) or 0),
      remaining_shares=int(data.get("remaining_shares", 0) or 0),
      reserved_shares=int(data.get("reserved_shares", 0) or 0),
      reserved_for_level_id=data.get("reserved_for_level_id"),
      reserved_order_id=data.get("reserved_order_id"),
      status=str(data.get("status") or "OPEN"),
      created_at=data.get("created_at"),
      updated_at=data.get("updated_at"),
    )


@strawberry.type(description="Pullback Grid 释放记录")
class StrategyGridReleaseEvent:
  event_id: str
  sell_level_id: Optional[str] = None
  sell_level_index: Optional[int] = None
  released_level_id: Optional[str] = None
  released_level_index: Optional[int] = None
  lot_ids: List[str] = strawberry.field(default_factory=list)
  order_id: Optional[str] = None
  intent_id: Optional[str] = None
  trade_id: Optional[str] = None
  price: float = 0.0
  shares: int = 0
  created_at: Optional[str] = None

  @staticmethod
  def from_dict(data: dict) -> "StrategyGridReleaseEvent":
    data = dict(data or {})
    return StrategyGridReleaseEvent(
      event_id=str(data.get("event_id") or ""),
      sell_level_id=data.get("sell_level_id"),
      sell_level_index=data.get("sell_level_index"),
      released_level_id=data.get("released_level_id"),
      released_level_index=data.get("released_level_index"),
      lot_ids=[str(value) for value in list(data.get("lot_ids") or [])],
      order_id=data.get("order_id"),
      intent_id=data.get("intent_id"),
      trade_id=data.get("trade_id"),
      price=float(data.get("price", 0.0) or 0.0),
      shares=int(data.get("shares", 0) or 0),
      created_at=data.get("created_at"),
    )


@strawberry.type(description="Pullback Grid 网格簿")
class StrategyGridBook:
  run_id: str
  instrument_code: str
  base_price: float = 0.0
  parameter_version: str = ""
  version: int = 1
  model_version: int = 2
  inventory_model: str = "INVENTORY_LEDGER_GRID"
  release_rule: str = "NEAREST_LOWER"
  sell_empty_behavior: str = "WAIT_FOR_INVENTORY"
  editable: bool = False
  needs_backtest: bool = False
  summary: StrategyGridBookSummary = strawberry.field(
    default_factory=StrategyGridBookSummary
  )
  levels: List[StrategyGridBookLevel] = strawberry.field(default_factory=list)
  inventory_lots: List[StrategyGridInventoryLot] = strawberry.field(default_factory=list)
  release_events: List[StrategyGridReleaseEvent] = strawberry.field(default_factory=list)
  updated_at: Optional[str] = None

  @staticmethod
  def from_dict(data: dict) -> "StrategyGridBook":
    data = dict(data or {})
    return StrategyGridBook(
      run_id=str(data.get("run_id") or ""),
      instrument_code=str(data.get("instrument_code") or ""),
      base_price=float(data.get("base_price", 0.0) or 0.0),
      parameter_version=str(data.get("parameter_version") or ""),
      version=int(data.get("version", 1) or 1),
      model_version=int(data.get("model_version", 2) or 2),
      inventory_model=str(data.get("inventory_model") or "INVENTORY_LEDGER_GRID"),
      release_rule=str(data.get("release_rule") or "NEAREST_LOWER"),
      sell_empty_behavior=str(data.get("sell_empty_behavior") or "WAIT_FOR_INVENTORY"),
      editable=bool(data.get("editable", False)),
      needs_backtest=bool(data.get("needs_backtest", False)),
      summary=StrategyGridBookSummary.from_dict(data.get("summary") or {}),
      levels=[
        StrategyGridBookLevel.from_dict(level)
        for level in list(data.get("levels") or [])
      ],
      inventory_lots=[
        StrategyGridInventoryLot.from_dict(lot)
        for lot in list(data.get("inventory_lots") or [])
      ],
      release_events=[
        StrategyGridReleaseEvent.from_dict(event)
        for event in list(data.get("release_events") or [])
      ],
      updated_at=data.get("updated_at"),
    )


@strawberry.input(description="Pullback Grid 网格簿档位更新")
class StrategyGridBookLevelInput:
  grid_id: Optional[str] = None
  level_index: int
  side: str
  price: float
  planned_shares: int
  pct_from_base: Optional[float] = None
  expected_profit: Optional[float] = None
  enabled: bool = True


@strawberry.input(description="Pullback Grid 网格簿更新输入")
class StrategyGridBookUpdateInput:
  levels: List[StrategyGridBookLevelInput]
  base_price: Optional[float] = None


@strawberry.type(description="策略实例事件")
class StrategyInstanceEvent:
  instance_id: str
  event_type: str
  timestamp: datetime
  payload: JSON = strawberry.field(default_factory=dict)


@strawberry.input(description="创建策略实例输入")
class StrategyInstanceCreateInput:
  strategy_key: str
  instrument_code: str
  display_name: Optional[str] = None
  mode: StrategyRunMode = StrategyRunMode.PAPER
  parameters: Optional[JSON] = None
  start_time: Optional[datetime] = None
  end_time: Optional[datetime] = None


@strawberry.type(description="允许原生移动端修改的单个策略参数")
class StrategyMobileParameter:
  key: str
  title: str
  description: str
  value_type: str
  current_value: JSON
  unit: Optional[str] = None
  minimum: Optional[float] = None
  maximum: Optional[float] = None
  step: Optional[float] = None
  enum_values: Optional[List[str]] = None
  apply_immediately: bool = False
  risk_level: str = "LOW"


@strawberry.type(description="策略实例的移动安全参数与乐观锁版本")
class StrategyInstanceMobileParameters:
  instance_id: str
  config_version: str
  editable: bool
  parameters: List[StrategyMobileParameter]


@strawberry.input(description="更新策略实例参数输入")
class StrategyInstanceParameterUpdateInput:
  parameters: JSON
  apply_immediately: bool = False
  expected_version: Optional[str] = strawberry.field(
    default=None,
    description="原生移动端必填；必须等于当前 configVersion",
  )


@strawberry.enum(description="需要设备逐次确认的实盘策略控制动作")
class StrategyControlAction(str, Enum):
  START_LIVE = "START_LIVE"
  RESUME_LIVE = "RESUME_LIVE"
  CLONE_TO_LIVE = "CLONE_TO_LIVE"


@strawberry.input(description="实盘策略控制预览输入")
class StrategyControlPreviewInput:
  account_id: str = strawberry.field(description="当前设备会话绑定的资金账号")
  instance_id: str = strawberry.field(description="目标或来源策略实例 ID")
  action: StrategyControlAction
  expected_config_version: str = strawberry.field(
    description="必须等于当前移动参数 configVersion",
  )
  idempotency_key: str = strawberry.field(
    description="调用方生成、当前动作内唯一的幂等键",
  )


@strawberry.input(description="实盘策略控制确认输入")
class StrategyControlConfirmationInput:
  challenge_id: str
  confirmation_token: str


@strawberry.type(description="策略实盘就绪检查项")
class StrategyControlReadinessCheck:
  code: str
  passed: bool
  message: str


@strawberry.type(description="实盘策略控制的服务端预览")
class StrategyControlPreview:
  challenge_id: str
  confirmation_token: str
  account_id: str
  instance_id: str
  target_instance_id: str
  action: StrategyControlAction
  current_mode: str
  current_status: str
  config_version: str
  readiness_status: str
  snapshot_id: Optional[str]
  snapshot_at: Optional[datetime]
  challenge_expires_at: datetime
  checks: List[StrategyControlReadinessCheck]
  warnings: List[str]


@strawberry.type(description="实盘策略控制预览结果")
class StrategyControlPreviewResult:
  success: bool
  code: str
  message: str
  preview: Optional[StrategyControlPreview] = None


@strawberry.type(description="实盘策略控制确认结果")
class StrategyControlConfirmationResult:
  success: bool
  code: str
  message: str
  challenge_id: Optional[str] = None
  instance_id: Optional[str] = None
  status: Optional[str] = None


def _json_object(value) -> dict:
  if value is None:
    return {}
  if isinstance(value, dict):
    return dict(value)
  if isinstance(value, str):
    import json

    try:
      parsed = json.loads(value)
      return parsed if isinstance(parsed, dict) else {}
    except Exception:
      return {}
  return {}


@strawberry.type(description="回测历史记录")
class StrategyBacktest:
  id: str = strawberry.field(description="回测ID")
  strategy_run_id: str = strawberry.field(description="关联的策略运行ID")
  version: int = strawberry.field(description="版本号")
  parameters: Optional[JSON] = strawberry.field(default=None, description="回测参数")
  instruments: Optional[List[str]] = strawberry.field(default=None, description="交易标的")
  backtest_start_time: Optional[datetime] = strawberry.field(default=None, description="回测数据起始时间")
  backtest_end_time: Optional[datetime] = strawberry.field(default=None, description="回测数据结束时间")
  start_time: Optional[datetime] = strawberry.field(default=None, description="执行开始时间")
  end_time: Optional[datetime] = strawberry.field(default=None, description="执行结束时间")
  metrics: Optional[JSON] = strawberry.field(default=None, description="回测绩效指标")
  status: str = strawberry.field(default="PENDING", description="状态")
  error_message: Optional[str] = strawberry.field(default=None, description="错误信息")
  result_path: Optional[str] = strawberry.field(default=None, description="结果文件路径")
  created_at: Optional[datetime] = strawberry.field(default=None, description="创建时间")

  @staticmethod
  def from_model(model) -> "StrategyBacktest":
    """从数据库 Model 转换为 GraphQL 类型"""
    return StrategyBacktest(
      id=model.id,
      strategy_run_id=model.strategy_run_id,
      version=model.version,
      parameters=model.parameters,
      instruments=model.instruments,
      backtest_start_time=model.backtest_start_time,
      backtest_end_time=model.backtest_end_time,
      start_time=model.start_time,
      end_time=model.end_time,
      metrics=model.metrics,
      status=model.status,
      error_message=model.error_message,
      result_path=model.result_path,
      created_at=model.created_at,
    )


@strawberry.type(description="策略绩效曲线点")
class StrategyPerformancePoint:
  sequence: int
  timestamp: str
  equity: float
  value: float
  benchmark_value: Optional[float] = None
  event_type: str = "event"

  @staticmethod
  def from_dict(data) -> "StrategyPerformancePoint":
    return StrategyPerformancePoint(
      sequence=int((data or {}).get("sequence") or 0),
      timestamp=str((data or {}).get("timestamp") or ""),
      equity=float((data or {}).get("equity") or 0.0),
      value=float((data or {}).get("value") or 0.0),
      benchmark_value=(data or {}).get("benchmark_value"),
      event_type=str((data or {}).get("event_type") or "event"),
    )


@strawberry.type(description="策略月度收益")
class StrategyMonthlyReturn:
  month: str
  return_pct: float

  @staticmethod
  def from_dict(data) -> "StrategyMonthlyReturn":
    return StrategyMonthlyReturn(
      month=str((data or {}).get("month") or ""),
      return_pct=float((data or {}).get("return_pct") or 0.0),
    )


@strawberry.type(description="策略绩效分页信息")
class StrategyPerformancePageInfo:
  has_more: bool = False
  next_cursor: Optional[str] = None

  @staticmethod
  def from_dict(data) -> "StrategyPerformancePageInfo":
    return StrategyPerformancePageInfo(
      has_more=bool((data or {}).get("has_more")),
      next_cursor=(data or {}).get("next_cursor"),
    )


@strawberry.type(description="策略绩效数据质量")
class StrategyPerformanceDataQuality:
  status: str = "OK"
  warning: Optional[str] = None
  sample_count: int = 0
  returned_sample_count: int = 0
  truncated: bool = False
  raw_sample_count: int = 0
  compressed_sample_count: int = 0
  compression_policy: Optional[str] = None

  @staticmethod
  def from_dict(data) -> "StrategyPerformanceDataQuality":
    raw_sample_count = int(
      (data or {}).get("raw_sample_count")
      or (data or {}).get("sample_count")
      or 0
    )
    compressed_sample_count = int(
      (data or {}).get("compressed_sample_count")
      or (data or {}).get("sample_count")
      or 0
    )
    return StrategyPerformanceDataQuality(
      status=str((data or {}).get("status") or "OK"),
      warning=(data or {}).get("warning"),
      sample_count=int((data or {}).get("sample_count") or 0),
      returned_sample_count=int((data or {}).get("returned_sample_count") or 0),
      truncated=bool((data or {}).get("truncated")),
      raw_sample_count=raw_sample_count,
      compressed_sample_count=compressed_sample_count,
      compression_policy=(data or {}).get("compression_policy"),
    )


@strawberry.type(description="策略绩效视图")
class StrategyPerformance:
  run_id: str
  backtest_id: Optional[str] = None
  mode: str = ""
  benchmark_code: Optional[str] = None
  source: str = ""
  generated_at: str = ""
  summary_only: bool = False
  summary: JSON = strawberry.field(default_factory=dict)
  risk: JSON = strawberry.field(default_factory=dict)
  trade_stats: JSON = strawberry.field(default_factory=dict)
  execution_quality: JSON = strawberry.field(default_factory=dict)
  equity_curve: List[StrategyPerformancePoint] = strawberry.field(default_factory=list)
  drawdown_curve: List[StrategyPerformancePoint] = strawberry.field(default_factory=list)
  monthly_returns: List[StrategyMonthlyReturn] = strawberry.field(default_factory=list)
  data_quality: StrategyPerformanceDataQuality = strawberry.field(
    default_factory=StrategyPerformanceDataQuality
  )
  page_info: StrategyPerformancePageInfo = strawberry.field(
    default_factory=StrategyPerformancePageInfo
  )

  @staticmethod
  def from_dict(data) -> "StrategyPerformance":
    data = data or {}
    return StrategyPerformance(
      run_id=str(data.get("run_id") or ""),
      backtest_id=data.get("backtest_id"),
      mode=str(data.get("mode") or ""),
      benchmark_code=data.get("benchmark_code"),
      source=str(data.get("source") or ""),
      generated_at=str(data.get("generated_at") or ""),
      summary_only=bool(data.get("summary_only")),
      summary=data.get("summary") or {},
      risk=data.get("risk") or {},
      trade_stats=data.get("trade_stats") or {},
      execution_quality=data.get("execution_quality") or {},
      equity_curve=[
        StrategyPerformancePoint.from_dict(item)
        for item in list(data.get("equity_curve") or [])
      ],
      drawdown_curve=[
        StrategyPerformancePoint.from_dict(item)
        for item in list(data.get("drawdown_curve") or [])
      ],
      monthly_returns=[
        StrategyMonthlyReturn.from_dict(item)
        for item in list(data.get("monthly_returns") or [])
      ],
      data_quality=StrategyPerformanceDataQuality.from_dict(
        data.get("data_quality") or {}
      ),
      page_info=StrategyPerformancePageInfo.from_dict(data.get("page_info") or {}),
    )


@strawberry.input(description="策略模板输入参数")
class StrategyInput:
  name: str = strawberry.field(description="策略名称")
  description: str = strawberry.field(description="策略描述")
  file_path: str = strawberry.field(description="文件路径")
  class_name: str = strawberry.field(description="类名")
  category: Optional[StrategyCategory] = strawberry.field(
    default=None, description="策略分类"
  )
  risk_level: Optional[RiskLevel] = strawberry.field(
    default=None, description="风险等级"
  )
  tags: Optional[List[str]] = strawberry.field(default=None, description="策略标签列表")
  parameter_schema: Optional[str] = strawberry.field(
    default=None, description="参数Schema定义(JSON Schema格式)"
  )
  version: Optional[str] = strawberry.field(default=None, description="策略版本号")
  status: Optional[StrategyStatus] = strawberry.field(
    default=None, description="策略状态"
  )


@strawberry.input(description="策略模板更新输入参数")
class StrategyUpdateInput:
  name: Optional[str] = strawberry.field(default=None, description="策略名称")
  description: Optional[str] = strawberry.field(default=None, description="策略描述")
  file_path: Optional[str] = strawberry.field(default=None, description="文件路径")
  class_name: Optional[str] = strawberry.field(default=None, description="类名")
  category: Optional[StrategyCategory] = strawberry.field(
    default=None, description="策略分类"
  )
  risk_level: Optional[RiskLevel] = strawberry.field(
    default=None, description="风险等级"
  )
  tags: Optional[List[str]] = strawberry.field(default=None, description="策略标签列表")
  parameter_schema: Optional[str] = strawberry.field(
    default=None, description="参数Schema定义(JSON Schema格式)"
  )
  version: Optional[str] = strawberry.field(default=None, description="策略版本号")
  status: Optional[StrategyStatus] = strawberry.field(
    default=None, description="策略状态"
  )


@strawberry.input(description="策略运行实例输入参数")
class StrategyRunInput:
  name: Optional[str] = strawberry.field(
    default=None, description="运行实例名称（可选）"
  )
  strategy_id: int = strawberry.field(description="策略模板ID")
  mode: StrategyRunMode = strawberry.field(description="运行模式")
  instruments: List[str] = strawberry.field(description="交易标的列表")
  parameters: JSON = strawberry.field(description="策略参数")
  start_time: Optional[datetime] = strawberry.field(
    description="开始时间（回测模式必需）", default=None
  )
  end_time: Optional[datetime] = strawberry.field(
    description="结束时间（回测模式必需）", default=None
  )


@strawberry.input(description="策略运行实例更新输入参数")
class StrategyRunUpdateInput:
  parameters: Optional[JSON] = strawberry.field(description="策略参数")
