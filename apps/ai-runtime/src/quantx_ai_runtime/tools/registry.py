"""Build a per-run allowlist of audited QuantX tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from agents import WebSearchTool, function_tool
from quantx_application.assistant.contracts import (
  AssistantExecutionContext,
  AssistantToolMetadata,
  AssistantToolRisk,
)
from quantx_application.assistant.policies import authorize_tool, tool_requires_approval
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import Instrument
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.models.first_board_promotion import LimitUpChainSnapshot
from quantx_infrastructure.models.stock_disclosure import StockAnnouncement
from quantx_infrastructure.repositories.account_repository import AccountRepository
from quantx_infrastructure.repositories.ai_assistant_repository import (
  AiAssistantRepository,
)
from quantx_infrastructure.repositories.backtest_repository import BacktestRepository
from quantx_infrastructure.repositories.first_board_promotion_repository import (
  FirstBoardPromotionRepository,
)
from quantx_infrastructure.repositories.position_repository import PositionRepository
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.engine_command_service import engine_command_service
from sqlalchemy import select

from quantx_ai_runtime.runtime.event_writer import AssistantEventWriter


@dataclass
class RuntimeRunContext:
  execution: AssistantExecutionContext
  tool_call_count: int = 0
  max_tool_calls: int = 8
  event_writer: AssistantEventWriter = field(default_factory=AssistantEventWriter)


def _jsonable(value: Any) -> Any:
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, (datetime,)):
    return value.isoformat()
  if isinstance(value, Decimal):
    return float(value)
  if isinstance(value, dict):
    return {str(key): _jsonable(item) for key, item in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_jsonable(item) for item in value]
  return value


def _idempotency_key(context: RuntimeRunContext, name: str, arguments: dict) -> str:
  digest = hashlib.sha256(
    json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode("utf-8")
  ).hexdigest()[:32]
  return f"ai:{context.execution.run_id}:{name}:{digest}"


def _require_persisted_approval(
  metadata: AssistantToolMetadata,
  call: Any,
) -> None:
  """Fail closed even if a provider accidentally skips its HITL interrupt."""
  if not tool_requires_approval(metadata):
    return
  if call is None or call.approval_status != "APPROVED":
    raise PermissionError("AI_TOOL_APPROVAL_REQUIRED")


async def _invoke_audited(
  context: RuntimeRunContext,
  metadata: AssistantToolMetadata,
  arguments: dict[str, Any],
  callback: Callable[[], Awaitable[dict[str, Any]]],
) -> str:
  authorize_tool(context.execution, metadata)
  context.tool_call_count += 1
  if context.tool_call_count > context.max_tool_calls:
    raise RuntimeError("AI_TOOL_CALL_LIMIT_EXCEEDED")

  idempotency_key = (
    _idempotency_key(context, metadata.name, arguments)
    if metadata.risk_level is AssistantToolRisk.NON_TRADING_WRITE
    else None
  )
  async with AsyncSessionLocal() as db:
    repository = AiAssistantRepository(db)
    call = (
      await repository.get_tool_call_by_idempotency(idempotency_key)
      if idempotency_key
      else None
    )
    _require_persisted_approval(metadata, call)
    if call is not None:
      if call.status == "SUCCEEDED" and call.result is not None:
        return json.dumps(call.result, ensure_ascii=False, default=str)
      call = await repository.mark_tool_call_running(call)
    else:
      call = await repository.create_tool_call(
        run_id=context.execution.run_id,
        call_id=f"runtime:{uuid.uuid4()}",
        tool_name=metadata.name,
        tool_version=metadata.version,
        risk_level=metadata.risk_level.value,
        arguments=arguments,
        approval_required=False,
        idempotency_key=idempotency_key,
      )

  await context.event_writer.append(
    thread_id=context.execution.thread_id,
    run_id=context.execution.run_id,
    event_type="TOOL_CALL_STARTED",
    payload={
      "toolCallId": call.id,
      "toolName": metadata.name,
      "toolStatus": "RUNNING",
    },
  )
  try:
    async with asyncio.timeout(metadata.timeout_seconds):
      result = _jsonable(await callback())
    summary = str(result.get("summary") or f"{metadata.name} 已完成")[:512]
    async with AsyncSessionLocal() as db:
      call = await AiAssistantRepository(db).finish_tool_call(
        await db.merge(call),
        status="SUCCEEDED",
        result=result,
        summary=summary,
      )
    await context.event_writer.append(
      thread_id=context.execution.thread_id,
      run_id=context.execution.run_id,
      event_type="TOOL_CALL_COMPLETED",
      payload={
        "toolCallId": call.id,
        "toolName": metadata.name,
        "toolStatus": "SUCCEEDED",
        "toolSummary": summary,
      },
    )
    return json.dumps(result, ensure_ascii=False, default=str)
  except Exception as exc:
    async with AsyncSessionLocal() as db:
      call = await AiAssistantRepository(db).finish_tool_call(
        await db.merge(call),
        status="FAILED",
        error_code=exc.__class__.__name__,
        error_message="工具执行失败",
      )
    await context.event_writer.append(
      thread_id=context.execution.thread_id,
      run_id=context.execution.run_id,
      event_type="TOOL_CALL_COMPLETED",
      payload={
        "toolCallId": call.id,
        "toolName": metadata.name,
        "toolStatus": "FAILED",
      },
    )
    raise


def build_tools(
  context: RuntimeRunContext, *, allowed_names: Optional[frozenset[str]] = None
) -> list[Any]:
  instrument_metadata = AssistantToolMetadata(
    name="get_instrument_snapshot",
    version="1",
    description="读取一只沪深标的的基础资料、昨收和涨跌停价",
    risk_level=AssistantToolRisk.READ,
    required_permissions=frozenset({"market:read"}),
  )

  @function_tool(name_override=instrument_metadata.name)
  async def get_instrument_snapshot(code: str) -> str:
    """读取一只沪深标的的基础资料。code 必须类似 600000.SH。"""
    normalized = code.strip().upper()

    async def query() -> dict[str, Any]:
      async with AsyncSessionLocal() as db:
        instrument = await db.get(Instrument, normalized)
        if instrument is None:
          raise ValueError("INSTRUMENT_NOT_FOUND")
        return {
          "summary": f"{normalized} {instrument.name or ''}".strip(),
          "code": normalized,
          "name": instrument.name,
          "market": instrument.market,
          "instrumentType": getattr(instrument.type, "value", instrument.type),
          "preClose": instrument.pre_close,
          "upStopPrice": instrument.up_stop_price,
          "downStopPrice": instrument.down_stop_price,
          "isTrading": instrument.is_trading,
          "updatedAt": instrument.updated_at,
        }

    return await _invoke_audited(
      context, instrument_metadata, {"code": normalized}, query
    )

  portfolio_metadata = AssistantToolMetadata(
    name="get_portfolio_summary",
    version="1",
    description="读取本次显式授权账户的资产与持仓汇总",
    risk_level=AssistantToolRisk.READ,
    required_permissions=frozenset({"portfolio:read"}),
    account_scoped=True,
    external_data_classification="SENSITIVE_FINANCIAL",
  )

  @function_tool(name_override=portfolio_metadata.name)
  async def get_portfolio_summary() -> str:
    """读取已附加账户的资产及按市值排序的前 50 个持仓。"""
    account_id = context.execution.require_account()

    async def query() -> dict[str, Any]:
      async with AsyncSessionLocal() as db:
        account = await AccountRepository(db).find_by_account_id(account_id)
        if account is None:
          raise ValueError("ACCOUNT_SNAPSHOT_NOT_FOUND")
        positions = await PositionRepository(db).find_all(account_id=account_id)
        positions.sort(key=lambda item: float(item.market_value or 0), reverse=True)
        return {
          "summary": f"账户资产快照包含 {len(positions)} 个持仓",
          "account": account.to_dict(),
          "positions": [
            {"stockCode": item.stock_code, **item.to_dict()} for item in positions[:50]
          ],
          "positionCount": len(positions),
        }

    return await _invoke_audited(context, portfolio_metadata, {}, query)

  backtest_metadata = AssistantToolMetadata(
    name="get_backtest_summary",
    version="1",
    description="读取当前用户策略运行下的回测版本和核心指标",
    risk_level=AssistantToolRisk.READ,
    required_permissions=frozenset({"strategy:read"}),
  )

  @function_tool(name_override=backtest_metadata.name)
  async def get_backtest_summary(strategy_run_id: str) -> str:
    """按 StrategyRun ID 读取该用户的所有回测版本。"""

    async def query() -> dict[str, Any]:
      async with AsyncSessionLocal() as db:
        strategy_run = await StrategyRunRepository(db).find_run_by_id(strategy_run_id)
        if strategy_run is None or strategy_run.user_id != context.execution.user_id:
          raise ValueError("STRATEGY_RUN_NOT_FOUND")
        versions = await BacktestRepository(db).get_backtests_by_run(strategy_run_id)
        return {
          "summary": f"回测实例包含 {len(versions)} 个版本",
          "strategyRunId": strategy_run_id,
          "versions": [item.to_dict() for item in versions[:20]],
        }

    return await _invoke_audited(
      context,
      backtest_metadata,
      {"strategyRunId": strategy_run_id},
      query,
    )

  rerun_metadata = AssistantToolMetadata(
    name="create_backtest_rerun_task",
    version="1",
    description="创建一个新的非实盘回测版本，始终需要用户逐次批准",
    risk_level=AssistantToolRisk.NON_TRADING_WRITE,
    required_permissions=frozenset({"assistant:write", "strategy:read"}),
    idempotent=True,
  )

  @function_tool(name_override=rerun_metadata.name, needs_approval=True)
  async def create_backtest_rerun_task(
    strategy_run_id: str,
    backtest_start_time: Optional[str] = None,
    backtest_end_time: Optional[str] = None,
  ) -> str:
    """经批准为回测模式 StrategyRun 创建新版本。时间使用 ISO-8601。"""
    arguments = {
      "strategyRunId": strategy_run_id,
      "backtestStartTime": backtest_start_time,
      "backtestEndTime": backtest_end_time,
    }

    async def command() -> dict[str, Any]:
      async with AsyncSessionLocal() as db:
        strategy_run = await StrategyRunRepository(db).find_run_by_id(strategy_run_id)
        if strategy_run is None or strategy_run.user_id != context.execution.user_id:
          raise ValueError("STRATEGY_RUN_NOT_FOUND")
        if strategy_run.mode != StrategyRunMode.BACKTEST:
          raise ValueError("ONLY_BACKTEST_RUN_CAN_BE_RERUN")
      for value in (backtest_start_time, backtest_end_time):
        if value:
          datetime.fromisoformat(value.replace("Z", "+00:00"))
      key = _idempotency_key(context, rerun_metadata.name, arguments)
      backtest_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
      receipt = await engine_command_service.enqueue(
        "STRATEGY_RERUN_BACKTEST",
        {
          "run_id": strategy_run_id,
          "backtest_id": backtest_id,
          "backtest_start_time": backtest_start_time,
          "backtest_end_time": backtest_end_time,
        },
        aggregate_id=strategy_run_id,
        idempotency_key=f"assistant:{key}",
      )
      return {
        "summary": "回测重跑任务已提交给 Engine",
        "taskKind": "BACKTEST_RERUN",
        "referenceId": backtest_id,
        "engineCommandId": receipt.message_id,
        "status": receipt.status,
      }

    return await _invoke_audited(context, rerun_metadata, arguments, command)

  candidate_metadata = AssistantToolMetadata(
    name="get_limit_up_candidate_snapshot",
    version="1",
    description="读取首板晋级候选的最新不可变快照与确定性评估",
    risk_level=AssistantToolRisk.READ,
    required_permissions=frozenset({"market:read"}),
  )

  @function_tool(name_override=candidate_metadata.name)
  async def get_limit_up_candidate_snapshot(code: str) -> str:
    """读取当日一只股票最新的首板候选快照，不包含交易建议。"""
    normalized = code.strip().upper()

    async def query() -> dict[str, Any]:
      trade_date = time_utils.to_shanghai(time_utils.now()).date()
      async with AsyncSessionLocal() as db:
        rows = await FirstBoardPromotionRepository(db).latest_assessments(
          trade_date, limit=500
        )
        assessment = next(
          (item for item in rows if item.instrument_code == normalized), None
        )
        if assessment is None:
          raise ValueError("LIMIT_UP_CANDIDATE_NOT_FOUND")
        return {
          "summary": f"{normalized} 首板候选确定性快照",
          "assessmentId": assessment.id,
          "code": normalized,
          "asOf": assessment.as_of,
          "modelVersion": assessment.model_version,
          "exitPolicyVersion": assessment.exit_policy_version,
          "eligible": assessment.eligible,
          "vetoReasons": assessment.veto_reasons,
          "rankScore": assessment.rank_score,
          "firstBoardCloseProbability": assessment.first_board_close_probability,
          "nextDayLimitTouchProbability": assessment.next_day_limit_touch_probability,
          "nextDayLimitSealProbability": assessment.next_day_limit_seal_probability,
          "expectedNetReturnPct": assessment.expected_net_return_pct,
          "cvar95LossPct": assessment.cvar95_loss_pct,
          "highPositionType": assessment.high_position_type,
          "snapshot": assessment.payload,
        }

    return await _invoke_audited(
      context, candidate_metadata, {"code": normalized}, query
    )

  chain_metadata = AssistantToolMetadata(
    name="get_limit_up_chain_summary",
    version="1",
    description="读取当日最新连板梯队、市场高度和炸板率快照",
    risk_level=AssistantToolRisk.READ,
    required_permissions=frozenset({"market:read"}),
  )

  @function_tool(name_override=chain_metadata.name)
  async def get_limit_up_chain_summary() -> str:
    """读取当日最新的市场级连板梯队事实。"""

    async def query() -> dict[str, Any]:
      trade_date = time_utils.to_shanghai(time_utils.now()).date()
      async with AsyncSessionLocal() as db:
        row = (
          await db.execute(
            select(LimitUpChainSnapshot)
            .where(LimitUpChainSnapshot.trade_date == trade_date)
            .order_by(LimitUpChainSnapshot.as_of.desc())
            .limit(1)
          )
        ).scalar_one_or_none()
        if row is None:
          raise ValueError("LIMIT_UP_CHAIN_NOT_FOUND")
        return {
          "summary": "当日连板梯队市场快照",
          "snapshotVersion": row.snapshot_version,
          "asOf": row.as_of,
          **dict(row.payload or {}),
        }

    return await _invoke_audited(context, chain_metadata, {}, query)

  announcement_metadata = AssistantToolMetadata(
    name="get_stock_announcement_summary",
    version="1",
    description="读取已持久化的交易所或巨潮等公告正文与来源，不访问外部网页",
    risk_level=AssistantToolRisk.READ,
    required_permissions=frozenset({"market:read"}),
  )

  @function_tool(name_override=announcement_metadata.name)
  async def get_stock_announcement_summary(code: str) -> str:
    """读取一只股票最近 20 条已持久化公告及数据缺口。"""
    normalized = code.strip().upper()

    async def query() -> dict[str, Any]:
      async with AsyncSessionLocal() as db:
        rows = (
          await db.execute(
            select(StockAnnouncement)
            .where(
              StockAnnouncement.stock_code == normalized,
              StockAnnouncement.source_authority.is_not(None),
            )
            .order_by(StockAnnouncement.announce_date.desc())
            .limit(20)
          )
        ).scalars().all()
        return {
          "summary": f"{normalized} 已持久化公告 {len(rows)} 条",
          "code": normalized,
          "announcements": [item.to_dict() for item in rows],
          "dataGaps": (
            ["NO_PERSISTED_ANNOUNCEMENT"]
            if not rows
            else [
              "ANNOUNCEMENT_CONTENT_MISSING"
              for item in rows
              if not item.content_text
            ][:1]
          ),
        }

    return await _invoke_audited(
      context, announcement_metadata, {"code": normalized}, query
    )

  tools: list[Any] = [
    get_instrument_snapshot,
    get_portfolio_summary,
    get_backtest_summary,
    create_backtest_rerun_task,
    get_limit_up_candidate_snapshot,
    get_limit_up_chain_summary,
    get_stock_announcement_summary,
  ]
  if context.execution.external_search_enabled:
    tools.append(WebSearchTool(search_context_size="medium", external_web_access=True))
  if allowed_names is not None:
    return [tool for tool in tools if str(getattr(tool, "name", "")) in allowed_names]
  return tools
