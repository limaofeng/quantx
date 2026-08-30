from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_ai_runtime import main as runtime_main
from quantx_ai_runtime import observability
from quantx_ai_runtime.config import AiRuntimeConfigController, load_config
from quantx_ai_runtime.runtime import consumer, limit_up_research
from sqlalchemy.exc import TimeoutError as PoolTimeout


@pytest.mark.asyncio
async def test_heartbeat_recovers_after_pool_timeout_without_exiting(
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  stopped = asyncio.Event()
  calls = 0

  async def write_heartbeat(**_kwargs):
    nonlocal calls
    calls += 1
    if calls == 1:
      raise PoolTimeout("private database exception context")
    stopped.set()

  monkeypatch.setattr(observability, "write_heartbeat", write_heartbeat)

  await asyncio.wait_for(
    observability.heartbeat_loop(
      stopped,
      instance_id="runtime-test",
      controller=AiRuntimeConfigController(load_config()),
      dependencies_available=True,
    ),
    timeout=3,
  )

  assert calls == 2
  assert "heartbeat" in caplog.text
  assert "private database exception context" not in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_does_not_hide_programming_errors(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    observability,
    "write_heartbeat",
    AsyncMock(side_effect=ValueError("invalid heartbeat")),
  )

  with pytest.raises(ValueError, match="invalid heartbeat"):
    await observability.heartbeat_loop(
      asyncio.Event(),
      instance_id="runtime-test",
      controller=SimpleNamespace(snapshot=load_config),
      dependencies_available=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["assistant", "research"])
async def test_queue_claim_recovers_without_exiting(component, monkeypatch, caplog):
  stopped = asyncio.Event()
  config = replace(load_config(), enabled=True, api_key="test-only")
  controller = AiRuntimeConfigController(config)
  module = consumer if component == "assistant" else limit_up_research
  calls = 0
  sessions_open = 0

  @asynccontextmanager
  async def sessions():
    nonlocal sessions_open
    sessions_open += 1
    try:
      yield object()
    finally:
      sessions_open -= 1

  async def claim(**_kwargs):
    nonlocal calls
    calls += 1
    if calls == 1:
      raise PoolTimeout("private connection details")
    stopped.set()
    return None

  async def retry(_stopped):
    assert sessions_open == 0
    await asyncio.sleep(0)

  repository = SimpleNamespace(
    claim_next_run=claim,
    claim_next_research_job=claim,
  )
  subscription = SimpleNamespace(wait_for_message=AsyncMock(), close=AsyncMock())
  monkeypatch.setattr(module, "database_session", sessions)
  monkeypatch.setattr(module, "wait_for_database_retry", retry)
  if component == "assistant":
    monkeypatch.setattr(module, "AiAssistantRepository", lambda _db: repository)
    monkeypatch.setattr(
      module.redis_pubsub, "open_subscription", AsyncMock(return_value=subscription)
    )
    run_loop = module.run_consumer
  else:
    monkeypatch.setattr(module, "FirstBoardPromotionRepository", lambda _db: repository)
    run_loop = module.run_limit_up_research_consumer

  await asyncio.wait_for(
    run_loop(stopped, instance_id="runtime-test", controller=controller), timeout=2
  )

  assert calls == 2
  assert sessions_open == 0
  assert f"{component}-claim" in caplog.text
  assert "private connection details" not in caplog.text


@pytest.mark.asyncio
async def test_guarded_run_cancels_child_and_releases_resources_on_shutdown(
  monkeypatch,
):
  entered = asyncio.Event()
  released = asyncio.Event()

  async def execute(*_args, **_kwargs):
    try:
      entered.set()
      await asyncio.Event().wait()
    finally:
      released.set()

  monkeypatch.setattr(consumer, "execute_run", execute)
  settle = AsyncMock()
  monkeypatch.setattr(consumer, "settle_run_failure", settle)
  task = asyncio.create_task(
    consumer._execute_guarded("run-test", load_config(), "runtime-test")
  )
  await entered.wait()
  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await task

  assert released.is_set()
  settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_capacity_timeout_cancels_model_instead_of_ignoring_lease(
  monkeypatch,
):
  entered = asyncio.Event()
  released = asyncio.Event()

  async def execute(*_args, **_kwargs):
    try:
      entered.set()
      await asyncio.Event().wait()
    finally:
      released.set()

  async def renew(*_args, **_kwargs):
    await entered.wait()
    raise PoolTimeout("private lease details")

  monkeypatch.setattr(consumer, "execute_run", execute)
  monkeypatch.setattr(consumer, "_renew_lease", renew)
  settle = AsyncMock()
  monkeypatch.setattr(consumer, "settle_run_failure", settle)

  await consumer._execute_guarded("run-test", load_config(), "runtime-test")

  assert released.is_set()
  settle.assert_awaited_once()
  assert isinstance(settle.await_args.args[1], PoolTimeout)


@pytest.mark.asyncio
async def test_heartbeat_pressure_retry_stops_promptly(monkeypatch):
  stopped = asyncio.Event()
  attempted = asyncio.Event()

  async def write(**_kwargs):
    attempted.set()
    raise PoolTimeout("busy")

  monkeypatch.setattr(observability, "write_heartbeat", write)
  task = asyncio.create_task(
    observability.heartbeat_loop(
      stopped,
      instance_id="runtime-test",
      controller=AiRuntimeConfigController(load_config()),
      dependencies_available=True,
    )
  )
  await attempted.wait()
  stopped.set()
  await asyncio.wait_for(task, timeout=0.5)


@pytest.mark.asyncio
async def test_runtime_survives_initial_heartbeat_timeout_and_closes_pool(monkeypatch):
  config = replace(load_config(), api_key="")
  stopped_holder = []
  attempts = 0

  async def config_loop(stopped, **_kwargs):
    stopped_holder.append(stopped)
    await stopped.wait()

  async def refresh(self):
    return self.snapshot()

  async def write(**_kwargs):
    nonlocal attempts
    attempts += 1
    if attempts == 1:
      raise PoolTimeout("initial checkout busy")
    stopped_holder[0].set()

  async def retry(_stopped):
    await asyncio.sleep(0)

  monkeypatch.setattr(runtime_main, "load_config", lambda: config)
  monkeypatch.setattr(runtime_main.AiRuntimeConfigController, "refresh", refresh)
  monkeypatch.setattr(runtime_main, "config_refresh_loop", config_loop)
  monkeypatch.setattr(observability, "write_heartbeat", write)
  monkeypatch.setattr(observability, "wait_for_database_retry", retry)
  monkeypatch.setattr(
    runtime_main, "write_heartbeat", AsyncMock(side_effect=PoolTimeout("closing busy"))
  )
  close_database = AsyncMock()
  monkeypatch.setattr(runtime_main, "close_database", close_database)
  monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *_: None)

  await asyncio.wait_for(runtime_main.run_runtime(), timeout=2)

  assert attempts == 2
  close_database.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_pressure_does_not_cancel_an_inflight_run(monkeypatch):
  stopped = asyncio.Event()
  running = asyncio.Event()
  released = asyncio.Event()
  config = replace(
    load_config(), enabled=True, api_key="test-only", max_concurrent_runs=2
  )
  claims = 0

  @asynccontextmanager
  async def sessions():
    yield object()

  async def claim(**_kwargs):
    nonlocal claims
    claims += 1
    if claims == 1:
      return SimpleNamespace(
        id="run-test",
        model=config.model,
        runtime_config_snapshot={},
        runtime_config_version=0,
      )
    await running.wait()
    raise PoolTimeout("temporary claim pressure")

  async def execute(*_args, **_kwargs):
    try:
      running.set()
      await asyncio.Event().wait()
    finally:
      released.set()

  async def retry(_stopped):
    assert running.is_set()
    assert not released.is_set()
    stopped.set()

  subscription = SimpleNamespace(wait_for_message=AsyncMock(), close=AsyncMock())
  monkeypatch.setattr(consumer, "database_session", sessions)
  monkeypatch.setattr(
    consumer, "AiAssistantRepository", lambda _db: SimpleNamespace(claim_next_run=claim)
  )
  monkeypatch.setattr(consumer, "_execute_guarded", execute)
  monkeypatch.setattr(consumer, "wait_for_database_retry", retry)
  monkeypatch.setattr(
    consumer.redis_pubsub, "open_subscription", AsyncMock(return_value=subscription)
  )

  await asyncio.wait_for(
    consumer.run_consumer(
      stopped, instance_id="runtime-test", controller=AiRuntimeConfigController(config)
    ),
    timeout=2,
  )

  assert claims == 2
  assert released.is_set()  # Only the explicit shutdown cancelled this run.
