"""Authentication and account-authorization persistence models."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class AuthUser(Base, TimestampMixin):
  """A local QuantX API user. Password hashes are never exposed by API types."""

  __tablename__ = "auth_users"

  id = Column(String(36), primary_key=True, index=True)
  username = Column(String(80), nullable=False, unique=True, index=True)
  display_name = Column(String(120), nullable=False)
  password_hash = Column(String(256), nullable=False)
  is_active = Column(Boolean, nullable=False, default=True)
  permissions = Column(JSON, nullable=False, default=list)


class AuthUserAccountAccess(Base, TimestampMixin):
  """Explicit user-to-broker-account authorization."""

  __tablename__ = "auth_user_account_access"

  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="CASCADE"),
    primary_key=True,
  )
  account_id = Column(String(50), primary_key=True)
  is_default = Column(Boolean, nullable=False, default=False)


class AuthDeviceSession(Base, TimestampMixin):
  """Revocable device session with only a refresh-token digest persisted."""

  __tablename__ = "auth_device_sessions"
  __table_args__ = (
    Index("ix_auth_device_sessions_user_active", "user_id", "revoked_at"),
  )

  id = Column(String(36), primary_key=True, index=True)
  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  refresh_token_hash = Column(String(64), nullable=False, unique=True, index=True)
  expires_at = Column(DateTime, nullable=False)
  revoked_at = Column(DateTime, nullable=True)
  last_used_at = Column(DateTime, nullable=False)
  device_name = Column(String(120), nullable=True)


class AuthConsumedRefreshToken(Base):
  """Consumed refresh-token digests retained for replay detection."""

  __tablename__ = "auth_consumed_refresh_tokens"
  __table_args__ = (
    Index("ix_auth_consumed_refresh_tokens_expires_at", "expires_at"),
  )

  token_hash = Column(String(64), primary_key=True)
  device_session_id = Column(
    String(36),
    ForeignKey("auth_device_sessions.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  consumed_at = Column(DateTime, nullable=False)
  expires_at = Column(DateTime, nullable=False)


class AuthAuditEvent(Base):
  """Security audit metadata without credentials, tokens, or account IDs."""

  __tablename__ = "auth_audit_events"
  __table_args__ = (Index("ix_auth_audit_occurred_at", "occurred_at"),)

  id = Column(String(36), primary_key=True)
  event_type = Column(String(48), nullable=False, index=True)
  outcome = Column(String(24), nullable=False)
  reason_code = Column(String(64), nullable=True)
  user_id = Column(String(36), nullable=True, index=True)
  device_session_id = Column(String(36), nullable=True, index=True)
  subject_fingerprint = Column(String(64), nullable=True)
  request_id = Column(String(64), nullable=False)
  occurred_at = Column(DateTime, nullable=False)
