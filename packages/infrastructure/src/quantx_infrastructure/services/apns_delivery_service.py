"""Privacy-preserving APNs transport and durable outbox delivery.

The provider client never receives account or trading facts. Delivery claims are
leased in the database before network I/O so a crashed worker can retry without
holding a row lock across the HTTP/2 request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Protocol

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.auth import AuthDeviceSession, AuthUser
from quantx_infrastructure.models.ios_notifications import (
  NOTIFICATION_ROUTE_TYPES,
  PUSH_CATEGORIES,
  PUSH_ENVIRONMENTS,
  IosNotificationEvent,
  IosNotificationOutbox,
  IosPushCategoryPreference,
  IosPushRegistration,
)

_APNS_HOSTS = {
  "SANDBOX": "https://api.sandbox.push.apple.com",
  "PRODUCTION": "https://api.push.apple.com",
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9]{6,20}$")
_BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 503})
_INVALID_DEVICE_REASONS = frozenset(
  {
    "BadDeviceToken",
    "DeviceTokenNotForTopic",
    "ExpiredToken",
    "TOKEN_UNAVAILABLE",
    "Unregistered",
  }
)
_MAX_PAYLOAD_BYTES = 4096
_MAX_RETRY_DELAY_SECONDS = 60 * 60
_APNS_SERVER_RETRY_SECONDS = 15 * 60


def _utcnow() -> datetime:
  return datetime.now(timezone.utc).replace(tzinfo=None)


def _base64url(value: bytes) -> str:
  return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_segment(value: dict[str, object]) -> str:
  return _base64url(
    json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
  )


def _canonical_request_id(value: Optional[str]) -> Optional[str]:
  if not value:
    return None
  try:
    return str(uuid.UUID(value.strip()))
  except (AttributeError, ValueError):
    return None


def _safe_error_details(
  response: httpx.Response,
) -> tuple[str, Optional[datetime]]:
  try:
    payload = response.json()
  except (json.JSONDecodeError, UnicodeError, ValueError):
    return f"HTTP_{response.status_code}", None
  reason = payload.get("reason") if isinstance(payload, dict) else None
  safe_reason = (
    reason
    if isinstance(reason, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,62}", reason)
    else f"HTTP_{response.status_code}"
  )
  raw_timestamp = payload.get("timestamp") if isinstance(payload, dict) else None
  if (
    response.status_code != 410
    or isinstance(raw_timestamp, bool)
    or not isinstance(raw_timestamp, int)
    or raw_timestamp < 0
  ):
    return safe_reason, None
  try:
    invalidated_at = datetime.fromtimestamp(
      raw_timestamp / 1000,
      timezone.utc,
    ).replace(tzinfo=None)
  except (OSError, OverflowError, ValueError):
    invalidated_at = None
  return safe_reason, invalidated_at


def _retry_after_seconds(value: Optional[str], *, now_epoch: float) -> Optional[int]:
  """Parse a bounded Retry-After value without reflecting provider content."""

  normalized = str(value or "").strip()
  if not normalized:
    return None
  if normalized.isascii() and normalized.isdigit():
    if len(normalized) > 10:
      return None
    seconds = int(normalized)
  else:
    try:
      retry_at = parsedate_to_datetime(normalized)
      if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
      seconds = math.ceil(retry_at.timestamp() - now_epoch)
    except (OSError, TypeError, ValueError, OverflowError):
      return None
  if seconds < 0:
    return None
  return min(_MAX_RETRY_DELAY_SECONDS, max(1, seconds))


def build_minimal_apns_payload(
  *, event_id: str, category: str, route_type: str
) -> dict[str, object]:
  """Return the only payload shape accepted by the QuantX APNs sender."""

  try:
    normalized_event_id = str(uuid.UUID(str(event_id).strip()))
  except (AttributeError, ValueError):
    raise ValueError("invalid notification event id") from None
  normalized_category = str(category or "").strip().upper()
  normalized_route = str(route_type or "").strip().lower()
  if normalized_category not in PUSH_CATEGORIES:
    raise ValueError("invalid notification category")
  if normalized_route not in NOTIFICATION_ROUTE_TYPES:
    raise ValueError("invalid notification route")
  return {
    "aps": {
      "alert": {
        "title": "QuantX 有一项状态更新",
        "body": "打开应用查看当前状态",
      },
      "sound": "default",
    },
    "eventId": normalized_event_id,
    "category": normalized_category,
    "route": normalized_route,
  }


def _validated_minimal_apns_payload(
  payload: dict[str, object],
) -> dict[str, object]:
  """Reject custom or enriched payloads at the network trust boundary."""

  if not isinstance(payload, dict):
    raise ValueError("invalid APNs payload")
  try:
    expected = build_minimal_apns_payload(
      event_id=payload["eventId"],
      category=payload["category"],
      route_type=payload["route"],
    )
  except (KeyError, TypeError, ValueError):
    raise ValueError("invalid APNs payload") from None
  if payload != expected:
    raise ValueError("invalid APNs payload")
  return expected


def decrypt_push_token(*, signing_key: bytes, ciphertext: str) -> str:
  """Decrypt an APNs token only at the delivery boundary."""

  if len(signing_key) < 32:
    raise ValueError("notification signing key is not configured")
  derived = hashlib.sha256(b"quantx:apns-token:v1\0" + signing_key).digest()
  cipher = Fernet(base64.urlsafe_b64encode(derived))
  try:
    token = cipher.decrypt(ciphertext.encode("ascii")).decode("ascii")
  except (InvalidToken, UnicodeError, ValueError):
    raise ValueError("push token is unavailable") from None
  if (
    not token
    or len(token) > 2048
    or len(token) % 2
    or not re.fullmatch(r"[0-9a-f]+", token)
  ):
    raise ValueError("push token is unavailable")
  return token


def _push_token_fingerprint(*, signing_key: bytes, token: str) -> str:
  return hmac.new(
    signing_key,
    b"quantx:apns-token-fingerprint:v1\0" + token.encode("ascii"),
    hashlib.sha256,
  ).hexdigest()


@dataclass(frozen=True)
class ApnsProviderConfiguration:
  team_id: str
  key_id: str
  topic: str
  private_key_pem: bytes = field(repr=False)
  timeout_seconds: float = 10.0

  def __post_init__(self) -> None:
    if not _IDENTIFIER.fullmatch(self.team_id.strip()):
      raise ValueError("invalid APNs team id")
    if not _IDENTIFIER.fullmatch(self.key_id.strip()):
      raise ValueError("invalid APNs key id")
    if not _BUNDLE_ID.fullmatch(self.topic.strip()):
      raise ValueError("invalid APNs topic")
    if not self.private_key_pem:
      raise ValueError("APNs private key is empty")
    if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
      raise ValueError("invalid APNs timeout")


@dataclass(frozen=True)
class ApnsProviderResponse:
  status_code: int
  reason: str
  request_id: Optional[str] = None
  token_invalidated_at: Optional[datetime] = None
  retry_after_seconds: Optional[int] = None

  @property
  def succeeded(self) -> bool:
    return self.status_code == 200

  @property
  def retryable(self) -> bool:
    return self.status_code == 0 or self.status_code in _RETRYABLE_STATUS_CODES

  @property
  def invalid_device(self) -> bool:
    return self.status_code == 410 or self.reason in _INVALID_DEVICE_REASONS

  def retry_delay_seconds(self, *, attempt_count: int) -> int:
    """Return a bounded delay that follows APNs response guidance."""

    if self.retry_after_seconds is not None:
      minimum = _APNS_SERVER_RETRY_SECONDS if self.status_code in {500, 503} else 1
      return min(
        _MAX_RETRY_DELAY_SECONDS,
        max(minimum, int(self.retry_after_seconds)),
      )
    exponent = max(0, min(19, int(attempt_count) - 1))
    if self.status_code in {500, 503}:
      return min(
        _MAX_RETRY_DELAY_SECONDS,
        _APNS_SERVER_RETRY_SECONDS * (2**exponent),
      )
    if self.status_code == 429:
      return min(_MAX_RETRY_DELAY_SECONDS, 60 * (2**exponent))
    return min(_MAX_RETRY_DELAY_SECONDS, 30 * (2**exponent))


class ApnsSending(Protocol):
  async def send(
    self,
    *,
    device_token: str,
    environment: str,
    payload: dict[str, object],
    apns_id: str,
    expires_at: datetime,
  ) -> ApnsProviderResponse: ...


class ApnsProviderClient:
  """Apple token-auth provider client using HTTP/2 and short-lived ES256 JWTs."""

  def __init__(
    self,
    configuration: ApnsProviderConfiguration,
    *,
    client: Optional[httpx.AsyncClient] = None,
    clock: Callable[[], float] = time.time,
  ):
    self.configuration = configuration
    self._clock = clock
    loaded_key = serialization.load_pem_private_key(
      configuration.private_key_pem,
      password=None,
    )
    if not isinstance(loaded_key, ec.EllipticCurvePrivateKey) or not isinstance(
      loaded_key.curve, ec.SECP256R1
    ):
      raise ValueError("APNs private key must use the P-256 curve")
    self._private_key = loaded_key
    self._client = client or httpx.AsyncClient(
      http2=True,
      timeout=configuration.timeout_seconds,
      trust_env=False,
    )
    self._owns_client = client is None
    self._cached_provider_token: Optional[str] = None
    self._cached_provider_token_issued_at = 0
    self._provider_token_generation = 0

  async def close(self) -> None:
    if self._owns_client:
      await self._client.aclose()

  @property
  def provider_token_generation(self) -> int:
    """Increase only when a new provider JWT is actually signed."""

    return self._provider_token_generation

  def provider_token(self, *, force_refresh: bool = False) -> str:
    issued_at = int(self._clock())
    if (
      not force_refresh
      and self._cached_provider_token
      and issued_at - self._cached_provider_token_issued_at < 50 * 60
      and issued_at >= self._cached_provider_token_issued_at
    ):
      return self._cached_provider_token
    header = _json_segment({"alg": "ES256", "kid": self.configuration.key_id.strip()})
    claims = _json_segment(
      {"iat": issued_at, "iss": self.configuration.team_id.strip()}
    )
    signing_input = f"{header}.{claims}"
    der_signature = self._private_key.sign(
      signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())
    )
    r_value, s_value = decode_dss_signature(der_signature)
    raw_signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    token = f"{signing_input}.{_base64url(raw_signature)}"
    self._cached_provider_token = token
    self._cached_provider_token_issued_at = issued_at
    self._provider_token_generation += 1
    return token

  async def send(
    self,
    *,
    device_token: str,
    environment: str,
    payload: dict[str, object],
    apns_id: str,
    expires_at: datetime,
  ) -> ApnsProviderResponse:
    normalized_environment = str(environment or "").strip().upper()
    if normalized_environment not in PUSH_ENVIRONMENTS:
      raise ValueError("invalid APNs environment")
    if (
      not re.fullmatch(r"[0-9a-f]+", device_token)
      or len(device_token) > 2048
      or len(device_token) % 2
    ):
      raise ValueError("invalid APNs device token")
    normalized_apns_id = str(uuid.UUID(apns_id))
    minimal_payload = _validated_minimal_apns_payload(payload)
    encoded_payload = json.dumps(
      minimal_payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded_payload) > _MAX_PAYLOAD_BYTES:
      raise ValueError("APNs payload exceeds the alert limit")
    expiration = int(
      expires_at.replace(tzinfo=timezone.utc).timestamp()
      if expires_at.tzinfo is None
      else expires_at.timestamp()
    )
    url = f"{_APNS_HOSTS[normalized_environment]}/3/device/{device_token}"
    for force_refresh in (False, True):
      response = await self._client.post(
        url,
        content=encoded_payload,
        headers={
          "authorization": f"bearer {self.provider_token(force_refresh=force_refresh)}",
          "apns-collapse-id": normalized_apns_id,
          "apns-id": normalized_apns_id,
          "apns-topic": self.configuration.topic.strip(),
          "apns-push-type": "alert",
          "apns-priority": "10",
          "apns-expiration": str(expiration),
          "content-type": "application/json",
        },
      )
      if response.http_version != "HTTP/2":
        raise httpx.RemoteProtocolError("APNs did not negotiate HTTP/2")
      if response.status_code == 200:
        reason, token_invalidated_at = "OK", None
      else:
        reason, token_invalidated_at = _safe_error_details(response)
      if not (
        response.status_code == 403
        and reason == "ExpiredProviderToken"
        and not force_refresh
      ):
        return ApnsProviderResponse(
          status_code=response.status_code,
          reason=reason,
          request_id=_canonical_request_id(response.headers.get("apns-id")),
          token_invalidated_at=token_invalidated_at,
          retry_after_seconds=_retry_after_seconds(
            response.headers.get("retry-after"),
            now_epoch=self._clock(),
          ),
        )
    raise AssertionError("unreachable APNs provider retry state")


@dataclass(frozen=True)
class ApnsDeliveryClaim:
  outbox_id: str
  registration_id: str
  attempt_count: int
  token_ciphertext: str = field(repr=False)
  token_fingerprint: str = field(repr=False)
  token_registered_at: datetime
  environment: str
  event_id: str
  category: str
  route_type: str
  expires_at: datetime

  @property
  def payload(self) -> dict[str, object]:
    return build_minimal_apns_payload(
      event_id=self.event_id,
      category=self.category,
      route_type=self.route_type,
    )


@dataclass(frozen=True)
class ApnsDeliverySummary:
  claimed: int = 0
  sent: int = 0
  retried: int = 0
  failed: int = 0
  discarded: int = 0


@dataclass(frozen=True)
class ApnsClaimResult:
  claim: Optional[ApnsDeliveryClaim] = None
  failed: int = 0
  discarded: int = 0


class ApnsOutboxService:
  """Claim and settle APNs delivery intents using database leases."""

  def __init__(
    self,
    db: AsyncSession,
    *,
    signing_key: bytes,
    topic: str,
    max_attempts: int = 5,
    lease_seconds: int = 120,
  ):
    if len(signing_key) < 32:
      raise ValueError("notification signing key is not configured")
    if not _BUNDLE_ID.fullmatch(str(topic or "").strip()):
      raise ValueError("invalid APNs topic")
    if max_attempts < 1 or max_attempts > 20:
      raise ValueError("invalid APNs attempt limit")
    if lease_seconds < 15 or lease_seconds > 900:
      raise ValueError("invalid APNs lease")
    self.db = db
    self.signing_key = signing_key
    self.topic = topic.strip()
    self.max_attempts = max_attempts
    self.lease_seconds = lease_seconds

  async def claim_next(
    self, *, now: Optional[datetime] = None
  ) -> Optional[ApnsDeliveryClaim]:
    return (await self.claim_next_result(now=now)).claim

  async def claim_next_result(
    self, *, now: Optional[datetime] = None
  ) -> ApnsClaimResult:
    current = now or _utcnow()
    failed = 0
    discarded = 0
    for _ in range(100):
      row = (
        await self.db.execute(
          select(
            IosNotificationOutbox,
            IosNotificationEvent,
            IosPushRegistration,
            IosPushCategoryPreference,
            AuthDeviceSession,
            AuthUser,
          )
          .join(
            IosNotificationEvent,
            IosNotificationEvent.id == IosNotificationOutbox.event_id,
          )
          .join(
            IosPushRegistration,
            IosPushRegistration.id == IosNotificationOutbox.registration_id,
          )
          .outerjoin(
            IosPushCategoryPreference,
            (IosPushCategoryPreference.registration_id == IosPushRegistration.id)
            & (IosPushCategoryPreference.category == IosNotificationEvent.category),
          )
          .join(
            AuthDeviceSession,
            AuthDeviceSession.id == IosPushRegistration.device_session_id,
          )
          .join(AuthUser, AuthUser.id == AuthDeviceSession.user_id)
          .where(
            IosNotificationOutbox.status.in_(("PENDING", "RETRY")),
            IosNotificationOutbox.available_at <= current,
          )
          .order_by(
            IosNotificationOutbox.available_at,
            IosNotificationOutbox.created_at,
          )
          # Lock only the outbox row. PostgreSQL rejects FOR UPDATE against
          # the nullable side of the category-preference outer join.
          .with_for_update(skip_locked=True, of=IosNotificationOutbox)
          .limit(1)
        )
      ).one_or_none()
      if row is None:
        return ApnsClaimResult(failed=failed, discarded=discarded)
      outbox, event, registration, preference, session, user = row
      discard_reason = self._discard_reason(
        event=event,
        registration=registration,
        preference=preference,
        session=session,
        user=user,
        now=current,
      )
      if discard_reason:
        outbox.status = "DISCARDED"
        outbox.last_error_code = discard_reason
        outbox.updated_at = current
        await self.db.flush()
        discarded += 1
        continue
      if int(outbox.attempt_count or 0) >= self.max_attempts:
        outbox.status = "FAILED"
        outbox.last_error_code = "MAX_ATTEMPTS"
        outbox.updated_at = current
        await self.db.flush()
        failed += 1
        continue
      outbox.attempt_count = int(outbox.attempt_count or 0) + 1
      outbox.status = "RETRY"
      outbox.available_at = current + timedelta(seconds=self.lease_seconds)
      outbox.last_error_code = None
      outbox.updated_at = current
      await self.db.flush()
      return ApnsClaimResult(
        claim=ApnsDeliveryClaim(
          outbox_id=outbox.id,
          registration_id=registration.id,
          attempt_count=outbox.attempt_count,
          token_ciphertext=str(registration.token_ciphertext),
          token_fingerprint=str(registration.token_fingerprint),
          token_registered_at=registration.registered_at,
          environment=registration.apns_environment,
          event_id=event.id,
          category=event.category,
          route_type=event.route_type,
          expires_at=event.expires_at,
        ),
        failed=failed,
        discarded=discarded,
      )
    return ApnsClaimResult(failed=failed, discarded=discarded)

  async def settle(
    self,
    claim: ApnsDeliveryClaim,
    response: ApnsProviderResponse,
    *,
    now: Optional[datetime] = None,
  ) -> str:
    current = now or _utcnow()
    # Push registration mutations lock the registration before touching its
    # outbox rows. Keep the same order here to avoid a registration/outbox
    # deadlock during token rotation or explicit unregister.
    registration = await self.db.scalar(
      select(IosPushRegistration)
      .where(IosPushRegistration.id == claim.registration_id)
      .with_for_update()
    )
    outbox = await self.db.scalar(
      select(IosNotificationOutbox)
      .where(IosNotificationOutbox.id == claim.outbox_id)
      .with_for_update()
    )
    if (
      outbox is None
      or outbox.status != "RETRY"
      or int(outbox.attempt_count or 0) != claim.attempt_count
    ):
      return "STALE"
    request_id = _canonical_request_id(response.request_id)
    if request_id is not None:
      outbox.apns_request_id = request_id
    outbox.updated_at = current
    if response.succeeded:
      outbox.status = "SENT"
      outbox.sent_at = current
      outbox.last_error_code = None
      await self.db.flush()
      return "SENT"
    reason = (
      response.reason
      if isinstance(response.reason, str)
      and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", response.reason)
      else "PROVIDER_ERROR"
    )
    if response.invalid_device:
      if registration is None or registration.invalidated_at is not None:
        outbox.status = "DISCARDED"
        outbox.last_error_code = "DEVICE_UNREGISTERED"
      elif not self._claim_matches_registration(claim, registration, response):
        # The in-flight response belongs to an older token generation. Retry
        # the same event against the current registration without invalidating
        # or exposing the replacement token.
        outbox.status = "RETRY"
        outbox.available_at = current
        outbox.last_error_code = "TOKEN_ROTATED"
        await self.db.flush()
        return "RETRY"
      else:
        await self._invalidate_registration(
          registration,
          now=current,
          reason=reason,
        )
      await self.db.flush()
      return "DISCARDED"
    if response.retryable and claim.attempt_count < self.max_attempts:
      outbox.status = "RETRY"
      outbox.available_at = current + timedelta(
        seconds=response.retry_delay_seconds(attempt_count=claim.attempt_count)
      )
      outbox.last_error_code = reason
      await self.db.flush()
      return "RETRY"
    outbox.status = "FAILED"
    outbox.last_error_code = reason
    await self.db.flush()
    return "FAILED"

  def _claim_matches_registration(
    self,
    claim: ApnsDeliveryClaim,
    registration: IosPushRegistration,
    response: ApnsProviderResponse,
  ) -> bool:
    if (
      registration.token_ciphertext != claim.token_ciphertext
      or registration.token_fingerprint != claim.token_fingerprint
      or registration.registered_at != claim.token_registered_at
    ):
      return False
    invalidated_at = response.token_invalidated_at
    if invalidated_at is None:
      return True
    if invalidated_at.tzinfo is not None:
      invalidated_at = invalidated_at.astimezone(timezone.utc).replace(tzinfo=None)
    return claim.token_registered_at <= invalidated_at

  def _discard_reason(
    self,
    *,
    event: IosNotificationEvent,
    registration: IosPushRegistration,
    preference: Optional[IosPushCategoryPreference],
    session: AuthDeviceSession,
    user: AuthUser,
    now: datetime,
  ) -> Optional[str]:
    if event.expires_at <= now:
      return "EVENT_EXPIRED"
    if registration.invalidated_at is not None or not registration.token_ciphertext:
      return "DEVICE_UNREGISTERED"
    if registration.app_bundle_id != self.topic:
      return "TOPIC_MISMATCH"
    if registration.apns_environment not in PUSH_ENVIRONMENTS:
      return "INVALID_ENVIRONMENT"
    if (
      session.revoked_at is not None
      or session.expires_at <= now
      or not bool(user.is_active)
      or user.id != session.user_id
      or session.user_id != registration.user_id
      or session.active_account_id != registration.account_id
      or "notification:manage" not in set(session.granted_permissions or [])
      or event.device_session_id != registration.device_session_id
      or event.user_id != registration.user_id
      or event.account_id != registration.account_id
    ):
      return "SESSION_INACTIVE"
    if preference is None or not bool(preference.enabled):
      return "CATEGORY_DISABLED"
    return None

  async def _invalidate_registration(
    self,
    registration: IosPushRegistration,
    *,
    now: datetime,
    reason: str,
  ) -> None:
    registration.invalidated_at = now
    registration.token_ciphertext = None
    registration.updated_at = now
    await self.db.execute(
      update(IosNotificationOutbox)
      .where(
        IosNotificationOutbox.registration_id == registration.id,
        IosNotificationOutbox.status.in_(("PENDING", "RETRY")),
      )
      .values(
        status="DISCARDED",
        last_error_code=reason[:64],
        updated_at=now,
      )
    )


async def deliver_apns_batch(
  *,
  session_factory,
  sender: ApnsSending,
  signing_key: bytes,
  topic: str,
  batch_size: int = 50,
  max_attempts: int = 5,
  lease_seconds: int = 120,
  clock: Callable[[], datetime] = _utcnow,
) -> ApnsDeliverySummary:
  """Deliver at most one bounded batch; all network errors remain retryable."""

  if batch_size < 1 or batch_size > 500:
    raise ValueError("invalid APNs batch size")
  counts = {"claimed": 0, "sent": 0, "retried": 0, "failed": 0, "discarded": 0}
  for _ in range(batch_size):
    async with session_factory() as db:
      service = ApnsOutboxService(
        db,
        signing_key=signing_key,
        topic=topic,
        max_attempts=max_attempts,
        lease_seconds=lease_seconds,
      )
      claim_result = await service.claim_next_result(now=clock())
      await db.commit()
    counts["failed"] += claim_result.failed
    counts["discarded"] += claim_result.discarded
    claim = claim_result.claim
    if claim is None:
      break
    counts["claimed"] += 1
    try:
      device_token = decrypt_push_token(
        signing_key=signing_key,
        ciphertext=claim.token_ciphertext,
      )
      if not hmac.compare_digest(
        claim.token_fingerprint,
        _push_token_fingerprint(signing_key=signing_key, token=device_token),
      ):
        raise ValueError("push token is unavailable")
    except ValueError:
      response = ApnsProviderResponse(status_code=400, reason="TOKEN_UNAVAILABLE")
    else:
      try:
        response = await sender.send(
          device_token=device_token,
          environment=claim.environment,
          payload=claim.payload,
          apns_id=claim.outbox_id,
          expires_at=claim.expires_at,
        )
      except (httpx.HTTPError, TimeoutError):
        response = ApnsProviderResponse(status_code=0, reason="TRANSPORT_ERROR")
      except ValueError:
        response = ApnsProviderResponse(
          status_code=400,
          reason="INVALID_DELIVERY_DATA",
        )
    async with session_factory() as db:
      outcome = await ApnsOutboxService(
        db,
        signing_key=signing_key,
        topic=topic,
        max_attempts=max_attempts,
        lease_seconds=lease_seconds,
      ).settle(claim, response, now=clock())
      await db.commit()
    if outcome == "SENT":
      counts["sent"] += 1
    elif outcome == "RETRY":
      counts["retried"] += 1
    elif outcome == "FAILED":
      counts["failed"] += 1
    elif outcome == "DISCARDED":
      counts["discarded"] += 1
  return ApnsDeliverySummary(**counts)


__all__ = [
  "ApnsClaimResult",
  "ApnsDeliveryClaim",
  "ApnsDeliverySummary",
  "ApnsOutboxService",
  "ApnsProviderClient",
  "ApnsProviderConfiguration",
  "ApnsProviderResponse",
  "ApnsSending",
  "build_minimal_apns_payload",
  "decrypt_push_token",
  "deliver_apns_batch",
]
