from __future__ import annotations

import asyncio
import os
import subprocess
import time

import pytest
from quantx_qmt_agent.process_watchdog import (
  WATCHDOG_STALE_TIMEOUT_SECONDS,
  AgentProcessWatchdog,
  monitor_parent_process,
)


class _FakeProcess:
  def __init__(self) -> None:
    self.return_code: int | None = None
    self.terminated = False
    self.killed = False

  def poll(self):
    return self.return_code

  def terminate(self) -> None:
    self.terminated = True
    self.return_code = 0

  def kill(self) -> None:
    self.killed = True
    self.return_code = -1

  def wait(self, timeout=None):
    del timeout
    return self.return_code


def test_external_watchdog_timeout_exceeds_native_timeout_grace() -> None:
  assert WATCHDOG_STALE_TIMEOUT_SECONDS >= 75


def test_monitor_force_terminates_stale_parent_without_real_kill(tmp_path) -> None:
  heartbeat = tmp_path / "agent.heartbeat"
  heartbeat.touch()
  modified_at = heartbeat.stat().st_mtime
  terminated: list[tuple[int, str]] = []

  def terminate(process_id: int, identity: str) -> bool:
    terminated.append((process_id, identity))
    return True

  outcome = monitor_parent_process(
    parent_pid=1234,
    parent_identity="process-identity-1",
    heartbeat_path=heartbeat,
    stale_timeout_seconds=90,
    poll_interval_seconds=5,
    is_parent_alive=lambda _pid: True,
    read_parent_identity=lambda _pid: "process-identity-1",
    terminate_parent=terminate,
    wall_clock=lambda: modified_at + 91,
    sleeper=lambda _seconds: pytest.fail("stale parent should not sleep"),
  )

  assert outcome == "heartbeat-timeout"
  assert terminated == [(1234, "process-identity-1")]


def test_monitor_exits_without_killing_when_parent_is_gone(tmp_path) -> None:
  terminated: list[tuple[int, str]] = []

  outcome = monitor_parent_process(
    parent_pid=1234,
    parent_identity="process-identity-1",
    heartbeat_path=tmp_path / "missing.heartbeat",
    stale_timeout_seconds=90,
    poll_interval_seconds=5,
    is_parent_alive=lambda _pid: False,
    terminate_parent=lambda process_id, identity: bool(
      terminated.append((process_id, identity))
    ),
  )

  assert outcome == "parent-exited"
  assert terminated == []


def test_monitor_never_kills_reused_parent_pid(tmp_path) -> None:
  heartbeat = tmp_path / "agent.heartbeat"
  heartbeat.touch()
  modified_at = heartbeat.stat().st_mtime
  identities = iter(("process-identity-1", "replacement-process"))
  terminated: list[tuple[int, str]] = []

  outcome = monitor_parent_process(
    parent_pid=1234,
    parent_identity="process-identity-1",
    heartbeat_path=heartbeat,
    stale_timeout_seconds=90,
    poll_interval_seconds=5,
    is_parent_alive=lambda _pid: True,
    read_parent_identity=lambda _pid: next(identities),
    terminate_parent=lambda process_id, identity: bool(
      terminated.append((process_id, identity))
    ),
    wall_clock=lambda: modified_at + 91,
    sleeper=lambda _seconds: pytest.fail("stale parent should not sleep"),
  )

  assert outcome == "parent-exited"
  assert terminated == []


def test_watchdog_spawns_hidden_secret_free_child_and_cleans_up(tmp_path) -> None:
  process = _FakeProcess()
  invocations = []

  def popen(command, **kwargs):
    invocations.append((command, kwargs))
    return process

  heartbeat = tmp_path / "watchdog" / "agent.heartbeat"
  watchdog = AgentProcessWatchdog(
    heartbeat,
    parent_pid=4321,
    popen_factory=popen,
    process_identity_reader=lambda _pid: "windows:123456",
  )

  watchdog.start()

  assert heartbeat.exists()
  assert len(invocations) == 1
  command, options = invocations[0]
  assert command[1:3] == ["-m", "quantx_qmt_agent.process_watchdog"]
  assert command[3:] == [
    "--monitor",
    "--parent-pid",
    "4321",
    "--parent-identity",
    "windows:123456",
    "--heartbeat-path",
    str(heartbeat),
    "--stale-timeout-seconds",
    "90.0",
    "--poll-interval-seconds",
    "5.0",
  ]
  assert options["stdin"] is subprocess.DEVNULL
  assert options["stdout"] is subprocess.DEVNULL
  assert options["stderr"] is subprocess.DEVNULL
  if os.name == "nt":
    assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert options["startupinfo"].wShowWindow == subprocess.SW_HIDE

  watchdog.close()

  assert process.terminated is True
  assert process.killed is False
  assert not heartbeat.exists()


def test_real_watchdog_child_monitors_current_parent_without_exiting(tmp_path) -> None:
  watchdog = AgentProcessWatchdog(
    tmp_path / "real-child.heartbeat",
  )
  watchdog.start()
  process = watchdog._process
  try:
    assert process is not None
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and process.poll() is None:
      time.sleep(0.01)
    assert process.poll() is None
  finally:
    watchdog.close()

  assert not watchdog.heartbeat_path.exists()


@pytest.mark.asyncio
async def test_parent_heartbeat_loop_detects_dead_watchdog_child(tmp_path) -> None:
  process = _FakeProcess()
  watchdog = AgentProcessWatchdog(
    tmp_path / "agent.heartbeat",
    heartbeat_interval_seconds=0.01,
    stale_timeout_seconds=1,
    popen_factory=lambda *_args, **_kwargs: process,
  )
  watchdog.start()
  process.return_code = 1

  with pytest.raises(RuntimeError, match="exited unexpectedly"):
    await watchdog.heartbeat_loop()

  watchdog.close()


@pytest.mark.asyncio
async def test_parent_heartbeat_loop_refreshes_file(tmp_path) -> None:
  process = _FakeProcess()
  heartbeat = tmp_path / "agent.heartbeat"
  watchdog = AgentProcessWatchdog(
    heartbeat,
    heartbeat_interval_seconds=0.01,
    stale_timeout_seconds=1,
    popen_factory=lambda *_args, **_kwargs: process,
  )
  watchdog.start()
  os.utime(heartbeat, (1, 1))
  heartbeat_task = asyncio.create_task(watchdog.heartbeat_loop())
  try:
    await asyncio.sleep(0.02)
    assert heartbeat.stat().st_mtime > 1
  finally:
    heartbeat_task.cancel()
    await asyncio.gather(heartbeat_task, return_exceptions=True)
    watchdog.close()
