"""Private iOS push registrations and opaque notification routing records."""

from sqlalchemy import (
  Boolean,
  CheckConstraint,
  Column,
  DateTime,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
  text,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin

PUSH_ENVIRONMENTS = ("SANDBOX", "PRODUCTION")
PUSH_CATEGORIES = (
  "ACTION_REQUIRED",
  "ORDER_UPDATE",
  "RISK_SAFETY",
  "AUTOMATION_ERROR",
  "CONNECTION_DATA",
)
NOTIFICATION_ROUTE_TYPES = (
  "today.action",
  "trading.orders",
  "trading.safety",
  "quant.workspace",
  "system.status",
)
NOTIFICATION_OUTBOX_STATUSES = (
  "PENDING",
  "SENT",
  "RETRY",
  "FAILED",
  "DISCARDED",
)


def _quoted(values: tuple[str, ...]) -> str:
  return ", ".join(f"'{value}'" for value in values)


class IosPushRegistration(Base, TimestampMixin):
  """One encrypted APNs token bound to an authenticated app installation."""

  __tablename__ = "ios_push_registrations"
  __table_args__ = (
    CheckConstraint(
      f"apns_environment IN ({_quoted(PUSH_ENVIRONMENTS)})",
      name="ck_ios_push_registration_environment",
    ),
    CheckConstraint(
      "invalidated_at IS NOT NULL OR token_ciphertext IS NOT NULL",
      name="ck_ios_push_registration_active_token",
    ),
    UniqueConstraint(
      "user_id",
      "app_bundle_id",
      "apns_environment",
      "device_install_id",
      name="uq_ios_push_registration_install",
    ),
    Index(
      "uq_ios_push_registration_active_session",
      "device_session_id",
      "app_bundle_id",
      "apns_environment",
      unique=True,
      postgresql_where=text("invalidated_at IS NULL"),
      sqlite_where=text("invalidated_at IS NULL"),
    ),
    Index(
      "ix_ios_push_registration_token_fingerprint",
      "token_fingerprint",
    ),
    Index(
      "ix_ios_push_registration_account_active",
      "account_id",
      "invalidated_at",
    ),
  )

  id = Column(String(36), primary_key=True)
  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="CASCADE"),
    nullable=False,
  )
  device_session_id = Column(
    String(36),
    ForeignKey("auth_device_sessions.id", ondelete="CASCADE"),
    nullable=False,
  )
  account_id = Column(String(50), nullable=False)
  device_install_id = Column(String(36), nullable=False)
  app_bundle_id = Column(String(255), nullable=False)
  app_version = Column(String(64), nullable=False)
  apns_environment = Column(String(16), nullable=False)
  # Ciphertext is intentionally opaque to generic repositories and never
  # returned through GraphQL. The keyed fingerprint supports safe rotation
  # checks without retaining another plaintext representation.
  token_ciphertext = Column(Text, nullable=True)
  token_fingerprint = Column(String(64), nullable=False)
  registered_at = Column(DateTime, nullable=False)
  last_seen_at = Column(DateTime, nullable=False)
  invalidated_at = Column(DateTime, nullable=True)


class IosPushCategoryPreference(Base, TimestampMixin):
  """Per-registration opt-in state; in-app safety events remain authoritative."""

  __tablename__ = "ios_push_category_preferences"
  __table_args__ = (
    CheckConstraint(
      f"category IN ({_quoted(PUSH_CATEGORIES)})",
      name="ck_ios_push_preference_category",
    ),
  )

  registration_id = Column(
    String(36),
    ForeignKey("ios_push_registrations.id", ondelete="CASCADE"),
    primary_key=True,
  )
  category = Column(String(32), primary_key=True)
  enabled = Column(Boolean, nullable=False)


class IosNotificationEvent(Base, TimestampMixin):
  """Opaque route metadata resolved only after authenticating the same session."""

  __tablename__ = "ios_notification_events"
  __table_args__ = (
    CheckConstraint(
      f"category IN ({_quoted(PUSH_CATEGORIES)})",
      name="ck_ios_notification_event_category",
    ),
    CheckConstraint(
      f"route_type IN ({_quoted(NOTIFICATION_ROUTE_TYPES)})",
      name="ck_ios_notification_event_route",
    ),
    Index(
      "ix_ios_notification_event_session_expiry",
      "device_session_id",
      "expires_at",
    ),
    Index(
      "ix_ios_notification_event_account_occurred",
      "account_id",
      "occurred_at",
    ),
  )

  id = Column(String(36), primary_key=True)
  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="CASCADE"),
    nullable=False,
  )
  device_session_id = Column(
    String(36),
    ForeignKey("auth_device_sessions.id", ondelete="CASCADE"),
    nullable=False,
  )
  account_id = Column(String(50), nullable=False)
  category = Column(String(32), nullable=False)
  route_type = Column(String(40), nullable=False)
  occurred_at = Column(DateTime, nullable=False)
  expires_at = Column(DateTime, nullable=False)


class IosBusinessNotificationReceipt(Base):
  """Global receipt proving that one durable business event was projected."""

  __tablename__ = "ios_business_notification_receipts"
  __table_args__ = (
    CheckConstraint(
      f"category IN ({_quoted(PUSH_CATEGORIES)})",
      name="ck_ios_business_notification_receipt_category",
    ),
    CheckConstraint(
      "queued_event_count >= 0",
      name="ck_ios_business_notification_receipt_queued_count",
    ),
    UniqueConstraint(
      "source_kind",
      "source_event_id",
      name="uq_ios_business_notification_receipt_source",
    ),
    Index(
      "ix_ios_business_notification_receipt_account_projected",
      "account_id",
      "projected_at",
    ),
  )

  # This is a server-keyed HMAC. The controlled kind and technical event ID
  # support starvation-free scans; order, symbol, amount, and payload details
  # are deliberately never copied into the notification persistence boundary.
  source_event_key_hash = Column(String(64), primary_key=True)
  source_kind = Column(String(48), nullable=False)
  source_event_id = Column(String(128), nullable=False)
  account_id = Column(String(50), nullable=False)
  category = Column(String(32), nullable=False)
  occurred_at = Column(DateTime, nullable=False)
  expires_at = Column(DateTime, nullable=False)
  projected_at = Column(DateTime, nullable=False)
  queued_event_count = Column(Integer, nullable=False, default=0)


class IosNotificationOutbox(Base, TimestampMixin):
  """Delivery intent without device tokens or business payload details."""

  __tablename__ = "ios_notification_outbox"
  __table_args__ = (
    CheckConstraint(
      f"status IN ({_quoted(NOTIFICATION_OUTBOX_STATUSES)})",
      name="ck_ios_notification_outbox_status",
    ),
    CheckConstraint(
      "attempt_count >= 0",
      name="ck_ios_notification_outbox_attempt_count",
    ),
    UniqueConstraint(
      "event_id",
      "registration_id",
      name="uq_ios_notification_outbox_event_registration",
    ),
    Index(
      "ix_ios_notification_outbox_delivery",
      "status",
      "available_at",
    ),
  )

  id = Column(String(36), primary_key=True)
  event_id = Column(
    String(36),
    ForeignKey("ios_notification_events.id", ondelete="CASCADE"),
    nullable=False,
  )
  registration_id = Column(
    String(36),
    ForeignKey("ios_push_registrations.id", ondelete="CASCADE"),
    nullable=False,
  )
  status = Column(String(16), nullable=False, default="PENDING")
  attempt_count = Column(Integer, nullable=False, default=0)
  available_at = Column(DateTime, nullable=False)
  sent_at = Column(DateTime, nullable=True)
  apns_request_id = Column(String(36), nullable=True)
  last_error_code = Column(String(64), nullable=True)


__all__ = [
  "IosBusinessNotificationReceipt",
  "IosNotificationEvent",
  "IosNotificationOutbox",
  "IosPushCategoryPreference",
  "IosPushRegistration",
  "NOTIFICATION_OUTBOX_STATUSES",
  "NOTIFICATION_ROUTE_TYPES",
  "PUSH_CATEGORIES",
  "PUSH_ENVIRONMENTS",
]
