"""Read-only next-day probability model registry and daily projections."""

from __future__ import annotations

import uuid

from sqlalchemy import (
  JSON,
  Boolean,
  CheckConstraint,
  Column,
  Date,
  DateTime,
  Float,
  ForeignKey,
  Index,
  Integer,
  String,
  UniqueConstraint,
  text,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin

MODEL_STAGES = ("CANDIDATE", "SHADOW", "ACTIVE", "SUSPENDED", "RETIRED")


class StockSelectionModelVersion(Base, TimestampMixin):
  __tablename__ = "stock_selection_model_versions"
  __table_args__ = (
    CheckConstraint(
      "stage IN ('CANDIDATE','SHADOW','ACTIVE','SUSPENDED','RETIRED')",
      name="ck_stock_selection_model_stage",
    ),
    CheckConstraint(
      "selected_family IN ('LOGISTIC','LIGHTGBM')",
      name="ck_stock_selection_model_family",
    ),
    CheckConstraint(
      "training_start <= training_end AND training_end < calibration_start "
      "AND calibration_start <= calibration_end AND calibration_end < test_start "
      "AND test_start <= test_end",
      name="ck_stock_selection_model_periods",
    ),
    CheckConstraint(
      "state_version >= 1", name="ck_stock_selection_model_state_version"
    ),
    Index(
      "uq_stock_selection_one_active",
      "stage",
      unique=True,
      postgresql_where=text("stage = 'ACTIVE'"),
      sqlite_where=text("stage = 'ACTIVE'"),
    ),
  )

  model_version = Column(String(80), primary_key=True)
  run_key = Column(String(160), nullable=False, unique=True)
  artifact_directory = Column(String(512), nullable=False)
  artifact_manifest_sha256 = Column(String(64), nullable=False)
  selected_family = Column(String(16), nullable=False)
  indicator_version = Column(String(64), nullable=False)
  factor_set_version = Column(String(80), nullable=False)
  factor_set_hash = Column(String(64), nullable=False)
  label_version = Column(String(80), nullable=False)
  calibrator_version = Column(String(80), nullable=False)
  stage = Column(String(16), nullable=False, default="CANDIDATE", index=True)
  training_start = Column(Date, nullable=False)
  training_end = Column(Date, nullable=False)
  calibration_start = Column(Date, nullable=False)
  calibration_end = Column(Date, nullable=False)
  test_start = Column(Date, nullable=False)
  test_end = Column(Date, nullable=False)
  historical_universe_complete = Column(Boolean, nullable=False, default=False)
  effect_gate_passed = Column(Boolean, nullable=False, default=False)
  metrics = Column(JSON, nullable=False, default=dict)
  gates = Column(JSON, nullable=False, default=dict)
  evidence = Column(JSON, nullable=False, default=dict)
  approved_by = Column(String(64), nullable=False, default="")
  approved_at = Column(DateTime, nullable=True)
  state_version = Column(Integer, nullable=False, default=1)


class StockPredictionRun(Base, TimestampMixin):
  __tablename__ = "stock_prediction_runs"
  __table_args__ = (
    CheckConstraint(
      "model_stage IN ('ACTIVE','SHADOW')",
      name="ck_stock_prediction_run_model_stage",
    ),
    CheckConstraint(
      "status IN ('RUNNING','SUCCESS','FAILED')",
      name="ck_stock_prediction_run_status",
    ),
    CheckConstraint("target_date > as_of_date", name="ck_stock_prediction_run_target"),
    CheckConstraint(
      "eligible_count >= 0 AND prediction_count >= 0 AND candidate_count >= 0",
      name="ck_stock_prediction_run_counts",
    ),
    Index("ix_stock_prediction_run_date_status", "as_of_date", "status"),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  run_key = Column(String(160), nullable=False, unique=True)
  model_version = Column(
    String(80),
    ForeignKey("stock_selection_model_versions.model_version"),
    nullable=False,
    index=True,
  )
  model_stage = Column(String(16), nullable=False)
  as_of_date = Column(Date, nullable=False, index=True)
  target_date = Column(Date, nullable=False)
  cutoff_at = Column(DateTime, nullable=False)
  status = Column(String(24), nullable=False, default="RUNNING", index=True)
  indicator_version = Column(String(64), nullable=False)
  factor_set_version = Column(String(80), nullable=False)
  factor_set_hash = Column(String(64), nullable=False)
  label_version = Column(String(80), nullable=False)
  calibrator_version = Column(String(80), nullable=False)
  candidate_rule_version = Column(
    String(80),
    ForeignKey("stock_candidate_rule_versions.rule_version"),
    nullable=False,
  )
  factor_snapshot_path = Column(String(512), nullable=True)
  factor_snapshot_sha256 = Column(String(64), nullable=True)
  started_at = Column(DateTime, nullable=False)
  completed_at = Column(DateTime, nullable=True)
  eligible_count = Column(Integer, nullable=False, default=0)
  prediction_count = Column(Integer, nullable=False, default=0)
  candidate_count = Column(Integer, nullable=False, default=0)
  error_code = Column(String(64), nullable=True)
  error_message = Column(String(512), nullable=True)
  warnings = Column(JSON, nullable=False, default=list)


class StockPrediction(Base, TimestampMixin):
  __tablename__ = "stock_predictions"
  __table_args__ = (
    CheckConstraint(
      "calibrated_probability >= 0 AND calibrated_probability <= 1",
      name="ck_stock_prediction_probability",
    ),
    CheckConstraint(
      "confidence >= 0 AND confidence <= 1",
      name="ck_stock_prediction_confidence",
    ),
    CheckConstraint(
      "logistic_probability >= 0 AND logistic_probability <= 1 "
      "AND lightgbm_probability >= 0 AND lightgbm_probability <= 1",
      name="ck_stock_prediction_family_probabilities",
    ),
    CheckConstraint(
      "factor_completeness >= 0 AND factor_completeness <= 1 "
      "AND ood_fit >= 0 AND ood_fit <= 1",
      name="ck_stock_prediction_quality",
    ),
    CheckConstraint(
      "model_stage IN ('ACTIVE','SHADOW')",
      name="ck_stock_prediction_model_stage",
    ),
    CheckConstraint(
      "rank >= 1 AND calibration_bin_index >= 0 AND calibration_bin_samples >= 0",
      name="ck_stock_prediction_rank_calibration",
    ),
    CheckConstraint(
      "candidate_level IS NULL OR candidate_level IN ('A','B')",
      name="ck_stock_prediction_candidate_level",
    ),
    UniqueConstraint(
      "prediction_run_id", "instrument_code", name="uq_stock_prediction_run_code"
    ),
    Index("ix_stock_prediction_date_rank", "as_of_date", "rank"),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  prediction_run_id = Column(
    String(36), ForeignKey("stock_prediction_runs.id"), nullable=False, index=True
  )
  as_of_date = Column(Date, nullable=False, index=True)
  target_date = Column(Date, nullable=False)
  instrument_code = Column(String(20), nullable=False, index=True)
  model_version = Column(
    String(80),
    ForeignKey("stock_selection_model_versions.model_version"),
    nullable=False,
    index=True,
  )
  model_stage = Column(String(16), nullable=False)
  calibrated_probability = Column(Float, nullable=False)
  raw_score = Column(Float, nullable=False)
  logistic_probability = Column(Float, nullable=False)
  lightgbm_probability = Column(Float, nullable=False)
  rank = Column(Integer, nullable=False)
  confidence = Column(Float, nullable=False)
  factor_completeness = Column(Float, nullable=False)
  ood_fit = Column(Float, nullable=False)
  calibration_bin_index = Column(Integer, nullable=False)
  calibration_bin_samples = Column(Integer, nullable=False)
  calibration_bin_realized_rate = Column(Float, nullable=True)
  eligible = Column(Boolean, nullable=False, default=False)
  candidate_level = Column(String(1), nullable=True)
  reason_codes = Column(JSON, nullable=False, default=list)
  risk_flags = Column(JSON, nullable=False, default=list)


class StockCandidateRuleVersion(Base, TimestampMixin):
  __tablename__ = "stock_candidate_rule_versions"
  __table_args__ = (
    CheckConstraint(
      "status IN ('ACTIVE','RETIRED')",
      name="ck_stock_candidate_rule_status",
    ),
    CheckConstraint(
      "minimum_probability >= 0 AND minimum_probability <= 1 "
      "AND minimum_confidence >= 0 AND minimum_confidence <= 1 "
      "AND minimum_ood_fit >= 0 AND minimum_ood_fit <= 1 "
      "AND minimum_factor_completeness >= 0 "
      "AND minimum_factor_completeness <= 1",
      name="ck_stock_candidate_rule_thresholds",
    ),
    CheckConstraint(
      "minimum_valid_history >= 252 AND level_a_size >= 1 AND level_b_size >= 1",
      name="ck_stock_candidate_rule_sizes",
    ),
    CheckConstraint("state_version >= 1", name="ck_stock_candidate_rule_state_version"),
    Index(
      "uq_stock_candidate_one_active_rule",
      "status",
      unique=True,
      postgresql_where=text("status = 'ACTIVE'"),
      sqlite_where=text("status = 'ACTIVE'"),
    ),
  )

  rule_version = Column(String(80), primary_key=True)
  status = Column(String(16), nullable=False, default="ACTIVE", index=True)
  minimum_probability = Column(Float, nullable=False, default=0.6)
  minimum_confidence = Column(Float, nullable=False, default=0.6)
  minimum_ood_fit = Column(Float, nullable=False, default=0.8)
  minimum_factor_completeness = Column(Float, nullable=False, default=0.9)
  minimum_valid_history = Column(Integer, nullable=False, default=252)
  level_a_size = Column(Integer, nullable=False, default=20)
  level_b_size = Column(Integer, nullable=False, default=30)
  rules = Column(JSON, nullable=False, default=dict)
  state_version = Column(Integer, nullable=False, default=1)


class StockCandidate(Base, TimestampMixin):
  __tablename__ = "stock_candidates"
  __table_args__ = (
    CheckConstraint(
      "candidate_level IN ('A','B')",
      name="ck_stock_candidate_level",
    ),
    CheckConstraint(
      "model_stage IN ('ACTIVE','SHADOW')",
      name="ck_stock_candidate_model_stage",
    ),
    CheckConstraint(
      "rank >= 1 AND calibrated_probability >= 0 AND calibrated_probability <= 1 "
      "AND confidence >= 0 AND confidence <= 1",
      name="ck_stock_candidate_rank_probability",
    ),
    UniqueConstraint(
      "prediction_run_id", "instrument_code", name="uq_stock_candidate_run_code"
    ),
    Index(
      "ix_stock_candidate_date_level_rank", "as_of_date", "candidate_level", "rank"
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  prediction_run_id = Column(
    String(36), ForeignKey("stock_prediction_runs.id"), nullable=False, index=True
  )
  prediction_id = Column(
    String(36), ForeignKey("stock_predictions.id"), nullable=False, unique=True
  )
  as_of_date = Column(Date, nullable=False, index=True)
  target_date = Column(Date, nullable=False)
  instrument_code = Column(String(20), nullable=False, index=True)
  model_version = Column(
    String(80),
    ForeignKey("stock_selection_model_versions.model_version"),
    nullable=False,
    index=True,
  )
  model_stage = Column(String(16), nullable=False)
  rule_version = Column(
    String(80),
    ForeignKey("stock_candidate_rule_versions.rule_version"),
    nullable=False,
  )
  candidate_level = Column(String(1), nullable=False)
  rank = Column(Integer, nullable=False)
  calibrated_probability = Column(Float, nullable=False)
  confidence = Column(Float, nullable=False)
  reason_codes = Column(JSON, nullable=False, default=list)
  risk_flags = Column(JSON, nullable=False, default=list)
