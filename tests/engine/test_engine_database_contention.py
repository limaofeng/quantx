import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_engine.command_processor as command_processor
import quantx_engine.report_processor as report_processor
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError


@pytest.mark.asyncio
async def test_command_claim_pool_contention_retries_without_exiting(
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  stopped = asyncio.Event()
  attempts = 0

  async def claim_next():
    nonlocal attempts
    attempts += 1
    if attempts == 1:
      raise SQLAlchemyTimeoutError("pool busy")
    stopped.set()
    return None

  monkeypatch.setattr(
    command_processor,
    "_recover_processing_commands",
    AsyncMock(),
  )
  monkeypatch.setattr(command_processor, "_claim_next", claim_next)
  monkeypatch.setattr(
    command_processor,
    "_DATABASE_CONTENTION_RETRY_SECONDS",
    0.001,
  )

  with caplog.at_level(logging.WARNING):
    await command_processor.run_command_consumer(stopped)

  assert attempts == 2
  assert "command claim deferred by database pool contention" in caplog.text


@pytest.mark.asyncio
async def test_command_completion_pool_contention_retries_same_result(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  stopped = asyncio.Event()
  completion_attempts = 0

  async def claim_next():
    return "message-1", "TEST", {"value": 1}

  async def complete(message_id, *, result=None, error=None):
    nonlocal completion_attempts
    assert message_id == "message-1"
    assert result == {"ok": True}
    assert error is None
    completion_attempts += 1
    if completion_attempts == 1:
      raise SQLAlchemyTimeoutError("pool busy")
    stopped.set()

  monkeypatch.setattr(
    command_processor,
    "_recover_processing_commands",
    AsyncMock(),
  )
  monkeypatch.setattr(command_processor, "_claim_next", claim_next)
  monkeypatch.setattr(
    command_processor,
    "_dispatch",
    AsyncMock(return_value={"ok": True}),
  )
  monkeypatch.setattr(command_processor, "_complete", complete)
  monkeypatch.setattr(
    command_processor,
    "_DATABASE_CONTENTION_RETRY_SECONDS",
    0.001,
  )

  await command_processor.run_command_consumer(stopped)

  assert completion_attempts == 2


@pytest.mark.asyncio
async def test_report_releases_inbox_session_before_projection(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  stopped = asyncio.Event()
  session_released = False
  expunged = False
  report = SimpleNamespace(
    message_id="report-1",
    message_type="delta_report",
    client_order_id=None,
    payload={},
  )

  class Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      nonlocal session_released
      session_released = True

    async def get(self, _model, message_id):
      assert message_id == "report-1"
      return report

    def expunge(self, value):
      nonlocal expunged
      assert value is report
      expunged = True

  async def process(value):
    assert value is report
    assert session_released
    assert expunged

  async def finish(_message_id, *, error=None):
    assert error is None
    stopped.set()

  monkeypatch.setattr(
    report_processor, "_recover_consumer_state", AsyncMock(return_value=True)
  )
  monkeypatch.setattr(
    report_processor, "_open_wakeup_subscription", AsyncMock(return_value=None)
  )
  monkeypatch.setattr(report_processor, "_claim", AsyncMock(return_value="report-1"))
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", Session)
  monkeypatch.setattr(report_processor, "_process", process)
  monkeypatch.setattr(report_processor, "_stage_runtime_events", AsyncMock())
  monkeypatch.setattr(report_processor, "_drain_runtime_events", AsyncMock())
  monkeypatch.setattr(report_processor, "_broker_order_ids", lambda _report: [])
  monkeypatch.setattr(report_processor, "_finish", finish)

  await report_processor.run_report_consumer(stopped)

  assert session_released
  assert expunged


@pytest.mark.asyncio
async def test_report_pool_contention_remains_retryable(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  stopped = asyncio.Event()
  captured_error = None
  report = SimpleNamespace(
    message_id="report-2",
    message_type="delta_report",
    client_order_id=None,
    payload={},
  )

  class Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, _model, _message_id):
      return report

    def expunge(self, _value):
      return None

  async def process(_report):
    raise SQLAlchemyTimeoutError("pool busy")

  async def finish(_message_id, *, error=None):
    nonlocal captured_error
    captured_error = error
    stopped.set()

  monkeypatch.setattr(
    report_processor, "_recover_consumer_state", AsyncMock(return_value=True)
  )
  monkeypatch.setattr(
    report_processor, "_open_wakeup_subscription", AsyncMock(return_value=None)
  )
  monkeypatch.setattr(report_processor, "_claim", AsyncMock(return_value="report-2"))
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", Session)
  monkeypatch.setattr(report_processor, "_process", process)
  monkeypatch.setattr(report_processor, "_finish", finish)

  await report_processor.run_report_consumer(stopped)

  assert isinstance(captured_error, report_processor.RetryableReportError)
