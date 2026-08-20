from __future__ import annotations

from datetime import timedelta

import pytest
from quantx_domain.clock import utcnow
from quantx_engine import report_processor
from quantx_engine.strategy_executor import RuntimeConsumerUnavailable
from quantx_engine.strategy_manager import strategy_manager
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import StrategyRuntimeEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _event(
  event_id: str,
  run_id: str,
  *,
  offset_seconds: int,
) -> StrategyRuntimeEvent:
  return StrategyRuntimeEvent(
    event_id=event_id,
    business_key=f"order:{event_id}",
    strategy_run_id=run_id,
    client_order_id=f"client:{event_id}",
    broker_order_id=event_id,
    event_type="ORDER",
    payload={},
    application_status="PENDING",
    application_attempts=0,
    created_at=utcnow() + timedelta(seconds=offset_seconds),
  )


@pytest.mark.asyncio
async def test_unavailable_run_does_not_consume_attempt_or_block_other_runs(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[StrategyRuntimeEvent.__table__],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)

  class AvailabilityExecutor:
    available = {"run-ready"}

    def require_durable_event_consumer(self, run_id: str) -> None:
      if run_id not in self.available:
        raise RuntimeConsumerUnavailable(f"unavailable: {run_id}")

    def arm_durable_event_barrier(self, _run_id: str, _event_key: str) -> None:
      return None

    async def advance_durable_event_barrier(
      self,
      _run_id: str,
      _event_key: str,
    ) -> None:
      return None

  executor = AvailabilityExecutor()
  monkeypatch.setattr(strategy_manager, "executor", executor)
  applied: list[str] = []

  async def capture(event: StrategyRuntimeEvent) -> None:
    applied.append(event.event_id)

  monkeypatch.setattr(report_processor, "_apply_runtime_event", capture)
  paused_first = _event("paused-1", "run-paused", offset_seconds=0)
  paused_second = _event("paused-2", "run-paused", offset_seconds=1)
  ready = _event("ready-1", "run-ready", offset_seconds=2)
  async with sessions() as db:
    db.add_all([paused_first, paused_second, ready])
    await db.commit()

  for _ in range(8):
    await report_processor._drain_runtime_events()

  async with sessions() as db:
    stored = {
      event.event_id: event
      for event in (
        await db.execute(
          select(StrategyRuntimeEvent).order_by(StrategyRuntimeEvent.created_at)
        )
      ).scalars()
    }
  assert applied == ["ready-1"]
  assert stored["paused-1"].application_status == "PENDING"
  assert stored["paused-1"].application_attempts == 0
  assert stored["paused-2"].application_status == "PENDING"
  assert stored["paused-2"].application_attempts == 0
  assert stored["ready-1"].application_status == "APPLIED"
  assert stored["ready-1"].application_attempts == 1

  executor.available.add("run-paused")
  await report_processor._drain_runtime_events()

  async with sessions() as db:
    paused = (
      await db.execute(
        select(StrategyRuntimeEvent)
        .where(StrategyRuntimeEvent.strategy_run_id == "run-paused")
        .order_by(StrategyRuntimeEvent.created_at)
      )
    ).scalars().all()
  assert applied == ["ready-1", "paused-1", "paused-2"]
  assert [event.application_status for event in paused] == ["APPLIED", "APPLIED"]
  assert [event.application_attempts for event in paused] == [1, 1]
  await engine.dispose()


@pytest.mark.asyncio
async def test_failed_run_is_blocked_for_pass_while_other_run_advances(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[StrategyRuntimeEvent.__table__],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)

  class AvailableExecutor:
    def require_durable_event_consumer(self, _run_id: str) -> None:
      return None

    def arm_durable_event_barrier(self, _run_id: str, _event_key: str) -> None:
      return None

    async def advance_durable_event_barrier(
      self,
      _run_id: str,
      _event_key: str,
    ) -> None:
      return None

  monkeypatch.setattr(strategy_manager, "executor", AvailableExecutor())
  applied: list[str] = []

  async def apply(event: StrategyRuntimeEvent) -> None:
    applied.append(event.event_id)
    if event.strategy_run_id == "run-failing":
      raise RuntimeError("callback failed")

  monkeypatch.setattr(report_processor, "_apply_runtime_event", apply)
  failing_first = _event("failing-1", "run-failing", offset_seconds=0)
  failing_second = _event("failing-2", "run-failing", offset_seconds=1)
  ready = _event("ready-1", "run-ready", offset_seconds=2)
  async with sessions() as db:
    db.add_all([failing_first, failing_second, ready])
    await db.commit()

  with pytest.raises(report_processor.RetryableReportError, match="callback failed"):
    await report_processor._drain_runtime_events()

  async with sessions() as db:
    stored = {
      event.event_id: event
      for event in (
        await db.execute(
          select(StrategyRuntimeEvent).order_by(StrategyRuntimeEvent.created_at)
        )
      ).scalars()
    }
  assert applied == ["failing-1", "ready-1"]
  assert stored["failing-1"].application_status == "PENDING"
  assert stored["failing-1"].application_attempts == 1
  assert "callback failed" in stored["failing-1"].application_error
  assert stored["failing-2"].application_status == "PENDING"
  assert stored["failing-2"].application_attempts == 0
  assert stored["ready-1"].application_status == "APPLIED"
  await engine.dispose()


@pytest.mark.asyncio
async def test_processing_event_recovers_after_compensating_session_failure(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[StrategyRuntimeEvent.__table__],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)

  class AvailableExecutor:
    def require_durable_event_consumer(self, _run_id: str) -> None:
      return None

    def arm_durable_event_barrier(self, _run_id: str, _event_key: str) -> None:
      return None

    async def advance_durable_event_barrier(
      self,
      _run_id: str,
      _event_key: str,
    ) -> None:
      return None

  monkeypatch.setattr(strategy_manager, "executor", AvailableExecutor())
  apply_attempts = 0

  async def fail_once(_event: StrategyRuntimeEvent) -> None:
    nonlocal apply_attempts
    apply_attempts += 1
    if apply_attempts == 1:
      raise RuntimeError("callback failed before pending restore")

  monkeypatch.setattr(report_processor, "_apply_runtime_event", fail_once)
  event = _event("processing-recovery", "run-recovery", offset_seconds=0)
  async with sessions() as db:
    db.add(event)
    await db.commit()

  class FailedSessionContext:
    async def __aenter__(self):
      raise RuntimeError("database unavailable during pending restore")

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
      return None

  session_calls = 0

  def fail_fourth_session():
    nonlocal session_calls
    session_calls += 1
    if session_calls == 4:
      return FailedSessionContext()
    return sessions()

  monkeypatch.setattr(report_processor, "AsyncSessionLocal", fail_fourth_session)

  with pytest.raises(
    RuntimeError,
    match="database unavailable during pending restore",
  ):
    await report_processor._drain_runtime_events()

  async with sessions() as db:
    interrupted = await db.get(StrategyRuntimeEvent, event.event_id)
    assert interrupted is not None
    assert interrupted.application_status == "PROCESSING"

  await report_processor._drain_runtime_events()

  async with sessions() as db:
    recovered = await db.get(StrategyRuntimeEvent, event.event_id)
    assert recovered is not None
    assert recovered.application_status == "APPLIED"
    assert recovered.application_attempts == 2
    assert recovered.application_error is None
  assert apply_attempts == 2
  await engine.dispose()
