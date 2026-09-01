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

_HASH = re.compile(r"^[0-9a-f]{64}$")
_MODEL_VERSION = re.compile(r"^next-day-up-v1-[0-9a-f]{16}$")
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
  return value


def _reject_json_constant(token: str) -> None:
  raise ValueError(f"不允许的 JSON 数值: {token}")


def _verify_index(directory: Path, manifest: dict[str, Any]) -> None:
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
    if relative in indexed:
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
  missing = _REQUIRED_FILES - ({"manifest.json"} | set(indexed))
  if missing:
    raise SelectionArtifactError(f"模型清单缺少必要产物: {sorted(missing)}")
  forbidden = [
    path
    for path in indexed
    if Path(path).suffix.lower() in {".pkl", ".pickle", ".joblib"}
  ]
  if forbidden:
    raise SelectionArtifactError("模型清单包含不安全的 Python 序列化产物")


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


def sanitize_selection_metrics(value: dict[str, Any]) -> dict[str, Any]:
  validation = value.get("validation")
  frozen = value.get("frozen_test")
  gates = value.get("gates")
  stability = frozen.get("annual_stability") if isinstance(frozen, dict) else None
  if not isinstance(validation, dict) or not isinstance(frozen, dict):
    raise SelectionArtifactError("模型评估结构无效")
  if (
    not isinstance(gates, dict)
    or not isinstance(stability, list)
    or len(stability) > 20
  ):
    raise SelectionArtifactError("模型门禁或年度稳定性结构无效")
  projected_stability: list[dict[str, Any]] = []
  for row in stability:
    if not isinstance(row, dict):
      raise SelectionArtifactError("年度稳定性记录无效")
    projected_stability.append(
      {
        "year": _integer(row.get("year"), "annual_stability.year", minimum=1990),
        "sample_count": _integer(
          row.get("sample_count"), "annual_stability.sample_count", minimum=1
        ),
        "brier": _number(
          row.get("brier"), "annual_stability.brier", minimum=0, maximum=1
        ),
        "brier_skill": _number(row.get("brier_skill"), "annual_stability.brier_skill"),
        "ece": _number(row.get("ece"), "annual_stability.ece", minimum=0, maximum=1),
        "top20_up_rate_lift": _number(
          row.get("top20_up_rate_lift"), "annual_stability.top20_up_rate_lift"
        ),
      }
    )
  gate_names = (
    "brier_skill_positive",
    "ece_within_3pct",
    "top20_lift_ci_lower_positive",
    "historical_universe_complete",
    "effect_gate_passed",
    "active_eligible",
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
  if gates["effect_gate_passed"] != effect_expected or gates["active_eligible"] != (
    effect_expected and gates["historical_universe_complete"]
  ):
    raise SelectionArtifactError("模型发布门禁逻辑不一致")
  schema_version = _integer(
    value.get("schema_version"), "metrics.schema_version", minimum=1
  )
  if schema_version != 1:
    raise SelectionArtifactError("模型评估版本无效")
  return {
    "schema_version": schema_version,
    "model_version": str(value.get("model_version", "")),
    "selected_family": str(value.get("selected_family", "")),
    "validation": {
      "fold_count": _integer(
        validation.get("fold_count"), "validation.fold_count", minimum=1
      ),
      "logistic_brier": _number(
        validation.get("logistic_brier"),
        "validation.logistic_brier",
        minimum=0,
        maximum=1,
      ),
      "lightgbm_brier": _number(
        validation.get("lightgbm_brier"),
        "validation.lightgbm_brier",
        minimum=0,
        maximum=1,
      ),
    },
    "frozen_test": {
      "start": _iso_date(frozen.get("start"), "frozen_test.start"),
      "end": _iso_date(frozen.get("end"), "frozen_test.end"),
      "probability": _probability_metrics(frozen.get("probability")),
      "ranking": _ranking_metrics(frozen.get("ranking")),
      "annual_stability": projected_stability,
    },
    "gates": {name: gates[name] for name in gate_names},
  }


def sanitize_selection_data_quality(value: dict[str, Any]) -> dict[str, Any]:
  historical = value.get("historical_universe")
  source = value.get("source")
  if not isinstance(historical, dict) or not isinstance(source, dict):
    raise SelectionArtifactError("模型数据质量结构无效")
  if not isinstance(historical.get("complete"), bool):
    raise SelectionArtifactError("历史股票池完整性字段无效")
  fingerprint = value.get("data_fingerprint")
  if not isinstance(fingerprint, str) or not _HASH.fullmatch(fingerprint):
    raise SelectionArtifactError("模型数据指纹无效")
  reason = historical.get("reason")
  if reason is not None and (not isinstance(reason, str) or len(reason) > 128):
    raise SelectionArtifactError("历史股票池质量原因无效")
  return {
    "source_kind": str(source.get("kind") or "indicator-source")[:64],
    "historical_universe": {
      "complete": historical["complete"],
      "reason": reason,
      "coverage": _number(
        historical.get("coverage"), "historical_universe.coverage", minimum=0, maximum=1
      ),
    },
    "data_fingerprint": fingerprint,
    "sample_count": _integer(
      value.get("sample_count"), "data_quality.sample_count", minimum=1
    ),
    "stock_count": _integer(
      value.get("stock_count"), "data_quality.stock_count", minimum=1
    ),
    "date_count": _integer(
      value.get("date_count"), "data_quality.date_count", minimum=1
    ),
    "data_start": _iso_date(value.get("data_start"), "data_quality.data_start"),
    "data_end": _iso_date(value.get("data_end"), "data_quality.data_end"),
  }


def _validate_manifest_periods(manifest: dict[str, Any]) -> None:
  names = (
    "training_start",
    "training_end",
    "calibration_start",
    "calibration_end",
    "test_start",
    "test_end",
  )
  values = [date.fromisoformat(_iso_date(manifest.get(name), name)) for name in names]
  if not (values[0] <= values[1] < values[2] <= values[3] < values[4] <= values[5]):
    raise SelectionArtifactError("模型训练、校准与冻结测试区间交叉或倒序")


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


def _validate_runtime(value: dict[str, Any]) -> str:
  selected = value.get("selected_family")
  families = value.get("families")
  if selected not in {"LOGISTIC", "LIGHTGBM"}:
    raise SelectionArtifactError("模型主家族无效")
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
  return str(selected)


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
  if (
    manifest.get("schema_version") != 1
    or manifest.get("study_id") != "next-day-selection"
    or manifest.get("version") != "v1"
    or manifest.get("status") != "success"
  ):
    raise SelectionArtifactError("模型研究运行身份或状态无效")
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
  selected_family = _validate_runtime(runtime)
  _validate_calibrators(calibrators)
  _validate_logistic(logistic)
  if metrics.get("model_version") != model_version:
    raise SelectionArtifactError("模型评估与清单版本不一致")
  if (
    metrics.get("selected_family") != selected_family
    or manifest.get("selected_family") != selected_family
  ):
    raise SelectionArtifactError("模型主家族在产物间不一致")
  if metrics.get("gates") != manifest.get("gates"):
    raise SelectionArtifactError("模型门禁在清单与评估间不一致")
  if data_quality.get("data_fingerprint") != manifest.get("data_fingerprint"):
    raise SelectionArtifactError("模型数据指纹在清单与质量证据间不一致")
  if (
    data_quality["historical_universe"]["complete"]
    != metrics["gates"]["historical_universe_complete"]
  ):
    raise SelectionArtifactError("历史股票池完整性在质量证据与门禁间不一致")
  frozen = metrics["frozen_test"]
  if frozen["start"] != manifest.get("test_start") or frozen["end"] != manifest.get(
    "test_end"
  ):
    raise SelectionArtifactError("冻结测试日期在清单与评估间不一致")
  lightgbm_path = _safe_file(root, "lightgbm.txt", max_bytes=_MAX_MODEL_BYTES)
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
