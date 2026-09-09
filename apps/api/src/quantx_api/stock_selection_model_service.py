"""Manual registration boundary for finalized selection model evidence.

The training database row is the source of truth for ``run_key``.  Filesystem
artifacts are only accepted after that row has reached the immutable
FINAL_EVALUATION/SUCCEEDED state and the strict schema-v2 loader has checked
every identity and hash.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from quantx_infrastructure.models.stock_selection import StockSelectionTrainingRun
from quantx_infrastructure.repositories.stock_selection_repository import (
  StockSelectionRepository,
)
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
)
from quantx_infrastructure.services.stock_selection_artifacts import (
  SelectionArtifactError,
  load_selection_artifact,
)

from quantx_api.research_artifacts import research_runs_root

_HASH = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ELIGIBLE_CONCLUSIONS = {"SHADOW_ELIGIBLE", "ACTIVE_ELIGIBLE"}
_ABSOLUTE_PATH_START = re.compile(
  r"(?<![A-Za-z0-9_.-])(?:[A-Za-z]:[\\/]|\\\\|//|/(?!/))"
)


def _stable_run_key(*, study_id: str, version: str, run_id: str) -> str:
  return hashlib.sha256(f"{study_id}\0{version}\0{run_id}".encode("utf-8")).hexdigest()


def _safe_evidence(value: Any, *, key: str = "") -> Any:
  lowered = key.lower()
  if any(token in lowered for token in ("path", "root", "directory", "reference")):
    return None
  if any(token in lowered for token in ("password", "secret", "token", "credential", "api_key")):
    return None
  if isinstance(value, dict):
    result: dict[str, Any] = {}
    for name, item in value.items():
      safe = _safe_evidence(item, key=str(name))
      if safe is not None:
        result[str(name)] = safe
    return result
  if isinstance(value, list):
    return [_safe_evidence(item, key=key) for item in value][:100]
  if isinstance(value, str):
    return value[:512]
  return value


def _safe_error(exc: BaseException) -> str:
  # Do not return a raw loader/SQLAlchemy exception: it can contain a host
  # path, a query fragment, or a private source reference.
  text = str(exc)
  # Once an absolute path is present, redact the complete suffix rather than
  # stopping at whitespace.  That covers paths with spaces, Windows UNC
  # shares, and Unix paths without leaking the tail after the first segment.
  path_match = _ABSOLUTE_PATH_START.search(text)
  if path_match:
    text = f"{text[:path_match.start()]}<path>"
  text = re.sub(r"(?i)(password|secret|token|credential)[=:][^,;\s]+", r"\1=<redacted>", text)
  return text[:256]


class StockSelectionModelService:
  def __init__(
    self,
    repository: StockSelectionRepository,
    training_repository: StockSelectionTrainingRepository | None = None,
    *,
    runs_root: str | Path | None = None,
  ):
    self.repository = repository
    self.training_repository = training_repository
    self.runs_root = Path(runs_root).expanduser() if runs_root is not None else research_runs_root()

  async def _run_by_key(self, run_key: str) -> StockSelectionTrainingRun | None:
    if self.training_repository is None:
      raise ValueError("训练运行数据库未配置")
    return await self.training_repository.get_run_by_run_key(run_key)

  @staticmethod
  def _record_value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
      return record.get(name, default)
    return getattr(record, name, default)

  def _run_directory(self, run_id: str) -> Path:
    if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
      raise ValueError("训练运行目录身份无效")
    root = self.runs_root.resolve(strict=False)
    candidate = root / run_id
    # Check every component so a junction/symlink cannot redirect a private
    # source directory while resolving the exact run path.
    cursor = root
    for part in candidate.relative_to(root).parts:
      cursor = cursor / part
      if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
        raise ValueError("训练运行目录不得包含链接")
    resolved_root = root.resolve(strict=False)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root) or not resolved.is_dir():
      raise ValueError("训练运行目录无效")
    return resolved

  async def register(self, run_key: str):
    if not isinstance(run_key, str) or not _HASH.fullmatch(run_key):
      raise ValueError("runKey 必须是训练运行数据库中的 SHA-256 key")
    row = await self._run_by_key(run_key)
    if row is None:
      raise ValueError("训练运行不存在")
    if (
      row.run_key != run_key
      or row.run_kind != "FINAL_EVALUATION"
      or row.status != "SUCCEEDED"
      or not row.artifact_manifest_sha256
      or not _HASH.fullmatch(str(row.artifact_manifest_sha256))
    ):
      raise ValueError("只能登记成功的 FINAL_EVALUATION 运行")
    spec_id = self._record_value(row, "spec_id")
    if not spec_id:
      raise ValueError("FINAL_EVALUATION 缺少训练配置")
    spec = await self.training_repository.get_spec(str(spec_id))
    if spec is None or self._record_value(spec, "run_kind") != "FINAL_EVALUATION":
      raise ValueError("FINAL_EVALUATION 训练配置不存在或类型无效")
    parent_run_id = self._record_value(row, "parent_run_id")
    if not parent_run_id:
      raise ValueError("FINAL_EVALUATION 缺少 DEVELOPMENT 父运行")
    parent = await self.training_repository.get_run(str(parent_run_id))
    if (
      parent is None
      or self._record_value(parent, "run_kind") != "DEVELOPMENT"
      or self._record_value(parent, "status") != "SUCCEEDED"
    ):
      raise ValueError("FINAL_EVALUATION 父运行无效")
    parent_spec_id = self._record_value(parent, "spec_id")
    parent_spec = (
      await self.training_repository.get_spec(str(parent_spec_id))
      if parent_spec_id
      else None
    )
    spec_coordinate = self._record_value(spec, "coordinate_hash")
    parent_coordinate = self._record_value(parent_spec, "coordinate_hash")
    if (
      not isinstance(spec_coordinate, str)
      or not _HASH.fullmatch(spec_coordinate)
      or spec_coordinate != parent_coordinate
    ):
      raise ValueError("FINAL_EVALUATION 与 DEVELOPMENT 父运行坐标不一致")
    try:
      directory = self._run_directory(str(row.run_id))
      bundle = load_selection_artifact(
        directory,
        expected_manifest_sha256=str(row.artifact_manifest_sha256),
      )
    except (SelectionArtifactError, OSError, ValueError) as exc:
      raise ValueError(f"模型产物未通过安全校验: {_safe_error(exc)}") from exc
    manifest = bundle.manifest
    metrics = bundle.metrics
    parent_evidence = metrics.get("parent_development")
    if (
      manifest.get("parent_run_id") != parent_run_id
      or not isinstance(parent_evidence, Mapping)
      or parent_evidence.get("run_id") != parent_run_id
    ):
      raise ValueError("模型产物与数据库的 DEVELOPMENT 父运行不一致")
    for field in ("spec_hash", "coordinate_hash"):
      manifest_value = manifest.get(field)
      spec_value = self._record_value(spec, field)
      if (
        not isinstance(manifest_value, str)
        or not _HASH.fullmatch(manifest_value)
        or not isinstance(spec_value, str)
        or not _HASH.fullmatch(spec_value)
        or manifest_value != spec_value
      ):
        raise ValueError(f"模型 manifest 与训练 spec 的 {field} 不一致")
    for field in ("requested_backend", "resolved_backend"):
      manifest_value = str(manifest.get(field) or "").upper()
      raw_spec_value = self._record_value(spec, field)
      spec_value = str(getattr(raw_spec_value, "value", raw_spec_value) or "").upper()
      if not manifest_value or not spec_value or manifest_value != spec_value:
        raise ValueError(f"模型 manifest 与训练 spec 的 {field} 不一致")
    gates = metrics.get("gates")
    conclusion = metrics.get("conclusion")
    if (
      manifest.get("run_id") != row.run_id
      or manifest.get("run_kind") != "FINAL_EVALUATION"
      or manifest.get("status") != "SUCCEEDED"
      or _stable_run_key(
        study_id=str(manifest.get("study_id")),
        version=str(manifest.get("version")),
        run_id=str(manifest.get("run_id")),
      )
      != run_key
      or not isinstance(gates, dict)
      or conclusion not in _ELIGIBLE_CONCLUSIONS
      or metrics.get("registerable") is not True
      or manifest.get("registerable") is not True
    ):
      raise ValueError("模型运行身份、结论或登记门禁无效")
    # Only these fields cross into the registry.  ``artifact_directory`` is a
    # private persistence field; GraphQL model projections never return it.
    return await self.repository.register_model(
      {
        "model_version": manifest["model_version"],
        "run_key": run_key,
        "artifact_directory": str(bundle.directory),
        "artifact_manifest_sha256": bundle.manifest_sha256,
        "selected_family": manifest["selected_family"],
        "indicator_version": manifest["indicator_version"],
        "factor_set_version": manifest["factor_set_version"],
        "factor_set_hash": manifest["factor_set_hash"],
        "label_version": manifest["label_version"],
        "calibrator_version": manifest["calibrator_version"],
        "training_start": date.fromisoformat(manifest["training_start"]),
        "training_end": date.fromisoformat(manifest["training_end"]),
        "calibration_start": date.fromisoformat(manifest["calibration_start"]),
        "calibration_end": date.fromisoformat(manifest["calibration_end"]),
        "test_start": date.fromisoformat(manifest["test_start"]),
        "test_end": date.fromisoformat(manifest["test_end"]),
        "historical_universe_complete": bool(gates["historical_universe_complete"]),
        "effect_gate_passed": bool(gates["effect_gate_passed"]),
        "metrics": _safe_evidence(metrics),
        "gates": _safe_evidence(gates),
        "evidence": {
          "config_hash": manifest.get("config_hash"),
          "data_fingerprint": manifest.get("data_fingerprint"),
          "artifact_count": len(manifest.get("artifacts") or []),
          "conclusion": conclusion,
          "data_quality": _safe_evidence(bundle.data_quality),
        },
        "approved_by": "",
      }
    )
