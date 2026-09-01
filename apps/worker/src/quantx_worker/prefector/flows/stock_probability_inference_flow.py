"""Daily read-only inference after a certified indicator snapshot."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from prefect import flow, get_run_logger
from quantx_domain.indicators import INDICATOR_DEFINITIONS, INDICATOR_VERSION
from quantx_domain.selection_factors import (
  FACTOR_SET_HASH,
  build_selection_factor_frame,
  selection_factor_completeness,
  selection_feature_columns,
)
from quantx_domain.selection_model import (
  apply_calibrator,
  geometric_confidence,
  predict_logistic,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.indicator_snapshot import IndicatorSnapshot
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.repositories.daily_signal_run_repository import (
  DailySignalRunRepository,
)
from quantx_infrastructure.repositories.stock_selection_repository import (
  StockSelectionRepository,
)
from quantx_infrastructure.services.stock_selection_artifacts import (
  SelectionArtifactBundle,
  file_sha256,
  load_selection_artifact,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper
from sqlalchemy import select

_ST_NAME = re.compile(r"(?:\*?ST|退)", re.IGNORECASE)
_ORDINARY_A_SHARE = re.compile(
  r"^(?:(?:600|601|603|605|688|689)\d{3}\.SH|"
  r"(?:000|001|002|003|300|301)\d{3}\.SZ)$"
)


def _workspace_root() -> Path:
  configured = os.environ.get("QUANTX_ROOT")
  return (
    Path(configured).expanduser().resolve()
    if configured
    else Path(__file__).resolve().parents[6]
  )


def _preprocess(frame: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
  columns = list(artifact["feature_columns"])
  if columns != list(selection_feature_columns()):
    raise ValueError("推理预处理特征顺序与当前模型因子契约不一致")
  values = frame.reindex(columns=columns).apply(pd.to_numeric, errors="coerce")
  imputation = pd.Series(artifact["imputation_values"], dtype=float)
  means = pd.Series(artifact["means"], dtype=float)
  scales = pd.Series(artifact["scales"], dtype=float)
  matrix = values.fillna(imputation).sub(means).div(scales).to_numpy(dtype=float)
  if not np.isfinite(matrix).all():
    raise ValueError("推理预处理产生非有限值")
  return matrix


def _ood_fit(frame: pd.DataFrame, preprocessor: dict[str, Any]) -> np.ndarray:
  columns = list(preprocessor["feature_columns"])
  values = frame.reindex(columns=columns).apply(pd.to_numeric, errors="coerce")
  lower = pd.Series(preprocessor["ood_lower"], dtype=float)
  upper = pd.Series(preprocessor["ood_upper"], dtype=float)
  observed = values.notna()
  within = values.ge(lower, axis=1) & values.le(upper, axis=1)
  observed_count = observed.sum(axis=1).replace(0, np.nan)
  return within.where(observed).sum(axis=1).div(observed_count).fillna(0.0).to_numpy()


def _calibration_bucket(
  bundle: SelectionArtifactBundle, probability: float
) -> dict[str, Any]:
  rows = (
    bundle.metrics.get("frozen_test", {})
    .get("probability", {})
    .get("calibration_bins", [])
  )
  for row in rows:
    if not isinstance(row, dict):
      continue
    lower = float(row.get("lower", 0.0))
    upper = float(row.get("upper", 1.0))
    if lower <= probability < upper or (probability == 1.0 and upper == 1.0):
      return row
  return {"index": 0, "sample_count": 0, "realized_rate": None}


def _model_predictions(
  factors: pd.DataFrame,
  bundle: SelectionArtifactBundle,
) -> dict[str, np.ndarray]:
  preprocessing = bundle.preprocessing["families"]
  logistic_matrix = _preprocess(factors, preprocessing["LOGISTIC"])
  logistic_raw, logistic_uncalibrated = predict_logistic(
    logistic_matrix, bundle.logistic
  )
  logistic_probability = apply_calibrator(
    logistic_raw,
    logistic_uncalibrated,
    bundle.calibrators["families"]["LOGISTIC"],
  )
  lightgbm_matrix = _preprocess(factors, preprocessing["LIGHTGBM"])
  booster = lgb.Booster(model_file=str(bundle.lightgbm_path))
  lightgbm_uncalibrated = np.asarray(booster.predict(lightgbm_matrix), dtype=float)
  clipped = np.clip(lightgbm_uncalibrated, 1e-8, 1 - 1e-8)
  lightgbm_raw = np.log(clipped / (1.0 - clipped))
  lightgbm_probability = apply_calibrator(
    lightgbm_raw,
    lightgbm_uncalibrated,
    bundle.calibrators["families"]["LIGHTGBM"],
  )
  selected = bundle.runtime["selected_family"]
  result = {
    "raw": logistic_raw if selected == "LOGISTIC" else lightgbm_raw,
    "selected": (
      logistic_probability if selected == "LOGISTIC" else lightgbm_probability
    ),
    "logistic": logistic_probability,
    "lightgbm": lightgbm_probability,
    "ood_fit": _ood_fit(factors, preprocessing[selected]),
  }
  expected_rows = len(factors)
  for name, values in result.items():
    array = np.asarray(values, dtype=float)
    if array.shape != (expected_rows,) or not np.isfinite(array).all():
      raise ValueError(f"模型推理输出无效: {name}")
    if name != "raw" and ((array < 0).any() or (array > 1).any()):
      raise ValueError(f"模型推理概率或质量输出越界: {name}")
    result[name] = array
  return result


async def _certified_snapshot(
  as_of_date: date,
) -> tuple[pd.DataFrame, datetime]:
  async with AsyncSessionLocal() as db:
    certified = await DailySignalRunRepository(db).find_latest_completed(as_of_date)
    if certified is None or certified.completed_at is None:
      raise ValueError("指定日期没有当前指标版本的全市场成功快照")
    result = await db.execute(
      select(IndicatorSnapshot, Instrument)
      .outerjoin(Instrument, Instrument.id == IndicatorSnapshot.code)
      .where(
        IndicatorSnapshot.snapshot_date == as_of_date,
        IndicatorSnapshot.calculation_version == INDICATOR_VERSION,
      )
      .order_by(IndicatorSnapshot.code.asc())
    )
    records: list[dict[str, Any]] = []
    for snapshot, instrument in result.all():
      item = {
        "stock_code": snapshot.code,
        "snapshot_date": snapshot.snapshot_date,
        "name": snapshot.name or (instrument.name if instrument else snapshot.code),
        "instrument_type": snapshot.instrument_type,
        "volume": snapshot.volume,
        "amount": snapshot.amount,
        "open_date": instrument.open_date if instrument else None,
        "expire_date": instrument.expire_date if instrument else None,
        "instrument_status": instrument.instrument_status if instrument else None,
        "valid_history": snapshot.valid_history_count,
      }
      for definition in INDICATOR_DEFINITIONS:
        item[definition.id] = getattr(snapshot, definition.id, None)
      records.append(item)
    if not records:
      raise ValueError("当前指标版本的认证快照没有可推理行")
    return pd.DataFrame.from_records(records), certified.completed_at


def _factor_snapshot(
  indicators: pd.DataFrame,
  *,
  as_of_date: date,
  minimum_valid_history: int,
  minimum_factor_completeness: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
  metadata = indicators[
    [
      "stock_code",
      "snapshot_date",
      "name",
      "instrument_type",
      "volume",
      "amount",
      "open_date",
      "expire_date",
      "instrument_status",
      "valid_history",
    ]
  ].reset_index(drop=True)
  metadata["is_ordinary_stock"] = metadata["instrument_type"].astype(
    str
  ).str.lower().eq("stock") & metadata["stock_code"].astype(str).str.fullmatch(
    _ORDINARY_A_SHARE
  )
  metadata["is_st"] = metadata["name"].fillna("").str.contains(_ST_NAME)
  metadata["is_suspended"] = pd.to_numeric(metadata["volume"], errors="coerce").fillna(
    0
  ).le(0) | pd.to_numeric(metadata["amount"], errors="coerce").fillna(0).le(0)
  metadata["has_delisting_risk"] = (
    metadata["name"].fillna("").str.contains("退")
    | pd.to_datetime(metadata["expire_date"], errors="coerce").le(
      pd.Timestamp(as_of_date)
    )
  ).fillna(False)
  metadata["valid_history"] = (
    pd.to_numeric(metadata["valid_history"], errors="coerce").fillna(0).astype(int)
  )
  base_eligible = (
    metadata["is_ordinary_stock"]
    & ~metadata["is_st"]
    & ~metadata["is_suspended"]
    & ~metadata["has_delisting_risk"]
    & metadata["valid_history"].ge(int(minimum_valid_history))
  )
  indicators = indicators.loc[base_eligible].reset_index(drop=True)
  metadata = metadata.loc[base_eligible].reset_index(drop=True)
  if metadata.empty:
    raise ValueError("认证指标快照没有符合基础股票池资格的普通 A 股")
  completeness = selection_factor_completeness(indicators)
  complete = completeness.ge(float(minimum_factor_completeness))
  indicators = indicators.loc[complete].reset_index(drop=True)
  metadata = metadata.loc[complete].reset_index(drop=True)
  completeness = completeness.loc[complete].reset_index(drop=True)
  if metadata.empty:
    raise ValueError("认证指标快照没有达到因子完整度门禁的普通 A 股")
  factors = build_selection_factor_frame(indicators, date_column="snapshot_date")
  metadata["factor_completeness"] = completeness
  return factors.reset_index(drop=True), metadata


def _write_factor_snapshot(
  factors: pd.DataFrame,
  metadata: pd.DataFrame,
  *,
  as_of_date: date,
  model_version: str,
  inference_id: str,
) -> tuple[Path, str]:
  directory = (
    _workspace_root()
    / ".runtime"
    / "prediction-runs"
    / as_of_date.isoformat()
    / model_version
  )
  directory.mkdir(parents=True, exist_ok=True)
  target = directory / f"{inference_id}.parquet"
  temporary = target.with_name(f".{target.name}.partial")
  pd.concat([metadata, factors], axis=1).to_parquet(temporary, index=False)
  temporary.replace(target)
  return target, file_sha256(target)


def _prediction_rows(
  metadata: pd.DataFrame,
  predicted: dict[str, np.ndarray],
  bundle: SelectionArtifactBundle,
  rule,
) -> list[dict[str, Any]]:
  selected = predicted["selected"]
  ordered = sorted(
    range(len(metadata)),
    key=lambda index: (-float(selected[index]), str(metadata.iloc[index].stock_code)),
  )
  rank_by_index = {index: rank for rank, index in enumerate(ordered, start=1)}
  rows: list[dict[str, Any]] = []
  for index in range(len(metadata)):
    item = metadata.iloc[index]
    probability = float(selected[index])
    ood_fit = float(predicted["ood_fit"][index])
    bucket = _calibration_bucket(bundle, probability)
    reasons: list[str] = []
    risks: list[str] = []
    if not bool(item.is_ordinary_stock):
      risks.append("NOT_ORDINARY_SH_SZ_A_SHARE")
    if bool(item.is_st):
      risks.append("CURRENT_ST")
    if bool(item.is_suspended):
      risks.append("CURRENT_SUSPENDED")
    if bool(item.has_delisting_risk):
      risks.append("CURRENT_DELISTING_RISK")
    if int(item.valid_history) < int(rule.minimum_valid_history):
      risks.append("INSUFFICIENT_VALID_HISTORY")
    if float(item.factor_completeness) < float(rule.minimum_factor_completeness):
      risks.append("LOW_FACTOR_COMPLETENESS")
    if ood_fit < float(rule.minimum_ood_fit):
      risks.append("CRITICAL_OOD")
    confidence = geometric_confidence(
      factor_completeness=float(item.factor_completeness),
      ood_fit=ood_fit,
      logistic_probability=float(predicted["logistic"][index]),
      lightgbm_probability=float(predicted["lightgbm"][index]),
      calibration_bin_samples=int(bucket.get("sample_count") or 0),
    )
    if probability < float(rule.minimum_probability):
      reasons.append("PROBABILITY_BELOW_0_60")
    if confidence < float(rule.minimum_confidence):
      reasons.append("CONFIDENCE_BELOW_0_60")
    eligible = not risks and not reasons
    rank = rank_by_index[index]
    candidate_level = None
    if eligible and rank <= int(rule.level_a_size):
      candidate_level = "A"
    elif eligible and rank <= int(rule.level_a_size + rule.level_b_size):
      candidate_level = "B"
    if candidate_level:
      reasons.append(f"CANDIDATE_LEVEL_{candidate_level}")
    rows.append(
      {
        "instrument_code": str(item.stock_code),
        "calibrated_probability": probability,
        "raw_score": float(predicted["raw"][index]),
        "logistic_probability": float(predicted["logistic"][index]),
        "lightgbm_probability": float(predicted["lightgbm"][index]),
        "rank": rank,
        "confidence": confidence,
        "factor_completeness": float(item.factor_completeness),
        "ood_fit": ood_fit,
        "calibration_bin_index": int(bucket.get("index") or 0),
        "calibration_bin_samples": int(bucket.get("sample_count") or 0),
        "calibration_bin_realized_rate": (
          float(bucket["realized_rate"])
          if bucket.get("realized_rate") is not None
          else None
        ),
        "eligible": eligible,
        "candidate_level": candidate_level,
        "reason_codes": reasons,
        "risk_flags": risks,
      }
    )
  return rows


@flow(
  name="次日上涨概率只读推理",
  description="在认证日级指标快照后生成 ACTIVE 与 SHADOW 研究候选",
  retries=0,
)
async def stock_probability_inference_flow(
  as_of: str = "",
) -> dict[str, Any]:
  logger = get_run_logger()
  as_of_date = date.fromisoformat(as_of) if as_of else time_utils.today()
  async with AsyncSessionLocal() as db:
    repository = StockSelectionRepository(db)
    models = await repository.runtime_models()
  if not models:
    return {
      "status": "skipped",
      "as_of": as_of_date.isoformat(),
      "reason": "NO_ACTIVE_OR_SHADOW_MODEL",
      "runs": [],
    }
  indicators, cutoff_at = await _certified_snapshot(as_of_date)
  target_date = await TradingDateHelper().get_next_trading_date(
    "SH", from_date=as_of_date
  )
  async with AsyncSessionLocal() as db:
    rule = await StockSelectionRepository(db).active_rule()
  factors, metadata = _factor_snapshot(
    indicators,
    as_of_date=as_of_date,
    minimum_valid_history=rule.minimum_valid_history,
    minimum_factor_completeness=rule.minimum_factor_completeness,
  )
  results: list[dict[str, Any]] = []
  for model in models:
    async with AsyncSessionLocal() as db:
      repository = StockSelectionRepository(db)
      run = await repository.create_prediction_run(
        model=model,
        as_of_date=as_of_date,
        target_date=target_date,
        cutoff_at=cutoff_at,
        started_at=time_utils.now(),
        rule=rule,
      )
      try:
        if model.factor_set_hash != FACTOR_SET_HASH:
          raise ValueError("登记模型因子哈希与当前运行时不一致")
        bundle = load_selection_artifact(
          model.artifact_directory,
          expected_manifest_sha256=model.artifact_manifest_sha256,
        )
        predicted = _model_predictions(factors, bundle)
        rows = _prediction_rows(metadata, predicted, bundle, rule)
        snapshot_path, snapshot_hash = _write_factor_snapshot(
          factors,
          metadata,
          as_of_date=as_of_date,
          model_version=model.model_version,
          inference_id=run.id,
        )
        current = await repository.get_model(model.model_version)
        if current is None or current.stage != model.stage:
          raise ValueError("模型阶段在推理期间变化，拒绝发布旧阶段结果")
        await repository.publish_predictions(
          run,
          predictions=rows,
          rule=rule,
          factor_snapshot_path=str(snapshot_path),
          factor_snapshot_sha256=snapshot_hash,
          completed_at=time_utils.now(),
        )
        results.append(
          {
            "model_version": model.model_version,
            "stage": model.stage,
            "status": "SUCCESS",
            "prediction_count": len(rows),
            "candidate_count": sum(bool(row["candidate_level"]) for row in rows),
            "factor_snapshot_sha256": snapshot_hash,
          }
        )
      except Exception as exc:
        logger.exception("概率模型推理失败: model=%s", model.model_version)
        await repository.fail_prediction_run(
          run,
          error_code="INFERENCE_FAILED",
          error_message=str(exc),
          completed_at=time_utils.now(),
        )
        results.append(
          {
            "model_version": model.model_version,
            "stage": model.stage,
            "status": "FAILED",
            "error": str(exc),
          }
        )
  return {
    "status": "success"
    if all(item["status"] == "SUCCESS" for item in results)
    else "failed",
    "as_of": as_of_date.isoformat(),
    "target_date": target_date.isoformat(),
    "runs": results,
  }
