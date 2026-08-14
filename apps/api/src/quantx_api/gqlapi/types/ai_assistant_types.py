"""Typed GraphQL contract for the product-facing AI assistant."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Optional, Union

import strawberry
from quantx_infrastructure.models.ai_assistant import (
  AiAssistantEvent as EventModel,
)
from quantx_infrastructure.models.ai_assistant import (
  AiAssistantMessage as MessageModel,
)
from quantx_infrastructure.models.ai_assistant import AiAssistantRun as RunModel
from quantx_infrastructure.models.ai_assistant import (
  AiAssistantThread as ThreadModel,
)


@strawberry.enum
class AiAssistantRunStatus(Enum):
  QUEUED = "QUEUED"
  RUNNING = "RUNNING"
  WAITING_APPROVAL = "WAITING_APPROVAL"
  COMPLETED = "COMPLETED"
  FAILED = "FAILED"
  CANCELLED = "CANCELLED"


@strawberry.enum
class AiAssistantEventType(Enum):
  RUN_STATUS_CHANGED = "RUN_STATUS_CHANGED"
  MESSAGE_DELTA = "MESSAGE_DELTA"
  MESSAGE_COMPLETED = "MESSAGE_COMPLETED"
  TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
  TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
  APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
  USAGE_RECORDED = "USAGE_RECORDED"
  RUN_FAILED = "RUN_FAILED"


@strawberry.enum
class AiAssistantApprovalDecision(Enum):
  APPROVE = "APPROVE"
  REJECT = "REJECT"


@strawberry.type
class AiAssistantTextBlock:
  kind: str = "TEXT"
  text: str = ""


@strawberry.type
class AiAssistantCitationBlock:
  kind: str = "CITATION"
  title: str = ""
  url: str = ""
  published_at: Optional[datetime] = None
  visited_at: Optional[datetime] = None


@strawberry.type
class AiAssistantContextBlock:
  kind: str = "CONTEXT"
  context_kind: str = ""
  object_id: str = ""
  label: Optional[str] = None


@strawberry.type
class AiAssistantToolResultBlock:
  kind: str = "TOOL_RESULT"
  tool_call_id: str = ""
  tool_name: str = ""
  status: str = ""
  summary: Optional[str] = None


@strawberry.type
class AiAssistantTaskReferenceBlock:
  kind: str = "TASK_REFERENCE"
  task_kind: str = ""
  reference_id: str = ""
  label: str = ""


@strawberry.type
class AiAssistantErrorBlock:
  kind: str = "ERROR"
  code: str = ""
  message: str = ""
  retryable: bool = False


AiAssistantContentBlock = Annotated[
  Union[
    AiAssistantTextBlock,
    AiAssistantCitationBlock,
    AiAssistantContextBlock,
    AiAssistantToolResultBlock,
    AiAssistantTaskReferenceBlock,
    AiAssistantErrorBlock,
  ],
  strawberry.union("AiAssistantContentBlock"),
]


def _parse_datetime(value) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  if not value:
    return None
  try:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except ValueError:
    return None


def content_block_from_dict(value: dict) -> AiAssistantContentBlock:
  kind = str(value.get("kind") or "TEXT").upper()
  if kind == "CITATION":
    return AiAssistantCitationBlock(
      title=str(value.get("title") or ""),
      url=str(value.get("url") or ""),
      published_at=_parse_datetime(
        value.get("publishedAt") or value.get("published_at")
      ),
      visited_at=_parse_datetime(value.get("visitedAt") or value.get("visited_at")),
    )
  if kind == "CONTEXT":
    return AiAssistantContextBlock(
      context_kind=str(value.get("contextKind") or value.get("context_kind") or ""),
      object_id=str(value.get("objectId") or value.get("object_id") or ""),
      label=str(value["label"]) if value.get("label") is not None else None,
    )
  if kind == "TOOL_RESULT":
    return AiAssistantToolResultBlock(
      tool_call_id=str(value.get("toolCallId") or value.get("tool_call_id") or ""),
      tool_name=str(value.get("toolName") or value.get("tool_name") or ""),
      status=str(value.get("status") or ""),
      summary=str(value["summary"]) if value.get("summary") is not None else None,
    )
  if kind == "TASK_REFERENCE":
    return AiAssistantTaskReferenceBlock(
      task_kind=str(value.get("taskKind") or value.get("task_kind") or ""),
      reference_id=str(value.get("referenceId") or value.get("reference_id") or ""),
      label=str(value.get("label") or ""),
    )
  if kind == "ERROR":
    return AiAssistantErrorBlock(
      code=str(value.get("code") or "AI_ERROR"),
      message=str(value.get("message") or "AI 运行失败"),
      retryable=bool(value.get("retryable", False)),
    )
  return AiAssistantTextBlock(text=str(value.get("text") or ""))


@strawberry.type
class AiAssistantMessage:
  id: strawberry.ID
  thread_id: strawberry.ID
  run_id: Optional[strawberry.ID]
  sequence: int
  role: str
  content: list[AiAssistantContentBlock]
  created_at: datetime

  @staticmethod
  def from_model(model: MessageModel) -> "AiAssistantMessage":
    return AiAssistantMessage(
      id=model.id,
      thread_id=model.thread_id,
      run_id=model.run_id,
      sequence=int(model.sequence),
      role=model.role,
      content=[
        content_block_from_dict(dict(item)) for item in model.content_blocks or []
      ],
      created_at=model.created_at,
    )


@strawberry.type
class AiAssistantRun:
  id: strawberry.ID
  thread_id: strawberry.ID
  status: AiAssistantRunStatus
  model: str
  error_code: Optional[str]
  error_message: Optional[str]
  input_tokens: int
  output_tokens: int
  request_count: int
  tool_call_count: int
  created_at: datetime
  started_at: Optional[datetime]
  finished_at: Optional[datetime]

  @staticmethod
  def from_model(model: RunModel) -> "AiAssistantRun":
    return AiAssistantRun(
      id=model.id,
      thread_id=model.thread_id,
      status=AiAssistantRunStatus(model.status),
      model=model.model,
      error_code=model.error_code,
      error_message=model.error_message,
      input_tokens=int(model.input_tokens or 0),
      output_tokens=int(model.output_tokens or 0),
      request_count=int(model.request_count or 0),
      tool_call_count=int(model.tool_call_count or 0),
      created_at=model.created_at,
      started_at=model.started_at,
      finished_at=model.finished_at,
    )


@strawberry.type
class AiAssistantThread:
  id: strawberry.ID
  account_id: Optional[str]
  agent_id: str
  title: str
  external_search_enabled: bool
  status: str
  last_activity_at: datetime
  created_at: datetime
  updated_at: datetime

  @staticmethod
  def from_model(model: ThreadModel) -> "AiAssistantThread":
    return AiAssistantThread(
      id=model.id,
      account_id=model.account_id,
      agent_id=model.agent_id,
      title=model.title,
      external_search_enabled=bool(model.external_search_enabled),
      status=model.status,
      last_activity_at=model.last_activity_at,
      created_at=model.created_at,
      updated_at=model.updated_at,
    )


@strawberry.type
class AiAssistantThreadEdge:
  cursor: str
  node: AiAssistantThread


@strawberry.type
class AiAssistantThreadConnection:
  edges: list[AiAssistantThreadEdge]
  end_cursor: Optional[str]
  has_next_page: bool


@strawberry.type
class AiAssistantMessagePage:
  items: list[AiAssistantMessage]
  next_sequence: Optional[int]
  has_more: bool


@strawberry.type
class AiAssistantToolCapability:
  name: str
  description: str
  risk_level: str
  approval_required: bool


@strawberry.type
class AiAssistantCapabilities:
  enabled: bool
  runtime_status: str
  model: str
  external_search_available: bool
  agents: list[str]
  tools: list[AiAssistantToolCapability]
  max_message_length: int
  max_context_refs: int
  max_concurrent_runs: int


@strawberry.type
class AiAssistantEvent:
  id: strawberry.ID
  thread_id: strawberry.ID
  run_id: strawberry.ID
  sequence: int
  event_type: AiAssistantEventType
  text: Optional[str]
  message: Optional[AiAssistantMessage]
  run: Optional[AiAssistantRun]
  tool_call_id: Optional[strawberry.ID]
  tool_name: Optional[str]
  tool_status: Optional[str]
  tool_summary: Optional[str]
  error_code: Optional[str]
  error_message: Optional[str]
  retryable: bool
  created_at: datetime

  @staticmethod
  def from_model(model: EventModel) -> "AiAssistantEvent":
    payload = dict(model.payload or {})
    message = payload.get("message")
    run = payload.get("run")
    return AiAssistantEvent(
      id=model.id,
      thread_id=model.thread_id,
      run_id=model.run_id,
      sequence=int(model.sequence),
      event_type=AiAssistantEventType(model.event_type),
      text=str(payload["text"]) if payload.get("text") is not None else None,
      message=_message_from_payload(message) if isinstance(message, dict) else None,
      run=_run_from_payload(run) if isinstance(run, dict) else None,
      tool_call_id=payload.get("toolCallId"),
      tool_name=payload.get("toolName"),
      tool_status=payload.get("toolStatus"),
      tool_summary=payload.get("toolSummary"),
      error_code=payload.get("errorCode"),
      error_message=payload.get("errorMessage"),
      retryable=bool(payload.get("retryable", False)),
      created_at=model.created_at,
    )


def _message_from_payload(value: dict) -> AiAssistantMessage:
  return AiAssistantMessage(
    id=str(value.get("id") or ""),
    thread_id=str(value.get("threadId") or ""),
    run_id=value.get("runId"),
    sequence=int(value.get("sequence") or 0),
    role=str(value.get("role") or "ASSISTANT"),
    content=[
      content_block_from_dict(dict(item)) for item in value.get("content") or []
    ],
    created_at=_parse_datetime(value.get("createdAt")) or datetime.utcnow(),
  )


def _run_from_payload(value: dict) -> AiAssistantRun:
  return AiAssistantRun(
    id=str(value.get("id") or ""),
    thread_id=str(value.get("threadId") or ""),
    status=AiAssistantRunStatus(str(value.get("status") or "FAILED")),
    model=str(value.get("model") or ""),
    error_code=value.get("errorCode"),
    error_message=value.get("errorMessage"),
    input_tokens=int(value.get("inputTokens") or 0),
    output_tokens=int(value.get("outputTokens") or 0),
    request_count=int(value.get("requestCount") or 0),
    tool_call_count=int(value.get("toolCallCount") or 0),
    created_at=_parse_datetime(value.get("createdAt")) or datetime.utcnow(),
    started_at=_parse_datetime(value.get("startedAt")),
    finished_at=_parse_datetime(value.get("finishedAt")),
  )


@strawberry.input
class AiAssistantContextRefInput:
  kind: str
  object_id: str
  label: Optional[str] = None


@strawberry.input
class AiAssistantRouteContextInput:
  path: str
  object_type: Optional[str] = None
  object_id: Optional[str] = None


@strawberry.input
class CreateAiAssistantThreadInput:
  account_id: Optional[str] = None
  agent_id: str = "research_assistant"
  title: Optional[str] = None


@strawberry.input
class SendAiAssistantMessageInput:
  thread_id: strawberry.ID
  text: str
  client_message_id: str
  route_context: Optional[AiAssistantRouteContextInput] = None
  context_refs: list[AiAssistantContextRefInput] = strawberry.field(
    default_factory=list
  )


@strawberry.input
class UpdateAiAssistantThreadInput:
  thread_id: strawberry.ID
  title: Optional[str] = None
  external_search_enabled: Optional[bool] = None


@strawberry.input
class ResolveAiAssistantApprovalInput:
  run_id: strawberry.ID
  tool_call_id: strawberry.ID
  decision: AiAssistantApprovalDecision
