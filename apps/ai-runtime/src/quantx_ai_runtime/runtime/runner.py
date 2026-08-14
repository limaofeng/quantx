"""Execute one leased assistant run with streaming and durable recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from agents import Runner
from openai.types.responses import ResponseTextDeltaEvent
from quantx_application.assistant.contracts import (
  AssistantContextRef,
  AssistantExecutionContext,
)
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.ai_assistant import AiAssistantRun
from quantx_infrastructure.models.auth import AuthUser, AuthUserAccountAccess
from quantx_infrastructure.repositories.ai_assistant_repository import (
  AiAssistantRepository,
  AssistantRunLeaseLostError,
)
from sqlalchemy import select

from quantx_ai_runtime.agents import build_agent
from quantx_ai_runtime.config import AiRuntimeConfig
from quantx_ai_runtime.guardrails import validate_user_text
from quantx_ai_runtime.tools import RuntimeRunContext

from .event_writer import AssistantEventWriter
from .recovery import (
  deserialize_run_state,
  interruption_details,
  serialize_run_state,
  state_interruptions,
)


class AssistantRunCancelled(Exception):
  pass


def _jsonable_item(item: Any) -> dict[str, Any]:
  if isinstance(item, dict):
    return item
  model_dump = getattr(item, "model_dump", None)
  if callable(model_dump):
    return dict(model_dump(mode="json"))
  to_dict = getattr(item, "to_dict", None)
  if callable(to_dict):
    return dict(to_dict())
  raise TypeError(f"Unsupported SDK session item: {type(item).__name__}")


def _run_payload(run: AiAssistantRun) -> dict[str, Any]:
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


def _message_payload(message) -> dict[str, Any]:
  return {
    "id": message.id,
    "threadId": message.thread_id,
    "runId": message.run_id,
    "sequence": int(message.sequence),
    "role": message.role,
    "content": list(message.content_blocks or []),
    "createdAt": message.created_at.isoformat(),
  }


def _dump_value(value: Any) -> Any:
  if isinstance(value, dict):
    return {str(key): _dump_value(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_dump_value(item) for item in value]
  model_dump = getattr(value, "model_dump", None)
  if callable(model_dump):
    return _dump_value(model_dump(mode="json", exclude_none=True))
  return value


def _nested_dicts(value: Any):
  dumped = _dump_value(value)
  if isinstance(dumped, dict):
    yield dumped
    for item in dumped.values():
      yield from _nested_dicts(item)
  elif isinstance(dumped, list):
    for item in dumped:
      yield from _nested_dicts(item)


def _content_blocks(text: str, new_items: list[Any]) -> list[dict[str, Any]]:
  blocks: list[dict[str, Any]] = [{"kind": "TEXT", "text": text}]
  seen: set[str] = set()
  for item in new_items:
    raw_item = getattr(item, "raw_item", item)
    for annotation in _nested_dicts(raw_item):
      if str(annotation.get("type") or "").lower() != "url_citation":
        continue
      url = str(annotation.get("url") or "").strip()
      if not url.startswith(("http://", "https://")) or url in seen:
        continue
      seen.add(url)
      blocks.append(
        {
          "kind": "CITATION",
          "title": str(annotation.get("title") or url)[:300],
          "url": url,
          "visitedAt": utcnow().isoformat(),
        }
      )
  return blocks


def _tool_idempotency(run_id: str, tool_name: str, arguments: dict) -> str:
  digest = hashlib.sha256(
    json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode("utf-8")
  ).hexdigest()[:32]
  return f"ai:{run_id}:{tool_name}:{digest}"


async def _execution_context(run: AiAssistantRun) -> AssistantExecutionContext:
  async with AsyncSessionLocal() as db:
    thread = await AiAssistantRepository(db).get_thread(run.thread_id)
    if thread is None:
      raise ValueError("AI_THREAD_NOT_FOUND")
    user = await db.get(AuthUser, thread.user_id)
    if user is None or not user.is_active:
      raise PermissionError("AI_USER_NOT_ACTIVE")
    account_ids = tuple(
      str(value)
      for value in (
        await db.scalars(
          select(AuthUserAccountAccess.account_id).where(
            AuthUserAccountAccess.user_id == user.id
          )
        )
      ).all()
    )
    permissions = frozenset(str(value) for value in user.permissions or [])
    if not {"assistant:read", "assistant:write"}.issubset(permissions):
      raise PermissionError("AI_PERMISSION_REVOKED")
    if run.account_id and run.account_id not in account_ids:
      raise PermissionError("AI_ACCOUNT_PERMISSION_REVOKED")
    refs = tuple(
      AssistantContextRef(
        kind=str(item.get("kind") or ""),
        object_id=str(item.get("objectId") or ""),
        label=str(item.get("label")) if item.get("label") else None,
      )
      for item in run.context_refs or []
    )
    return AssistantExecutionContext(
      user_id=user.id,
      permissions=permissions,
      authorized_account_ids=account_ids,
      thread_id=run.thread_id,
      run_id=run.id,
      request_id=run.request_id,
      account_id=run.account_id,
      context_refs=refs,
      external_search_enabled=bool(run.external_search_enabled),
    )


async def _run_was_cancelled(run_id: str) -> bool:
  async with AsyncSessionLocal() as db:
    current = await db.get(AiAssistantRun, run_id)
    return current is None or current.cancel_requested_at is not None


async def _persist_approval_interruptions(
  run: AiAssistantRun,
  result: Any,
  event_writer: AssistantEventWriter,
) -> dict[str, Any]:
  state = result.to_state()
  state_payload = serialize_run_state(state)
  for interruption in list(result.interruptions or []):
    tool_name, call_id, arguments = interruption_details(interruption)
    if tool_name != "create_backtest_rerun_task" or not call_id:
      raise PermissionError("AI_UNEXPECTED_APPROVAL_TOOL")
    idempotency_key = _tool_idempotency(run.id, tool_name, arguments)
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      call = await repository.get_tool_call_by_idempotency(idempotency_key)
      if call is None:
        call = await repository.create_tool_call(
          run_id=run.id,
          call_id=call_id,
          tool_name=tool_name,
          tool_version="1",
          risk_level="NON_TRADING_WRITE",
          arguments=arguments,
          approval_required=True,
          idempotency_key=idempotency_key,
        )
    await event_writer.append(
      thread_id=run.thread_id,
      run_id=run.id,
      event_type="APPROVAL_REQUIRED",
      payload={
        "toolCallId": call.id,
        "toolName": tool_name,
        "toolStatus": "WAITING_APPROVAL",
        "toolSummary": "创建新的非实盘回测版本",
      },
    )
  return state_payload


async def _audit_hosted_web_searches(
  run: AiAssistantRun,
  result: Any,
  runtime_context: RuntimeRunContext,
  event_writer: AssistantEventWriter,
) -> None:
  seen: set[str] = set()
  for item in list(result.new_items or []):
    raw = _dump_value(getattr(item, "raw_item", item))
    if not isinstance(raw, dict) or raw.get("type") != "web_search_call":
      continue
    call_id = str(raw.get("id") or raw.get("call_id") or "").strip()
    if not call_id or call_id in seen:
      continue
    seen.add(call_id)
    if not runtime_context.execution.external_search_enabled:
      raise PermissionError("AI_UNEXPECTED_EXTERNAL_SEARCH")
    arguments = {"action": _dump_value(raw.get("action") or {})}
    async with AsyncSessionLocal() as db:
      repository = AiAssistantRepository(db)
      existing = next(
        (
          call
          for call in await repository.list_tool_calls(run.id)
          if call.call_id == call_id
        ),
        None,
      )
      if existing is not None and existing.status == "SUCCEEDED":
        continue
      call = existing or await repository.create_tool_call(
        run_id=run.id,
        call_id=call_id,
        tool_name="web_search",
        tool_version="openai-hosted-v1",
        risk_level="READ",
        arguments=arguments,
        approval_required=False,
        idempotency_key=None,
      )
    runtime_context.tool_call_count += 1
    limit_exceeded = runtime_context.tool_call_count > runtime_context.max_tool_calls
    await event_writer.append(
      thread_id=run.thread_id,
      run_id=run.id,
      event_type="TOOL_CALL_STARTED",
      payload={
        "toolCallId": call.id,
        "toolName": "web_search",
        "toolStatus": "RUNNING",
      },
    )
    if limit_exceeded:
      async with AsyncSessionLocal() as db:
        call = await AiAssistantRepository(db).finish_tool_call(
          await db.merge(call),
          status="FAILED",
          error_code="AI_TOOL_CALL_LIMIT_EXCEEDED",
          error_message="工具调用次数超过运行上限",
        )
      await event_writer.append(
        thread_id=run.thread_id,
        run_id=run.id,
        event_type="TOOL_CALL_COMPLETED",
        payload={
          "toolCallId": call.id,
          "toolName": "web_search",
          "toolStatus": "FAILED",
        },
      )
      raise RuntimeError("AI_TOOL_CALL_LIMIT_EXCEEDED")
    async with AsyncSessionLocal() as db:
      call = await AiAssistantRepository(db).finish_tool_call(
        await db.merge(call),
        status="SUCCEEDED",
        result={"summary": "OpenAI 托管网页搜索已完成"},
        summary="外部网页搜索已完成",
      )
    await event_writer.append(
      thread_id=run.thread_id,
      run_id=run.id,
      event_type="TOOL_CALL_COMPLETED",
      payload={
        "toolCallId": call.id,
        "toolName": "web_search",
        "toolStatus": "SUCCEEDED",
        "toolSummary": "外部网页搜索已完成",
      },
    )


def _sdk_context(run: AiAssistantRun) -> dict[str, str]:
  return {
    "thread_id": run.thread_id,
    "run_id": run.id,
    "request_id": run.request_id,
  }


async def _resume_state(run: AiAssistantRun, agent: Any) -> Any:
  if not isinstance(run.resume_state, dict):
    return None
  state = await deserialize_run_state(
    agent,
    dict(run.resume_state),
    context_override=_sdk_context(run),
  )
  interruptions = state_interruptions(state)
  async with AsyncSessionLocal() as db:
    calls = await AiAssistantRepository(db).list_tool_calls(run.id)
  calls_by_sdk_id = {call.call_id: call for call in calls}
  calls_by_idempotency = {
    call.idempotency_key: call for call in calls if call.idempotency_key
  }
  unresolved = False
  for interruption in interruptions:
    tool_name, call_id, arguments = interruption_details(interruption)
    idempotency_key = _tool_idempotency(run.id, tool_name, arguments)
    call = calls_by_sdk_id.get(call_id) or calls_by_idempotency.get(idempotency_key)
    if call is None or call.approval_status == "PENDING":
      unresolved = True
      continue
    if call.approval_status == "APPROVED":
      state.approve(interruption)
    else:
      state.reject(
        interruption,
        rejection_message="用户拒绝了本次非实盘任务创建。",
      )
  if unresolved:
    raise RuntimeError("AI_APPROVAL_DECISION_MISSING")
  return state


async def execute_run(
  run_id: str,
  config: AiRuntimeConfig,
  *,
  instance_id: str,
) -> None:
  event_writer = AssistantEventWriter()
  async with AsyncSessionLocal() as db:
    repository = AiAssistantRepository(db)
    run = await repository.get_run(run_id)
    if run is None or run.status != "RUNNING":
      return
    message = await repository.get_message(run.user_message_id)
    if message is None:
      raise ValueError("AI_USER_MESSAGE_NOT_FOUND")
    text_blocks = [
      str(item.get("text") or "")
      for item in message.content_blocks or []
      if str(item.get("kind") or "").upper() == "TEXT"
    ]
    user_text = validate_user_text("\n".join(text_blocks))
    history = await repository.load_session_items(run.thread_id)
    prior_calls = await repository.list_tool_calls(run.id)

  persisted_call_count = sum(
    call.status in {"RUNNING", "SUCCEEDED", "FAILED"} for call in prior_calls
  )
  prior_tool_call_count = max(
    int(run.tool_call_count or 0),
    persisted_call_count,
  )

  execution = await _execution_context(run)
  runtime_context = RuntimeRunContext(
    execution=execution,
    tool_call_count=prior_tool_call_count,
    max_tool_calls=config.max_tool_calls,
  )
  agent = build_agent(runtime_context, model=config.model)
  resume_state = await _resume_state(run, agent)
  input_value: Any = resume_state
  if input_value is None:
    input_value = [*history, {"role": "user", "content": user_text}]

  await event_writer.append(
    thread_id=run.thread_id,
    run_id=run.id,
    event_type="RUN_STATUS_CHANGED",
    payload={"run": _run_payload(run)},
  )

  stream = Runner.run_streamed(
    agent,
    input_value,
    context=_sdk_context(run) if resume_state is None else None,
    max_turns=config.max_turns,
  )
  buffer = ""
  last_flush = asyncio.get_running_loop().time()
  async with asyncio.timeout(config.run_timeout_seconds):
    async for event in stream.stream_events():
      if await _run_was_cancelled(run.id):
        raise AssistantRunCancelled()
      if event.type != "raw_response_event" or not isinstance(
        event.data, ResponseTextDeltaEvent
      ):
        continue
      buffer += event.data.delta
      now = asyncio.get_running_loop().time()
      if len(buffer) >= 1024 or now - last_flush >= 0.25:
        await event_writer.append(
          thread_id=run.thread_id,
          run_id=run.id,
          event_type="MESSAGE_DELTA",
          payload={"text": buffer},
        )
        buffer = ""
        last_flush = now
  if buffer:
    await event_writer.append(
      thread_id=run.thread_id,
      run_id=run.id,
      event_type="MESSAGE_DELTA",
      payload={"text": buffer},
    )

  await _audit_hosted_web_searches(run, stream, runtime_context, event_writer)
  usage = stream.context_wrapper.usage
  if stream.interruptions:
    state_payload = await _persist_approval_interruptions(run, stream, event_writer)
    async with AsyncSessionLocal() as db:
      run = await AiAssistantRepository(db).finish_run(
        run,
        status="WAITING_APPROVAL",
        resume_state=state_payload,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        request_count=int(getattr(usage, "requests", 0) or 0),
        tool_call_count=runtime_context.tool_call_count,
        expected_lease_owner=instance_id,
      )
    await event_writer.append(
      thread_id=run.thread_id,
      run_id=run.id,
      event_type="RUN_STATUS_CHANGED",
      payload={"run": _run_payload(run)},
    )
    return

  final_text = str(stream.final_output or "").strip()
  if not final_text:
    final_text = "本次运行没有生成可展示的结果。"
  blocks = _content_blocks(final_text, list(stream.new_items or []))
  async with AsyncSessionLocal() as db:
    repository = AiAssistantRepository(db)
    message, run = await repository.complete_run(
      run,
      expected_lease_owner=instance_id,
      content_blocks=blocks,
      session_items=[_jsonable_item(item) for item in stream.to_input_list()],
      input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
      output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
      request_count=int(getattr(usage, "requests", 0) or 0),
      tool_call_count=runtime_context.tool_call_count,
    )
  if message is None:
    await event_writer.append(
      thread_id=run.thread_id,
      run_id=run.id,
      event_type="RUN_STATUS_CHANGED",
      payload={"run": _run_payload(run)},
    )
    return
  await event_writer.append(
    thread_id=run.thread_id,
    run_id=run.id,
    event_type="MESSAGE_COMPLETED",
    payload={"message": _message_payload(message)},
  )
  await event_writer.append(
    thread_id=run.thread_id,
    run_id=run.id,
    event_type="RUN_STATUS_CHANGED",
    payload={"run": _run_payload(run)},
  )


async def settle_run_failure(
  run_id: str,
  exc: BaseException,
  *,
  instance_id: str,
) -> None:
  status = "CANCELLED" if isinstance(exc, AssistantRunCancelled) else "FAILED"
  code = "AI_RUN_CANCELLED" if status == "CANCELLED" else exc.__class__.__name__
  if status == "CANCELLED":
    message = "AI 运行已取消"
  elif isinstance(exc, TimeoutError):
    message = "AI 运行超时，请缩小问题范围后重试。"
  elif isinstance(exc, PermissionError):
    message = "当前权限或账户授权已变化，AI 运行已停止。"
  else:
    message = "AI 运行暂时失败，请稍后重试。"
  async with AsyncSessionLocal() as db:
    repository = AiAssistantRepository(db)
    run = await repository.get_run(run_id)
    if run is None:
      return
    try:
      run = await repository.finish_run(
        run,
        status=status,
        error_code=code,
        error_message=message,
        expected_lease_owner=instance_id,
      )
    except AssistantRunLeaseLostError:
      return
  writer = AssistantEventWriter()
  await writer.append(
    thread_id=run.thread_id,
    run_id=run.id,
    event_type="RUN_FAILED" if status == "FAILED" else "RUN_STATUS_CHANGED",
    payload={
      "run": _run_payload(run),
      "errorCode": code,
      "errorMessage": message,
      "retryable": status == "FAILED",
    },
  )
