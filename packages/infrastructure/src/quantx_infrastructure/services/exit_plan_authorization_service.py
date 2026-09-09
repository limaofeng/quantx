"""Exact, revocable authorization envelopes for automatic live exits.

The historical ``auto_exit_authorized`` boolean is kept as a compatibility
projection only.  A live plan is authorized exclusively when the exact plan,
position and competing-sell snapshot still matches the durable envelope
created by a device-bound confirmation challenge.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Mapping, Optional

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerType
from quantx_domain.clock import utcnow
from quantx_domain.trading.exit_plan import ExitPlanTemplate
from quantx_domain.trading.market_rules import AShareMarketRules
from quantx_domain.trading.t_order_policy import TExitOrderPolicy
from sqlalchemy import select

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  RESERVING_EXIT_PLAN_STATUSES,
)
from quantx_infrastructure.services.exit_plan_scope_lock import (
  LockedExitPlanScope,
  lock_exit_plan_scope,
  lock_exit_plan_scope_for_plan,
)

AUTO_EXIT_AUTHORIZATION_LIFETIME = timedelta(days=7)
REQUIRED_AUTO_EXIT_SCOPES = frozenset({"liquidation:control", "trade:approve"})
ACTIVE_PENDING_SELL_STATUSES = frozenset(
  {"QUEUED", "PENDING", "SUBMITTED", "REPORTED", "PARTIAL_FILLED"}
)
AUTHORIZABLE_PLAN_STATUSES = frozenset({"ACTIVE", "PARTIALLY_EXITED"})
T_TRADE_ENTRY_APPROVAL_ACTION = "T_TRADE_ENTRY_APPROVAL"
EXIT_PLAN_SELL_APPROVAL_ACTION = "EXIT_PLAN_SELL_APPROVAL"
T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY = "t_trade_exit_authorization_v1"
_T_TRADE_EXIT_AUTHORIZATION_SCHEMA_VERSION = 1
_T_ASSISTANT_EXIT_AUTHORIZATION_SCHEMA_VERSION = 2
_DEFAULT_AUTH_SECRET = "change-this-secret-key"


@dataclass(frozen=True)
class ExitPlanAuthorizationSnapshot:
  """Stable safety facts covered by one automatic-exit authorization."""

  subject: dict[str, Any]
  fingerprint: str
  position_updated_at: Optional[datetime]
  has_pending_sell: bool


@dataclass(frozen=True)
class ExitPlanAuthorizationValidation:
  valid: bool
  code: str
  message: str
  fingerprint: Optional[str] = None
  authorization_user_id: Optional[str] = None
  config_version: Optional[int] = None
  challenge_id: Optional[str] = None
  device_session_id: Optional[str] = None
  authorized_at: Optional[datetime] = None
  authorization_expires_at: Optional[datetime] = None


@dataclass(frozen=True)
class TTradeEntryExitAuthorizationEnvelope:
  """Immutable exit authority disclosed by one T-entry confirmation."""

  subject: dict[str, Any]
  fingerprint: str

  def to_dict(self) -> dict[str, Any]:
    return {
      "subject": dict(self.subject),
      "fingerprint": self.fingerprint,
    }


@dataclass(frozen=True)
class TTradeExitAuthorizationDerivation:
  """Result of deriving exact exit authority from an executed T entry."""

  valid: bool
  code: str
  message: str
  authorization_expires_at: Optional[datetime] = None
  fingerprint: Optional[str] = None
  authorization_user_id: Optional[str] = None
  config_version: Optional[int] = None
  challenge_id: Optional[str] = None


def authorization_expiry_for_challenge(expires_at: datetime) -> datetime:
  """Return the server-owned authorization expiry bound to a challenge."""

  return time_utils.to_shanghai(expires_at) + AUTO_EXIT_AUTHORIZATION_LIFETIME


def _version_token(value: Optional[datetime]) -> Optional[str]:
  return value.isoformat(timespec="microseconds") if value is not None else None


def _canonical_fingerprint(value: dict[str, Any]) -> str:
  # ``status`` temporarily becomes EXIT_PENDING as soon as Engine reserves an
  # intent, before the broker command is queued.  That state-machine detail is
  # shown in the preview and rechecked during confirmation, but is not part of
  # the durable authorization scope; otherwise every legitimate trigger would
  # invalidate itself before it could reach the atomic enqueue gate.
  scope = {
    **value,
    "plan": dict(value.get("plan") or {}),
  }
  scope["plan"].pop("status", None)
  encoded = json.dumps(
    scope,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _sha256_fingerprint(value: Mapping[str, Any]) -> str:
  encoded = json.dumps(
    dict(value),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def trade_confirmation_payload_fingerprint(payload: Mapping[str, Any]) -> str:
  """Return the HMAC used by durable trade-confirmation challenges.

  This intentionally matches the API challenge signer while keeping Engine
  authorization validation out of the API package dependency graph.
  """

  secret = str(getattr(settings, "secret_key", "") or "").strip()
  algorithm = str(getattr(settings, "algorithm", "") or "").upper()
  normalized_secret = secret.lower()
  if (
    secret == _DEFAULT_AUTH_SECRET
    or normalized_secret.startswith("change-this")
    or normalized_secret.startswith("replace-me")
    or len(secret.encode("utf-8")) < 32
    or algorithm != "HS256"
  ):
    raise ValueError("AUTH_SIGNING_KEY_UNAVAILABLE")
  encoded = json.dumps(
    dict(payload),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hmac.new(secret.encode("utf-8"), encoded, hashlib.sha256).hexdigest()


async def validate_consumed_exit_plan_sell_challenge(
  db: Any,
  *,
  plan_id: str,
  intent_id: str,
  account_id: str,
  approval_audit: Optional[Mapping[str, Any]],
) -> str:
  """Require an exact consumed device challenge for one manual plan SELL."""

  audit = dict(approval_audit or {})
  challenge_id = str(audit.get("challenge_id") or "").strip()
  actor_id = str(audit.get("actor_id") or "").strip()
  device_session_id = str(audit.get("device_session_id") or "").strip()
  channel = str(audit.get("channel") or "").strip().upper()
  if (
    not challenge_id
    or not actor_id
    or not device_session_id
    or channel not in {"EXIT_PLAN_DEVICE_CHALLENGE", "IOS_BIOMETRIC"}
  ):
    raise ValueError("EXIT_PLAN_DEVICE_CHALLENGE_REQUIRED")
  challenge = await db.get(TradeConfirmationChallenge, challenge_id)
  intent = await db.get(TradeIntentRecord, intent_id)
  if challenge is None or intent is None:
    raise ValueError("EXIT_PLAN_DEVICE_CHALLENGE_REQUIRED")
  payload = dict(challenge.payload or {})
  try:
    fingerprint = trade_confirmation_payload_fingerprint(payload)
  except ValueError as exc:
    raise ValueError("EXIT_PLAN_DEVICE_CHALLENGE_INVALID") from exc
  if (
    challenge.consumed_at is None
    or str(challenge.action or "") != EXIT_PLAN_SELL_APPROVAL_ACTION
    or str(challenge.user_id or "") != actor_id
    or str(challenge.device_session_id or "") != device_session_id
    or str(challenge.account_id or "") != str(account_id)
    or str(challenge.owner_type or "").upper() != ExecutionOwnerType.EXIT_PLAN.value
    or str(challenge.owner_id or "") != str(plan_id)
    or str(challenge.environment or "").upper()
    != str(intent.environment or "").upper()
    or str(payload.get("action") or "") != EXIT_PLAN_SELL_APPROVAL_ACTION
    or str(payload.get("user_id") or "") != actor_id
    or str(payload.get("device_session_id") or "") != device_session_id
    or str(payload.get("account_id") or "") != str(account_id)
    or str(payload.get("owner_type") or "").upper()
    != ExecutionOwnerType.EXIT_PLAN.value
    or str(payload.get("owner_id") or "") != str(plan_id)
    or str(payload.get("environment") or "").upper()
    != str(intent.environment or "").upper()
    or str(payload.get("intent_id") or "") != str(intent_id)
    or not hmac.compare_digest(
      str(challenge.payload_fingerprint or ""),
      fingerprint,
    )
    or str(intent.owner_type or "").upper() != "EXIT_PLAN"
    or str(intent.owner_id or "") != str(plan_id)
    or str(intent.account_id or "") != str(account_id)
    or str(intent.direction or "").upper() != "SELL"
    or str(dict(intent.intent_metadata or {}).get("exit_plan_id") or "")
    != str(plan_id)
  ):
    raise ValueError("EXIT_PLAN_DEVICE_CHALLENGE_CONTEXT_MISMATCH")
  return challenge_id


def _positive_int(value: Any) -> int:
  try:
    parsed = int(value or 0)
  except (TypeError, ValueError, OverflowError):
    return 0
  return max(0, parsed)


def _positive_float(value: Any) -> float:
  try:
    parsed = float(value or 0.0)
  except (TypeError, ValueError, OverflowError):
    return 0.0
  return parsed if isfinite(parsed) and parsed > 0 else 0.0


def _t_trade_entry_volume_ceiling(record: TradeIntentRecord) -> int:
  metadata = dict(record.intent_metadata or {})
  rules = AShareMarketRules()
  requested_volume = _positive_int(
    record.target_volume
    or metadata.get("requested_volume")
    or metadata.get("volume")
  )
  if requested_volume > 0:
    return int(rules.normalize_buy_volume(requested_volume))

  target_amount = _positive_float(
    record.target_amount
    or metadata.get("target_amount")
    or metadata.get("requested_entry_amount")
    or metadata.get("target_trade_amount")
  )
  reference_price = _positive_float(
    record.limit_price_hint
    or metadata.get("signal_price")
    or dict(metadata.get("signal") or {}).get("signal_price")
  )
  try:
    deviation_bps = float(metadata.get("max_price_deviation_bps") or 0.0)
  except (TypeError, ValueError, OverflowError):
    deviation_bps = 0.0
  if (
    target_amount <= 0
    or reference_price <= 0
    or not isfinite(deviation_bps)
    or deviation_bps <= 0
    or deviation_bps >= 10_000
  ):
    return 0
  minimum_approved_price = reference_price * (1.0 - deviation_bps / 10_000.0)
  if minimum_approved_price <= 0:
    return 0
  return int(
    rules.normalize_buy_volume(int(target_amount // minimum_approved_price))
  )


def _normalized_t_trade_exit_template(
  record: TradeIntentRecord,
) -> dict[str, Any]:
  metadata = dict(record.intent_metadata or {})
  raw_template = metadata.get("exit_plan_template")
  if not isinstance(raw_template, Mapping):
    raise ValueError("T_TRADE_EXIT_PLAN_TEMPLATE_MISSING")
  try:
    template = ExitPlanTemplate.from_dict(raw_template).to_dict()
  except (KeyError, TypeError, ValueError) as exc:
    raise ValueError("T_TRADE_EXIT_PLAN_TEMPLATE_INVALID") from exc
  template.pop("auto_exit_authorized", None)

  account_id = str(record.account_id or "").strip()
  run_id = str(record.owner_id or "").strip()
  instrument_code = str(record.instrument_code or "").strip().upper()
  batch_id = str(metadata.get("t_batch_id") or "").strip()
  plan_id = str(metadata.get("exit_plan_id") or "").strip()
  template_metadata = dict(template.get("metadata") or {})
  independent = str(record.owner_type or "") == "T_ASSISTANT_EXECUTION"
  expected = {
    "plan_id": (template.get("plan_id"), plan_id),
    "source_type": (template.get("source_type"), "T_TRADE_BATCH"),
    "source_id": (template.get("source_id"), batch_id),
    "account_id": (template.get("account_id"), account_id),
    "instrument_code": (
      str(template.get("instrument_code") or "").upper(),
      instrument_code,
    ),
    "bucket": (template.get("bucket"), record.bucket),
    "run_id": (template.get("run_id"), "" if independent else run_id),
    "metadata.t_batch_id": (template_metadata.get("t_batch_id"), batch_id),
    "metadata.instrument_code": (
      str(template_metadata.get("instrument_code") or "").upper(),
      instrument_code,
    ),
    "metadata.t_trade_role": (
      str(template_metadata.get("t_trade_role") or "").lower(),
      "exit",
    ),
  }
  if independent:
    source_ref = {"owner_type": "T_ASSISTANT_EXECUTION", "owner_id": run_id}
    if (
      record.environment != "LIVE"
      or record.strategy_run_id
      or metadata.get("strategy_run_id")
      or template_metadata.get("strategy_run_id")
      or metadata.get("source_execution_ref") != source_ref
      or template_metadata.get("source_execution_ref") != source_ref
      or template_metadata.get("account_id", account_id) != account_id
    ):
      raise ValueError("T_TRADE_EXIT_AUTHORIZATION_IDENTITY_MISMATCH")
    for key in ("candidate_id", "candidate_fingerprint", "policy_version"):
      if not isinstance(metadata.get(key), str) or not metadata[key].strip():
        raise ValueError("T_TRADE_ENTRY_CANDIDATE_BINDING_REQUIRED")
      expected[f"metadata.{key}"] = (template_metadata.get(key), metadata[key])
    schema = metadata.get("feature_schema_version")
    if type(schema) is not int or schema <= 0 or type(template_metadata.get("feature_schema_version")) is not int or template_metadata.get("feature_schema_version") != schema:
      raise ValueError("T_TRADE_ENTRY_CANDIDATE_BINDING_REQUIRED")
  else:
    expected["metadata.strategy_run_id"] = (template_metadata.get("strategy_run_id"), run_id)
    expected["metadata.account_id"] = (template_metadata.get("account_id"), account_id)
  if (
    str(record.owner_type or "").upper() not in {"STRATEGY_RUN", "T_ASSISTANT_EXECUTION"}
    or not account_id
    or not run_id
    or not instrument_code
    or not batch_id
    or not plan_id
  ):
    raise ValueError("T_TRADE_EXIT_AUTHORIZATION_IDENTITY_MISSING")
  if any(str(actual or "") != str(wanted or "") for actual, wanted in expected.values()):
    raise ValueError("T_TRADE_EXIT_AUTHORIZATION_IDENTITY_MISMATCH")
  if int(template.get("config_version") or 0) <= 0:
    raise ValueError("T_TRADE_EXIT_CONFIG_VERSION_INVALID")
  execution = dict(template.get("execution") or {})
  order_policy = TExitOrderPolicy()
  if (
    str(execution.get("price_reference") or "").upper() != "BID"
    or str(execution.get("price_type") or "").upper() != "FIX_PRICE"
    or execution.get("protected_limit") is not True
    or float(execution.get("max_slippage_bps") or 0)
    != float(order_policy.max_slippage_bps)
    or str(execution.get("execution_mode") or "").upper() != "AUTO"
    or str(template_metadata.get("exit_policy_version") or "")
    != order_policy.version
    or str(template_metadata.get("t_exit_order_policy_version") or "")
    != order_policy.version
    or int(template_metadata.get("t_exit_order_ttl_seconds") or 0)
    != order_policy.order_ttl_seconds
    or int(template_metadata.get("t_exit_total_ttl_seconds") or 0)
    != order_policy.total_ttl_seconds
    or int(template_metadata.get("t_exit_max_replace_count") or -1)
    != order_policy.max_replace_count
    or int(template_metadata.get("t_exit_max_slippage_bps") or 0)
    != order_policy.max_slippage_bps
  ):
    raise ValueError("T_TRADE_EXIT_EXECUTION_POLICY_INVALID")
  if not list(template.get("rules") or []):
    raise ValueError("T_TRADE_EXIT_RULES_UNAVAILABLE")
  return template


def build_t_trade_entry_exit_authorization_envelope(
  record: TradeIntentRecord,
  *,
  max_protected_volume: Optional[int] = None,
) -> TTradeEntryExitAuthorizationEnvelope:
  """Build the immutable exit scope shown with a T-entry confirmation."""

  metadata = dict(record.intent_metadata or {})
  if (
    str(record.direction or "").upper() != "BUY"
    or str(metadata.get("t_trade_role") or "").lower() != "entry"
  ):
    raise ValueError("T_TRADE_ENTRY_INTENT_REQUIRED")
  if str(record.owner_type or "").upper() not in {"STRATEGY_RUN", "T_ASSISTANT_EXECUTION"}:
    raise ValueError("T_TRADE_ENTRY_OWNER_INVALID")
  template = _normalized_t_trade_exit_template(record)
  computed_ceiling = _t_trade_entry_volume_ceiling(record)
  requested_ceiling = _positive_int(max_protected_volume)
  if computed_ceiling <= 0:
    raise ValueError("T_TRADE_ENTRY_VOLUME_BOUND_UNAVAILABLE")
  if max_protected_volume is not None:
    normalized_requested = int(
      AShareMarketRules().normalize_buy_volume(requested_ceiling)
    )
    if normalized_requested <= 0 or normalized_requested > computed_ceiling:
      raise ValueError("T_TRADE_ENTRY_VOLUME_BOUND_INVALID")
    volume_ceiling = normalized_requested
  else:
    volume_ceiling = computed_ceiling
  account_id = str(record.account_id or "").strip()
  run_id = str(record.owner_id or "").strip()
  subject = {
    "schema_version": _T_TRADE_EXIT_AUTHORIZATION_SCHEMA_VERSION,
    "account_id": account_id,
    "strategy_run_id": run_id,
    "entry_intent_id": str(record.id),
    "instrument_code": str(record.instrument_code or "").strip().upper(),
    "bucket": str(record.bucket or ""),
    "t_batch_id": str(metadata.get("t_batch_id") or ""),
    "exit_plan_id": str(metadata.get("exit_plan_id") or ""),
    "exit_config_version": int(template.get("config_version") or 0),
    "max_protected_volume": volume_ceiling,
    "entry_target_amount": _positive_float(record.target_amount),
    "entry_reference_price": _positive_float(record.limit_price_hint),
    "entry_max_price_deviation_bps": _positive_float(
      metadata.get("max_price_deviation_bps")
    ),
    "exit_plan_template": template,
  }
  if record.owner_type == "T_ASSISTANT_EXECUTION":
    subject.pop("strategy_run_id")
    subject.update(
      schema_version=_T_ASSISTANT_EXIT_AUTHORIZATION_SCHEMA_VERSION,
      source_execution_ref={"owner_type": record.owner_type, "owner_id": run_id},
      environment=str(record.environment),
      candidate_id=metadata["candidate_id"],
      candidate_fingerprint=metadata["candidate_fingerprint"],
      policy_version=metadata["policy_version"],
      feature_schema_version=metadata["feature_schema_version"],
    )
  return TTradeEntryExitAuthorizationEnvelope(
    subject=subject,
    fingerprint=_sha256_fingerprint(subject),
  )


def bind_t_trade_exit_authorization_to_challenge_payload(
  payload: Mapping[str, Any],
  record: TradeIntentRecord,
  *,
  max_protected_volume: Optional[int] = None,
) -> dict[str, Any]:
  """Attach an exact T-exit envelope before the challenge is HMAC-signed."""

  bound = dict(payload)
  envelope = build_t_trade_entry_exit_authorization_envelope(
    record,
    max_protected_volume=max_protected_volume,
  )
  expected_payload = {
    "action": T_TRADE_ENTRY_APPROVAL_ACTION,
    "account_id": envelope.subject["account_id"],
    "owner_type": str(record.owner_type),
    "owner_id": str(record.owner_id),
    "environment": str(record.environment or "").upper(),
    "intent_id": envelope.subject["entry_intent_id"],
  }
  if any(
    str(bound.get(key) or "") != str(value)
    for key, value in expected_payload.items()
  ):
    raise ValueError("T_TRADE_CHALLENGE_CONTEXT_MISMATCH")
  existing = bound.get(T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY)
  if existing is not None and dict(existing or {}) != envelope.to_dict():
    raise ValueError("T_TRADE_CHALLENGE_BINDING_CONFLICT")
  bound[T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY] = envelope.to_dict()
  return bound


def _template_binding(record: AutoExitPlanRecord) -> dict[str, Any]:
  state = dict(record.plan_state or {})
  template = dict(state.get("template") or {})
  template.pop("auto_exit_authorized", None)
  binding = {
    "plan_id": str(record.plan_id),
    "account_id": str(record.account_id),
    "instrument_code": str(record.instrument_code),
    "bucket": str(record.bucket),
    "source_type": str(record.source_type),
    "source_id": str(record.source_id),
    "strategy_run_id": str(record.strategy_run_id or ""),
    "enabled": bool(record.enabled),
    "status": str(record.status or "").upper(),
    "execution_mode": str(record.environment or "").upper().lower(),
    "config_version": int(record.config_version or 0),
    "protected_volume": max(0, int(record.protected_volume or 0)),
    "exited_volume": max(0, int(record.exited_volume or 0)),
    "remaining_volume": max(0, int(record.remaining_volume or 0)),
    "template": template,
  }
  if record.source_execution_owner_type == "T_ASSISTANT_EXECUTION":
    binding["source_execution_ref"] = {
      "owner_type": record.source_execution_owner_type,
      "owner_id": record.source_execution_owner_id,
    }
    binding["source_execution_environment"] = record.source_execution_environment
  return binding


def require_authorizable_live_plan(
  record: Optional[AutoExitPlanRecord],
  *,
  account_id: str,
  expected_config_version: int,
) -> AutoExitPlanRecord:
  if record is None:
    raise ValueError("EXIT_PLAN_NOT_FOUND")
  if str(record.account_id) != str(account_id):
    raise ValueError("ACCOUNT_SCOPE_MISMATCH")
  if str(record.environment or "").upper() != ExecutionEnvironment.LIVE.value:
    raise ValueError("LIVE_EXIT_PLAN_REQUIRED")
  if int(record.config_version or 0) != int(expected_config_version):
    raise ValueError("CONFIG_VERSION_CONFLICT")
  if not bool(record.enabled) or str(record.status or "").upper() not in (
    AUTHORIZABLE_PLAN_STATUSES
  ):
    raise ValueError("EXIT_PLAN_NOT_ACTIVE")
  if int(record.protected_volume or 0) <= 0 or int(record.remaining_volume or 0) <= 0:
    raise ValueError("EXIT_PLAN_HAS_NO_REMAINING_VOLUME")
  if str(getattr(record, "capacity_status", "READY") or "READY") != "READY":
    raise ValueError("EXIT_PLAN_CAPACITY_RECONCILIATION_REQUIRED")
  state = dict(record.plan_state or {})
  template = dict(state.get("template") or {})
  rules = list(template.get("rules") or [])
  if not rules or not any(
    bool(dict(rule or {}).get("enabled", True)) for rule in rules
  ):
    raise ValueError("EXIT_PLAN_RULES_UNAVAILABLE")
  return record


async def build_exit_plan_authorization_snapshot(
  db: Any,
  record: AutoExitPlanRecord,
  *,
  lock_mutable_rows: bool,
  locked_scope: Optional[LockedExitPlanScope] = None,
) -> ExitPlanAuthorizationSnapshot:
  """Build the stable LIVE plan/position/T+1/protection subject to be signed."""

  if str(record.environment or "").upper() != ExecutionEnvironment.LIVE.value:
    raise ValueError("LIVE_EXIT_PLAN_REQUIRED")

  scope = locked_scope
  if lock_mutable_rows:
    scope = scope or await lock_exit_plan_scope(
      db,
      account_id=str(record.account_id),
      instrument_code=str(record.instrument_code),
      target_plan_id=str(record.plan_id),
    )
    record = scope.plan(str(record.plan_id)) or record
    position = scope.position
    conflicts = sorted(
      (item for item in scope.plans if item.plan_id != record.plan_id),
      key=lambda item: item.plan_id,
    )
  else:
    position = await db.scalar(
      select(Position).where(
        Position.account_id == record.account_id,
        Position.stock_code == record.instrument_code,
      )
    )
    conflicts = list(
      (
        await db.execute(
          select(AutoExitPlanRecord)
          .where(
            AutoExitPlanRecord.account_id == record.account_id,
            AutoExitPlanRecord.instrument_code == record.instrument_code,
            AutoExitPlanRecord.environment == ExecutionEnvironment.LIVE.value,
            AutoExitPlanRecord.plan_id != record.plan_id,
            AutoExitPlanRecord.status.in_(RESERVING_EXIT_PLAN_STATUSES),
          )
          .order_by(AutoExitPlanRecord.plan_id)
        )
      )
      .scalars()
      .all()
    )
  if position is None or int(position.volume or 0) <= 0:
    raise ValueError("POSITION_SNAPSHOT_UNAVAILABLE")
  protected_volume = max(0, int(record.remaining_volume or 0)) + sum(
    max(0, int(item.remaining_volume or 0)) for item in conflicts
  )
  if protected_volume > max(0, int(position.volume or 0)):
    raise ValueError("EXIT_PLAN_CAPACITY_RECONCILIATION_REQUIRED")

  pending_stmt = (
    select(PendingTradeOrder)
    .where(
      PendingTradeOrder.account_id == record.account_id,
      PendingTradeOrder.instrument_code == record.instrument_code,
      PendingTradeOrder.environment == ExecutionEnvironment.LIVE.value,
      PendingTradeOrder.side == "SELL",
      PendingTradeOrder.status.in_(ACTIVE_PENDING_SELL_STATUSES),
    )
    .order_by(PendingTradeOrder.client_order_id)
  )
  if lock_mutable_rows:
    pending_stmt = pending_stmt.with_for_update()
  pending_sells = list((await db.execute(pending_stmt)).scalars().all())

  total_volume = max(0, int(position.volume or 0))
  available_volume = max(
    0,
    min(total_volume, int(position.can_use_volume or 0)),
  )
  frozen_volume = max(0, min(total_volume, int(position.frozen_volume or 0)))
  yesterday_volume = max(
    0,
    min(total_volume, int(position.yesterday_volume or 0)),
  )
  subject = {
    "plan": _template_binding(record),
    "position": {
      "account_id": str(position.account_id),
      "instrument_code": str(position.stock_code),
      "total_volume": total_volume,
      "available_volume": available_volume,
      "frozen_volume": frozen_volume,
      "yesterday_volume": yesterday_volume,
      "t1_unavailable_volume": max(
        0,
        total_volume - available_volume - frozen_volume,
      ),
    },
    "other_protections": [
      {
        "plan_id": str(item.plan_id),
        "source_type": str(item.source_type),
        "status": str(item.status or "").upper(),
        "config_version": int(item.config_version or 0),
        "remaining_volume": max(0, int(item.remaining_volume or 0)),
        "pending": bool(
          str(item.status or "").upper() == "EXIT_PENDING"
          or item.pending_client_order_id
        ),
      }
      for item in conflicts
    ],
    "pending_sells": [
      {
        "client_order_id": str(item.client_order_id),
        "status": str(item.status or "").upper(),
        "volume": max(0, int(item.volume or 0)),
        "intent_id": str(item.intent_id or ""),
      }
      for item in pending_sells
    ],
  }
  return ExitPlanAuthorizationSnapshot(
    subject=subject,
    fingerprint=_canonical_fingerprint(subject),
    position_updated_at=position.updated_at,
    has_pending_sell=bool(pending_sells),
  )


def _advance_authorization_projection_version(
  record: AutoExitPlanRecord,
  *,
  previous_state: Mapping[str, Any],
  bump_state_version: bool,
) -> None:
  """Version an authorization change carried inside the canonical plan state."""

  if not bump_state_version or dict(previous_state) == dict(record.plan_state or {}):
    return
  current_version = max(1, int(getattr(record, "state_version", 1) or 1))
  record.state_version = current_version + 1


def clear_exact_auto_exit_authorization(
  record: AutoExitPlanRecord,
  *,
  bump_state_version: bool = True,
) -> None:
  """Remove only autonomous-live authority; the plan keeps monitoring."""

  previous_state = dict(record.plan_state or {})
  record.auto_exit_authorized = False
  record.auto_exit_authorization_fingerprint = None
  record.auto_exit_authorization_config_version = None
  record.auto_exit_authorized_at = None
  record.auto_exit_authorization_expires_at = None
  record.auto_exit_authorization_challenge_id = None
  record.auto_exit_authorization_user_id = None
  record.auto_exit_authorization_device_session_id = None
  state = dict(record.plan_state or {})
  template = dict(state.get("template") or {})
  if template:
    template["auto_exit_authorized"] = False
    state["template"] = template
    record.plan_state = state
  _advance_authorization_projection_version(
    record,
    previous_state=previous_state,
    bump_state_version=bump_state_version,
  )


def grant_exact_auto_exit_authorization(
  record: AutoExitPlanRecord,
  *,
  fingerprint: str,
  challenge_id: str,
  user_id: str,
  device_session_id: str,
  authorized_at: datetime,
  authorization_expires_at: datetime,
  bump_state_version: bool = True,
) -> None:
  if str(record.environment or "").upper() != ExecutionEnvironment.LIVE.value:
    raise ValueError("LIVE_EXIT_PLAN_REQUIRED")
  if not fingerprint or len(fingerprint) != 64:
    raise ValueError("INVALID_AUTHORIZATION_FINGERPRINT")
  if authorization_expires_at <= authorized_at:
    raise ValueError("INVALID_AUTHORIZATION_EXPIRY")
  previous_state = dict(record.plan_state or {})
  record.auto_exit_authorized = True
  record.auto_exit_authorization_fingerprint = fingerprint
  record.auto_exit_authorization_config_version = int(record.config_version or 0)
  record.auto_exit_authorized_at = authorized_at
  record.auto_exit_authorization_expires_at = authorization_expires_at
  record.auto_exit_authorization_challenge_id = challenge_id
  record.auto_exit_authorization_user_id = user_id
  record.auto_exit_authorization_device_session_id = device_session_id
  state = dict(record.plan_state or {})
  template = dict(state.get("template") or {})
  if not template:
    raise ValueError("EXIT_PLAN_RULES_UNAVAILABLE")
  template["auto_exit_authorized"] = True
  state["template"] = template
  record.plan_state = state
  _advance_authorization_projection_version(
    record,
    previous_state=previous_state,
    bump_state_version=bump_state_version,
  )


def _t_trade_derivation_failure(
  record: AutoExitPlanRecord,
  code: str,
  message: str,
) -> TTradeExitAuthorizationDerivation:
  clear_exact_auto_exit_authorization(record)
  return TTradeExitAuthorizationDerivation(False, code, message)


async def _add_t_trade_authorization_event(
  db: Any,
  record: AutoExitPlanRecord,
  *,
  challenge: TradeConfirmationChallenge,
  entry_intent_id: str,
  cumulative_filled_volume: int,
  fingerprint: str,
  authorization_expires_at: datetime,
  created_at: datetime,
) -> None:
  identity = _sha256_fingerprint(
    {
      "plan_id": str(record.plan_id),
      "challenge_id": str(challenge.id),
      "entry_intent_id": entry_intent_id,
      "cumulative_filled_volume": cumulative_filled_volume,
      "authorization_fingerprint": fingerprint,
    }
  )
  business_key = f"t-entry-exit-authorization-derived:{identity}"
  existing = await db.scalar(
    select(AutoExitPlanEvent).where(
      AutoExitPlanEvent.business_key == business_key
    )
  )
  if existing is not None:
    return
  db.add(
    AutoExitPlanEvent(
      event_id=str(uuid.uuid4()),
      business_key=business_key,
      plan_id=str(record.plan_id),
      event_type="AUTO_EXIT_AUTHORIZATION_DERIVED_FROM_T_ENTRY",
      payload={
        "challenge_id": str(challenge.id),
        "entry_intent_id": entry_intent_id,
        "strategy_run_id": str(record.strategy_run_id or ""),
        "t_batch_id": str(record.source_id),
        "config_version": int(record.config_version or 0),
        "cumulative_filled_volume": cumulative_filled_volume,
        "authorization_fingerprint": fingerprint,
        "actor_user_id": str(challenge.user_id),
        "device_session_id": str(challenge.device_session_id),
        "authorization_expires_at": authorization_expires_at.isoformat(),
      },
      created_at=created_at,
    )
  )


async def derive_exact_auto_exit_authorization_from_t_trade_entry(
  db: Any,
  record: AutoExitPlanRecord,
  *,
  entry_intent_id: str,
  challenge_id: str,
  cumulative_filled_volume: int,
  now: Optional[datetime] = None,
  locked_scope: Optional[LockedExitPlanScope] = None,
) -> TTradeExitAuthorizationDerivation:
  """Derive exact live-exit authority from a consumed T-entry challenge.

  The caller must update the durable entry intent, position and exit-plan
  volume in the same transaction before calling this function.  The function
  never commits.  That lets the report UOW atomically publish the fill and the
  resulting exact authorization.
  """

  checked_at = now or time_utils.now()
  normalized_intent_id = str(entry_intent_id or "").strip()
  normalized_challenge_id = str(challenge_id or "").strip()
  actual_volume = _positive_int(cumulative_filled_volume)
  if not normalized_intent_id or not normalized_challenge_id or actual_volume <= 0:
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_AUTHORIZATION_CONTEXT_MISSING",
      "做 T 买入成交缺少可验证的确认上下文",
    )

  scope = locked_scope or await lock_exit_plan_scope(
    db,
    account_id=str(record.account_id),
    instrument_code=str(record.instrument_code),
    target_plan_id=str(record.plan_id),
  )
  locked_record = scope.plan(str(record.plan_id))
  if locked_record is None:
    return _t_trade_derivation_failure(
      record,
      "EXIT_PLAN_NOT_FOUND",
      "退出计划不存在",
    )
  record = locked_record
  intent = await db.get(TradeIntentRecord, normalized_intent_id)
  challenge = await db.get(TradeConfirmationChallenge, normalized_challenge_id)
  if intent is None:
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_INTENT_NOT_FOUND",
      "做 T 买入意图不存在",
    )
  if challenge is None:
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_CHALLENGE_NOT_FOUND",
      "做 T 买入确认挑战不存在",
    )

  payload = dict(challenge.payload or {})
  try:
    expected_payload_fingerprint = trade_confirmation_payload_fingerprint(payload)
  except ValueError:
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_CHALLENGE_SIGNATURE_UNAVAILABLE",
      "做 T 买入确认签名无法验证",
    )
  if not hmac.compare_digest(
    str(challenge.payload_fingerprint or ""),
    expected_payload_fingerprint,
  ):
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_CHALLENGE_TAMPERED",
      "做 T 买入确认内容已变化",
    )
  expected_challenge_context = {
    "action": T_TRADE_ENTRY_APPROVAL_ACTION,
    "user_id": str(challenge.user_id),
    "device_session_id": str(challenge.device_session_id),
    "account_id": str(challenge.account_id),
    "owner_type": str(intent.owner_type or "").upper(),
    "owner_id": str(intent.owner_id or ""),
    "environment": str(intent.environment or "").upper(),
    "intent_id": str(intent.id),
  }
  if (
    str(challenge.action or "") != T_TRADE_ENTRY_APPROVAL_ACTION
    or str(challenge.owner_type or "").upper()
    != str(intent.owner_type or "").upper()
    or str(challenge.owner_id or "") != str(intent.owner_id or "")
    or str(challenge.environment or "").upper()
    != str(intent.environment or "").upper()
    or challenge.consumed_at is None
    or any(
      str(payload.get(key) or "") != expected
      for key, expected in expected_challenge_context.items()
    )
  ):
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_CHALLENGE_CONTEXT_MISMATCH",
      "做 T 买入确认未消费或身份上下文不匹配",
    )

  raw_envelope = payload.get(T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY)
  if not isinstance(raw_envelope, Mapping):
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_EXIT_AUTHORIZATION_BINDING_MISSING",
      "做 T 买入确认未绑定自动退出范围",
    )
  subject = raw_envelope.get("subject")
  envelope_fingerprint = str(raw_envelope.get("fingerprint") or "")
  if (
    not isinstance(subject, Mapping)
    or int(dict(subject).get("schema_version") or 0)
    != (_T_ASSISTANT_EXIT_AUTHORIZATION_SCHEMA_VERSION
        if intent.owner_type == "T_ASSISTANT_EXECUTION"
        else _T_TRADE_EXIT_AUTHORIZATION_SCHEMA_VERSION)
    or len(envelope_fingerprint) != 64
    or not hmac.compare_digest(
      envelope_fingerprint,
      _sha256_fingerprint(subject),
    )
  ):
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_EXIT_AUTHORIZATION_BINDING_INVALID",
      "做 T 自动退出确认范围无效",
    )
  bound_subject = dict(subject)
  try:
    rebuilt = build_t_trade_entry_exit_authorization_envelope(
      intent,
      max_protected_volume=_positive_int(
        bound_subject.get("max_protected_volume")
      ),
    )
  except ValueError:
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_INTENT_SCOPE_CHANGED",
      "做 T 买入意图或退出模板已变化",
    )
  if (
    rebuilt.subject != bound_subject
    or not hmac.compare_digest(rebuilt.fingerprint, envelope_fingerprint)
  ):
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_INTENT_SCOPE_CHANGED",
      "做 T 买入意图或退出模板已变化",
    )

  plan_binding = _template_binding(record)
  expected_plan_binding = {
    "plan_id": str(bound_subject.get("exit_plan_id") or ""),
    "account_id": str(bound_subject.get("account_id") or ""),
    "instrument_code": str(bound_subject.get("instrument_code") or ""),
    "bucket": str(bound_subject.get("bucket") or ""),
    "source_type": "T_TRADE_BATCH",
    "source_id": str(bound_subject.get("t_batch_id") or ""),
    "strategy_run_id": str(bound_subject.get("strategy_run_id") or ""),
    "config_version": int(bound_subject.get("exit_config_version") or 0),
    "template": dict(bound_subject.get("exit_plan_template") or {}),
  }
  if intent.owner_type == "T_ASSISTANT_EXECUTION":
    expected_plan_binding["source_execution_ref"] = bound_subject["source_execution_ref"]
    expected_plan_binding["source_execution_environment"] = bound_subject["environment"]
  if any(
    plan_binding.get(key) != value
    for key, value in expected_plan_binding.items()
  ):
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_EXIT_PLAN_SCOPE_CHANGED",
      "退出计划与买入时确认的范围不一致",
    )
  try:
    require_authorizable_live_plan(
      record,
      account_id=str(bound_subject.get("account_id") or ""),
      expected_config_version=int(
        bound_subject.get("exit_config_version") or 0
      ),
    )
  except ValueError as exc:
    return _t_trade_derivation_failure(record, str(exc), "退出计划当前不可授权")

  max_volume = _positive_int(bound_subject.get("max_protected_volume"))
  durable_filled_volume = _positive_int(intent.executed_volume)
  if (
    actual_volume > max_volume
    or durable_filled_volume != actual_volume
    or str(intent.status or "").upper() not in {"PARTIAL_FILLED", "FILLED"}
    or int(record.protected_volume or 0) != actual_volume
    or int(record.exited_volume or 0) > actual_volume
    or int(record.remaining_volume or 0)
    != actual_volume - int(record.exited_volume or 0)
  ):
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_FILL_SCOPE_MISMATCH",
      "实际买入成交量超出确认上限或尚未形成一致的持久化计划",
    )

  authorization_expires_at = authorization_expiry_for_challenge(
    challenge.expires_at
  )
  if authorization_expires_at <= checked_at:
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_EXIT_AUTHORIZATION_EXPIRED",
      "买入确认派生的自动退出授权已过期",
    )
  if not await _authorization_session_valid(
    db,
    record,
    lock_mutable_rows=True,
    authorization_user_id=str(challenge.user_id),
    authorization_device_session_id=str(challenge.device_session_id),
  ):
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_ENTRY_AUTHORIZATION_REVOKED",
      "买入确认对应的用户、设备权限或账户范围已失效",
    )
  try:
    snapshot = await build_exit_plan_authorization_snapshot(
      db,
      record,
      lock_mutable_rows=True,
      locked_scope=scope,
    )
  except ValueError:
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_EXIT_SAFETY_SNAPSHOT_UNAVAILABLE",
      "当前持仓、T+1 或保护计划状态无法形成安全快照",
    )
  if snapshot.has_pending_sell:
    return _t_trade_derivation_failure(
      record,
      "T_TRADE_EXIT_COMPETING_SELL_EXISTS",
      "当前已有竞争卖单，不能派生自动退出授权",
    )

  authorized_at = time_utils.to_shanghai(challenge.consumed_at)
  grant_exact_auto_exit_authorization(
    record,
    fingerprint=snapshot.fingerprint,
    challenge_id=str(challenge.id),
    user_id=str(challenge.user_id),
    device_session_id=str(challenge.device_session_id),
    authorized_at=authorized_at,
    authorization_expires_at=authorization_expires_at,
  )
  await _add_t_trade_authorization_event(
    db,
    record,
    challenge=challenge,
    entry_intent_id=normalized_intent_id,
    cumulative_filled_volume=actual_volume,
    fingerprint=snapshot.fingerprint,
    authorization_expires_at=authorization_expires_at,
    created_at=checked_at,
  )
  return TTradeExitAuthorizationDerivation(
    True,
    "T_TRADE_EXIT_AUTHORIZATION_DERIVED",
    "已从做 T 买入确认派生精确自动退出授权",
    authorization_expires_at=authorization_expires_at,
    fingerprint=snapshot.fingerprint,
    authorization_user_id=str(challenge.user_id),
    config_version=int(record.config_version or 0),
    challenge_id=str(challenge.id),
  )


async def _authorization_session_valid(
  db: Any,
  record: AutoExitPlanRecord,
  *,
  lock_mutable_rows: bool,
  authorization_user_id: Optional[str] = None,
  authorization_device_session_id: Optional[str] = None,
) -> bool:
  session_id = str(
    authorization_device_session_id
    or record.auto_exit_authorization_device_session_id
    or ""
  )
  user_id = str(
    authorization_user_id or record.auto_exit_authorization_user_id or ""
  )
  if not session_id or not user_id:
    return False
  session_stmt = (
    select(AuthDeviceSession, AuthUser)
    .join(AuthUser, AuthUser.id == AuthDeviceSession.user_id)
    .where(
      AuthDeviceSession.id == session_id,
      AuthDeviceSession.user_id == user_id,
    )
  )
  if lock_mutable_rows:
    session_stmt = session_stmt.with_for_update()
  row = (await db.execute(session_stmt)).one_or_none()
  if row is None:
    return False
  session, user = row
  if (
    session.revoked_at is not None
    or session.expires_at <= utcnow()
    or not bool(user.is_active)
  ):
    return False
  user_scopes = {
    str(value).strip()
    for value in list(user.permissions or [])
    if isinstance(value, str) and value.strip()
  }
  granted_permissions = session.granted_permissions
  if granted_permissions is None:
    # Web sessions inherit the user's current permissions. Native sessions
    # persist an explicit, purpose-limited scope list instead.
    session_scopes = set(user_scopes)
  elif isinstance(granted_permissions, list):
    session_scopes = {
      str(value).strip()
      for value in granted_permissions
      if isinstance(value, str) and value.strip()
    }
  else:
    return False
  if not REQUIRED_AUTO_EXIT_SCOPES <= (session_scopes & user_scopes):
    return False
  access_stmt = select(AuthUserAccountAccess).where(
    AuthUserAccountAccess.user_id == user_id,
    AuthUserAccountAccess.account_id == record.account_id,
  )
  if lock_mutable_rows:
    access_stmt = access_stmt.with_for_update()
  access = await db.scalar(access_stmt)
  return access is not None


async def validate_exact_auto_exit_authorization(
  db: Any,
  record: AutoExitPlanRecord,
  *,
  now: Optional[datetime] = None,
  lock_mutable_rows: bool = False,
  locked_scope: Optional[LockedExitPlanScope] = None,
) -> ExitPlanAuthorizationValidation:
  scope = locked_scope
  if lock_mutable_rows:
    scope = scope or await lock_exit_plan_scope(
      db,
      account_id=str(record.account_id),
      instrument_code=str(record.instrument_code),
      target_plan_id=str(record.plan_id),
    )
    record = scope.plan(str(record.plan_id)) or record
  checked_at = now or time_utils.now()
  if not bool(record.auto_exit_authorized):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_NOT_AUTHORIZED",
      "退出计划尚未获得精确自动实盘授权",
    )
  required_values = (
    record.auto_exit_authorization_fingerprint,
    record.auto_exit_authorization_config_version,
    record.auto_exit_authorized_at,
    record.auto_exit_authorization_expires_at,
    record.auto_exit_authorization_challenge_id,
    record.auto_exit_authorization_user_id,
    record.auto_exit_authorization_device_session_id,
  )
  if any(value is None or value == "" for value in required_values):
    return ExitPlanAuthorizationValidation(
      False,
      "LEGACY_BOOLEAN_AUTHORIZATION_REJECTED",
      "旧布尔授权不具备自动实盘权限",
    )
  if str(record.environment or "").upper() != ExecutionEnvironment.LIVE.value:
    return ExitPlanAuthorizationValidation(
      False,
      "LIVE_EXIT_PLAN_REQUIRED",
      "只有明确的 LIVE 退出计划可使用自动实盘授权",
    )
  if str(record.status or "").upper() not in (
    AUTHORIZABLE_PLAN_STATUSES | {"EXIT_PENDING"}
  ):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_PLAN_NOT_ACTIVE",
      "退出计划当前状态不允许自动实盘执行",
    )
  if int(record.auto_exit_authorization_config_version or 0) != int(
    record.config_version or 0
  ):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_CONFIG_CHANGED",
      "退出计划配置版本已变化",
    )
  expires_at = time_utils.to_shanghai(record.auto_exit_authorization_expires_at)
  if expires_at <= checked_at:
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_AUTHORIZATION_EXPIRED",
      "自动实盘授权已过期",
    )
  if not await _authorization_session_valid(
    db,
    record,
    lock_mutable_rows=lock_mutable_rows,
  ):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_AUTHORIZATION_REVOKED",
      "授权设备会话、权限或账户范围已失效",
    )
  try:
    snapshot = await build_exit_plan_authorization_snapshot(
      db,
      record,
      lock_mutable_rows=lock_mutable_rows,
      locked_scope=scope,
    )
  except ValueError:
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_SAFETY_SNAPSHOT_UNAVAILABLE",
      "当前持仓安全快照不可用",
    )
  if snapshot.fingerprint != str(record.auto_exit_authorization_fingerprint):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_AUTHORIZATION_SCOPE_CHANGED",
      "规则、保护量、持仓、T+1、冲突或待成交 SELL 已变化",
    )
  return ExitPlanAuthorizationValidation(
    True,
    "AUTHORIZED",
    "精确授权有效",
    fingerprint=str(record.auto_exit_authorization_fingerprint or ""),
    authorization_user_id=str(record.auto_exit_authorization_user_id or ""),
    config_version=int(record.auto_exit_authorization_config_version or 0),
    challenge_id=str(record.auto_exit_authorization_challenge_id or ""),
    device_session_id=str(record.auto_exit_authorization_device_session_id or ""),
    authorized_at=time_utils.to_shanghai(record.auto_exit_authorized_at),
    authorization_expires_at=expires_at,
  )


class AutoExitAuthorizationGuard:
  """Engine-side defense that downgrades invalid live authority to approval."""

  @staticmethod
  async def validate_or_invalidate(plan_id: str) -> ExitPlanAuthorizationValidation:
    async with AsyncSessionLocal() as db:
      scope = await lock_exit_plan_scope_for_plan(db, plan_id)
      record = scope.plan(plan_id)
      if record is None:
        return ExitPlanAuthorizationValidation(
          False,
          "EXIT_PLAN_NOT_FOUND",
          "退出计划不存在",
        )
      result = await validate_exact_auto_exit_authorization(
        db,
        record,
        lock_mutable_rows=True,
        locked_scope=scope,
      )
      if result.valid:
        return result

      challenge_id = str(record.auto_exit_authorization_challenge_id or "legacy")
      if bool(record.auto_exit_authorized):
        clear_exact_auto_exit_authorization(record)
        business_key = (
          f"auto-exit-authorization-invalidated:{record.plan_id}:"
          f"{challenge_id}:{result.code}"
        )
        existing = await db.scalar(
          select(AutoExitPlanEvent).where(
            AutoExitPlanEvent.business_key == business_key
          )
        )
        if existing is None:
          db.add(
            AutoExitPlanEvent(
              event_id=str(uuid.uuid4()),
              business_key=business_key,
              plan_id=str(record.plan_id),
              event_type="AUTO_EXIT_AUTHORIZATION_INVALIDATED",
              payload={
                "challenge_id": None if challenge_id == "legacy" else challenge_id,
                "config_version": int(record.config_version or 0),
                "reason_code": result.code,
              },
              created_at=time_utils.now(),
            )
          )
        await db.commit()
      return result


__all__ = [
  "ACTIVE_PENDING_SELL_STATUSES",
  "AUTO_EXIT_AUTHORIZATION_LIFETIME",
  "AutoExitAuthorizationGuard",
  "ExitPlanAuthorizationSnapshot",
  "ExitPlanAuthorizationValidation",
  "EXIT_PLAN_SELL_APPROVAL_ACTION",
  "T_TRADE_ENTRY_APPROVAL_ACTION",
  "T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY",
  "TTradeEntryExitAuthorizationEnvelope",
  "TTradeExitAuthorizationDerivation",
  "authorization_expiry_for_challenge",
  "bind_t_trade_exit_authorization_to_challenge_payload",
  "build_exit_plan_authorization_snapshot",
  "build_t_trade_entry_exit_authorization_envelope",
  "clear_exact_auto_exit_authorization",
  "derive_exact_auto_exit_authorization_from_t_trade_entry",
  "grant_exact_auto_exit_authorization",
  "lock_exit_plan_scope",
  "lock_exit_plan_scope_for_plan",
  "require_authorizable_live_plan",
  "trade_confirmation_payload_fingerprint",
  "validate_consumed_exit_plan_sell_challenge",
  "validate_exact_auto_exit_authorization",
]
