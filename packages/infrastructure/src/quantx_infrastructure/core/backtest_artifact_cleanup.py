"""Safe cleanup for file artifacts owned by a persisted backtest."""

from __future__ import annotations

import logging
import os
from typing import Optional

from quantx_infrastructure.core.backtest_result_storage import BacktestResultStorage

logger = logging.getLogger(__name__)


def _result_path_candidates(raw_path: str) -> list[str]:
  if not raw_path:
    return []
  return [
    raw_path,
    os.path.join("data", raw_path),
    os.path.join("data", "backtests", os.path.basename(raw_path)),
  ]


def _is_within_root(data_root: str, path: str) -> bool:
  try:
    return os.path.commonpath([data_root, os.path.abspath(path)]) == data_root
  except ValueError:
    return False


def delete_backtest_artifacts(
  backtest_id: str,
  result_path: Optional[str],
) -> list[str]:
  """Delete one backtest's files without ever leaving ``data/backtests``."""

  data_root = os.path.abspath(os.path.join("data", "backtests"))
  candidates = set(_result_path_candidates(result_path or ""))
  candidates.add(
    os.path.join("data", "backtests", "performance", f"{backtest_id}.json")
  )
  candidates.add(
    os.path.join(
      "data",
      "backtests",
      "performance",
      str(backtest_id),
      "manifest.json",
    )
  )

  for candidate in list(candidates):
    if not _is_within_root(data_root, candidate):
      continue
    manifest = BacktestResultStorage.load_manifest(candidate)
    if not manifest:
      continue
    for artifact in dict(manifest.get("artifacts") or {}).values():
      artifact_path = artifact.get("path") if isinstance(artifact, dict) else artifact
      if artifact_path:
        candidates.add(os.path.join(os.path.dirname(candidate), str(artifact_path)))

  deleted: list[str] = []
  for candidate in candidates:
    if not candidate:
      continue
    abs_path = os.path.abspath(candidate)
    if not _is_within_root(data_root, abs_path):
      continue
    if not os.path.isfile(abs_path):
      continue
    try:
      os.remove(abs_path)
      deleted.append(abs_path)
    except OSError as exc:
      logger.warning("删除回测文件失败: %s (%s)", abs_path, exc)
  return deleted
