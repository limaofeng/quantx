from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from quantx_infrastructure.config.settings import Settings
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auth import AuthDeviceSession, AuthUser
from quantx_infrastructure.models.ios_notifications import (
  IosNotificationEvent,
  IosNotificationOutbox,
  IosPushCategoryPreference,
  IosPushRegistration,
)
from quantx_infrastructure.services.apns_delivery_service import (
  ApnsDeliverySummary,
  ApnsOutboxService,
  ApnsProviderClient,
  ApnsProviderConfiguration,
  ApnsProviderResponse,
  build_minimal_apns_payload,
  decrypt_push_token,
  deliver_apns_batch,
)
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SIGNING_KEY = b"apns-delivery-test-key-that-is-longer-than-thirty-two-bytes"
TOPIC = "com.quantx.personal"
DEVICE_TOKEN = "ab" * 40
ROTATED_DEVICE_TOKEN = "cd" * 40
NOW = datetime(2026, 8, 15, 1, 2, 3)
HTTP2_EXTENSIONS = {"http_version": b"HTTP/2"}


def _encrypted_token(token: str = DEVICE_TOKEN) -> str:
  derived = hashlib.sha256(b"quantx:apns-token:v1\0" + SIGNING_KEY).digest()
  return (
    Fernet(base64.urlsafe_b64encode(derived))
    .encrypt(token.encode("ascii"))
    .decode("ascii")
  )


def _token_fingerprint(token: str = DEVICE_TOKEN) -> str:
  return hmac.new(
    SIGNING_KEY,
    b"quantx:apns-token-fingerprint:v1\0" + token.encode("ascii"),
    hashlib.sha256,
  ).hexdigest()


def _private_key_pem() -> tuple[ec.EllipticCurvePrivateKey, bytes]:
  key = ec.generate_private_key(ec.SECP256R1())
  return key, key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
  )


def _decode_segment(value: str) -> bytes:
  return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@pytest.mark.asyncio
async def test_provider_uses_es256_http2_contract_and_minimal_payload():
  private_key, private_pem = _private_key_pem()
  requests: list[httpx.Request] = []

  async def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    return httpx.Response(
      200,
      headers={"apns-id": "5f7d9b16-7278-4c18-99c7-a9481f01b386"},
      extensions=HTTP2_EXTENSIONS,
    )

  def clock() -> float:
    return 1_765_000_000.0

  async with httpx.AsyncClient(
    transport=httpx.MockTransport(handler),
    http2=True,
  ) as http_client:
    configuration = ApnsProviderConfiguration(
      team_id="TEAMID1234",
      key_id="KEYID12345",
      topic=TOPIC,
      private_key_pem=private_pem,
    )
    assert private_pem.decode("ascii") not in repr(configuration)
    provider = ApnsProviderClient(
      configuration,
      client=http_client,
      clock=clock,
    )
    event_id = str(uuid.uuid4())
    apns_id = str(uuid.uuid4())
    expires_at = NOW + timedelta(hours=1)
    payload = build_minimal_apns_payload(
      event_id=event_id,
      category="ORDER_UPDATE",
      route_type="trading.orders",
    )
    response = await provider.send(
      device_token=DEVICE_TOKEN,
      environment="PRODUCTION",
      payload=payload,
      apns_id=apns_id,
      expires_at=expires_at,
    )

  assert response.succeeded is True
  assert provider.provider_token_generation == 1
  assert response.request_id == "5f7d9b16-7278-4c18-99c7-a9481f01b386"
  assert len(requests) == 1
  request = requests[0]
  assert request.url.host == "api.push.apple.com"
  assert request.url.path == f"/3/device/{DEVICE_TOKEN}"
  assert request.headers["apns-topic"] == TOPIC
  assert request.headers["apns-push-type"] == "alert"
  assert request.headers["apns-priority"] == "10"
  assert request.headers["apns-collapse-id"] == apns_id
  assert request.headers["apns-id"] == apns_id
  assert request.headers["apns-expiration"] == str(
    int(expires_at.replace(tzinfo=timezone.utc).timestamp())
  )
  assert request.headers["content-type"] == "application/json"
  assert json.loads(request.content) == payload
  serialized = request.content.decode("utf-8").lower()
  assert "account" not in serialized
  assert "price" not in serialized
  assert "volume" not in serialized

  provider_token = request.headers["authorization"].removeprefix("bearer ")
  header, claims, raw_signature = provider_token.split(".")
  assert json.loads(_decode_segment(header)) == {
    "alg": "ES256",
    "kid": "KEYID12345",
  }
  assert json.loads(_decode_segment(claims)) == {
    "iat": 1_765_000_000,
    "iss": "TEAMID1234",
  }
  signature = _decode_segment(raw_signature)
  assert len(signature) == 64
  r_value = int.from_bytes(signature[:32], "big")
  s_value = int.from_bytes(signature[32:], "big")
  private_key.public_key().verify(
    encode_dss_signature(r_value, s_value),
    f"{header}.{claims}".encode("ascii"),
    ec.ECDSA(hashes.SHA256()),
  )


@pytest.mark.asyncio
async def test_provider_rejects_enriched_payload_before_signing_or_network():
  _private_key, private_pem = _private_key_pem()
  requests: list[httpx.Request] = []

  async def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    return httpx.Response(200, extensions=HTTP2_EXTENSIONS)

  async with httpx.AsyncClient(
    transport=httpx.MockTransport(handler),
    http2=True,
  ) as http_client:
    provider = ApnsProviderClient(
      ApnsProviderConfiguration(
        team_id="TEAMID1234",
        key_id="KEYID12345",
        topic=TOPIC,
        private_key_pem=private_pem,
      ),
      client=http_client,
    )
    payload = build_minimal_apns_payload(
      event_id=str(uuid.uuid4()),
      category="ORDER_UPDATE",
      route_type="trading.orders",
    )
    payload["accountId"] = "ACCOUNT-1"

    with pytest.raises(ValueError, match="invalid APNs payload"):
      await provider.send(
        device_token=DEVICE_TOKEN,
        environment="PRODUCTION",
        payload=payload,
        apns_id=str(uuid.uuid4()),
        expires_at=NOW + timedelta(hours=1),
      )

  assert requests == []
  assert provider.provider_token_generation == 0


@pytest.mark.asyncio
async def test_provider_token_is_reused_for_fifty_minutes():
  _private_key, private_pem = _private_key_pem()
  current = [1_765_000_000.0]
  async with httpx.AsyncClient(
    transport=httpx.MockTransport(lambda _request: httpx.Response(200))
  ) as http_client:
    provider = ApnsProviderClient(
      ApnsProviderConfiguration(
        team_id="TEAMID1234",
        key_id="KEYID12345",
        topic=TOPIC,
        private_key_pem=private_pem,
      ),
      client=http_client,
      clock=lambda: current[0],
    )

    first = provider.provider_token()
    assert provider.provider_token_generation == 1
    current[0] += 49 * 60
    assert provider.provider_token() == first
    assert provider.provider_token_generation == 1
    current[0] += 60
    assert provider.provider_token() != first
    assert provider.provider_token_generation == 2


@pytest.mark.asyncio
async def test_owned_provider_client_requires_http2_and_ignores_proxy_environment(
  monkeypatch,
):
  service_module = importlib.import_module(
    "quantx_infrastructure.services.apns_delivery_service"
  )
  _private_key, private_pem = _private_key_pem()
  captured: dict[str, object] = {}

  class FakeAsyncClient:
    def __init__(self, **kwargs):
      captured.update(kwargs)

    async def aclose(self):
      captured["closed"] = True

  monkeypatch.setattr(service_module.httpx, "AsyncClient", FakeAsyncClient)
  provider = ApnsProviderClient(
    ApnsProviderConfiguration(
      team_id="TEAMID1234",
      key_id="KEYID12345",
      topic=TOPIC,
      private_key_pem=private_pem,
    )
  )

  assert captured == {"http2": True, "timeout": 10.0, "trust_env": False}
  await provider.close()
  assert captured["closed"] is True


@pytest.mark.asyncio
async def test_provider_fails_closed_when_http2_is_not_negotiated():
  _private_key, private_pem = _private_key_pem()

  async def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, extensions={"http_version": b"HTTP/1.1"})

  async with httpx.AsyncClient(
    transport=httpx.MockTransport(handler),
    http2=True,
  ) as http_client:
    provider = ApnsProviderClient(
      ApnsProviderConfiguration(
        team_id="TEAMID1234",
        key_id="KEYID12345",
        topic=TOPIC,
        private_key_pem=private_pem,
      ),
      client=http_client,
    )

    with pytest.raises(httpx.RemoteProtocolError, match="HTTP/2"):
      await provider.send(
        device_token=DEVICE_TOKEN,
        environment="PRODUCTION",
        payload=build_minimal_apns_payload(
          event_id=str(uuid.uuid4()),
          category="ORDER_UPDATE",
          route_type="trading.orders",
        ),
        apns_id=str(uuid.uuid4()),
        expires_at=NOW + timedelta(hours=1),
      )


@pytest.mark.asyncio
async def test_provider_refreshes_expired_jwt_once_and_honors_retry_after():
  _private_key, private_pem = _private_key_pem()
  requests: list[httpx.Request] = []

  async def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    if len(requests) == 1:
      return httpx.Response(
        403,
        json={"reason": "ExpiredProviderToken"},
        extensions=HTTP2_EXTENSIONS,
      )
    return httpx.Response(
      503,
      json={"reason": "ServiceUnavailable"},
      headers={"retry-after": "120"},
      extensions=HTTP2_EXTENSIONS,
    )

  async with httpx.AsyncClient(
    transport=httpx.MockTransport(handler),
    http2=True,
  ) as http_client:
    provider = ApnsProviderClient(
      ApnsProviderConfiguration(
        team_id="TEAMID1234",
        key_id="KEYID12345",
        topic=TOPIC,
        private_key_pem=private_pem,
      ),
      client=http_client,
      clock=lambda: 1_765_000_000.0,
    )
    response = await provider.send(
      device_token=DEVICE_TOKEN,
      environment="SANDBOX",
      payload=build_minimal_apns_payload(
        event_id=str(uuid.uuid4()),
        category="RISK_SAFETY",
        route_type="trading.safety",
      ),
      apns_id=str(uuid.uuid4()),
      expires_at=NOW + timedelta(hours=1),
    )

  assert len(requests) == 2
  assert requests[0].url.host == "api.sandbox.push.apple.com"
  assert requests[0].headers["authorization"] != requests[1].headers["authorization"]
  assert response.reason == "ServiceUnavailable"
  assert response.retry_after_seconds == 120
  assert response.retry_delay_seconds(attempt_count=1) == 900
  assert (
    ApnsProviderResponse(
      status_code=429,
      reason="TooManyRequests",
      retry_after_seconds=120,
    ).retry_delay_seconds(attempt_count=1)
    == 120
  )


@pytest.mark.asyncio
async def test_provider_parses_410_token_invalidation_timestamp():
  _private_key, private_pem = _private_key_pem()
  invalidated_at = NOW + timedelta(seconds=30)

  async def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
      410,
      json={
        "reason": "Unregistered",
        "timestamp": int(
          invalidated_at.replace(tzinfo=timezone.utc).timestamp() * 1000
        ),
      },
      extensions=HTTP2_EXTENSIONS,
    )

  async with httpx.AsyncClient(
    transport=httpx.MockTransport(handler),
    http2=True,
  ) as http_client:
    provider = ApnsProviderClient(
      ApnsProviderConfiguration(
        team_id="TEAMID1234",
        key_id="KEYID12345",
        topic=TOPIC,
        private_key_pem=private_pem,
      ),
      client=http_client,
    )
    response = await provider.send(
      device_token=DEVICE_TOKEN,
      environment="PRODUCTION",
      payload=build_minimal_apns_payload(
        event_id=str(uuid.uuid4()),
        category="ACTION_REQUIRED",
        route_type="today.action",
      ),
      apns_id=str(uuid.uuid4()),
      expires_at=NOW + timedelta(hours=1),
    )

  assert response.invalid_device is True
  assert response.token_invalidated_at == invalidated_at


@pytest.fixture
async def apns_database():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    AuthDeviceSession.__table__,
    IosPushRegistration.__table__,
    IosPushCategoryPreference.__table__,
    IosNotificationEvent.__table__,
    IosNotificationOutbox.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="owner",
        display_name="Owner",
        password_hash="hash",
        is_active=True,
        permissions=["notification:manage"],
      )
    )
    db.add(
      AuthDeviceSession(
        id="session-1",
        user_id="user-1",
        refresh_token_hash="1" * 64,
        expires_at=NOW + timedelta(days=1),
        revoked_at=None,
        last_used_at=NOW,
        device_name="iPhone",
        active_account_id="ACCOUNT-1",
        granted_permissions=["notification:manage"],
      )
    )
    db.add(
      IosPushRegistration(
        id="registration-1",
        user_id="user-1",
        device_session_id="session-1",
        account_id="ACCOUNT-1",
        device_install_id="5ad0c4c4-1e2b-48c7-955b-3604526d3978",
        app_bundle_id=TOPIC,
        app_version="1.0",
        apns_environment="SANDBOX",
        token_ciphertext=_encrypted_token(),
        token_fingerprint=_token_fingerprint(),
        registered_at=NOW,
        last_seen_at=NOW,
        invalidated_at=None,
        created_at=NOW,
        updated_at=NOW,
      )
    )
    db.add(
      IosPushCategoryPreference(
        registration_id="registration-1",
        category="ACTION_REQUIRED",
        enabled=True,
        created_at=NOW,
        updated_at=NOW,
      )
    )
    db.add_all(
      [
        IosNotificationEvent(
          id="c43c3d81-afbc-4700-b8e9-39c950de37ea",
          user_id="user-1",
          device_session_id="session-1",
          account_id="ACCOUNT-1",
          category="ACTION_REQUIRED",
          route_type="today.action",
          occurred_at=NOW,
          expires_at=NOW + timedelta(hours=1),
          created_at=NOW,
          updated_at=NOW,
        ),
        IosNotificationEvent(
          id="fdf2c626-1d45-49f8-8ff2-c13dbef454de",
          user_id="user-1",
          device_session_id="session-1",
          account_id="ACCOUNT-1",
          category="ACTION_REQUIRED",
          route_type="today.action",
          occurred_at=NOW,
          expires_at=NOW + timedelta(hours=1),
          created_at=NOW,
          updated_at=NOW,
        ),
      ]
    )
    db.add_all(
      [
        IosNotificationOutbox(
          id="281b9712-ebc8-46a6-a753-c1868d46e720",
          event_id="c43c3d81-afbc-4700-b8e9-39c950de37ea",
          registration_id="registration-1",
          status="PENDING",
          attempt_count=0,
          available_at=NOW,
          created_at=NOW,
          updated_at=NOW,
        ),
        IosNotificationOutbox(
          id="500dd7d5-fc6b-46f1-bb6f-c9e396176412",
          event_id="fdf2c626-1d45-49f8-8ff2-c13dbef454de",
          registration_id="registration-1",
          status="PENDING",
          attempt_count=0,
          available_at=NOW + timedelta(minutes=5),
          created_at=NOW,
          updated_at=NOW,
        ),
      ]
    )
    await db.commit()
  yield sessions
  await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_claim_is_leased_and_success_is_not_reported_as_delivery_early(
  apns_database,
):
  async with apns_database() as db:
    service = ApnsOutboxService(db, signing_key=SIGNING_KEY, topic=TOPIC)
    claim = await service.claim_next(now=NOW)
    assert claim is not None
    assert (
      decrypt_push_token(
        signing_key=SIGNING_KEY,
        ciphertext=claim.token_ciphertext,
      )
      == DEVICE_TOKEN
    )
    assert DEVICE_TOKEN not in repr(claim)
    assert claim.token_ciphertext not in repr(claim)
    row = await db.get(IosNotificationOutbox, claim.outbox_id)
    assert row.status == "RETRY"
    assert row.sent_at is None
    assert row.available_at == NOW + timedelta(seconds=120)
    outcome = await service.settle(
      claim,
      ApnsProviderResponse(
        status_code=200,
        reason="OK",
        request_id="91d85a34-80c7-4e8d-b4ea-79a15fd54144",
      ),
      now=NOW + timedelta(seconds=1),
    )
    await db.commit()

  assert outcome == "SENT"
  async with apns_database() as db:
    row = await db.get(IosNotificationOutbox, claim.outbox_id)
    assert row.status == "SENT"
    assert row.sent_at == NOW + timedelta(seconds=1)
    assert row.apns_request_id == "91d85a34-80c7-4e8d-b4ea-79a15fd54144"


@pytest.mark.asyncio
async def test_unregistered_response_purges_token_and_discards_remaining_outbox(
  apns_database,
):
  async with apns_database() as db:
    service = ApnsOutboxService(db, signing_key=SIGNING_KEY, topic=TOPIC)
    claim = await service.claim_next(now=NOW)
    assert claim is not None
    outcome = await service.settle(
      claim,
      ApnsProviderResponse(
        status_code=410,
        reason="Unregistered",
        token_invalidated_at=NOW + timedelta(seconds=1),
      ),
      now=NOW + timedelta(seconds=1),
    )
    await db.commit()

  assert outcome == "DISCARDED"
  async with apns_database() as db:
    registration = await db.get(IosPushRegistration, "registration-1")
    assert registration.invalidated_at == NOW + timedelta(seconds=1)
    assert registration.token_ciphertext is None
    rows = (
      await db.execute(select(IosNotificationOutbox).order_by(IosNotificationOutbox.id))
    ).scalars()
    assert {row.status for row in rows} == {"DISCARDED"}


@pytest.mark.asyncio
async def test_retryable_provider_error_uses_bounded_backoff(apns_database):
  async with apns_database() as db:
    service = ApnsOutboxService(db, signing_key=SIGNING_KEY, topic=TOPIC)
    claim = await service.claim_next(now=NOW)
    assert claim is not None
    outcome = await service.settle(
      claim,
      ApnsProviderResponse(status_code=503, reason="ServiceUnavailable"),
      now=NOW + timedelta(seconds=1),
    )
    await db.commit()

  assert outcome == "RETRY"
  async with apns_database() as db:
    row = await db.get(IosNotificationOutbox, claim.outbox_id)
    assert row.status == "RETRY"
    assert row.attempt_count == 1
    assert row.available_at == NOW + timedelta(seconds=901)
    assert row.last_error_code == "ServiceUnavailable"


@pytest.mark.asyncio
async def test_batch_converts_transport_failure_to_retry_without_sensitive_error(
  apns_database,
):
  class FailingSender:
    async def send(self, **_kwargs):
      raise httpx.ConnectError(f"failed to send token {DEVICE_TOKEN}")

  summary = await deliver_apns_batch(
    session_factory=apns_database,
    sender=FailingSender(),
    signing_key=SIGNING_KEY,
    topic=TOPIC,
    batch_size=1,
    clock=lambda: NOW,
  )

  assert summary.claimed == 1
  assert summary.retried == 1
  async with apns_database() as db:
    row = await db.get(
      IosNotificationOutbox,
      "281b9712-ebc8-46a6-a753-c1868d46e720",
    )
    assert row.last_error_code == "TRANSPORT_ERROR"
    assert DEVICE_TOKEN not in repr(row.last_error_code)


@pytest.mark.asyncio
async def test_claim_query_locks_only_outbox_on_postgresql():
  class EmptyResult:
    def one_or_none(self):
      return None

  class CapturingDatabase:
    statement = None

    async def execute(self, statement):
      self.statement = statement
      return EmptyResult()

  database = CapturingDatabase()
  service = ApnsOutboxService(database, signing_key=SIGNING_KEY, topic=TOPIC)

  assert await service.claim_next(now=NOW) is None
  compiled = str(
    database.statement.compile(
      dialect=postgresql.dialect(),
      compile_kwargs={"literal_binds": True},
    )
  ).replace("\n", " ")
  assert "FOR UPDATE OF ios_notification_outbox SKIP LOCKED" in compiled


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_and_old_settlement_is_stale(
  apns_database,
):
  async with apns_database() as db:
    first = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).claim_next(now=NOW)
    assert first is not None
    await db.commit()

  async with apns_database() as db:
    unavailable = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).claim_next(now=NOW + timedelta(seconds=119))
    assert unavailable is None
    await db.commit()

  async with apns_database() as db:
    reclaimed = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).claim_next(now=NOW + timedelta(seconds=121))
    assert reclaimed is not None
    assert reclaimed.outbox_id == first.outbox_id
    assert reclaimed.attempt_count == first.attempt_count + 1
    await db.commit()

  async with apns_database() as db:
    stale = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).settle(
      first,
      ApnsProviderResponse(status_code=200, reason="OK"),
      now=NOW + timedelta(seconds=122),
    )
    assert stale == "STALE"
    await db.commit()

  async with apns_database() as db:
    sent = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).settle(
      reclaimed,
      ApnsProviderResponse(status_code=200, reason="OK"),
      now=NOW + timedelta(seconds=123),
    )
    assert sent == "SENT"
    await db.commit()


@pytest.mark.asyncio
async def test_old_410_cannot_invalidate_a_rotated_token(apns_database):
  async with apns_database() as db:
    claim = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).claim_next(now=NOW)
    assert claim is not None
    await db.commit()

  rotated_ciphertext = _encrypted_token(ROTATED_DEVICE_TOKEN)
  async with apns_database() as db:
    registration = await db.get(IosPushRegistration, "registration-1")
    registration.token_ciphertext = rotated_ciphertext
    registration.token_fingerprint = "b" * 64
    registration.registered_at = NOW + timedelta(milliseconds=500)
    registration.updated_at = NOW + timedelta(milliseconds=500)
    await db.commit()

  async with apns_database() as db:
    outcome = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).settle(
      claim,
      ApnsProviderResponse(
        status_code=410,
        reason="Unregistered",
        token_invalidated_at=NOW + timedelta(seconds=1),
      ),
      now=NOW + timedelta(seconds=1),
    )
    assert outcome == "RETRY"
    await db.commit()

  async with apns_database() as db:
    registration = await db.get(IosPushRegistration, "registration-1")
    assert registration.invalidated_at is None
    assert registration.token_ciphertext == rotated_ciphertext
    outbox = await db.get(IosNotificationOutbox, claim.outbox_id)
    assert outbox.status == "RETRY"
    assert outbox.available_at == NOW + timedelta(seconds=1)
    assert outbox.last_error_code == "TOKEN_ROTATED"


@pytest.mark.asyncio
async def test_corrupt_token_is_discarded_without_calling_sender(apns_database):
  async with apns_database() as db:
    registration = await db.get(IosPushRegistration, "registration-1")
    registration.token_ciphertext = "not-a-fernet-token"
    await db.commit()

  class ForbiddenSender:
    async def send(self, **_kwargs):
      raise AssertionError("network sender must not be called")

  summary = await deliver_apns_batch(
    session_factory=apns_database,
    sender=ForbiddenSender(),
    signing_key=SIGNING_KEY,
    topic=TOPIC,
    batch_size=1,
    clock=lambda: NOW,
  )

  assert summary.claimed == 1
  assert summary.discarded == 1
  async with apns_database() as db:
    registration = await db.get(IosPushRegistration, "registration-1")
    assert registration.invalidated_at is not None
    assert registration.token_ciphertext is None


@pytest.mark.asyncio
async def test_mismatched_token_fingerprint_is_discarded_without_network(
  apns_database,
):
  async with apns_database() as db:
    registration = await db.get(IosPushRegistration, "registration-1")
    registration.token_fingerprint = "b" * 64
    await db.commit()

  class ForbiddenSender:
    async def send(self, **_kwargs):
      raise AssertionError("network sender must not be called")

  summary = await deliver_apns_batch(
    session_factory=apns_database,
    sender=ForbiddenSender(),
    signing_key=SIGNING_KEY,
    topic=TOPIC,
    batch_size=1,
    clock=lambda: NOW,
  )

  assert summary.claimed == 1
  assert summary.discarded == 1
  async with apns_database() as db:
    registration = await db.get(IosPushRegistration, "registration-1")
    assert registration.invalidated_at is not None
    assert registration.token_ciphertext is None


@pytest.mark.asyncio
async def test_max_attempts_fails_closed(apns_database):
  async with apns_database() as db:
    service = ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
      max_attempts=1,
    )
    claim = await service.claim_next(now=NOW)
    assert claim is not None
    outcome = await service.settle(
      claim,
      ApnsProviderResponse(status_code=503, reason="ServiceUnavailable"),
      now=NOW + timedelta(seconds=1),
    )
    assert outcome == "FAILED"
    await db.commit()

  async with apns_database() as db:
    outbox = await db.get(IosNotificationOutbox, claim.outbox_id)
    assert outbox.status == "FAILED"
    assert outbox.attempt_count == 1
    assert outbox.last_error_code == "ServiceUnavailable"


@pytest.mark.asyncio
async def test_account_drift_discards_without_decrypting_token(apns_database):
  async with apns_database() as db:
    session = await db.get(AuthDeviceSession, "session-1")
    session.active_account_id = "ACCOUNT-2"
    await db.commit()

  async with apns_database() as db:
    claim_result = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).claim_next_result(now=NOW + timedelta(minutes=6))
    assert claim_result.claim is None
    assert claim_result.discarded == 2
    assert claim_result.failed == 0
    await db.commit()

  async with apns_database() as db:
    rows = (await db.execute(select(IosNotificationOutbox))).scalars().all()
    assert {row.status for row in rows} == {"DISCARDED"}
    assert {row.last_error_code for row in rows} == {"SESSION_INACTIVE"}


@pytest.mark.asyncio
async def test_inactive_user_discards_without_decrypting_token(apns_database):
  async with apns_database() as db:
    user = await db.get(AuthUser, "user-1")
    user.is_active = False
    await db.commit()

  async with apns_database() as db:
    claim_result = await ApnsOutboxService(
      db,
      signing_key=SIGNING_KEY,
      topic=TOPIC,
    ).claim_next_result(now=NOW)
    assert claim_result.claim is None
    assert claim_result.discarded == 1
    await db.commit()

  async with apns_database() as db:
    row = await db.get(
      IosNotificationOutbox,
      "281b9712-ebc8-46a6-a753-c1868d46e720",
    )
    assert row.status == "DISCARDED"
    assert row.last_error_code == "SESSION_INACTIVE"


@pytest.mark.asyncio
async def test_worker_disabled_by_default_performs_no_key_or_network_setup(
  monkeypatch,
):
  flow_module = importlib.import_module(
    "quantx_worker.prefector.flows.apns_delivery_flow"
  )
  assert Settings.model_fields["apns_delivery_enabled"].default is False
  configured = Settings(apns_delivery_enabled=False)
  monkeypatch.setattr(
    flow_module,
    "_provider_configuration",
    lambda _configured: pytest.fail("provider configuration must stay untouched"),
  )
  monkeypatch.setattr(
    flow_module,
    "_notification_signing_key",
    lambda _configured: pytest.fail("signing key must stay untouched"),
  )
  monkeypatch.setattr(
    flow_module,
    "deliver_apns_batch",
    lambda **_kwargs: pytest.fail("database and sender must stay untouched"),
  )

  result = await flow_module.run_apns_delivery(configured)

  assert result == {
    "status": "disabled",
    "claimed": 0,
    "sent": 0,
    "retried": 0,
    "failed": 0,
    "discarded": 0,
  }


@pytest.mark.asyncio
async def test_worker_rejects_lease_shorter_than_two_request_timeouts(
  monkeypatch,
):
  flow_module = importlib.import_module(
    "quantx_worker.prefector.flows.apns_delivery_flow"
  )
  configured = Settings(
    apns_delivery_enabled=True,
    apns_lease_seconds=30,
    apns_timeout_seconds=10,
  )
  monkeypatch.setattr(
    flow_module,
    "_provider_configuration",
    lambda _configured: pytest.fail("provider setup must stay untouched"),
  )

  with pytest.raises(RuntimeError, match="request safety window"):
    await flow_module.run_apns_delivery(configured)


@pytest.mark.asyncio
async def test_worker_reuses_one_provider_and_signs_no_token_for_empty_window(
  monkeypatch,
):
  flow_module = importlib.import_module(
    "quantx_worker.prefector.flows.apns_delivery_flow"
  )
  current = [0.0]
  batch_times: list[float] = []
  providers: list[object] = []

  class FakeProvider:
    provider_token_generation = 0
    closed = False

    def __init__(self, _configuration):
      providers.append(self)

    async def close(self):
      self.closed = True

  async def empty_batch(**_kwargs):
    batch_times.append(current[0])
    return ApnsDeliverySummary()

  async def advance(seconds: float):
    current[0] += seconds

  monkeypatch.setattr(flow_module, "ApnsProviderClient", FakeProvider)
  monkeypatch.setattr(flow_module, "_provider_configuration", lambda _config: object())
  monkeypatch.setattr(
    flow_module,
    "_notification_signing_key",
    lambda _config: SIGNING_KEY,
  )
  monkeypatch.setattr(flow_module, "deliver_apns_batch", empty_batch)
  configured = Settings(
    apns_delivery_enabled=True,
    apns_topic=TOPIC,
    apns_delivery_window_seconds=60,
    apns_poll_interval_seconds=20,
  )

  result = await flow_module.run_apns_delivery(
    configured,
    monotonic_clock=lambda: current[0],
    sleeper=advance,
  )

  assert batch_times == [0.0, 20.0, 40.0]
  assert len(providers) == 1
  assert providers[0].provider_token_generation == 0
  assert providers[0].closed is True
  assert result == {"status": "completed", **asdict(ApnsDeliverySummary())}


@pytest.mark.asyncio
async def test_worker_keeps_fixed_window_when_provider_token_is_first_generated_late(
  monkeypatch,
):
  flow_module = importlib.import_module(
    "quantx_worker.prefector.flows.apns_delivery_flow"
  )
  current = [0.0]
  batch_times: list[float] = []

  class FakeProvider:
    provider_token_generation = 0
    closed = False

    async def close(self):
      self.closed = True

  provider = FakeProvider()

  async def batches(**kwargs):
    assert kwargs["sender"] is provider
    batch_times.append(current[0])
    if len(batch_times) == 3:
      provider.provider_token_generation += 1
    return ApnsDeliverySummary(sent=1 if len(batch_times) == 3 else 0)

  async def advance(seconds: float):
    current[0] += seconds

  monkeypatch.setattr(flow_module, "ApnsProviderClient", lambda _config: provider)
  monkeypatch.setattr(flow_module, "_provider_configuration", lambda _config: object())
  monkeypatch.setattr(
    flow_module,
    "_notification_signing_key",
    lambda _config: SIGNING_KEY,
  )
  monkeypatch.setattr(flow_module, "deliver_apns_batch", batches)
  configured = Settings(
    apns_delivery_enabled=True,
    apns_topic=TOPIC,
    apns_delivery_window_seconds=60,
    apns_poll_interval_seconds=20,
  )

  result = await flow_module.run_apns_delivery(
    configured,
    monotonic_clock=lambda: current[0],
    sleeper=advance,
  )

  assert batch_times == [0.0, 20.0, 40.0]
  assert provider.closed is True
  assert result["sent"] == 1


@pytest.mark.asyncio
async def test_worker_cancellation_closes_provider_without_swallowing(
  monkeypatch,
):
  flow_module = importlib.import_module(
    "quantx_worker.prefector.flows.apns_delivery_flow"
  )

  class FakeProvider:
    provider_token_generation = 0
    closed = False

    async def close(self):
      self.closed = True

  provider = FakeProvider()

  async def cancel_batch(**_kwargs):
    raise asyncio.CancelledError

  monkeypatch.setattr(flow_module, "ApnsProviderClient", lambda _config: provider)
  monkeypatch.setattr(flow_module, "_provider_configuration", lambda _config: object())
  monkeypatch.setattr(
    flow_module,
    "_notification_signing_key",
    lambda _config: SIGNING_KEY,
  )
  monkeypatch.setattr(flow_module, "deliver_apns_batch", cancel_batch)
  configured = Settings(apns_delivery_enabled=True, apns_topic=TOPIC)

  with pytest.raises(asyncio.CancelledError):
    await flow_module.run_apns_delivery(configured)

  assert provider.closed is True
