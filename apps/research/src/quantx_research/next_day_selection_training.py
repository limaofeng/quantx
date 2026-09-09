"""Manual, point-in-time training for next-open-to-close up probability."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

import lightgbm as lgb
import numpy as np
import pandas as pd
from quantx_domain.indicators import INDICATOR_VERSION
from quantx_domain.selection_factors import (
  FACTOR_SET_HASH,
  FACTOR_SET_VERSION,
  LABEL_VERSION,
  SELECTION_FACTOR_DEFINITIONS,
  build_selection_factor_frame,
  factor_schema_manifest,
  selection_factor_completeness,
  selection_feature_columns,
)
from quantx_domain.selection_model import CALIBRATOR_VERSION, sigmoid
from quantx_domain.stock_selection_training import (
  GateConclusion,
  GateEvidence,
  RequestedBackend,
  ResolvedBackend,
  RunKind,
  TrainingPhase,
  build_training_time_split,
  gate_conclusion,
  stable_json_sha256,
)
from quantx_infrastructure.training_host_guard import training_cpu_threads
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from quantx_research.artifacts import (
  artifact_index,
  file_sha256,
  runtime_metadata,
  write_json,
  write_yaml,
)
from quantx_research.indicator_config import IndicatorStudyConfig
from quantx_research.indicator_runner import stage_indicator_features
from quantx_research.next_day_selection_config import (
  NextDaySelectionConfig,
  load_next_day_selection_config,
)
from quantx_research.next_day_selection_metrics import (
  annual_stability,
  brier_score,
  evaluate_probability,
  evaluate_ranking,
)
from quantx_research.runner import REPO_ROOT, _research_source
from quantx_research.runtime_memory import RuntimeMemoryMonitor

_ORDINARY_A_SHARE = re.compile(
  r"^(?:(?:600|601|603|605|688|689)\d{3}\.SH|"
  r"(?:000|001|002|003|300|301)\d{3}\.SZ)$"
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _locked_hash(spec: Mapping[str, Any], field: str) -> str:
  value = spec.get(field)
  if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
    raise ValueError(f"训练 spec 的 {field} 必须是小写 SHA-256")
  return value


@dataclass(frozen=True)
class WalkForwardFold:
  train_months: tuple[pd.Period, ...]
  calibration_months: tuple[pd.Period, ...]
  validation_month: pd.Period


@dataclass
class FittedFamily:
  family: Literal["LOGISTIC", "LIGHTGBM"]
  model: Any
  preprocessor: dict[str, Any]
  calibrator: dict[str, Any]


class _GpuRuntimeSampler:
  """Sample nvidia-smi only for an already locked GPU run."""

  def __init__(self, interval_seconds: float) -> None:
    from quantx_research.next_day_selection_gpu import _run_nvidia_smi

    self._read = _run_nvidia_smi
    self._interval_seconds = max(float(interval_seconds), 0.05)
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None
    self._snapshots: list[dict[str, Any]] = []

  def start(self) -> None:
    self._sample()
    self._thread = threading.Thread(
      target=self._sample_loop,
      name="quantx-research-gpu-memory",
      daemon=True,
    )
    self._thread.start()

  def _sample_loop(self) -> None:
    while not self._stop.wait(self._interval_seconds):
      self._sample()

  def _sample(self) -> None:
    try:
      value = self._read()
    except Exception:
      return
    if not isinstance(value, Mapping):
      return
    try:
      total = float(value.get("memory_total_mib"))
      free = float(value.get("memory_free_mib"))
    except (TypeError, ValueError):
      return
    if not math.isfinite(total) or not math.isfinite(free) or total <= 0 or free < 0:
      return
    self._snapshots.append(
      {
        "memory_total_mib": total,
        "memory_free_mib": min(free, total),
        "used_memory_mib": max(0.0, total - free),
        "memory_fraction": min(max(1.0 - free / total, 0.0), 1.0),
      }
    )

  def close(self) -> None:
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=max(1.0, self._interval_seconds * 2.0))
      self._thread = None
    self._sample()

  def to_dict(self) -> dict[str, Any]:
    if not self._snapshots:
      return {
        "sampling_available": False,
        "sample_count": 0,
        "peak_memory_fraction": None,
        "peak_used_memory_mib": None,
        "minimum_available_memory_mib": None,
      }
    return {
      "sampling_available": True,
      "sample_count": len(self._snapshots),
      "peak_memory_fraction": max(item["memory_fraction"] for item in self._snapshots),
      "peak_used_memory_mib": max(item["used_memory_mib"] for item in self._snapshots),
      "minimum_available_memory_mib": min(item["memory_free_mib"] for item in self._snapshots),
    }


def _start_gpu_runtime_sampler(interval_seconds: float) -> _GpuRuntimeSampler:
  sampler = _GpuRuntimeSampler(interval_seconds)
  sampler.start()
  return sampler


def build_next_day_labels(
  panel: pd.DataFrame,
  calendar: pd.DatetimeIndex,
) -> pd.DataFrame:
  """Attach the exact next market session; missing T+1 never skips forward."""

  result = panel.copy()
  result["event_date"] = pd.to_datetime(result["event_date"]).dt.normalize()
  if result.duplicated(["stock_code", "event_date"]).any():
    raise ValueError("训练面板存在重复股票与交易日")
  market_calendar = (
    pd.DatetimeIndex(calendar).normalize().drop_duplicates().sort_values()
  )
  next_dates = pd.Series(
    market_calendar[1:].to_numpy(), index=market_calendar[:-1], dtype="datetime64[ns]"
  )
  result["target_date"] = result["event_date"].map(next_dates)
  result["next_open"] = np.nan
  result["next_close"] = np.nan
  result["next_outcome_valid"] = False
  for _, indices in result.groupby("stock_code", sort=False).groups.items():
    stock = result.loc[indices]
    dense = stock.set_index("event_date").reindex(market_calendar)
    valid = dense["outcome_valid"].eq(True).shift(-1, fill_value=False)
    next_open = pd.to_numeric(dense["open"], errors="coerce").shift(-1)
    next_close = pd.to_numeric(dense["close"], errors="coerce").shift(-1)
    event_index = pd.DatetimeIndex(stock["event_date"])
    result.loc[indices, "next_open"] = next_open.reindex(event_index).to_numpy()
    result.loc[indices, "next_close"] = next_close.reindex(event_index).to_numpy()
    result.loc[indices, "next_outcome_valid"] = (
      valid.reindex(event_index).fillna(False).to_numpy(dtype=bool)
    )
  valid_target = (
    result["next_outcome_valid"]
    & pd.to_numeric(result["next_open"], errors="coerce").gt(0)
    & pd.to_numeric(result["next_close"], errors="coerce").gt(0)
  )
  returns = result["next_close"].div(result["next_open"]).sub(1.0)
  result["next_open_to_close_return"] = returns.where(valid_target)
  result["label"] = returns.gt(0).astype(float).where(valid_target)
  return result


def walk_forward_folds(
  months: list[pd.Period] | tuple[pd.Period, ...],
  *,
  minimum_training_months: int,
  calibration_months: int,
) -> list[WalkForwardFold]:
  ordered = tuple(sorted(set(months)))
  first_validation = minimum_training_months + calibration_months
  if len(ordered) <= first_validation:
    raise ValueError("开发区间不足以形成 30月训练 + 6月校准 + 1月验证")
  folds: list[WalkForwardFold] = []
  for validation_index in range(first_validation, len(ordered)):
    calibration_start = validation_index - calibration_months
    folds.append(
      WalkForwardFold(
        train_months=ordered[:calibration_start],
        calibration_months=ordered[calibration_start:validation_index],
        validation_month=ordered[validation_index],
      )
    )
  return folds


def _date_equal_weights(dates: pd.Series) -> np.ndarray:
  counts = dates.value_counts()
  weights = dates.map(lambda value: 1.0 / float(counts[value])).to_numpy(dtype=float)
  return weights * (len(weights) / weights.sum())


def _fit_preprocessor(frame: pd.DataFrame) -> dict[str, Any]:
  columns = list(selection_feature_columns())
  numeric = frame.reindex(columns=columns).apply(pd.to_numeric, errors="coerce")
  medians = numeric.median(axis=0).fillna(0.0)
  filled = numeric.fillna(medians)
  means = filled.mean(axis=0)
  scales = filled.std(axis=0, ddof=0).replace(0, 1.0).fillna(1.0)
  lower = filled.quantile(0.005)
  upper = filled.quantile(0.995)
  return {
    "schema_version": 1,
    "feature_columns": columns,
    "imputation_values": {key: float(value) for key, value in medians.items()},
    "means": {key: float(value) for key, value in means.items()},
    "scales": {key: float(value) for key, value in scales.items()},
    "ood_lower": {key: float(value) for key, value in lower.items()},
    "ood_upper": {key: float(value) for key, value in upper.items()},
  }


def transform_with_preprocessor(
  frame: pd.DataFrame,
  preprocessor: dict[str, Any],
) -> np.ndarray:
  columns = list(preprocessor["feature_columns"])
  numeric = frame.reindex(columns=columns).apply(pd.to_numeric, errors="coerce")
  imputation = pd.Series(preprocessor["imputation_values"], dtype=float)
  means = pd.Series(preprocessor["means"], dtype=float)
  scales = pd.Series(preprocessor["scales"], dtype=float)
  transformed = numeric.fillna(imputation).sub(means).div(scales)
  values = transformed.to_numpy(dtype=float)
  if not np.isfinite(values).all():
    raise ValueError("预处理后仍包含非有限值")
  return values


def _raw_and_probability(
  family: str, model: Any, matrix: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
  if family == "LOGISTIC":
    raw = np.asarray(model.decision_function(matrix), dtype=float)
    return raw, sigmoid(raw)
  probability = np.asarray(model.predict_proba(matrix)[:, 1], dtype=float)
  clipped = np.clip(probability, 1e-8, 1 - 1e-8)
  return np.log(clipped / (1.0 - clipped)), probability


def _fit_calibrator(
  raw: np.ndarray,
  probability: np.ndarray,
  labels: np.ndarray,
  weights: np.ndarray,
  *,
  config: NextDaySelectionConfig,
) -> tuple[dict[str, Any], np.ndarray]:
  platt = LogisticRegression(C=1_000_000.0, max_iter=1000, solver="lbfgs")
  platt.fit(raw.reshape(-1, 1), labels, sample_weight=weights)
  platt_values = platt.predict_proba(raw.reshape(-1, 1))[:, 1]
  selected = {
    "kind": "platt",
    "slope": float(platt.coef_[0, 0]),
    "intercept": float(platt.intercept_[0]),
  }
  calibrated = platt_values
  minimum_positives = config.calibration.isotonic_minimum_positives
  if int(labels.sum()) >= minimum_positives:
    isotonic = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    isotonic_values = isotonic.fit_transform(probability, labels, sample_weight=weights)
    platt_brier = brier_score(labels, platt_values)
    isotonic_brier = brier_score(labels, isotonic_values)
    improvement = (platt_brier - isotonic_brier) / platt_brier if platt_brier else 0.0
    if improvement >= config.calibration.isotonic_minimum_relative_brier_improvement:
      selected = {
        "kind": "isotonic",
        "x_thresholds": [float(value) for value in isotonic.X_thresholds_],
        "y_thresholds": [float(value) for value in isotonic.y_thresholds_],
      }
      calibrated = isotonic_values
  return {
    "version": CALIBRATOR_VERSION,
    **selected,
    "calibration_sample_count": int(len(labels)),
    "calibration_positive_count": int(labels.sum()),
  }, np.asarray(calibrated, dtype=float)


def apply_calibrator_artifact(
  raw: np.ndarray,
  probability: np.ndarray,
  calibrator: dict[str, Any],
) -> np.ndarray:
  if calibrator["kind"] == "platt":
    return sigmoid(raw * float(calibrator["slope"]) + float(calibrator["intercept"]))
  return np.interp(
    probability,
    np.asarray(calibrator["x_thresholds"], dtype=float),
    np.asarray(calibrator["y_thresholds"], dtype=float),
  )


def _fit_family(
  family: Literal["LOGISTIC", "LIGHTGBM"],
  params: dict[str, Any],
  training: pd.DataFrame,
  calibration: pd.DataFrame,
  config: NextDaySelectionConfig,
  *,
  resolved_backend: ResolvedBackend = ResolvedBackend.CPU,
) -> FittedFamily:
  preprocessor = _fit_preprocessor(training)
  train_matrix = transform_with_preprocessor(training, preprocessor)
  labels = training["label"].to_numpy(dtype=int)
  weights = _date_equal_weights(training["event_date"])
  if family == "LOGISTIC":
    model = LogisticRegression(
      C=float(params["C"]),
      max_iter=config.logistic.max_iter,
      solver="lbfgs",
      random_state=config.random_seed,
    )
  else:
    lightgbm_parameters: dict[str, Any] = {
      "objective": "binary",
      "learning_rate": config.lightgbm.learning_rate,
      "n_estimators": config.lightgbm.n_estimators,
      "num_leaves": int(params["num_leaves"]),
      "reg_lambda": float(params["reg_lambda"]),
      "min_child_samples": config.lightgbm.min_child_samples,
      "subsample": config.lightgbm.subsample,
      "subsample_freq": 1,
      "colsample_bytree": config.lightgbm.colsample_bytree,
      "max_bin": config.lightgbm.max_bin,
      "random_state": config.random_seed,
      "n_jobs": training_cpu_threads(),
      "verbosity": -1,
      "device_type": (
        "gpu"
        if resolved_backend is ResolvedBackend.LIGHTGBM_OPENCL_GPU
        else "cpu"
      ),
    }
    if resolved_backend is ResolvedBackend.LIGHTGBM_OPENCL_GPU:
      lightgbm_parameters["gpu_use_dp"] = config.lightgbm.gpu_use_dp
      if config.lightgbm.gpu_platform_id is not None:
        lightgbm_parameters["gpu_platform_id"] = config.lightgbm.gpu_platform_id
      if config.lightgbm.gpu_device_id is not None:
        lightgbm_parameters["gpu_device_id"] = config.lightgbm.gpu_device_id
    model = lgb.LGBMClassifier(**lightgbm_parameters)
  model.fit(train_matrix, labels, sample_weight=weights)
  calibration_matrix = transform_with_preprocessor(calibration, preprocessor)
  raw, probability = _raw_and_probability(family, model, calibration_matrix)
  calibrator, _ = _fit_calibrator(
    raw,
    probability,
    calibration["label"].to_numpy(dtype=int),
    _date_equal_weights(calibration["event_date"]),
    config=config,
  )
  return FittedFamily(family, model, preprocessor, calibrator)


def _predict(
  fitted: FittedFamily, frame: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
  matrix = transform_with_preprocessor(frame, fitted.preprocessor)
  raw, uncalibrated = _raw_and_probability(fitted.family, fitted.model, matrix)
  calibrated = apply_calibrator_artifact(raw, uncalibrated, fitted.calibrator)
  return raw, uncalibrated, calibrated


def _family_grid(config: NextDaySelectionConfig, family: str) -> list[dict[str, Any]]:
  if family == "LOGISTIC":
    return [{"C": value} for value in config.logistic.c_values]
  return [
    {"num_leaves": leaves, "reg_lambda": penalty}
    for leaves, penalty in itertools.product(
      config.lightgbm.num_leaves, config.lightgbm.reg_lambda
    )
  ]


def _select_parameters(
  panel: pd.DataFrame,
  folds: list[WalkForwardFold],
  family: Literal["LOGISTIC", "LIGHTGBM"],
  config: NextDaySelectionConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
  months = panel["month"]
  results: list[dict[str, Any]] = []
  for params in _family_grid(config, family):
    fold_scores: list[float] = []
    for fold in folds:
      training = panel[months.isin(fold.train_months)]
      calibration = panel[months.isin(fold.calibration_months)]
      validation = panel[months.eq(fold.validation_month)]
      fitted = _fit_family(family, params, training, calibration, config)
      _, _, probabilities = _predict(fitted, validation)
      fold_scores.append(
        brier_score(validation["label"].to_numpy(dtype=float), probabilities)
      )
    results.append(
      {
        "params": params,
        "fold_brier": fold_scores,
        "mean_brier": float(np.mean(fold_scores)),
      }
    )
  best = min(
    results,
    key=lambda item: (item["mean_brier"], json.dumps(item["params"], sort_keys=True)),
  )
  return dict(best["params"]), results


def _read_frame(path: Path) -> pd.DataFrame:
  if path.suffix.lower() == ".parquet":
    return pd.read_parquet(path)
  if path.suffix.lower() in {".csv", ".tsv"}:
    return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",")
  raise ValueError(f"不支持的点时历史格式: {path.suffix}")


def _load_history(path: Path, required_value: str) -> pd.DataFrame:
  frame = _read_frame(path)
  required = {"event_date", "stock_code", required_value}
  if required - set(frame):
    raise ValueError(f"历史股票池文件缺少字段: {sorted(required - set(frame))}")
  result = frame[list(required)].copy()
  result["event_date"] = pd.to_datetime(result["event_date"]).dt.normalize()
  result["stock_code"] = result["stock_code"].astype(str).str.upper()
  if result.duplicated(["event_date", "stock_code"]).any():
    raise ValueError("历史股票池文件存在重复日期与代码")
  return result


def _apply_historical_universe(
  panel: pd.DataFrame,
  config: NextDaySelectionConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
  paths = {
    "is_st": config.data.historical_st_membership_path,
    "industry": config.data.historical_industry_membership_path,
    "delisting_risk": config.data.historical_delisting_status_path,
  }
  if any(path is None for path in paths.values()):
    return panel, {
      "complete": False,
      "reason": "HISTORICAL_ST_INDUSTRY_DELISTING_UNAVAILABLE",
      "coverage": 0.0,
    }
  merged = panel
  for value, path in paths.items():
    assert path is not None
    merged = merged.merge(
      _load_history(path, value),
      on=["event_date", "stock_code"],
      how="left",
      validate="many_to_one",
    )
  complete = bool(
    merged[["is_st", "industry", "delisting_risk"]].notna().all(axis=1).all()
  )
  coverage = float(
    merged[["is_st", "industry", "delisting_risk"]].notna().all(axis=1).mean()
  )
  if not complete:
    raise ValueError("历史 ST/行业/退市状态未覆盖全部训练日期与标的")
  eligible = ~merged["is_st"].astype(bool) & ~merged["delisting_risk"].astype(bool)
  return merged.loc[eligible].copy(), {
    "complete": True,
    "reason": None,
    "coverage": coverage,
  }


def prepare_training_panel(
  raw_panel: pd.DataFrame,
  calendar: pd.DatetimeIndex,
  config: NextDaySelectionConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
  required = {"stock_code", "event_date", "open", "close", "outcome_valid", "open_date"}
  indicator_ids = {
    indicator_id
    for definition in SELECTION_FACTOR_DEFINITIONS
    for indicator_id in definition.source_indicators
  }
  missing = (required | indicator_ids) - set(raw_panel)
  if missing:
    raise ValueError(f"训练面板缺少字段: {sorted(missing)}")
  panel = raw_panel.copy()
  panel["stock_code"] = panel["stock_code"].astype(str).str.upper()
  panel["event_date"] = pd.to_datetime(panel["event_date"]).dt.normalize()
  panel["open_date"] = pd.to_datetime(
    panel["open_date"], errors="coerce"
  ).dt.normalize()
  panel = panel[
    panel["stock_code"].map(lambda value: bool(_ORDINARY_A_SHARE.fullmatch(value)))
  ]
  if config.data.stock_codes is not None:
    requested_codes = set(config.data.stock_codes)
    available_codes = set(panel["stock_code"].unique())
    if not requested_codes <= available_codes:
      raise ValueError("认证训练面板未覆盖全部请求股票")
    panel = panel[panel["stock_code"].isin(requested_codes)].copy()
  panel = panel.sort_values(["stock_code", "event_date"], kind="mergesort")
  panel["valid_history"] = (
    panel["outcome_valid"].eq(True).astype(int).groupby(panel["stock_code"]).cumsum()
  )
  panel = build_next_day_labels(panel, calendar)
  panel = panel[
    panel["event_date"].between(
      pd.Timestamp(config.data.date_range[0]), pd.Timestamp(config.data.date_range[1])
    )
    & panel["outcome_valid"].eq(True)
  ].copy()
  panel = panel[
    panel["valid_history"].ge(config.data.minimum_listing_days)
    & (panel["event_date"] - panel["open_date"]).dt.days.ge(
      config.data.minimum_listing_days
    )
  ]
  panel, universe_quality = _apply_historical_universe(panel, config)
  completeness = selection_factor_completeness(panel)
  complete = completeness.ge(config.candidate_gate.minimum_factor_completeness)
  panel = panel.loc[complete].copy()
  completeness = completeness.loc[complete]
  if panel.empty:
    raise ValueError("训练区间没有达到因子完整度门禁的样本")
  factors = build_selection_factor_frame(panel, date_column="event_date")
  panel["factor_completeness"] = completeness
  # Binary passthrough factors use the source indicator's public id (for
  # example ``kdj_cross_up``) as their model column.  Drop the raw indicator
  # with that name before concatenating the derived factor frame so the ready-
  # to-train Parquet has a unique, deterministic schema.
  panel = panel.drop(columns=list(factors.columns.intersection(panel.columns)))
  panel = pd.concat(
    [panel.reset_index(drop=True), factors.reset_index(drop=True)], axis=1
  )
  panel = panel[panel["label"].notna()].copy()
  panel["month"] = panel["event_date"].dt.to_period("M")
  if panel.empty:
    raise ValueError("训练区间没有符合标签和完整性门禁的样本")
  return panel, universe_quality


def certification_study_config(config: NextDaySelectionConfig, source_end) -> IndicatorStudyConfig:
  indicator_ids = tuple(
    sorted(
      {
        indicator_id
        for definition in SELECTION_FACTOR_DEFINITIONS
        for indicator_id in definition.source_indicators
      }
    )
  )
  return IndicatorStudyConfig.model_validate(
    {
      "study": "indicator-study",
      "version": "v1",
      "indicator_ids": indicator_ids,
      "date_range": (config.data.date_range[0], source_end),
      "universe": {
        "instrument_type": "stock",
        "stock_codes": config.data.stock_codes,
        "benchmark_code": config.data.benchmark_code,
        "exclude_st": False,
        "include_industries": [],
        "exclude_industries": [],
        "minimum_listing_days": config.data.minimum_listing_days,
      },
      "runtime": config.runtime.model_dump(mode="json"),
    }
  )


async def _source_panel(
  config: NextDaySelectionConfig,
  staging: Path,
  *,
  source: Any | None = None,
  calendar: Any | None = None,
) -> tuple[pd.DataFrame, pd.DatetimeIndex, dict[str, Any]]:
  _reject_links(staging)
  if (source is None) != (calendar is None):
    raise ValueError("外部认证数据源必须同时提供冻结交易日历")
  if source is not None and (
    config.data.verified_panel_path is not None
    or config.data.market_data_archive is not None
  ):
    raise ValueError("外部认证数据源不能同时指定其他面板或行情归档")
  if config.data.verified_panel_path is not None:
    source_path = Path(config.data.verified_panel_path)
    _reject_links(source_path)
    if _is_link_like(source_path) or not source_path.is_file():
      raise ValueError("verified_panel_path 必须是安全普通文件")
    source_path = source_path.resolve(strict=True)
    _reject_links(source_path)
    panel = pd.read_parquet(source_path)
    calendar = pd.DatetimeIndex(
      pd.to_datetime(panel["event_date"]).dropna().unique()
    ).sort_values()
    return (
      panel,
      calendar,
      {"kind": "verified-panel"},
    )
  if config.data.market_data_archive is not None:
    archive_path = Path(config.data.market_data_archive)
    _reject_links(archive_path)
    if _is_link_like(archive_path):
      raise ValueError("market_data_archive 不允许符号链接或联接点")
  if calendar is None:
    from quantx_infrastructure.services.trading_time_service import TradingDateHelper

    calendar = TradingDateHelper()
  source_end = await calendar.get_next_trading_date(
    "SH", config.data.date_range[1]
  )
  study_config = certification_study_config(config, source_end)
  monitor = RuntimeMemoryMonitor(
    reserve_gib=config.runtime.minimum_available_memory_gib,
    sample_interval_seconds=config.runtime.memory_sample_interval_seconds,
  )
  async with _research_source(
    source, market_data_archive=config.data.market_data_archive
  ) as active_source:
    with monitor:
      stage = await stage_indicator_features(active_source, study_config, staging, monitor)
  panel = pd.concat((pd.read_parquet(path) for path in stage.paths), ignore_index=True)
  return panel, stage.calendar, stage.quality


def _data_fingerprint(panel: pd.DataFrame) -> str:
  columns = [
    "event_date",
    "stock_code",
    "label",
    "next_open_to_close_return",
    *selection_feature_columns(),
  ]
  digest = hashlib.sha256()
  ordered = panel.sort_values(["event_date", "stock_code"], kind="mergesort")
  for start in range(0, len(ordered), 100_000):
    batch = ordered.iloc[start : start + 100_000][columns]
    digest.update(pd.util.hash_pandas_object(batch, index=False).values.tobytes())
  return digest.hexdigest()


def _save_family(run_dir: Path, fitted: FittedFamily) -> dict[str, Any]:
  if fitted.family == "LOGISTIC":
    artifact = {
      "schema_version": 1,
      "family": fitted.family,
      "coefficients": [float(value) for value in fitted.model.coef_[0]],
      "intercept": float(fitted.model.intercept_[0]),
    }
    write_json(run_dir / "logistic.json", artifact)
    model_path = "logistic.json"
  else:
    fitted.model.booster_.save_model(str(run_dir / "lightgbm.txt"))
    model_path = "lightgbm.txt"
  return {
    "family": fitted.family,
    "model_path": model_path,
  }


def _model_choice(logistic_brier: float, lightgbm_brier: float) -> str:
  if logistic_brier <= lightgbm_brier * 1.005:
    return "LOGISTIC"
  return "LIGHTGBM"


def _model_version(config_hash: str, data_hash: str, chosen: str) -> str:
  identity = hashlib.sha256(
    f"{config_hash}:{data_hash}:{FACTOR_SET_HASH}:{chosen}".encode("utf-8")
  ).hexdigest()[:16]
  return f"next-day-up-v1-{identity}"


class RunCancelled(RuntimeError):
  """Raised at a safe fold/model/artifact checkpoint."""


class _LogisticArtifactModel:
  """Minimal CPU-only model loaded from the safe logistic JSON artifact."""

  def __init__(self, coefficients: list[float], intercept: float) -> None:
    self._coefficients = np.asarray(coefficients, dtype=float)
    self._intercept = float(intercept)

  def decision_function(self, matrix: np.ndarray) -> np.ndarray:
    if matrix.shape[1] != self._coefficients.size:
      raise ValueError("Logistic 产物特征维度不匹配")
    return matrix @ self._coefficients + self._intercept


def _safe_run_id(run_id: str) -> str:
  value = str(run_id).strip()
  if not value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
    raise ValueError("run_id 只能是安全的单层目录名")
  return value


def _reject_links(path: Path) -> None:
  absolute = Path(os.path.abspath(path))
  current = Path(absolute.anchor)
  for component in absolute.parts[1:]:
    current /= component
    if _is_link_like(current):
      raise ValueError(f"运行路径不允许符号链接或联接点: {current.name}")


def _is_link_like(path: Path) -> bool:
  """Treat symlinks, junctions, and unreadable reparse points as links."""

  if path.is_symlink() or os.path.islink(str(path)):
    return True
  is_junction = getattr(path, "is_junction", None)
  if is_junction is None:
    return False
  try:
    return bool(is_junction())
  except OSError:
    return True


def _safe_remove_tree(path: Path) -> None:
  """Remove a temporary tree only after fail-closed link checks."""

  try:
    _reject_links(path)
  except ValueError:
    return
  if not path.exists() or _is_link_like(path) or not path.is_dir():
    return
  try:
    _reject_links(path)
  except ValueError:
    return
  shutil.rmtree(path)


def _safe_error(exc: BaseException) -> str:
  """Return a display-safe error without host paths or credentials."""
  message = str(exc).replace("\\", "/")
  # Paths may contain spaces and may be Windows drive, UNC, or Unix absolute
  # paths.  Redact the whole absolute run up to a structural delimiter so no
  # directory fragment can escape into a run manifest or worker log.
  message = re.sub(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:/|//|/)[^\"'\r\n<>;,，。；：]*",
    "<path>",
    message,
  )
  message = re.sub(r"(?i)(password|secret|token|credential)[=:][^,;\s]+", r"\1=<redacted>", message)
  return message[:500]


def _call_cancel(cancel_callback: Any | None) -> bool:
  if cancel_callback is None:
    return False
  result = cancel_callback()
  if hasattr(result, "__await__"):
    # The public worker callback is synchronous.  An async callback must be
    # handled by the caller before entering this synchronous fit checkpoint.
    raise TypeError("cancel callback must be synchronous")
  return bool(result)


def _emit_progress(
  progress_callback: Any | None,
  *,
  phase: TrainingPhase,
  completed_units: int,
  total_units: int,
  message: str,
) -> None:
  if progress_callback is None:
    return
  progress_callback(
    {
      "phase": phase.value,
      "completed_units": int(completed_units),
      "total_units": int(total_units),
      "message": str(message)[:300],
    }
  )


def _config_from_spec(spec: Mapping[str, Any]) -> NextDaySelectionConfig:
  if not isinstance(spec, Mapping):
    raise ValueError("训练 spec 必须是 mapping")
  # The isolated job passes one flat, canonical config snapshot.  Only the
  # two execution-envelope fields below are removed; accepting ``config`` or
  # ``resolved_config`` here would create a second protocol shape.
  payload = dict(spec)
  for key in (
    "run_kind",
    "resolved_backend",
    "parent_run_directory",
    "spec_hash",
    "coordinate_hash",
    "environment_requirement_hash",
    "qualification",
  ):
    payload.pop(key, None)
  return NextDaySelectionConfig.model_validate(payload)


def _config_hash_payload(value: Any, *, key: str = "") -> Any:
  """Canonicalize model semantics without host paths or runtime roots."""

  lowered = key.lower()
  if any(token in lowered for token in ("path", "archive", "output_root", "root")):
    return None
  if isinstance(value, Mapping):
    return {
      str(name): _config_hash_payload(item, key=str(name))
      for name, item in value.items()
      if str(name) not in {"parent_run_directory", "run_kind"}
    }
  if isinstance(value, (list, tuple)):
    return [_config_hash_payload(item, key=key) for item in value]
  return value


def _run_qualification_projection(
  spec: Mapping[str, Any],
  backend: ResolvedBackend,
) -> dict[str, Any]:
  if backend is ResolvedBackend.CPU:
    # CPU runs deliberately carry no GPU qualification facts.  This is a
    # semantic CPU projection, not a claim that the GPU gates passed.
    return {
      "status": "CPU_AVAILABLE",
      "acceleration": None,
      "minimum_sample_count": None,
      "peak_memory_fraction": None,
      "gates_passed": True,
      "evidence_sha256": None,
    }
  raw = spec.get("qualification")
  if not isinstance(raw, Mapping):
    return {
      "status": "GPU_UNQUALIFIED",
      "acceleration": None,
      "minimum_sample_count": None,
      "peak_memory_fraction": None,
      "gates_passed": False,
      "evidence_sha256": None,
    }
  return {
    "status": str(raw.get("status") or "GPU_UNQUALIFIED"),
    "acceleration": raw.get("acceleration"),
    "minimum_sample_count": raw.get("minimum_sample_count"),
    "peak_memory_fraction": raw.get("peak_memory_fraction"),
    "gates_passed": raw.get("gates_passed") is True,
    "evidence_sha256": raw.get("evidence_sha256"),
  }


def _run_telemetry(
  *,
  spec: Mapping[str, Any],
  config: NextDaySelectionConfig,
  backend: ResolvedBackend,
  monitor: RuntimeMemoryMonitor,
  gpu_sampler: _GpuRuntimeSampler | None,
  wall_started: float,
  cpu_started: float,
  model_parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
  qualification = _run_qualification_projection(spec, backend)
  environment = runtime_metadata()
  environment["environment_requirement_hash"] = _locked_hash(
    spec, "environment_requirement_hash"
  )
  raw_qualification = spec.get("qualification")
  raw_qualification = raw_qualification if isinstance(raw_qualification, Mapping) else {}
  if backend is ResolvedBackend.LIGHTGBM_OPENCL_GPU:
    environment["qualification_version"] = raw_qualification.get("qualification_version")
    environment["requirement_hash"] = raw_qualification.get("requirement_hash")
    environment["evidence_sha256"] = qualification["evidence_sha256"]
  else:
    environment["qualification_version"] = None
    environment["requirement_hash"] = None
    environment["evidence_sha256"] = None
  if backend is ResolvedBackend.LIGHTGBM_OPENCL_GPU:
    from quantx_research.next_day_selection_gpu import _environment_evidence

    gpu_environment = _environment_evidence()
    environment["gpu"] = gpu_environment.get("gpu", {})
    environment["opencl"] = gpu_environment.get("opencl", {})
  else:
    # CPU execution must not probe or initialize a GPU.
    environment["gpu"] = None
    environment["opencl"] = None
  gpu_data = (
    gpu_sampler.to_dict()
    if gpu_sampler is not None
    else {
      "sampling_available": False,
      "sample_count": 0,
      "peak_memory_fraction": None,
      "peak_used_memory_mib": None,
      "minimum_available_memory_mib": None,
    }
  )
  return {
    "wall_time_seconds": max(0.0, time.perf_counter() - wall_started),
    "process_cpu_time_seconds": max(0.0, time.process_time() - cpu_started),
    "runtime_memory": monitor.to_dict(),
    "gpu": gpu_data,
    "qualification": qualification,
    "lightgbm": {
      "backend": backend.value,
      "device_type": "gpu" if backend is ResolvedBackend.LIGHTGBM_OPENCL_GPU else "cpu",
      "max_bin": config.lightgbm.max_bin,
      "gpu_use_dp": config.lightgbm.gpu_use_dp,
      "parameters": dict(model_parameters or config.lightgbm.model_dump(mode="json")),
    },
    "environment": environment,
  }


def _locked_backend(
  spec: Mapping[str, Any], config: NextDaySelectionConfig
) -> tuple[RequestedBackend, ResolvedBackend]:
  """Validate the backend decision already frozen into the queued spec.

  Backend qualification is a pre-queue concern.  Once a run request is
  accepted, this function only validates the immutable decision carried by
  the spec; it never probes or resolves a backend again.
  """

  if "requested_backend" not in spec:
    raise ValueError("训练 spec 缺少 requested_backend")
  requested_raw = spec["requested_backend"]
  try:
    requested = RequestedBackend(requested_raw)
  except (TypeError, ValueError) as exc:
    raise ValueError("训练 spec 的 requested_backend 非法") from exc
  if requested.value != config.requested_backend:
    raise ValueError("训练 spec 的 requested_backend 与 config 不一致")
  if "resolved_backend" not in spec:
    raise ValueError("训练 spec 缺少已锁定的 resolved_backend")
  try:
    resolved = ResolvedBackend(spec["resolved_backend"])
  except (TypeError, ValueError) as exc:
    raise ValueError("训练 spec 的 resolved_backend 非法") from exc
  allowed = {
    RequestedBackend.CPU: {ResolvedBackend.CPU},
    RequestedBackend.GPU_REQUIRED: {ResolvedBackend.LIGHTGBM_OPENCL_GPU},
    RequestedBackend.AUTO: {
      ResolvedBackend.CPU,
      ResolvedBackend.LIGHTGBM_OPENCL_GPU,
    },
  }[requested]
  if resolved not in allowed:
    raise ValueError(
      f"requested_backend={requested.value} 与 resolved_backend={resolved.value} 组合非法"
    )
  return requested, resolved


def _redact_config_artifact(value: Any, *, key: str = "") -> Any:
  """Remove host paths from the human-readable resolved config artifact."""
  if isinstance(value, Mapping):
    return {
      str(name): _redact_config_artifact(item, key=str(name))
      for name, item in value.items()
    }
  if isinstance(value, (list, tuple)):
    return [_redact_config_artifact(item, key=key) for item in value]
  if isinstance(value, str) and any(token in key.lower() for token in ("path", "archive", "root")):
    # Keep a harmless relative basename as a debugging hint, never its host
    # directory or drive.  The actual immutable dataset is identified by its
    # manifest hash elsewhere in the run evidence.
    return Path(value).name if value else None
  return value


def _load_certified_panel(dataset_directory: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
  from quantx_research.next_day_selection_dataset import load_certified_dataset_manifest

  manifest = load_certified_dataset_manifest(dataset_directory)
  root = Path(dataset_directory).resolve(strict=True)
  panel = pd.read_parquet(root / "training-panel.parquet")
  required = {"event_date", "stock_code", "label", "month"}
  if required - set(panel):
    raise ValueError(f"认证训练面板缺少字段: {sorted(required - set(panel))}")
  computed = _data_fingerprint(panel)
  expected = manifest.get("data_fingerprint")
  if expected is not None and expected != computed:
    raise ValueError("认证数据集面板内容哈希不匹配")
  return panel, manifest


def _project_certified_panel(
  panel: pd.DataFrame,
  manifest: Mapping[str, Any],
  config: NextDaySelectionConfig,
) -> pd.DataFrame:
  """Apply the immutable request universe to a certified panel.

  Certification fixes the source evidence and its minimum listing-history
  requirement.  A queued run may narrow that evidence by date or symbols, but
  it may not widen the date range, escape the certified symbol scope, or lower
  the listing-history gate.
  """

  if "event_date" not in panel or "stock_code" not in panel:
    raise ValueError("认证训练面板缺少 event_date 或 stock_code")
  try:
    event_dates = pd.to_datetime(panel["event_date"], errors="coerce").dt.normalize()
    requested_start = pd.Timestamp(config.data.date_range[0]).normalize()
    requested_end = pd.Timestamp(config.data.date_range[1]).normalize()
    certified_start = pd.Timestamp(manifest["date_start"]).normalize()
    certified_end = pd.Timestamp(manifest["date_end"]).normalize()
  except (KeyError, TypeError, ValueError) as exc:
    raise ValueError("认证数据集日期证据不可解析") from exc
  if requested_start < certified_start or requested_end > certified_end:
    raise ValueError("训练 spec 日期范围超出认证数据集覆盖范围")

  universe_spec = manifest.get("universe_spec")
  if not isinstance(universe_spec, Mapping):
    raise ValueError("认证数据集缺少 universe_spec 证据")
  required_universe_fields = {
    "kind",
    "index_code",
    "benchmark_code",
    "stock_codes",
    "minimum_listing_days",
  }
  if set(universe_spec) != required_universe_fields:
    raise ValueError("认证数据集 universe_spec 字段必须完整且唯一")
  certified_kind = universe_spec.get("kind")
  requested_kind = config.data.universe_kind
  if certified_kind != requested_kind:
    raise ValueError("训练 spec universe_kind 与认证数据集 kind 不一致")
  certified_index = universe_spec.get("index_code")
  if certified_index != config.data.index_code:
    raise ValueError("训练 spec index_code 与认证数据集不一致")
  certified_benchmark = universe_spec.get("benchmark_code")
  if certified_benchmark != config.data.benchmark_code:
    raise ValueError("训练 spec benchmark_code 与认证数据集不一致")
  certified_minimum = universe_spec.get("minimum_listing_days")
  if certified_minimum is None:
    raise ValueError("认证数据集缺少 minimum_listing_days 证据")
  try:
    certified_minimum_int = int(certified_minimum)
  except (TypeError, ValueError) as exc:
    raise ValueError("认证数据集 minimum_listing_days 证据非法") from exc
  if config.data.minimum_listing_days < certified_minimum_int:
    raise ValueError("训练 spec 不得弱化认证数据集的上市历史门槛")

  result = panel.copy()
  result["event_date"] = event_dates
  result["stock_code"] = result["stock_code"].astype(str).str.upper()
  requested_codes = {
    str(code).upper() for code in (config.data.stock_codes or ())
  }
  certified_codes_raw = universe_spec.get("stock_codes")
  if certified_codes_raw is not None and not isinstance(certified_codes_raw, list):
    raise ValueError("认证数据集 universe_spec.stock_codes 证据非法")
  certified_codes = {
    str(code).upper() for code in (certified_codes_raw or ())
  }
  if certified_kind == "EXPLICIT" and not certified_codes:
    raise ValueError("EXPLICIT 认证数据集缺少 stock_codes")
  if certified_kind == "CERTIFIED_INDEX":
    historical = manifest.get("quality", {})
    coverage = historical.get("coverage", {}) if isinstance(historical, Mapping) else {}
    point_in_time = coverage.get("historical_universe", {}) if isinstance(coverage, Mapping) else {}
    if not isinstance(point_in_time, Mapping) or point_in_time.get("complete") is not True:
      raise ValueError("CERTIFIED_INDEX 认证数据集缺少完整 point-in-time universe 证据")
  if requested_codes and certified_codes and not requested_codes <= certified_codes:
    raise ValueError("训练 spec 股票范围超出认证数据集 universe_spec")
  if certified_codes and not set(result["stock_code"].dropna().unique()) <= certified_codes:
    raise ValueError("认证训练面板含 universe_spec 之外的股票")

  mask = result["event_date"].between(requested_start, requested_end)
  if requested_codes:
    mask &= result["stock_code"].isin(requested_codes)
  result = result.loc[mask].copy()

  requested_minimum = config.data.minimum_listing_days
  if "valid_history" not in result or "open_date" not in result:
    raise ValueError("认证面板缺少实际上市历史投影所需字段")
  open_dates = pd.to_datetime(result["open_date"], errors="coerce").dt.normalize()
  valid_history = pd.to_numeric(result["valid_history"], errors="coerce")
  result = result.loc[
    valid_history.ge(requested_minimum)
    & (result["event_date"] - open_dates).dt.days.ge(requested_minimum)
  ].copy()
  if result.empty:
    raise ValueError("训练 spec 投影后的认证面板为空")
  if requested_codes and not requested_codes <= set(result["stock_code"].unique()):
    raise ValueError("训练 spec 股票范围在请求日期内未被完整覆盖")
  return result.reset_index(drop=True)


def _fit_checked(
  family: Literal["LOGISTIC", "LIGHTGBM"],
  params: dict[str, Any],
  training: pd.DataFrame,
  calibration: pd.DataFrame,
  config: NextDaySelectionConfig,
  *,
  backend: ResolvedBackend,
  cancel_callback: Any | None,
) -> FittedFamily:
  if _call_cancel(cancel_callback):
    raise RunCancelled("运行已请求取消")
  return _fit_family(
    family,
    params,
    training,
    calibration,
    config,
    resolved_backend=backend,
  )


def _select_parameters_checked(
  panel: pd.DataFrame,
  folds: list[WalkForwardFold],
  family: Literal["LOGISTIC", "LIGHTGBM"],
  config: NextDaySelectionConfig,
  *,
  backend: ResolvedBackend,
  cancel_callback: Any | None,
  progress_callback: Any | None,
  completed_units: int,
  total_units: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
  months = panel["month"]
  results: list[dict[str, Any]] = []
  for params in _family_grid(config, family):
    fold_scores: list[float] = []
    for fold in folds:
      fitted = _fit_checked(
        family,
        params,
        panel[months.isin(fold.train_months)],
        panel[months.isin(fold.calibration_months)],
        config,
        backend=backend,
        cancel_callback=cancel_callback,
      )
      validation = panel[months.eq(fold.validation_month)]
      _, _, probabilities = _predict(fitted, validation)
      fold_scores.append(
        brier_score(validation["label"].to_numpy(dtype=float), probabilities)
      )
      completed_units += 1
      _emit_progress(
        progress_callback,
        phase=TrainingPhase.WALK_FORWARD,
        completed_units=completed_units,
        total_units=total_units,
        message=f"{family} 参数网格与 fold {fold.validation_month} 完成",
      )
      if _call_cancel(cancel_callback):
        raise RunCancelled("运行已请求取消")
    results.append(
      {
        "params": params,
        "fold_brier": fold_scores,
        "mean_brier": float(np.mean(fold_scores)),
      }
    )
  best = min(
    results,
    key=lambda item: (item["mean_brier"], json.dumps(item["params"], sort_keys=True)),
  )
  return dict(best["params"]), results, completed_units


def _artifact_hashes(run_dir: Path) -> dict[str, str]:
  _reject_links(run_dir)
  result: dict[str, str] = {}
  for path in sorted(run_dir.rglob("*")):
    if _is_link_like(path):
      raise ValueError(f"运行产物不允许符号链接或联接点: {path.name}")
    if path.is_file() and path.name not in {"manifest.json", "development-lock.json"}:
      result[path.relative_to(run_dir).as_posix()] = file_sha256(path)
  return result


def _load_json(path: Path) -> dict[str, Any]:
  _reject_links(path)
  if _is_link_like(path) or not path.is_file():
    raise ValueError(f"安全产物缺失: {path.name}")
  value = json.loads(
    path.read_text(encoding="utf-8"),
    parse_constant=lambda token: (_ for _ in ()).throw(
      ValueError(f"产物 JSON 不允许非有限值: {token}")
    ),
  )
  if not isinstance(value, dict):
    raise ValueError(f"安全产物必须是 JSON object: {path.name}")
  return value


def _assert_finite_payload(value: Any, *, name: str) -> None:
  """Reject non-finite values in a persisted evidence object."""

  if isinstance(value, Mapping):
    for key, item in value.items():
      _assert_finite_payload(item, name=f"{name}.{key}")
    return
  if isinstance(value, (list, tuple)):
    for index, item in enumerate(value):
      _assert_finite_payload(item, name=f"{name}[{index}]")
    return
  if isinstance(value, float) and not math.isfinite(value):
    raise ValueError(f"{name} 含非有限值")


def _validate_development_validation(value: Any) -> dict[str, Any]:
  """Validate the complete, immutable walk-forward validation projection."""

  if not isinstance(value, dict):
    raise ValueError("父级 metrics.json 缺少完整 validation 指标")
  required = {
    "fold_count",
    "logistic_brier",
    "lightgbm_brier",
    "logistic_grid",
    "lightgbm_grid",
  }
  if set(value) != required:
    raise ValueError("父级 validation 指标字段不完整")
  _assert_finite_payload(value, name="validation")
  fold_count = value.get("fold_count")
  if isinstance(fold_count, bool) or not isinstance(fold_count, int) or fold_count < 1:
    raise ValueError("父级 validation fold_count 非法")
  for family, brier_key, grid_key in (
    ("LOGISTIC", "logistic_brier", "logistic_grid"),
    ("LIGHTGBM", "lightgbm_brier", "lightgbm_grid"),
  ):
    try:
      family_brier = float(value[brier_key])
    except (KeyError, TypeError, ValueError) as exc:
      raise ValueError(f"父级 {family} validation Brier 非法") from exc
    if not math.isfinite(family_brier) or family_brier < 0:
      raise ValueError(f"父级 {family} validation Brier 非法")
    grid = value.get(grid_key)
    if not isinstance(grid, list) or not grid:
      raise ValueError(f"父级 {family} validation 参数网格缺失")
    for item in grid:
      if not isinstance(item, dict) or set(item) != {"params", "fold_brier", "mean_brier"}:
        raise ValueError(f"父级 {family} validation 网格证据不完整")
      fold_scores = item.get("fold_brier")
      if not isinstance(fold_scores, list) or len(fold_scores) != fold_count:
        raise ValueError(f"父级 {family} validation fold 数量不一致")
      if not isinstance(item.get("params"), dict):
        raise ValueError(f"父级 {family} validation 参数非法")
      try:
        scores = [float(score) for score in fold_scores]
        mean_brier = float(item["mean_brier"])
      except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"父级 {family} validation Brier 网格非法") from exc
      if any(not math.isfinite(score) or score < 0 for score in scores):
        raise ValueError(f"父级 {family} validation 含非法 Brier")
      if not math.isfinite(mean_brier) or mean_brier < 0:
        raise ValueError(f"父级 {family} validation mean Brier 非法")
      if not math.isclose(mean_brier, float(np.mean(scores)), rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"父级 {family} validation mean Brier 不一致")
    if not math.isclose(family_brier, min(float(item["mean_brier"]) for item in grid), rel_tol=1e-12, abs_tol=1e-12):
      raise ValueError(f"父级 {family} validation 选择 Brier 不一致")
  return value


def _validate_parent_lock(
  parent_dir: Path,
  parent_manifest: Mapping[str, Any],
  lock: Mapping[str, Any],
  *,
  spec_hash: str,
  coordinate_hash: str,
  config_hash: str,
  environment_requirement_hash: str,
  dataset_manifest_hash: str,
  panel_hash: str,
) -> tuple[dict[str, str], dict[str, Any], str]:
  """Verify every immutable DEVELOPMENT input before FINAL can read it."""

  if lock.get("schema_version") != 1 or lock.get("run_kind") != RunKind.DEVELOPMENT.value:
    raise ValueError("父级 development-lock 类型或 schema 不匹配")
  for key, expected in (
    ("spec_hash", spec_hash),
    ("coordinate_hash", coordinate_hash),
    ("config_hash", config_hash),
    ("environment_requirement_hash", environment_requirement_hash),
    ("dataset_manifest_sha256", dataset_manifest_hash),
    ("training_panel_sha256", panel_hash),
  ):
    if lock.get(key) != expected:
      raise ValueError(f"父级锁定 {key} 不匹配")
  if (
    parent_manifest.get("spec_hash") != spec_hash
    or parent_manifest.get("coordinate_hash") != coordinate_hash
    or parent_manifest.get("config_hash") != config_hash
    or parent_manifest.get("environment_requirement_hash") != environment_requirement_hash
    or parent_manifest.get("dataset_manifest_sha256") != dataset_manifest_hash
    or parent_manifest.get("training_panel_sha256") != panel_hash
  ):
    raise ValueError("父级 manifest 与当前 spec 坐标不一致")
  required_hash_fields = {
    "spec_hash",
    "coordinate_hash",
    "config_hash",
    "environment_requirement_hash",
    "dataset_manifest_sha256",
    "training_panel_sha256",
    "data_fingerprint",
  }
  for key in required_hash_fields:
    raw = lock.get(key)
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9a-f]{64}", raw):
      raise ValueError(f"父级锁定 {key} 非法")
  artifact_hashes = lock.get("artifact_hashes")
  if not isinstance(artifact_hashes, dict) or not artifact_hashes:
    raise ValueError("父级锁定缺少完整 artifact_hashes")
  required_artifacts = {
    "resolved-config.yaml",
    "factor-schema.json",
    "preprocessing.json",
    "calibrators.json",
    "model-runtime.json",
    "metrics.json",
    "data-quality.json",
    "logistic.json",
    "lightgbm.txt",
  }
  if not required_artifacts <= set(artifact_hashes):
    raise ValueError("父级锁定产物索引不完整")
  if not isinstance(lock.get("split"), dict) or not isinstance(lock.get("parameters"), dict):
    raise ValueError("父级锁定缺少完整切分或参数证据")
  if lock.get("conclusion") != GateConclusion.BLOCKED.value or lock.get("registerable") is not False:
    raise ValueError("DEVELOPMENT 父级锁定门禁状态非法")
  for relative, expected_hash in artifact_hashes.items():
    relative_path = Path(str(relative))
    path = parent_dir / relative_path
    if (
      relative_path.is_absolute()
      or ".." in relative_path.parts
      or relative_path.as_posix() != str(relative)
      or _is_link_like(path)
      or not path.is_file()
      or not isinstance(expected_hash, str)
      or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
    ):
      raise ValueError("父级锁定产物路径或哈希非法")
    if file_sha256(path) != expected_hash:
      raise ValueError(f"父级锁定产物哈希不匹配: {relative_path.name}")
  parent_metrics = _load_json(parent_dir / "metrics.json")
  if parent_metrics.get("schema_version") != 2:
    raise ValueError("父级 metrics.json schema 不匹配")
  validation = _validate_development_validation(parent_metrics.get("validation"))
  if (
    parent_metrics.get("spec_hash") != spec_hash
    or parent_metrics.get("coordinate_hash") != coordinate_hash
    or parent_metrics.get("config_hash") != config_hash
    or parent_metrics.get("environment_requirement_hash") != environment_requirement_hash
  ):
    raise ValueError("父级 metrics 坐标不匹配")
  parent_lock_hash = file_sha256(parent_dir / "development-lock.json")
  return dict(artifact_hashes), validation, parent_lock_hash


def _validate_parent_runtime(
  runtime: Mapping[str, Any],
  preprocessing: Mapping[str, Any],
  calibrators: Mapping[str, Any],
  *,
  config: NextDaySelectionConfig,
) -> str:
  if runtime.get("schema_version") != 2:
    raise ValueError("父级 model-runtime.json schema 不匹配")
  families = runtime.get("families")
  if not isinstance(families, dict) or set(families) != {"LOGISTIC", "LIGHTGBM"}:
    raise ValueError("父级 model-runtime.json 缺少完整模型家族")
  selected = runtime.get("selected_family")
  if selected not in families:
    raise ValueError("父级 model-runtime.json selected_family 非法")
  if runtime.get("backend") not in {item.value for item in ResolvedBackend}:
    raise ValueError("父级 model-runtime.json backend 非法")
  if runtime.get("max_bin") != config.lightgbm.max_bin or runtime.get("gpu_use_dp") != config.lightgbm.gpu_use_dp:
    raise ValueError("父级 LightGBM 运行参数与当前 config 不一致")
  for family, expected_model in (("LOGISTIC", "logistic.json"), ("LIGHTGBM", "lightgbm.txt")):
    entry = families[family]
    if not isinstance(entry, dict) or set(entry) != {"family", "model_path", "preprocessing_path", "calibrator_path"}:
      raise ValueError(f"父级 {family} runtime 证据不完整")
    if entry.get("family") != family or entry.get("model_path") != expected_model:
      raise ValueError(f"父级 {family} runtime 模型路径不匹配")
    if entry.get("preprocessing_path") != "preprocessing.json" or entry.get("calibrator_path") != "calibrators.json":
      raise ValueError(f"父级 {family} runtime 引用路径不匹配")
  if preprocessing.get("schema_version") != 1 or set(preprocessing.get("families", {})) != {"LOGISTIC", "LIGHTGBM"}:
    raise ValueError("父级 preprocessing.json 不完整")
  if calibrators.get("schema_version") != 1 or set(calibrators.get("families", {})) != {"LOGISTIC", "LIGHTGBM"}:
    raise ValueError("父级 calibrators.json 不完整")
  return str(selected)


def _load_parent_family(
  parent_dir: Path,
  family: Literal["LOGISTIC", "LIGHTGBM"],
  runtime: Mapping[str, Any],
  preprocessing: Mapping[str, Any],
  calibrators: Mapping[str, Any],
) -> FittedFamily:
  family_runtime = runtime.get("families", {}).get(family)
  if not isinstance(family_runtime, Mapping):
    raise ValueError(f"父级缺少 {family} 锁定产物")
  model_path = family_runtime.get("model_path")
  if not isinstance(model_path, str) or Path(model_path).name != model_path:
    raise ValueError("父级模型路径不是安全相对文件名")
  path = parent_dir / model_path
  _reject_links(path)
  if family == "LOGISTIC":
    artifact = _load_json(path)
    if artifact.get("family") != "LOGISTIC":
      raise ValueError("父级 Logistic 产物类型不匹配")
    model: Any = _LogisticArtifactModel(
      [float(value) for value in artifact.get("coefficients", ())],
      float(artifact.get("intercept")),
    )
  else:
    if lgb is None:
      raise ValueError("当前运行时无法加载 LightGBM 文本产物")
    if path.suffix.lower() != ".txt" or _is_link_like(path) or not path.is_file():
      raise ValueError("父级 LightGBM 产物不是安全文本模型")
    model = lgb.Booster(model_file=str(path))
  preprocessor = preprocessing.get("families", {}).get(family)
  calibrator = calibrators.get("families", {}).get(family)
  if not isinstance(preprocessor, dict) or not isinstance(calibrator, dict):
    raise ValueError(f"父级缺少 {family} 预处理或校准器")
  return FittedFamily(family, model, preprocessor, calibrator)


def _copy_parent_artifacts(parent_dir: Path, run_dir: Path, runtime: Mapping[str, Any]) -> None:
  _reject_links(parent_dir)
  _reject_links(run_dir)
  for family_runtime in (runtime.get("families", {}) or {}).values():
    if not isinstance(family_runtime, Mapping):
      continue
    model_path = family_runtime.get("model_path")
    if not isinstance(model_path, str) or Path(model_path).name != model_path:
      raise ValueError("父级产物路径不是安全相对路径")
    source = parent_dir / model_path
    destination = run_dir / model_path
    _reject_links(source)
    _reject_links(destination)
    if _is_link_like(source) or _is_link_like(destination) or not source.is_file():
      raise ValueError("父级产物不存在或为链接")
    shutil.copyfile(source, destination)
  for name in ("preprocessing.json", "calibrators.json", "factor-schema.json"):
    source = parent_dir / name
    destination = run_dir / name
    _reject_links(source)
    _reject_links(destination)
    if source.is_file() and not _is_link_like(source) and not _is_link_like(destination):
      shutil.copyfile(source, destination)


def _gate_projection(
  probability_metrics: Mapping[str, Any],
  ranking_metrics: Mapping[str, Any],
  universe_quality: Mapping[str, Any],
  *,
  frozen_test_access_count: int,
  artifact_valid: bool = True,
  data_quality_valid: bool = True,
) -> tuple[dict[str, Any], GateConclusion]:
  evidence = GateEvidence(
    brier_skill_positive=bool(
      isinstance(probability_metrics.get("brier_skill"), (int, float))
      and math.isfinite(float(probability_metrics["brier_skill"]))
      and float(probability_metrics["brier_skill"]) > 0
    ),
    ece_within_limit=bool(
      isinstance(probability_metrics.get("ece"), (int, float))
      and math.isfinite(float(probability_metrics["ece"]))
      and float(probability_metrics["ece"]) <= 0.03
    ),
    top20_lift_ci_lower_positive=bool(
      isinstance(ranking_metrics.get("top_20", {}).get("up_rate_lift_ci_low"), (int, float))
      and math.isfinite(float(ranking_metrics["top_20"]["up_rate_lift_ci_low"]))
      and float(ranking_metrics["top_20"]["up_rate_lift_ci_low"]) > 0
    ),
    historical_universe_complete=bool(universe_quality.get("complete")),
    artifact_valid=bool(artifact_valid),
    data_quality_valid=bool(data_quality_valid),
    unbiased_frozen_evidence=frozen_test_access_count == 1,
  )
  conclusion = gate_conclusion(
    evidence,
    frozen_test_access_count=frozen_test_access_count,
  )
  gates = {
    "brier_skill_positive": evidence.brier_skill_positive,
    "ece_within_3pct": evidence.ece_within_limit,
    "top20_lift_ci_lower_positive": evidence.top20_lift_ci_lower_positive,
    "historical_universe_complete": evidence.historical_universe_complete,
    "artifact_valid": evidence.artifact_valid,
    "data_quality_valid": evidence.data_quality_valid,
    "effect_gate_passed": (
      evidence.brier_skill_positive
      and evidence.ece_within_limit
      and evidence.top20_lift_ci_lower_positive
    ),
    "unbiased_frozen_evidence": evidence.unbiased_frozen_evidence,
    "conclusion": conclusion.value,
    "active_eligible": conclusion is GateConclusion.ACTIVE_ELIGIBLE,
    "access_evidence_valid": frozen_test_access_count == 1,
  }
  return gates, conclusion


async def execute_next_day_selection_run(
  *,
  run_kind: RunKind | str,
  spec: Mapping[str, Any],
  dataset_directory: str | Path,
  output_root: str | Path,
  run_id: str,
  parent_run_directory: str | Path | None = None,
  progress_callback: Any | None = None,
  cancel_callback: Any | None = None,
  frozen_test_access_count: int = 0,
) -> Path:
  """Execute DEVELOPMENT or FINAL_EVALUATION from one immutable interface."""

  kind = RunKind(run_kind)
  safe_id = _safe_run_id(run_id)
  root = Path(output_root)
  if not root.is_absolute():
    root = REPO_ROOT / root
  _reject_links(root)
  root = root.resolve(strict=False)
  root.mkdir(parents=True, exist_ok=True)
  _reject_links(root)
  run_dir = root / safe_id
  if run_dir.exists():
    if _is_link_like(run_dir) or any(run_dir.iterdir()):
      raise ValueError("指定 run_id 已存在，拒绝覆盖")
  run_dir.mkdir(parents=False, exist_ok=False)
  _reject_links(run_dir)
  started = datetime.now(timezone.utc)
  spec_payload = json.loads(json.dumps(dict(spec), default=str))
  # These hashes are DB-locked queue inputs.  They are never recomputed from
  # the full runtime config: DEVELOPMENT and FINAL_EVALUATION must consume the
  # exact same authoritative coordinates from the accepted request.
  spec_hash = _locked_hash(spec_payload, "spec_hash")
  coordinate_hash = _locked_hash(spec_payload, "coordinate_hash")
  environment_requirement_hash = _locked_hash(
    spec_payload, "environment_requirement_hash"
  )
  manifest: dict[str, Any] = {
    "schema_version": 2,
    "study_id": "next-day-selection",
    "version": "v1",
    "run_id": safe_id,
    "run_kind": kind.value,
    "status": "RUNNING",
    "phase": TrainingPhase.PREFLIGHT.value,
    "completed_units": 0,
    "total_units": 1,
    "started_at": started,
    "spec_hash": spec_hash,
    "coordinate_hash": coordinate_hash,
    "environment_requirement_hash": environment_requirement_hash,
    "indicator_version": INDICATOR_VERSION,
    "factor_set_version": FACTOR_SET_VERSION,
    "factor_set_hash": FACTOR_SET_HASH,
    "label_version": LABEL_VERSION,
  }
  write_json(run_dir / "manifest.json", manifest)
  # Scratch files are removed on exit and must never enter the immutable result inventory.
  staging = Path(tempfile.mkdtemp(prefix=f"next-day-selection-{safe_id}-", dir=root))
  runtime_payload = spec_payload.get("runtime")
  configured_reserve = (
    runtime_payload.get("minimum_available_memory_gib")
    if isinstance(runtime_payload, Mapping)
    else 4.0
  )
  try:
    reserve_gib = float(configured_reserve)
  except (TypeError, ValueError):
    reserve_gib = 4.0
  if not math.isfinite(reserve_gib) or reserve_gib < 1.0:
    reserve_gib = 4.0
  configured_interval = (
    runtime_payload.get("memory_sample_interval_seconds", 0.25)
    if isinstance(runtime_payload, Mapping)
    else 0.25
  )
  try:
    sample_interval = float(configured_interval)
  except (TypeError, ValueError):
    sample_interval = 0.25
  if not math.isfinite(sample_interval) or sample_interval <= 0:
    sample_interval = 0.25
  monitor = RuntimeMemoryMonitor(
    reserve_gib=max(reserve_gib, 1.0),
    sample_interval_seconds=sample_interval,
  )
  monitor_started = False
  monitor_closed = False
  gpu_sampler: _GpuRuntimeSampler | None = None
  telemetry_config: NextDaySelectionConfig | None = None
  telemetry_backend: ResolvedBackend | None = None
  model_parameters: Mapping[str, Any] | None = None
  wall_started = time.perf_counter()
  cpu_started = time.process_time()

  def update_manifest(**changes: Any) -> None:
    manifest.update(changes)
    write_json(run_dir / "manifest.json", manifest)

  def checkpoint(
    phase: TrainingPhase,
    completed_units: int,
    total_units: int,
    message: str,
  ) -> None:
    if monitor_started:
      monitor.set_stage(phase.value)
    manifest["phase"] = phase.value
    manifest["completed_units"] = int(completed_units)
    manifest["total_units"] = int(total_units)
    _emit_progress(
      progress_callback,
      phase=phase,
      completed_units=completed_units,
      total_units=total_units,
      message=message,
    )
    write_json(run_dir / "manifest.json", manifest)
    if _call_cancel(cancel_callback):
      raise RunCancelled("运行已请求取消")

  def finalize_telemetry(*, enforce: bool) -> dict[str, Any]:
    nonlocal monitor_closed
    if gpu_sampler is not None:
      gpu_sampler.close()
    if monitor_started and not monitor_closed:
      try:
        monitor.close(enforce=enforce)
      finally:
        monitor_closed = True
    if telemetry_config is not None and telemetry_backend is not None:
      return _run_telemetry(
        spec=spec_payload,
        config=telemetry_config,
        backend=telemetry_backend,
        monitor=monitor,
        gpu_sampler=gpu_sampler,
        wall_started=wall_started,
        cpu_started=cpu_started,
        model_parameters=model_parameters,
      )
    return {
      "wall_time_seconds": max(0.0, time.perf_counter() - wall_started),
      "process_cpu_time_seconds": max(0.0, time.process_time() - cpu_started),
      "runtime_memory": monitor.to_dict(),
      "gpu": {
        "sampling_available": False,
        "sample_count": 0,
        "peak_memory_fraction": None,
        "peak_used_memory_mib": None,
        "minimum_available_memory_mib": None,
      },
      "qualification": None,
      "lightgbm": None,
      "environment": runtime_metadata(),
    }

  try:
    monitor.__enter__()
    monitor_started = True
    checkpoint(TrainingPhase.PREFLIGHT, 0, 1, "核验训练 spec 与认证数据集")
    config = _config_from_spec(spec_payload)
    panel, dataset_manifest = _load_certified_panel(dataset_directory)
    panel = _project_certified_panel(panel, dataset_manifest, config)
    requested, backend = _locked_backend(spec_payload, config)
    telemetry_config = config
    telemetry_backend = backend
    if backend is ResolvedBackend.LIGHTGBM_OPENCL_GPU:
      gpu_sampler = _start_gpu_runtime_sampler(
        config.runtime.memory_sample_interval_seconds
      )
    resolved = config.model_dump(mode="json")
    resolved["requested_backend"] = requested.value
    resolved["resolved_backend"] = backend.value
    config_hash = stable_json_sha256(_config_hash_payload(resolved))
    write_yaml(run_dir / "resolved-config.yaml", _redact_config_artifact(resolved))
    write_json(run_dir / "factor-schema.json", factor_schema_manifest())
    panel_hash = dataset_manifest["training_panel_sha256"]
    data_hash = _data_fingerprint(panel)
    months = sorted(panel["month"].unique())
    split = build_training_time_split(
      months,
      frozen_test_months=config.walk_forward.frozen_test_months,
      minimum_training_months=config.walk_forward.minimum_training_months,
      calibration_months=config.walk_forward.calibration_months,
      validation_months=config.walk_forward.validation_months,
    )
    folds = walk_forward_folds(
      [pd.Period(item, freq="M") for item in split.development_months],
      minimum_training_months=config.walk_forward.minimum_training_months,
      calibration_months=config.walk_forward.calibration_months,
    )
    frozen_periods = tuple(pd.Period(item, freq="M") for item in split.frozen_test_months)
    frozen_start = frozen_periods[0]
    pretest = panel[panel["month"] < frozen_start]
    calibration_periods = tuple(
      pretest["month"].drop_duplicates().sort_values()
    )[-config.walk_forward.calibration_months :]
    training = pretest[~pretest["month"].isin(calibration_periods)]
    calibration = pretest[pretest["month"].isin(calibration_periods)]

    def _bounds(frame: pd.DataFrame) -> tuple[str | None, str | None]:
      if frame.empty:
        return None, None
      values = pd.to_datetime(frame["event_date"], errors="coerce").dropna()
      if values.empty:
        return None, None
      return str(values.min().date()), str(values.max().date())

    training_start, training_end = _bounds(training)
    calibration_start, calibration_end = _bounds(calibration)
    test_frame = panel[panel["month"].isin(frozen_periods)]
    test_start, test_end = _bounds(test_frame)
    projection_evidence = {
      "date_start": config.data.date_range[0].isoformat(),
      "date_end": config.data.date_range[1].isoformat(),
      "stock_codes": sorted(set(config.data.stock_codes or ())),
      "minimum_listing_days": config.data.minimum_listing_days,
      "source_manifest_sha256": dataset_manifest["manifest_sha256"],
    }
    universe_quality = {
      "complete": bool(dataset_manifest.get("quality", {}).get("coverage", {}).get("historical_universe", {}).get("complete", False)),
      "coverage": dataset_manifest.get("quality", {}).get("coverage", {}).get("historical_universe", {}),
    }
    grid_units = len(folds) * (
      len(_family_grid(config, "LOGISTIC")) + len(_family_grid(config, "LIGHTGBM"))
    )
    total_units = max(grid_units + 5, 1)
    update_manifest(
      dataset_manifest_sha256=dataset_manifest["manifest_sha256"],
      training_panel_sha256=panel_hash,
      data_fingerprint=data_hash,
      config_hash=config_hash,
      sample_count=int(len(panel)),
      stock_count=int(panel["stock_code"].nunique()),
      trading_day_count=int(panel["event_date"].nunique()),
      date_start=str(panel["event_date"].min().date()),
      date_end=str(panel["event_date"].max().date()),
      projection=projection_evidence,
      requested_backend=requested.value,
      resolved_backend=backend.value,
      environment_requirement_hash=environment_requirement_hash,
      split=split.as_dict(),
      total_units=total_units,
    )
    checkpoint(TrainingPhase.DATASET_BUILD, 0, total_units, "认证面板核验完成")

    if kind is RunKind.DEVELOPMENT:
      logistic_params, logistic_search, completed = _select_parameters_checked(
        panel,
        folds,
        "LOGISTIC",
        config,
        backend=ResolvedBackend.CPU,
        cancel_callback=cancel_callback,
        progress_callback=progress_callback,
        completed_units=0,
        total_units=total_units,
      )
      lightgbm_params, lightgbm_search, completed = _select_parameters_checked(
        panel,
        folds,
        "LIGHTGBM",
        config,
        backend=backend,
        cancel_callback=cancel_callback,
        progress_callback=progress_callback,
        completed_units=completed,
        total_units=total_units,
      )
      model_parameters = {
        "LOGISTIC": logistic_params,
        "LIGHTGBM": lightgbm_params,
      }
      checkpoint(TrainingPhase.FINAL_FIT, completed, total_units, "锁定候选参数并拟合开发模型")
      logistic = _fit_checked(
        "LOGISTIC", logistic_params, training, calibration, config,
        backend=ResolvedBackend.CPU, cancel_callback=cancel_callback,
      )
      lightgbm = _fit_checked(
        "LIGHTGBM", lightgbm_params, training, calibration, config,
        backend=backend, cancel_callback=cancel_callback,
      )
      checkpoint(TrainingPhase.CALIBRATION, completed + 2, total_units, "校准器锁定完成")
      logistic_validation = min(item["mean_brier"] for item in logistic_search)
      lightgbm_validation = min(item["mean_brier"] for item in lightgbm_search)
      chosen = _model_choice(logistic_validation, lightgbm_validation)
      families = {
        "LOGISTIC": _save_family(run_dir, logistic),
        "LIGHTGBM": _save_family(run_dir, lightgbm),
      }
      write_json(run_dir / "preprocessing.json", {
        "schema_version": 1,
        "families": {"LOGISTIC": logistic.preprocessor, "LIGHTGBM": lightgbm.preprocessor},
      })
      write_json(run_dir / "calibrators.json", {
        "schema_version": 1,
        "version": CALIBRATOR_VERSION,
        "families": {"LOGISTIC": logistic.calibrator, "LIGHTGBM": lightgbm.calibrator},
      })
      for family in families.values():
        family["preprocessing_path"] = "preprocessing.json"
        family["calibrator_path"] = "calibrators.json"
      write_json(run_dir / "model-runtime.json", {
        "schema_version": 2,
        "selected_family": chosen,
        "families": families,
        "backend": backend.value,
        "max_bin": config.lightgbm.max_bin,
        "gpu_use_dp": config.lightgbm.gpu_use_dp,
      })
      validation_metrics = {
        "fold_count": len(folds),
        "logistic_brier": logistic_validation,
        "lightgbm_brier": lightgbm_validation,
        "logistic_grid": logistic_search,
        "lightgbm_grid": lightgbm_search,
      }
      development_gates = {
        "brier_skill_positive": False,
        "ece_within_3pct": False,
        "top20_lift_ci_lower_positive": False,
        "historical_universe_complete": universe_quality["complete"],
        "artifact_valid": True,
        "data_quality_valid": True,
        "effect_gate_passed": False,
        "unbiased_frozen_evidence": False,
        "active_eligible": False,
        "access_evidence_valid": False,
        "conclusion": GateConclusion.BLOCKED.value,
        "registerable": False,
      }
      model_version = _model_version(spec_hash, data_hash, chosen)
      write_json(run_dir / "metrics.json", {
        "schema_version": 2,
        "model_version": model_version,
        "selected_family": chosen,
        "spec_hash": spec_hash,
        "config_hash": config_hash,
        "coordinate_hash": coordinate_hash,
        "environment_requirement_hash": environment_requirement_hash,
        "calibrator_version": CALIBRATOR_VERSION,
        "training_start": training_start,
        "training_end": training_end,
        "calibration_start": calibration_start,
        "calibration_end": calibration_end,
        "test_start": None,
        "test_end": None,
        "validation": validation_metrics,
        "gates": development_gates,
        "conclusion": GateConclusion.BLOCKED.value,
        "registerable": False,
      })
      write_json(run_dir / "data-quality.json", {
        "schema_version": 2,
        "dataset_manifest_sha256": dataset_manifest["manifest_sha256"],
        "source_training_panel_sha256": panel_hash,
        "data_fingerprint": data_hash,
        "projection": projection_evidence,
        "sample_count": int(len(panel)),
        "stock_count": int(panel["stock_code"].nunique()),
        "trading_day_count": int(panel["event_date"].nunique()),
        "date_count": int(panel["event_date"].nunique()),
        "date_start": str(panel["event_date"].min().date()),
        "date_end": str(panel["event_date"].max().date()),
        "training_start": training_start,
        "training_end": training_end,
        "calibration_start": calibration_start,
        "calibration_end": calibration_end,
        "test_start": None,
        "test_end": None,
        "coverage": dataset_manifest.get("quality", {}).get("coverage", {}),
        "source": {
          "kind": dataset_manifest.get("source_kind"),
          "reference": dataset_manifest.get("source_reference"),
        },
        "historical_universe": universe_quality,
        "leakage_checks": dataset_manifest.get("quality", {}).get("leakage_checks", {}),
      })
      checkpoint(TrainingPhase.ARTIFACT_PUBLISH, total_units - 1, total_units, "发布 DEVELOPMENT 锁定产物")
      lock = {
        "schema_version": 1,
        "run_kind": RunKind.DEVELOPMENT.value,
        "spec_hash": spec_hash,
        "config_hash": config_hash,
        "coordinate_hash": coordinate_hash,
        "environment_requirement_hash": environment_requirement_hash,
        "dataset_manifest_sha256": dataset_manifest["manifest_sha256"],
        "training_panel_sha256": panel_hash,
        "data_fingerprint": data_hash,
        "requested_backend": requested.value,
        "resolved_backend": backend.value,
        "training_start": training_start,
        "training_end": training_end,
        "calibration_start": calibration_start,
        "calibration_end": calibration_end,
        "test_start": None,
        "test_end": None,
        "split": split.as_dict(),
        "parameters": {"LOGISTIC": logistic_params, "LIGHTGBM": lightgbm_params},
        "chosen_family": chosen,
        "calibrator_selection": {
          "LOGISTIC": logistic.calibrator.get("kind"),
          "LIGHTGBM": lightgbm.calibrator.get("kind"),
        },
        "versions": {
          "indicator_version": INDICATOR_VERSION,
          "factor_set_version": FACTOR_SET_VERSION,
          "factor_set_hash": FACTOR_SET_HASH,
          "label_version": LABEL_VERSION,
          "calibrator_version": CALIBRATOR_VERSION,
          "lightgbm_max_bin": config.lightgbm.max_bin,
          "gpu_use_dp": config.lightgbm.gpu_use_dp,
        },
        "gates": development_gates,
        "conclusion": GateConclusion.BLOCKED.value,
        "registerable": False,
        "artifact_hashes": {},
      }
      write_json(run_dir / "development-lock.json", lock)
      lock["artifact_hashes"] = _artifact_hashes(run_dir)
      write_json(run_dir / "development-lock.json", lock)
      checkpoint(TrainingPhase.ARTIFACT_PUBLISH, total_units, total_units, "DEVELOPMENT 完成")
      telemetry = finalize_telemetry(enforce=True)
      completed_at = datetime.now(timezone.utc)
      update_manifest(
        status="SUCCEEDED",
        phase=TrainingPhase.ARTIFACT_PUBLISH.value,
        completed_units=total_units,
        completed_at=completed_at,
        elapsed_seconds=max(0.0, (completed_at - started).total_seconds()),
        model_version=model_version,
        selected_family=chosen,
        config_hash=config_hash,
        environment_requirement_hash=environment_requirement_hash,
        training_start=training_start,
        training_end=training_end,
        calibration_start=calibration_start,
        calibration_end=calibration_end,
        test_start=None,
        test_end=None,
        gates=development_gates,
        conclusion=GateConclusion.BLOCKED.value,
        registerable=False,
        telemetry=telemetry,
        environment=telemetry["environment"],
        artifacts=artifact_index(run_dir),
      )
      return run_dir

    # FINAL_EVALUATION is deliberately a read-only consumer of the parent lock.
    if parent_run_directory is None:
      raise ValueError("FINAL_EVALUATION 必须提供成功 DEVELOPMENT 父目录")
    parent_dir = Path(parent_run_directory)
    _reject_links(parent_dir)
    parent_dir = parent_dir.resolve(strict=True)
    parent_manifest = _load_json(parent_dir / "manifest.json")
    if parent_manifest.get("status") != "SUCCEEDED" or parent_manifest.get("run_kind") != RunKind.DEVELOPMENT.value:
      raise ValueError("父级必须是成功的 DEVELOPMENT 运行")
    parent_run_id = _safe_run_id(parent_manifest.get("run_id", ""))
    # Trainer binds the parent ID and manifest hash before handing off its
    # content-addressed cache. Identity belongs to the verified manifest and
    # development lock, not the name of a machine-local directory.
    lock = _load_json(parent_dir / "development-lock.json")
    artifact_hashes, parent_validation, parent_lock_hash = _validate_parent_lock(
      parent_dir,
      parent_manifest,
      lock,
      spec_hash=spec_hash,
      coordinate_hash=coordinate_hash,
      config_hash=config_hash,
      environment_requirement_hash=environment_requirement_hash,
      dataset_manifest_hash=dataset_manifest["manifest_sha256"],
      panel_hash=panel_hash,
    )
    parent_metrics_hash = artifact_hashes["metrics.json"]
    parent_runtime = _load_json(parent_dir / "model-runtime.json")
    parent_preprocessor = _load_json(parent_dir / "preprocessing.json")
    parent_calibrators = _load_json(parent_dir / "calibrators.json")
    chosen = _validate_parent_runtime(
      parent_runtime,
      parent_preprocessor,
      parent_calibrators,
      config=config,
    )
    if lock.get("chosen_family") != chosen:
      raise ValueError("父级锁定 chosen_family 与 runtime 不一致")
    if lock.get("requested_backend") != requested.value or lock.get("resolved_backend") != backend.value:
      raise ValueError("父级锁定后端与当前已锁定后端不一致")
    if parent_runtime.get("backend") != backend.value:
      raise ValueError("父级 runtime 后端与当前已锁定后端不一致")
    if lock.get("split") != split.as_dict():
      raise ValueError("父级锁定时间切分与当前切分不一致")
    expected_parameters = {
      "LOGISTIC": _family_grid(config, "LOGISTIC"),
      "LIGHTGBM": _family_grid(config, "LIGHTGBM"),
    }
    locked_parameters = lock.get("parameters")
    if not isinstance(locked_parameters, dict) or any(
      family not in locked_parameters or not isinstance(locked_parameters[family], dict)
      for family in expected_parameters
    ):
      raise ValueError("父级锁定参数证据不完整")
    if any(
      locked_parameters[family] not in expected_parameters[family]
      for family in expected_parameters
    ):
      raise ValueError("父级锁定参数不属于当前冻结参数网格")
    model_parameters = locked_parameters
    parent_quality = _load_json(parent_dir / "data-quality.json")
    parent_leakage = parent_quality.get("leakage_checks")
    parent_data_quality_valid = bool(
      isinstance(parent_leakage, dict)
      and parent_leakage
      and all(value is True for value in parent_leakage.values())
      and parent_quality.get("dataset_manifest_sha256") == dataset_manifest["manifest_sha256"]
      and parent_quality.get("source_training_panel_sha256") == panel_hash
      and parent_quality.get("data_fingerprint") == lock.get("data_fingerprint")
    )
    _copy_parent_artifacts(parent_dir, run_dir, parent_runtime)
    write_json(run_dir / "model-runtime.json", parent_runtime)
    write_json(run_dir / "preprocessing.json", parent_preprocessor)
    write_json(run_dir / "calibrators.json", parent_calibrators)
    logistic = _load_parent_family(parent_dir, "LOGISTIC", parent_runtime, parent_preprocessor, parent_calibrators)
    lightgbm = _load_parent_family(parent_dir, "LIGHTGBM", parent_runtime, parent_preprocessor, parent_calibrators)
    checkpoint(TrainingPhase.FROZEN_TEST, max(grid_units, 0), total_units, "按父级锁定模型执行一次冻结测试")
    test = panel[panel["month"].isin([pd.Period(item, freq="M") for item in split.frozen_test_months])].copy()
    if test.empty:
      raise ValueError("冻结测试区间没有认证样本")
    logistic_raw, _, logistic_probability = _predict(logistic, test)
    lightgbm_raw, _, lightgbm_probability = _predict(lightgbm, test)
    arrays = (
      logistic_raw,
      logistic_probability,
      lightgbm_raw,
      lightgbm_probability,
      test["label"].to_numpy(dtype=float),
    )
    if any(value.ndim != 1 or value.size != len(test) or not np.isfinite(value).all() for value in arrays):
      raise ValueError("冻结测试模型输出含非有限值或维度不一致")
    selected_probability = logistic_probability if chosen == "LOGISTIC" else lightgbm_probability
    selected_raw = logistic_raw if chosen == "LOGISTIC" else lightgbm_raw
    test["probability"] = selected_probability
    test["raw_score"] = selected_raw
    test["logistic_probability"] = logistic_probability
    test["lightgbm_probability"] = lightgbm_probability
    probability_delta = np.abs(logistic_probability - lightgbm_probability)
    probability_disagreement = {
      "sample_count": int(len(probability_delta)),
      "threshold": 0.05,
      "mean_absolute_difference": float(np.mean(probability_delta)),
      "median_absolute_difference": float(np.median(probability_delta)),
      "max_absolute_difference": float(np.max(probability_delta)),
      "fraction_at_least_5pct": float(np.mean(probability_delta >= 0.05)),
    }
    probability_metrics = evaluate_probability(test["label"].to_numpy(), selected_probability, bins=config.calibration.bins)
    ranking_metrics = evaluate_ranking(test, bootstrap_samples=config.evaluation.bootstrap_samples, seed=config.random_seed)
    stability = annual_stability(test, bins=config.calibration.bins)
    gates, conclusion = _gate_projection(
      probability_metrics,
      ranking_metrics,
      universe_quality,
      frozen_test_access_count=frozen_test_access_count,
      artifact_valid=True,
      data_quality_valid=parent_data_quality_valid,
    )
    registerable = bool(
      conclusion is not GateConclusion.BLOCKED
      and gates.get("unbiased_frozen_evidence") is True
      and gates.get("access_evidence_valid") is True
    )
    gates["registerable"] = registerable
    model_version = _model_version(spec_hash, data_hash, chosen)
    prediction_columns = [
      "event_date", "target_date", "stock_code", "label", "next_open_to_close_return",
      "probability", "raw_score", "logistic_probability", "lightgbm_probability", "factor_completeness",
    ]
    test[[column for column in prediction_columns if column in test]].to_parquet(run_dir / "test-predictions.parquet", index=False)
    write_json(run_dir / "metrics.json", {
      "schema_version": 2,
      "model_version": model_version,
      "selected_family": chosen,
      "spec_hash": spec_hash,
      "config_hash": config_hash,
      "coordinate_hash": coordinate_hash,
      "environment_requirement_hash": environment_requirement_hash,
      "calibrator_version": CALIBRATOR_VERSION,
      "training_start": training_start,
      "training_end": training_end,
      "calibration_start": calibration_start,
      "calibration_end": calibration_end,
      "test_start": test_start,
      "test_end": test_end,
      "validation": parent_validation,
      "parent_development": {
        "run_id": parent_run_id,
        "metrics_sha256": parent_metrics_hash,
        "lock_sha256": parent_lock_hash,
      },
      "frozen_test": {
        "start": str(test["event_date"].min().date()),
        "end": str(test["event_date"].max().date()),
        "probability": probability_metrics,
        "ranking": ranking_metrics,
        "annual_stability": stability,
        "access_count": int(frozen_test_access_count),
      },
      "probability_disagreement": probability_disagreement,
      "gates": gates,
      "conclusion": conclusion.value,
      "registerable": registerable,
    })
    write_json(run_dir / "data-quality.json", {
      "schema_version": 2,
      "dataset_manifest_sha256": dataset_manifest["manifest_sha256"],
      "source_training_panel_sha256": panel_hash,
      "data_fingerprint": data_hash,
      "projection": projection_evidence,
      "sample_count": int(len(panel)),
      "stock_count": int(panel["stock_code"].nunique()),
      "trading_day_count": int(panel["event_date"].nunique()),
      "date_count": int(panel["event_date"].nunique()),
      "date_start": str(panel["event_date"].min().date()),
      "date_end": str(panel["event_date"].max().date()),
      "training_start": training_start,
      "training_end": training_end,
      "calibration_start": calibration_start,
      "calibration_end": calibration_end,
      "test_start": test_start,
      "test_end": test_end,
      "coverage": dataset_manifest.get("quality", {}).get("coverage", {}),
      "source": {
        "kind": dataset_manifest.get("source_kind"),
        "reference": dataset_manifest.get("source_reference"),
      },
      "historical_universe": universe_quality,
      "leakage_checks": dataset_manifest.get("quality", {}).get("leakage_checks", {}),
    })
    checkpoint(TrainingPhase.ARTIFACT_PUBLISH, total_units - 1, total_units, "发布 FINAL_EVALUATION 证据")
    telemetry = finalize_telemetry(enforce=True)
    completed_at = datetime.now(timezone.utc)
    update_manifest(
      status="SUCCEEDED",
      phase=TrainingPhase.ARTIFACT_PUBLISH.value,
      completed_units=total_units,
      completed_at=completed_at,
      elapsed_seconds=max(0.0, (completed_at - started).total_seconds()),
      model_version=model_version,
      selected_family=chosen,
      config_hash=config_hash,
      environment_requirement_hash=environment_requirement_hash,
      training_start=training_start,
      training_end=training_end,
      calibration_start=calibration_start,
      calibration_end=calibration_end,
      test_start=test_start,
      test_end=test_end,
      gates=gates,
      conclusion=conclusion.value,
      registerable=registerable,
      telemetry=telemetry,
      environment=telemetry["environment"],
      artifacts=artifact_index(run_dir),
      parent_run_id=parent_run_id,
    )
    return run_dir
  except RunCancelled as exc:
    try:
      telemetry = finalize_telemetry(enforce=False)
    except BaseException:
      telemetry = None
    update_manifest(
      status="CANCELLED",
      completed_at=datetime.now(timezone.utc),
      error_type=type(exc).__name__,
      error_message=_safe_error(exc),
      registerable=False,
      telemetry=telemetry,
      environment=telemetry["environment"] if telemetry else runtime_metadata(),
      artifacts=artifact_index(run_dir),
    )
    raise
  except BaseException as exc:
    try:
      telemetry = finalize_telemetry(enforce=False)
    except BaseException:
      telemetry = None
    update_manifest(
      status="FAILED",
      completed_at=datetime.now(timezone.utc),
      error_type=type(exc).__name__,
      error_message=_safe_error(exc),
      registerable=False,
      telemetry=telemetry,
      environment=telemetry["environment"] if telemetry else runtime_metadata(),
      artifacts=artifact_index(run_dir),
    )
    raise
  finally:
    if monitor_started and not monitor_closed:
      try:
        finalize_telemetry(enforce=False)
      except BaseException:
        pass
    _safe_remove_tree(staging)


async def train_next_day_selection(
  config_path: str | Path,
  *,
  run_kind: RunKind | str,
  dataset_directory: str | Path,
  spec_hash: str,
  coordinate_hash: str,
  environment_requirement_hash: str,
  output_root: str | Path | None = None,
  run_id: str | None = None,
  parent_run_directory: str | Path | None = None,
  progress_callback: Any | None = None,
  cancel_callback: Any | None = None,
  frozen_test_access_count: int = 0,
) -> Path:
  """CLI-facing adapter; both run kinds use the same execution function."""

  config = load_next_day_selection_config(config_path)
  spec = config.model_dump(mode="json")
  spec["run_kind"] = RunKind(run_kind).value
  spec["spec_hash"] = _locked_hash(
    {"spec_hash": spec_hash}, "spec_hash"
  )
  spec["coordinate_hash"] = _locked_hash(
    {"coordinate_hash": coordinate_hash}, "coordinate_hash"
  )
  spec["environment_requirement_hash"] = _locked_hash(
    {"environment_requirement_hash": environment_requirement_hash},
    "environment_requirement_hash",
  )
  # The adapter is only allowed to freeze an explicit CPU choice locally.  A
  # non-CPU request must arrive with an upstream qualification decision in the
  # immutable spec rather than being re-resolved here.
  if config.requested_backend == RequestedBackend.CPU.value:
    spec["resolved_backend"] = ResolvedBackend.CPU.value
  if parent_run_directory is not None:
    spec["parent_run_directory"] = str(parent_run_directory)
  identifier = run_id or datetime.now(timezone.utc).strftime("next-day-selection-%Y%m%d-%H%M%S")
  root = output_root or config.runtime.output_root
  return await execute_next_day_selection_run(
    run_kind=run_kind,
    spec=spec,
    dataset_directory=dataset_directory,
    output_root=root,
    run_id=identifier,
    parent_run_directory=parent_run_directory,
    progress_callback=progress_callback,
    cancel_callback=cancel_callback,
    frozen_test_access_count=frozen_test_access_count,
  )


__all__ = [
  "FittedFamily",
  "RunCancelled",
  "apply_calibrator_artifact",
  "build_next_day_labels",
  "execute_next_day_selection_run",
  "prepare_training_panel",
  "train_next_day_selection",
  "transform_with_preprocessor",
  "walk_forward_folds",
]
