import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkout(tmp_path):
  checkout = tmp_path / "code"
  (checkout / "ops" / "trainer").mkdir(parents=True)
  for name in ("quantx.ps1", "trainer/bootstrap.ps1"):
    shutil.copy2(ROOT / "ops" / name, checkout / "ops" / name)
  return checkout


def invoke(checkout, *arguments):
  return subprocess.run(
    [
      shutil.which("pwsh") or "powershell",
      "-NoProfile",
      "-NonInteractive",
      "-File",
      str(checkout / "ops" / "quantx.ps1"),
      *arguments,
    ],
    capture_output=True,
    text=True,
    timeout=15,
  )


@pytest.mark.parametrize(
  "arguments",
  [
    ["bootstrap", "-Component", "trainer"],
    ["up", "-Component", "trainer", "-Environment", "dev"],
    ["bootstrap", "-Component", "trainer", "-Environment", "dev"],
    ["status", "-CondaExecutable", "unused"],
  ],
)
def test_invalid_trainer_invocation_cannot_enter_production_lifecycle(
  checkout, arguments
):
  result = invoke(checkout, *arguments)
  assert result.returncode != 0
  assert not (checkout / ".runtime").exists()


def test_partial_conda_prefix_is_preserved_without_reinstallation(checkout, tmp_path):
  conda = tmp_path / "miniconda3"
  (conda / "Scripts").mkdir(parents=True)
  (conda / "conda-meta").mkdir()
  executable = conda / "Scripts" / "conda.exe"
  executable.write_text("must never execute this file")
  prefix = conda / "envs" / "quantx-train"
  prefix.mkdir(parents=True)
  marker = prefix / "diagnostic.txt"
  marker.write_text("unfinished installation evidence")
  result = invoke(
    checkout,
    "bootstrap",
    "-Environment",
    "dev",
    "-Component",
    "trainer",
    "-CondaExecutable",
    str(executable),
  )
  assert result.returncode != 0
  assert "prefix is incomplete" in result.stderr
  assert marker.read_text() == "unfinished installation evidence"
  assert not (checkout / ".runtime").exists()


def test_conda_prefix_cannot_alias_existing_production_environment(checkout, tmp_path):
  conda = tmp_path / "miniconda3"
  (conda / "Scripts").mkdir(parents=True)
  (conda / "conda-meta").mkdir()
  executable = conda / "Scripts" / "conda.exe"
  executable.touch()
  production = conda / "envs" / "quantx"
  production.mkdir(parents=True)
  (conda / "envs" / "quantx-train").symlink_to(production, target_is_directory=True)
  result = invoke(
    checkout,
    "bootstrap",
    "-Environment",
    "dev",
    "-Component",
    "trainer",
    "-CondaExecutable",
    str(executable),
  )
  assert result.returncode != 0
  assert "must not be links or junctions" in result.stderr
  assert not list(production.iterdir())
