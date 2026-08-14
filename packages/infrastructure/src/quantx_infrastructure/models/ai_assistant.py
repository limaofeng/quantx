"""Durable product-facing AI assistant state."""

from sqlalchemy import (
  JSON,
  BigInteger,
  Boolean,
  Column,
  DateTime,
  ForeignKey,
  Index,
  Integer,
  String,
  UniqueConstraint,
  text,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class AiAssistantThread(Base, TimestampMixin):
  __tablename__ = "ai_assistant_threads"
  __table_args__ = (
    Index("ix_ai_assistant_thread_user_activity", "user_id", "last_activity_at"),
  )

  id = Column(String(36), primary_key=True)
  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  account_id = Column(String(50), nullable=True, index=True)
  agent_id = Column(String(64), nullable=False, default="research_assistant")
  title = Column(String(160), nullable=False, default="新对话")
  external_search_enabled = Column(Boolean, nullable=False, default=False)
  status = Column(String(24), nullable=False, default="ACTIVE")
  last_activity_at = Column(DateTime, nullable=False)


class AiAssistantMessage(Base):
  __tablename__ = "ai_assistant_messages"
  __table_args__ = (
    UniqueConstraint("thread_id", "sequence", name="uq_ai_assistant_message_sequence"),
    UniqueConstraint(
      "thread_id",
      "client_message_id",
      name="uq_ai_assistant_client_message",
    ),
    Index("ix_ai_assistant_message_thread_created", "thread_id", "created_at"),
  )

  id = Column(String(36), primary_key=True)
  thread_id = Column(
    String(36),
    ForeignKey("ai_assistant_threads.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  run_id = Column(String(36), nullable=True, index=True)
  client_message_id = Column(String(128), nullable=True)
  sequence = Column(BigInteger, nullable=False)
  role = Column(String(24), nullable=False)
  content_blocks = Column(JSON, nullable=False, default=list)
  created_at = Column(DateTime, nullable=False)


class AiAssistantSessionItem(Base):
  __tablename__ = "ai_assistant_session_items"
  __table_args__ = (
    UniqueConstraint("thread_id", "sequence", name="uq_ai_assistant_session_sequence"),
    Index("ix_ai_assistant_session_thread_sequence", "thread_id", "sequence"),
  )

  id = Column(String(36), primary_key=True)
  thread_id = Column(
    String(36),
    ForeignKey("ai_assistant_threads.id", ondelete="CASCADE"),
    nullable=False,
  )
  sequence = Column(BigInteger, nullable=False)
  item = Column(JSON, nullable=False)
  created_at = Column(DateTime, nullable=False)


class AiAssistantRun(Base, TimestampMixin):
  __tablename__ = "ai_assistant_runs"
  __table_args__ = (
    Index("ix_ai_assistant_run_queue", "status", "created_at"),
    Index("ix_ai_assistant_run_lease", "status", "lease_expires_at"),
    Index(
      "uq_ai_assistant_active_run",
      "thread_id",
      unique=True,
      postgresql_where=text("status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL')"),
    ),
  )

  id = Column(String(36), primary_key=True)
  thread_id = Column(
    String(36),
    ForeignKey("ai_assistant_threads.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  user_message_id = Column(
    String(36),
    ForeignKey("ai_assistant_messages.id", ondelete="CASCADE"),
    nullable=False,
  )
  request_id = Column(String(64), nullable=False)
  status = Column(String(24), nullable=False, default="QUEUED")
  model = Column(String(80), nullable=False)
  prompt_version = Column(String(32), nullable=False, default="v1")
  account_id = Column(String(50), nullable=True)
  context_refs = Column(JSON, nullable=False, default=list)
  external_search_enabled = Column(Boolean, nullable=False, default=False)
  lease_owner = Column(String(96), nullable=True)
  lease_expires_at = Column(DateTime, nullable=True)
  resume_state = Column(JSON, nullable=True)
  input_tokens = Column(Integer, nullable=False, default=0)
  output_tokens = Column(Integer, nullable=False, default=0)
  request_count = Column(Integer, nullable=False, default=0)
  tool_call_count = Column(Integer, nullable=False, default=0)
  cancel_requested_at = Column(DateTime, nullable=True)
  started_at = Column(DateTime, nullable=True)
  finished_at = Column(DateTime, nullable=True)
  error_code = Column(String(64), nullable=True)
  error_message = Column(String(512), nullable=True)


class AiAssistantEvent(Base):
  __tablename__ = "ai_assistant_events"
  __table_args__ = (
    UniqueConstraint("thread_id", "sequence", name="uq_ai_assistant_event_sequence"),
    Index("ix_ai_assistant_event_thread_sequence", "thread_id", "sequence"),
  )

  id = Column(String(36), primary_key=True)
  thread_id = Column(
    String(36),
    ForeignKey("ai_assistant_threads.id", ondelete="CASCADE"),
    nullable=False,
  )
  run_id = Column(String(36), nullable=False, index=True)
  sequence = Column(BigInteger, nullable=False)
  event_type = Column(String(40), nullable=False)
  payload = Column(JSON, nullable=False, default=dict)
  created_at = Column(DateTime, nullable=False)


class AiAssistantToolCall(Base, TimestampMixin):
  __tablename__ = "ai_assistant_tool_calls"
  __table_args__ = (
    UniqueConstraint("run_id", "call_id", name="uq_ai_assistant_tool_call"),
    UniqueConstraint("idempotency_key", name="uq_ai_assistant_tool_idempotency"),
    Index("ix_ai_assistant_tool_run_created", "run_id", "created_at"),
  )

  id = Column(String(36), primary_key=True)
  run_id = Column(
    String(36),
    ForeignKey("ai_assistant_runs.id", ondelete="CASCADE"),
    nullable=False,
  )
  call_id = Column(String(128), nullable=False)
  tool_name = Column(String(80), nullable=False)
  tool_version = Column(String(24), nullable=False)
  risk_level = Column(String(32), nullable=False)
  arguments = Column(JSON, nullable=False, default=dict)
  result = Column(JSON, nullable=True)
  result_summary = Column(String(512), nullable=True)
  status = Column(String(32), nullable=False, default="PENDING")
  approval_status = Column(String(24), nullable=False, default="NOT_REQUIRED")
  idempotency_key = Column(String(160), nullable=True)
  error_code = Column(String(64), nullable=True)
  error_message = Column(String(512), nullable=True)
  started_at = Column(DateTime, nullable=True)
  finished_at = Column(DateTime, nullable=True)


class AiAssistantDeletionAudit(Base):
  __tablename__ = "ai_assistant_deletion_audits"
  __table_args__ = (Index("ix_ai_assistant_deletion_occurred", "occurred_at"),)

  id = Column(String(36), primary_key=True)
  user_id = Column(String(36), nullable=False, index=True)
  thread_fingerprint = Column(String(64), nullable=False)
  request_id = Column(String(64), nullable=False)
  occurred_at = Column(DateTime, nullable=False)
