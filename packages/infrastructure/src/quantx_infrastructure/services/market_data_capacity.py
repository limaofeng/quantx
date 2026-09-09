"""Shared staging limits and conservative native-collection admission."""

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from quantx_infrastructure.services.market_data_staging import is_reparse_point

MAX_MARKET_DATA_INFLIGHT_REQUESTS_PER_DEVICE = 2
MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_MARKET_DATA_STAGING_BYTES = 1024 * 1024 * 1024
MIN_MARKET_DATA_STAGING_FREE_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class StagingUsage:
  retained_bytes: int
  request_bytes: dict[str, int]


def staging_usage(root: Path) -> StagingUsage:
  """Count retained files, including failed/orphan files; never follow links."""
  if root.is_symlink() or is_reparse_point(root):
    raise RuntimeError("unsafe market-data staging root")
  if not root.exists():
    return StagingUsage(0, {})
  if not root.is_dir():
    raise RuntimeError("market-data staging root is not a directory")
  resolved_root = root.resolve()
  total = 0
  requests = {}
  deadline = time.monotonic() + 2
  for index, path in enumerate(root.rglob("*")):
    if index >= 10000 or time.monotonic() >= deadline:
      raise RuntimeError("market-data staging scan budget exhausted")
    if path.is_symlink() or is_reparse_point(path):
      raise RuntimeError("market-data staging contains a reparse point")
    if not path.is_file():
      continue
    resolved = path.resolve()
    if resolved_root not in resolved.parents:
      raise RuntimeError("market-data staging file escaped its root")
    size = path.stat().st_size
    total += size
    relative = resolved.relative_to(resolved_root)
    if len(relative.parts) >= 2:
      request = relative.parts[0]
      requests[request] = requests.get(request, 0) + size
  return StagingUsage(total, requests)


def staging_usage_bytes(root: Path) -> int:
  return staging_usage(root).retained_bytes


def staging_free_bytes(root: Path) -> int:
  # Admission precedes the first upload and therefore directory creation.
  probe = root
  while not probe.exists():
    if probe.is_symlink() or is_reparse_point(probe):
      raise RuntimeError("unsafe market-data staging path")
    probe = probe.parent
  return int(shutil.disk_usage(probe).free)


def collection_has_capacity(root: Path, collecting_request_ids: set[str]) -> bool:
  """Reserve each still-mutable request's remaining compressed allowance.

  Uploads retain their own per-write checks. This reservation covers concurrent
  uploads by admitted requests; unrelated disk users can still exhaust a disk.
  """
  try:
    usage = staging_usage(root)
    if any(
      usage.request_bytes.get(request, 0) > MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES
      for request in collecting_request_ids
    ):
      return False
    remaining = sum(
      max(
        0,
        MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES - usage.request_bytes.get(request, 0),
      )
      for request in collecting_request_ids
    )
    return (
      usage.retained_bytes + remaining <= MAX_MARKET_DATA_STAGING_BYTES
      and staging_free_bytes(root) - remaining >= MIN_MARKET_DATA_STAGING_FREE_BYTES
    )
  except (OSError, RuntimeError):
    return False
