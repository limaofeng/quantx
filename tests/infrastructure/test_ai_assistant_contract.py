from __future__ import annotations

import inspect

from quantx_infrastructure.models.ai_assistant import (
  AiAssistantEvent,
  AiAssistantRun,
  AiAssistantThread,
  AiAssistantToolCall,
)
from quantx_infrastructure.repositories.ai_assistant_repository import (
  AiAssistantRepository,
)
from sqlalchemy import UniqueConstraint


def test_assistant_tables_keep_personal_scope_and_active_run_uniqueness() -> None:
  assert AiAssistantThread.__table__.c.user_id.foreign_keys
  assert AiAssistantThread.__table__.c.account_id.nullable is True

  active_index = next(
    index
    for index in AiAssistantRun.__table__.indexes
    if index.name == "uq_ai_assistant_active_run"
  )
  assert active_index.unique is True
  predicate = str(active_index.dialect_options["postgresql"]["where"])
  assert "WAITING_APPROVAL" in predicate
  assert "RUNNING" in predicate


def test_assistant_event_and_tool_audit_sequences_are_idempotent() -> None:
  event_unique = {
    constraint.name
    for constraint in AiAssistantEvent.__table__.constraints
    if isinstance(constraint, UniqueConstraint)
  }
  tool_unique = {
    constraint.name
    for constraint in AiAssistantToolCall.__table__.constraints
    if isinstance(constraint, UniqueConstraint)
  }

  assert "uq_ai_assistant_event_sequence" in event_unique
  assert "uq_ai_assistant_tool_call" in tool_unique
  assert "uq_ai_assistant_tool_idempotency" in tool_unique


def test_run_completion_is_atomic_and_lease_owned() -> None:
  completion = inspect.getsource(AiAssistantRepository.complete_run)
  finish = inspect.getsource(AiAssistantRepository.finish_run)

  assert "expected_lease_owner" in completion
  assert ".with_for_update()" in completion
  assert "AiAssistantMessage(" in completion
  assert "AiAssistantSessionItem(" in completion
  assert 'run.status = "COMPLETED"' in completion
  assert "expected_lease_owner" in finish


def test_approval_resolution_serializes_the_run_and_tool_call() -> None:
  source = inspect.getsource(AiAssistantRepository.resolve_approval)

  assert source.count(".with_for_update()") >= 2


def test_cancel_and_delete_serialize_against_runtime_state_changes() -> None:
  cancel = inspect.getsource(AiAssistantRepository.cancel_run)
  delete_thread = inspect.getsource(AiAssistantRepository.delete_thread)

  assert ".with_for_update()" in cancel
  assert ".with_for_update()" in delete_thread
  assert "ACTIVE_RUN_STATUSES" in delete_thread
