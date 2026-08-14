"""Transactional persistence and leasing for AI assistant runs."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any, Iterable, Optional, Sequence

from quantx_domain.clock import utcnow
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.ai_assistant import (
  AiAssistantDeletionAudit,
  AiAssistantEvent,
  AiAssistantMessage,
  AiAssistantRun,
  AiAssistantSessionItem,
  AiAssistantThread,
  AiAssistantToolCall,
)

ACTIVE_RUN_STATUSES = ("QUEUED", "RUNNING", "WAITING_APPROVAL")
TERMINAL_RUN_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


class AssistantRunAlreadyActiveError(ValueError):
  pass


class AssistantRunLeaseLostError(RuntimeError):
  pass


class AiAssistantRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def create_thread(
    self,
    *,
    user_id: str,
    account_id: Optional[str],
    agent_id: str = "research_assistant",
    title: str = "新对话",
  ) -> AiAssistantThread:
    now = utcnow()
    thread = AiAssistantThread(
      id=str(uuid.uuid4()),
      user_id=user_id,
      account_id=account_id,
      agent_id=agent_id,
      title=(title.strip() or "新对话")[:160],
      external_search_enabled=False,
      status="ACTIVE",
      last_activity_at=now,
    )
    self.db.add(thread)
    await self.db.commit()
    await self.db.refresh(thread)
    return thread

  async def get_thread(
    self,
    thread_id: str,
    *,
    user_id: Optional[str] = None,
  ) -> Optional[AiAssistantThread]:
    stmt = select(AiAssistantThread).where(AiAssistantThread.id == thread_id)
    if user_id is not None:
      stmt = stmt.where(AiAssistantThread.user_id == user_id)
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def list_threads(
    self,
    *,
    user_id: str,
    authorized_account_ids: Sequence[str],
    limit: int,
    before_activity_at=None,
    before_id: Optional[str] = None,
  ) -> list[AiAssistantThread]:
    stmt = select(AiAssistantThread).where(
      AiAssistantThread.user_id == user_id,
      AiAssistantThread.status == "ACTIVE",
      or_(
        AiAssistantThread.account_id.is_(None),
        AiAssistantThread.account_id.in_(tuple(authorized_account_ids)),
      ),
    )
    if before_activity_at is not None and before_id:
      stmt = stmt.where(
        or_(
          AiAssistantThread.last_activity_at < before_activity_at,
          (
            (AiAssistantThread.last_activity_at == before_activity_at)
            & (AiAssistantThread.id < before_id)
          ),
        )
      )
    result = await self.db.execute(
      stmt.order_by(
        AiAssistantThread.last_activity_at.desc(),
        AiAssistantThread.id.desc(),
      ).limit(max(1, min(limit, 100)) + 1)
    )
    return list(result.scalars().all())

  async def update_thread(
    self,
    thread: AiAssistantThread,
    *,
    title: Optional[str] = None,
    external_search_enabled: Optional[bool] = None,
  ) -> AiAssistantThread:
    locked_thread = await self.db.scalar(
      select(AiAssistantThread)
      .where(AiAssistantThread.id == thread.id)
      .with_for_update()
    )
    if locked_thread is None:
      raise ValueError("assistant thread does not exist")
    thread = locked_thread
    if title is not None:
      thread.title = (title.strip() or "新对话")[:160]
    if external_search_enabled is not None:
      thread.external_search_enabled = bool(external_search_enabled)
    thread.last_activity_at = utcnow()
    await self.db.commit()
    await self.db.refresh(thread)
    return thread

  async def delete_thread(
    self,
    thread: AiAssistantThread,
    *,
    request_id: str,
  ) -> None:
    locked_thread = await self.db.scalar(
      select(AiAssistantThread)
      .where(AiAssistantThread.id == thread.id)
      .with_for_update()
    )
    if locked_thread is None:
      raise ValueError("assistant thread does not exist")
    thread = locked_thread
    active_run = await self.db.scalar(
      select(AiAssistantRun.id).where(
        AiAssistantRun.thread_id == thread.id,
        AiAssistantRun.status.in_(ACTIVE_RUN_STATUSES),
      )
    )
    if active_run is not None:
      raise AssistantRunAlreadyActiveError(active_run)
    fingerprint = hashlib.sha256(thread.id.encode("utf-8")).hexdigest()
    self.db.add(
      AiAssistantDeletionAudit(
        id=str(uuid.uuid4()),
        user_id=thread.user_id,
        thread_fingerprint=fingerprint,
        request_id=request_id[:64],
        occurred_at=utcnow(),
      )
    )
    await self.db.delete(thread)
    await self.db.commit()

  async def create_message_run(
    self,
    *,
    thread: AiAssistantThread,
    text: str,
    client_message_id: str,
    request_id: str,
    model: str,
    context_refs: Sequence[dict[str, Any]],
    account_id: Optional[str],
  ) -> tuple[AiAssistantMessage, AiAssistantRun, bool]:
    locked_thread = await self.db.scalar(
      select(AiAssistantThread)
      .where(AiAssistantThread.id == thread.id)
      .with_for_update()
    )
    if locked_thread is None:
      raise ValueError("assistant thread does not exist")
    thread = locked_thread
    # Re-check idempotency only after taking the thread lock. This makes two
    # concurrent deliveries of the same client message converge on one run.
    existing_message = await self.db.scalar(
      select(AiAssistantMessage).where(
        AiAssistantMessage.thread_id == thread.id,
        AiAssistantMessage.client_message_id == client_message_id,
      )
    )
    if existing_message is not None:
      existing_run = await self.db.scalar(
        select(AiAssistantRun).where(
          AiAssistantRun.user_message_id == existing_message.id
        )
      )
      if existing_run is None:
        raise RuntimeError("idempotent assistant message has no run")
      return existing_message, existing_run, False

    active_run = await self.db.scalar(
      select(AiAssistantRun).where(
        AiAssistantRun.thread_id == thread.id,
        AiAssistantRun.status.in_(ACTIVE_RUN_STATUSES),
      )
    )
    if active_run is not None:
      raise AssistantRunAlreadyActiveError(active_run.id)

    now = utcnow()
    sequence = await self._next_message_sequence(thread.id)
    message = AiAssistantMessage(
      id=str(uuid.uuid4()),
      thread_id=thread.id,
      client_message_id=client_message_id[:128],
      sequence=sequence,
      role="USER",
      content_blocks=[{"kind": "TEXT", "text": text}],
      created_at=now,
    )
    run = AiAssistantRun(
      id=str(uuid.uuid4()),
      thread_id=thread.id,
      user_message_id=message.id,
      request_id=request_id[:64],
      status="QUEUED",
      model=model[:80],
      prompt_version="v1",
      account_id=account_id,
      context_refs=list(context_refs),
      external_search_enabled=bool(thread.external_search_enabled),
    )
    message.run_id = run.id
    thread.last_activity_at = now
    if thread.title == "新对话":
      thread.title = text.strip().replace("\n", " ")[:60] or "新对话"
    self.db.add_all([message, run])
    await self.db.commit()
    await self.db.refresh(message)
    await self.db.refresh(run)
    return message, run, True

  async def retry_run(
    self,
    *,
    previous: AiAssistantRun,
    request_id: str,
  ) -> AiAssistantRun:
    await self.db.execute(
      select(AiAssistantThread)
      .where(AiAssistantThread.id == previous.thread_id)
      .with_for_update()
    )
    active = await self.db.scalar(
      select(AiAssistantRun).where(
        AiAssistantRun.thread_id == previous.thread_id,
        AiAssistantRun.status.in_(ACTIVE_RUN_STATUSES),
      )
    )
    if active is not None:
      raise AssistantRunAlreadyActiveError(active.id)
    run = AiAssistantRun(
      id=str(uuid.uuid4()),
      thread_id=previous.thread_id,
      user_message_id=previous.user_message_id,
      request_id=request_id[:64],
      status="QUEUED",
      model=previous.model,
      prompt_version=previous.prompt_version,
      account_id=previous.account_id,
      context_refs=list(previous.context_refs or []),
      external_search_enabled=previous.external_search_enabled,
    )
    self.db.add(run)
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def get_run(
    self,
    run_id: str,
    *,
    user_id: Optional[str] = None,
  ) -> Optional[AiAssistantRun]:
    stmt = select(AiAssistantRun).where(AiAssistantRun.id == run_id)
    if user_id is not None:
      stmt = stmt.join(
        AiAssistantThread,
        AiAssistantThread.id == AiAssistantRun.thread_id,
      ).where(AiAssistantThread.user_id == user_id)
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def cancel_run(self, run: AiAssistantRun) -> AiAssistantRun:
    locked_run = await self.db.scalar(
      select(AiAssistantRun).where(AiAssistantRun.id == run.id).with_for_update()
    )
    if locked_run is None:
      raise ValueError("assistant run does not exist")
    run = locked_run
    if run.status in TERMINAL_RUN_STATUSES:
      return run
    run.cancel_requested_at = utcnow()
    if run.status in {"QUEUED", "WAITING_APPROVAL"}:
      run.status = "CANCELLED"
      run.finished_at = utcnow()
      run.lease_owner = None
      run.lease_expires_at = None
      await self.db.execute(
        update(AiAssistantToolCall)
        .where(
          AiAssistantToolCall.run_id == run.id,
          AiAssistantToolCall.approval_status == "PENDING",
        )
        .values(
          approval_status="REJECTED",
          status="CANCELLED",
          finished_at=utcnow(),
        )
      )
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def resolve_approval(
    self,
    *,
    run: AiAssistantRun,
    tool_call_id: str,
    approved: bool,
  ) -> AiAssistantToolCall:
    locked_run = await self.db.scalar(
      select(AiAssistantRun).where(AiAssistantRun.id == run.id).with_for_update()
    )
    if locked_run is None or locked_run.status != "WAITING_APPROVAL":
      raise ValueError("assistant run is not waiting for approval")
    run = locked_run
    call = await self.db.scalar(
      select(AiAssistantToolCall)
      .where(
        AiAssistantToolCall.id == tool_call_id,
        AiAssistantToolCall.run_id == run.id,
        AiAssistantToolCall.status == "WAITING_APPROVAL",
      )
      .with_for_update()
    )
    if call is None:
      raise ValueError("pending assistant tool call does not exist")
    call.approval_status = "APPROVED" if approved else "REJECTED"
    call.status = "APPROVED" if approved else "REJECTED"
    await self.db.flush()
    unresolved = await self.db.scalar(
      select(func.count(AiAssistantToolCall.id)).where(
        AiAssistantToolCall.run_id == run.id,
        AiAssistantToolCall.approval_status == "PENDING",
      )
    )
    if int(unresolved or 0) == 0:
      run.status = "QUEUED"
      run.lease_owner = None
      run.lease_expires_at = None
    await self.db.commit()
    await self.db.refresh(call)
    await self.db.refresh(run)
    return call

  async def has_successful_non_trading_write(self, run_id: str) -> bool:
    count = await self.db.scalar(
      select(func.count(AiAssistantToolCall.id)).where(
        AiAssistantToolCall.run_id == run_id,
        AiAssistantToolCall.risk_level == "NON_TRADING_WRITE",
        AiAssistantToolCall.status == "SUCCEEDED",
      )
    )
    return int(count or 0) > 0

  async def claim_next_run(
    self,
    *,
    instance_id: str,
    lease_seconds: int,
  ) -> Optional[AiAssistantRun]:
    now = utcnow()
    stmt = (
      select(AiAssistantRun)
      .where(
        or_(
          AiAssistantRun.status == "QUEUED",
          (
            (AiAssistantRun.status == "RUNNING")
            & (AiAssistantRun.lease_expires_at < now)
          ),
        )
      )
      .order_by(AiAssistantRun.created_at)
      .with_for_update(skip_locked=True)
      .limit(1)
    )
    run = (await self.db.execute(stmt)).scalar_one_or_none()
    if run is None:
      await self.db.rollback()
      return None
    run.status = "RUNNING"
    run.lease_owner = instance_id[:96]
    run.lease_expires_at = now + timedelta(seconds=max(15, lease_seconds))
    run.started_at = run.started_at or now
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def renew_lease(
    self,
    run_id: str,
    *,
    instance_id: str,
    lease_seconds: int,
  ) -> bool:
    result = await self.db.execute(
      update(AiAssistantRun)
      .where(
        AiAssistantRun.id == run_id,
        AiAssistantRun.status == "RUNNING",
        AiAssistantRun.lease_owner == instance_id,
      )
      .values(lease_expires_at=utcnow() + timedelta(seconds=max(15, lease_seconds)))
    )
    await self.db.commit()
    return bool(result.rowcount)

  async def finish_run(
    self,
    run: AiAssistantRun,
    *,
    status: str,
    resume_state: Optional[dict[str, Any]] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    request_count: int = 0,
    tool_call_count: int = 0,
    expected_lease_owner: Optional[str] = None,
  ) -> AiAssistantRun:
    locked_run = await self.db.scalar(
      select(AiAssistantRun).where(AiAssistantRun.id == run.id).with_for_update()
    )
    if locked_run is None:
      raise ValueError("assistant run does not exist")
    if expected_lease_owner and (
      locked_run.status != "RUNNING" or locked_run.lease_owner != expected_lease_owner
    ):
      await self.db.rollback()
      raise AssistantRunLeaseLostError(run.id)
    run = locked_run
    if run.cancel_requested_at is not None and status != "CANCELLED":
      status = "CANCELLED"
      resume_state = None
      error_code = "AI_RUN_CANCELLED"
      error_message = "AI 运行已取消"
    if status == "CANCELLED":
      await self.db.execute(
        update(AiAssistantToolCall)
        .where(
          AiAssistantToolCall.run_id == run.id,
          AiAssistantToolCall.approval_status == "PENDING",
        )
        .values(
          approval_status="REJECTED",
          status="CANCELLED",
          finished_at=utcnow(),
        )
      )
    run.status = status
    run.resume_state = resume_state
    run.error_code = error_code
    run.error_message = error_message[:512] if error_message else None
    run.input_tokens = max(0, input_tokens)
    run.output_tokens = max(0, output_tokens)
    run.request_count = max(0, request_count)
    run.tool_call_count = max(0, tool_call_count)
    run.lease_owner = None
    run.lease_expires_at = None
    if status in TERMINAL_RUN_STATUSES:
      run.finished_at = utcnow()
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def complete_run(
    self,
    run: AiAssistantRun,
    *,
    expected_lease_owner: str,
    content_blocks: Sequence[dict[str, Any]],
    session_items: Iterable[dict[str, Any]],
    input_tokens: int,
    output_tokens: int,
    request_count: int,
    tool_call_count: int,
  ) -> tuple[Optional[AiAssistantMessage], AiAssistantRun]:
    """Atomically persist the final reply, SDK session, and terminal run."""
    serialized_session = list(session_items)
    locked_run = await self.db.scalar(
      select(AiAssistantRun).where(AiAssistantRun.id == run.id).with_for_update()
    )
    if locked_run is None:
      raise ValueError("assistant run does not exist")
    if locked_run.status != "RUNNING" or locked_run.lease_owner != expected_lease_owner:
      await self.db.rollback()
      raise AssistantRunLeaseLostError(run.id)
    run = locked_run
    if run.cancel_requested_at is not None:
      await self.db.execute(
        update(AiAssistantToolCall)
        .where(
          AiAssistantToolCall.run_id == run.id,
          AiAssistantToolCall.approval_status == "PENDING",
        )
        .values(
          approval_status="REJECTED",
          status="CANCELLED",
          finished_at=utcnow(),
        )
      )
      run.status = "CANCELLED"
      run.resume_state = None
      run.error_code = "AI_RUN_CANCELLED"
      run.error_message = "AI 运行已取消"
      run.lease_owner = None
      run.lease_expires_at = None
      run.finished_at = utcnow()
      await self.db.commit()
      await self.db.refresh(run)
      return None, run

    await self.db.execute(
      select(AiAssistantThread)
      .where(AiAssistantThread.id == run.thread_id)
      .with_for_update()
    )
    message = AiAssistantMessage(
      id=str(uuid.uuid4()),
      thread_id=run.thread_id,
      run_id=run.id,
      sequence=await self._next_message_sequence(run.thread_id),
      role="ASSISTANT",
      content_blocks=list(content_blocks),
      created_at=utcnow(),
    )
    self.db.add(message)
    await self.db.execute(
      delete(AiAssistantSessionItem).where(
        AiAssistantSessionItem.thread_id == run.thread_id
      )
    )
    now = utcnow()
    self.db.add_all(
      [
        AiAssistantSessionItem(
          id=str(uuid.uuid4()),
          thread_id=run.thread_id,
          sequence=index + 1,
          item=item,
          created_at=now,
        )
        for index, item in enumerate(serialized_session)
      ]
    )
    run.status = "COMPLETED"
    run.resume_state = None
    run.error_code = None
    run.error_message = None
    run.input_tokens = max(0, input_tokens)
    run.output_tokens = max(0, output_tokens)
    run.request_count = max(0, request_count)
    run.tool_call_count = max(0, tool_call_count)
    run.lease_owner = None
    run.lease_expires_at = None
    run.finished_at = utcnow()
    await self.db.commit()
    await self.db.refresh(message)
    await self.db.refresh(run)
    return message, run

  async def list_messages(
    self,
    thread_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 100,
  ) -> list[AiAssistantMessage]:
    result = await self.db.execute(
      select(AiAssistantMessage)
      .where(
        AiAssistantMessage.thread_id == thread_id,
        AiAssistantMessage.sequence > max(0, after_sequence),
      )
      .order_by(AiAssistantMessage.sequence)
      .limit(max(1, min(limit, 200)) + 1)
    )
    return list(result.scalars().all())

  async def append_message(
    self,
    *,
    thread_id: str,
    run_id: str,
    role: str,
    content_blocks: Sequence[dict[str, Any]],
  ) -> AiAssistantMessage:
    await self.db.execute(
      select(AiAssistantThread)
      .where(AiAssistantThread.id == thread_id)
      .with_for_update()
    )
    message = AiAssistantMessage(
      id=str(uuid.uuid4()),
      thread_id=thread_id,
      run_id=run_id,
      sequence=await self._next_message_sequence(thread_id),
      role=role,
      content_blocks=list(content_blocks),
      created_at=utcnow(),
    )
    self.db.add(message)
    await self.db.commit()
    await self.db.refresh(message)
    return message

  async def append_event(
    self,
    *,
    thread_id: str,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
  ) -> AiAssistantEvent:
    await self.db.execute(
      select(AiAssistantThread)
      .where(AiAssistantThread.id == thread_id)
      .with_for_update()
    )
    next_sequence = (
      int(
        await self.db.scalar(
          select(func.coalesce(func.max(AiAssistantEvent.sequence), 0)).where(
            AiAssistantEvent.thread_id == thread_id
          )
        )
        or 0
      )
      + 1
    )
    event = AiAssistantEvent(
      id=str(uuid.uuid4()),
      thread_id=thread_id,
      run_id=run_id,
      sequence=next_sequence,
      event_type=event_type,
      payload=payload,
      created_at=utcnow(),
    )
    self.db.add(event)
    await self.db.commit()
    await self.db.refresh(event)
    return event

  async def list_events(
    self,
    thread_id: str,
    *,
    after_sequence: int,
    limit: int = 200,
  ) -> list[AiAssistantEvent]:
    result = await self.db.execute(
      select(AiAssistantEvent)
      .where(
        AiAssistantEvent.thread_id == thread_id,
        AiAssistantEvent.sequence > max(0, after_sequence),
      )
      .order_by(AiAssistantEvent.sequence)
      .limit(max(1, min(limit, 500)))
    )
    return list(result.scalars().all())

  async def load_session_items(self, thread_id: str) -> list[dict[str, Any]]:
    result = await self.db.execute(
      select(AiAssistantSessionItem)
      .where(AiAssistantSessionItem.thread_id == thread_id)
      .order_by(AiAssistantSessionItem.sequence)
    )
    return [dict(item.item or {}) for item in result.scalars().all()]

  async def append_session_items(
    self,
    thread_id: str,
    items: Iterable[dict[str, Any]],
  ) -> None:
    serialized = list(items)
    if not serialized:
      return
    await self.db.execute(
      select(AiAssistantThread)
      .where(AiAssistantThread.id == thread_id)
      .with_for_update()
    )
    next_sequence = (
      int(
        await self.db.scalar(
          select(func.coalesce(func.max(AiAssistantSessionItem.sequence), 0)).where(
            AiAssistantSessionItem.thread_id == thread_id
          )
        )
        or 0
      )
      + 1
    )
    now = utcnow()
    self.db.add_all(
      [
        AiAssistantSessionItem(
          id=str(uuid.uuid4()),
          thread_id=thread_id,
          sequence=next_sequence + index,
          item=item,
          created_at=now,
        )
        for index, item in enumerate(serialized)
      ]
    )
    await self.db.commit()

  async def replace_session_items(
    self,
    thread_id: str,
    items: Iterable[dict[str, Any]],
  ) -> None:
    serialized = list(items)
    await self.db.execute(
      delete(AiAssistantSessionItem).where(
        AiAssistantSessionItem.thread_id == thread_id
      )
    )
    now = utcnow()
    self.db.add_all(
      [
        AiAssistantSessionItem(
          id=str(uuid.uuid4()),
          thread_id=thread_id,
          sequence=index + 1,
          item=item,
          created_at=now,
        )
        for index, item in enumerate(serialized)
      ]
    )
    await self.db.commit()

  async def list_tool_calls(self, run_id: str) -> list[AiAssistantToolCall]:
    result = await self.db.execute(
      select(AiAssistantToolCall)
      .where(AiAssistantToolCall.run_id == run_id)
      .order_by(AiAssistantToolCall.created_at, AiAssistantToolCall.id)
    )
    return list(result.scalars().all())

  async def get_tool_call_by_idempotency(
    self,
    idempotency_key: str,
  ) -> Optional[AiAssistantToolCall]:
    return await self.db.scalar(
      select(AiAssistantToolCall).where(
        AiAssistantToolCall.idempotency_key == idempotency_key
      )
    )

  async def mark_tool_call_running(
    self,
    call: AiAssistantToolCall,
  ) -> AiAssistantToolCall:
    call.status = "RUNNING"
    call.started_at = utcnow()
    await self.db.commit()
    await self.db.refresh(call)
    return call

  async def get_message(self, message_id: str) -> Optional[AiAssistantMessage]:
    return await self.db.get(AiAssistantMessage, message_id)

  async def create_tool_call(
    self,
    *,
    run_id: str,
    call_id: str,
    tool_name: str,
    tool_version: str,
    risk_level: str,
    arguments: dict[str, Any],
    approval_required: bool,
    idempotency_key: Optional[str],
  ) -> AiAssistantToolCall:
    existing = await self.db.scalar(
      select(AiAssistantToolCall).where(
        AiAssistantToolCall.run_id == run_id,
        AiAssistantToolCall.call_id == call_id,
      )
    )
    if existing is not None:
      return existing
    call = AiAssistantToolCall(
      id=str(uuid.uuid4()),
      run_id=run_id,
      call_id=call_id[:128],
      tool_name=tool_name[:80],
      tool_version=tool_version[:24],
      risk_level=risk_level[:32],
      arguments=arguments,
      status="WAITING_APPROVAL" if approval_required else "RUNNING",
      approval_status="PENDING" if approval_required else "NOT_REQUIRED",
      idempotency_key=idempotency_key,
      started_at=utcnow(),
    )
    self.db.add(call)
    await self.db.commit()
    await self.db.refresh(call)
    return call

  async def finish_tool_call(
    self,
    call: AiAssistantToolCall,
    *,
    status: str,
    result: Optional[dict[str, Any]] = None,
    summary: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
  ) -> AiAssistantToolCall:
    call.status = status
    call.result = result
    call.result_summary = summary[:512] if summary else None
    call.error_code = error_code
    call.error_message = error_message[:512] if error_message else None
    call.finished_at = utcnow()
    await self.db.commit()
    await self.db.refresh(call)
    return call

  async def _next_message_sequence(self, thread_id: str) -> int:
    current = await self.db.scalar(
      select(func.coalesce(func.max(AiAssistantMessage.sequence), 0)).where(
        AiAssistantMessage.thread_id == thread_id
      )
    )
    return int(current or 0) + 1
