import asyncio
import logging

import pytest
import quantx_engine.main as engine_main


def test_configured_engine_instance_id_is_authoritative(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("QUANTX_ENGINE_INSTANCE_ID", "mac-runtime-engine")

  assert engine_main._engine_instance_id() == "mac-runtime-engine"


def test_engine_instance_id_rejects_oversized_value(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("QUANTX_ENGINE_INSTANCE_ID", "x" * 65)

  with pytest.raises(RuntimeError, match="heartbeat schema limit"):
    engine_main._engine_instance_id()


@pytest.mark.asyncio
async def test_transient_heartbeat_timeout_retries_without_failing_engine(
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  stopped = asyncio.Event()
  writes = 0

  async def write_heartbeat(_instance_id: str) -> None:
    nonlocal writes
    writes += 1
    if writes == 1:
      raise asyncio.TimeoutError
    stopped.set()

  monkeypatch.setattr(engine_main, "_write_heartbeat_once", write_heartbeat)
  monkeypatch.setattr(engine_main, "ENGINE_HEARTBEAT_RETRY_SECONDS", 0.001)

  with caplog.at_level(logging.WARNING):
    await engine_main._heartbeat(stopped, "engine-instance")

  assert writes == 2
  assert "retrying without restarting" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_cancellation_still_propagates(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  entered = asyncio.Event()

  async def blocked_write(_instance_id: str) -> None:
    entered.set()
    await asyncio.Future()

  monkeypatch.setattr(engine_main, "_write_heartbeat_once", blocked_write)
  monkeypatch.setattr(
    engine_main,
    "ENGINE_DATABASE_OPERATION_TIMEOUT_SECONDS",
    60.0,
  )
  task = asyncio.create_task(engine_main._heartbeat(asyncio.Event(), "engine-instance"))
  await entered.wait()

  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await task
