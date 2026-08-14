"""Authenticated GraphQL API for durable AI assistant conversations."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional

import strawberry
from graphql import GraphQLError
from quantx_domain.clock import utcnow
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.repositories.ai_assistant_repository import (
  AiAssistantRepository,
  AssistantRunAlreadyActiveError,
)
from quantx_infrastructure.services.ai_assistant_event_bus import (
  ai_assistant_event_channel,
  notify_ai_assistant_event,
  notify_ai_assistant_run,
)

from ..security import principal_from_context
from ..types.ai_assistant_types import (
  AiAssistantApprovalDecision,
  AiAssistantCapabilities,
  AiAssistantEvent,
  AiAssistantMessage,
  AiAssistantMessagePage,
  AiAssistantRun,
  AiAssistantThread,
  AiAssistantThreadConnection,
  AiAssistantThreadEdge,
  AiAssistantToolCapability,
  CreateAiAssistantThreadInput,
  ResolveAiAssistantApprovalInput,
  SendAiAssistantMessageInput,
  UpdateAiAssistantThreadInput,
)

logger = logging.getLogger(__name__)
MAX_MESSAGE_LENGTH = 12_000
MAX_CONTEXT_REFS = 8
ALLOWED_CONTEXT_KINDS = frozenset(
  {
    "ROUTE",
    "INSTRUMENT",
    "STRATEGY_RUN",
    "RESEARCH_RUN",
    "PORTFOLIO_ACCOUNT",
    "SCREENING_RESULT",
  }
)


def _error(code: str, message: str, *, retryable: bool = False) -> GraphQLError:
  return GraphQLError(
    message,
    extensions={"code": code, "retryable": retryable},
  )


def _encode_cursor(activity_at: datetime, thread_id: str) -> str:
  raw = json.dumps([activity_at.isoformat(), thread_id], separators=(",", ":"))
  return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: Optional[str]) -> tuple[Optional[datetime], Optional[str]]:
  if not cursor:
    return None, None
  try:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    timestamp, thread_id = json.loads(raw)
    return datetime.fromisoformat(str(timestamp)), str(thread_id)
  except (ValueError, TypeError, json.JSONDecodeError):
    raise _error("AI_INVALID_CURSOR", "对话分页游标无效") from None


def _validate_thread_account(principal, thread) -> None:
  if thread.account_id:
    principal.require_account(thread.account_id)


def _assistant_configured() -> bool:
  return bool(settings.ai_assistant_enabled and settings.openai_api_key.strip())


def _require_assistant_configured() -> None:
  if not _assistant_configured():
    raise _error(
      "AI_ASSISTANT_UNAVAILABLE",
      "AI Assistant 尚未配置，QuantX 其他功能不受影响",
      retryable=False,
    )


def _run_payload(run) -> dict:
  return {
    "id": run.id,
    "threadId": run.thread_id,
    "status": run.status,
    "model": run.model,
    "errorCode": run.error_code,
    "errorMessage": run.error_message,
    "inputTokens": int(run.input_tokens or 0),
    "outputTokens": int(run.output_tokens or 0),
    "requestCount": int(run.request_count or 0),
    "toolCallCount": int(run.tool_call_count or 0),
    "createdAt": run.created_at.isoformat(),
    "startedAt": run.started_at.isoformat() if run.started_at else None,
    "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
  }


def _context_refs(input: SendAiAssistantMessageInput) -> list[dict[str, str]]:
  total_ref_count = len(input.context_refs) + int(input.route_context is not None)
  if total_ref_count > MAX_CONTEXT_REFS:
    raise _error("AI_TOO_MANY_CONTEXT_REFS", "一次最多附加 8 个页面上下文")
  refs: list[dict[str, str]] = []
  if input.route_context is not None:
    path = input.route_context.path.strip()
    if not path.startswith("/") or len(path) > 512:
      raise _error("AI_INVALID_ROUTE_CONTEXT", "页面路由上下文无效")
    refs.append(
      {
        "kind": "ROUTE",
        "objectId": path,
        "label": str(input.route_context.object_type or "当前页面")[:120],
      }
    )
  for item in input.context_refs:
    kind = item.kind.strip().upper()
    object_id = item.object_id.strip()
    if kind not in ALLOWED_CONTEXT_KINDS or not object_id or len(object_id) > 160:
      raise _error("AI_INVALID_CONTEXT_REF", "页面上下文引用无效")
    refs.append(
      {
        "kind": kind,
        "objectId": object_id,
        "label": str(item.label or "")[:120],
      }
    )
  return refs


async def _safe_notify_run(run_id: str) -> None:
  try:
    await notify_ai_assistant_run(run_id)
  except Exception as exc:
    logger.warning("AI assistant Redis wake-up failed: %s", exc.__class__.__name__)


async def _safe_notify_event(thread_id: str, sequence: int) -> None:
  try:
    await notify_ai_assistant_event(thread_id=thread_id, sequence=sequence)
  except Exception as exc:
    logger.warning("AI assistant event wake-up failed: %s", exc.__class__.__name__)


@strawberry.type(description="产品内 AI Assistant 查询")
class AiAssistantQuery:
  @strawberry.field
  async def ai_assistant_capabilities(
    self,
    info: strawberry.types.Info,
  ) -> AiAssistantCapabilities:
    principal_from_context(info.context)
    status = "unconfigured"
    async with AsyncSessionLocal() as db:
      heartbeat = await db.get(RuntimeComponentHeartbeat, "ai-runtime")
      if heartbeat is not None:
        status = str(heartbeat.status or "unavailable").lower()
        age_seconds = (utcnow() - heartbeat.updated_at).total_seconds()
        if age_seconds > 45:
          status = "unavailable"
    enabled = _assistant_configured()
    return AiAssistantCapabilities(
      enabled=enabled,
      runtime_status=status,
      model=settings.quantx_ai_model,
      external_search_available=enabled,
      agents=["research_assistant"],
      tools=[
        AiAssistantToolCapability(
          name="get_instrument_snapshot",
          description="读取标的基础资料与最新快照",
          risk_level="READ",
          approval_required=False,
        ),
        AiAssistantToolCapability(
          name="get_portfolio_summary",
          description="读取显式附加账户的持仓汇总",
          risk_level="READ",
          approval_required=False,
        ),
        AiAssistantToolCapability(
          name="get_backtest_summary",
          description="读取回测版本与核心指标",
          risk_level="READ",
          approval_required=False,
        ),
        AiAssistantToolCapability(
          name="create_backtest_rerun_task",
          description="经逐次批准创建非实盘回测重跑任务",
          risk_level="NON_TRADING_WRITE",
          approval_required=True,
        ),
      ],
      max_message_length=MAX_MESSAGE_LENGTH,
      max_context_refs=MAX_CONTEXT_REFS,
      max_concurrent_runs=settings.ai_assistant_max_concurrent_runs,
    )

  @strawberry.field
  async def ai_assistant_threads(
    self,
    info: strawberry.types.Info,
    first: int = 30,
    after: Optional[str] = None,
  ) -> AiAssistantThreadConnection:
    principal = principal_from_context(info.context)
    before_activity, before_id = _decode_cursor(after)
    page_size = max(1, min(first, 100))
    async with AsyncSessionLocal() as db:
      rows = await AiAssistantRepository(db).list_threads(
        user_id=principal.user_id,
        authorized_account_ids=tuple(principal.authorized_account_ids),
        limit=page_size,
        before_activity_at=before_activity,
        before_id=before_id,
      )
    has_next = len(rows) > page_size
    items = rows[:page_size]
    edges = [
      AiAssistantThreadEdge(
        cursor=_encode_cursor(row.last_activity_at, row.id),
        node=AiAssistantThread.from_model(row),
      )
      for row in items
    ]
    return AiAssistantThreadConnection(
      edges=edges,
      end_cursor=edges[-1].cursor if edges else None,
      has_next_page=has_next,
    )

  @strawberry.field
  async def ai_assistant_thread(
    self,
    info: strawberry.types.Info,
    id: strawberry.ID,
  ) -> Optional[AiAssistantThread]:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      thread = await AiAssistantRepository(db).get_thread(
        str(id), user_id=principal.user_id
      )
      if thread is None:
        return None
      _validate_thread_account(principal, thread)
      return AiAssistantThread.from_model(thread)

  @strawberry.field
  async def ai_assistant_messages(
    self,
    info: strawberry.types.Info,
    thread_id: strawberry.ID,
    after_sequence: int = 0,
    limit: int = 100,
  ) -> AiAssistantMessagePage:
    principal = principal_from_context(info.context)
    page_size = max(1, min(limit, 200))
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      thread = await repository.get_thread(str(thread_id), user_id=principal.user_id)
      if thread is None:
        raise _error("AI_THREAD_NOT_FOUND", "对话不存在")
      _validate_thread_account(principal, thread)
      rows = await repository.list_messages(
        thread.id,
        after_sequence=after_sequence,
        limit=page_size,
      )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    return AiAssistantMessagePage(
      items=[AiAssistantMessage.from_model(row) for row in items],
      next_sequence=int(items[-1].sequence) if has_more and items else None,
      has_more=has_more,
    )


@strawberry.type(description="产品内 AI Assistant 变更")
class AiAssistantMutation:
  @strawberry.mutation
  async def create_ai_assistant_thread(
    self,
    info: strawberry.types.Info,
    input: CreateAiAssistantThreadInput,
  ) -> AiAssistantThread:
    principal = principal_from_context(info.context)
    if input.agent_id != "research_assistant":
      raise _error("AI_AGENT_NOT_AVAILABLE", "该 Agent 尚未开放")
    account_id = input.account_id.strip() if input.account_id else None
    if account_id:
      principal.require_account(account_id)
    async with AsyncSessionLocal() as db:
      thread = await AiAssistantRepository(db).create_thread(
        user_id=principal.user_id,
        account_id=account_id,
        agent_id=input.agent_id,
        title=input.title or "新对话",
      )
      return AiAssistantThread.from_model(thread)

  @strawberry.mutation
  async def send_ai_assistant_message(
    self,
    info: strawberry.types.Info,
    input: SendAiAssistantMessageInput,
  ) -> AiAssistantRun:
    _require_assistant_configured()
    principal = principal_from_context(info.context)
    text = input.text.strip()
    client_message_id = input.client_message_id.strip()
    if not text or len(text) > MAX_MESSAGE_LENGTH:
      raise _error("AI_INVALID_MESSAGE", "消息必须为 1 至 12000 个字符")
    if not client_message_id or len(client_message_id) > 128:
      raise _error("AI_INVALID_CLIENT_MESSAGE_ID", "消息幂等标识无效")
    refs = _context_refs(input)
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      thread = await repository.get_thread(
        str(input.thread_id), user_id=principal.user_id
      )
      if thread is None:
        raise _error("AI_THREAD_NOT_FOUND", "对话不存在")
      _validate_thread_account(principal, thread)
      attached_account_ids = [
        str(item["objectId"])
        for item in refs
        if item.get("kind") == "PORTFOLIO_ACCOUNT"
      ]
      if len(set(attached_account_ids)) > 1:
        raise _error(
          "AI_MULTIPLE_ACCOUNT_CONTEXTS",
          "一次运行只能附加一个资金账户",
        )
      attached_account_id = attached_account_ids[0] if attached_account_ids else None
      if attached_account_id:
        principal.require_account(attached_account_id)
      if (
        thread.account_id
        and attached_account_id
        and thread.account_id != attached_account_id
      ):
        raise _error(
          "AI_ACCOUNT_CONTEXT_CONFLICT",
          "附加账户与当前对话绑定账户不一致",
        )
      selected_account_id = thread.account_id or attached_account_id
      try:
        _, run, created = await repository.create_message_run(
          thread=thread,
          text=text,
          client_message_id=client_message_id,
          request_id=str(info.context.get("request_id") or "graphql"),
          model=settings.quantx_ai_model,
          context_refs=refs,
          account_id=selected_account_id,
        )
      except AssistantRunAlreadyActiveError as exc:
        raise _error(
          "AI_RUN_ALREADY_ACTIVE",
          f"当前对话已有执行中的任务：{exc}",
          retryable=True,
        ) from None
      event = None
      if created:
        event = await repository.append_event(
          thread_id=run.thread_id,
          run_id=run.id,
          event_type="RUN_STATUS_CHANGED",
          payload={"run": _run_payload(run)},
        )
    if created:
      await _safe_notify_event(run.thread_id, int(event.sequence))
      await _safe_notify_run(run.id)
    return AiAssistantRun.from_model(run)

  @strawberry.mutation
  async def cancel_ai_assistant_run(
    self,
    info: strawberry.types.Info,
    run_id: strawberry.ID,
  ) -> AiAssistantRun:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      run = await repository.get_run(str(run_id), user_id=principal.user_id)
      if run is None:
        raise _error("AI_RUN_NOT_FOUND", "AI 运行不存在")
      previous_status = run.status
      run = await repository.cancel_run(run)
      event = None
      if run.status != previous_status:
        event = await repository.append_event(
          thread_id=run.thread_id,
          run_id=run.id,
          event_type="RUN_STATUS_CHANGED",
          payload={"run": _run_payload(run)},
        )
    if event is not None:
      await _safe_notify_event(run.thread_id, int(event.sequence))
    return AiAssistantRun.from_model(run)

  @strawberry.mutation
  async def retry_ai_assistant_run(
    self,
    info: strawberry.types.Info,
    run_id: strawberry.ID,
  ) -> AiAssistantRun:
    _require_assistant_configured()
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      previous = await repository.get_run(str(run_id), user_id=principal.user_id)
      if previous is None:
        raise _error("AI_RUN_NOT_FOUND", "AI 运行不存在")
      if previous.status not in {"FAILED", "CANCELLED"}:
        raise _error("AI_RUN_NOT_RETRYABLE", "只有失败或取消的运行可以重试")
      if await repository.has_successful_non_trading_write(previous.id):
        raise _error(
          "AI_RUN_NOT_RETRYABLE_AFTER_WRITE",
          "该运行已成功创建任务，为避免重复写入不能直接重试",
        )
      try:
        run = await repository.retry_run(
          previous=previous,
          request_id=str(info.context.get("request_id") or "graphql"),
        )
      except AssistantRunAlreadyActiveError:
        raise _error("AI_RUN_ALREADY_ACTIVE", "当前对话已有执行中的任务") from None
      event = await repository.append_event(
        thread_id=run.thread_id,
        run_id=run.id,
        event_type="RUN_STATUS_CHANGED",
        payload={"run": _run_payload(run)},
      )
    await _safe_notify_event(run.thread_id, int(event.sequence))
    await _safe_notify_run(run.id)
    return AiAssistantRun.from_model(run)

  @strawberry.mutation
  async def resolve_ai_assistant_approval(
    self,
    info: strawberry.types.Info,
    input: ResolveAiAssistantApprovalInput,
  ) -> AiAssistantRun:
    _require_assistant_configured()
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      run = await repository.get_run(str(input.run_id), user_id=principal.user_id)
      if run is None:
        raise _error("AI_RUN_NOT_FOUND", "AI 运行不存在")
      try:
        call = await repository.resolve_approval(
          run=run,
          tool_call_id=str(input.tool_call_id),
          approved=input.decision is AiAssistantApprovalDecision.APPROVE,
        )
      except ValueError as exc:
        raise _error("AI_APPROVAL_NOT_PENDING", str(exc)) from None
      events = [
        await repository.append_event(
          thread_id=run.thread_id,
          run_id=run.id,
          event_type="TOOL_CALL_COMPLETED",
          payload={
            "toolCallId": call.id,
            "toolName": call.tool_name,
            "toolStatus": call.status,
            "toolSummary": "用户已批准" if call.status == "APPROVED" else "用户已拒绝",
          },
        )
      ]
      if run.status == "QUEUED":
        events.append(
          await repository.append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="RUN_STATUS_CHANGED",
            payload={"run": _run_payload(run)},
          )
        )
    for event in events:
      await _safe_notify_event(run.thread_id, int(event.sequence))
    if run.status == "QUEUED":
      await _safe_notify_run(run.id)
    return AiAssistantRun.from_model(run)

  @strawberry.mutation
  async def update_ai_assistant_thread(
    self,
    info: strawberry.types.Info,
    input: UpdateAiAssistantThreadInput,
  ) -> AiAssistantThread:
    principal = principal_from_context(info.context)
    if input.title is not None and len(input.title.strip()) > 160:
      raise _error("AI_INVALID_THREAD_TITLE", "对话标题不能超过 160 个字符")
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      thread = await repository.get_thread(
        str(input.thread_id), user_id=principal.user_id
      )
      if thread is None:
        raise _error("AI_THREAD_NOT_FOUND", "对话不存在")
      _validate_thread_account(principal, thread)
      thread = await repository.update_thread(
        thread,
        title=input.title,
        external_search_enabled=input.external_search_enabled,
      )
      return AiAssistantThread.from_model(thread)

  @strawberry.mutation
  async def delete_ai_assistant_thread(
    self,
    info: strawberry.types.Info,
    thread_id: strawberry.ID,
  ) -> bool:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      thread = await repository.get_thread(str(thread_id), user_id=principal.user_id)
      if thread is None:
        return False
      _validate_thread_account(principal, thread)
      try:
        await repository.delete_thread(
          thread,
          request_id=str(info.context.get("request_id") or "graphql"),
        )
      except AssistantRunAlreadyActiveError:
        raise _error("AI_RUN_ALREADY_ACTIVE", "请先取消当前运行再删除对话")
      return True


@strawberry.type(description="产品内 AI Assistant 实时事件")
class AiAssistantSubscription:
  @strawberry.subscription
  async def ai_assistant_events(
    self,
    info: strawberry.types.Info,
    thread_id: strawberry.ID,
    after_sequence: int = 0,
  ) -> AsyncGenerator[AiAssistantEvent, None]:
    principal = principal_from_context(info.context)
    normalized_thread_id = str(thread_id)
    async with AsyncSessionLocal() as db:
      thread = await AiAssistantRepository(db).get_thread(
        normalized_thread_id,
        user_id=principal.user_id,
      )
      if thread is None:
        raise _error("AI_THREAD_NOT_FOUND", "对话不存在")
      _validate_thread_account(principal, thread)

    subscription = await redis_pubsub.open_subscription(
      ai_assistant_event_channel(normalized_thread_id)
    )
    cursor = max(0, after_sequence)
    try:
      while True:
        async with AsyncSessionLocal() as db:
          events = await AiAssistantRepository(db).list_events(
            normalized_thread_id,
            after_sequence=cursor,
          )
        for event in events:
          cursor = int(event.sequence)
          yield AiAssistantEvent.from_model(event)
        await subscription.wait_for_message(timeout=1.0)
        await asyncio.sleep(0)
    finally:
      await subscription.close()
