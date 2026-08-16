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
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.repositories.ai_assistant_repository import (
  AiAssistantRepository,
  AssistantRunAlreadyActiveError,
)
from quantx_infrastructure.repositories.ai_runtime_settings_repository import (
  AiRuntimeEditableValues,
  AiRuntimeSettingsRepository,
  AiRuntimeSettingsVersionConflict,
  EffectiveAiRuntimeSettings,
)
from quantx_infrastructure.services.ai_assistant_event_bus import (
  ai_assistant_event_channel,
  notify_ai_assistant_event,
  notify_ai_assistant_run,
)
from quantx_infrastructure.services.ai_runtime_settings_event_bus import (
  notify_ai_runtime_settings,
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
from ..types.ai_runtime_settings_types import (
  AiRuntimeApplyState,
  AiRuntimeSettings,
  AiRuntimeSettingsSource,
  AiRuntimeStatus,
  UpdateAiRuntimeSettingsInput,
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
    "LIMIT_UP_CANDIDATE",
    "LIMIT_UP_RESEARCH",
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


def _assistant_configured(config: EffectiveAiRuntimeSettings) -> bool:
  return bool(config.values.enabled and config.api_key_configured)


def _require_assistant_configured(config: EffectiveAiRuntimeSettings) -> None:
  if not _assistant_configured(config):
    raise _error(
      "AI_ASSISTANT_UNAVAILABLE",
      "AI Assistant 尚未配置，QuantX 其他功能不受影响",
      retryable=False,
    )


async def _runtime_settings_view(
  db,
  config: EffectiveAiRuntimeSettings,
) -> AiRuntimeSettings:
  heartbeat = await db.get(RuntimeComponentHeartbeat, "ai-runtime")
  applied_version: int | None = None
  runtime_status = AiRuntimeStatus.OFFLINE
  apply_state = AiRuntimeApplyState.OFFLINE
  if heartbeat is not None:
    age_seconds = (utcnow() - heartbeat.updated_at).total_seconds()
    if age_seconds <= 45:
      details = dict(heartbeat.details or {})
      raw_version = details.get("configVersion")
      if raw_version is not None:
        try:
          applied_version = max(0, int(raw_version))
        except (TypeError, ValueError):
          applied_version = None
      raw_status = str(heartbeat.status or "unavailable").upper()
      runtime_status = {
        "READY": AiRuntimeStatus.READY,
        "DISABLED": AiRuntimeStatus.DISABLED,
        "UNCONFIGURED": AiRuntimeStatus.UNCONFIGURED,
        "UNAVAILABLE": AiRuntimeStatus.UNAVAILABLE,
      }.get(raw_status, AiRuntimeStatus.UNAVAILABLE)
      apply_state = (
        AiRuntimeApplyState.APPLIED
        if applied_version == config.version
        else AiRuntimeApplyState.PENDING
      )
  return AiRuntimeSettings(
    version=config.version,
    source=AiRuntimeSettingsSource(config.source),
    enabled=config.values.enabled,
    api_key_configured=config.api_key_configured,
    model=config.values.model,
    max_concurrent_runs=config.values.max_concurrent_runs,
    max_turns=config.values.max_turns,
    max_tool_calls=config.values.max_tool_calls,
    run_timeout_seconds=config.values.run_timeout_seconds,
    tracing_enabled=config.tracing_enabled,
    lease_seconds=config.lease_seconds,
    runtime_status=runtime_status,
    applied_version=applied_version,
    apply_state=apply_state,
    updated_at=config.updated_at,
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


async def _safe_notify_runtime_settings(version: int) -> None:
  try:
    await notify_ai_runtime_settings(version)
  except Exception as exc:
    logger.warning(
      "AI Runtime settings wake-up failed: %s",
      exc.__class__.__name__,
    )


async def _safe_notify_event(thread_id: str, sequence: int) -> None:
  try:
    await notify_ai_assistant_event(thread_id=thread_id, sequence=sequence)
  except Exception as exc:
    logger.warning("AI assistant event wake-up failed: %s", exc.__class__.__name__)


@strawberry.type(description="产品内 AI Assistant 查询")
class AiAssistantQuery:
  @strawberry.field(description="AI Runtime 全局非敏感配置与应用状态")
  async def ai_runtime_settings(
    self,
    info: strawberry.types.Info,
  ) -> AiRuntimeSettings:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      config = await AiRuntimeSettingsRepository(db).get_effective()
      return await _runtime_settings_view(db, config)

  @strawberry.field(description="读取当前 AI Assistant 能力、模型与工具风险声明")
  async def ai_assistant_capabilities(
    self,
    info: strawberry.types.Info,
  ) -> AiAssistantCapabilities:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      config = await AiRuntimeSettingsRepository(db).get_effective()
      runtime_view = await _runtime_settings_view(db, config)
    enabled = _assistant_configured(config)
    return AiAssistantCapabilities(
      enabled=enabled,
      runtime_status=runtime_view.runtime_status.value.lower(),
      model=config.values.model,
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
      max_concurrent_runs=config.values.max_concurrent_runs,
    )

  @strawberry.field(description="按最近活动时间分页读取当前用户的 AI 对话")
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

  @strawberry.field(description="读取当前用户拥有的单个 AI 对话")
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

  @strawberry.field(description="按序号增量读取单个 AI 对话的消息")
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
  @strawberry.mutation(description="更新 AI Runtime 全局非敏感配置")
  async def update_ai_runtime_settings(
    self,
    info: strawberry.types.Info,
    input: UpdateAiRuntimeSettingsInput,
  ) -> AiRuntimeSettings:
    principal = principal_from_context(info.context)
    model = input.model.strip()
    if input.expected_version < 0:
      raise _error("AI_RUNTIME_INVALID_VERSION", "配置版本不能小于 0")
    if not model or len(model) > 120:
      raise _error("AI_RUNTIME_INVALID_MODEL", "模型名称必须为 1 至 120 个字符")
    if not 1 <= input.max_concurrent_runs <= 16:
      raise _error("AI_RUNTIME_INVALID_CONCURRENCY", "最大并发必须为 1 至 16")
    if not 1 <= input.max_turns <= 64:
      raise _error("AI_RUNTIME_INVALID_TURNS", "最大轮次必须为 1 至 64")
    if not 1 <= input.max_tool_calls <= 64:
      raise _error("AI_RUNTIME_INVALID_TOOL_CALLS", "工具调用上限必须为 1 至 64")
    if not 30 <= input.run_timeout_seconds <= 3600:
      raise _error("AI_RUNTIME_INVALID_TIMEOUT", "运行超时必须为 30 至 3600 秒")
    values = AiRuntimeEditableValues(
      enabled=input.enabled,
      model=model,
      max_concurrent_runs=input.max_concurrent_runs,
      max_turns=input.max_turns,
      max_tool_calls=input.max_tool_calls,
      run_timeout_seconds=input.run_timeout_seconds,
    )
    async with AsyncSessionLocal() as db:
      repository = AiRuntimeSettingsRepository(db)
      try:
        config = await repository.update(
          expected_version=input.expected_version,
          values=values,
          user_id=principal.user_id,
          request_id=str(info.context.get("request_id") or "graphql"),
        )
      except AiRuntimeSettingsVersionConflict as exc:
        raise _error(
          "AI_RUNTIME_SETTINGS_VERSION_CONFLICT",
          f"配置已更新，请刷新后重试（当前版本 {exc.current_version}）",
          retryable=True,
        ) from None
      view = await _runtime_settings_view(db, config)
    await _safe_notify_runtime_settings(config.version)
    return view

  @strawberry.mutation(description="创建归属当前用户、可选绑定账户的 AI 对话")
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

  @strawberry.mutation(
    description="幂等写入用户消息并创建异步 AI 运行；返回运行状态而非最终回答"
  )
  async def send_ai_assistant_message(
    self,
    info: strawberry.types.Info,
    input: SendAiAssistantMessageInput,
  ) -> AiAssistantRun:
    principal = principal_from_context(info.context)
    text = input.text.strip()
    client_message_id = input.client_message_id.strip()
    if not text or len(text) > MAX_MESSAGE_LENGTH:
      raise _error("AI_INVALID_MESSAGE", "消息必须为 1 至 12000 个字符")
    if not client_message_id or len(client_message_id) > 128:
      raise _error("AI_INVALID_CLIENT_MESSAGE_ID", "消息幂等标识无效")
    refs = _context_refs(input)
    async with AsyncSessionLocal() as db:
      config = await AiRuntimeSettingsRepository(db).get_effective()
      _require_assistant_configured(config)
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
          model=config.values.model,
          context_refs=refs,
          account_id=selected_account_id,
          runtime_config_version=config.version,
          runtime_config_snapshot=config.values.to_run_snapshot(),
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

  @strawberry.mutation(description="请求取消当前用户拥有的 AI 运行")
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

  @strawberry.mutation(
    description="为失败或取消且未完成写操作的 AI 运行创建一次新重试"
  )
  async def retry_ai_assistant_run(
    self,
    info: strawberry.types.Info,
    run_id: strawberry.ID,
  ) -> AiAssistantRun:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      config = await AiRuntimeSettingsRepository(db).get_effective()
      _require_assistant_configured(config)
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
          model=config.values.model,
          runtime_config_version=config.version,
          runtime_config_snapshot=config.values.to_run_snapshot(),
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

  @strawberry.mutation(
    description="批准或拒绝 AI 运行中等待处理的非交易工具调用"
  )
  async def resolve_ai_assistant_approval(
    self,
    info: strawberry.types.Info,
    input: ResolveAiAssistantApprovalInput,
  ) -> AiAssistantRun:
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

  @strawberry.mutation(description="更新当前用户 AI 对话的标题或外部搜索偏好")
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

  @strawberry.mutation(
    description="删除当前用户拥有且没有活动运行的 AI 对话及其消息"
  )
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
  @strawberry.subscription(
    description="按持久化序号订阅单个 AI 对话的可恢复事件流"
  )
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
