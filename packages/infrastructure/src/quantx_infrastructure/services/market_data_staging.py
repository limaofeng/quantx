"""Authoritative filesystem boundary for durable market-data staging."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path


def market_data_staging_root() -> Path:
  """Resolve the one staging root shared by API upload and ingestion cleanup."""

  runtime_dir = str(os.environ.get("QUANTX_RUNTIME_DIR") or "").strip()
  if runtime_dir:
    runtime_root = Path(runtime_dir).expanduser().resolve()
  else:
    quantx_root = str(os.environ.get("QUANTX_ROOT") or "").strip()
    repository_root = (
      Path(quantx_root).expanduser().resolve()
      if quantx_root
      else Path(__file__).resolve().parents[5]
    )
    runtime_root = repository_root / ".runtime"
  return runtime_root / "market-data"


def is_reparse_point(path: Path) -> bool:
  """Return whether a path is a Windows reparse point, including junctions."""

  reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
  try:
    attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
  except FileNotFoundError:
    return False
  return bool(attributes & reparse_flag)


def safe_market_data_request_directory(root: Path, candidate: Path) -> Path:
  """Resolve one canonical UUID directory without following staging links."""

  try:
    normalized_name = str(uuid.UUID(candidate.name))
  except ValueError as exc:
    raise RuntimeError("unsafe market-data staging directory name") from exc
  if normalized_name != candidate.name:
    raise RuntimeError("non-canonical market-data staging directory name")
  if root.is_symlink() or is_reparse_point(root):
    raise RuntimeError("unsafe market-data staging root")
  if candidate.is_symlink() or is_reparse_point(candidate):
    raise RuntimeError("unsafe market-data staging reparse point")
  resolved_root = root.resolve(strict=True)
  if not resolved_root.is_dir():
    raise RuntimeError("market-data staging root is not a directory")
  resolved = candidate.resolve(strict=True)
  if not resolved.is_dir() or resolved.parent != resolved_root:
    raise RuntimeError("market-data staging directory escaped its root")
  return resolved


def safe_market_data_staging_file(
  *,
  root: Path,
  request_id: str,
  storage_reference: str,
) -> Path:
  """Resolve one ordinary manifest file directly below its request directory."""

  try:
    normalized_request_id = str(uuid.UUID(request_id))
  except ValueError as exc:
    raise RuntimeError("unsafe market-data request id") from exc
  if normalized_request_id != request_id:
    raise RuntimeError("non-canonical market-data request id")

  request_directory = safe_market_data_request_directory(
    root,
    root / normalized_request_id,
  )
  candidate = Path(storage_reference)
  if candidate.is_symlink() or is_reparse_point(candidate):
    raise RuntimeError("unsafe market-data staging file reparse point")
  if candidate.parent.is_symlink() or is_reparse_point(candidate.parent):
    raise RuntimeError("unsafe market-data staging parent reparse point")
  candidate_stat = candidate.lstat()
  if not stat.S_ISREG(candidate_stat.st_mode):
    raise RuntimeError("market-data staging path is not a regular file")
  resolved = candidate.resolve(strict=True)
  if resolved.parent != request_directory:
    raise RuntimeError("market-data staging file escaped its request directory")
  return resolved


def market_data_request_staging_usage_bytes(
  *,
  root: Path,
  request_id: str,
) -> int:
  """Sum actual ordinary files retained directly under one request directory."""

  try:
    normalized_request_id = str(uuid.UUID(request_id))
  except ValueError as exc:
    raise RuntimeError("unsafe market-data request id") from exc
  if normalized_request_id != request_id:
    raise RuntimeError("non-canonical market-data request id")
  request_directory = safe_market_data_request_directory(
    root,
    root / normalized_request_id,
  )
  total = 0
  for candidate in request_directory.iterdir():
    safe_file = safe_market_data_staging_file(
      root=root,
      request_id=normalized_request_id,
      storage_reference=str(candidate),
    )
    total += safe_file.stat().st_size
  return total
