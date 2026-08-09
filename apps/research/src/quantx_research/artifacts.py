"""Reproducible filesystem artifacts for offline research runs."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
import sys
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel


def json_value(value: Any) -> Any:
  """Convert common scientific Python values into deterministic JSON values."""
  if isinstance(value, BaseModel):
    return json_value(value.model_dump(mode="json"))
  if dataclasses.is_dataclass(value) and not isinstance(value, type):
    return json_value(dataclasses.asdict(value))
  if isinstance(value, Mapping):
    return {
      str(key): json_value(item)
      for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
  if isinstance(value, (list, tuple, set)):
    return [json_value(item) for item in value]
  if isinstance(value, Enum):
    return json_value(value.value)
  if isinstance(value, (datetime, date, pd.Timestamp)):
    return value.isoformat()
  if isinstance(value, Path):
    return value.as_posix()
  if isinstance(value, np.ndarray):
    return json_value(value.tolist())
  if isinstance(value, np.generic):
    return json_value(value.item())
  if isinstance(value, float) and not math.isfinite(value):
    return None
  return value


def canonical_json(value: Any) -> str:
  return json.dumps(
    json_value(value),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
  )


def fingerprint(value: Any) -> str:
  return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def directory_fingerprint(root: Path) -> str:
  """Hash source-relative paths and contents, excluding generated Python files."""
  digest = hashlib.sha256()
  excluded_parts = {"__pycache__", "build", "dist"}
  for path in sorted(item for item in root.rglob("*") if item.is_file()):
    relative = path.relative_to(root)
    if excluded_parts.intersection(relative.parts):
      continue
    if path.suffix in {".pyc", ".pyo"} or any(
      part.endswith(".egg-info") for part in relative.parts
    ):
      continue
    digest.update(relative.as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(file_sha256(path).encode("ascii"))
    digest.update(b"\0")
  return digest.hexdigest()


def write_json(path: Path, value: Any) -> Path:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    json.dumps(
      json_value(value),
      ensure_ascii=False,
      indent=2,
      sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
  )
  return path


def write_yaml(path: Path, value: Any) -> Path:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    yaml.safe_dump(
      json_value(value),
      allow_unicode=True,
      sort_keys=False,
    ),
    encoding="utf-8",
  )
  return path


def git_state(repo_root: Path) -> dict[str, Any]:
  def _run(*args: str) -> str:
    result = subprocess.run(
      ["git", *args],
      cwd=repo_root,
      check=True,
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
    )
    return result.stdout.strip()

  try:
    commit = _run("rev-parse", "HEAD")
    status = _run("status", "--porcelain", "--untracked-files=normal")
    return {
      "commit": commit,
      "dirty": bool(status),
      "status_fingerprint": fingerprint(status.splitlines()),
    }
  except (OSError, subprocess.CalledProcessError) as exc:
    return {
      "commit": None,
      "dirty": None,
      "status_fingerprint": None,
      "warning": f"无法读取 Git 状态: {exc}",
    }


def create_run_directory(
  output_root: Path,
  study_id: str,
  version: str,
  config_fingerprint: str,
  *,
  now: datetime | None = None,
) -> Path:
  timestamp = (
    (now or datetime.now(timezone.utc))
    .astimezone(ZoneInfo("Asia/Shanghai"))
    .strftime("%Y%m%d-%H%M%S")
  )
  run_dir = (
    output_root / f"{study_id}-{version}" / f"{timestamp}-{config_fingerprint[:8]}"
  )
  for attempt in range(100):
    candidate = (
      run_dir if attempt == 0 else run_dir.with_name(f"{run_dir.name}-{attempt:02d}")
    )
    try:
      candidate.mkdir(parents=True, exist_ok=False)
      return candidate
    except FileExistsError:
      continue
  raise FileExistsError(f"无法分配唯一研究运行目录: {run_dir}")


def runtime_metadata() -> dict[str, Any]:
  dependency_names = (
    "jinja2",
    "numpy",
    "pandas",
    "pyarrow",
    "pydantic",
    "pyyaml",
    "quantx-infrastructure",
  )
  dependencies: dict[str, str | None] = {}
  for name in dependency_names:
    try:
      dependencies[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
      dependencies[name] = None
  return {
    "python": sys.version.split()[0],
    "platform": platform.platform(),
    "pandas": pd.__version__,
    "numpy": np.__version__,
    "dependencies": dependencies,
  }


def artifact_index(run_dir: Path) -> list[dict[str, Any]]:
  artifacts: list[dict[str, Any]] = []
  for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
    if path.name == "manifest.json":
      continue
    artifacts.append(
      {
        "path": path.relative_to(run_dir).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
      }
    )
  return artifacts
