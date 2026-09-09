"""Strict loader for safe next-day selection model artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from quantx_domain.indicators import INDICATOR_VERSION
from quantx_domain.selection_factors import (
  FACTOR_SET_HASH,
  FACTOR_SET_VERSION,
  LABEL_VERSION,
  factor_schema_manifest,
  selection_feature_columns,
)
from quantx_domain.selection_model import CALIBRATOR_VERSION
from quantx_domain.stock_selection_training import validate_time_split

_HASH = re.compile(r"^[0-9a-f]{64}$")
_MODEL_VERSION = re.compile(r"^next-day-up-v1-[0-9a-f]{16}$")
_ARTIFACT_SCHEMA_VERSION = 2
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_FINAL_RUN_KIND = "FINAL_EVALUATION"
_CONCLUSIONS = {"BLOCKED", "SHADOW_ELIGIBLE", "ACTIVE_ELIGIBLE"}
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_MODEL_BYTES = 256 * 1024 * 1024
_REQUIRED_FILES = {
  "manifest.json",
  "metrics.json",
  "data-quality.json",
  "factor-schema.json",
  "model-runtime.json",
  "preprocessing.json",
  "calibrators.json",
  "logistic.json",
  "lightgbm.txt",
}
_MAX_EVIDENCE_DEPTH = 8
_MAX_EVIDENCE_ENTRIES = 256
_MAX_TELEMETRY_SECONDS = 365 * 24 * 60 * 60
_MAX_RUNTIME_PARAMETER_ENTRIES = 64
_QUALIFICATION_STATUSES = {
  "CPU_AVAILABLE",
  "GPU_UNAVAILABLE_BUILD",
  "GPU_UNAVAILABLE_RUNTIME",
  "GPU_INSUFFICIENT_MEMORY",
  "GPU_UNQUALIFIED",
  "GPU_AVAILABLE",
}
_SENSITIVE_EVIDENCE_KEYS = (
  "path",
  "root",
  "directory",
  "reference",
  "password",
  "secret",
  "token",
  "credential",
  "api_key",
)
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|//|/(?!/))")


class SelectionArtifactError(ValueError):
  pass


@dataclass(frozen=True)
class SelectionArtifactBundle:
  directory: Path
  manifest_sha256: str
  manifest: dict[str, Any]
  metrics: dict[str, Any]
  data_quality: dict[str, Any]
  runtime: dict[str, Any]
  preprocessing: dict[str, Any]
  calibrators: dict[str, Any]
  logistic: dict[str, Any]
  lightgbm_path: Path


def file_sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def _is_link_like(path: Path) -> bool:
  if path.is_symlink():
    return True
  is_junction = getattr(path, "is_junction", None)
  return bool(is_junction and is_junction())


def _safe_file(directory: Path, relative: str, *, max_bytes: int) -> Path:
  if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
    raise SelectionArtifactError("模型产物路径无效")
  path = directory / relative
  cursor = directory
  for part in Path(relative).parts:
    cursor = cursor / part
    if _is_link_like(cursor):
      raise SelectionArtifactError(f"模型产物路径包含链接: {relative}")
  if _is_link_like(path) or not path.is_file():
    raise SelectionArtifactError(f"模型产物缺失或不是普通文件: {relative}")
  resolved = path.resolve(strict=True)
  if not resolved.is_relative_to(directory):
    raise SelectionArtifactError("模型产物越过运行目录")
  if resolved.stat().st_size > max_bytes:
    raise SelectionArtifactError(f"模型产物超过大小上限: {relative}")
  return resolved


def _json(directory: Path, relative: str) -> dict[str, Any]:
  path = _safe_file(directory, relative, max_bytes=_MAX_JSON_BYTES)
  try:
    value = json.loads(
      path.read_text(encoding="utf-8"),
      parse_constant=lambda token: _reject_json_constant(token),
    )
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
    raise SelectionArtifactError(f"模型 JSON 无法读取: {relative}") from exc
  if not isinstance(value, dict):
    raise SelectionArtifactError(f"模型 JSON 根节点无效: {relative}")
  _assert_finite_json(value, field=relative)
  return value


def _reject_json_constant(token: str) -> None:
  raise ValueError(f"不允许的 JSON 数值: {token}")


def _assert_finite_json(value: Any, *, field: str = "artifact") -> None:
  """Reject non-finite values even when a JSON decoder is replaced in tests."""

  if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
    return
  if isinstance(value, float):
    if not math.isfinite(value):
      raise SelectionArtifactError(f"模型产物数值必须有限: {field}")
    return
  if isinstance(value, list):
    for index, item in enumerate(value):
      _assert_finite_json(item, field=f"{field}[{index}]")
    return
  if isinstance(value, dict):
    for key, item in value.items():
      _assert_finite_json(item, field=f"{field}.{key}")
    return
  raise SelectionArtifactError(f"模型产物 JSON 类型无效: {field}")


def _safe_public_json(value: Any, *, key: str = "") -> Any:
  """Project evidence without source paths, credentials, or raw exceptions."""

  lowered = key.lower()
  if any(token in lowered for token in ("path", "root", "directory", "reference")):
    return None
  if any(token in lowered for token in ("password", "secret", "token", "credential", "api_key")):
    return None
  if isinstance(value, dict):
    return {
      str(name): safe_value
      for name, item in value.items()
      if (safe_value := _safe_public_json(item, key=str(name))) is not None
    }
  if isinstance(value, list):
    return [_safe_public_json(item, key=key) for item in value]
  if isinstance(value, str):
    return value[:512]
  return value


def _verify_index(
  directory: Path,
  manifest: dict[str, Any],
) -> dict[str, str]:
  artifacts = manifest.get("artifacts")
  if not isinstance(artifacts, list) or len(artifacts) > 64:
    raise SelectionArtifactError("模型清单缺少有界产物索引")
  indexed: dict[str, str] = {}
  for item in artifacts:
    if not isinstance(item, dict):
      raise SelectionArtifactError("模型产物索引格式无效")
    relative = item.get("path")
    digest = item.get("sha256")
    if (
      not isinstance(relative, str)
      or not isinstance(digest, str)
      or not _HASH.fullmatch(digest)
    ):
      raise SelectionArtifactError("模型产物索引字段无效")
    if relative in indexed or relative == "manifest.json":
      raise SelectionArtifactError("模型产物索引重复")
    if Path(relative).suffix.lower() not in {
      ".json",
      ".yaml",
      ".yml",
      ".parquet",
      ".txt",
    }:
      raise SelectionArtifactError(f"模型清单包含未允许的产物类型: {relative}")
    path = _safe_file(
      directory,
      relative,
      max_bytes=(
        _MAX_MODEL_BYTES
        if Path(relative).suffix.lower() in {".txt", ".parquet"}
        else _MAX_JSON_BYTES
      ),
    )
    if file_sha256(path) != digest:
      raise SelectionArtifactError(f"模型产物哈希不一致: {relative}")
    if item.get("bytes") != path.stat().st_size:
      raise SelectionArtifactError(f"模型产物大小不一致: {relative}")
    indexed[relative] = digest
  required = set(_REQUIRED_FILES) | {"resolved-config.yaml", "test-predictions.parquet"}
  missing = required - ({"manifest.json"} | set(indexed))
  if missing:
    raise SelectionArtifactError(f"模型清单缺少必要产物: {sorted(missing)}")
  forbidden = [
    path
    for path in indexed
    if Path(path).suffix.lower() in {".pkl", ".pickle", ".joblib"}
  ]
  if forbidden:
    raise SelectionArtifactError("模型清单包含不安全的 Python 序列化产物")
  return indexed


def _number(
  value: Any,
  field: str,
  *,
  minimum: float | None = None,
  maximum: float | None = None,
) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise SelectionArtifactError(f"模型产物数值字段无效: {field}")
  result = float(value)
  if not math.isfinite(result):
    raise SelectionArtifactError(f"模型产物数值必须有限: {field}")
  if minimum is not None and result < minimum:
    raise SelectionArtifactError(f"模型产物数值低于下界: {field}")
  if maximum is not None and result > maximum:
    raise SelectionArtifactError(f"模型产物数值高于上界: {field}")
  return result


def _optional_number(
  value: Any,
  field: str,
  *,
  minimum: float | None = None,
  maximum: float | None = None,
) -> float | None:
  if value is None:
    return None
  return _number(value, field, minimum=minimum, maximum=maximum)


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
  if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
    raise SelectionArtifactError(f"模型产物整数字段无效: {field}")
  return value


def _iso_date(value: Any, field: str) -> str:
  if not isinstance(value, str):
    raise SelectionArtifactError(f"模型产物日期字段无效: {field}")
  try:
    date.fromisoformat(value)
  except ValueError as exc:
    raise SelectionArtifactError(f"模型产物日期字段无效: {field}") from exc
  return value


def _optional_iso_date(value: Any, field: str) -> str | None:
  if value is None:
    return None
  return _iso_date(value, field)


def _probability_metrics(value: Any) -> dict[str, Any]:
  if not isinstance(value, dict):
    raise SelectionArtifactError("冻结测试缺少概率指标")
  bins = value.get("calibration_bins")
  if not isinstance(bins, list) or not 5 <= len(bins) <= 50:
    raise SelectionArtifactError("校准桶数量无效")
  projected_bins: list[dict[str, Any]] = []
  previous_upper = 0.0
  for expected_index, row in enumerate(bins):
    if not isinstance(row, dict):
      raise SelectionArtifactError("校准桶格式无效")
    index = _integer(row.get("index"), "calibration_bins.index")
    lower = _number(row.get("lower"), "calibration_bins.lower", minimum=0, maximum=1)
    upper = _number(row.get("upper"), "calibration_bins.upper", minimum=0, maximum=1)
    if index != expected_index or abs(lower - previous_upper) > 1e-12 or upper <= lower:
      raise SelectionArtifactError("校准桶边界或顺序无效")
    previous_upper = upper
    projected_bins.append(
      {
        "index": index,
        "lower": lower,
        "upper": upper,
        "sample_count": _integer(
          row.get("sample_count"), "calibration_bins.sample_count"
        ),
        "mean_probability": _optional_number(
          row.get("mean_probability"),
          "calibration_bins.mean_probability",
          minimum=0,
          maximum=1,
        ),
        "realized_rate": _optional_number(
          row.get("realized_rate"),
          "calibration_bins.realized_rate",
          minimum=0,
          maximum=1,
        ),
      }
    )
  if abs(projected_bins[0]["lower"]) > 1e-12 or abs(previous_upper - 1.0) > 1e-12:
    raise SelectionArtifactError("校准桶必须完整覆盖 0 到 1")
  sample_count = _integer(
    value.get("sample_count"), "probability.sample_count", minimum=1
  )
  positive_count = _integer(value.get("positive_count"), "probability.positive_count")
  if positive_count > sample_count:
    raise SelectionArtifactError("概率正样本数超过总样本数")
  return {
    "sample_count": sample_count,
    "positive_count": positive_count,
    "prevalence": _number(
      value.get("prevalence"), "probability.prevalence", minimum=0, maximum=1
    ),
    "brier": _number(value.get("brier"), "probability.brier", minimum=0, maximum=1),
    "baseline_brier": _number(
      value.get("baseline_brier"), "probability.baseline_brier", minimum=0, maximum=1
    ),
    "brier_skill": _number(value.get("brier_skill"), "probability.brier_skill"),
    "log_loss": _number(value.get("log_loss"), "probability.log_loss", minimum=0),
    "ece": _number(value.get("ece"), "probability.ece", minimum=0, maximum=1),
    "roc_auc": _optional_number(
      value.get("roc_auc"), "probability.roc_auc", minimum=0, maximum=1
    ),
    "pr_auc": _optional_number(
      value.get("pr_auc"), "probability.pr_auc", minimum=0, maximum=1
    ),
    "calibration_bins": projected_bins,
  }


def _ranking_metrics(value: Any) -> dict[str, Any]:
  if not isinstance(value, dict):
    raise SelectionArtifactError("冻结测试缺少排序指标")
  projected: dict[str, Any] = {}
  for key in ("top_20", "top_50"):
    row = value.get(key)
    if not isinstance(row, dict):
      raise SelectionArtifactError(f"冻结测试缺少 {key} 指标")
    projected[key] = {
      "date_count": _integer(row.get("date_count"), f"{key}.date_count", minimum=1),
      "precision": _number(
        row.get("precision"), f"{key}.precision", minimum=0, maximum=1
      ),
      "mean_return": _number(row.get("mean_return"), f"{key}.mean_return"),
      "baseline_up_rate": _number(
        row.get("baseline_up_rate"), f"{key}.baseline_up_rate", minimum=0, maximum=1
      ),
      "up_rate_lift": _number(row.get("up_rate_lift"), f"{key}.up_rate_lift"),
      "up_rate_lift_ci_low": _number(
        row.get("up_rate_lift_ci_low"), f"{key}.up_rate_lift_ci_low"
      ),
      "up_rate_lift_ci_high": _number(
        row.get("up_rate_lift_ci_high"), f"{key}.up_rate_lift_ci_high"
      ),
      "mean_return_lift": _number(
        row.get("mean_return_lift"), f"{key}.mean_return_lift"
      ),
    }
  return projected


def _hash_field(value: Any, field: str, *, optional: bool = False) -> str | None:
  if value is None and optional:
    return None
  if not isinstance(value, str) or not _HASH.fullmatch(value):
    raise SelectionArtifactError(f"模型产物哈希字段无效: {field}")
  return value


def _project_grid(value: Any, field: str) -> list[dict[str, Any]]:
  if not isinstance(value, list) or len(value) > 1000:
    raise SelectionArtifactError(f"模型参数搜索结构无效: {field}")
  projected: list[dict[str, Any]] = []
  for row in value:
    if not isinstance(row, dict):
      raise SelectionArtifactError(f"模型参数搜索记录无效: {field}")
    # The search grid is evidence, not a runtime input.  Keep only bounded
    # scalar values and never expose arbitrary nested payloads.
    safe = _safe_public_json(row)
    if not isinstance(safe, dict):
      raise SelectionArtifactError(f"模型参数搜索记录无效: {field}")
    projected.append(safe)
  return projected


def sanitize_selection_metrics(
  value: dict[str, Any],
) -> dict[str, Any]:
  """Validate and project the schema-v2 FINAL_EVALUATION metrics contract."""

  if not isinstance(value, dict) or value.get("schema_version") != _ARTIFACT_SCHEMA_VERSION:
    raise SelectionArtifactError("模型评估必须使用唯一 schema-v2")
  model_version = value.get("model_version")
  selected_family = value.get("selected_family")
  if not isinstance(model_version, str) or not _MODEL_VERSION.fullmatch(model_version):
    raise SelectionArtifactError("模型评估版本格式无效")
  if selected_family not in {"LOGISTIC", "LIGHTGBM"}:
    raise SelectionArtifactError("模型评估主家族无效")
  spec_hash = _hash_field(value.get("spec_hash"), "metrics.spec_hash")
  coordinate_hash = _hash_field(value.get("coordinate_hash"), "metrics.coordinate_hash")
  config_hash = _hash_field(value.get("config_hash"), "metrics.config_hash")
  parent = value.get("parent_development")
  if not isinstance(parent, dict) or not isinstance(parent.get("run_id"), str) or not _RUN_ID.fullmatch(parent["run_id"]):
    raise SelectionArtifactError("模型评估缺少有效 DEVELOPMENT 父运行")
  parent_projection = {
    "run_id": parent["run_id"],
    "metrics_sha256": _hash_field(parent.get("metrics_sha256"), "parent_development.metrics_sha256"),
    "lock_sha256": _hash_field(parent.get("lock_sha256"), "parent_development.lock_sha256"),
  }
  if value.get("calibrator_version") != CALIBRATOR_VERSION:
    raise SelectionArtifactError("模型评估校准器版本不一致")
  validation = value.get("validation")
  if not isinstance(validation, dict):
    raise SelectionArtifactError("模型评估 validation 结构无效")
  validation_projection: dict[str, Any] = {
    "fold_count": _integer(validation.get("fold_count"), "validation.fold_count", minimum=1),
    "logistic_brier": _number(
      validation.get("logistic_brier"), "validation.logistic_brier", minimum=0, maximum=1
    ),
    "lightgbm_brier": _number(
      validation.get("lightgbm_brier"), "validation.lightgbm_brier", minimum=0, maximum=1
    ),
  }
  for key in ("logistic_grid", "lightgbm_grid"):
    if key in validation:
      validation_projection[key] = _project_grid(validation[key], f"validation.{key}")

  gates = value.get("gates")
  if not isinstance(gates, dict):
    raise SelectionArtifactError("模型发布门禁结构无效")
  gate_names = (
    "brier_skill_positive",
    "ece_within_3pct",
    "top20_lift_ci_lower_positive",
    "historical_universe_complete",
    "effect_gate_passed",
    "unbiased_frozen_evidence",
    "active_eligible",
    "access_evidence_valid",
  )
  if any(not isinstance(gates.get(name), bool) for name in gate_names):
    raise SelectionArtifactError("模型发布门禁字段无效")
  effect_expected = all(
    gates[name]
    for name in (
      "brier_skill_positive",
      "ece_within_3pct",
      "top20_lift_ci_lower_positive",
    )
  )
  if gates["effect_gate_passed"] != effect_expected:
    raise SelectionArtifactError("模型发布效果门禁逻辑不一致")
  expected_conclusion = (
    "BLOCKED"
    if not effect_expected
    else "SHADOW_ELIGIBLE"
    if not gates["historical_universe_complete"]
    or not gates["unbiased_frozen_evidence"]
    or not gates["access_evidence_valid"]
    else "ACTIVE_ELIGIBLE"
  )
  conclusion = value.get("conclusion", gates.get("conclusion"))
  if conclusion not in _CONCLUSIONS:
    raise SelectionArtifactError("模型发布结论无效")
  normalized_conclusion = str(conclusion)
  if normalized_conclusion != expected_conclusion:
    raise SelectionArtifactError("模型发布结论与门禁不一致")
  if gates.get("conclusion") is not None and gates.get("conclusion") != conclusion:
    raise SelectionArtifactError("模型发布结论在门禁与评估间不一致")
  registerable = value.get("registerable")
  if not isinstance(registerable, bool):
    raise SelectionArtifactError("模型登记资格字段无效")
  expected_registerable = (
    normalized_conclusion != "BLOCKED"
    and gates["access_evidence_valid"]
  )
  if registerable != expected_registerable:
    raise SelectionArtifactError("模型登记资格与运行类型或门禁不一致")

  frozen = value.get("frozen_test")
  if not isinstance(frozen, dict):
    raise SelectionArtifactError("FINAL_EVALUATION 缺少冻结测试证据")
  stability = frozen.get("annual_stability")
  if not isinstance(stability, list) or len(stability) > 20:
    raise SelectionArtifactError("模型年度稳定性结构无效")
  projected_stability: list[dict[str, Any]] = []
  for row in stability:
    if not isinstance(row, dict):
      raise SelectionArtifactError("年度稳定性记录无效")
    projected_stability.append(
      {
        "year": _integer(row.get("year"), "annual_stability.year", minimum=1990),
        "sample_count": _integer(row.get("sample_count"), "annual_stability.sample_count", minimum=1),
        "brier": _number(row.get("brier"), "annual_stability.brier", minimum=0, maximum=1),
        "brier_skill": _number(row.get("brier_skill"), "annual_stability.brier_skill"),
        "ece": _number(row.get("ece"), "annual_stability.ece", minimum=0, maximum=1),
        "top20_up_rate_lift": _number(row.get("top20_up_rate_lift"), "annual_stability.top20_up_rate_lift"),
      }
    )
  access_count = _integer(frozen.get("access_count"), "frozen_test.access_count", minimum=1)
  frozen_projection = {
    "start": _iso_date(frozen.get("start"), "frozen_test.start"),
    "end": _iso_date(frozen.get("end"), "frozen_test.end"),
    "probability": _probability_metrics(frozen.get("probability")),
    "ranking": _ranking_metrics(frozen.get("ranking")),
    "annual_stability": projected_stability,
    "access_count": access_count,
  }

  disagreement = value.get("probability_disagreement")
  disagreement_projection: dict[str, float] | None = None
  if disagreement is not None:
    if not isinstance(disagreement, dict):
      raise SelectionArtifactError("概率差异证据结构无效")
    disagreement_projection = {
      name: _number(disagreement.get(name), f"probability_disagreement.{name}", minimum=0, maximum=1)
      for name in (
        "mean_absolute_difference",
        "median_absolute_difference",
        "max_absolute_difference",
        "fraction_at_least_5pct",
      )
    }
  else:
    raise SelectionArtifactError("FINAL_EVALUATION 缺少概率差异证据")
  return {
    "schema_version": _ARTIFACT_SCHEMA_VERSION,
    "model_version": model_version,
    "selected_family": selected_family,
    "spec_hash": spec_hash,
    "config_hash": config_hash,
    "coordinate_hash": coordinate_hash,
    "calibrator_version": CALIBRATOR_VERSION,
    "training_start": _optional_iso_date(value.get("training_start"), "training_start"),
    "training_end": _optional_iso_date(value.get("training_end"), "training_end"),
    "calibration_start": _optional_iso_date(value.get("calibration_start"), "calibration_start"),
    "calibration_end": _optional_iso_date(value.get("calibration_end"), "calibration_end"),
    "test_start": _optional_iso_date(value.get("test_start"), "test_start"),
    "test_end": _optional_iso_date(value.get("test_end"), "test_end"),
    "validation": validation_projection,
    "frozen_test": frozen_projection,
    "probability_disagreement": disagreement_projection,
    "parent_development": parent_projection,
    "gates": {name: gates[name] for name in gate_names} | {"conclusion": normalized_conclusion, "registerable": registerable},
    "conclusion": normalized_conclusion,
    "registerable": registerable,
  }


def sanitize_selection_data_quality(
  value: dict[str, Any],
) -> dict[str, Any]:
  if not isinstance(value, dict) or value.get("schema_version") != _ARTIFACT_SCHEMA_VERSION:
    raise SelectionArtifactError("模型数据质量必须使用唯一 schema-v2")
  historical = value.get("historical_universe")
  source = value.get("source")
  if not isinstance(historical, dict) or not isinstance(source, dict):
    raise SelectionArtifactError("模型数据质量结构无效")
  if not isinstance(historical.get("complete"), bool):
    raise SelectionArtifactError("历史股票池完整性字段无效")
  fingerprint = _hash_field(value.get("data_fingerprint"), "data_quality.data_fingerprint")
  dataset_hash = _hash_field(value.get("dataset_manifest_sha256"), "data_quality.dataset_manifest_sha256")
  panel_hash = _hash_field(value.get("source_training_panel_sha256"), "data_quality.source_training_panel_sha256")
  reason = historical.get("reason")
  if reason is not None and (not isinstance(reason, str) or len(reason) > 128):
    raise SelectionArtifactError("历史股票池质量原因无效")
  coverage = historical.get("coverage")
  if not isinstance(coverage, dict):
    raise SelectionArtifactError("历史股票池覆盖证据无效")
  complete_coverage = coverage.get("complete", historical["complete"])
  if not isinstance(complete_coverage, bool):
    raise SelectionArtifactError("历史股票池覆盖完整性字段无效")
  projected: dict[str, Any] = {
    "schema_version": _ARTIFACT_SCHEMA_VERSION,
    "dataset_manifest_sha256": dataset_hash,
    "source_training_panel_sha256": panel_hash,
    "data_fingerprint": fingerprint,
    "source_kind": str(source.get("kind") or "indicator-source")[:64],
    "historical_universe": {
      "complete": historical["complete"],
      "reason": reason,
      "coverage": _safe_public_json(coverage),
    },
    "sample_count": _integer(value.get("sample_count"), "data_quality.sample_count", minimum=1),
    "stock_count": _integer(value.get("stock_count"), "data_quality.stock_count", minimum=1),
    "trading_day_count": _integer(value.get("trading_day_count"), "data_quality.trading_day_count", minimum=1),
    "date_count": _integer(value.get("date_count"), "data_quality.date_count", minimum=1),
    "date_start": _iso_date(value.get("date_start"), "data_quality.date_start"),
    "date_end": _iso_date(value.get("date_end"), "data_quality.date_end"),
    "training_start": _optional_iso_date(value.get("training_start"), "data_quality.training_start"),
    "training_end": _optional_iso_date(value.get("training_end"), "data_quality.training_end"),
    "calibration_start": _optional_iso_date(value.get("calibration_start"), "data_quality.calibration_start"),
    "calibration_end": _optional_iso_date(value.get("calibration_end"), "data_quality.calibration_end"),
    "test_start": _optional_iso_date(value.get("test_start"), "data_quality.test_start"),
    "test_end": _optional_iso_date(value.get("test_end"), "data_quality.test_end"),
    "coverage": _safe_public_json(value.get("coverage", {})),
    "leakage_checks": _safe_public_json(value.get("leakage_checks", {})),
  }
  return projected


def _validate_manifest_periods(manifest: dict[str, Any]) -> None:
  names = ("training_start", "training_end", "calibration_start", "calibration_end")
  values = [date.fromisoformat(_iso_date(manifest.get(name), name)) for name in names]
  if not (values[0] <= values[1] < values[2] <= values[3]):
    raise SelectionArtifactError("模型训练、校准区间交叉或倒序")
  test_start = manifest.get("test_start")
  test_end = manifest.get("test_end")
  if test_start is None or test_end is None:
    raise SelectionArtifactError("FINAL_EVALUATION 缺少冻结测试日期")
  test_values = [date.fromisoformat(_iso_date(item, name)) for item, name in ((test_start, "test_start"), (test_end, "test_end"))]
  if not values[3] < test_values[0] <= test_values[1]:
    raise SelectionArtifactError("模型冻结测试区间交叉或倒序")


def _validate_split(value: Any) -> dict[str, Any]:
  if not isinstance(value, dict):
    raise SelectionArtifactError("模型时间切分结构无效")
  try:
    split = validate_time_split(value)
  except (TypeError, KeyError, ValueError) as exc:
    raise SelectionArtifactError("模型时间切分不符合固定 30/6/1/12 契约") from exc
  if (
    split.minimum_training_months != 30
    or split.calibration_months != 6
    or split.validation_months != 1
    or split.frozen_test_size != 12
  ):
    raise SelectionArtifactError("模型时间切分不符合固定 30/6/1/12 契约")
  return split.as_dict()


def _validate_preprocessors(value: dict[str, Any]) -> None:
  expected = list(selection_feature_columns())
  families = value.get("families")
  if value.get("schema_version") != 1 or not isinstance(families, dict):
    raise SelectionArtifactError("预处理产物版本无效")
  for family in ("LOGISTIC", "LIGHTGBM"):
    item = families.get(family)
    if not isinstance(item, dict) or item.get("feature_columns") != expected:
      raise SelectionArtifactError(f"{family} 预处理特征契约不一致")
    for key in ("imputation_values", "means", "scales", "ood_lower", "ood_upper"):
      mapping = item.get(key)
      if not isinstance(mapping, dict) or set(mapping) != set(expected):
        raise SelectionArtifactError(f"{family} 预处理字段不完整: {key}")
      for column, raw in mapping.items():
        number = _number(raw, f"{family}.{key}.{column}")
        if key == "scales" and number <= 0:
          raise SelectionArtifactError(f"{family} 预处理尺度必须为正数")
    for column in expected:
      if float(item["ood_lower"][column]) > float(item["ood_upper"][column]):
        raise SelectionArtifactError(f"{family} OOD 边界顺序无效: {column}")


def _validate_calibrators(value: dict[str, Any]) -> None:
  if value.get("schema_version") != 1 or value.get("version") != CALIBRATOR_VERSION:
    raise SelectionArtifactError("校准器版本不一致")
  families = value.get("families")
  if not isinstance(families, dict) or set(families) != {"LOGISTIC", "LIGHTGBM"}:
    raise SelectionArtifactError("校准器模型家族不完整")
  for family, calibrator in families.items():
    if not isinstance(calibrator, dict):
      raise SelectionArtifactError(f"{family} 校准器结构无效")
    if calibrator.get("version") != CALIBRATOR_VERSION:
      raise SelectionArtifactError(f"{family} 校准器版本无效")
    sample_count = _integer(
      calibrator.get("calibration_sample_count"),
      f"{family}.calibration_sample_count",
      minimum=1,
    )
    positive_count = _integer(
      calibrator.get("calibration_positive_count"),
      f"{family}.calibration_positive_count",
    )
    if positive_count > sample_count:
      raise SelectionArtifactError(f"{family} 校准正样本数超过总样本数")
    kind = calibrator.get("kind")
    if kind == "platt":
      _number(calibrator.get("slope"), f"{family}.slope")
      _number(calibrator.get("intercept"), f"{family}.intercept")
      continue
    if kind != "isotonic":
      raise SelectionArtifactError(f"{family} 校准器类型无效")
    x = calibrator.get("x_thresholds")
    y = calibrator.get("y_thresholds")
    if (
      not isinstance(x, list)
      or not isinstance(y, list)
      or len(x) != len(y)
      or not 2 <= len(x) <= 100_000
    ):
      raise SelectionArtifactError(f"{family} Isotonic 阈值无效")
    x_values = [_number(item, f"{family}.x_thresholds") for item in x]
    y_values = [
      _number(item, f"{family}.y_thresholds", minimum=0, maximum=1) for item in y
    ]
    if any(right < left for left, right in zip(x_values, x_values[1:])) or any(
      right < left for left, right in zip(y_values, y_values[1:])
    ):
      raise SelectionArtifactError(f"{family} Isotonic 校准器必须单调")


def _validate_safe_evidence(value: Any, field: str, *, depth: int = 0) -> None:
  """Validate bounded, non-sensitive JSON evidence before it is exposed."""

  if depth > _MAX_EVIDENCE_DEPTH:
    raise SelectionArtifactError(f"模型证据嵌套过深: {field}")
  if value is None or isinstance(value, bool):
    return
  if isinstance(value, int):
    if abs(value) > 2**63:
      raise SelectionArtifactError(f"模型证据整数超出范围: {field}")
    return
  if isinstance(value, float):
    if not math.isfinite(value) or abs(value) > 1e18:
      raise SelectionArtifactError(f"模型证据数值无效: {field}")
    return
  if isinstance(value, str):
    if len(value) > 512 or _ABSOLUTE_PATH.match(value):
      raise SelectionArtifactError(f"模型证据字符串无效: {field}")
    return
  if isinstance(value, list):
    if len(value) > _MAX_EVIDENCE_ENTRIES:
      raise SelectionArtifactError(f"模型证据列表超出范围: {field}")
    for index, item in enumerate(value):
      _validate_safe_evidence(item, f"{field}[{index}]", depth=depth + 1)
    return
  if isinstance(value, dict):
    if len(value) > _MAX_EVIDENCE_ENTRIES:
      raise SelectionArtifactError(f"模型证据 object 超出范围: {field}")
    for name, item in value.items():
      if not isinstance(name, str) or len(name) > 128:
        raise SelectionArtifactError(f"模型证据字段名无效: {field}")
      lowered = name.lower()
      if any(token in lowered for token in _SENSITIVE_EVIDENCE_KEYS):
        raise SelectionArtifactError(f"模型证据包含私密字段: {field}.{name}")
      _validate_safe_evidence(item, f"{field}.{name}", depth=depth + 1)
    return
  raise SelectionArtifactError(f"模型证据类型无效: {field}")


def _required_bounded_string(value: Any, field: str, *, maximum: int = 160) -> str:
  if not isinstance(value, str) or not value or len(value) > maximum:
    raise SelectionArtifactError(f"模型环境字段无效: {field}")
  if _ABSOLUTE_PATH.match(value):
    raise SelectionArtifactError(f"模型环境字段包含路径: {field}")
  return value


def _validate_environment(
  value: Any,
  *,
  manifest: dict[str, Any],
  resolved_backend: str,
) -> None:
  if not isinstance(value, dict):
    raise SelectionArtifactError("模型清单缺少 schema-v2 environment")
  required = {
    "python",
    "platform",
    "pandas",
    "numpy",
    "dependencies",
    "environment_requirement_hash",
    "qualification_version",
    "requirement_hash",
    "evidence_sha256",
    "gpu",
    "opencl",
  }
  if not required.issubset(value):
    raise SelectionArtifactError("模型 environment 证据字段不完整")
  _validate_safe_evidence(value, "manifest.environment")
  for field in ("python", "platform", "pandas", "numpy"):
    _required_bounded_string(value.get(field), f"environment.{field}")
  dependencies = value.get("dependencies")
  if not isinstance(dependencies, dict) or len(dependencies) > 64:
    raise SelectionArtifactError("模型环境依赖证据无效")
  for name, version in dependencies.items():
    _required_bounded_string(name, "environment.dependencies.name", maximum=96)
    if version is not None:
      _required_bounded_string(
        version,
        f"environment.dependencies.{name}",
        maximum=96,
      )
  environment_hash = _hash_field(
    value.get("environment_requirement_hash"),
    "manifest.environment.environment_requirement_hash",
  )
  if environment_hash != manifest.get("environment_requirement_hash"):
    raise SelectionArtifactError("环境要求哈希在清单与环境证据间不一致")
  qualification_version = value.get("qualification_version")
  if qualification_version is not None:
    _required_bounded_string(
      qualification_version,
      "environment.qualification_version",
      maximum=96,
    )
  for field in ("requirement_hash", "evidence_sha256"):
    raw = value.get(field)
    if raw is not None:
      _hash_field(raw, f"environment.{field}")
  gpu = value.get("gpu")
  opencl = value.get("opencl")
  if resolved_backend == "CPU":
    if gpu is not None or opencl is not None:
      raise SelectionArtifactError("CPU 运行不得包含 GPU 或 OpenCL 环境证据")
  else:
    if not isinstance(gpu, dict) or not gpu:
      raise SelectionArtifactError("GPU 运行缺少 GPU 环境证据")
    if not isinstance(opencl, dict) or not opencl:
      raise SelectionArtifactError("GPU 运行缺少 OpenCL 环境证据")


def _validate_telemetry(
  value: Any,
  *,
  manifest: dict[str, Any],
  resolved_backend: str,
  max_bin: int,
  gpu_use_dp: bool,
) -> None:
  if not isinstance(value, dict):
    raise SelectionArtifactError("模型清单缺少 schema-v2 telemetry")
  required = {
    "wall_time_seconds",
    "process_cpu_time_seconds",
    "runtime_memory",
    "gpu",
    "qualification",
    "lightgbm",
    "environment",
  }
  if not required.issubset(value):
    raise SelectionArtifactError("模型运行 telemetry 字段不完整")
  _validate_safe_evidence(value, "manifest.telemetry")
  _number(
    value.get("wall_time_seconds"),
    "telemetry.wall_time_seconds",
    minimum=0,
    maximum=_MAX_TELEMETRY_SECONDS,
  )
  _number(
    value.get("process_cpu_time_seconds"),
    "telemetry.process_cpu_time_seconds",
    minimum=0,
    maximum=_MAX_TELEMETRY_SECONDS,
  )
  runtime_memory = value.get("runtime_memory")
  if not isinstance(runtime_memory, dict) or runtime_memory.get("physical_only") is not True:
    raise SelectionArtifactError("模型运行内存 telemetry 无效")
  _number(runtime_memory.get("reserve_gib"), "telemetry.runtime_memory.reserve_gib", minimum=0, maximum=1024)
  _number(runtime_memory.get("peak_process_rss_gib"), "telemetry.runtime_memory.peak_process_rss_gib", minimum=0, maximum=1024 * 1024)
  sampling_error = runtime_memory.get("sampling_error")
  if sampling_error is not None:
    _required_bounded_string(sampling_error, "telemetry.runtime_memory.sampling_error", maximum=256)

  gpu_telemetry = value.get("gpu")
  if not isinstance(gpu_telemetry, dict):
    raise SelectionArtifactError("模型 GPU telemetry 无效")
  for field in (
    "sampling_available",
    "sample_count",
    "peak_memory_fraction",
    "peak_used_memory_mib",
    "minimum_available_memory_mib",
  ):
    if field not in gpu_telemetry:
      raise SelectionArtifactError(f"模型 GPU telemetry 缺少 {field}")
  sampling_available = gpu_telemetry.get("sampling_available")
  sample_count = _integer(gpu_telemetry.get("sample_count"), "telemetry.gpu.sample_count")
  peak_fraction = _optional_number(
    gpu_telemetry.get("peak_memory_fraction"),
    "telemetry.gpu.peak_memory_fraction",
    minimum=0,
    maximum=1,
  )
  peak_used = _optional_number(
    gpu_telemetry.get("peak_used_memory_mib"),
    "telemetry.gpu.peak_used_memory_mib",
    minimum=0,
    maximum=1024 * 1024,
  )
  minimum_available = _optional_number(
    gpu_telemetry.get("minimum_available_memory_mib"),
    "telemetry.gpu.minimum_available_memory_mib",
    minimum=0,
    maximum=1024 * 1024,
  )
  if resolved_backend == "CPU":
    if (
      sampling_available is not False
      or sample_count != 0
      or peak_fraction is not None
      or peak_used is not None
      or minimum_available is not None
    ):
      raise SelectionArtifactError("CPU 运行不得伪造 GPU 峰值或采样")
  elif (
    sampling_available is not True
    or sample_count < 1
    or peak_fraction is None
    or peak_used is None
    or minimum_available is None
  ):
    raise SelectionArtifactError("GPU 运行必须包含有效显存采样")

  qualification = value.get("qualification")
  if not isinstance(qualification, dict):
    raise SelectionArtifactError("模型 telemetry 资格投影无效")
  if not {
    "status",
    "acceleration",
    "minimum_sample_count",
    "peak_memory_fraction",
    "gates_passed",
    "evidence_sha256",
  }.issubset(qualification):
    raise SelectionArtifactError("模型 telemetry 资格投影字段不完整")
  _required_bounded_string(qualification.get("status"), "telemetry.qualification.status", maximum=96)
  if qualification.get("status") not in _QUALIFICATION_STATUSES:
    raise SelectionArtifactError("模型 telemetry 资格状态无效")
  if not isinstance(qualification.get("gates_passed"), bool):
    raise SelectionArtifactError("模型 telemetry 资格门禁无效")
  for field in ("acceleration", "peak_memory_fraction"):
    _optional_number(
      qualification.get(field),
      f"telemetry.qualification.{field}",
      minimum=0,
      maximum=1024,
    )
  if qualification.get("minimum_sample_count") is not None:
    _integer(
      qualification.get("minimum_sample_count"),
      "telemetry.qualification.minimum_sample_count",
      minimum=1,
    )
  if qualification.get("evidence_sha256") is not None:
    _hash_field(qualification.get("evidence_sha256"), "telemetry.qualification.evidence_sha256")
  if resolved_backend == "LIGHTGBM_OPENCL_GPU":
    if (
      qualification.get("status") != "GPU_AVAILABLE"
      or qualification.get("gates_passed") is not True
      or not qualification.get("evidence_sha256")
    ):
      raise SelectionArtifactError("GPU 运行资格状态或证据无效")
  elif (
    qualification.get("status") != "CPU_AVAILABLE"
    or qualification.get("gates_passed") is not True
  ):
    raise SelectionArtifactError("CPU 成功运行资格状态或门禁无效")

  lightgbm = value.get("lightgbm")
  if not isinstance(lightgbm, dict):
    raise SelectionArtifactError("模型 LightGBM telemetry 无效")
  if (
    lightgbm.get("backend") != resolved_backend
    or lightgbm.get("device_type") != (
      "gpu" if resolved_backend == "LIGHTGBM_OPENCL_GPU" else "cpu"
    )
  ):
    raise SelectionArtifactError("模型 LightGBM telemetry 后端不一致")
  if lightgbm.get("max_bin") != max_bin or lightgbm.get("gpu_use_dp") != gpu_use_dp:
    raise SelectionArtifactError("模型 LightGBM max_bin 或 gpu_use_dp 不一致")
  parameters = lightgbm.get("parameters")
  if (
    not isinstance(parameters, dict)
    or set(parameters) != {"LOGISTIC", "LIGHTGBM"}
    or any(not isinstance(parameters.get(family), dict) for family in ("LOGISTIC", "LIGHTGBM"))
  ):
    raise SelectionArtifactError("模型 telemetry 缺少实际 LightGBM 参数")
  for family in ("LOGISTIC", "LIGHTGBM"):
    if len(parameters[family]) > _MAX_RUNTIME_PARAMETER_ENTRIES:
      raise SelectionArtifactError(f"模型 telemetry {family} 参数过多")
    _validate_safe_evidence(parameters[family], f"telemetry.lightgbm.parameters.{family}")
  environment = value.get("environment")
  _validate_environment(
    environment,
    manifest=manifest,
    resolved_backend=resolved_backend,
  )
  if resolved_backend == "LIGHTGBM_OPENCL_GPU" and (
    not isinstance(environment, dict)
    or not environment.get("qualification_version")
    or not environment.get("requirement_hash")
    or not environment.get("evidence_sha256")
  ):
    raise SelectionArtifactError("GPU 运行缺少完整资格环境证据")
  if environment != manifest.get("environment"):
    raise SelectionArtifactError("模型 environment 在清单与 telemetry 间不一致")


def _validate_runtime(value: dict[str, Any]) -> tuple[str, str, int, bool]:
  if value.get("schema_version") != _ARTIFACT_SCHEMA_VERSION:
    raise SelectionArtifactError("模型运行时必须使用 schema-v2")
  if set(value) != {
    "schema_version",
    "selected_family",
    "families",
    "backend",
    "max_bin",
    "gpu_use_dp",
  }:
    raise SelectionArtifactError("模型运行时字段集合无效")
  selected = value.get("selected_family")
  backend = value.get("backend")
  families = value.get("families")
  if selected not in {"LOGISTIC", "LIGHTGBM"}:
    raise SelectionArtifactError("模型主家族无效")
  if backend not in {"CPU", "LIGHTGBM_OPENCL_GPU"}:
    raise SelectionArtifactError("模型运行时后端无效")
  if not isinstance(families, dict) or set(families) != {"LOGISTIC", "LIGHTGBM"}:
    raise SelectionArtifactError("模型运行时家族不完整")
  expected_paths = {"LOGISTIC": "logistic.json", "LIGHTGBM": "lightgbm.txt"}
  for family, model_path in expected_paths.items():
    item = families.get(family)
    if (
      not isinstance(item, dict)
      or item.get("family") != family
      or item.get("model_path") != model_path
      or item.get("preprocessing_path") != "preprocessing.json"
      or item.get("calibrator_path") != "calibrators.json"
    ):
      raise SelectionArtifactError(f"{family} 运行时路径契约无效")
  max_bin = _integer(value.get("max_bin"), "model-runtime.max_bin", minimum=1)
  if max_bin > 1024:
    raise SelectionArtifactError("模型运行时 max_bin 超出范围")
  if not isinstance(value.get("gpu_use_dp"), bool):
    raise SelectionArtifactError("模型运行时 GPU 精度开关无效")
  return str(selected), str(backend), max_bin, value["gpu_use_dp"]


def _validate_logistic(value: dict[str, Any]) -> None:
  coefficients = value.get("coefficients")
  if (
    value.get("schema_version") != 1
    or value.get("family") != "LOGISTIC"
    or not isinstance(coefficients, list)
    or len(coefficients) != len(selection_feature_columns())
  ):
    raise SelectionArtifactError("Logistic 模型结构无效")
  for index, coefficient in enumerate(coefficients):
    _number(coefficient, f"logistic.coefficients[{index}]")
  _number(value.get("intercept"), "logistic.intercept")


def _validate_lightgbm(path: Path) -> None:
  try:
    payload = path.read_bytes()
  except OSError as exc:
    raise SelectionArtifactError("LightGBM 模型产物无法读取") from exc
  if not payload or len(payload) > _MAX_MODEL_BYTES:
    raise SelectionArtifactError("LightGBM 模型产物大小无效")
  try:
    text = payload.decode("utf-8")
  except UnicodeDecodeError as exc:
    raise SelectionArtifactError("LightGBM 模型产物编码无效") from exc
  if "tree" not in text.lower() and "version=" not in text.lower():
    raise SelectionArtifactError("LightGBM 模型产物内容无效")


def load_selection_artifact(
  directory: str | Path,
  *,
  expected_manifest_sha256: str | None = None,
) -> SelectionArtifactBundle:
  unresolved_root = Path(directory).expanduser()
  if _is_link_like(unresolved_root):
    raise SelectionArtifactError("模型运行目录不得为符号链接或联接点")
  root = unresolved_root.resolve(strict=True)
  if not root.is_dir():
    raise SelectionArtifactError("模型运行目录无效")
  manifest_path = _safe_file(root, "manifest.json", max_bytes=_MAX_JSON_BYTES)
  manifest_hash = file_sha256(manifest_path)
  if expected_manifest_sha256 is not None and manifest_hash != expected_manifest_sha256:
    raise SelectionArtifactError("模型清单与登记哈希不一致")
  manifest = _json(root, "manifest.json")
  if manifest.get("manifest_sha256") is not None and manifest.get("manifest_sha256") != manifest_hash:
    raise SelectionArtifactError("模型清单自带哈希不一致")
  run_kind = manifest.get("run_kind")
  run_id = manifest.get("run_id")
  if run_kind == "DEVELOPMENT":
    raise SelectionArtifactError("DEVELOPMENT 产物不可作为模型 bundle")
  if (
    manifest.get("schema_version") != _ARTIFACT_SCHEMA_VERSION
    or manifest.get("study_id") != "next-day-selection"
    or manifest.get("version") != "v1"
    or manifest.get("status") != "SUCCEEDED"
    or run_kind != _FINAL_RUN_KIND
    or not isinstance(run_id, str)
    or not _RUN_ID.fullmatch(run_id)
    or run_id != root.name
  ):
    raise SelectionArtifactError("模型研究运行身份或状态无效")
  # This bundle is the inference/registration boundary.  DEVELOPMENT output
  # is intentionally inspected through the training-run projection only and
  # must never become a model runtime input.
  model_version = manifest.get("model_version")
  if not isinstance(model_version, str) or not _MODEL_VERSION.fullmatch(model_version):
    raise SelectionArtifactError("模型版本格式无效")
  expected_identity = {
    "indicator_version": INDICATOR_VERSION,
    "factor_set_version": FACTOR_SET_VERSION,
    "factor_set_hash": FACTOR_SET_HASH,
    "label_version": LABEL_VERSION,
    "calibrator_version": CALIBRATOR_VERSION,
  }
  if any(manifest.get(key) != value for key, value in expected_identity.items()):
    raise SelectionArtifactError("模型版本契约与当前运行时不一致")
  config_hash = manifest.get("config_hash")
  data_fingerprint = manifest.get("data_fingerprint")
  if (
    not isinstance(config_hash, str)
    or not _HASH.fullmatch(config_hash)
    or not isinstance(data_fingerprint, str)
    or not _HASH.fullmatch(data_fingerprint)
  ):
    raise SelectionArtifactError("模型配置或数据指纹无效")
  spec_hash = _hash_field(manifest.get("spec_hash"), "manifest.spec_hash")
  coordinate_hash = _hash_field(manifest.get("coordinate_hash"), "manifest.coordinate_hash")
  dataset_manifest_sha256 = _hash_field(
    manifest.get("dataset_manifest_sha256"), "manifest.dataset_manifest_sha256"
  )
  training_panel_sha256 = _hash_field(
    manifest.get("training_panel_sha256"), "manifest.training_panel_sha256"
  )
  requested_backend = manifest.get("requested_backend")
  manifest_backend = manifest.get("resolved_backend")
  if requested_backend not in {"AUTO", "CPU", "GPU_REQUIRED"}:
    raise SelectionArtifactError("模型请求后端无效")
  if manifest_backend not in {"CPU", "LIGHTGBM_OPENCL_GPU"}:
    raise SelectionArtifactError("模型解析后端无效")
  if (
    requested_backend == "CPU" and manifest_backend != "CPU"
  ) or (
    requested_backend == "GPU_REQUIRED"
    and manifest_backend != "LIGHTGBM_OPENCL_GPU"
  ):
    raise SelectionArtifactError("模型请求后端与解析后端不一致")
  split = _validate_split(manifest.get("split"))
  _validate_manifest_periods(manifest)
  _verify_index(root, manifest)
  metrics = sanitize_selection_metrics(_json(root, "metrics.json"))
  data_quality = sanitize_selection_data_quality(_json(root, "data-quality.json"))
  runtime = _json(root, "model-runtime.json")
  preprocessing = _json(root, "preprocessing.json")
  calibrators = _json(root, "calibrators.json")
  logistic = _json(root, "logistic.json")
  factor_schema = _json(root, "factor-schema.json")
  expected_factor_schema = json.loads(
    json.dumps(factor_schema_manifest(), ensure_ascii=False, sort_keys=True)
  )
  if factor_schema != expected_factor_schema:
    raise SelectionArtifactError("模型因子结构清单与当前运行时不一致")
  _validate_preprocessors(preprocessing)
  selected_family, resolved_backend, max_bin, gpu_use_dp = _validate_runtime(runtime)
  _validate_telemetry(
    manifest.get("telemetry"),
    manifest=manifest,
    resolved_backend=resolved_backend,
    max_bin=max_bin,
    gpu_use_dp=gpu_use_dp,
  )
  _validate_calibrators(calibrators)
  _validate_logistic(logistic)
  lightgbm_path = _safe_file(root, "lightgbm.txt", max_bytes=_MAX_MODEL_BYTES)
  _validate_lightgbm(lightgbm_path)
  if metrics.get("model_version") != model_version:
    raise SelectionArtifactError("模型评估与清单版本不一致")
  if (
    metrics.get("selected_family") != selected_family
    or manifest.get("selected_family") != selected_family
  ):
    raise SelectionArtifactError("模型主家族在产物间不一致")
  if resolved_backend != manifest.get("resolved_backend"):
    raise SelectionArtifactError("模型后端在清单与运行时不一致")
  hash_values = {
    "spec_hash": spec_hash,
    "coordinate_hash": coordinate_hash,
    "config_hash": config_hash,
  }
  for field, manifest_hash in hash_values.items():
    if metrics.get(field) != manifest_hash:
      raise SelectionArtifactError(f"模型 {field} 在清单与评估间不一致")
  if metrics.get("gates") != manifest.get("gates"):
    raise SelectionArtifactError("模型门禁在清单与评估间不一致")
  if metrics.get("conclusion") != manifest.get("conclusion"):
    raise SelectionArtifactError("模型结论在清单与评估间不一致")
  if metrics.get("registerable") != manifest.get("registerable"):
    raise SelectionArtifactError("模型登记资格在清单与评估间不一致")
  if data_quality.get("data_fingerprint") != manifest.get("data_fingerprint"):
    raise SelectionArtifactError("模型数据指纹在清单与质量证据间不一致")
  if data_quality.get("dataset_manifest_sha256") != dataset_manifest_sha256:
    raise SelectionArtifactError("认证数据集哈希在清单与质量证据间不一致")
  if data_quality.get("source_training_panel_sha256") != training_panel_sha256:
    raise SelectionArtifactError("训练面板哈希在清单与质量证据间不一致")
  if (
    data_quality["historical_universe"]["complete"]
    != metrics["gates"]["historical_universe_complete"]
  ):
    raise SelectionArtifactError("历史股票池完整性在质量证据与门禁间不一致")
  if metrics.get("training_start") != manifest.get("training_start"):
    raise SelectionArtifactError("训练起始日期在清单与评估间不一致")
  if metrics.get("training_end") != manifest.get("training_end"):
    raise SelectionArtifactError("训练结束日期在清单与评估间不一致")
  if metrics.get("calibration_start") != manifest.get("calibration_start"):
    raise SelectionArtifactError("校准起始日期在清单与评估间不一致")
  if metrics.get("calibration_end") != manifest.get("calibration_end"):
    raise SelectionArtifactError("校准结束日期在清单与评估间不一致")
  if metrics.get("test_start") != manifest.get("test_start") or metrics.get("test_end") != manifest.get("test_end"):
    raise SelectionArtifactError("冻结测试日期在清单与评估间不一致")
  frozen = metrics.get("frozen_test")
  if not isinstance(frozen, dict):
    raise SelectionArtifactError("冻结测试证据缺失")
  if frozen["start"] != manifest.get("test_start") or frozen["end"] != manifest.get("test_end"):
    raise SelectionArtifactError("冻结测试日期在清单与评估间不一致")
  if _safe_public_json(manifest.get("projection", {})) is None:
    raise SelectionArtifactError("模型数据投影无效")
  # The serialized split is part of the coordinate identity.  Reconstructing
  # it here prevents a caller from mixing a model and a differently shaped
  # validation timeline.
  if manifest.get("split") != split:
    raise SelectionArtifactError("模型时间切分无法规范化")
  return SelectionArtifactBundle(
    directory=root,
    manifest_sha256=manifest_hash,
    manifest=manifest,
    metrics=metrics,
    data_quality=data_quality,
    runtime=runtime,
    preprocessing=preprocessing,
    calibrators=calibrators,
    logistic=logistic,
    lightgbm_path=lightgbm_path,
  )
