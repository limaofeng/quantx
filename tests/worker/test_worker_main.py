from __future__ import annotations

import sys

from quantx_worker import main as worker_main


def test_worker_bootstrap_uses_configured_pool_and_stable_name(monkeypatch) -> None:
  commands: list[tuple[list[str], bool]] = []

  def run(command: list[str], *, check: bool = True) -> int:
    commands.append((command, check))
    return 0

  monkeypatch.setenv("PREFECT_WORKER_POOL", "quantx-pool")
  monkeypatch.setenv("PREFECT_WORKER_NAME", "quantx-macos-dev")
  monkeypatch.setattr(worker_main, "run", run)

  worker_main.main()

  assert commands[0] == (
    [sys.executable, "-m", "prefect", "work-pool", "inspect", "quantx-pool"],
    False,
  )
  assert commands[-1] == (
    [
      sys.executable,
      "-m",
      "prefect",
      "worker",
      "start",
      "--pool",
      "quantx-pool",
      "--name",
      "quantx-macos-dev",
    ],
    True,
  )
