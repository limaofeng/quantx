"""可选的本地 Parquet 数据集缓存。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .models import (
  DataQualityReport,
  DividendFactorCoverageReport,
  ResearchDataset,
)

_CACHE_SCHEMA_VERSION = 1


class ParquetDatasetCache:
  """以原子文件替换方式读写一个研究数据集目录。"""

  def __init__(self, directory: str | Path) -> None:
    self.directory = Path(directory).resolve()

  def write(self, dataset: ResearchDataset) -> Path:
    self.directory.mkdir(parents=True, exist_ok=True)
    artifacts = {
      "panel": self._write_frame("panel.parquet", dataset.panel),
      "benchmark": self._write_frame("benchmark.parquet", dataset.benchmark),
      "instruments": self._write_frame("instruments.parquet", dataset.instruments),
      "factors": self._write_frame("factors.parquet", dataset.factors),
    }
    quality_path = self.directory / "data-quality.json"
    _atomic_write_text(
      quality_path,
      json.dumps(
        dataset.quality.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
      ),
    )
    artifacts["quality"] = _artifact_metadata(quality_path)
    if dataset.factor_coverage is not None:
      factor_coverage_path = self.directory / "factor-coverage.json"
      _atomic_write_text(
        factor_coverage_path,
        json.dumps(
          dataset.factor_coverage.to_dict(),
          ensure_ascii=False,
          indent=2,
          sort_keys=True,
        ),
      )
      artifacts["factor_coverage"] = _artifact_metadata(factor_coverage_path)

    manifest = {
      "schema_version": _CACHE_SCHEMA_VERSION,
      "created_at": datetime.now(timezone.utc).isoformat(),
      "artifacts": artifacts,
    }
    _atomic_write_text(
      self.directory / "dataset-manifest.json",
      json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
    )
    return self.directory

  def read(self, *, verify: bool = True) -> ResearchDataset:
    manifest_path = self.directory / "dataset-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != _CACHE_SCHEMA_VERSION:
      raise ValueError(f"不支持的数据集缓存版本: {manifest.get('schema_version')}")
    if verify:
      self._verify_artifacts(manifest["artifacts"])

    quality_payload = json.loads(
      (self.directory / "data-quality.json").read_text(encoding="utf-8")
    )
    factor_coverage = None
    if "factor_coverage" in manifest["artifacts"]:
      factor_coverage = DividendFactorCoverageReport.from_dict(
        json.loads(
          (self.directory / "factor-coverage.json").read_text(encoding="utf-8")
        )
      )
    return ResearchDataset(
      panel=pd.read_parquet(self.directory / "panel.parquet"),
      benchmark=pd.read_parquet(self.directory / "benchmark.parquet"),
      instruments=pd.read_parquet(self.directory / "instruments.parquet"),
      factors=pd.read_parquet(self.directory / "factors.parquet"),
      quality=DataQualityReport.from_dict(quality_payload),
      factor_coverage=factor_coverage,
    )

  def _write_frame(self, filename: str, frame: pd.DataFrame) -> dict[str, Any]:
    target = self.directory / filename
    temporary = target.with_suffix(f"{target.suffix}.tmp-{os.getpid()}")
    try:
      frame.to_parquet(temporary, index=False, engine="pyarrow")
      temporary.replace(target)
    finally:
      temporary.unlink(missing_ok=True)
    return _artifact_metadata(target)

  def _verify_artifacts(self, artifacts: dict[str, dict[str, Any]]) -> None:
    for metadata in artifacts.values():
      path = self.directory / metadata["path"]
      if not path.is_file():
        raise FileNotFoundError(f"数据集缓存文件不存在: {path}")
      if _sha256(path) != metadata["sha256"]:
        raise ValueError(f"数据集缓存校验失败: {path.name}")


def _artifact_metadata(path: Path) -> dict[str, Any]:
  return {
    "path": path.name,
    "bytes": path.stat().st_size,
    "sha256": _sha256(path),
  }


def _sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
  temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
  try:
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
  finally:
    temporary.unlink(missing_ok=True)
