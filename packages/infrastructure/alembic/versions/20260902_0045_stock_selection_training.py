"""Persist certified datasets and the next-day training queue."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260902_0045"
down_revision = "20260901_0044"
branch_labels = None
depends_on = None


def _created_at() -> sa.Column:
  return sa.Column(
    "created_at",
    sa.DateTime(timezone=True),
    nullable=False,
    server_default=sa.func.now(),
  )


def upgrade() -> None:
  op.create_table(
    "stock_selection_dataset_versions",
    sa.Column("dataset_version", sa.String(120), primary_key=True),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("source_kind", sa.String(48), nullable=False),
    sa.Column("source_reference", sa.String(512), nullable=False),
    sa.Column("date_start", sa.Date(), nullable=False),
    sa.Column("date_end", sa.Date(), nullable=False),
    sa.Column("universe_spec", sa.JSON(), nullable=False),
    sa.Column("indicator_version", sa.String(80), nullable=False),
    sa.Column("factor_set_version", sa.String(80), nullable=False),
    sa.Column("factor_set_hash", sa.String(64), nullable=False),
    sa.Column("label_version", sa.String(80), nullable=False),
    sa.Column("manifest_sha256", sa.String(64), nullable=False),
    sa.Column("sample_count", sa.Integer(), nullable=False),
    sa.Column("stock_count", sa.Integer(), nullable=False),
    sa.Column("trading_day_count", sa.Integer(), nullable=False),
    sa.Column("quality_summary", sa.JSON(), nullable=False),
    _created_at(),
    sa.CheckConstraint(
      "status IN ('CERTIFIED','RETIRED')",
      name="ck_stock_selection_dataset_status",
    ),
    sa.CheckConstraint(
      "date_start <= date_end",
      name="ck_stock_selection_dataset_dates",
    ),
    sa.CheckConstraint(
      "length(manifest_sha256) = 64",
      name="ck_stock_selection_dataset_manifest_sha256",
    ),
    sa.CheckConstraint(
      "length(factor_set_hash) = 64",
      name="ck_stock_selection_dataset_factor_set_hash",
    ),
    sa.CheckConstraint(
      "sample_count >= 0 AND stock_count >= 0 AND trading_day_count >= 0",
      name="ck_stock_selection_dataset_counts",
    ),
    comment="次日上涨概率训练认证数据集版本与不可变证据",
  )
  op.create_index(
    "ix_stock_selection_dataset_versions_status",
    "stock_selection_dataset_versions",
    ["status"],
  )
  op.create_index(
    "ix_stock_selection_dataset_status_created",
    "stock_selection_dataset_versions",
    ["status", "created_at"],
  )

  op.create_table(
    "stock_selection_training_specs",
    sa.Column("spec_id", sa.String(36), primary_key=True),
    sa.Column(
      "dataset_version",
      sa.String(120),
      sa.ForeignKey(
        "stock_selection_dataset_versions.dataset_version", ondelete="RESTRICT"
      ),
      nullable=False,
    ),
    sa.Column("universe_spec", sa.JSON(), nullable=False),
    sa.Column("run_kind", sa.String(24), nullable=False),
    sa.Column("split_spec", sa.JSON(), nullable=False),
    sa.Column("model_spec", sa.JSON(), nullable=False),
    sa.Column("evaluation_spec", sa.JSON(), nullable=False),
    sa.Column("requested_backend", sa.String(16), nullable=False),
    sa.Column("resolved_backend", sa.String(32), nullable=False),
    sa.Column("random_seed", sa.Integer(), nullable=False),
    sa.Column("worker_batch_size", sa.Integer(), nullable=False),
    sa.Column("note", sa.String(500), nullable=False),
    sa.Column("spec_hash", sa.String(64), nullable=False),
    sa.Column("environment_requirement_hash", sa.String(64), nullable=False),
    sa.Column("coordinate_hash", sa.String(64), nullable=False),
    sa.Column("experiment_group_hash", sa.String(64), nullable=False),
    sa.Column("frozen_test_access_count", sa.Integer(), nullable=False),
    sa.Column("created_by", sa.String(64), nullable=False),
    _created_at(),
    sa.CheckConstraint(
      "run_kind IN ('DEVELOPMENT','FINAL_EVALUATION')",
      name="ck_stock_selection_training_spec_run_kind",
    ),
    sa.CheckConstraint(
      "requested_backend IN ('AUTO','CPU','GPU_REQUIRED')",
      name="ck_stock_selection_training_spec_requested_backend",
    ),
    sa.CheckConstraint(
      "resolved_backend IN ('CPU','LIGHTGBM_OPENCL_GPU')",
      name="ck_stock_selection_training_spec_resolved_backend",
    ),
    sa.CheckConstraint(
      "worker_batch_size >= 1 AND worker_batch_size <= 1000",
      name="ck_stock_selection_training_spec_batch_size",
    ),
    sa.CheckConstraint(
      "length(spec_hash) = 64 AND length(environment_requirement_hash) = 64 "
      "AND length(coordinate_hash) = 64 AND length(experiment_group_hash) = 64",
      name="ck_stock_selection_training_spec_hash_lengths",
    ),
    sa.CheckConstraint(
      "frozen_test_access_count >= 0",
      name="ck_stock_selection_training_spec_frozen_access_count",
    ),
    comment="次日上涨概率训练不可变配置快照",
  )
  op.create_index(
    "ix_stock_selection_training_specs_dataset_version",
    "stock_selection_training_specs",
    ["dataset_version"],
  )
  op.create_index(
    "ix_stock_selection_training_specs_dataset_kind",
    "stock_selection_training_specs",
    ["dataset_version", "run_kind"],
  )
  op.create_index(
    "ix_stock_selection_training_specs_experiment_group",
    "stock_selection_training_specs",
    ["experiment_group_hash"],
  )

  op.create_table(
    "stock_selection_training_runs",
    sa.Column("run_id", sa.String(36), primary_key=True),
    sa.Column("run_key", sa.String(160), nullable=True, unique=True),
    sa.Column(
      "spec_id",
      sa.String(36),
      sa.ForeignKey("stock_selection_training_specs.spec_id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("run_kind", sa.String(24), nullable=False),
    sa.Column(
      "parent_run_id",
      sa.String(36),
      sa.ForeignKey("stock_selection_training_runs.run_id", ondelete="RESTRICT"),
      nullable=True,
    ),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("phase", sa.String(24), nullable=False),
    sa.Column("completed_units", sa.Integer(), nullable=False),
    sa.Column("total_units", sa.Integer(), nullable=False),
    sa.Column("prefect_flow_run_id", sa.String(128), nullable=True),
    sa.Column(
      "requested_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cancel_idempotency_key", sa.String(160), nullable=True),
    sa.Column("artifact_manifest_sha256", sa.String(64), nullable=True),
    sa.Column("environment_evidence", sa.JSON(), nullable=False),
    sa.Column("metrics_summary", sa.JSON(), nullable=False),
    sa.Column("gate_summary", sa.JSON(), nullable=False),
    sa.Column("error_code", sa.String(64), nullable=True),
    sa.Column("error_message", sa.String(512), nullable=True),
    sa.Column("state_version", sa.Integer(), nullable=False),
    sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
    sa.CheckConstraint(
      "run_kind IN ('DEVELOPMENT','FINAL_EVALUATION')",
      name="ck_stock_selection_training_run_kind",
    ),
    sa.CheckConstraint(
      "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
      name="ck_stock_selection_training_run_status",
    ),
    sa.CheckConstraint(
      "phase IN ('PREFLIGHT','DATASET_BUILD','WALK_FORWARD','FINAL_FIT',"
      "'CALIBRATION','FROZEN_TEST','ARTIFACT_PUBLISH')",
      name="ck_stock_selection_training_run_phase",
    ),
    sa.CheckConstraint(
      "completed_units >= 0 AND total_units >= 0 AND completed_units <= total_units",
      name="ck_stock_selection_training_run_units",
    ),
    sa.CheckConstraint(
      "((run_kind = 'FINAL_EVALUATION' AND parent_run_id IS NOT NULL) OR "
      "(run_kind = 'DEVELOPMENT' AND parent_run_id IS NULL))",
      name="ck_stock_selection_training_run_parent_kind",
    ),
    sa.CheckConstraint(
      "state_version >= 1",
      name="ck_stock_selection_training_run_state_version",
    ),
    comment="次日上涨概率训练排队、进度、取消与产物状态真源",
  )
  op.create_index(
    "ix_stock_selection_training_runs_spec_id",
    "stock_selection_training_runs",
    ["spec_id"],
  )
  op.create_index(
    "ix_stock_selection_training_runs_status",
    "stock_selection_training_runs",
    ["status"],
  )
  op.create_index(
    "ix_stock_selection_training_runs_status_requested",
    "stock_selection_training_runs",
    ["status", "requested_at"],
  )
  op.create_index(
    "ix_stock_selection_training_runs_parent",
    "stock_selection_training_runs",
    ["parent_run_id"],
  )
  op.create_index(
    "uq_stock_selection_training_runs_one_running",
    "stock_selection_training_runs",
    ["status"],
    unique=True,
    postgresql_where=sa.text("status = 'RUNNING'"),
    sqlite_where=sa.text("status = 'RUNNING'"),
  )


def downgrade() -> None:
  raise RuntimeError("QuantX schema downgrades are intentionally disabled")
