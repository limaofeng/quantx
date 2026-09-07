"""Expected PAPER input blockers do not stop the shared CRITICAL market lane."""

from types import SimpleNamespace

import pytest
from quantx_engine.t_assistant_paper_shadow_supervisor import (
  TAssistantPaperShadowSupervisor,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import select

from tests.engine.unit.test_t_assistant_paper_entry_runtime import seeded
from tests.infrastructure.test_t_candidate_evidence import (
  allocation_sessions,
  base_sessions,
  frozen_config,
  ledger_sessions,
  sessions,
)

_FIXTURES = (
  allocation_sessions,
  base_sessions,
  frozen_config,
  ledger_sessions,
  sessions,
)


async def test_expected_input_blocker_has_one_durable_audit_and_keeps_consumer_alive(
  sessions, frozen_config, monkeypatch
):
  source, _ = await seeded(sessions)
  supervisor = TAssistantPaperShadowSupervisor(
    session_factory=sessions, clock=lambda: source.now
  )
  async with sessions() as db:
    execution = await TAssistantExecutionRepository(db).get_domain(source.execution_id)

  async def unavailable(**kwargs):
    raise ValueError("T_VALUATION_MARK_STALE")

  monkeypatch.setattr(supervisor._entry_runtime, "dispatch", unavailable)
  binding = SimpleNamespace(execution=execution)
  for _ in range(2):
    result = await supervisor._dispatch_entries(binding)
    assert result.status == "BLOCKED"
    assert result.reason_codes == ("T_VALUATION_MARK_STALE",)
  async with sessions() as db:
    events = list(
      (
        await db.scalars(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.event_type == "PAPER_ENTRY_INPUT_BLOCKED"
          )
        )
      ).all()
    )
    assert len(events) == 1
    assert events[0].payload == {"reason_codes": ["T_VALUATION_MARK_STALE"]}


async def test_unknown_corruption_is_not_downgraded_to_retryable_input(
  sessions, frozen_config, monkeypatch
):
  source, _ = await seeded(sessions)
  supervisor = TAssistantPaperShadowSupervisor(
    session_factory=sessions, clock=lambda: source.now
  )
  async with sessions() as db:
    execution = await TAssistantExecutionRepository(db).get_domain(source.execution_id)

  async def corrupt(**kwargs):
    raise ValueError("PAPER_PORTFOLIO_EVENT_EVIDENCE_CONFLICT")

  monkeypatch.setattr(supervisor._entry_runtime, "dispatch", corrupt)
  with pytest.raises(ValueError, match="EVENT_EVIDENCE_CONFLICT"):
    await supervisor._dispatch_entries(SimpleNamespace(execution=execution))
  async with sessions() as db:
    assert not (
      await db.scalars(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.event_type == "PAPER_ENTRY_INPUT_BLOCKED"
        )
      )
    ).all()
