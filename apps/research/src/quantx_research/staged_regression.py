"""Bounded-memory panel regressions over staged Parquet partitions."""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from quantx_research.core.config import StudyConfig
from quantx_research.core.models import RegressionCoefficient, RegressionResult
from quantx_research.core.regression import _apply_interaction_fdr
from quantx_research.runtime_memory import RuntimeMemoryMonitor
from quantx_research.staging import StagedVolumeDataset, estimate_projection_bytes

_PARQUET_BATCH_SIZE = 65_536
_FEATURE_TERMS = (
  "shock_indicator",
  "centered_price_position",
  "shock_position_interaction",
  "momentum_20",
  "volatility_20",
  "log_average_amount_20",
)
_CONTROL_COLUMNS = (
  "price_position",
  "momentum_20",
  "volatility_20",
  "log_average_amount_20",
)
_FOCAL_INTERACTION = "shock_position_interaction"


@dataclass(slots=True)
class _DateAggregate:
  count: int
  sums: np.ndarray


@dataclass(frozen=True, slots=True)
class _EligibleDateLookup:
  keys: np.ndarray
  means: np.ndarray


@dataclass(frozen=True, slots=True)
class _NormalEquations:
  nobs: int
  feature_sum: np.ndarray
  feature_sum_squares: np.ndarray
  xtx: np.ndarray
  xty: np.ndarray
  sst: float


def run_staged_panel_regressions(
  staged: StagedVolumeDataset,
  config: StudyConfig,
  *,
  monitor: RuntimeMemoryMonitor,
) -> list[RegressionResult]:
  """Estimate the canonical regressions without materializing the full panel."""
  if not config.statistics.run_regression:
    return []

  centers = _global_centers_and_validate(staged, monitor)
  results: list[RegressionResult] = []
  for horizon in config.outcomes.horizons:
    if config.outcomes.include_close_response:
      dependent = _dependent_column("close", horizon, config)
      results.append(
        _fit_one_staged(
          staged,
          dependent,
          centers=centers,
          return_kind="close_response",
          horizon=horizon,
          confidence_level=config.statistics.confidence_level,
          minimum_observations=max(
            config.statistics.minimum_cell_samples,
            len(_FEATURE_TERMS) + 2,
          ),
          minimum_date_clusters=config.statistics.minimum_inference_dates,
          monitor=monitor,
        )
      )
    if config.outcomes.include_next_open_return:
      dependent = _dependent_column("next_open", horizon, config)
      results.append(
        _fit_one_staged(
          staged,
          dependent,
          centers=centers,
          return_kind="next_open",
          horizon=horizon,
          confidence_level=config.statistics.confidence_level,
          minimum_observations=max(
            config.statistics.minimum_cell_samples,
            len(_FEATURE_TERMS) + 2,
          ),
          minimum_date_clusters=config.statistics.minimum_inference_dates,
          monitor=monitor,
        )
      )
  return _apply_interaction_fdr(results, alpha=config.statistics.fdr_alpha)


def _global_centers_and_validate(
  staged: StagedVolumeDataset,
  monitor: RuntimeMemoryMonitor,
) -> dict[str, float]:
  columns = (
    "stock_code",
    "event_date",
    "is_primary_shock_event",
    *_CONTROL_COLUMNS,
  )
  sums = {column: 0.0 for column in _CONTROL_COLUMNS}
  counts = {column: 0 for column in _CONTROL_COLUMNS}
  previous_code: str | None = None
  previous_date: int | None = None
  row_count = 0

  for frame in _iter_frames(
    staged,
    columns,
    monitor=monitor,
    stage="regression_global_centers",
  ):
    codes, dates = _code_date_arrays(frame)
    previous_code, previous_date = _validate_panel_order(
      codes,
      dates,
      previous_code=previous_code,
      previous_date=previous_date,
    )
    row_count += len(frame)
    for column in _CONTROL_COLUMNS:
      values = _numeric_array(frame[column])
      valid = np.isfinite(values)
      if valid.any():
        sums[column] = math.fsum((sums[column], float(values[valid].sum())))
        counts[column] += int(valid.sum())

  if row_count != staged.analysis_sample_count:
    raise ValueError(
      f"staged regression 行数不一致: {row_count} != {staged.analysis_sample_count}"
    )
  return {
    column: sums[column] / counts[column] if counts[column] else math.nan
    for column in _CONTROL_COLUMNS
  }


def _fit_one_staged(
  staged: StagedVolumeDataset,
  dependent: str,
  *,
  centers: dict[str, float],
  return_kind: str,
  horizon: int,
  confidence_level: float,
  minimum_observations: int,
  minimum_date_clusters: int,
  monitor: RuntimeMemoryMonitor,
) -> RegressionResult:
  warnings: list[str] = []
  value_columns = (
    "event_date",
    "is_primary_shock_event",
    *_CONTROL_COLUMNS,
    dependent,
  )
  cluster_columns = ("stock_code", *value_columns)
  missing = _missing_partition_columns(staged, cluster_columns)
  if missing:
    return RegressionResult(
      return_kind=return_kind,
      horizon=horizon,
      dependent_variable=dependent,
      nobs=0,
      r_squared=None,
      warnings=[f"回归缺少字段: {', '.join(missing)}"],
    )

  date_aggregates = _collect_date_aggregates(
    staged,
    value_columns,
    dependent=dependent,
    centers=centers,
    monitor=monitor,
  )
  singleton_rows = sum(
    aggregate.count for aggregate in date_aggregates.values() if aggregate.count < 2
  )
  if singleton_rows:
    warnings.append(f"{singleton_rows} 个日期单例样本被日期固定效应完全吸收，已移除")
  eligible_dates = {
    date_key: aggregate
    for date_key, aggregate in date_aggregates.items()
    if aggregate.count >= 2
  }
  nobs = sum(aggregate.count for aggregate in eligible_dates.values())
  if nobs < minimum_observations:
    return RegressionResult(
      return_kind=return_kind,
      horizon=horizon,
      dependent_variable=dependent,
      nobs=nobs,
      r_squared=None,
      warnings=[f"有效样本 {nobs} 少于回归最低要求 {minimum_observations}，未估计"],
    )
  date_cluster_count = len(eligible_dates)
  if date_cluster_count < minimum_date_clusters:
    return RegressionResult(
      return_kind=return_kind,
      horizon=horizon,
      dependent_variable=dependent,
      nobs=nobs,
      r_squared=None,
      warnings=[
        f"独立事件日期 {date_cluster_count} 少于推断最低要求 "
        f"{minimum_date_clusters}，未估计"
      ],
    )
  date_lookup = _build_date_lookup(eligible_dates)

  equations = _collect_normal_equations(
    staged,
    value_columns,
    dependent=dependent,
    centers=centers,
    date_lookup=date_lookup,
    monitor=monitor,
  )
  if equations.nobs != nobs:
    raise ValueError(
      f"{dependent} 回归有效行数在扫描间发生变化: {equations.nobs} != {nobs}"
    )
  variances = equations.feature_sum_squares / nobs - (equations.feature_sum / nobs) ** 2
  active_indices = [
    index
    for index, variance in enumerate(variances)
    if math.sqrt(max(0.0, float(variance))) > 1e-12
  ]
  active_terms = [_FEATURE_TERMS[index] for index in active_indices]
  dropped_terms = [
    term for index, term in enumerate(_FEATURE_TERMS) if index not in active_indices
  ]
  if dropped_terms:
    warnings.append(f"日期固定效应吸收后常量项已移除: {', '.join(dropped_terms)}")
  if not active_terms:
    return RegressionResult(
      return_kind=return_kind,
      horizon=horizon,
      dependent_variable=dependent,
      nobs=nobs,
      r_squared=None,
      warnings=warnings + ["所有解释变量均无可识别的组内变化"],
    )

  xtx = equations.xtx[np.ix_(active_indices, active_indices)]
  xty = equations.xty[active_indices]
  matrix_rank = _matrix_rank_from_xtx(xtx, nobs=nobs)
  if matrix_rank < len(active_terms):
    return RegressionResult(
      return_kind=return_kind,
      horizon=horizon,
      dependent_variable=dependent,
      nobs=nobs,
      r_squared=None,
      warnings=warnings
      + ["日期固定效应吸收后的设计矩阵秩不足，未报告不可识别的回归系数"],
    )
  beta = np.linalg.pinv(xtx, rcond=1e-12) @ xty
  (
    covariance,
    residual_sum_squares,
    covariance_warning,
  ) = _collect_cluster_covariance(
    staged,
    cluster_columns,
    dependent=dependent,
    centers=centers,
    date_lookup=date_lookup,
    active_indices=active_indices,
    xtx=xtx,
    beta=beta,
    nobs=nobs,
    monitor=monitor,
  )
  if covariance_warning:
    warnings.append(covariance_warning)
  r_squared = 1.0 - residual_sum_squares / equations.sst if equations.sst > 0 else None
  critical = NormalDist().inv_cdf((1.0 + confidence_level) / 2.0)
  coefficients: list[RegressionCoefficient] = []
  for index, (term, estimate) in enumerate(zip(active_terms, beta)):
    std_error: float | None = None
    if covariance is not None:
      variance = float(covariance[index, index])
      if np.isfinite(variance) and variance >= 0:
        std_error = math.sqrt(variance)
      else:
        warnings.append(f"{term} 的聚类方差非正，未报告标准误")
    t_stat = (
      float(estimate / std_error) if std_error is not None and std_error > 0 else None
    )
    p_value = math.erfc(abs(t_stat) / math.sqrt(2.0)) if t_stat is not None else None
    coefficients.append(
      RegressionCoefficient(
        term=term,
        estimate=_finite(estimate),
        std_error=_finite(std_error),
        t_stat=_finite(t_stat),
        p_value=_finite(p_value),
        ci_low=_finite(estimate - critical * std_error)
        if std_error is not None
        else None,
        ci_high=_finite(estimate + critical * std_error)
        if std_error is not None
        else None,
      )
    )
  return RegressionResult(
    return_kind=return_kind,
    horizon=horizon,
    dependent_variable=dependent,
    nobs=nobs,
    r_squared=_finite(r_squared),
    coefficients=coefficients,
    warnings=list(dict.fromkeys(warnings)),
  )


def _collect_date_aggregates(
  staged: StagedVolumeDataset,
  columns: Sequence[str],
  *,
  dependent: str,
  centers: dict[str, float],
  monitor: RuntimeMemoryMonitor,
) -> dict[int, _DateAggregate]:
  result: dict[int, _DateAggregate] = {}
  for frame in _iter_frames(
    staged,
    columns,
    monitor=monitor,
    stage=f"regression_{dependent}_date_stats",
  ):
    dates = _date_array(frame)
    values = _regression_values(frame, dependent=dependent, centers=centers)
    valid = _valid_value_rows(dates, values)
    if not valid.any():
      continue
    valid_dates = dates[valid]
    valid_values = values[valid]
    unique_dates, inverse, counts = np.unique(
      valid_dates,
      return_inverse=True,
      return_counts=True,
    )
    local_sums = np.zeros((len(unique_dates), valid_values.shape[1]), dtype=float)
    np.add.at(local_sums, inverse, valid_values)
    for date_key, count, sums in zip(unique_dates, counts, local_sums):
      key = int(date_key)
      existing = result.get(key)
      if existing is None:
        result[key] = _DateAggregate(int(count), sums.copy())
      else:
        existing.count += int(count)
        existing.sums += sums
  return result


def _collect_normal_equations(
  staged: StagedVolumeDataset,
  columns: Sequence[str],
  *,
  dependent: str,
  centers: dict[str, float],
  date_lookup: _EligibleDateLookup,
  monitor: RuntimeMemoryMonitor,
) -> _NormalEquations:
  feature_count = len(_FEATURE_TERMS)
  nobs = 0
  feature_sum = np.zeros(feature_count, dtype=float)
  feature_sum_squares = np.zeros(feature_count, dtype=float)
  xtx = np.zeros((feature_count, feature_count), dtype=float)
  xty = np.zeros(feature_count, dtype=float)
  sst = 0.0
  for frame in _iter_frames(
    staged,
    columns,
    monitor=monitor,
    stage=f"regression_{dependent}_normal_equations",
  ):
    dates = _date_array(frame)
    values = _regression_values(frame, dependent=dependent, centers=centers)
    valid = _valid_value_rows(dates, values)
    eligible, date_indices = _match_eligible_dates(dates, date_lookup)
    valid &= eligible
    if not valid.any():
      continue
    demeaned = values[valid] - date_lookup.means[date_indices[valid]]
    y = demeaned[:, 0]
    x = demeaned[:, 1:]
    nobs += len(y)
    feature_sum += x.sum(axis=0)
    feature_sum_squares += np.square(x).sum(axis=0)
    xtx += x.T @ x
    xty += x.T @ y
    sst = math.fsum((sst, float(y @ y)))
  return _NormalEquations(
    nobs=nobs,
    feature_sum=feature_sum,
    feature_sum_squares=feature_sum_squares,
    xtx=xtx,
    xty=xty,
    sst=sst,
  )


def _collect_cluster_covariance(
  staged: StagedVolumeDataset,
  columns: Sequence[str],
  *,
  dependent: str,
  centers: dict[str, float],
  date_lookup: _EligibleDateLookup,
  active_indices: list[int],
  xtx: np.ndarray,
  beta: np.ndarray,
  nobs: int,
  monitor: RuntimeMemoryMonitor,
) -> tuple[np.ndarray | None, float, str | None]:
  parameter_count = len(active_indices)
  stock_scores: dict[str, np.ndarray] = {}
  date_scores: dict[int, np.ndarray] = {}
  intersection_meat = np.zeros((parameter_count, parameter_count), dtype=float)
  residual_sum_squares = 0.0
  observed_rows = 0
  for frame in _iter_frames(
    staged,
    columns,
    monitor=monitor,
    stage=f"regression_{dependent}_cluster_meat",
  ):
    codes, dates = _code_date_arrays(frame)
    values = _regression_values(frame, dependent=dependent, centers=centers)
    valid = _valid_value_rows(dates, values)
    eligible, date_indices = _match_eligible_dates(dates, date_lookup)
    valid &= eligible
    if not valid.any():
      continue
    valid_codes = codes[valid]
    valid_dates = dates[valid]
    demeaned = values[valid] - date_lookup.means[date_indices[valid]]
    y = demeaned[:, 0]
    x = demeaned[:, 1:][:, active_indices]
    residual = y - x @ beta
    scores = x * residual[:, None]
    observed_rows += len(y)
    residual_sum_squares = math.fsum((residual_sum_squares, float(residual @ residual)))
    intersection_meat += scores.T @ scores
    _accumulate_cluster_scores(stock_scores, valid_codes, scores)
    _accumulate_cluster_scores(date_scores, valid_dates, scores)

  if observed_rows != nobs:
    raise ValueError(f"{dependent} 聚类扫描行数不一致: {observed_rows} != {nobs}")
  stock_count = len(stock_scores)
  date_count = len(date_scores)
  residual_degrees = nobs - parameter_count
  if stock_count < 2 or date_count < 2 or residual_degrees <= 0:
    return (
      None,
      residual_sum_squares,
      "聚类数量或自由度不足，无法计算双向聚类标准误",
    )
  stock_meat = _score_meat(stock_scores)
  date_meat = _score_meat(date_scores)
  stock_correction = (stock_count / (stock_count - 1)) * ((nobs - 1) / residual_degrees)
  date_correction = (date_count / (date_count - 1)) * ((nobs - 1) / residual_degrees)
  intersection_count = nobs
  intersection_correction = (
    (intersection_count / (intersection_count - 1)) * ((nobs - 1) / residual_degrees)
    if intersection_count > 1
    else 1.0
  )
  bread = np.linalg.pinv(xtx, rcond=1e-12)
  meat = (
    stock_correction * stock_meat
    + date_correction * date_meat
    - intersection_correction * intersection_meat
  )
  covariance = bread @ meat @ bread
  return (
    (covariance + covariance.T) / 2.0,
    residual_sum_squares,
    None,
  )


def _iter_frames(
  staged: StagedVolumeDataset,
  columns: Sequence[str],
  *,
  monitor: RuntimeMemoryMonitor,
  stage: str,
) -> Iterator[pd.DataFrame]:
  selected = list(dict.fromkeys(columns))
  estimated_increment = max(
    128 * 1024**2,
    estimate_projection_bytes(_PARQUET_BATCH_SIZE, len(selected)) * 6,
  )
  for partition in staged.partitions:
    parquet = pq.ParquetFile(partition)
    iterator = parquet.iter_batches(
      batch_size=_PARQUET_BATCH_SIZE,
      columns=selected,
      use_threads=False,
    )
    while True:
      monitor.guard(stage, estimated_increment_bytes=estimated_increment)
      try:
        batch = next(iterator)
      except StopIteration:
        break
      frame = batch.to_pandas()
      completed = False
      try:
        yield frame
      except BaseException:
        raise
      else:
        completed = True
      finally:
        del frame, batch
      if completed:
        monitor.checkpoint(f"{stage}_checkpoint")


def _regression_values(
  frame: pd.DataFrame,
  *,
  dependent: str,
  centers: dict[str, float],
) -> np.ndarray:
  shock = (
    frame["is_primary_shock_event"]
    .astype("boolean")
    .to_numpy(dtype=float, na_value=np.nan)
  )
  position = _numeric_array(frame["price_position"])
  centered_position = position - centers["price_position"]
  columns = (
    _numeric_array(frame[dependent]),
    shock,
    centered_position,
    shock * centered_position,
    _numeric_array(frame["momentum_20"]) - centers["momentum_20"],
    _numeric_array(frame["volatility_20"]) - centers["volatility_20"],
    _numeric_array(frame["log_average_amount_20"]) - centers["log_average_amount_20"],
  )
  return np.column_stack(columns)


def _valid_value_rows(
  dates: np.ndarray,
  values: np.ndarray,
) -> np.ndarray:
  return (dates != np.iinfo(np.int64).min) & np.isfinite(values).all(axis=1)


def _build_date_lookup(
  aggregates: dict[int, _DateAggregate],
) -> _EligibleDateLookup:
  keys = np.asarray(sorted(aggregates), dtype=np.int64)
  means = np.vstack(
    [aggregates[int(key)].sums / aggregates[int(key)].count for key in keys]
  )
  return _EligibleDateLookup(keys=keys, means=means)


def _match_eligible_dates(
  dates: np.ndarray,
  lookup: _EligibleDateLookup,
) -> tuple[np.ndarray, np.ndarray]:
  indices = np.searchsorted(lookup.keys, dates)
  matched = indices < len(lookup.keys)
  if matched.any():
    matched_indices = np.flatnonzero(matched)
    matched[matched_indices] = (
      lookup.keys[indices[matched_indices]] == dates[matched_indices]
    )
  return matched, indices


def _matrix_rank_from_xtx(xtx: np.ndarray, *, nobs: int) -> int:
  """Apply NumPy's rank(X) tolerance to singular values recovered from X'X."""
  symmetric = (xtx + xtx.T) / 2.0
  eigenvalues = np.linalg.eigvalsh(symmetric)
  singular_values = np.sqrt(np.clip(eigenvalues, 0.0, None))
  if not len(singular_values):
    return 0
  maximum = float(singular_values.max())
  tolerance = maximum * max(nobs, xtx.shape[0]) * np.finfo(float).eps
  return int(np.count_nonzero(singular_values > tolerance))


def _code_date_arrays(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
  code_series = frame["stock_code"].astype("string")
  codes = code_series.to_numpy(dtype=object, na_value=None)
  return codes, _date_array(frame)


def _date_array(frame: pd.DataFrame) -> np.ndarray:
  parsed_dates = pd.to_datetime(frame["event_date"], errors="coerce")
  return parsed_dates.to_numpy(dtype="datetime64[ns]").astype(np.int64)


def _validate_panel_order(
  codes: np.ndarray,
  dates: np.ndarray,
  *,
  previous_code: str | None,
  previous_date: int | None,
) -> tuple[str | None, int | None]:
  if not len(codes):
    return previous_code, previous_date
  if any(code is None for code in codes) or np.any(dates == np.iinfo(np.int64).min):
    raise ValueError("staged regression 面板包含空 stock_code/event_date")
  text_codes = np.asarray(codes, dtype=str)
  if previous_code is not None and previous_date is not None:
    first_code = str(text_codes[0])
    first_date = int(dates[0])
    if first_code < previous_code or (
      first_code == previous_code and first_date < previous_date
    ):
      raise ValueError("staged regression 面板未按 stock_code/event_date 排序")
    if first_code == previous_code and first_date == previous_date:
      raise ValueError("staged regression 面板存在重复 stock_code×event_date")
  if len(text_codes) > 1:
    same_code = text_codes[1:] == text_codes[:-1]
    if np.any(text_codes[1:] < text_codes[:-1]) or np.any(
      same_code & (dates[1:] < dates[:-1])
    ):
      raise ValueError("staged regression 面板未按 stock_code/event_date 排序")
    if np.any(same_code & (dates[1:] == dates[:-1])):
      raise ValueError("staged regression 面板存在重复 stock_code×event_date")
  return str(text_codes[-1]), int(dates[-1])


def _accumulate_cluster_scores(
  target: dict[object, np.ndarray],
  labels: np.ndarray,
  scores: np.ndarray,
) -> None:
  unique_labels, inverse = np.unique(labels, return_inverse=True)
  local = np.zeros((len(unique_labels), scores.shape[1]), dtype=float)
  np.add.at(local, inverse, scores)
  for label, score in zip(unique_labels, local):
    key = label.item() if isinstance(label, np.generic) else label
    existing = target.get(key)
    if existing is None:
      target[key] = score.copy()
    else:
      existing += score


def _score_meat(scores: dict[object, np.ndarray]) -> np.ndarray:
  matrix = np.vstack(tuple(scores.values()))
  return matrix.T @ matrix


def _numeric_array(values: pd.Series) -> np.ndarray:
  result = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float, copy=True)
  result[~np.isfinite(result)] = np.nan
  return result


def _missing_partition_columns(
  staged: StagedVolumeDataset,
  columns: Sequence[str],
) -> list[str]:
  required = set(columns)
  available = (
    set(pq.ParquetFile(staged.partitions[0]).schema_arrow.names)
    if staged.partitions
    else set()
  )
  return sorted(required.difference(available))


def _dependent_column(prefix: str, horizon: int, config: StudyConfig) -> str:
  if config.outcomes.include_benchmark_excess:
    return f"csi300_excess_{prefix}_h{horizon}"
  if config.outcomes.include_cross_section_excess:
    return f"market_excess_{prefix}_h{horizon}"
  return f"{prefix}_return_h{horizon}"


def _finite(value: object) -> float | None:
  if value is None:
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if np.isfinite(number) else None
