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
from quantx_ai_runtime import database
from quantx_ai_runtime.config import load_config
from quantx_ai_runtime.runtime import consumer, runner
from quantx_ai_runtime.tools import registry as tool_registry
from quantx_application.assistant.contracts import AssistantExecutionContext
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncSession


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
    runner,
    "_execution_context",
    AsyncMock(return_value=SimpleNamespace(run_id="run-test")),
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


@pytest.fixture
async def tool_streaming_run(monkeypatch, streaming_run):
  """Real SDK function calls and session cleanup, with no SQL or provider I/O."""
  codes = ("600000.SH", "000001.SZ")
  started = {code: asyncio.Event() for code in codes}
  closing = {code: asyncio.Event() for code in codes}
  closed = {code: asyncio.Event() for code in codes}
  allow_query = asyncio.Event()
  allow_close = asyncio.Event()
  tool_tasks = set()
  contexts = []
  options = SimpleNamespace(approval=False)
  admission = database.DatabaseAdmission(3, timeout_seconds=0.05)

  class ToolSession(AsyncSession):
    tool_code = None

    async def get(self, _entity, code, **kwargs):
      self.tool_code = code
      tool_tasks.add(asyncio.current_task())
      started[code].set()
      await allow_query.wait()
      return SimpleNamespace(
        name="test instrument",
        market="SH",
        type="STOCK",
        pre_close=10,
        up_stop_price=11,
        down_stop_price=9,
        is_trading=True,
        updated_at=None,
      )

    async def merge(self, instance, **kwargs):
      return instance

    async def close(self):
      if self.tool_code is not None:
        closing[self.tool_code].set()
        await allow_close.wait()
      await super().close()
      if self.tool_code is not None:
        closed[self.tool_code].set()

  class ToolModel(Model):
    calls = 0

    async def get_response(self, *args, **kwargs):
      raise AssertionError("expected a streaming model call")

    async def stream_response(self, *args, **kwargs):
      self.calls += 1
      if self.calls == 1:
        output = [
          {
            "id": f"item-{index}",
            "type": "function_call",
            "call_id": f"call-{index}",
            "name": "get_instrument_snapshot",
            "arguments": '{"code": "' + code + '"}',
          }
          for index, code in enumerate(codes)
        ]
        if options.approval:
          output.append(
            {
              "id": "approval-item",
              "type": "function_call",
              "call_id": "approval-call",
              "name": "create_backtest_rerun_task",
              "arguments": (
                '{"strategy_run_id":"strategy-test",'
                '"backtest_start_time":null,"backtest_end_time":null}'
              ),
            }
          )
      else:
        output = [
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
        ]
      yield ResponseCompletedEvent(
        type="response.completed",
        sequence_number=self.calls,
        response=Response(
          id=f"response-{self.calls}",
          object="response",
          created_at=0,
          model="local-tools-test",
          status="completed",
          parallel_tool_calls=True,
          tool_choice="auto",
          tools=[],
          output=output,
        ),
      )

  def build_agent(context, **kwargs):
    contexts.append(context)
    context.event_writer = SimpleNamespace(append=AsyncMock())
    return Agent(
      name="Local tool cancellation test",
      model=ToolModel(),
      tools=tool_registry.build_tools(
        context,
        allowed_names=frozenset(
          {"get_instrument_snapshot", "create_backtest_rerun_task"}
        ),
      ),
    )

  async def finish_tool_call(call, **kwargs):
    return call

  monkeypatch.setattr(database, "database_admission", admission)
  monkeypatch.setattr(database, "AsyncSessionLocal", ToolSession)
  monkeypatch.setattr(
    tool_registry,
    "AiAssistantRepository",
    lambda db: SimpleNamespace(
      create_tool_call=AsyncMock(return_value=SimpleNamespace(id="tool-test")),
      finish_tool_call=AsyncMock(side_effect=finish_tool_call),
    ),
  )
  monkeypatch.setattr(runner, "build_agent", build_agent)
  monkeypatch.setattr(
    runner,
    "_execution_context",
    AsyncMock(
      return_value=AssistantExecutionContext(
        user_id="user-test",
        permissions=frozenset({"market:read"}),
        authorized_account_ids=(),
        thread_id="thread-test",
        run_id="run-test",
        request_id="request-test",
      )
    ),
  )

  async def wait_for_all(events):
    async with asyncio.timeout(2):
      await asyncio.gather(*(event.wait() for event in events.values()))

  try:
    yield SimpleNamespace(
      started=started,
      closing=closing,
      closed=closed,
      allow_query=allow_query,
      allow_close=allow_close,
      tasks=tool_tasks,
      contexts=contexts,
      options=options,
      admission=admission,
      wait_for_all=wait_for_all,
      stream=streaming_run,
    )
  finally:
    allow_query.set()
    allow_close.set()
    for task in tool_tasks:
      if not task.done() and not task.cancelling():
        task.cancel()
    await asyncio.gather(*tool_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_lease_failure_joins_sdk_tools_before_settlement(
  monkeypatch, tool_streaming_run
):
  harness = tool_streaming_run
  settled_after_close = []
  failure = PoolTimeout("lease capacity timeout")

  async def renew(*args, **kwargs):
    await harness.wait_for_all(harness.started)
    raise failure

  async def settle(run_id, exc, **kwargs):
    settled_after_close.append(
      (exc, all(event.is_set() for event in harness.closed.values()))
    )

  monkeypatch.setattr(consumer, "_renew_lease", renew)
  settlement = AsyncMock(side_effect=settle)
  monkeypatch.setattr(consumer, "settle_run_failure", settlement)
  task = asyncio.create_task(
    consumer._execute_guarded("run-test", load_config(), "runtime-test")
  )
  try:
    await harness.wait_for_all(harness.closing)
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done, "run settled while its SDK tool sessions were still closing"
    settlement.assert_not_awaited()
    assert harness.admission._work_slots._value == 0
    with pytest.raises(database.DatabaseCapacityTimeout):
      async with database.database_session():
        pytest.fail("replacement work stole heartbeat capacity")
    async with database.database_session(heartbeat=True):
      assert harness.admission._all_slots._value == 0

    harness.allow_close.set()
    await asyncio.wait_for(task, timeout=2)
    assert settled_after_close == [(failure, True)]
    assert len(harness.tasks) == 2
    assert all(tool.done() for tool in harness.tasks)
    assert not harness.contexts[0]._tool_tasks
    assert harness.admission._work_slots._value == 2
    assert harness.admission._all_slots._value == 3
  finally:
    harness.allow_close.set()
    if not task.done():
      task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["event_body", "event_queue"])
@pytest.mark.parametrize("guarded", [False, True])
async def test_repeated_cancellation_joins_parallel_sdk_tools(
  tool_streaming_run, cancel_at, guarded
):
  harness = tool_streaming_run
  task = asyncio.create_task(
    consumer._execute_guarded("run-test", load_config(), "runtime-test")
    if guarded
    else runner.execute_run("run-test", load_config(), instance_id="runtime-test")
  )
  try:
    await harness.wait_for_all(harness.started)
    if cancel_at == "event_queue":
      harness.stream.release_query.set()
      async with asyncio.timeout(2):
        while not harness.stream.streams[0]._waiting_on_event_queue:
          await asyncio.sleep(0)
    task.cancel()
    await harness.wait_for_all(harness.closing)
    cancellation_counts = {tool: tool.cancelling() for tool in harness.tasks}
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done, "repeat cancellation detached tool cleanup"
    assert harness.admission._work_slots._value == 0
    assert len(harness.contexts[0]._tool_tasks) == 2
    harness.allow_close.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, timeout=2)
    assert all(event.is_set() for event in harness.closed.values())
    assert all(tool.done() for tool in harness.tasks)
    assert {tool: tool.cancelling() for tool in harness.tasks} == cancellation_counts
    assert not harness.contexts[0]._tool_tasks
    assert harness.admission._all_slots._value == 3
    harness.stream.repository.complete_run.assert_not_awaited()
  finally:
    harness.allow_close.set()
    if not task.done():
      task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_timeout_joins_sdk_tools_before_propagating(tool_streaming_run):
  harness = tool_streaming_run
  task = asyncio.create_task(
    runner.execute_run(
      "run-test",
      replace(load_config(), run_timeout_seconds=0.2),
      instance_id="runtime-test",
    )
  )
  try:
    await harness.wait_for_all(harness.started)
    await harness.wait_for_all(harness.closing)
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done
    harness.allow_close.set()
    with pytest.raises(TimeoutError):
      await asyncio.wait_for(task, timeout=2)
    assert all(tool.done() for tool in harness.tasks)
    assert all(event.is_set() for event in harness.closed.values())
    assert harness.admission._all_slots._value == 3
  finally:
    harness.allow_close.set()
    if not task.done():
      task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "error", [runner.AssistantRunCancelled, PoolTimeout, ValueError]
)
async def test_event_failure_joins_sdk_tools_and_preserves_error(
  monkeypatch, tool_streaming_run, error
):
  harness = tool_streaming_run
  failure = error("original failure")

  async def fail_query(_run_id):
    await harness.wait_for_all(harness.started)
    raise failure

  monkeypatch.setattr(runner, "_run_was_cancelled", fail_query)
  task = asyncio.create_task(
    runner.execute_run("run-test", load_config(), instance_id="runtime-test")
  )
  try:
    await harness.wait_for_all(harness.closing)
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done
    harness.allow_close.set()
    with pytest.raises(error) as caught:
      await asyncio.wait_for(task, timeout=2)
    assert caught.value is failure
    assert all(tool.done() for tool in harness.tasks)
    assert not harness.contexts[0]._tool_tasks
    assert harness.admission._all_slots._value == 3
  finally:
    harness.allow_close.set()
    if not task.done():
      task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", [False, True])
async def test_completed_sdk_tools_are_not_cancelled(
  monkeypatch, tool_streaming_run, approval
):
  harness = tool_streaming_run
  harness.options.approval = approval
  harness.allow_query.set()
  harness.stream.release_query.set()
  repository = harness.stream.repository
  run = repository.get_run.return_value
  repository.finish_run = AsyncMock(return_value=run)
  persist_approval = AsyncMock(return_value={"test": "approval-state"})
  monkeypatch.setattr(runner, "_persist_approval_interruptions", persist_approval)
  task = asyncio.create_task(
    runner.execute_run("run-test", load_config(), instance_id="runtime-test")
  )
  try:
    await harness.wait_for_all(harness.closing)
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done
    repository.complete_run.assert_not_awaited()
    repository.finish_run.assert_not_awaited()
    harness.allow_close.set()
    await asyncio.wait_for(task, timeout=2)
    stream = harness.stream.streams[0]
    assert stream._cancel_mode == "none"
    assert stream.run_loop_task.done()
    assert all(tool.done() and not tool.cancelled() for tool in harness.tasks)
    assert all(event.is_set() for event in harness.closed.values())
    assert not harness.contexts[0]._tool_tasks
    assert harness.contexts[0].tool_call_count == 2
    assert harness.admission._all_slots._value == 3
    if approval:
      assert len(stream.interruptions) == 1
      persist_approval.assert_awaited_once()
      repository.complete_run.assert_not_awaited()
      assert repository.finish_run.await_args.kwargs["status"] == "WAITING_APPROVAL"
    else:
      assert stream.final_output == "done"
      assert not stream.interruptions
      repository.complete_run.assert_awaited_once()
      persist_approval.assert_not_awaited()
  finally:
    harness.allow_close.set()
    if not task.done():
      task.cancel()
    await asyncio.gather(task, return_exceptions=True)
