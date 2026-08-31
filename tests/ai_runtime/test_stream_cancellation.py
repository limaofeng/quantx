"""Run the real Agents SDK with a local model; no provider or trading calls."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents import Agent, RunConfig, Runner
from agents.models.interface import Model
from openai.types.responses import Response, ResponseCompletedEvent
from quantx_ai_runtime.config import load_config
from quantx_ai_runtime.runtime import consumer, runner
from sqlalchemy.exc import TimeoutError as PoolTimeout


@pytest.fixture
async def streaming_run(monkeypatch):
  model_started = asyncio.Event()
  model_closing = asyncio.Event()
  model_closed = asyncio.Event()
  allow_model_close = asyncio.Event()
  allow_model_close.set()
  event_body_entered = asyncio.Event()
  release_query = asyncio.Event()
  response_ready = asyncio.Event()
  streams = []

  class LocalModel(Model):
    async def get_response(self, *args, **kwargs):
      raise AssertionError("expected a streaming model call")

    async def stream_response(self, *args, **kwargs):
      model_started.set()
      try:
        await response_ready.wait()
        yield ResponseCompletedEvent(
          type="response.completed",
          sequence_number=1,
          response=Response(
            id="response-test",
            object="response",
            created_at=0,
            model="local-test",
            status="completed",
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
            output=[
              {
                "id": "output-test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                  {
                    "type": "output_text",
                    "text": "done",
                    "annotations": [],
                    "logprobs": [],
                  }
                ],
              }
            ],
          ),
        )
      finally:
        model_closing.set()
        await allow_model_close.wait()
        model_closed.set()

  agent = Agent(name="Local cancellation test", model=LocalModel())
  run = SimpleNamespace(
    id="run-test",
    status="RUNNING",
    user_message_id="message-test",
    thread_id="thread-test",
    tool_call_count=0,
    agent_id="research_assistant",
  )
  repository = SimpleNamespace(
    get_run=AsyncMock(return_value=run),
    get_message=AsyncMock(
      return_value=SimpleNamespace(
        content_blocks=[{"kind": "TEXT", "text": "test"}],
      )
    ),
    load_session_items=AsyncMock(return_value=[]),
    list_tool_calls=AsyncMock(return_value=[]),
    complete_run=AsyncMock(return_value=(None, run)),
  )

  @asynccontextmanager
  async def sessions():
    yield object()

  async def cancellation_query(_run_id):
    event_body_entered.set()
    await release_query.wait()
    return False

  original_streamed = Runner.run_streamed

  def streamed(*args, **kwargs):
    kwargs["run_config"] = RunConfig(tracing_disabled=True)
    result = original_streamed(*args, **kwargs)
    streams.append(result)
    return result

  monkeypatch.setattr(runner, "database_session", sessions)
  monkeypatch.setattr(runner, "AiAssistantRepository", lambda db: repository)
  monkeypatch.setattr(
    runner, "AssistantEventWriter", lambda: SimpleNamespace(append=AsyncMock())
  )
  monkeypatch.setattr(
    runner, "_execution_context", AsyncMock(return_value=SimpleNamespace())
  )
  monkeypatch.setattr(runner, "build_agent", lambda *args, **kwargs: agent)
  monkeypatch.setattr(runner, "_resume_state", AsyncMock(return_value=None))
  monkeypatch.setattr(runner, "_sdk_context", lambda run: {})
  monkeypatch.setattr(runner, "_run_payload", lambda run: {})
  monkeypatch.setattr(runner, "_run_was_cancelled", cancellation_query)
  monkeypatch.setattr(Runner, "run_streamed", streamed)

  try:
    yield SimpleNamespace(
      model_started=model_started,
      model_closing=model_closing,
      model_closed=model_closed,
      allow_model_close=allow_model_close,
      event_body_entered=event_body_entered,
      release_query=release_query,
      streams=streams,
      response_ready=response_ready,
      repository=repository,
    )
  finally:
    allow_model_close.set()
    for stream in streams:
      stream.cancel()
    await asyncio.gather(
      *(stream.run_loop_task for stream in streams), return_exceptions=True
    )


@pytest.mark.asyncio
async def test_lease_failure_waits_for_real_sdk_model_to_stop(
  monkeypatch, streaming_run
):
  model_closed_at_settlement = []

  async def renew(*args, **kwargs):
    await streaming_run.event_body_entered.wait()
    await streaming_run.model_started.wait()
    raise PoolTimeout("lease capacity timeout")

  async def settle(*args, **kwargs):
    model_closed_at_settlement.append(streaming_run.model_closed.is_set())

  monkeypatch.setattr(consumer, "_renew_lease", renew)
  settlement = AsyncMock(side_effect=settle)
  monkeypatch.setattr(consumer, "settle_run_failure", settlement)
  await asyncio.wait_for(
    consumer._execute_guarded("run-test", load_config(), "runtime-test"), timeout=2
  )

  settlement.assert_awaited_once()
  assert model_closed_at_settlement == [True]
  assert streaming_run.model_closed.is_set()
  assert streaming_run.streams[0].run_loop_task.done()


@pytest.mark.asyncio
async def test_run_timeout_stops_sdk_model_during_event_body(streaming_run):
  config = replace(load_config(), run_timeout_seconds=0.2)
  with pytest.raises(TimeoutError):
    await asyncio.wait_for(
      runner.execute_run("run-test", config, instance_id="runtime-test"), timeout=2
    )
  assert streaming_run.model_started.is_set()
  assert streaming_run.model_closed.is_set()
  assert streaming_run.streams[0].run_loop_task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["event_body", "event_queue"])
@pytest.mark.parametrize("guarded", [False, True])
async def test_repeated_cancellation_waits_for_sdk_cleanup(
  streaming_run, cancel_at, guarded
):
  streaming_run.allow_model_close.clear()
  task = asyncio.create_task(
    consumer._execute_guarded("run-test", load_config(), "runtime-test")
    if guarded
    else runner.execute_run("run-test", load_config(), instance_id="runtime-test")
  )
  try:
    await asyncio.wait_for(streaming_run.event_body_entered.wait(), timeout=1)
    await asyncio.wait_for(streaming_run.model_started.wait(), timeout=1)
    if cancel_at == "event_queue":
      streaming_run.release_query.set()
      async with asyncio.timeout(1):
        while not streaming_run.streams[0]._waiting_on_event_queue:
          await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait_for(streaming_run.model_closing.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done(), "caller left before model cleanup completed"
    streaming_run.allow_model_close.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, timeout=1)
    assert streaming_run.model_closed.is_set()
    assert streaming_run.streams[0].run_loop_task.done()
  finally:
    streaming_run.allow_model_close.set()
    if not task.done():
      task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "error", [runner.AssistantRunCancelled, PoolTimeout, ValueError]
)
async def test_event_body_failure_stops_model_and_preserves_error(
  monkeypatch, streaming_run, error
):
  failure = error("original failure")

  async def fail_query(_run_id):
    await streaming_run.model_started.wait()
    raise failure

  monkeypatch.setattr(runner, "_run_was_cancelled", fail_query)
  with pytest.raises(error) as caught:
    await asyncio.wait_for(
      runner.execute_run("run-test", load_config(), instance_id="runtime-test"),
      timeout=2,
    )
  assert caught.value is failure
  assert streaming_run.model_closed.is_set()
  assert streaming_run.streams[0].run_loop_task.done()
  assert streaming_run.streams[0]._active_stream_consumers == 0


@pytest.mark.asyncio
async def test_normal_sdk_completion_is_not_cancelled(streaming_run):
  streaming_run.release_query.set()
  streaming_run.response_ready.set()
  await asyncio.wait_for(
    runner.execute_run("run-test", load_config(), instance_id="runtime-test"), timeout=2
  )
  result = streaming_run.streams[0]
  assert result.final_output == "done"
  assert result._cancel_mode == "none"
  assert result.run_loop_task.done()
  assert streaming_run.model_closed.is_set()
  streaming_run.repository.complete_run.assert_awaited_once()
  assert (
    streaming_run.repository.complete_run.await_args.kwargs["expected_lease_owner"]
    == "runtime-test"
  )
