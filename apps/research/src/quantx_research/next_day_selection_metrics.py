"""Probability and daily-equal ranking metrics for model release evidence."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
  average_precision_score,
  log_loss,
  roc_auc_score,
)


def brier_score(labels: np.ndarray, probabilities: np.ndarray) -> float:
  return float(np.mean((probabilities - labels) ** 2))


def calibration_bins(
  labels: np.ndarray,
  probabilities: np.ndarray,
  *,
  bins: int,
) -> list[dict[str, Any]]:
  edges = np.linspace(0.0, 1.0, bins + 1)
  indices = np.minimum(
    np.searchsorted(edges, probabilities, side="right") - 1, bins - 1
  )
  indices = np.maximum(indices, 0)
  rows: list[dict[str, Any]] = []
  for index in range(bins):
    selected = indices == index
    count = int(selected.sum())
    rows.append(
      {
        "index": index,
        "lower": float(edges[index]),
        "upper": float(edges[index + 1]),
        "sample_count": count,
        "mean_probability": float(probabilities[selected].mean()) if count else None,
        "realized_rate": float(labels[selected].mean()) if count else None,
      }
    )
  return rows


def expected_calibration_error(rows: list[dict[str, Any]]) -> float:
  total = sum(int(row["sample_count"]) for row in rows)
  if total <= 0:
    return float("nan")
  return float(
    sum(
      int(row["sample_count"])
      * abs(float(row["mean_probability"]) - float(row["realized_rate"]))
      for row in rows
      if row["sample_count"]
    )
    / total
  )


def evaluate_probability(
  labels: np.ndarray,
  probabilities: np.ndarray,
  *,
  bins: int,
) -> dict[str, Any]:
  y = np.asarray(labels, dtype=float)
  p = np.clip(np.asarray(probabilities, dtype=float), 1e-8, 1 - 1e-8)
  if y.ndim != 1 or p.shape != y.shape or not len(y):
    raise ValueError("概率评估输入维度无效")
  prevalence = float(y.mean())
  brier = brier_score(y, p)
  baseline = brier_score(y, np.full(len(y), prevalence))
  rows = calibration_bins(y, p, bins=bins)
  has_two_classes = len(np.unique(y)) == 2
  return {
    "sample_count": int(len(y)),
    "positive_count": int(y.sum()),
    "prevalence": prevalence,
    "brier": brier,
    "baseline_brier": baseline,
    "brier_skill": 1.0 - brier / baseline if baseline > 0 else None,
    "log_loss": float(log_loss(y, p, labels=[0.0, 1.0])),
    "ece": expected_calibration_error(rows),
    "roc_auc": float(roc_auc_score(y, p)) if has_two_classes else None,
    "pr_auc": float(average_precision_score(y, p)) if has_two_classes else None,
    "calibration_bins": rows,
  }


def _daily_top(panel: pd.DataFrame, size: int) -> pd.DataFrame:
  ordered = panel.sort_values(
    ["event_date", "probability", "stock_code"],
    ascending=[True, False, True],
    kind="mergesort",
  )
  return ordered.groupby("event_date", sort=True, group_keys=False).head(size)


def _daily_summary(panel: pd.DataFrame, size: int) -> pd.DataFrame:
  top = _daily_top(panel, size)
  top_daily = top.groupby("event_date", sort=True).agg(
    top_up_rate=("label", "mean"),
    top_mean_return=("next_open_to_close_return", "mean"),
    selected=("stock_code", "size"),
  )
  baseline = panel.groupby("event_date", sort=True).agg(
    baseline_up_rate=("label", "mean"),
    baseline_mean_return=("next_open_to_close_return", "mean"),
  )
  return top_daily.join(baseline, how="inner").assign(
    up_rate_lift=lambda frame: frame.top_up_rate - frame.baseline_up_rate,
    mean_return_lift=lambda frame: frame.top_mean_return - frame.baseline_mean_return,
  )


def moving_block_confidence_interval(
  values: np.ndarray,
  *,
  samples: int,
  block_length: int,
  seed: int,
) -> tuple[float | None, float | None]:
  clean = np.asarray(values, dtype=float)
  clean = clean[np.isfinite(clean)]
  if len(clean) < 2 or samples <= 0:
    return None, None
  rng = np.random.default_rng(seed)
  block = max(1, min(int(block_length), len(clean)))
  needed = math.ceil(len(clean) / block)
  starts = np.arange(len(clean))
  estimates = np.empty(samples, dtype=float)
  for iteration in range(samples):
    chosen = rng.choice(starts, size=needed, replace=True)
    indices = np.concatenate(
      [(np.arange(start, start + block) % len(clean)) for start in chosen]
    )[: len(clean)]
    estimates[iteration] = float(clean[indices].mean())
  lower, upper = np.quantile(estimates, [0.025, 0.975])
  return float(lower), float(upper)


def evaluate_ranking(
  panel: pd.DataFrame,
  *,
  bootstrap_samples: int,
  seed: int,
) -> dict[str, Any]:
  required = {
    "event_date",
    "stock_code",
    "label",
    "next_open_to_close_return",
    "probability",
  }
  if required - set(panel):
    raise ValueError("排序评估缺少必要字段")
  result: dict[str, Any] = {}
  for size in (20, 50):
    daily = _daily_summary(panel, size)
    low, high = moving_block_confidence_interval(
      daily["up_rate_lift"].to_numpy(),
      samples=bootstrap_samples,
      block_length=5,
      seed=seed + size,
    )
    result[f"top_{size}"] = {
      "date_count": int(len(daily)),
      "precision": float(daily["top_up_rate"].mean()) if len(daily) else None,
      "mean_return": float(daily["top_mean_return"].mean()) if len(daily) else None,
      "baseline_up_rate": float(daily["baseline_up_rate"].mean())
      if len(daily)
      else None,
      "up_rate_lift": float(daily["up_rate_lift"].mean()) if len(daily) else None,
      "up_rate_lift_ci_low": low,
      "up_rate_lift_ci_high": high,
      "mean_return_lift": float(daily["mean_return_lift"].mean())
      if len(daily)
      else None,
    }
  return result


def annual_stability(panel: pd.DataFrame, *, bins: int) -> list[dict[str, Any]]:
  rows: list[dict[str, Any]] = []
  years = pd.to_datetime(panel["event_date"]).dt.year
  for year, yearly in panel.groupby(years, sort=True):
    probability = evaluate_probability(
      yearly["label"].to_numpy(), yearly["probability"].to_numpy(), bins=bins
    )
    ranking = evaluate_ranking(yearly, bootstrap_samples=0, seed=int(year))
    rows.append(
      {
        "year": int(year),
        "sample_count": probability["sample_count"],
        "brier": probability["brier"],
        "brier_skill": probability["brier_skill"],
        "ece": probability["ece"],
        "top20_up_rate_lift": ranking["top_20"]["up_rate_lift"],
      }
    )
  return rows
