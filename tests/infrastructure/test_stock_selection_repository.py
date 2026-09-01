from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.stock_selection import (
  StockCandidate,
  StockCandidateRuleVersion,
  StockPrediction,
  StockPredictionRun,
  StockSelectionModelVersion,
)
from quantx_infrastructure.repositories.stock_selection_repository import (
  StockSelectionRepository,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TABLES = [
  Instrument.__table__,
  StockSelectionModelVersion.__table__,
  StockPredictionRun.__table__,
  StockPrediction.__table__,
  StockCandidateRuleVersion.__table__,
  StockCandidate.__table__,
]


def model_values(version: str, *, eligible: bool = True) -> dict[str, object]:
  return {
    "model_version": version,
    "run_key": f"run-{version}",
    "artifact_directory": f"/safe/{version}",
    "artifact_manifest_sha256": "a" * 64,
    "selected_family": "LOGISTIC",
    "indicator_version": "daily-indicator-v1",
    "factor_set_version": "next-day-selection-factor-v1",
    "factor_set_hash": "b" * 64,
    "label_version": "next-open-to-close-up-v1",
    "calibrator_version": "next-day-selection-calibrator-v1",
    "training_start": date(2021, 1, 1),
    "training_end": date(2025, 6, 30),
    "calibration_start": date(2025, 7, 1),
    "calibration_end": date(2025, 12, 31),
    "test_start": date(2026, 1, 1),
    "test_end": date(2026, 8, 31),
    "historical_universe_complete": eligible,
    "effect_gate_passed": eligible,
    "metrics": {},
    "gates": {"active_eligible": eligible},
    "evidence": {},
    "approved_by": "",
  }


@pytest.mark.asyncio
async def test_model_release_limits_and_atomic_active_switch() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(sync, tables=TABLES)
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  now = datetime(2026, 9, 1, tzinfo=timezone.utc)
  async with sessions() as db:
    repository = StockSelectionRepository(db)
    first = await repository.register_model(model_values("model-a"))
    second = await repository.register_model(model_values("model-b"))
    blocked = await repository.register_model(model_values("model-c", eligible=False))
    fourth = await repository.register_model(model_values("model-d"))

    changed = model_values("model-a")
    changed["artifact_manifest_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="不可变模型证据"):
      await repository.register_model(changed)

    first = await repository.set_model_stage(
      first.model_version,
      "SHADOW",
      expected_version=first.state_version,
      approved_by="owner",
      approved_at=now,
    )
    first = await repository.set_model_stage(
      first.model_version,
      "ACTIVE",
      expected_version=first.state_version,
      approved_by="owner",
      approved_at=now,
    )
    second = await repository.set_model_stage(
      second.model_version,
      "SHADOW",
      expected_version=second.state_version,
      approved_by="owner",
      approved_at=now,
    )
    second = await repository.set_model_stage(
      second.model_version,
      "ACTIVE",
      expected_version=second.state_version,
      approved_by="owner",
      approved_at=now,
    )

    assert (await repository.get_model("model-a")).stage == "SUSPENDED"
    assert second.stage == "ACTIVE"
    blocked = await repository.set_model_stage(
      blocked.model_version,
      "SHADOW",
      expected_version=blocked.state_version,
      approved_by="owner",
      approved_at=now,
    )
    with pytest.raises(ValueError, match="效果门禁"):
      await repository.set_model_stage(
        blocked.model_version,
        "ACTIVE",
        expected_version=blocked.state_version,
        approved_by="owner",
        approved_at=now,
      )
    with pytest.raises(ValueError, match="状态版本冲突"):
      await repository.set_model_stage(
        blocked.model_version,
        "SUSPENDED",
        expected_version=1,
        approved_by="owner",
        approved_at=now,
      )

    suspended = await repository.get_model("model-a")
    await repository.set_model_stage(
      suspended.model_version,
      "SHADOW",
      expected_version=suspended.state_version,
      approved_by="owner",
      approved_at=now,
    )
    with pytest.raises(ValueError, match="最多同时保留两个"):
      await repository.set_model_stage(
        fourth.model_version,
        "SHADOW",
        expected_version=fourth.state_version,
        approved_by="owner",
        approved_at=now,
      )

    runtime = await repository.runtime_models()
    assert [model.stage for model in runtime] == ["ACTIVE", "SHADOW", "SHADOW"]

  await engine.dispose()


@pytest.mark.asyncio
async def test_prediction_publish_is_one_complete_batch() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(sync, tables=TABLES)
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  now = datetime(2026, 9, 1, tzinfo=timezone.utc)
  async with sessions() as db:
    repository = StockSelectionRepository(db)
    model = await repository.register_model(model_values("model-a"))
    model = await repository.set_model_stage(
      model.model_version,
      "SHADOW",
      expected_version=model.state_version,
      approved_by="owner",
      approved_at=now,
    )
    rule = StockCandidateRuleVersion(
      rule_version="next-day-selection-candidate-v1",
      status="ACTIVE",
      minimum_probability=0.6,
      minimum_confidence=0.6,
      minimum_ood_fit=0.8,
      minimum_factor_completeness=0.9,
      minimum_valid_history=252,
      level_a_size=20,
      level_b_size=30,
      rules={},
      state_version=1,
    )
    db.add(rule)
    db.add(
      Instrument(
        id="600000.SH",
        instrument_id="600000",
        name="浦发银行",
      )
    )
    await db.commit()
    run = await repository.create_prediction_run(
      model=model,
      as_of_date=date(2026, 9, 1),
      target_date=date(2026, 9, 2),
      cutoff_at=now,
      started_at=now,
      rule=rule,
    )
    published = await repository.publish_predictions(
      run,
      predictions=[
        {
          "instrument_code": "600000.SH",
          "calibrated_probability": 0.68,
          "raw_score": 0.75,
          "logistic_probability": 0.66,
          "lightgbm_probability": 0.7,
          "rank": 1,
          "confidence": 0.8,
          "factor_completeness": 0.98,
          "ood_fit": 0.9,
          "calibration_bin_index": 6,
          "calibration_bin_samples": 1200,
          "calibration_bin_realized_rate": 0.64,
          "eligible": True,
          "candidate_level": "A",
          "reason_codes": ["TOP_20"],
          "risk_flags": [],
        }
      ],
      rule=rule,
      factor_snapshot_path="factors.parquet",
      factor_snapshot_sha256="c" * 64,
      completed_at=now,
    )

    assert published.status == "SUCCESS"
    assert published.prediction_count == 1
    assert published.candidate_count == 1
    assert await db.scalar(select(func.count()).select_from(StockPrediction)) == 1
    assert await db.scalar(select(func.count()).select_from(StockCandidate)) == 1
    with pytest.raises(ValueError, match="RUNNING"):
      await repository.publish_predictions(
        published,
        predictions=[],
        rule=rule,
        factor_snapshot_path="factors.parquet",
        factor_snapshot_sha256="c" * 64,
        completed_at=now,
      )

    second_run = await repository.create_prediction_run(
      model=model,
      as_of_date=date(2026, 9, 1),
      target_date=date(2026, 9, 2),
      cutoff_at=now,
      started_at=now,
      rule=rule,
    )
    await repository.publish_predictions(
      second_run,
      predictions=[
        {
          "instrument_code": "600000.SH",
          "calibrated_probability": 0.72,
          "raw_score": 0.8,
          "logistic_probability": 0.7,
          "lightgbm_probability": 0.74,
          "rank": 1,
          "confidence": 0.82,
          "factor_completeness": 0.99,
          "ood_fit": 0.91,
          "calibration_bin_index": 7,
          "calibration_bin_samples": 1300,
          "calibration_bin_realized_rate": 0.68,
          "eligible": True,
          "candidate_level": "A",
          "reason_codes": ["TOP_20"],
          "risk_flags": [],
        }
      ],
      rule=rule,
      factor_snapshot_path="factors-2.parquet",
      factor_snapshot_sha256="d" * 64,
      completed_at=now,
    )

    rows, total = await repository.list_candidates(
      as_of_date=date(2026, 9, 1),
      prediction_run_ids=[second_run.id],
    )
    assert total == 1
    assert len(rows) == 1
    assert rows[0][0].prediction_run_id == second_run.id
    assert rows[0][0].calibrated_probability == pytest.approx(0.72)

    stale_stage_run = await repository.create_prediction_run(
      model=model,
      as_of_date=date(2026, 9, 1),
      target_date=date(2026, 9, 2),
      cutoff_at=now,
      started_at=now,
      rule=rule,
    )
    model = await repository.set_model_stage(
      model.model_version,
      "SUSPENDED",
      expected_version=model.state_version,
      approved_by="owner",
      approved_at=now,
    )
    with pytest.raises(ValueError, match="模型阶段"):
      await repository.publish_predictions(
        stale_stage_run,
        predictions=[],
        rule=rule,
        factor_snapshot_path="factors-3.parquet",
        factor_snapshot_sha256="e" * 64,
        completed_at=now,
      )

  await engine.dispose()
