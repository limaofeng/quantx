"""Manual, point-in-time training for next-open-to-close up probability."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

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
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from quantx_research.artifacts import (
  artifact_index,
  create_run_directory,
  fingerprint,
  git_state,
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
    model = lgb.LGBMClassifier(
      objective="binary",
      learning_rate=config.lightgbm.learning_rate,
      n_estimators=config.lightgbm.n_estimators,
      num_leaves=int(params["num_leaves"]),
      reg_lambda=float(params["reg_lambda"]),
      min_child_samples=config.lightgbm.min_child_samples,
      subsample=config.lightgbm.subsample,
      subsample_freq=1,
      colsample_bytree=config.lightgbm.colsample_bytree,
      random_state=config.random_seed,
      n_jobs=-1,
      verbosity=-1,
    )
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
  panel = pd.concat(
    [panel.reset_index(drop=True), factors.reset_index(drop=True)], axis=1
  )
  panel = panel[panel["label"].notna()].copy()
  panel["month"] = panel["event_date"].dt.to_period("M")
  if panel.empty:
    raise ValueError("训练区间没有符合标签和完整性门禁的样本")
  return panel, universe_quality


async def _source_panel(
  config: NextDaySelectionConfig,
  staging: Path,
) -> tuple[pd.DataFrame, pd.DatetimeIndex, dict[str, Any]]:
  if config.data.verified_panel_path is not None:
    panel = pd.read_parquet(config.data.verified_panel_path)
    calendar = pd.DatetimeIndex(
      pd.to_datetime(panel["event_date"]).dropna().unique()
    ).sort_values()
    return (
      panel,
      calendar,
      {"kind": "verified-panel", "path": str(config.data.verified_panel_path)},
    )
  indicator_ids = tuple(
    sorted(
      {
        indicator_id
        for definition in SELECTION_FACTOR_DEFINITIONS
        for indicator_id in definition.source_indicators
      }
    )
  )
  study_config = IndicatorStudyConfig.model_validate(
    {
      "study": "indicator-study",
      "version": "v1",
      "indicator_ids": indicator_ids,
      "date_range": config.data.date_range,
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
  monitor = RuntimeMemoryMonitor(
    reserve_gib=config.runtime.minimum_available_memory_gib,
    sample_interval_seconds=config.runtime.memory_sample_interval_seconds,
  )
  async with _research_source(
    None, market_data_archive=config.data.market_data_archive
  ) as source:
    with monitor:
      stage = await stage_indicator_features(source, study_config, staging, monitor)
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


async def train_next_day_selection(
  config_path: str | Path,
  *,
  output_root: str | Path | None = None,
  now: datetime | None = None,
) -> Path:
  config = load_next_day_selection_config(config_path)
  resolved = config.model_dump(mode="json")
  config_hash = fingerprint(resolved)
  root = Path(output_root or config.runtime.output_root)
  if not root.is_absolute():
    root = REPO_ROOT / root
  run_dir = create_run_directory(
    root, config.study_id, config.version, config_hash, now=now
  )
  started = now or datetime.now(timezone.utc)
  if started.tzinfo is None:
    started = started.replace(tzinfo=timezone.utc)
  manifest: dict[str, Any] = {
    "schema_version": 1,
    "study_id": config.study_id,
    "version": config.version,
    "run_id": run_dir.name,
    "status": "running",
    "started_at": started,
    "config_hash": config_hash,
    "indicator_version": INDICATOR_VERSION,
    "factor_set_version": FACTOR_SET_VERSION,
    "factor_set_hash": FACTOR_SET_HASH,
    "label_version": LABEL_VERSION,
  }
  write_json(run_dir / "manifest.json", manifest)
  write_yaml(run_dir / "resolved-config.yaml", resolved)
  write_json(run_dir / "factor-schema.json", factor_schema_manifest())
  staging = Path(tempfile.mkdtemp(prefix="next-day-selection-", dir=run_dir))
  try:
    raw_panel, calendar, source_quality = await _source_panel(config, staging)
    shutil.rmtree(staging, ignore_errors=True)
    panel, universe_quality = prepare_training_panel(raw_panel, calendar, config)
    data_hash = _data_fingerprint(panel)
    months = sorted(panel["month"].unique())
    if len(months) < 48 + config.walk_forward.frozen_test_months:
      raise ValueError("有效样本不足完整的 48 月开发区间与 12 月冻结测试区间")
    test_months = tuple(months[-config.walk_forward.frozen_test_months :])
    development_months = tuple(
      months[
        -(
          48 + config.walk_forward.frozen_test_months
        ) : -config.walk_forward.frozen_test_months
      ]
    )
    folds = walk_forward_folds(
      development_months,
      minimum_training_months=config.walk_forward.minimum_training_months,
      calibration_months=config.walk_forward.calibration_months,
    )
    logistic_params, logistic_search = _select_parameters(
      panel, folds, "LOGISTIC", config
    )
    lightgbm_params, lightgbm_search = _select_parameters(
      panel, folds, "LIGHTGBM", config
    )
    logistic_validation = min(item["mean_brier"] for item in logistic_search)
    lightgbm_validation = min(item["mean_brier"] for item in lightgbm_search)
    chosen = _model_choice(logistic_validation, lightgbm_validation)

    test_start = test_months[0]
    pretest_months = tuple(month for month in months if month < test_start)
    calibration_months = pretest_months[-config.walk_forward.calibration_months :]
    training_months = pretest_months[: -config.walk_forward.calibration_months]
    training = panel[panel["month"].isin(training_months)]
    calibration = panel[panel["month"].isin(calibration_months)]
    test = panel[panel["month"].isin(test_months)].copy()
    logistic = _fit_family("LOGISTIC", logistic_params, training, calibration, config)
    lightgbm = _fit_family("LIGHTGBM", lightgbm_params, training, calibration, config)
    logistic_raw, logistic_uncalibrated, logistic_probability = _predict(logistic, test)
    lightgbm_raw, lightgbm_uncalibrated, lightgbm_probability = _predict(lightgbm, test)
    selected_probability = (
      logistic_probability if chosen == "LOGISTIC" else lightgbm_probability
    )
    selected_raw = logistic_raw if chosen == "LOGISTIC" else lightgbm_raw
    test["probability"] = selected_probability
    test["raw_score"] = selected_raw
    test["logistic_probability"] = logistic_probability
    test["lightgbm_probability"] = lightgbm_probability

    probability_metrics = evaluate_probability(
      test["label"].to_numpy(),
      selected_probability,
      bins=config.calibration.bins,
    )
    ranking_metrics = evaluate_ranking(
      test,
      bootstrap_samples=config.evaluation.bootstrap_samples,
      seed=config.random_seed,
    )
    stability = annual_stability(test, bins=config.calibration.bins)
    gates = {
      "brier_skill_positive": bool((probability_metrics["brier_skill"] or 0) > 0),
      "ece_within_3pct": bool(
        probability_metrics["ece"] <= config.candidate_gate.ece_maximum
      ),
      "top20_lift_ci_lower_positive": bool(
        (ranking_metrics["top_20"]["up_rate_lift_ci_low"] or 0) > 0
      ),
      "historical_universe_complete": bool(universe_quality["complete"]),
    }
    gates["effect_gate_passed"] = all(
      gates[key]
      for key in (
        "brier_skill_positive",
        "ece_within_3pct",
        "top20_lift_ci_lower_positive",
      )
    )
    gates["active_eligible"] = bool(
      gates["effect_gate_passed"] and gates["historical_universe_complete"]
    )
    model_version = _model_version(config_hash, data_hash, chosen)
    families = {
      "LOGISTIC": _save_family(run_dir, logistic),
      "LIGHTGBM": _save_family(run_dir, lightgbm),
    }
    write_json(
      run_dir / "preprocessing.json",
      {
        "schema_version": 1,
        "families": {
          "LOGISTIC": logistic.preprocessor,
          "LIGHTGBM": lightgbm.preprocessor,
        },
      },
    )
    write_json(
      run_dir / "calibrators.json",
      {
        "schema_version": 1,
        "version": CALIBRATOR_VERSION,
        "families": {
          "LOGISTIC": logistic.calibrator,
          "LIGHTGBM": lightgbm.calibrator,
        },
      },
    )
    for family in families.values():
      family["preprocessing_path"] = "preprocessing.json"
      family["calibrator_path"] = "calibrators.json"
    write_json(
      run_dir / "model-runtime.json", {"selected_family": chosen, "families": families}
    )
    test[
      [
        "event_date",
        "target_date",
        "stock_code",
        "label",
        "next_open_to_close_return",
        "probability",
        "raw_score",
        "logistic_probability",
        "lightgbm_probability",
        "factor_completeness",
      ]
    ].to_parquet(run_dir / "test-predictions.parquet", index=False)
    metrics = {
      "schema_version": 1,
      "model_version": model_version,
      "selected_family": chosen,
      "validation": {
        "fold_count": len(folds),
        "logistic_brier": logistic_validation,
        "lightgbm_brier": lightgbm_validation,
        "logistic_grid": logistic_search,
        "lightgbm_grid": lightgbm_search,
      },
      "frozen_test": {
        "start": str(test["event_date"].min().date()),
        "end": str(test["event_date"].max().date()),
        "probability": probability_metrics,
        "ranking": ranking_metrics,
        "annual_stability": stability,
      },
      "gates": gates,
    }
    data_quality = {
      "source": source_quality,
      "historical_universe": universe_quality,
      "data_fingerprint": data_hash,
      "sample_count": int(len(panel)),
      "stock_count": int(panel["stock_code"].nunique()),
      "date_count": int(panel["event_date"].nunique()),
      "data_start": str(panel["event_date"].min().date()),
      "data_end": str(panel["event_date"].max().date()),
    }
    write_json(run_dir / "metrics.json", metrics)
    write_json(run_dir / "data-quality.json", data_quality)
    completed = datetime.now(timezone.utc)
    manifest.update(
      {
        "status": "success",
        "completed_at": completed,
        "event_count": int(len(test)),
        "elapsed_seconds": max(0.0, (completed - started).total_seconds()),
        "model_version": model_version,
        "selected_family": chosen,
        "calibrator_version": CALIBRATOR_VERSION,
        "data_fingerprint": data_hash,
        "training_start": str(training["event_date"].min().date()),
        "training_end": str(training["event_date"].max().date()),
        "calibration_start": str(calibration["event_date"].min().date()),
        "calibration_end": str(calibration["event_date"].max().date()),
        "test_start": str(test["event_date"].min().date()),
        "test_end": str(test["event_date"].max().date()),
        "gates": gates,
        "runtime": runtime_metadata(),
        "git": git_state(REPO_ROOT),
      }
    )
    manifest["artifacts"] = artifact_index(run_dir)
    write_json(run_dir / "manifest.json", manifest)
    return run_dir
  except BaseException as exc:
    shutil.rmtree(staging, ignore_errors=True)
    manifest.update(
      {
        "status": "failed",
        "completed_at": datetime.now(timezone.utc),
        "error": f"{type(exc).__name__}: {exc}",
        "artifacts": artifact_index(run_dir),
      }
    )
    write_json(run_dir / "manifest.json", manifest)
    raise
