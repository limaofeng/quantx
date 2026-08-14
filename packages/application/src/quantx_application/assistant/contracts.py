"""Framework-neutral assistant DTOs and policy metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Optional


class AssistantRunStatus(StrEnum):
  QUEUED = "QUEUED"
  RUNNING = "RUNNING"
  WAITING_APPROVAL = "WAITING_APPROVAL"
  COMPLETED = "COMPLETED"
  FAILED = "FAILED"
  CANCELLED = "CANCELLED"


class AssistantEventType(StrEnum):
  RUN_STATUS_CHANGED = "RUN_STATUS_CHANGED"
  MESSAGE_DELTA = "MESSAGE_DELTA"
  MESSAGE_COMPLETED = "MESSAGE_COMPLETED"
  TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
  TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
  APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
  USAGE_RECORDED = "USAGE_RECORDED"
  RUN_FAILED = "RUN_FAILED"


class AssistantToolRisk(StrEnum):
  READ = "READ"
  COMPUTE = "COMPUTE"
  NON_TRADING_WRITE = "NON_TRADING_WRITE"
  TRADING = "TRADING"
  ADMIN = "ADMIN"


@dataclass(frozen=True)
class AssistantContextRef:
  kind: str
  object_id: str
  label: Optional[str] = None


@dataclass(frozen=True)
class AssistantExecutionContext:
  user_id: str
  permissions: frozenset[str]
  authorized_account_ids: tuple[str, ...]
  thread_id: str
  run_id: str
  request_id: str
  account_id: Optional[str] = None
  context_refs: tuple[AssistantContextRef, ...] = ()
  external_search_enabled: bool = False

  def require_permission(self, permission: str) -> None:
    if permission not in self.permissions:
      raise PermissionError(f"missing permission: {permission}")

  def require_account(self, account_id: Optional[str] = None) -> str:
    selected = str(account_id or self.account_id or "").strip()
    if not selected or selected not in self.authorized_account_ids:
      raise PermissionError("account is not authorized for this assistant run")
    return selected


@dataclass(frozen=True)
class AssistantToolMetadata:
  name: str
  version: str
  description: str
  risk_level: AssistantToolRisk
  required_permissions: frozenset[str] = frozenset()
  account_scoped: bool = False
  timeout_seconds: float = 15.0
  idempotent: bool = True
  external_data_classification: str = "INTERNAL"


@dataclass(frozen=True)
class AssistantToolResult:
  value: Mapping[str, Any]
  summary: str
  reference_id: Optional[str] = None


@dataclass(frozen=True)
class AssistantEventRecord:
  thread_id: str
  run_id: str
  event_type: AssistantEventType
  payload: Mapping[str, Any] = field(default_factory=dict)
