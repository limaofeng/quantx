import asyncio

import pytest
from quantx_ai_runtime import config as runtime_config
from quantx_ai_runtime.config import (
  AiRuntimeConfig,
  config_refresh_loop,
  runtime_status,
)


def _config(**overrides) -> AiRuntimeConfig:
  values = {
    "version": 4,
    "source": "DATABASE_OVERRIDE",
    "enabled": True,
    "api_key": "secret",
    "model": "gpt-current",
    "max_concurrent_runs": 4,
    "max_turns": 12,
    "max_tool_calls": 8,
    "run_timeout_seconds": 300,
    "lease_seconds": 60,
    "tracing_enabled": False,
  }
  values.update(overrides)
  return AiRuntimeConfig(**values)


def test_run_snapshot_keeps_execution_limits_but_not_global_concurrency() -> None:
  current = _config(max_concurrent_runs=2, max_turns=30)

  run = current.for_run(
    version=3,
    snapshot={
      "model": "gpt-snapshot",
      "maxTurns": 10,
      "maxToolCalls": 6,
      "runTimeoutSeconds": 120,
    },
  )

  assert run.version == 3
  assert run.model == "gpt-snapshot"
  assert run.max_turns == 10
  assert run.max_tool_calls == 6
  assert run.run_timeout_seconds == 120
  assert run.max_concurrent_runs == 2


def test_runtime_status_distinguishes_disabled_unconfigured_and_unavailable() -> None:
  assert runtime_status(_config(enabled=False), dependencies_available=True) == (
    "disabled"
  )
  assert runtime_status(
    _config(enabled=False, api_key=""), dependencies_available=False
  ) == ("disabled")
  assert runtime_status(_config(api_key=""), dependencies_available=True) == (
    "unconfigured"
  )
  assert runtime_status(_config(), dependencies_available=False) == "unavailable"
  assert runtime_status(_config(), dependencies_available=True) == "ready"


@pytest.mark.asyncio
async def test_config_refresh_polls_database_when_redis_wake_is_unavailable(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  stopped = asyncio.Event()

  class Controller:
    refresh_count = 0

    async def refresh(self):
      self.refresh_count += 1
      stopped.set()
      return _config(version=5)

  async def unavailable_subscription(_channel: str):
    raise ConnectionError("redis unavailable")

  controller = Controller()
  monkeypatch.setattr(
    runtime_config.redis_pubsub,
    "open_subscription",
    unavailable_subscription,
  )

  await config_refresh_loop(stopped, controller=controller)

  assert controller.refresh_count == 1
