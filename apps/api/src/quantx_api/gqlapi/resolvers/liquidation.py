"""
卖出管理与统一退出计划 GraphQL 解析器
"""

import hashlib
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import List, Optional

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder as ConditionalLiquidationOrderModel,
)
from quantx_infrastructure.models.liquidation import (
  LiquidationOrder as LiquidationOrderModel,
)
from quantx_infrastructure.models.liquidation import (
  LiquidationStatus,
)
from quantx_infrastructure.models.liquidation import (
  RedemptionRecord as RedemptionRecordModel,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanRepository,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.engine_command_service import engine_command_service
from quantx_infrastructure.services.exit_plan_replay_service import (
  ExitPlanReplayService,
)
from quantx_infrastructure.services.liquidation_service import LiquidationService
from sqlalchemy import desc, select

from quantx_api.auth.errors import AuthError

from ..types import MessageResponse
from ..types.liquidation_types import (
  ConditionalLiquidationEvaluationResult,
  ConditionalLiquidationOrder,
  ConditionalLiquidationOrderInput,
  CreateManualExitPlanInput,
  ExitPlanCapabilities,
  ExitPlanCapacityConflict,
  ExitPlanCapacityReconciliationResult,
  ExitPlanCostBasisCandidate,
  ExitPlanCostBasisCandidates,
  ExitPlanEventView,
  ExitPlanHoldingCapacity,
  ExitPlanReplay,
  ExitPlanReplayActualSellReference,
  ExitPlanReplayBuyFill,
  ExitPlanReplayCurvePoint,
  ExitPlanReplayEvent,
  ExitPlanReplayEventPage,
  ExitPlanReplayHorizon,
  ExitPlanReplayMutationResult,
  ExitPlanReplayPreparation,
  ExitPlanReplayPreparationInput,
  ExitPlanReplayReport,
  ExitPlanReplayStartInput,
  ExitPlanReplaySummary,
  ExitPlanRuleCapability,
  ExitPlanView,
  LiquidatablePosition,
  LiquidateAllPositionsInput,
  LiquidatePositionInput,
  LiquidatePositionsInput,
  LiquidationError,
  LiquidationGroupResult,
  LiquidationOrder,
  LiquidationPlanResult,
  LiquidationResult,
  LiquidationSummary,
  PositionLiquidationResult,
  RedeemPositionInput,
  RedemptionRecord,
  RedemptionResult,
  UpdateManualExitPlanInput,
)


class LiquidationResolver:
  """卖出管理与统一退出计划解析器。"""

  @staticmethod
  def _liquidation_order(model: LiquidationOrderModel) -> LiquidationOrder:
    return LiquidationOrder(
      id=model.id,
      account_id=model.account_id,
      liquidation_type=model.liquidation_type,
      status=model.status,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      target_volume=model.target_volume,
      completed_volume=int(model.completed_volume or 0),
      target_amount=float(model.target_amount)
      if model.target_amount is not None
      else None,
      completed_amount=float(model.completed_amount or 0),
      start_time=model.start_time.isoformat() if model.start_time else None,
      end_time=model.end_time.isoformat() if model.end_time else None,
      retry_count=int(model.retry_count or 0),
      remark=model.remark,
      error_message=model.error_message,
      created_at=model.created_at.isoformat() if model.created_at else None,
    )

  @staticmethod
  def _redemption_record(model: RedemptionRecordModel) -> RedemptionRecord:
    return RedemptionRecord(
      id=model.id,
      account_id=model.account_id,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      redemption_amount=float(model.redemption_amount),
      available_amount=float(model.available_amount)
      if model.available_amount is not None
      else None,
      redeemed_amount=float(model.redeemed_amount or 0),
      status=model.status,
      redemption_date=model.redemption_date.isoformat()
      if model.redemption_date
      else None,
      expected_arrival_date=model.expected_arrival_date.isoformat()
      if model.expected_arrival_date
      else None,
      actual_arrival_date=model.actual_arrival_date.isoformat()
      if model.actual_arrival_date
      else None,
      redemption_fee=float(model.redemption_fee or 0),
      remark=model.remark,
      created_at=model.created_at.isoformat() if model.created_at else None,
    )

  @staticmethod
  def _conditional_evaluation_result(
    result,
    exit_plan: Optional[AutoExitPlanRecord] = None,
  ) -> ConditionalLiquidationEvaluationResult:
    if isinstance(result, dict):
      order_data = dict(result.get("order") or {})
      for field in (
        "triggered_at",
        "last_checked_at",
        "created_at",
        "updated_at",
      ):
        value = order_data.get(field)
        if isinstance(value, str):
          order_data[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
      result = SimpleNamespace(
        **{
          **result,
          "order": SimpleNamespace(**order_data),
        }
      )
    return ConditionalLiquidationEvaluationResult(
      order=ConditionalLiquidationOrder.from_model(result.order, exit_plan),
      triggered=result.triggered,
      submitted=result.submitted,
      message=result.message,
      sell_volume=result.sell_volume,
      order_id=result.order_id,
      latest_price=result.latest_price,
      profit_pct=result.profit_pct,
      error=result.error,
    )

  @staticmethod
  async def _exit_plan_map(orders) -> dict[str, AutoExitPlanRecord]:
    plan_ids = {
      str(getattr(order, "exit_plan_id", None) or "")
      for order in orders
      if getattr(order, "exit_plan_id", None)
    }
    if not plan_ids:
      return {}
    async for db in get_async_db():
      result = await db.execute(
        select(AutoExitPlanRecord).where(
          AutoExitPlanRecord.plan_id.in_(plan_ids)
        )
      )
      return {item.plan_id: item for item in result.scalars().all()}
    return {}

  @staticmethod
  async def _request_engine(
    command_type: str,
    payload: dict,
    *,
    aggregate_id: str,
    idempotency_key: Optional[str] = None,
  ) -> dict:
    receipt = await engine_command_service.request(
      command_type,
      payload,
      aggregate_id=aggregate_id,
      idempotency_key=(
        idempotency_key
        or f"{command_type.lower()}:{aggregate_id}:{uuid.uuid4()}"
      ),
    )
    if receipt.status == "FAILED":
      raise ValueError(receipt.error or f"{command_type} 执行失败")
    if receipt.status != "SUCCEEDED":
      raise RuntimeError(f"Engine 尚未确认操作: {receipt.message_id}")
    return dict(receipt.result or {})

  @staticmethod
  def _datetime(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
      return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

  @classmethod
  def _exit_plan_replay(cls, data: dict) -> ExitPlanReplay:
    summary_data = data.get("summary")
    summary = None
    if isinstance(summary_data, dict):
      summary = ExitPlanReplaySummary(
        **{
          **summary_data,
          "exit_time": cls._datetime(summary_data.get("exit_time")),
        }
      )
    report_data = data.get("report")
    report = None
    if isinstance(report_data, dict):
      report = ExitPlanReplayReport(
        **{
          **report_data,
          "generated_at": cls._datetime(report_data.get("generated_at")),
        }
      )
    return ExitPlanReplay(
      run_id=str(data["run_id"]),
      backtest_id=data.get("backtest_id"),
      account_id=str(data["account_id"]),
      plan_id=data.get("plan_id"),
      config_version=int(data.get("config_version") or 0),
      instrument_code=str(data["instrument_code"]),
      status=str(data.get("status") or "PENDING"),
      progress_pct=float(data.get("progress_pct") or 0.0),
      revision=str(data.get("revision") or "0"),
      processed_until=cls._datetime(data.get("processed_until")),
      start_time=cls._datetime(data.get("start_time")),
      end_time=cls._datetime(data.get("end_time")),
      created_at=cls._datetime(data.get("created_at")),
      updated_at=cls._datetime(data.get("updated_at")),
      error_message=data.get("error_message"),
      data_quality=str(data.get("data_quality") or "RUNNING"),
      data_quality_message=str(data.get("data_quality_message") or ""),
      plan_snapshot=dict(data.get("plan_snapshot") or {}),
      origin=dict(data.get("origin") or {}),
      summary=summary,
      curve=[
        ExitPlanReplayCurvePoint(
          **{
            **item,
            "timestamp": cls._datetime(item.get("timestamp")),
          }
        )
        for item in list(data.get("curve") or [])
      ],
      events=[
        ExitPlanReplayEvent(
          **{
            **item,
            "timestamp": cls._datetime(item.get("timestamp")),
          }
        )
        for item in list(data.get("events") or [])
      ],
      post_exit_horizons=[
        ExitPlanReplayHorizon(**item)
        for item in list(data.get("post_exit_horizons") or [])
      ],
      actual_sell_references=[
        ExitPlanReplayActualSellReference(
          **{
            **item,
            "timestamp": cls._datetime(item.get("timestamp")),
          }
        )
        for item in list(data.get("actual_sell_references") or [])
      ],
      report=report,
    )

  @staticmethod
  def _exit_plan_replay_payload(input: ExitPlanReplayStartInput) -> dict:
    return {
      "account_id": input.account_id,
      "plan_id": input.plan_id,
      "expected_config_version": input.expected_config_version,
      "draft_template": dict(input.draft_template or {}) or None,
      "start_time": input.start_time.isoformat(),
      "end_time": input.end_time.isoformat(),
      "origin": {
        "mode": input.origin.mode,
        "order_ids": list(input.origin.order_ids),
        "activation_time": (
          input.origin.activation_time.isoformat()
          if input.origin.activation_time
          else None
        ),
        "volume": input.origin.volume,
        "unit_cost": input.origin.unit_cost,
      },
      "commission_rate": input.commission_rate,
      "minimum_commission": input.minimum_commission,
      "stamp_tax_rate": input.stamp_tax_rate,
      "transfer_fee_rate": input.transfer_fee_rate,
      "slippage_rate": input.slippage_rate,
    }

  @staticmethod
  async def _load_exit_plan(
    plan_id: str,
    *,
    account_id: Optional[str] = None,
  ) -> Optional[AutoExitPlanRecord]:
    async for db in get_async_db():
      record = await AutoExitPlanRepository(db).find_by_id(plan_id)
      if record is not None and account_id and record.account_id != account_id:
        return None
      return record
    return None

  @staticmethod
  async def exit_plan_account_id(plan_id: str) -> Optional[str]:
    record = await LiquidationResolver._load_exit_plan(plan_id)
    return record.account_id if record is not None else None

  @classmethod
  async def prepare_exit_plan_replay(
    cls,
    input: ExitPlanReplayPreparationInput,
    account_id: str,
  ) -> ExitPlanReplayPreparation:
    data = await ExitPlanReplayService().prepare(
      {
        "account_id": account_id,
        "plan_id": input.plan_id,
        "draft_template": dict(input.draft_template or {}) or None,
      }
    )
    return ExitPlanReplayPreparation(
      account_id=str(data["account_id"]),
      plan_id=data.get("plan_id"),
      config_version=int(data.get("config_version") or 0),
      instrument_code=str(data["instrument_code"]),
      plan_source=str(data["plan_source"]),
      template=dict(data.get("template") or {}),
      requires_tick=bool(data.get("requires_tick")),
      requires_depth=bool(data.get("requires_depth")),
      default_window_trading_days=int(
        data.get("default_window_trading_days") or 20
      ),
      quick_windows=[int(item) for item in data.get("quick_windows") or []],
      buy_fills=[
        ExitPlanReplayBuyFill(
          **{
            **item,
            "order_time": cls._datetime(item.get("order_time")),
          }
        )
        for item in list(data.get("buy_fills") or [])
      ],
      message=str(data.get("message") or ""),
      blocking_reasons=[str(item) for item in data.get("blocking_reasons") or []],
    )

  @classmethod
  async def get_exit_plan_replay(cls, run_id: str) -> Optional[ExitPlanReplay]:
    data = await ExitPlanReplayService().get(run_id)
    return cls._exit_plan_replay(data) if data is not None else None

  @staticmethod
  async def exit_plan_replay_account_id(run_id: str) -> Optional[str]:
    data = await ExitPlanReplayService().get(run_id)
    return str(data.get("account_id") or "") if data is not None else None

  @classmethod
  async def get_exit_plan_replay_history(
    cls, account_id: str, *, limit: int = 20
  ) -> List[ExitPlanReplay]:
    rows = await ExitPlanReplayService().history(account_id, limit)
    return [cls._exit_plan_replay(item) for item in rows]

  @classmethod
  async def get_exit_plan_replay_events(
    cls,
    run_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
  ) -> ExitPlanReplayEventPage:
    data = await ExitPlanReplayService().events(run_id, offset, limit)
    return ExitPlanReplayEventPage(
      run_id=str(data["run_id"]),
      total=int(data["total"]),
      offset=int(data["offset"]),
      limit=int(data["limit"]),
      has_more=bool(data["has_more"]),
      items=[
        ExitPlanReplayEvent(
          **{
            **item,
            "timestamp": cls._datetime(item.get("timestamp")),
          }
        )
        for item in list(data.get("items") or [])
      ],
    )

  @classmethod
  async def start_exit_plan_replay(
    cls,
    input: ExitPlanReplayStartInput,
    account_id: str,
  ) -> ExitPlanReplayMutationResult:
    key = str(input.idempotency_key or "").strip()
    if not key or len(key) > 128:
      return ExitPlanReplayMutationResult(
        success=False,
        code="INVALID_IDEMPOTENCY_KEY",
        message="回放幂等键不能为空且长度不能超过 128",
      )
    payload = cls._exit_plan_replay_payload(input)
    payload["account_id"] = account_id
    receipt = await engine_command_service.request(
      "EXIT_PLAN_REPLAY_START",
      {"input": payload},
      aggregate_id=account_id,
      idempotency_key=f"exit-plan-replay-start:{account_id}:{key}",
    )
    if receipt.status == "FAILED":
      return ExitPlanReplayMutationResult(
        success=False,
        code="START_FAILED",
        message=receipt.error or "卖出计划回放启动失败",
        run_id=receipt.message_id,
      )
    if receipt.status != "SUCCEEDED":
      return ExitPlanReplayMutationResult(
        success=True,
        code="QUEUED",
        message="卖出计划回放已进入 Engine 队列",
        run_id=receipt.message_id,
      )
    replay = cls._exit_plan_replay(dict(receipt.result or {}))
    return ExitPlanReplayMutationResult(
      success=True,
      code="STARTED",
      message="卖出计划回放已启动",
      run_id=replay.run_id,
      replay=replay,
    )

  @classmethod
  async def cancel_exit_plan_replay(
    cls,
    run_id: str,
    account_id: str,
  ) -> ExitPlanReplayMutationResult:
    receipt = await engine_command_service.request(
      "EXIT_PLAN_REPLAY_CANCEL",
      {"run_id": run_id},
      aggregate_id=account_id,
      idempotency_key=f"exit-plan-replay-cancel:{run_id}",
    )
    if receipt.status == "FAILED":
      return ExitPlanReplayMutationResult(
        success=False,
        code="CANCEL_FAILED",
        message=receipt.error or "卖出计划回放取消失败",
        run_id=run_id,
      )
    if receipt.status != "SUCCEEDED":
      return ExitPlanReplayMutationResult(
        success=True,
        code="QUEUED",
        message="取消请求已进入 Engine 队列",
        run_id=run_id,
      )
    return ExitPlanReplayMutationResult(
      success=True,
      code="CANCELLED",
      message="卖出计划回放已取消",
      run_id=run_id,
      replay=cls._exit_plan_replay(dict(receipt.result or {})),
    )

  @staticmethod
  async def get_exit_plans(
    account_id: str,
    *,
    instrument_code: Optional[str] = None,
    statuses: Optional[list[str]] = None,
    source_type: Optional[str] = None,
    limit: int = 200,
  ) -> List[ExitPlanView]:
    async for db in get_async_db():
      records = await AutoExitPlanRepository(db).find_all(
        account_id=account_id,
        instrument_code=(
          str(instrument_code or "").strip().upper() or None
        ),
        statuses=[str(item).upper() for item in statuses or []] or None,
        source_type=str(source_type or "").upper() or None,
        limit=limit,
      )
      return [ExitPlanView.from_model(record) for record in records]
    return []

  @staticmethod
  async def get_exit_plan(plan_id: str, account_id: str) -> Optional[ExitPlanView]:
    record = await LiquidationResolver._load_exit_plan(
      plan_id, account_id=account_id
    )
    return ExitPlanView.from_model(record) if record is not None else None

  @staticmethod
  async def get_exit_plan_events(
    plan_id: str,
    account_id: str,
    *,
    limit: int = 200,
  ) -> List[ExitPlanEventView]:
    record = await LiquidationResolver._load_exit_plan(
      plan_id, account_id=account_id
    )
    if record is None:
      return []
    async for db in get_async_db():
      events = await AutoExitPlanRepository(db).find_events(
        plan_id=plan_id, limit=limit
      )
      return [
        ExitPlanEventView(
          event_id=item.event_id,
          plan_id=item.plan_id,
          event_type=item.event_type,
          payload=dict(item.payload or {}),
          created_at=item.created_at,
        )
        for item in events
      ]
    return []

  @staticmethod
  def get_exit_plan_capabilities() -> ExitPlanCapabilities:
    labels = {
      "TARGET_PRICE": ("目标价", "price", {"target_price": "number"}),
      "STOP_PRICE": ("止损价", "price", {"stop_price": "number"}),
      "GROSS_TAKE_PROFIT": (
        "收益止盈",
        "profit",
        {"target_profit_pct": "number"},
      ),
      "NET_TAKE_PROFIT": (
        "净收益止盈",
        "profit",
        {"target_net_profit_pct": "number"},
      ),
      "TRAILING_NET_PROFIT": ("动态保盈", "trailing", {}),
      "ADAPTIVE_VOLUME_PRICE_TRAILING": ("量价动态止盈", "trailing", {}),
      "RAPID_PROFIT_REVERSAL": ("快速收益反转", "drawdown", {}),
      "TRAILING_PRICE_DRAWDOWN": ("价格回撤", "drawdown", {}),
      "HARD_STOP": ("硬止损", "risk", {"stop_loss_pct": "number"}),
      "TIME_OF_DAY": ("指定时间", "time", {"exit_time": "HH:mm"}),
      "MAX_HOLDING_DAYS": (
        "最大持有日",
        "time",
        {"max_holding_trading_days": "integer"},
      ),
      "LIMIT_UP_TOUCH": ("触及涨停", "limit_up", {}),
      "LIMIT_UP_BREAK": ("涨停开板", "limit_up", {}),
      "MANUAL_TRIGGER": ("人工计划触发", "manual", {}),
    }
    return ExitPlanCapabilities(
      rule_types=[
        ExitPlanRuleCapability(
          rule_type=rule_type,
          label=value[0],
          category=value[1],
          parameters=value[2],
        )
        for rule_type, value in labels.items()
      ],
      completion_strategies=["AVAILABLE_NOW", "UNTIL_SNAPSHOT_CLEARED"],
      conflict_strategies=["UNALLOCATED_ONLY", "REPLACE_CANCELLABLE"],
      execution_modes=["paper", "live"],
      rule_semantics="OR；按 priority 从高到低决定首个执行规则",
    )

  @staticmethod
  async def get_exit_plan_holding_capacity(
    account_id: str,
    instrument_code: str,
  ) -> ExitPlanHoldingCapacity:
    code = str(instrument_code or "").strip().upper()
    async for db in get_async_db():
      position = await db.scalar(
        select(Position)
        .where(Position.account_id == account_id)
        .where(Position.stock_code == code)
      )
      plans = await AutoExitPlanRepository(db).find_reserving(
        account_id=account_id,
        instrument_code=code,
      )
      total = int(getattr(position, "volume", 0) or 0)
      available = int(getattr(position, "can_use_volume", 0) or 0)
      frozen = int(getattr(position, "frozen_volume", 0) or 0)
      protected = sum(max(0, int(item.remaining_volume or 0)) for item in plans)
      pending = sum(
        max(0, int(item.remaining_volume or 0))
        for item in plans
        if item.status == "EXIT_PENDING" or item.pending_client_order_id
      )
      pending_reconciliation = next(
        (
          item
          for item in plans
          if str(getattr(item, "capacity_status", "READY") or "READY")
          != "READY"
        ),
        None,
      )
      capacity_ready = protected <= total and pending_reconciliation is None
      capacity_error = None
      if protected > total:
        capacity_error = f"持仓 {total} 股少于计划合计认领 {protected} 股"
      elif pending_reconciliation is not None:
        capacity_error = str(
          getattr(pending_reconciliation, "capacity_error", None)
          or "持仓容量需显式重新对账后才能继续卖出"
        )
      return ExitPlanHoldingCapacity(
        account_id=account_id,
        instrument_code=code,
        total_volume=total,
        available_volume=available,
        frozen_volume=frozen,
        protected_volume=protected,
        pending_volume=pending,
        unallocated_volume=max(0, total - protected),
        capacity_status="READY" if capacity_ready else "RECONCILE_REQUIRED",
        capacity_error=capacity_error,
        conflicts=[
          ExitPlanCapacityConflict(
            plan_id=item.plan_id,
            source_type=item.source_type,
            status=item.status,
            remaining_volume=int(item.remaining_volume or 0),
            pending=bool(
              item.status == "EXIT_PENDING" or item.pending_client_order_id
            ),
          )
          for item in plans
        ],
      )
    return ExitPlanHoldingCapacity(
      account_id=account_id,
      instrument_code=code,
      total_volume=0,
      available_volume=0,
      frozen_volume=0,
      protected_volume=0,
      pending_volume=0,
      unallocated_volume=0,
      capacity_status="READY",
      capacity_error=None,
      conflicts=[],
    )

  @staticmethod
  async def get_exit_plan_cost_basis_candidates(
    account_id: str,
    instrument_code: str,
    *,
    limit: int = 100,
  ) -> ExitPlanCostBasisCandidates:
    code = str(instrument_code or "").strip().upper()
    items = await AutoExitPlanService().list_cost_basis_candidates(
      account_id=account_id,
      instrument_code=code,
      limit=limit,
    )
    return ExitPlanCostBasisCandidates(
      account_id=account_id,
      instrument_code=code,
      items=[ExitPlanCostBasisCandidate(**item) for item in items],
      history_warning=(
        "仅展示 QuantX 已持久化且成交数量大于 0 的买入委托；"
        "若历史委托不完整，请改用手工每股全成本。"
      ),
    )

  @staticmethod
  async def get_liquidation_summary(
    account_id: Optional[str] = None,
  ) -> LiquidationSummary:
    """获取清仓概况"""
    liquidation_service = LiquidationService(account_id=account_id)
    summary_data = await liquidation_service.get_liquidation_summary()

    # 转换持仓数据
    positions = [
      LiquidatablePosition(
        stock_code=pos["stock_code"],
        instrument_name=pos.get("instrument_name"),
        volume=pos["volume"],
        can_use_volume=pos["can_use_volume"],
        market_value=pos["market_value"],
        avg_price=pos.get("avg_price"),
      )
      for pos in summary_data["positions"]
    ]

    summary = LiquidationSummary(
      total_positions=summary_data["total_positions"],
      liquidatable_positions=summary_data["liquidatable_positions"],
      total_market_value=summary_data["total_market_value"],
    )
    summary.positions = lambda: positions

    return summary

  @staticmethod
  async def get_conditional_liquidation_orders(
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    include_cancelled: bool = False,
  ) -> List[ConditionalLiquidationOrder]:
    service = LiquidationService(account_id=account_id)
    orders = await service.list_conditional_liquidation_orders(
      stock_code=stock_code,
      include_cancelled=include_cancelled,
    )
    plans = await LiquidationResolver._exit_plan_map(orders)
    return [
      ConditionalLiquidationOrder.from_model(
        order,
        plans.get(str(order.exit_plan_id or "")),
      )
      for order in orders
    ]

  @staticmethod
  async def create_manual_exit_plan(
    input: CreateManualExitPlanInput,
    account_id: str,
  ) -> ExitPlanView:
    if bool(input.auto_exit_authorized):
      raise AuthError(
        "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE",
        "创建计划不能通过布尔字段开启自动实盘，请使用预览—确认授权",
        status_code=400,
      )
    request_key = str(input.idempotency_key or "").strip()
    if not request_key or len(request_key) > 128:
      raise ValueError("创建计划幂等键不能为空且不能超过 128 个字符")
    command_key = hashlib.sha256(
      f"{account_id}:{request_key}".encode("utf-8")
    ).hexdigest()
    result = await LiquidationResolver._request_engine(
      "EXIT_PLAN_CREATE_MANUAL",
      {
        "account_id": account_id,
        "instrument_code": input.instrument_code,
        "protected_volume": input.protected_volume,
        "rules": list(input.rules or []),
        "bucket": input.bucket,
        "enabled": input.enabled,
        "execution_mode": input.execution_mode,
        "auto_exit_authorized": False,
        "remark": input.remark,
        "cost_basis": {
          "mode": input.cost_basis.mode,
          "order_ids": list(input.cost_basis.order_ids or []),
          "unit_cost_cny": input.cost_basis.unit_cost_cny,
        },
      },
      aggregate_id=f"{account_id}:{input.instrument_code.upper()}",
      idempotency_key=f"exit-plan-create:{command_key}",
    )
    record = await LiquidationResolver._load_exit_plan(
      str(result.get("plan_id") or ""), account_id=account_id
    )
    if record is None:
      raise RuntimeError("Engine 已创建计划，但读取持久化结果失败")
    return ExitPlanView.from_model(record)

  @staticmethod
  async def reconcile_exit_plan_capacity(
    account_id: str,
    instrument_code: str,
  ) -> ExitPlanCapacityReconciliationResult:
    code = str(instrument_code or "").strip().upper()
    result = await LiquidationResolver._request_engine(
      "EXIT_PLAN_RECONCILE_CAPACITY",
      {"account_id": account_id, "instrument_code": code},
      aggregate_id=f"{account_id}:{code}",
    )
    return ExitPlanCapacityReconciliationResult(
      ready=bool(result.get("ready")),
      capacity_status=str(result.get("capacity_status") or ""),
      capacity_error=str(result.get("capacity_error") or "") or None,
      total_volume=int(result.get("total_volume") or 0),
      protected_volume=int(result.get("protected_volume") or 0),
      plan_ids=[str(item) for item in list(result.get("plan_ids") or [])],
    )

  @staticmethod
  async def update_manual_exit_plan(
    input: UpdateManualExitPlanInput,
    account_id: str,
  ) -> ExitPlanView:
    if bool(input.auto_exit_authorized):
      raise AuthError(
        "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE",
        "修改计划不能通过布尔字段开启自动实盘，请重新预览并确认授权",
        status_code=400,
      )
    payload = {
      "account_id": account_id,
      "plan_id": input.plan_id,
      "config_version": input.config_version,
      "rules": list(input.rules or []),
      "remark": input.remark,
    }
    if input.protected_volume is not None:
      payload["protected_volume"] = input.protected_volume
    if input.execution_mode is not None:
      payload["execution_mode"] = input.execution_mode
    if input.auto_exit_authorized is not None:
      payload["auto_exit_authorized"] = False
    await LiquidationResolver._request_engine(
      "EXIT_PLAN_UPDATE_MANUAL",
      payload,
      aggregate_id=f"{account_id}:{input.plan_id}",
    )
    record = await LiquidationResolver._load_exit_plan(
      input.plan_id, account_id=account_id
    )
    if record is None:
      raise RuntimeError("退出计划不存在")
    return ExitPlanView.from_model(record)

  @staticmethod
  async def set_exit_plan_enabled(
    *,
    plan_id: str,
    enabled: bool,
    config_version: int,
    account_id: str,
  ) -> ExitPlanView:
    await LiquidationResolver._request_engine(
      "EXIT_PLAN_SET_ENABLED",
      {
        "plan_id": plan_id,
        "enabled": enabled,
        "config_version": config_version,
        "account_id": account_id,
      },
      aggregate_id=f"{account_id}:{plan_id}",
    )
    record = await LiquidationResolver._load_exit_plan(
      plan_id, account_id=account_id
    )
    if record is None:
      raise RuntimeError("退出计划不存在")
    return ExitPlanView.from_model(record)

  @staticmethod
  async def cancel_exit_plan(
    *,
    plan_id: str,
    config_version: int,
    reason: str,
    account_id: str,
  ) -> ExitPlanView:
    await LiquidationResolver._request_engine(
      "EXIT_PLAN_CANCEL",
      {
        "plan_id": plan_id,
        "config_version": config_version,
        "reason": reason,
        "account_id": account_id,
      },
      aggregate_id=f"{account_id}:{plan_id}",
    )
    record = await LiquidationResolver._load_exit_plan(
      plan_id, account_id=account_id
    )
    if record is None:
      raise RuntimeError("退出计划不存在")
    return ExitPlanView.from_model(record)

  @staticmethod
  async def evaluate_exit_plan_now(
    *,
    plan_id: str,
    account_id: str,
  ) -> ExitPlanView:
    await LiquidationResolver._request_engine(
      "EXIT_PLAN_EVALUATE_NOW",
      {"plan_id": plan_id, "account_id": account_id},
      aggregate_id=f"{account_id}:{plan_id}",
    )
    record = await LiquidationResolver._load_exit_plan(
      plan_id, account_id=account_id
    )
    if record is None:
      raise RuntimeError("退出计划不存在")
    return ExitPlanView.from_model(record)

  @staticmethod
  async def liquidate_positions(
    input: LiquidatePositionsInput,
    account_id: str,
  ) -> LiquidationGroupResult:
    execution_mode = str(input.execution_mode or "paper").strip().lower()
    if execution_mode != "paper" or bool(input.auto_exit_authorized):
      raise AuthError(
        "LEGACY_LIQUIDATION_UNSAFE_MODE",
        "旧清仓接口仅支持 PAPER 且不允许自动卖出授权",
        status_code=400,
      )
    result = await LiquidationResolver._request_engine(
      "EXIT_PLAN_LIQUIDATE_POSITIONS",
      {
        "account_id": account_id,
        "scope": input.scope,
        "instrument_codes": list(input.instrument_codes or []),
        "completion_strategy": input.completion_strategy,
        "conflict_strategy": input.conflict_strategy,
        # Preserve a hard safety invariant even after validating the legacy
        # compatibility input above.
        "execution_mode": "paper",
        "auto_exit_authorized": False,
        "confirm": input.confirm,
      },
      aggregate_id=account_id,
    )
    plans = [
      LiquidationPlanResult(
        instrument_code=str(item.get("instrument_code") or ""),
        success=bool(item.get("success")),
        plan_id=str(item.get("plan_id") or "") or None,
        protected_volume=(
          int(item["protected_volume"])
          if item.get("protected_volume") is not None
          else None
        ),
        conflict_plan_ids=[
          str(value) for value in list(item.get("conflict_plan_ids") or [])
        ],
        error=str(item.get("error") or "") or None,
      )
      for item in list(result.get("items") or [])
    ]
    succeeded = sum(1 for item in plans if item.success)
    return LiquidationGroupResult(
      group_id=str(result.get("group_id") or ""),
      success=bool(result.get("success")),
      message=f"已创建 {succeeded}/{len(plans)} 个清仓计划",
      plans=plans,
    )

  @staticmethod
  async def confirm_exit_intent(
    *,
    plan_id: str,
    intent_id: str,
    account_id: str,
  ) -> dict:
    return await LiquidationResolver._request_engine(
      "EXIT_PLAN_CONFIRM_INTENT",
      {
        "plan_id": plan_id,
        "intent_id": intent_id,
        "account_id": account_id,
      },
      aggregate_id=f"{account_id}:{plan_id}",
    )

  @staticmethod
  async def reject_exit_intent(
    *,
    plan_id: str,
    intent_id: str,
    reason: str,
    account_id: str,
  ) -> dict:
    return await LiquidationResolver._request_engine(
      "EXIT_PLAN_REJECT_INTENT",
      {
        "plan_id": plan_id,
        "intent_id": intent_id,
        "reason": reason,
        "account_id": account_id,
      },
      aggregate_id=f"{account_id}:{plan_id}",
    )

  @staticmethod
  async def liquidate_all_positions(
    input: LiquidateAllPositionsInput,
    account_id: str,
  ) -> LiquidationResult:
    """Compatibility adapter: old callers only protect currently sellable shares."""
    result_data = await LiquidationResolver.liquidate_positions(
      LiquidatePositionsInput(
        completion_strategy="AVAILABLE_NOW",
        conflict_strategy="UNALLOCATED_ONLY",
        confirm=input.confirm,
        account_id=account_id,
        scope="ALL",
        instrument_codes=[],
        execution_mode="paper",
        auto_exit_authorized=False,
      ),
      account_id,
    )
    errors = [
      LiquidationError(stock_code=item.instrument_code, error=item.error or "未知错误")
      for item in result_data.plans
      if not item.success
    ]
    result = LiquidationResult(
      success=result_data.success,
      total_positions=len(result_data.plans),
      liquidated_positions=sum(1 for item in result_data.plans if item.success),
      failed_positions=sum(1 for item in result_data.plans if not item.success),
      message=result_data.message,
    )
    result.errors = lambda: errors
    result.orders = lambda: [
      str(item.plan_id) for item in result_data.plans if item.plan_id
    ]
    return result

  @staticmethod
  async def liquidate_position(
    input: LiquidatePositionInput,
    account_id: str,
  ) -> PositionLiquidationResult:
    """Compatibility adapter fixed to AVAILABLE_NOW semantics."""
    group = await LiquidationResolver.liquidate_positions(
      LiquidatePositionsInput(
        completion_strategy="AVAILABLE_NOW",
        conflict_strategy="UNALLOCATED_ONLY",
        confirm=input.confirm,
        account_id=account_id,
        scope="SELECTED",
        instrument_codes=[input.stock_code],
        execution_mode="paper",
        auto_exit_authorized=False,
      ),
      account_id,
    )
    item = group.plans[0] if group.plans else None
    return PositionLiquidationResult(
      success=bool(item and item.success),
      stock_code=input.stock_code,
      volume=item.protected_volume if item else None,
      order_id=item.plan_id if item else None,
      message=group.message,
      error=item.error if item else "未创建清仓计划",
    )

  @staticmethod
  async def upsert_conditional_liquidation_order(
    input: ConditionalLiquidationOrderInput,
    account_id: str,
  ) -> ConditionalLiquidationOrder:
    if bool(input.auto_exit_authorized):
      raise AuthError(
        "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE",
        "条件退出规则不能通过布尔字段开启自动实盘，请先创建计划再精确授权",
        status_code=400,
      )
    service = LiquidationService(account_id=account_id)
    order = await service.upsert_conditional_liquidation_order(
      order_id=input.id,
      stock_code=input.stock_code,
      instrument_name=input.instrument_name,
      enabled=input.enabled,
      target_profit_pct=input.target_profit_pct,
      target_price=input.target_price,
      sell_mode=input.sell_mode,
      sell_ratio_pct=input.sell_ratio_pct,
      sell_volume=input.sell_volume,
      remark=input.remark,
      strategy=input.strategy,
      dynamic_policy=input.dynamic_policy,
      execution_mode=input.execution_mode,
      auto_exit_authorized=False,
    )
    plans = await LiquidationResolver._exit_plan_map([order])
    return ConditionalLiquidationOrder.from_model(
      order,
      plans.get(str(order.exit_plan_id or "")),
    )

  @staticmethod
  async def set_conditional_liquidation_order_enabled(
    order_id: str,
    enabled: bool,
    account_id: str,
  ) -> Optional[ConditionalLiquidationOrder]:
    service = LiquidationService(account_id=account_id)
    order = await service.set_conditional_liquidation_order_enabled(
      order_id,
      enabled,
    )
    if not order:
      return None
    plans = await LiquidationResolver._exit_plan_map([order])
    return ConditionalLiquidationOrder.from_model(
      order,
      plans.get(str(order.exit_plan_id or "")),
    )

  @staticmethod
  async def cancel_conditional_liquidation_order(
    order_id: str,
    account_id: str,
  ) -> Optional[ConditionalLiquidationOrder]:
    service = LiquidationService(account_id=account_id)
    order = await service.cancel_conditional_liquidation_order(order_id)
    if not order:
      return None
    plans = await LiquidationResolver._exit_plan_map([order])
    return ConditionalLiquidationOrder.from_model(
      order,
      plans.get(str(order.exit_plan_id or "")),
    )

  @staticmethod
  async def evaluate_conditional_liquidation_orders(
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
  ) -> List[ConditionalLiquidationEvaluationResult]:
    aggregate_id = account_id or "all"
    receipt = await engine_command_service.request(
      "LIQUIDATION_EVALUATE",
      {
        "account_id": account_id,
        "stock_code": stock_code,
      },
      aggregate_id=aggregate_id,
      idempotency_key=f"liquidation-evaluate:{aggregate_id}:{uuid.uuid4()}",
    )
    if receipt.status == "FAILED":
      raise RuntimeError(receipt.error or "条件清仓评估失败")
    if receipt.status != "SUCCEEDED":
      raise RuntimeError(
        f"条件清仓评估已排队但 Engine 尚未确认: {receipt.message_id}"
      )
    results = list((receipt.result or {}).get("items") or [])
    raw_orders = [SimpleNamespace(**dict(item.get("order") or {})) for item in results]
    plans = await LiquidationResolver._exit_plan_map(raw_orders)
    return [
      LiquidationResolver._conditional_evaluation_result(
        item,
        plans.get(str((item.get("order") or {}).get("exit_plan_id") or "")),
      )
      for item in results
    ]

  @staticmethod
  async def redeem_cleared_position(
    input: RedeemPositionInput,
    account_id: str,
  ) -> RedemptionResult:
    """已清仓股票资金赎回"""
    liquidation_service = LiquidationService(account_id=account_id)

    # 执行资金赎回
    result_data = await liquidation_service.redeem_cleared_position(
      stock_code=input.stock_code, amount=input.amount
    )

    return RedemptionResult(
      success=result_data["success"],
      stock_code=result_data["stock_code"],
      redeemed_amount=result_data.get("redeemed_amount"),
      remaining_amount=result_data.get("remaining_amount"),
      message=result_data["message"],
      error=result_data.get("error"),
    )

  @staticmethod
  async def get_liquidation_orders(
    account_id: str, limit: int = 20, offset: int = 0
  ) -> List[LiquidationOrder]:
    """获取清仓订单列表"""
    safe_limit = max(1, min(int(limit or 20), 200))
    safe_offset = max(0, int(offset or 0))
    async for db in get_async_db():
      result = await db.execute(
        select(LiquidationOrderModel)
        .filter(LiquidationOrderModel.account_id == account_id)
        .order_by(desc(LiquidationOrderModel.created_at))
        .limit(safe_limit)
        .offset(safe_offset)
      )
      return [
        LiquidationResolver._liquidation_order(model)
        for model in result.scalars().all()
      ]
    return []

  @staticmethod
  async def get_liquidation_order(
    order_id: str, account_id: str
  ) -> Optional[LiquidationOrder]:
    """获取单个清仓订单"""
    async for db in get_async_db():
      result = await db.execute(
        select(LiquidationOrderModel).filter(
          LiquidationOrderModel.id == order_id,
          LiquidationOrderModel.account_id == account_id,
        )
      )
      model = result.scalar_one_or_none()
      return LiquidationResolver._liquidation_order(model) if model else None
    return None

  @staticmethod
  async def get_redemption_records(
    account_id: str, stock_code: Optional[str] = None, limit: int = 20, offset: int = 0
  ) -> List[RedemptionRecord]:
    """获取赎回记录列表"""
    safe_limit = max(1, min(int(limit or 20), 200))
    safe_offset = max(0, int(offset or 0))
    statement = select(RedemptionRecordModel).filter(
      RedemptionRecordModel.account_id == account_id
    )
    if stock_code:
      statement = statement.filter(
        RedemptionRecordModel.stock_code == stock_code.strip().upper()
      )
    statement = (
      statement.order_by(desc(RedemptionRecordModel.created_at))
      .limit(safe_limit)
      .offset(safe_offset)
    )
    async for db in get_async_db():
      result = await db.execute(statement)
      return [
        LiquidationResolver._redemption_record(model)
        for model in result.scalars().all()
      ]
    return []

  @staticmethod
  async def conditional_order_account_id(order_id: str) -> Optional[str]:
    async for db in get_async_db():
      result = await db.execute(
        select(ConditionalLiquidationOrderModel.account_id).filter(
          ConditionalLiquidationOrderModel.id == order_id
        )
      )
      return result.scalar_one_or_none()
    return None

  @staticmethod
  async def cancel_liquidation_order(
    order_id: str,
    account_id: str,
  ) -> MessageResponse:
    """取消清仓订单"""
    async for db in get_async_db():
      result = await db.execute(
        select(LiquidationOrderModel)
        .filter(
          LiquidationOrderModel.id == order_id,
          LiquidationOrderModel.account_id == account_id,
        )
        .with_for_update()
      )
      order = result.scalar_one_or_none()
      if order is None:
        return MessageResponse(success=False, message="清仓订单不存在")
      if order.status != LiquidationStatus.PENDING:
        return MessageResponse(
          success=False,
          message=f"状态为 {order.status} 的清仓订单不可取消",
        )
      order.status = LiquidationStatus.CANCELLED
      order.end_time = datetime.now()
      await db.commit()
      return MessageResponse(success=True, message=f"清仓订单 {order_id} 已取消")
    return MessageResponse(success=False, message="数据库连接不可用")
