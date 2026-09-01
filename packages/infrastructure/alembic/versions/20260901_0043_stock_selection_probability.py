"""Add the read-only next-day stock selection model registry and projections."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260901_0043"
down_revision = "20260901_0042"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
  return (
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
  )


def upgrade() -> None:
  op.create_table(
    "stock_selection_model_versions",
    sa.Column("model_version", sa.String(80), primary_key=True),
    sa.Column("run_key", sa.String(160), nullable=False, unique=True),
    sa.Column("artifact_directory", sa.String(512), nullable=False),
    sa.Column("artifact_manifest_sha256", sa.String(64), nullable=False),
    sa.Column("selected_family", sa.String(16), nullable=False),
    sa.Column("indicator_version", sa.String(64), nullable=False),
    sa.Column("factor_set_version", sa.String(80), nullable=False),
    sa.Column("factor_set_hash", sa.String(64), nullable=False),
    sa.Column("label_version", sa.String(80), nullable=False),
    sa.Column("calibrator_version", sa.String(80), nullable=False),
    sa.Column("stage", sa.String(16), nullable=False),
    sa.Column("training_start", sa.Date(), nullable=False),
    sa.Column("training_end", sa.Date(), nullable=False),
    sa.Column("calibration_start", sa.Date(), nullable=False),
    sa.Column("calibration_end", sa.Date(), nullable=False),
    sa.Column("test_start", sa.Date(), nullable=False),
    sa.Column("test_end", sa.Date(), nullable=False),
    sa.Column("historical_universe_complete", sa.Boolean(), nullable=False),
    sa.Column("effect_gate_passed", sa.Boolean(), nullable=False),
    sa.Column("metrics", sa.JSON(), nullable=False),
    sa.Column("gates", sa.JSON(), nullable=False),
    sa.Column("evidence", sa.JSON(), nullable=False),
    sa.Column("approved_by", sa.String(64), nullable=False),
    sa.Column("approved_at", sa.DateTime(), nullable=True),
    sa.Column("state_version", sa.Integer(), nullable=False),
    *_timestamps(),
    sa.CheckConstraint(
      "stage IN ('CANDIDATE','SHADOW','ACTIVE','SUSPENDED','RETIRED')",
      name="ck_stock_selection_model_stage",
    ),
    sa.CheckConstraint(
      "selected_family IN ('LOGISTIC','LIGHTGBM')",
      name="ck_stock_selection_model_family",
    ),
    sa.CheckConstraint(
      "training_start <= training_end AND training_end < calibration_start "
      "AND calibration_start <= calibration_end AND calibration_end < test_start "
      "AND test_start <= test_end",
      name="ck_stock_selection_model_periods",
    ),
    sa.CheckConstraint(
      "state_version >= 1", name="ck_stock_selection_model_state_version"
    ),
    comment="次日上涨概率模型的人工发布登记与证据门禁",
  )
  op.create_index(
    "ix_stock_selection_model_versions_stage",
    "stock_selection_model_versions",
    ["stage"],
  )
  op.create_index(
    "uq_stock_selection_one_active",
    "stock_selection_model_versions",
    ["stage"],
    unique=True,
    postgresql_where=sa.text("stage = 'ACTIVE'"),
    sqlite_where=sa.text("stage = 'ACTIVE'"),
  )

  op.create_table(
    "stock_prediction_runs",
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("run_key", sa.String(160), nullable=False, unique=True),
    sa.Column(
      "model_version",
      sa.String(80),
      sa.ForeignKey("stock_selection_model_versions.model_version"),
      nullable=False,
    ),
    sa.Column("model_stage", sa.String(16), nullable=False),
    sa.Column("as_of_date", sa.Date(), nullable=False),
    sa.Column("target_date", sa.Date(), nullable=False),
    sa.Column("cutoff_at", sa.DateTime(), nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("indicator_version", sa.String(64), nullable=False),
    sa.Column("factor_set_version", sa.String(80), nullable=False),
    sa.Column("factor_set_hash", sa.String(64), nullable=False),
    sa.Column("label_version", sa.String(80), nullable=False),
    sa.Column("calibrator_version", sa.String(80), nullable=False),
    sa.Column("candidate_rule_version", sa.String(80), nullable=False),
    sa.Column("factor_snapshot_path", sa.String(512), nullable=True),
    sa.Column("factor_snapshot_sha256", sa.String(64), nullable=True),
    sa.Column("started_at", sa.DateTime(), nullable=False),
    sa.Column("completed_at", sa.DateTime(), nullable=True),
    sa.Column("eligible_count", sa.Integer(), nullable=False),
    sa.Column("prediction_count", sa.Integer(), nullable=False),
    sa.Column("candidate_count", sa.Integer(), nullable=False),
    sa.Column("error_code", sa.String(64), nullable=True),
    sa.Column("error_message", sa.String(512), nullable=True),
    sa.Column("warnings", sa.JSON(), nullable=False),
    *_timestamps(),
    sa.CheckConstraint(
      "model_stage IN ('ACTIVE','SHADOW')",
      name="ck_stock_prediction_run_model_stage",
    ),
    sa.CheckConstraint(
      "status IN ('RUNNING','SUCCESS','FAILED')",
      name="ck_stock_prediction_run_status",
    ),
    sa.CheckConstraint(
      "target_date > as_of_date", name="ck_stock_prediction_run_target"
    ),
    sa.CheckConstraint(
      "eligible_count >= 0 AND prediction_count >= 0 AND candidate_count >= 0",
      name="ck_stock_prediction_run_counts",
    ),
  )
  op.create_index(
    "ix_stock_prediction_runs_model_version", "stock_prediction_runs", ["model_version"]
  )
  op.create_index(
    "ix_stock_prediction_runs_as_of_date", "stock_prediction_runs", ["as_of_date"]
  )
  op.create_index(
    "ix_stock_prediction_runs_status", "stock_prediction_runs", ["status"]
  )
  op.create_index(
    "ix_stock_prediction_run_date_status",
    "stock_prediction_runs",
    ["as_of_date", "status"],
  )

  op.create_table(
    "stock_predictions",
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column(
      "prediction_run_id",
      sa.String(36),
      sa.ForeignKey("stock_prediction_runs.id"),
      nullable=False,
    ),
    sa.Column("as_of_date", sa.Date(), nullable=False),
    sa.Column("target_date", sa.Date(), nullable=False),
    sa.Column("instrument_code", sa.String(20), nullable=False),
    sa.Column(
      "model_version",
      sa.String(80),
      sa.ForeignKey("stock_selection_model_versions.model_version"),
      nullable=False,
    ),
    sa.Column("model_stage", sa.String(16), nullable=False),
    sa.Column("calibrated_probability", sa.Float(), nullable=False),
    sa.Column("raw_score", sa.Float(), nullable=False),
    sa.Column("logistic_probability", sa.Float(), nullable=False),
    sa.Column("lightgbm_probability", sa.Float(), nullable=False),
    sa.Column("rank", sa.Integer(), nullable=False),
    sa.Column("confidence", sa.Float(), nullable=False),
    sa.Column("factor_completeness", sa.Float(), nullable=False),
    sa.Column("ood_fit", sa.Float(), nullable=False),
    sa.Column("calibration_bin_index", sa.Integer(), nullable=False),
    sa.Column("calibration_bin_samples", sa.Integer(), nullable=False),
    sa.Column("calibration_bin_realized_rate", sa.Float(), nullable=True),
    sa.Column("eligible", sa.Boolean(), nullable=False),
    sa.Column("candidate_level", sa.String(1), nullable=True),
    sa.Column("reason_codes", sa.JSON(), nullable=False),
    sa.Column("risk_flags", sa.JSON(), nullable=False),
    *_timestamps(),
    sa.CheckConstraint(
      "calibrated_probability >= 0 AND calibrated_probability <= 1",
      name="ck_stock_prediction_probability",
    ),
    sa.CheckConstraint(
      "confidence >= 0 AND confidence <= 1",
      name="ck_stock_prediction_confidence",
    ),
    sa.CheckConstraint(
      "logistic_probability >= 0 AND logistic_probability <= 1 "
      "AND lightgbm_probability >= 0 AND lightgbm_probability <= 1",
      name="ck_stock_prediction_family_probabilities",
    ),
    sa.CheckConstraint(
      "factor_completeness >= 0 AND factor_completeness <= 1 "
      "AND ood_fit >= 0 AND ood_fit <= 1",
      name="ck_stock_prediction_quality",
    ),
    sa.CheckConstraint(
      "model_stage IN ('ACTIVE','SHADOW')",
      name="ck_stock_prediction_model_stage",
    ),
    sa.CheckConstraint(
      "rank >= 1 AND calibration_bin_index >= 0 AND calibration_bin_samples >= 0",
      name="ck_stock_prediction_rank_calibration",
    ),
    sa.CheckConstraint(
      "candidate_level IS NULL OR candidate_level IN ('A','B')",
      name="ck_stock_prediction_candidate_level",
    ),
    sa.UniqueConstraint(
      "prediction_run_id", "instrument_code", name="uq_stock_prediction_run_code"
    ),
  )
  for column in ("prediction_run_id", "as_of_date", "instrument_code", "model_version"):
    op.create_index(f"ix_stock_predictions_{column}", "stock_predictions", [column])
  op.create_index(
    "ix_stock_prediction_date_rank", "stock_predictions", ["as_of_date", "rank"]
  )

  op.create_table(
    "stock_candidate_rule_versions",
    sa.Column("rule_version", sa.String(80), primary_key=True),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("minimum_probability", sa.Float(), nullable=False),
    sa.Column("minimum_confidence", sa.Float(), nullable=False),
    sa.Column("minimum_ood_fit", sa.Float(), nullable=False),
    sa.Column("minimum_factor_completeness", sa.Float(), nullable=False),
    sa.Column("minimum_valid_history", sa.Integer(), nullable=False),
    sa.Column("level_a_size", sa.Integer(), nullable=False),
    sa.Column("level_b_size", sa.Integer(), nullable=False),
    sa.Column("rules", sa.JSON(), nullable=False),
    sa.Column("state_version", sa.Integer(), nullable=False),
    *_timestamps(),
    sa.CheckConstraint(
      "status IN ('ACTIVE','RETIRED')",
      name="ck_stock_candidate_rule_status",
    ),
    sa.CheckConstraint(
      "minimum_probability >= 0 AND minimum_probability <= 1 "
      "AND minimum_confidence >= 0 AND minimum_confidence <= 1 "
      "AND minimum_ood_fit >= 0 AND minimum_ood_fit <= 1 "
      "AND minimum_factor_completeness >= 0 "
      "AND minimum_factor_completeness <= 1",
      name="ck_stock_candidate_rule_thresholds",
    ),
    sa.CheckConstraint(
      "minimum_valid_history >= 252 AND level_a_size >= 1 AND level_b_size >= 1",
      name="ck_stock_candidate_rule_sizes",
    ),
    sa.CheckConstraint(
      "state_version >= 1", name="ck_stock_candidate_rule_state_version"
    ),
  )
  op.create_index(
    "ix_stock_candidate_rule_versions_status",
    "stock_candidate_rule_versions",
    ["status"],
  )
  op.create_index(
    "uq_stock_candidate_one_active_rule",
    "stock_candidate_rule_versions",
    ["status"],
    unique=True,
    postgresql_where=sa.text("status = 'ACTIVE'"),
    sqlite_where=sa.text("status = 'ACTIVE'"),
  )
  rules = sa.table(
    "stock_candidate_rule_versions",
    sa.column("rule_version", sa.String),
    sa.column("status", sa.String),
    sa.column("minimum_probability", sa.Float),
    sa.column("minimum_confidence", sa.Float),
    sa.column("minimum_ood_fit", sa.Float),
    sa.column("minimum_factor_completeness", sa.Float),
    sa.column("minimum_valid_history", sa.Integer),
    sa.column("level_a_size", sa.Integer),
    sa.column("level_b_size", sa.Integer),
    sa.column("rules", sa.JSON),
    sa.column("state_version", sa.Integer),
  )
  op.bulk_insert(
    rules,
    [
      {
        "rule_version": "next-day-selection-candidate-v1",
        "status": "ACTIVE",
        "minimum_probability": 0.6,
        "minimum_confidence": 0.6,
        "minimum_ood_fit": 0.8,
        "minimum_factor_completeness": 0.9,
        "minimum_valid_history": 252,
        "level_a_size": 20,
        "level_b_size": 30,
        "rules": {
          "exclude_st": True,
          "exclude_suspended": True,
          "exclude_delisting_risk": True,
          "critical_ood_blocks": True,
          "confidence_formula": "geometric-v1",
          "calibration_support_full_samples": 1000,
          "sort": ["probability_desc", "code_asc"],
        },
        "state_version": 1,
      }
    ],
  )
  op.create_foreign_key(
    "fk_stock_prediction_runs_candidate_rule_version",
    "stock_prediction_runs",
    "stock_candidate_rule_versions",
    ["candidate_rule_version"],
    ["rule_version"],
  )

  op.create_table(
    "stock_candidates",
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column(
      "prediction_run_id",
      sa.String(36),
      sa.ForeignKey("stock_prediction_runs.id"),
      nullable=False,
    ),
    sa.Column(
      "prediction_id",
      sa.String(36),
      sa.ForeignKey("stock_predictions.id"),
      nullable=False,
      unique=True,
    ),
    sa.Column("as_of_date", sa.Date(), nullable=False),
    sa.Column("target_date", sa.Date(), nullable=False),
    sa.Column("instrument_code", sa.String(20), nullable=False),
    sa.Column(
      "model_version",
      sa.String(80),
      sa.ForeignKey("stock_selection_model_versions.model_version"),
      nullable=False,
    ),
    sa.Column("model_stage", sa.String(16), nullable=False),
    sa.Column(
      "rule_version",
      sa.String(80),
      sa.ForeignKey("stock_candidate_rule_versions.rule_version"),
      nullable=False,
    ),
    sa.Column("candidate_level", sa.String(1), nullable=False),
    sa.Column("rank", sa.Integer(), nullable=False),
    sa.Column("calibrated_probability", sa.Float(), nullable=False),
    sa.Column("confidence", sa.Float(), nullable=False),
    sa.Column("reason_codes", sa.JSON(), nullable=False),
    sa.Column("risk_flags", sa.JSON(), nullable=False),
    *_timestamps(),
    sa.CheckConstraint(
      "candidate_level IN ('A','B')",
      name="ck_stock_candidate_level",
    ),
    sa.CheckConstraint(
      "model_stage IN ('ACTIVE','SHADOW')",
      name="ck_stock_candidate_model_stage",
    ),
    sa.CheckConstraint(
      "rank >= 1 AND calibrated_probability >= 0 AND calibrated_probability <= 1 "
      "AND confidence >= 0 AND confidence <= 1",
      name="ck_stock_candidate_rank_probability",
    ),
    sa.UniqueConstraint(
      "prediction_run_id", "instrument_code", name="uq_stock_candidate_run_code"
    ),
  )
  for column in ("prediction_run_id", "as_of_date", "instrument_code", "model_version"):
    op.create_index(f"ix_stock_candidates_{column}", "stock_candidates", [column])
  op.create_index(
    "ix_stock_candidate_date_level_rank",
    "stock_candidates",
    ["as_of_date", "candidate_level", "rank"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX schema downgrades are intentionally disabled")
