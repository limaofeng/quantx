"""Persistence boundary for read-only probability models and candidates."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.stock_selection import (
  MODEL_STAGES,
  StockCandidate,
  StockCandidateRuleVersion,
  StockPrediction,
  StockPredictionRun,
  StockSelectionModelVersion,
)

_TRANSITIONS = {
  "CANDIDATE": {"SHADOW", "RETIRED"},
  "SHADOW": {"ACTIVE", "SUSPENDED", "RETIRED"},
  "ACTIVE": {"SUSPENDED"},
  "SUSPENDED": {"SHADOW", "RETIRED"},
  "RETIRED": set(),
}


class StockSelectionRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def get_model(self, model_version: str) -> StockSelectionModelVersion | None:
    return await self.db.get(StockSelectionModelVersion, model_version)

  async def list_models(self, *, limit: int = 100) -> list[StockSelectionModelVersion]:
    result = await self.db.execute(
      select(StockSelectionModelVersion)
      .order_by(
        StockSelectionModelVersion.created_at.desc(),
        StockSelectionModelVersion.model_version.asc(),
      )
      .limit(max(1, min(limit, 200)))
    )
    return list(result.scalars().all())

  async def register_model(self, values: dict[str, Any]) -> StockSelectionModelVersion:
    existing = (
      await self.db.execute(
        select(StockSelectionModelVersion).where(
          or_(
            StockSelectionModelVersion.run_key == values["run_key"],
            StockSelectionModelVersion.model_version == values["model_version"],
          )
        )
      )
    ).scalar_one_or_none()
    if existing is not None:
      if existing.run_key != values["run_key"]:
        raise ValueError("模型版本已由另一研究运行登记")
      immutable_fields = (
        "model_version",
        "artifact_directory",
        "artifact_manifest_sha256",
        "selected_family",
        "indicator_version",
        "factor_set_version",
        "factor_set_hash",
        "label_version",
        "calibrator_version",
      )
      if any(getattr(existing, field) != values[field] for field in immutable_fields):
        raise ValueError("已登记研究运行的不可变模型证据发生变化")
      return existing
    row = StockSelectionModelVersion(stage="CANDIDATE", state_version=1, **values)
    self.db.add(row)
    await self.db.commit()
    await self.db.refresh(row)
    return row

  async def set_model_stage(
    self,
    model_version: str,
    stage: str,
    *,
    expected_version: int,
    approved_by: str,
    approved_at: datetime,
  ) -> StockSelectionModelVersion:
    target_stage = str(stage).upper()
    if target_stage not in MODEL_STAGES:
      raise ValueError("未知模型阶段")
    locked_rows = list(
      (
        await self.db.execute(
          select(StockSelectionModelVersion)
          .order_by(StockSelectionModelVersion.model_version.asc())
          .with_for_update()
        )
      ).scalars()
    )
    row = next(
      (item for item in locked_rows if item.model_version == model_version), None
    )
    if row is None:
      raise ValueError("模型版本不存在")
    if row.state_version != expected_version:
      raise ValueError("模型状态版本冲突，请刷新后重试")
    if target_stage == row.stage:
      return row
    if target_stage not in _TRANSITIONS.get(row.stage, set()):
      raise ValueError(f"不允许从 {row.stage} 切换到 {target_stage}")
    if target_stage == "SHADOW":
      shadow_count = sum(
        item.stage == "SHADOW" and item.model_version != row.model_version
        for item in locked_rows
      )
      if shadow_count >= 2:
        raise ValueError("最多同时保留两个 SHADOW 模型")
    if target_stage == "ACTIVE":
      if not row.effect_gate_passed:
        raise ValueError("模型未通过 Brier/ECE/Top20 置信区间效果门禁")
      if not row.historical_universe_complete:
        raise ValueError("历史 ST/行业/退市股票池不完整，模型只能保持 SHADOW")
      active_rows = [
        item
        for item in locked_rows
        if item.stage == "ACTIVE" and item.model_version != row.model_version
      ]
      for active in active_rows:
        active.stage = "SUSPENDED"
        active.state_version += 1
    row.stage = target_stage
    row.approved_by = approved_by[:64]
    row.approved_at = approved_at
    row.state_version += 1
    await self.db.commit()
    await self.db.refresh(row)
    return row

  async def runtime_models(self) -> list[StockSelectionModelVersion]:
    result = await self.db.execute(
      select(StockSelectionModelVersion)
      .where(StockSelectionModelVersion.stage.in_(("ACTIVE", "SHADOW")))
      .order_by(
        StockSelectionModelVersion.stage.asc(),
        StockSelectionModelVersion.approved_at.desc().nullslast(),
        StockSelectionModelVersion.model_version.asc(),
      )
    )
    rows = list(result.scalars().all())
    active = [row for row in rows if row.stage == "ACTIVE"][:1]
    shadow = [row for row in rows if row.stage == "SHADOW"][:2]
    return [*active, *shadow]

  async def active_rule(self) -> StockCandidateRuleVersion:
    row = (
      await self.db.execute(
        select(StockCandidateRuleVersion)
        .where(StockCandidateRuleVersion.status == "ACTIVE")
        .order_by(StockCandidateRuleVersion.created_at.desc())
        .limit(1)
      )
    ).scalar_one_or_none()
    if row is None:
      raise ValueError("候选规则版本未初始化")
    return row

  async def create_prediction_run(
    self,
    *,
    model: StockSelectionModelVersion,
    as_of_date: date,
    target_date: date,
    cutoff_at: datetime,
    started_at: datetime,
    rule: StockCandidateRuleVersion,
  ) -> StockPredictionRun:
    run = StockPredictionRun(
      id=str(uuid.uuid4()),
      run_key=f"{model.model_version}:{as_of_date.isoformat()}:{uuid.uuid4().hex[:12]}",
      model_version=model.model_version,
      model_stage=model.stage,
      as_of_date=as_of_date,
      target_date=target_date,
      cutoff_at=cutoff_at,
      status="RUNNING",
      indicator_version=model.indicator_version,
      factor_set_version=model.factor_set_version,
      factor_set_hash=model.factor_set_hash,
      label_version=model.label_version,
      calibrator_version=model.calibrator_version,
      candidate_rule_version=rule.rule_version,
      started_at=started_at,
      warnings=[],
    )
    self.db.add(run)
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def publish_predictions(
    self,
    run: StockPredictionRun,
    *,
    predictions: Sequence[dict[str, Any]],
    rule: StockCandidateRuleVersion,
    factor_snapshot_path: str,
    factor_snapshot_sha256: str,
    completed_at: datetime,
    warnings: Sequence[str] = (),
  ) -> StockPredictionRun:
    if run.status != "RUNNING":
      raise ValueError("只有 RUNNING 推理运行可以发布")
    if run.candidate_rule_version != rule.rule_version:
      raise ValueError("推理运行与候选规则版本不一致")
    current_model = (
      await self.db.execute(
        select(StockSelectionModelVersion)
        .where(StockSelectionModelVersion.model_version == run.model_version)
        .with_for_update()
      )
    ).scalar_one_or_none()
    if current_model is None or current_model.stage != run.model_stage:
      raise ValueError("模型阶段在推理期间变化，拒绝发布旧阶段结果")
    candidate_count = 0
    for values in predictions:
      prediction_id = str(uuid.uuid4())
      prediction = StockPrediction(
        id=prediction_id,
        prediction_run_id=run.id,
        as_of_date=run.as_of_date,
        target_date=run.target_date,
        model_version=run.model_version,
        model_stage=run.model_stage,
        **values,
      )
      self.db.add(prediction)
      if values.get("candidate_level"):
        candidate_count += 1
        self.db.add(
          StockCandidate(
            id=str(uuid.uuid4()),
            prediction_run_id=run.id,
            prediction_id=prediction_id,
            as_of_date=run.as_of_date,
            target_date=run.target_date,
            instrument_code=values["instrument_code"],
            model_version=run.model_version,
            model_stage=run.model_stage,
            rule_version=rule.rule_version,
            candidate_level=values["candidate_level"],
            rank=values["rank"],
            calibrated_probability=values["calibrated_probability"],
            confidence=values["confidence"],
            reason_codes=list(values.get("reason_codes") or []),
            risk_flags=list(values.get("risk_flags") or []),
          )
        )
    run.status = "SUCCESS"
    run.completed_at = completed_at
    run.eligible_count = sum(bool(item.get("eligible")) for item in predictions)
    run.prediction_count = len(predictions)
    run.candidate_count = candidate_count
    run.factor_snapshot_path = factor_snapshot_path[:512]
    run.factor_snapshot_sha256 = factor_snapshot_sha256
    run.warnings = list(warnings)
    run.error_code = None
    run.error_message = None
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def fail_prediction_run(
    self,
    run: StockPredictionRun,
    *,
    error_code: str,
    error_message: str,
    completed_at: datetime,
  ) -> None:
    await self.db.rollback()
    persisted = await self.db.get(StockPredictionRun, run.id)
    if persisted is None:
      return
    persisted.status = "FAILED"
    persisted.completed_at = completed_at
    persisted.error_code = str(error_code)[:64]
    persisted.error_message = str(error_message)[:512]
    await self.db.commit()

  async def latest_runs(
    self,
    *,
    as_of_date: date | None = None,
    limit: int = 20,
  ) -> list[StockPredictionRun]:
    stmt = select(StockPredictionRun)
    if as_of_date is not None:
      stmt = stmt.where(StockPredictionRun.as_of_date == as_of_date)
    result = await self.db.execute(
      stmt.order_by(
        StockPredictionRun.as_of_date.desc(),
        StockPredictionRun.started_at.desc(),
      ).limit(max(1, min(limit, 100)))
    )
    return list(result.scalars().all())

  async def list_candidates(
    self,
    *,
    as_of_date: date,
    prediction_run_ids: Sequence[str],
    levels: Sequence[str] = (),
    minimum_probability: float | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
  ) -> tuple[list[tuple[StockCandidate, StockPrediction, StockPredictionRun]], int]:
    run_ids = tuple(dict.fromkeys(prediction_run_ids))
    if not run_ids:
      return [], 0
    conditions = [
      StockCandidate.as_of_date == as_of_date,
      StockCandidate.prediction_run_id.in_(run_ids),
      StockPredictionRun.status == "SUCCESS",
    ]
    if levels:
      conditions.append(StockCandidate.candidate_level.in_(tuple(levels)))
    if minimum_probability is not None:
      conditions.append(
        StockCandidate.calibrated_probability >= float(minimum_probability)
      )
    if search:
      pattern = f"%{search.strip()}%"
      conditions.append(
        or_(
          StockCandidate.instrument_code.ilike(pattern),
          Instrument.name.ilike(pattern),
        )
      )
    joined = (
      select(StockCandidate, StockPrediction, StockPredictionRun)
      .join(StockPrediction, StockPrediction.id == StockCandidate.prediction_id)
      .join(
        StockPredictionRun, StockPredictionRun.id == StockCandidate.prediction_run_id
      )
      .join(Instrument, Instrument.id == StockCandidate.instrument_code)
      .where(*conditions)
    )
    total = int(
      await self.db.scalar(
        select(func.count()).select_from(joined.order_by(None).subquery())
      )
      or 0
    )
    result = await self.db.execute(
      joined.order_by(
        StockCandidate.calibrated_probability.desc(),
        StockCandidate.instrument_code.asc(),
      )
      .limit(max(1, min(limit, 200)))
      .offset(max(0, offset))
    )
    return list(result.all()), total
