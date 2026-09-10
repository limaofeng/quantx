"""Independent T model registry and authorization history."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0086"
down_revision = "20260910_0085"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "t_model_versions",
    sa.Column("model_id", sa.String(80), primary_key=True),
    sa.Column("model_version", sa.String(80), primary_key=True),
    sa.Column("run_key", sa.String(160), nullable=False, unique=True),
    sa.Column("artifact_sha256", sa.String(64), nullable=False),
    sa.Column("policy_compatibility_hash", sa.String(64), nullable=False),
    sa.Column("gate_conclusion", sa.String(24), nullable=False),
    sa.Column("evidence", sa.JSON, nullable=False),
    sa.Column("registration_hash", sa.String(64), nullable=False),
    sa.Column("registry_stage", sa.String(16), nullable=False),
    sa.Column("authorization_revision", sa.Integer, nullable=False),
    sa.CheckConstraint("registry_stage IN ('CANDIDATE','SHADOW','ACTIVE','SUSPENDED','RETIRED')", name="ck_t_model_stage"),
    sa.CheckConstraint("gate_conclusion IN ('SHADOW_ELIGIBLE','ACTIVE_ELIGIBLE')", name="ck_t_model_gate"),
    sa.CheckConstraint("registry_stage <> 'ACTIVE' OR gate_conclusion = 'ACTIVE_ELIGIBLE'", name="ck_t_model_active_gate"),
    sa.CheckConstraint("authorization_revision >= 1", name="ck_t_model_revision"),
    comment="做 T 独立模型登记证据与当前授权",
  )
  op.create_index("uq_t_model_one_active", "t_model_versions", ["registry_stage"], unique=True,
    postgresql_where=sa.text("registry_stage = 'ACTIVE'"), sqlite_where=sa.text("registry_stage = 'ACTIVE'"))
  op.create_table(
    "t_model_registry_events",
    sa.Column("model_id", sa.String(80), primary_key=True),
    sa.Column("model_version", sa.String(80), primary_key=True),
    sa.Column("authorization_revision", sa.Integer, primary_key=True),
    sa.Column("previous_stage", sa.String(16), nullable=True),
    sa.Column("registry_stage", sa.String(16), nullable=False),
    sa.Column("actor_id", sa.String(64), nullable=False),
    sa.Column("reason", sa.String(256), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("registration_hash", sa.String(64), nullable=False),
    sa.ForeignKeyConstraint(["model_id", "model_version"], ["t_model_versions.model_id", "t_model_versions.model_version"]),
    sa.CheckConstraint("authorization_revision >= 1", name="ck_t_model_event_revision"),
    comment="做 T 模型授权阶段变更审计",
  )


def downgrade():
  op.drop_table("t_model_registry_events")
  op.drop_index("uq_t_model_one_active", table_name="t_model_versions")
  op.drop_table("t_model_versions")
