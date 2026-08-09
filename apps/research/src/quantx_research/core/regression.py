"""Panel regression with date fixed effects and two-way clustered covariance."""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from quantx_research.core.config import StudyConfig
from quantx_research.core.models import RegressionCoefficient, RegressionResult

_FEATURE_TERMS = (
  "shock_indicator",
  "centered_price_position",
  "shock_position_interaction",
  "momentum_20",
  "volatility_20",
  "log_average_amount_20",
)
_FOCAL_INTERACTION = "shock_position_interaction"


def run_panel_regressions(
  events: pd.DataFrame,
  config: StudyConfig,
  *,
  copy_input: bool = True,
) -> list[RegressionResult]:
  """Estimate configured horizons after absorbing event-date fixed effects.

  ``copy_input=False`` is reserved for a disposable, already-minimal staging
  projection and avoids one full-panel copy.
  """
  if not config.statistics.run_regression:
    return []
  if "is_primary_shock_event" not in events:
    raise ValueError(
      "full regression sample is missing cooldown-adjusted "
      "is_primary_shock_event identity"
    )
  projection = [
    column for column in regression_input_columns(config) if column in events.columns
  ]
  if not copy_input and projection == list(events.columns):
    frame = events
  else:
    frame = events.loc[:, projection].copy()
  shock_identity = frame["is_primary_shock_event"].astype("boolean")
  frame["shock_indicator"] = shock_identity.astype(float)
  frame["centered_price_position"] = _center(frame["price_position"])
  for control in ("momentum_20", "volatility_20", "log_average_amount_20"):
    if control in frame:
      frame[control] = _center(frame[control])
  frame["shock_position_interaction"] = (
    frame["shock_indicator"] * frame["centered_price_position"]
  )

  results: list[RegressionResult] = []
  for horizon in config.outcomes.horizons:
    if config.outcomes.include_close_response:
      dependent = _dependent_column("close", horizon, config)
      results.append(
        _fit_one(
          frame,
          dependent,
          return_kind="close_response",
          horizon=horizon,
          confidence_level=config.statistics.confidence_level,
          minimum_observations=max(
            config.statistics.minimum_cell_samples, len(_FEATURE_TERMS) + 2
          ),
          minimum_date_clusters=config.statistics.minimum_inference_dates,
        )
      )
    if config.outcomes.include_next_open_return:
      dependent = _dependent_column("next_open", horizon, config)
      results.append(
        _fit_one(
          frame,
          dependent,
          return_kind="next_open",
          horizon=horizon,
          confidence_level=config.statistics.confidence_level,
          minimum_observations=max(
            config.statistics.minimum_cell_samples, len(_FEATURE_TERMS) + 2
          ),
          minimum_date_clusters=config.statistics.minimum_inference_dates,
        )
      )
  return _apply_interaction_fdr(
    results,
    alpha=config.statistics.fdr_alpha,
  )


def regression_input_columns(config: StudyConfig) -> tuple[str, ...]:
  """Return the minimal full-sample projection needed by all regressions."""
  columns = [
    "stock_code",
    "event_date",
    "is_primary_shock_event",
    "price_position",
    "momentum_20",
    "volatility_20",
    "log_average_amount_20",
  ]
  for horizon in config.outcomes.horizons:
    if config.outcomes.include_close_response:
      columns.append(_dependent_column("close", horizon, config))
    if config.outcomes.include_next_open_return:
      columns.append(_dependent_column("next_open", horizon, config))
  return tuple(dict.fromkeys(columns))


def _dependent_column(
  prefix: str,
  horizon: int,
  config: StudyConfig,
) -> str:
  # This is a preregistered, configuration-only decision. Never inspect
  # realized coverage, p-values, or fit quality to switch the dependent series.
  if config.outcomes.include_benchmark_excess:
    return f"csi300_excess_{prefix}_h{horizon}"
  if config.outcomes.include_cross_section_excess:
    return f"market_excess_{prefix}_h{horizon}"
  return f"{prefix}_return_h{horizon}"


def _fit_one(
  frame: pd.DataFrame,
  dependent: str,
  *,
  return_kind: str,
  horizon: int,
  confidence_level: float,
  minimum_observations: int,
  minimum_date_clusters: int,
) -> RegressionResult:
  warnings: list[str] = []
  required = [dependent, "stock_code", "event_date", *_FEATURE_TERMS]
  missing = sorted(set(required).difference(frame.columns))
  if missing:
    return RegressionResult(
      return_kind=return_kind,
      horizon=horizon,
      dependent_variable=dependent,
      nobs=0,
      r_squared=None,
      warnings=[f"回归缺少字段: {', '.join(missing)}"],
    )

  sample = frame[required].copy()
  for column in (dependent, *_FEATURE_TERMS):
    sample[column] = pd.to_numeric(sample[column], errors="coerce")
    sample.loc[~np.isfinite(sample[column]), column] = np.nan
  sample = sample.dropna()
  date_sizes = sample.groupby("event_date", observed=True)["event_date"].transform(
    "size"
  )
  singleton_rows = int((date_sizes < 2).sum())
  if singleton_rows:
    sample = sample[date_sizes >= 2].copy()
    warnings.append(f"{singleton_rows} 个日期单例样本被日期固定效应完全吸收，已移除")
  nobs = len(sample)
  if nobs < minimum_observations:
    return RegressionResult(
      return_kind=return_kind,
      horizon=horizon,
      dependent_variable=dependent,
      nobs=nobs,
      r_squared=None,
      warnings=[f"有效样本 {nobs} 少于回归最低要求 {minimum_observations}，未估计"],
    )
  date_cluster_count = int(sample["event_date"].nunique())
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

  absorbed = sample[[dependent, *_FEATURE_TERMS]].astype(float)
  date_means = absorbed.groupby(sample["event_date"], observed=True).transform("mean")
  absorbed = absorbed - date_means
  y = absorbed[dependent].to_numpy(dtype=float)

  active_terms = [
    term for term in _FEATURE_TERMS if float(absorbed[term].std(ddof=0)) > 1e-12
  ]
  dropped_terms = [term for term in _FEATURE_TERMS if term not in active_terms]
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

  x = absorbed[active_terms].to_numpy(dtype=float)
  matrix_rank = int(np.linalg.matrix_rank(x))
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
  beta = np.linalg.pinv(x.T @ x, rcond=1e-12) @ (x.T @ y)
  residual = y - x @ beta
  sst = float(y @ y)
  r_squared = 1.0 - float(residual @ residual) / sst if sst > 0 else None

  stock_labels = sample["stock_code"].astype(str).to_numpy()
  date_labels = sample["event_date"].astype(str).to_numpy()
  intersection_labels = np.char.add(np.char.add(stock_labels, "\x1f"), date_labels)
  covariance = _two_way_cluster_covariance(
    x,
    residual,
    stock_labels,
    date_labels,
    intersection_labels,
  )
  if covariance is None:
    warnings.append("聚类数量或自由度不足，无法计算双向聚类标准误")

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


def _apply_interaction_fdr(
  results: list[RegressionResult],
  *,
  alpha: float,
) -> list[RegressionResult]:
  eligible: list[tuple[int, int, float]] = []
  for result_index, result in enumerate(results):
    for coefficient_index, coefficient in enumerate(result.coefficients):
      if coefficient.term == _FOCAL_INTERACTION and coefficient.p_value is not None:
        eligible.append((result_index, coefficient_index, float(coefficient.p_value)))
  if not eligible:
    return results
  eligible.sort(key=lambda item: item[2])
  count = len(eligible)
  q_values = [1.0] * count
  running = 1.0
  for position in range(count - 1, -1, -1):
    running = min(running, eligible[position][2] * count / (position + 1))
    q_values[position] = min(1.0, running)
  updates = {
    (result_index, coefficient_index): q_value
    for (result_index, coefficient_index, _), q_value in zip(eligible, q_values)
  }
  adjusted: list[RegressionResult] = []
  for result_index, result in enumerate(results):
    coefficients = [
      coefficient.model_copy(
        update={
          "q_value": updates[(result_index, coefficient_index)],
          "significant": (updates[(result_index, coefficient_index)] <= alpha),
        }
      )
      if (result_index, coefficient_index) in updates
      else coefficient
      for coefficient_index, coefficient in enumerate(result.coefficients)
    ]
    adjusted.append(result.model_copy(update={"coefficients": coefficients}))
  return adjusted


def _two_way_cluster_covariance(
  x: np.ndarray,
  residual: np.ndarray,
  first_cluster: np.ndarray,
  second_cluster: np.ndarray,
  intersection_cluster: np.ndarray,
) -> np.ndarray | None:
  nobs, parameter_count = x.shape
  first_count = len(np.unique(first_cluster))
  second_count = len(np.unique(second_cluster))
  residual_degrees = nobs - parameter_count
  if first_count < 2 or second_count < 2 or residual_degrees <= 0:
    return None

  bread = np.linalg.pinv(x.T @ x, rcond=1e-12)
  first_meat = _cluster_meat(x, residual, first_cluster)
  second_meat = _cluster_meat(x, residual, second_cluster)
  intersection_meat = _cluster_meat(x, residual, intersection_cluster)
  first_correction = (first_count / (first_count - 1)) * ((nobs - 1) / residual_degrees)
  second_correction = (second_count / (second_count - 1)) * (
    (nobs - 1) / residual_degrees
  )
  intersection_count = len(np.unique(intersection_cluster))
  intersection_correction = (
    (intersection_count / (intersection_count - 1)) * ((nobs - 1) / residual_degrees)
    if intersection_count > 1
    else 1.0
  )
  meat = (
    first_correction * first_meat
    + second_correction * second_meat
    - intersection_correction * intersection_meat
  )
  covariance = bread @ meat @ bread
  return (covariance + covariance.T) / 2.0


def _cluster_meat(
  x: np.ndarray, residual: np.ndarray, labels: np.ndarray
) -> np.ndarray:
  _, inverse = np.unique(labels, return_inverse=True)
  scores = x * residual[:, None]
  cluster_scores = np.zeros((int(inverse.max()) + 1, x.shape[1]), dtype=float)
  np.add.at(cluster_scores, inverse, scores)
  return cluster_scores.T @ cluster_scores


def _finite(value: object) -> float | None:
  if value is None:
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if np.isfinite(number) else None


def _center(values: pd.Series) -> pd.Series:
  numeric = pd.to_numeric(values, errors="coerce").astype(float)
  numeric.loc[~np.isfinite(numeric)] = np.nan
  center = numeric.mean()
  return numeric - center if np.isfinite(center) else numeric
