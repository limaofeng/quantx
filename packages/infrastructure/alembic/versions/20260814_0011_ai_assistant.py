"""Add durable product-facing AI assistant state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260814_0011"
down_revision = "20260813_0010"
branch_labels = None
depends_on = None

ASSISTANT_TABLES = frozenset(
  {
    "ai_assistant_threads",
    "ai_assistant_messages",
    "ai_assistant_session_items",
    "ai_assistant_runs",
    "ai_assistant_events",
    "ai_assistant_tool_calls",
    "ai_assistant_deletion_audits",
  }
)


def upgrade() -> None:
  existing = set(inspect(op.get_bind()).get_table_names())
  existing_assistant_tables = ASSISTANT_TABLES & existing
  if existing_assistant_tables == ASSISTANT_TABLES:
    # Development and rolling deployments may import the additive ORM models
    # before Alembic runs. Base.metadata.create_all(checkfirst=True) then creates
    # the complete schema, which this revision can safely adopt.
    return
  if existing_assistant_tables:
    missing = ", ".join(sorted(ASSISTANT_TABLES - existing_assistant_tables))
    raise RuntimeError(
      "Partial AI assistant schema detected; missing tables: " + missing
    )

  op.create_table(
    "ai_assistant_threads",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "user_id",
      sa.String(length=36),
      sa.ForeignKey("auth_users.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("account_id", sa.String(length=50), nullable=True),
    sa.Column(
      "agent_id",
      sa.String(length=64),
      nullable=False,
      server_default="research_assistant",
    ),
    sa.Column("title", sa.String(length=160), nullable=False),
    sa.Column(
      "external_search_enabled",
      sa.Boolean(),
      nullable=False,
      server_default=sa.false(),
    ),
    sa.Column("status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
    sa.Column("last_activity_at", sa.DateTime(), nullable=False),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
  )
  op.create_index(
    "ix_ai_assistant_threads_user_id", "ai_assistant_threads", ["user_id"]
  )
  op.create_index(
    "ix_ai_assistant_threads_account_id", "ai_assistant_threads", ["account_id"]
  )
  op.create_index(
    "ix_ai_assistant_thread_user_activity",
    "ai_assistant_threads",
    ["user_id", "last_activity_at"],
  )

  op.create_table(
    "ai_assistant_messages",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "thread_id",
      sa.String(length=36),
      sa.ForeignKey("ai_assistant_threads.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("run_id", sa.String(length=36), nullable=True),
    sa.Column("client_message_id", sa.String(length=128), nullable=True),
    sa.Column("sequence", sa.BigInteger(), nullable=False),
    sa.Column("role", sa.String(length=24), nullable=False),
    sa.Column("content_blocks", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
      "thread_id", "sequence", name="uq_ai_assistant_message_sequence"
    ),
    sa.UniqueConstraint(
      "thread_id", "client_message_id", name="uq_ai_assistant_client_message"
    ),
  )
  op.create_index(
    "ix_ai_assistant_messages_thread_id", "ai_assistant_messages", ["thread_id"]
  )
  op.create_index(
    "ix_ai_assistant_messages_run_id", "ai_assistant_messages", ["run_id"]
  )
  op.create_index(
    "ix_ai_assistant_message_thread_created",
    "ai_assistant_messages",
    ["thread_id", "created_at"],
  )

  op.create_table(
    "ai_assistant_session_items",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "thread_id",
      sa.String(length=36),
      sa.ForeignKey("ai_assistant_threads.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("sequence", sa.BigInteger(), nullable=False),
    sa.Column("item", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
      "thread_id", "sequence", name="uq_ai_assistant_session_sequence"
    ),
  )
  op.create_index(
    "ix_ai_assistant_session_thread_sequence",
    "ai_assistant_session_items",
    ["thread_id", "sequence"],
  )

  op.create_table(
    "ai_assistant_runs",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "thread_id",
      sa.String(length=36),
      sa.ForeignKey("ai_assistant_threads.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column(
      "user_message_id",
      sa.String(length=36),
      sa.ForeignKey("ai_assistant_messages.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("request_id", sa.String(length=64), nullable=False),
    sa.Column("status", sa.String(length=24), nullable=False),
    sa.Column("model", sa.String(length=80), nullable=False),
    sa.Column("prompt_version", sa.String(length=32), nullable=False),
    sa.Column("account_id", sa.String(length=50), nullable=True),
    sa.Column("context_refs", sa.JSON(), nullable=False),
    sa.Column("external_search_enabled", sa.Boolean(), nullable=False),
    sa.Column("lease_owner", sa.String(length=96), nullable=True),
    sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    sa.Column("resume_state", sa.JSON(), nullable=True),
    sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
    sa.Column("started_at", sa.DateTime(), nullable=True),
    sa.Column("finished_at", sa.DateTime(), nullable=True),
    sa.Column("error_code", sa.String(length=64), nullable=True),
    sa.Column("error_message", sa.String(length=512), nullable=True),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
  )
  op.create_index("ix_ai_assistant_runs_thread_id", "ai_assistant_runs", ["thread_id"])
  op.create_index(
    "ix_ai_assistant_run_queue", "ai_assistant_runs", ["status", "created_at"]
  )
  op.create_index(
    "ix_ai_assistant_run_lease", "ai_assistant_runs", ["status", "lease_expires_at"]
  )
  op.create_index(
    "uq_ai_assistant_active_run",
    "ai_assistant_runs",
    ["thread_id"],
    unique=True,
    postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL')"),
  )

  op.create_table(
    "ai_assistant_events",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "thread_id",
      sa.String(length=36),
      sa.ForeignKey("ai_assistant_threads.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("run_id", sa.String(length=36), nullable=False),
    sa.Column("sequence", sa.BigInteger(), nullable=False),
    sa.Column("event_type", sa.String(length=40), nullable=False),
    sa.Column("payload", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint("thread_id", "sequence", name="uq_ai_assistant_event_sequence"),
  )
  op.create_index("ix_ai_assistant_events_run_id", "ai_assistant_events", ["run_id"])
  op.create_index(
    "ix_ai_assistant_event_thread_sequence",
    "ai_assistant_events",
    ["thread_id", "sequence"],
  )

  op.create_table(
    "ai_assistant_tool_calls",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "run_id",
      sa.String(length=36),
      sa.ForeignKey("ai_assistant_runs.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("call_id", sa.String(length=128), nullable=False),
    sa.Column("tool_name", sa.String(length=80), nullable=False),
    sa.Column("tool_version", sa.String(length=24), nullable=False),
    sa.Column("risk_level", sa.String(length=32), nullable=False),
    sa.Column("arguments", sa.JSON(), nullable=False),
    sa.Column("result", sa.JSON(), nullable=True),
    sa.Column("result_summary", sa.String(length=512), nullable=True),
    sa.Column("status", sa.String(length=32), nullable=False),
    sa.Column("approval_status", sa.String(length=24), nullable=False),
    sa.Column("idempotency_key", sa.String(length=160), nullable=True),
    sa.Column("error_code", sa.String(length=64), nullable=True),
    sa.Column("error_message", sa.String(length=512), nullable=True),
    sa.Column("started_at", sa.DateTime(), nullable=True),
    sa.Column("finished_at", sa.DateTime(), nullable=True),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("run_id", "call_id", name="uq_ai_assistant_tool_call"),
    sa.UniqueConstraint("idempotency_key", name="uq_ai_assistant_tool_idempotency"),
  )
  op.create_index(
    "ix_ai_assistant_tool_run_created",
    "ai_assistant_tool_calls",
    ["run_id", "created_at"],
  )

  op.create_table(
    "ai_assistant_deletion_audits",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("user_id", sa.String(length=36), nullable=False),
    sa.Column("thread_fingerprint", sa.String(length=64), nullable=False),
    sa.Column("request_id", sa.String(length=64), nullable=False),
    sa.Column("occurred_at", sa.DateTime(), nullable=False),
  )
  op.create_index(
    "ix_ai_assistant_deletion_audits_user_id",
    "ai_assistant_deletion_audits",
    ["user_id"],
  )
  op.create_index(
    "ix_ai_assistant_deletion_occurred",
    "ai_assistant_deletion_audits",
    ["occurred_at"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
