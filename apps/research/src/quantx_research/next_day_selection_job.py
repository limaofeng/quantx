"""Isolated subprocess entry point for next-day selection training."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from quantx_infrastructure.training_host_guard import (
  HostAdmissionDenied,
  high_resource_guard,
)

_REQUEST_KEYS = {
  "run_id",
  "run_kind",
  "spec",
  "dataset_directory",
  "output_root",
  "parent_run_directory",
  "progress_file",
  "cancel_file",
  "frozen_test_access_count",
}


def _reject_links(path: Path) -> None:
  absolute = Path(os.path.abspath(path))
  current = Path(absolute.anchor)
  for component in absolute.parts[1:]:
    current /= component
    if _is_link_like(current):
      raise ValueError(f"作业路径不允许符号链接或联接点: {current.name}")


def _is_link_like(path: Path) -> bool:
  if path.is_symlink() or os.path.islink(str(path)):
    return True
  is_junction = getattr(path, "is_junction", None)
  if is_junction is None:
    return False
  try:
    return bool(is_junction())
  except OSError:
    return True


def _finite(value: Any) -> Any:
  if isinstance(value, float) and not math.isfinite(value):
    raise ValueError("请求 JSON 不允许 NaN 或 Infinity")
  if isinstance(value, Mapping):
    return {str(key): _finite(item) for key, item in value.items()}
  if isinstance(value, list):
    return [_finite(item) for item in value]
  return value


def _reject_json_constant(value: str) -> None:
  raise ValueError(f"非法 JSON 常量: {value}")


def load_request(path: str | Path) -> dict[str, Any]:
  request_path = Path(path)
  _reject_links(request_path)
  text = request_path.read_text(encoding="utf-8")
  payload = json.loads(text, parse_constant=_reject_json_constant)
  if not isinstance(payload, dict) or set(payload) != _REQUEST_KEYS:
    raise ValueError("训练 request JSON 字段必须与隔离作业契约完全一致")
  return _finite(payload)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
  _reject_links(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  _reject_links(path.parent)
  fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      json.dump(
        value,
        handle,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
      )
      handle.write("\n")
      handle.flush()
      os.fsync(handle.fileno())
    _reject_links(Path(temporary))
    _reject_links(path)
    _reject_links(path.parent)
    os.replace(temporary, path)
  except BaseException:
    try:
      os.unlink(temporary)
    except OSError:
      pass
    raise


def _progress_writer(progress_file: str | Path):
  path = Path(progress_file)

  def write(progress: Mapping[str, Any]) -> None:
    _atomic_write_json(
      path,
      {
        "phase": str(progress.get("phase", "")),
        "completed_units": int(progress.get("completed_units", 0)),
        "total_units": int(progress.get("total_units", 0)),
        "message": str(progress.get("message", ""))[:300],
      },
    )

  return write


def _cancel_reader(cancel_file: str | Path):
  path = Path(cancel_file)

  def requested() -> bool:
    _reject_links(path)
    if not path.is_file():
      return False
    try:
      value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
      return True
    # The one canonical shape is the affirmative form.  Any malformed or
    # ambiguous control file cancels conservatively as well (fail closed).
    if (
      isinstance(value, dict)
      and set(value) == {"cancel"}
      and value.get("cancel") is True
    ):
      return True
    return True

  return requested


async def run_request(request: Mapping[str, Any]) -> Path:
  from quantx_research.next_day_selection_training import execute_next_day_selection_run

  progress = _progress_writer(request["progress_file"])
  cancel = _cancel_reader(request["cancel_file"])
  progress(
    {
      "phase": "PREFLIGHT",
      "completed_units": 0,
      "total_units": 1,
      "message": "作业已启动",
    }
  )
  return await execute_next_day_selection_run(
    run_kind=request["run_kind"],
    spec=request["spec"],
    dataset_directory=request["dataset_directory"],
    output_root=request["output_root"],
    run_id=request["run_id"],
    parent_run_directory=request.get("parent_run_directory"),
    progress_callback=progress,
    cancel_callback=cancel,
    frozen_test_access_count=int(request.get("frozen_test_access_count", 0)),
  )


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="quantx-research-next-day-selection-job")
  parser.add_argument("--request-file", type=Path, required=True)
  args = parser.parse_args(argv)
  try:
    with high_resource_guard():
      return _execute_job(args.request_file)
  except HostAdmissionDenied as exc:
    print(f"主机训练门禁: {exc}", flush=True)
    return 75


def _execute_job(request_file: Path) -> int:
  from quantx_research.next_day_selection_training import RunCancelled, _safe_error

  try:
    request = load_request(request_file)
    asyncio.run(run_request(request))
    return 0
  except RunCancelled:
    return 3
  except Exception as exc:
    # The run directory, when created, owns the sanitized failure manifest.
    # Never print request paths or full exception strings from this boundary.
    message = _safe_error(exc)
    print(f"训练作业失败: {type(exc).__name__}: {message}", flush=True)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
