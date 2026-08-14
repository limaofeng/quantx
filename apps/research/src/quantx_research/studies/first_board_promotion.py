"""Walk-forward research for the first-board promotion strategy.

The study deliberately accepts an already point-in-time-correct feature panel.  It
then enforces the as-of contract again before fitting separate, interpretable
models for the main and growth-board universes.  Production never imports this
module; its only promotion output is an evidence document for operator review.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np
import pandas as pd

DEFAULT_FEATURE_COLUMNS = (
  "price_position_252d",
  "return_5d_pct",
  "return_20d_pct",
  "ma20_deviation_pct",
  "volatility_20d_pct",
  "recent_limit_up_count",
  "free_float_log",
  "volume_acceleration",
  "turnover_rate_pct",
  "order_book_strength",
  "sector_promotion_rate",
  "market_break_rate",
)
SUPPORTED_SEGMENTS = frozenset({"MAIN", "GROWTH"})


@dataclass(frozen=True)
class FirstBoardResearchConfig:
  model_version: str = "first-board-promotion-v2-shadow-1"
  exit_policy_version: str = "first-board-exit-v2-shadow-1"
  min_train_trading_days: int = 60
  refit_interval_days: int = 5
  ridge_penalty: float = 1.0
  logistic_iterations: int = 300
  logistic_learning_rate: float = 0.08
  bootstrap_samples: int = 2_000
  bootstrap_seed: int = 20260814
  confidence_level: float = 0.95
  max_cvar95_loss_pct: float = 7.5
  required_shadow_days: int = 20
  required_samples_per_segment: int = 100


@dataclass(frozen=True)
class PromotionComparison:
  baseline: str
  mean_difference_pct: float
  confidence_interval_lower_pct: float
  confidence_interval_upper_pct: float
  trading_days: int


@dataclass(frozen=True)
class FirstBoardResearchResult:
  model_version: str
  exit_policy_version: str
  predictions: pd.DataFrame
  comparisons: tuple[PromotionComparison, ...]
  sample_trading_days: int
  main_board_eligible_samples: int
  growth_board_eligible_samples: int
  observed_cvar95_loss_pct: float | None
  tail_loss_budget_passed: bool
  historical_rules_complete: bool
  release_ready_for_paper: bool
  warnings: tuple[str, ...]

  def release_evidence(self) -> dict[str, object]:
    """Return the non-authoritative evidence payload used by an approver."""
    v1 = next(
      (item for item in self.comparisons if item.baseline == "V1_RADAR"),
      None,
    )
    return {
      "model_version": self.model_version,
      "exit_policy_version": self.exit_policy_version,
      "stage": "SHADOW",
      "sample_trading_days": self.sample_trading_days,
      "main_board_eligible_samples": self.main_board_eligible_samples,
      "growth_board_eligible_samples": self.growth_board_eligible_samples,
      "bootstrap_ci_lower_pct": (
        v1.confidence_interval_lower_pct if v1 is not None else None
      ),
      "tail_loss_budget_passed": self.tail_loss_budget_passed,
      "historical_rules_complete": self.historical_rules_complete,
      "release_ready_for_paper": self.release_ready_for_paper,
      "comparisons": [asdict(item) for item in self.comparisons],
      "warnings": list(self.warnings),
    }


class FirstBoardPromotionStudy:
  """Fit point-in-time walk-forward models and evaluate V2 against controls."""

  required_columns = (
    "instrument_code",
    "trade_date",
    "segment",
    "signal_at",
    "feature_as_of",
    "outcome_at",
    "eligible",
    "first_board_close",
    "next_day_limit_touch",
    "next_day_limit_seal",
    "net_return_pct",
    "v1_net_return_pct",
    "all_near_limit_net_return_pct",
    "historical_rules_complete",
  )

  def __init__(
    self,
    config: FirstBoardResearchConfig | None = None,
    *,
    feature_columns: Sequence[str] = DEFAULT_FEATURE_COLUMNS,
  ) -> None:
    self.config = config or FirstBoardResearchConfig()
    self.feature_columns = tuple(feature_columns)

  def run(self, panel: pd.DataFrame) -> FirstBoardResearchResult:
    sample = self._validated_sample(panel)
    predictions = self._walk_forward(sample)
    comparisons = self._compare_controls(predictions)

    selected = predictions[predictions["selected_v2"]]
    observed_cvar = _cvar95_loss(selected["net_return_pct"].to_numpy())
    sample_days = int(selected["trade_date"].nunique())
    main_samples = int((selected["segment"] == "MAIN").sum())
    growth_samples = int((selected["segment"] == "GROWTH").sum())
    rules_complete = bool(sample["historical_rules_complete"].all())
    v1 = next((item for item in comparisons if item.baseline == "V1_RADAR"), None)
    tail_passed = (
      observed_cvar is not None
      and observed_cvar <= self.config.max_cvar95_loss_pct
    )
    release_ready = bool(
      v1 is not None
      and v1.confidence_interval_lower_pct > 0
      and sample_days >= self.config.required_shadow_days
      and main_samples >= self.config.required_samples_per_segment
      and growth_samples >= self.config.required_samples_per_segment
      and tail_passed
      and rules_complete
    )

    warnings = [
      "全部概率与收益预测均为按交易日滚动的样本外结果。",
      "研究产物不能直接改变生产模型阶段，仍需人工复核并写入发布门禁。",
    ]
    if not rules_complete:
      warnings.append(
        "历史 ST、上市、退市或涨跌停规则覆盖不完整，不得宣称 V2 优于 V1。"
      )
    if predictions.empty:
      warnings.append("训练窗口不足，未形成可评估的样本外预测。")

    return FirstBoardResearchResult(
      model_version=self.config.model_version,
      exit_policy_version=self.config.exit_policy_version,
      predictions=predictions,
      comparisons=comparisons,
      sample_trading_days=sample_days,
      main_board_eligible_samples=main_samples,
      growth_board_eligible_samples=growth_samples,
      observed_cvar95_loss_pct=observed_cvar,
      tail_loss_budget_passed=tail_passed,
      historical_rules_complete=rules_complete,
      release_ready_for_paper=release_ready,
      warnings=tuple(warnings),
    )

  def _validated_sample(self, panel: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(
      set(self.required_columns + self.feature_columns) - set(panel.columns)
    )
    if missing:
      raise ValueError(f"first-board research panel missing columns: {missing}")

    sample = panel.copy()
    for column in ("trade_date", "signal_at", "feature_as_of", "outcome_at"):
      sample[column] = pd.to_datetime(sample[column], errors="raise")
    sample["trade_date"] = sample["trade_date"].dt.normalize()
    sample["segment"] = sample["segment"].astype(str).str.upper()

    unsupported = sorted(set(sample["segment"]) - SUPPORTED_SEGMENTS)
    if unsupported:
      raise ValueError(f"unsupported first-board segments: {unsupported}")
    if bool((sample["feature_as_of"] > sample["signal_at"]).any()):
      raise ValueError("future data detected: feature_as_of is after signal_at")
    if bool((sample["outcome_at"] <= sample["signal_at"]).any()):
      raise ValueError("future outcome contract invalid: outcome_at must follow signal_at")
    if bool((sample["outcome_at"].dt.normalize() <= sample["trade_date"]).any()):
      raise ValueError("T+1 outcome must be observed after the signal trade date")

    numeric = self.feature_columns + (
      "first_board_close",
      "next_day_limit_touch",
      "next_day_limit_seal",
      "net_return_pct",
      "v1_net_return_pct",
      "all_near_limit_net_return_pct",
    )
    for column in numeric:
      sample[column] = pd.to_numeric(sample[column], errors="coerce")
    sample = sample.dropna(subset=list(numeric)).copy()
    sample = sample.sort_values(
      ["trade_date", "segment", "instrument_code"], kind="stable"
    ).reset_index(drop=True)
    return sample

  def _walk_forward(self, sample: pd.DataFrame) -> pd.DataFrame:
    output: list[pd.DataFrame] = []
    for segment in sorted(SUPPORTED_SEGMENTS):
      segment_sample = sample[sample["segment"] == segment]
      dates = tuple(segment_sample["trade_date"].drop_duplicates().sort_values())
      cached_models: tuple[object, ...] | None = None
      cached_train_end: pd.Timestamp | None = None
      for position, test_date in enumerate(dates):
        train_dates = dates[:position]
        if len(train_dates) < self.config.min_train_trading_days:
          continue
        should_refit = (
          cached_models is None
          or (position - self.config.min_train_trading_days)
          % self.config.refit_interval_days
          == 0
        )
        training = segment_sample[segment_sample["trade_date"] < test_date]
        if should_refit:
          cached_models = self._fit_models(training)
          cached_train_end = pd.Timestamp(training["trade_date"].max())
        assert cached_models is not None
        assert cached_train_end is not None and cached_train_end < test_date

        test = segment_sample[segment_sample["trade_date"] == test_date].copy()
        scaler, close_model, touch_model, seal_model, return_model, cvar = cached_models
        matrix = _transform(test.loc[:, self.feature_columns].to_numpy(), scaler)
        close_probability = _predict_logistic(matrix, close_model)
        touch_probability = _predict_logistic(matrix, touch_model)
        seal_probability = _predict_logistic(matrix, seal_model)
        expected_return = _predict_linear(matrix, return_model)
        test["first_board_close_probability"] = close_probability
        test["next_day_limit_touch_probability"] = touch_probability
        test["next_day_limit_seal_probability"] = seal_probability
        test["expected_net_return_pct"] = expected_return
        test["predicted_cvar95_loss_pct"] = cvar
        test["rank_score"] = (
          expected_return * close_probability * touch_probability
          - 0.10 * cvar
        )
        test["selected_v2"] = (
          test["eligible"].astype(bool)
          & (test["rank_score"] > 0)
          & (close_probability >= 0.50)
          & (touch_probability >= 0.25)
        )
        test["model_train_end_date"] = cached_train_end
        output.append(test)
    if not output:
      return pd.DataFrame(
        columns=list(sample.columns)
        + [
          "first_board_close_probability",
          "next_day_limit_touch_probability",
          "next_day_limit_seal_probability",
          "expected_net_return_pct",
          "predicted_cvar95_loss_pct",
          "rank_score",
          "selected_v2",
          "model_train_end_date",
        ]
      )
    return pd.concat(output, ignore_index=True)

  def _fit_models(self, training: pd.DataFrame) -> tuple[object, ...]:
    raw = training.loc[:, self.feature_columns].to_numpy(dtype=float)
    mean = raw.mean(axis=0)
    scale = raw.std(axis=0)
    scale[scale < 1e-9] = 1.0
    scaler = (mean, scale)
    matrix = _transform(raw, scaler)
    close_model = _fit_logistic(
      matrix,
      training["first_board_close"].to_numpy(dtype=float),
      self.config,
    )
    touch_model = _fit_logistic(
      matrix,
      training["next_day_limit_touch"].to_numpy(dtype=float),
      self.config,
    )
    seal_model = _fit_logistic(
      matrix,
      training["next_day_limit_seal"].to_numpy(dtype=float),
      self.config,
    )
    return_model = _fit_linear(
      matrix,
      training["net_return_pct"].to_numpy(dtype=float),
      self.config.ridge_penalty,
    )
    cvar = _cvar95_loss(training["net_return_pct"].to_numpy(dtype=float)) or 0.0
    return scaler, close_model, touch_model, seal_model, return_model, cvar

  def _compare_controls(
    self, predictions: pd.DataFrame
  ) -> tuple[PromotionComparison, ...]:
    if predictions.empty:
      return ()
    selected = predictions[predictions["selected_v2"]]
    if selected.empty:
      return ()

    comparisons = []
    for baseline, column in (
      ("V1_RADAR", "v1_net_return_pct"),
      ("ALL_NEAR_LIMIT", "all_near_limit_net_return_pct"),
    ):
      daily = selected.groupby("trade_date", sort=True)[
        ["net_return_pct", column]
      ].mean()
      differences = (daily["net_return_pct"] - daily[column]).to_numpy()
      lower, upper = _date_block_bootstrap_interval(differences, self.config)
      comparisons.append(
        PromotionComparison(
          baseline=baseline,
          mean_difference_pct=float(differences.mean()),
          confidence_interval_lower_pct=lower,
          confidence_interval_upper_pct=upper,
          trading_days=len(daily),
        )
      )
    return tuple(comparisons)


def _transform(
  matrix: np.ndarray, scaler: tuple[np.ndarray, np.ndarray]
) -> np.ndarray:
  mean, scale = scaler
  standardized = (matrix.astype(float) - mean) / scale
  return np.column_stack([np.ones(len(standardized)), standardized])


def _fit_logistic(
  matrix: np.ndarray,
  target: np.ndarray,
  config: FirstBoardResearchConfig,
) -> tuple[str, np.ndarray | float]:
  prevalence = float(np.clip(target.mean(), 1e-4, 1 - 1e-4))
  if np.unique(target).size < 2:
    return "constant", prevalence
  weights = np.zeros(matrix.shape[1], dtype=float)
  for _ in range(config.logistic_iterations):
    probability = 1.0 / (1.0 + np.exp(-np.clip(matrix @ weights, -30, 30)))
    gradient = matrix.T @ (probability - target) / len(target)
    gradient[1:] += config.ridge_penalty * weights[1:] / len(target)
    weights -= config.logistic_learning_rate * gradient
  return "weights", weights


def _predict_logistic(
  matrix: np.ndarray, model: tuple[str, np.ndarray | float]
) -> np.ndarray:
  kind, value = model
  if kind == "constant":
    return np.full(len(matrix), float(value))
  logits = matrix @ np.asarray(value)
  return 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))


def _fit_linear(matrix: np.ndarray, target: np.ndarray, penalty: float) -> np.ndarray:
  identity = np.eye(matrix.shape[1])
  identity[0, 0] = 0.0
  return np.linalg.pinv(matrix.T @ matrix + penalty * identity) @ matrix.T @ target


def _predict_linear(matrix: np.ndarray, weights: np.ndarray) -> np.ndarray:
  return matrix @ weights


def _cvar95_loss(returns: np.ndarray) -> float | None:
  if returns.size == 0:
    return None
  losses = -np.asarray(returns, dtype=float)
  threshold = float(np.quantile(losses, 0.95))
  tail = losses[losses >= threshold]
  return float(max(0.0, tail.mean()))


def _date_block_bootstrap_interval(
  daily_differences: np.ndarray,
  config: FirstBoardResearchConfig,
) -> tuple[float, float]:
  if daily_differences.size == 0:
    return 0.0, 0.0
  rng = np.random.default_rng(config.bootstrap_seed)
  samples = rng.choice(
    daily_differences,
    size=(config.bootstrap_samples, len(daily_differences)),
    replace=True,
  ).mean(axis=1)
  alpha = (1.0 - config.confidence_level) / 2.0
  return float(np.quantile(samples, alpha)), float(np.quantile(samples, 1.0 - alpha))
