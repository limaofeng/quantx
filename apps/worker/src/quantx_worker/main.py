"""Non-interactive Prefect worker bootstrap."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DEFAULT_POOL = "quantx-pool"


def run(command: list[str], *, check: bool = True) -> int:
  completed = subprocess.run(command, check=False)
  if check and completed.returncode:
    raise SystemExit(completed.returncode)
  return completed.returncode


def main() -> None:
  pool = os.environ.get("PREFECT_WORKER_POOL", DEFAULT_POOL).strip()
  if not pool:
    raise SystemExit("PREFECT_WORKER_POOL must not be empty")
  prefect_file = Path(__file__).resolve().parents[2] / "prefect.yaml"
  exists = (
    run(
      [sys.executable, "-m", "prefect", "work-pool", "inspect", pool],
      check=False,
    )
    == 0
  )
  if not exists:
    raise SystemExit(
      f"Required external Prefect work pool {pool!r} does not exist"
    )
  run(
    [
      sys.executable,
      "-m",
      "prefect",
      "deploy",
      "--prefect-file",
      str(prefect_file),
      "--all",
    ]
  )
  run([sys.executable, "-m", "prefect", "worker", "start", "--pool", pool])


if __name__ == "__main__":
  main()
