import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
  "command,selectors",
  [
    ("up", ["-TrainerRunId", "run"]),
    ("logs", ["-TrainerJobId", "job"]),
    ("logs", ["-TrainerOwner", "owner"]),
    (
      "logs",
      ["-TrainerRunId", "run", "-TrainerJobId", "job", "-TrainerOwner", "owner"],
    ),
  ],
)
def test_powershell_rejects_invalid_log_selectors_before_launch(command, selectors):
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  result = subprocess.run(
    [
      powershell,
      "-NoProfile",
      "-File",
      str(ROOT / "ops/quantx.ps1"),
      command,
      "-Component",
      "trainer",
      "-Environment",
      "dev",
      *selectors,
    ],
    capture_output=True,
    text=True,
    timeout=20,
  )
  assert result.returncode != 0
  assert "require" in result.stderr and "logs" in result.stderr
  assert "absolute" not in result.stderr


@pytest.mark.parametrize(
  "command,selectors",
  [
    ("up", ["--trainer-run-id", "run"]),
    ("logs", ["--trainer-job-id", "job"]),
    ("logs", ["--trainer-owner", "owner"]),
    (
      "logs",
      [
        "--trainer-run-id",
        "run",
        "--trainer-job-id",
        "job",
        "--trainer-owner",
        "owner",
      ],
    ),
  ],
)
def test_macos_rejects_invalid_log_selectors_before_launch(
  command, selectors, monkeypatch, capsys
):
  monkeypatch.syspath_prepend(str(ROOT / "ops"))
  spec = importlib.util.spec_from_file_location(
    "invalid_trainer_ops", ROOT / "ops/macos_runtime.py"
  )
  runtime = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(runtime)
  monkeypatch.setattr(
    sys, "argv", ["quantx.sh", command, "--component", "trainer", *selectors]
  )
  with pytest.raises(SystemExit) as stopped:
    runtime.main()
  assert stopped.value.code == 2
  assert "Trainer" in capsys.readouterr().err


@pytest.mark.skipif(
  os.name == "nt", reason="POSIX executable fixture for PowerShell routing"
)
@pytest.mark.parametrize(
  "command",
  [
    "up",
    "down",
    "status",
    "logs",
    "doctor",
    "drain",
    "resume",
    "logs-run",
    "logs-preparation",
  ],
)
def test_powershell_routes_only_to_explicit_trainer_python(tmp_path, command):
  selector = command.split("-", 1)[1] if "-" in command else None
  command = command.split("-", 1)[0]
  extras = (
    ["-TrainerRunId", "run-1"]
    if selector == "run"
    else ["-TrainerJobId", "job", "-TrainerOwner", "owner"]
    if selector
    else []
  )
  powershell = shutil.which("pwsh")
  assert powershell is not None
  python = tmp_path / "trainer python"
  captured = tmp_path / "arguments.json"
  python.write_text(
    f"#!{sys.executable}\nimport json,sys\n"
    f"from pathlib import Path\nPath({str(captured)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    "raise SystemExit(3)\n"
  )
  python.chmod(0o700)
  config = tmp_path / "trainer config.toml"
  config.write_text("fixture")
  result = subprocess.run(
    [
      powershell,
      "-NoProfile",
      "-File",
      str(ROOT / "ops/quantx.ps1"),
      command,
      "-Component",
      "trainer",
      "-Environment",
      "dev",
      "-TrainerPython",
      str(python),
      "-TrainerConfig",
      str(config),
      "-Tail",
      "7",
      *extras,
    ],
    capture_output=True,
    text=True,
    timeout=20,
  )
  assert result.returncode == 3, result.stderr
  expected = [
    "-I",
    "-m",
    "quantx_trainer.main",
    "preflight" if command == "doctor" else command,
    "--config",
    str(config),
  ]
  if command == "logs":
    expected += ["--lines", "7"]
  if selector == "run":
    expected += ["--run-id", "run-1"]
  elif selector:
    expected += ["--job-id", "job", "--owner", "owner"]
  assert json.loads(captured.read_text()) == expected


@pytest.mark.parametrize(
  "extra",
  [[], ["-Environment", "production"], ["-Environment", "dev", "-Mode", "live"]],
)
def test_powershell_rejects_production_or_trading_mode_before_dispatch(extra):
  powershell = shutil.which("pwsh") or shutil.which("powershell")
  assert powershell is not None
  result = subprocess.run(
    [
      powershell,
      "-NoProfile",
      "-File",
      str(ROOT / "ops/quantx.ps1"),
      "up",
      "-Component",
      "trainer",
      *extra,
    ],
    capture_output=True,
    text=True,
    timeout=20,
  )
  assert result.returncode != 0
  assert "explicit -Environment dev" in result.stderr or "trading mode" in result.stderr


@pytest.mark.parametrize(
  "command",
  [
    "up",
    "down",
    "status",
    "logs",
    "doctor",
    "drain",
    "resume",
    "logs-run",
    "logs-preparation",
  ],
)
def test_macos_trainer_route_precedes_shared_runtime_state(
  tmp_path, monkeypatch, command
):
  selector = command.split("-", 1)[1] if "-" in command else None
  command = command.split("-", 1)[0]
  extras = (
    ["--trainer-run-id", "run-1"]
    if selector == "run"
    else ["--trainer-job-id", "job", "--trainer-owner", "owner"]
    if selector
    else []
  )
  monkeypatch.syspath_prepend(str(ROOT / "ops"))
  spec = importlib.util.spec_from_file_location(
    "trainer_ops_fixture", ROOT / "ops/macos_runtime.py"
  )
  runtime = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(runtime)
  python = tmp_path / "python"
  python.touch()
  config = tmp_path / "config.toml"
  config.touch()
  prefix = tmp_path / "quantx"
  (prefix / "conda-meta").mkdir(parents=True)
  monkeypatch.setattr(runtime, "RUNTIME", tmp_path / "must-not-create")
  monkeypatch.setattr(sys, "platform", "darwin")
  monkeypatch.setattr(sys, "prefix", str(prefix))
  monkeypatch.setattr(
    sys,
    "argv",
    [
      "quantx.sh",
      command,
      "--component",
      "trainer",
      "--trainer-python",
      str(python),
      "--trainer-config",
      str(config),
      "--tail",
      "7",
      *extras,
    ],
  )
  calls = []
  monkeypatch.setattr(
    runtime.subprocess, "call", lambda arguments: calls.append(arguments) or 3
  )
  with pytest.raises(SystemExit) as stopped:
    runtime.main()
  assert stopped.value.code == 3
  expected = [
    str(python),
    "-I",
    "-m",
    "quantx_trainer.main",
    "preflight" if command == "doctor" else command,
    "--config",
    str(config),
  ]
  if command == "logs":
    expected += ["--lines", "7"]
  if selector == "run":
    expected += ["--run-id", "run-1"]
  elif selector:
    expected += ["--job-id", "job", "--owner", "owner"]
  assert calls == [expected]
  assert not runtime.RUNTIME.exists()
