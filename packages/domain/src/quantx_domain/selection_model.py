"""Safe, dependency-light runtime primitives for selection model artifacts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

CALIBRATOR_VERSION = "next-day-selection-calibrator-v1"
MODEL_ARTIFACT_SCHEMA_VERSION = 1


def sigmoid(values: np.ndarray) -> np.ndarray:
  clipped = np.clip(np.asarray(values, dtype=float), -40.0, 40.0)
  return 1.0 / (1.0 + np.exp(-clipped))


def predict_logistic(
  matrix: np.ndarray,
  artifact: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
  coefficients = np.asarray(artifact.get("coefficients"), dtype=float)
  intercept = float(artifact.get("intercept"))
  values = np.asarray(matrix, dtype=float)
  if values.ndim != 2 or coefficients.ndim != 1:
    raise ValueError("Logistic 模型维度无效")
  if values.shape[1] != coefficients.shape[0]:
    raise ValueError("Logistic 模型特征数不一致")
  raw = values @ coefficients + intercept
  return raw, sigmoid(raw)


def apply_calibrator(
  raw_scores: Sequence[float] | np.ndarray,
  probabilities: Sequence[float] | np.ndarray,
  calibrator: Mapping[str, Any],
) -> np.ndarray:
  kind = str(calibrator.get("kind", ""))
  raw = np.asarray(raw_scores, dtype=float)
  uncalibrated = np.asarray(probabilities, dtype=float)
  if raw.shape != uncalibrated.shape:
    raise ValueError("校准输入维度不一致")
  if kind == "platt":
    slope = float(calibrator.get("slope"))
    intercept = float(calibrator.get("intercept"))
    return sigmoid(raw * slope + intercept)
  if kind == "isotonic":
    x = np.asarray(calibrator.get("x_thresholds"), dtype=float)
    y = np.asarray(calibrator.get("y_thresholds"), dtype=float)
    if x.ndim != 1 or y.ndim != 1 or len(x) != len(y) or len(x) < 2:
      raise ValueError("Isotonic 校准器阈值无效")
    if not np.all(np.diff(x) >= 0) or not np.all(np.diff(y) >= 0):
      raise ValueError("Isotonic 校准器必须单调")
    return np.interp(uncalibrated, x, y, left=y[0], right=y[-1])
  raise ValueError(f"未知校准器: {kind}")


def geometric_confidence(
  *,
  factor_completeness: float,
  ood_fit: float,
  logistic_probability: float,
  lightgbm_probability: float,
  calibration_bin_samples: int,
) -> float:
  components = (
    _unit(factor_completeness),
    _unit(ood_fit),
    _unit(1.0 - abs(logistic_probability - lightgbm_probability)),
    _unit(calibration_bin_samples / 1000.0),
  )
  if any(value <= 0 for value in components):
    return 0.0
  return float(math.prod(components) ** (1.0 / len(components)))


def _unit(value: float) -> float:
  if not math.isfinite(float(value)):
    return 0.0
  return min(1.0, max(0.0, float(value)))
