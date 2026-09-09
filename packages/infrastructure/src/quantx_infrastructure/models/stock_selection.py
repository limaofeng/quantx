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
  func,
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


# The training tables intentionally live beside the read-only inference
# registry.  They are a separate lifecycle: a successful training run is
# evidence for a later, manual model registration and never an inference
# model by itself.
TRAINING_RUN_KINDS = ("DEVELOPMENT", "FINAL_EVALUATION")
TRAINING_RUN_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")
TRAINING_RUN_PHASES = (
  "PREFLIGHT",
  "DATASET_BUILD",
  "WALK_FORWARD",
  "FINAL_FIT",
  "CALIBRATION",
  "FROZEN_TEST",
  "ARTIFACT_PUBLISH",
)


class StockSelectionDatasetVersion(Base):
  """Immutable evidence for a certified local research dataset."""

  __tablename__ = "stock_selection_dataset_versions"
  __table_args__ = (
    CheckConstraint(
      "status IN ('CERTIFIED','RETIRED')",
      name="ck_stock_selection_dataset_status",
    ),
    CheckConstraint(
      "date_start <= date_end",
      name="ck_stock_selection_dataset_dates",
    ),
    CheckConstraint(
      "length(manifest_sha256) = 64",
      name="ck_stock_selection_dataset_manifest_sha256",
    ),
    CheckConstraint(
      "length(factor_set_hash) = 64",
      name="ck_stock_selection_dataset_factor_set_hash",
    ),
    CheckConstraint(
      "sample_count >= 0 AND stock_count >= 0 AND trading_day_count >= 0",
      name="ck_stock_selection_dataset_counts",
    ),
    Index(
      "ix_stock_selection_dataset_status_created",
      "status",
      "created_at",
    ),
  )

  dataset_version = Column(String(120), primary_key=True)
  status = Column(String(16), nullable=False, default="CERTIFIED", index=True)
  source_kind = Column(String(48), nullable=False)
  # This is a safe relative directory key, never an absolute server path.
  source_reference = Column(String(512), nullable=False)
  date_start = Column(Date, nullable=False)
  date_end = Column(Date, nullable=False)
  universe_spec = Column(JSON, nullable=False, default=dict)
  indicator_version = Column(String(80), nullable=False)
  factor_set_version = Column(String(80), nullable=False)
  factor_set_hash = Column(String(64), nullable=False)
  label_version = Column(String(80), nullable=False)
  manifest_sha256 = Column(String(64), nullable=False)
  sample_count = Column(Integer, nullable=False)
  stock_count = Column(Integer, nullable=False)
  trading_day_count = Column(Integer, nullable=False)
  quality_summary = Column(JSON, nullable=False, default=dict)
  created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockSelectionTrainingSpec(Base):
  """Immutable, hashed training configuration snapshot."""

  __tablename__ = "stock_selection_training_specs"
  __table_args__ = (
    CheckConstraint(
      "run_kind IN ('DEVELOPMENT','FINAL_EVALUATION')",
      name="ck_stock_selection_training_spec_run_kind",
    ),
    CheckConstraint(
      "requested_backend IN ('AUTO','CPU','GPU_REQUIRED')",
      name="ck_stock_selection_training_spec_requested_backend",
    ),
    CheckConstraint(
      "resolved_backend IN ('CPU','LIGHTGBM_OPENCL_GPU')",
      name="ck_stock_selection_training_spec_resolved_backend",
    ),
    CheckConstraint(
      "worker_batch_size >= 1 AND worker_batch_size <= 1000",
      name="ck_stock_selection_training_spec_batch_size",
    ),
    CheckConstraint(
      "length(spec_hash) = 64 AND length(environment_requirement_hash) = 64 "
      "AND length(coordinate_hash) = 64 AND length(experiment_group_hash) = 64",
      name="ck_stock_selection_training_spec_hash_lengths",
    ),
    CheckConstraint(
      "frozen_test_access_count >= 0",
      name="ck_stock_selection_training_spec_frozen_access_count",
    ),
    Index(
      "ix_stock_selection_training_specs_dataset_kind",
      "dataset_version",
      "run_kind",
    ),
    Index(
      "ix_stock_selection_training_specs_experiment_group",
      "experiment_group_hash",
    ),
  )

  spec_id = Column(String(36), primary_key=True)
  dataset_version = Column(
    String(120),
    ForeignKey("stock_selection_dataset_versions.dataset_version", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )
  universe_spec = Column(JSON, nullable=False, default=dict)
  run_kind = Column(String(24), nullable=False)
  split_spec = Column(JSON, nullable=False, default=dict)
  model_spec = Column(JSON, nullable=False, default=dict)
  evaluation_spec = Column(JSON, nullable=False, default=dict)
  requested_backend = Column(String(16), nullable=False)
  resolved_backend = Column(String(32), nullable=False)
  random_seed = Column(Integer, nullable=False)
  worker_batch_size = Column(Integer, nullable=False)
  note = Column(String(500), nullable=False, default="")
  spec_hash = Column(String(64), nullable=False)
  environment_requirement_hash = Column(String(64), nullable=False)
  coordinate_hash = Column(String(64), nullable=False)
  experiment_group_hash = Column(String(64), nullable=False)
  frozen_test_access_count = Column(Integer, nullable=False, default=0)
  created_by = Column(String(64), nullable=False)
  created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockSelectionTrainingRun(Base):
  """Durable queue and state machine for one isolated Research process."""

  __tablename__ = "stock_selection_training_runs"
  __table_args__ = (
    CheckConstraint(
      "run_kind IN ('DEVELOPMENT','FINAL_EVALUATION')",
      name="ck_stock_selection_training_run_kind",
    ),
    CheckConstraint(
      "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
      name="ck_stock_selection_training_run_status",
    ),
    CheckConstraint(
      "phase IN ('PREFLIGHT','DATASET_BUILD','WALK_FORWARD','FINAL_FIT',"
      "'CALIBRATION','FROZEN_TEST','ARTIFACT_PUBLISH')",
      name="ck_stock_selection_training_run_phase",
    ),
    CheckConstraint(
      "completed_units >= 0 AND total_units >= 0 AND completed_units <= total_units",
      name="ck_stock_selection_training_run_units",
    ),
    CheckConstraint(
      "((run_kind = 'FINAL_EVALUATION' AND parent_run_id IS NOT NULL) OR "
      "(run_kind = 'DEVELOPMENT' AND parent_run_id IS NULL))",
      name="ck_stock_selection_training_run_parent_kind",
    ),
    CheckConstraint(
      "state_version >= 1",
      name="ck_stock_selection_training_run_state_version",
    ),
    Index(
      "ix_stock_selection_training_runs_status_requested",
      "status",
      "requested_at",
    ),
    Index(
      "ix_stock_selection_training_runs_parent",
      "parent_run_id",
    ),
    Index(
      "uq_stock_selection_training_runs_one_running",
      "status",
      unique=True,
      postgresql_where=text("status = 'RUNNING'"),
      sqlite_where=text("status = 'RUNNING'"),
    ),
  )

  run_id = Column(String(36), primary_key=True)
  run_key = Column(String(160), nullable=True, unique=True)
  spec_id = Column(
    String(36),
    ForeignKey("stock_selection_training_specs.spec_id", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )
  run_kind = Column(String(24), nullable=False)
  parent_run_id = Column(
    String(36),
    ForeignKey("stock_selection_training_runs.run_id", ondelete="RESTRICT"),
    nullable=True,
  )
  status = Column(String(16), nullable=False, default="QUEUED", index=True)
  phase = Column(String(24), nullable=False, default="PREFLIGHT")
  completed_units = Column(Integer, nullable=False, default=0)
  total_units = Column(Integer, nullable=False, default=0)
  prefect_flow_run_id = Column(String(128), nullable=True)
  execution_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
  requested_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
  started_at = Column(DateTime(timezone=True), nullable=True)
  completed_at = Column(DateTime(timezone=True), nullable=True)
  cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
  cancel_idempotency_key = Column(String(160), nullable=True)
  artifact_manifest_sha256 = Column(String(64), nullable=True)
  environment_evidence = Column(JSON, nullable=False, default=dict)
  metrics_summary = Column(JSON, nullable=False, default=dict)
  gate_summary = Column(JSON, nullable=False, default=dict)
  error_code = Column(String(64), nullable=True)
  error_message = Column(String(512), nullable=True)
  state_version = Column(Integer, nullable=False, default=1)
  idempotency_key = Column(String(160), nullable=False, unique=True)
