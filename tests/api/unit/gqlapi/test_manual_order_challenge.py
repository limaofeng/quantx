from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi import manual_order, trade_approval
from quantx_api.gqlapi.manual_order import (
  ManualOrderChallengeService,
  ManualOrderPreflightData,
  normalize_manual_order_request,
)
from quantx_api.gqlapi.trade_approval import TradeApprovalChallengeError
from quantx_domain.clock import utcnow
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models import (
  Account,
  AccountTradingRollout,
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
  Instrument,
  Position,
  TradeCommandOutbox,
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services import trade_command_service as command_module
from quantx_infrastructure.services.trade_command_service import QueuedTradeCommand
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _principal(*, device_session_id: str = "device-session-1") -> Principal:
  return Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id=device_session_id,
    access_token_expires_at=utcnow() + timedelta(minutes=5),
    permissions=frozenset({"trade:manual"}),
    authorized_account_ids=("ACCOUNT-1",),
  )


def _request(*, key: str = "ios-order-1"):
  return normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    side="BUY",
    price_type="LIMIT",
    volume=100,
    limit_price=10.5,
    idempotency_key=key,
  )


_MUTABLE_SNAPSHOT_AT = datetime(2026, 8, 15, 10, 0, 0)


def _preflight_data(
  *,
  quote_timestamp=None,
  quote_fingerprint: str = "quote-fingerprint-1",
  reference_price: float = 10.5,
  requested_volume: int = 100,
  final_volume: int = 100,
  rollout_snapshot_id: str = "snapshot-1",
  rollout_snapshot_hash: str = "snapshot-hash-1",
  account_updated_at: datetime = _MUTABLE_SNAPSHOT_AT,
  position_updated_at=None,
  risk_action: str = "ALLOW",
  risk_reason_code: str = "OK",
  risk_reason_detail: str = "",
) -> ManualOrderPreflightData:
  return ManualOrderPreflightData(
    quote_timestamp=quote_timestamp or time_utils.now_aware(),
    quote_fingerprint=quote_fingerprint,
    reference_price=reference_price,
    requested_volume=requested_volume,
    final_volume=final_volume,
    estimated_amount=1050.0,
    estimated_fees=None,
    available_cash=100000.0,
    available_volume=None,
    rollout_snapshot_id=rollout_snapshot_id,
    rollout_snapshot_hash=rollout_snapshot_hash,
    account_updated_at=account_updated_at,
    position_updated_at=position_updated_at,
    risk_decision_id="risk-placeholder",
    risk_action=risk_action,
    risk_reason_code=risk_reason_code,
    risk_reason_detail=risk_reason_detail,
    warnings=["fees unavailable"],
  )


@pytest.fixture
async def challenge_database(monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AuthUser.__table__,
          AuthUserAccountAccess.__table__,
          AuthDeviceSession.__table__,
          TradeConfirmationChallenge.__table__,
          TradeCommandOutbox.__table__,
        ],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="operator",
        display_name="Operator",
        password_hash="hash",
        is_active=True,
        permissions=["trade:manual"],
      )
    )
    db.add(
      AuthDeviceSession(
        id="device-session-1",
        user_id="user-1",
        refresh_token_hash="r" * 64,
        expires_at=utcnow() + timedelta(hours=1),
        revoked_at=None,
        last_used_at=utcnow(),
        device_name="iPhone",
      )
    )
    db.add(
      AuthUserAccountAccess(
        user_id="user-1",
        account_id="ACCOUNT-1",
        is_default=True,
      )
    )
    await db.commit()
  monkeypatch.setattr(manual_order, "AsyncSessionLocal", session_factory)
  monkeypatch.setattr(
    trade_approval,
    "settings",
    SimpleNamespace(
      secret_key="test-trade-confirmation-signing-key-at-least-32-bytes",
      algorithm="HS256",
    ),
  )
  yield session_factory
  await engine.dispose()


@pytest.mark.asyncio
async def test_manual_order_challenge_is_bound_consumed_once_and_queues_once(
  challenge_database,
  monkeypatch,
):
  preflight_calls = 0

  async def preflight(_request, **_kwargs):
    nonlocal preflight_calls
    preflight_calls += 1
    return _preflight_data()

  queued_calls = []

  async def enqueue(service, **kwargs):
    queued_calls.append(kwargs)
    return QueuedTradeCommand("client-order-1", "message-1", "QUEUED")

  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)

  preview = await ManualOrderChallengeService.issue(
    principal=_principal(),
    request=_request(),
  )
  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert stored.action == "MANUAL_ORDER"
    assert preview.confirmation_token not in str(stored.payload)
    assert preview.confirmation_token not in stored.token_digest
    assert stored.device_session_id == "device-session-1"
    assert stored.idempotency_key == "ios-order-1"

  result = await ManualOrderChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert result.client_order_id == "client-order-1"
  assert result.status == "QUEUED"
  assert preflight_calls == 2
  assert len(queued_calls) == 1
  assert queued_calls[0]["manual_live"] is True
  assert queued_calls[0]["execution_mode"] == "live"
  assert queued_calls[0]["commit_transaction"] is False
  assert queued_calls[0]["risk_decision_id"] == manual_order._stable_risk_decision_id(
    preview.challenge_id
  )
  assert queued_calls[0]["reason_tags"] == ["MOBILE_MANUAL_ORDER", "OK"]
  assert queued_calls[0]["idempotency_key"] == (
    f"manual-order:{preview.challenge_id}:ios-order-1"
  )

  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert stored.consumed_at is not None
    assert stored.result_reference == {
      "client_order_id": "client-order-1",
      "message_id": "message-1",
      "status": "QUEUED",
    }

  replay = await ManualOrderChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert replay == result
  assert len(queued_calls) == 1


@pytest.mark.asyncio
async def test_consumed_confirmation_recovers_committed_outbox_after_timeout(
  challenge_database,
  monkeypatch,
):
  preflight_calls = 0

  async def preflight(_request, **_kwargs):
    nonlocal preflight_calls
    preflight_calls += 1
    return _preflight_data()

  async def must_not_enqueue(*_args, **_kwargs):
    raise AssertionError("idempotent retry must never enqueue another command")

  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(
    manual_order.TradeCommandService,
    "enqueue_order",
    must_not_enqueue,
  )
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(),
    request=_request(key="ios-timeout-1"),
  )

  digest = manual_order.TradeCommandService.order_idempotency_digest(
    user_id="user-1",
    account_id="ACCOUNT-1",
    idempotency_key=(
      f"manual-order:{preview.challenge_id}:ios-timeout-1"
    ),
  )
  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    stored.consumed_at = time_utils.now()
    stored.result_reference = None
    db.add(
      TradeCommandOutbox(
        message_id="message-timeout-1",
        client_order_id="client-timeout-1",
        idempotency_key=digest,
        device_id="qmt-device-1",
        account_id="ACCOUNT-1",
        payload={"command_kind": "PLACE_ORDER"},
        delivery_status="QUEUED",
        expires_at=time_utils.now() + timedelta(minutes=2),
        attempts=0,
      )
    )
    await db.commit()

  result = await ManualOrderChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert result.client_order_id == "client-timeout-1"
  assert result.status == "QUEUED"
  assert preflight_calls == 1

  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert stored.result_reference["message_id"] == "message-timeout-1"


@pytest.mark.asyncio
async def test_consumed_confirmation_can_recover_result_after_challenge_ttl(
  challenge_database,
  monkeypatch,
):
  async def preflight(_request, **_kwargs):
    return _preflight_data()

  async def enqueue(service, **_kwargs):
    return QueuedTradeCommand("client-expired-1", "message-expired-1", "QUEUED")

  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(),
    request=_request(key="ios-expired-retry-1"),
  )
  first = await ManualOrderChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )

  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    stored.expires_at = time_utils.now() - timedelta(seconds=1)
    await db.commit()

  replay = await ManualOrderChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert replay == first


@pytest.mark.asyncio
async def test_shenzhen_best_order_uses_exact_qmt_peer_price_contract(
  challenge_database,
  monkeypatch,
):
  async def preflight(_request, **_kwargs):
    return _preflight_data()

  queued_calls = []

  async def enqueue(service, **kwargs):
    queued_calls.append(kwargs)
    return QueuedTradeCommand("client-best-1", "message-best-1", "QUEUED")

  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  request = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="000001.SZ",
    side="BUY",
    price_type="BEST",
    volume=100,
    limit_price=None,
    idempotency_key="ios-best-1",
  )
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(),
    request=request,
  )

  await ManualOrderChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )

  assert len(queued_calls) == 1
  assert queued_calls[0]["order_type"] == "MARKET_PEER_PRICE_FIRST"
  assert queued_calls[0]["limit_price"] == Decimal("0")


@pytest.mark.asyncio
async def test_manual_order_challenge_rejects_device_and_payload_changes(
  challenge_database,
  monkeypatch,
):
  async def preflight(_request, **_kwargs):
    return _preflight_data()

  monkeypatch.setattr(manual_order, "_preflight", preflight)
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(),
    request=_request(),
  )

  with pytest.raises(TradeApprovalChallengeError) as device_mismatch:
    await ManualOrderChallengeService.confirm(
      principal=_principal(device_session_id="device-session-2"),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert device_mismatch.value.code == "CONFIRMATION_CONTEXT_MISMATCH"

  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    stored.payload = {**stored.payload, "volume": 200}
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as payload_changed:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert payload_changed.value.code == "TRADE_PAYLOAD_CHANGED"


@pytest.mark.asyncio
async def test_cap_preview_binds_both_volumes_and_confirm_queues_only_final_volume(
  challenge_database,
  monkeypatch,
):
  capped = _preflight_data(
    requested_volume=150,
    final_volume=100,
    risk_action="CAP",
    risk_reason_code="BUY_LOT_NORMALIZED",
    risk_reason_detail="150 capped to 100",
  )

  async def preflight(_request, **_kwargs):
    return capped

  queued_calls = []

  async def enqueue(_service, **kwargs):
    queued_calls.append(kwargs)
    return QueuedTradeCommand("client-cap-1", "message-cap-1", "QUEUED")

  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  request = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    side="BUY",
    price_type="LIMIT",
    volume=150,
    limit_price=10.5,
    idempotency_key="ios-cap-1",
  )

  preview = await ManualOrderChallengeService.issue(
    principal=_principal(),
    request=request,
  )
  assert preview.preflight.requested_volume == 150
  assert preview.preflight.final_volume == 100
  assert preview.preflight.risk_action == "CAP"

  await ManualOrderChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )

  assert len(queued_calls) == 1
  assert queued_calls[0]["volume"] == 100
  assert queued_calls[0]["request_metadata"]["requested_volume"] == 150
  assert queued_calls[0]["request_metadata"]["final_volume"] == 100
  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert stored.payload["requested_volume"] == 150
    assert stored.payload["final_volume"] == 100


@pytest.mark.asyncio
async def test_cap_payload_tamper_is_rejected_before_enqueue(
  challenge_database,
  monkeypatch,
):
  capped = _preflight_data(
    requested_volume=150,
    final_volume=100,
    risk_action="CAP",
    risk_reason_code="BUY_LOT_NORMALIZED",
  )

  async def preflight(_request, **_kwargs):
    return capped

  enqueue = AsyncMock()
  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  request = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    side="BUY",
    price_type="LIMIT",
    volume=150,
    limit_price=10.5,
    idempotency_key="ios-cap-tamper-1",
  )
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=request
  )
  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    stored.payload = {**stored.payload, "final_volume": 150}
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert rejected.value.code == "TRADE_PAYLOAD_CHANGED"
  enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_recomputed_cap_change_requires_new_preview_and_creates_no_outbox(
  challenge_database,
  monkeypatch,
):
  calls = 0

  async def preflight(_request, **_kwargs):
    nonlocal calls
    calls += 1
    final_volume = 200 if calls == 1 else 100
    return _preflight_data(
      requested_volume=300,
      final_volume=final_volume,
      risk_action="CAP",
      risk_reason_code="SELL_VOLUME_NORMALIZED_OR_CAPPED",
    )

  enqueue = AsyncMock()
  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  request = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    side="SELL",
    price_type="LIMIT",
    volume=300,
    limit_price=10.5,
    idempotency_key="ios-cap-change-1",
  )
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=request
  )

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert rejected.value.code == "RISK_DECISION_CHANGED"
  enqueue.assert_not_awaited()
  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert stored.consumed_at is None
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0


@pytest.mark.asyncio
async def test_snapshot_change_after_preview_requires_new_preview(
  challenge_database,
  monkeypatch,
):
  calls = 0

  async def preflight(_request, **_kwargs):
    nonlocal calls
    calls += 1
    return _preflight_data(
      rollout_snapshot_id=f"snapshot-{calls}",
      rollout_snapshot_hash=f"snapshot-hash-{calls}",
    )

  enqueue = AsyncMock()
  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=_request(key="ios-snapshot-change-1")
  )

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert rejected.value.code == "ACCOUNT_SNAPSHOT_CHANGED"
  enqueue.assert_not_awaited()
  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert stored.consumed_at is None
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0


@pytest.mark.asyncio
async def test_best_quote_change_and_expiry_are_fail_closed(
  challenge_database,
  monkeypatch,
):
  request = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="000001.SZ",
    side="BUY",
    price_type="BEST",
    volume=100,
    limit_price=None,
    idempotency_key="ios-best-change-1",
  )
  calls = 0

  async def changed_preflight(_request, **_kwargs):
    nonlocal calls
    calls += 1
    return _preflight_data(
      quote_fingerprint=f"quote-{calls}",
      reference_price=10.5 + calls / 100,
    )

  enqueue = AsyncMock()
  monkeypatch.setattr(manual_order, "_preflight", changed_preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=request
  )

  with pytest.raises(TradeApprovalChallengeError) as changed:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert changed.value.code == "BEST_QUOTE_CHANGED"
  enqueue.assert_not_awaited()

  expired_data = _preflight_data(
    quote_timestamp=time_utils.now_aware() - timedelta(seconds=11),
  )

  async def expired_preflight(_request, **_kwargs):
    return expired_data

  monkeypatch.setattr(manual_order, "_preflight", expired_preflight)
  expired_request = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="000001.SZ",
    side="BUY",
    price_type="BEST",
    volume=100,
    limit_price=None,
    idempotency_key="ios-best-expired-1",
  )
  expired_preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=expired_request
  )
  with pytest.raises(TradeApprovalChallengeError) as expired:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=expired_preview.challenge_id,
      confirmation_token=expired_preview.confirmation_token,
    )
  assert expired.value.code == "BEST_PREVIEW_EXPIRED"


@pytest.mark.asyncio
async def test_revoked_and_expired_device_sessions_cannot_confirm(
  challenge_database,
  monkeypatch,
):
  data = _preflight_data()

  async def preflight(_request, **_kwargs):
    return data

  enqueue = AsyncMock()
  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  revoked_preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=_request(key="ios-revoked-1")
  )
  async with challenge_database() as db:
    session = await db.get(AuthDeviceSession, "device-session-1")
    session.revoked_at = utcnow()
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as revoked:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=revoked_preview.challenge_id,
      confirmation_token=revoked_preview.confirmation_token,
    )
  assert revoked.value.code == "UNAUTHENTICATED"

  async with challenge_database() as db:
    session = await db.get(AuthDeviceSession, "device-session-1")
    session.revoked_at = None
    session.expires_at = utcnow() + timedelta(hours=1)
    await db.commit()
  expired_preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=_request(key="ios-expired-session-1")
  )
  async with challenge_database() as db:
    session = await db.get(AuthDeviceSession, "device-session-1")
    session.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as expired:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=expired_preview.challenge_id,
      confirmation_token=expired_preview.confirmation_token,
    )
  assert expired.value.code == "UNAUTHENTICATED"
  enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_and_account_revocation_after_preview_fail_closed(
  challenge_database,
  monkeypatch,
):
  data = _preflight_data()

  async def preflight(_request, **_kwargs):
    return data

  enqueue = AsyncMock()
  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  permission_preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=_request(key="ios-permission-revoke-1")
  )
  async with challenge_database() as db:
    user = await db.get(AuthUser, "user-1")
    user.permissions = []
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as permission_revoked:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=permission_preview.challenge_id,
      confirmation_token=permission_preview.confirmation_token,
    )
  assert permission_revoked.value.code == "FORBIDDEN"

  async with challenge_database() as db:
    user = await db.get(AuthUser, "user-1")
    user.permissions = ["trade:manual"]
    await db.commit()
  account_preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=_request(key="ios-account-revoke-1")
  )
  async with challenge_database() as db:
    await db.execute(
      delete(AuthUserAccountAccess).where(
        AuthUserAccountAccess.user_id == "user-1",
        AuthUserAccountAccess.account_id == "ACCOUNT-1",
      )
    )
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as account_revoked:
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=account_preview.challenge_id,
      confirmation_token=account_preview.confirmation_token,
    )
  assert account_revoked.value.code == "FORBIDDEN"
  enqueue.assert_not_awaited()
  async with challenge_database() as db:
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0


@pytest.mark.asyncio
async def test_final_commit_failure_rolls_back_consumption_and_outbox_for_retry(
  challenge_database,
  monkeypatch,
):
  data = _preflight_data()

  async def preflight(_request, **_kwargs):
    return data

  async def enqueue(service, **kwargs):
    digest = manual_order.TradeCommandService.order_idempotency_digest(
      user_id=kwargs["user_id"],
      account_id=kwargs["account_id"],
      idempotency_key=kwargs["idempotency_key"],
    )
    service.db.add(
      TradeCommandOutbox(
        message_id="message-atomic-1",
        client_order_id="client-atomic-1",
        idempotency_key=digest,
        device_id="qmt-device-1",
        account_id="ACCOUNT-1",
        payload={"command_kind": "PLACE_ORDER"},
        delivery_status="QUEUED",
        expires_at=time_utils.now() + timedelta(minutes=2),
        attempts=0,
      )
    )
    await service.db.flush()
    return QueuedTradeCommand("client-atomic-1", "message-atomic-1", "QUEUED")

  monkeypatch.setattr(manual_order, "_preflight", preflight)
  monkeypatch.setattr(manual_order.TradeCommandService, "enqueue_order", enqueue)
  preview = await ManualOrderChallengeService.issue(
    principal=_principal(), request=_request(key="ios-atomic-1")
  )

  session_class = challenge_database.class_
  original_commit = session_class.commit
  fail_once = True

  async def commit_with_fault(session):
    nonlocal fail_once
    if fail_once:
      fail_once = False
      await session.flush()
      raise RuntimeError("injected final commit failure")
    await original_commit(session)

  monkeypatch.setattr(session_class, "commit", commit_with_fault)
  with pytest.raises(RuntimeError, match="injected final commit failure"):
    await ManualOrderChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )

  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert stored.consumed_at is None
    assert stored.result_reference is None
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0

  retried = await ManualOrderChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert retried.client_order_id == "client-atomic-1"
  async with challenge_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert stored.consumed_at is not None
    assert stored.result_reference["message_id"] == "message-atomic-1"
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 1


@pytest.mark.parametrize(
  ("overrides", "code"),
  [
    ({"side": "HOLD"}, "INVALID_SIDE"),
    ({"price_type": "MARKET"}, "INVALID_PRICE_TYPE"),
    ({"price_type": "BEST", "limit_price": 10.0}, "BEST_PRICE_MUST_BE_EMPTY"),
    (
      {"instrument_code": "430047.BJ", "price_type": "BEST", "limit_price": None},
      "BEST_NOT_SUPPORTED_FOR_MARKET",
    ),
    ({"volume": 0}, "INVALID_VOLUME"),
    ({"idempotency_key": ""}, "INVALID_IDEMPOTENCY_KEY"),
  ],
)
def test_manual_order_input_is_strongly_and_fail_closed_validated(overrides, code):
  values = {
    "account_id": "ACCOUNT-1",
    "instrument_code": "600000.SH",
    "side": "BUY",
    "price_type": "LIMIT",
    "volume": 100,
    "limit_price": 10.5,
    "idempotency_key": "ios-order-1",
    **overrides,
  }
  with pytest.raises(TradeApprovalChallengeError) as rejected:
    normalize_manual_order_request(**values)
  assert rejected.value.code == code


@pytest.mark.asyncio
async def test_preflight_uses_fresh_quote_account_instrument_and_order_sizer(
  monkeypatch,
):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    Account.__table__,
    Position.__table__,
    Instrument.__table__,
    AccountTradingRollout.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  now = time_utils.now()
  async with session_factory() as db:
    db.add_all(
      [
        Account(
          id="account-row-1",
          account_id="ACCOUNT-1",
          account_type=AccountType.STOCK,
          total_asset=200000,
          cash=100000,
          market_value=100000,
          frozen_cash=0,
          created_at=now,
          updated_at=now,
        ),
        Instrument(
          id="600000.SH",
          instrument_id="600000",
          market="SH",
          name="浦发银行",
          is_trading=True,
          price_tick=0.01,
          up_stop_price=11.0,
          down_stop_price=9.0,
          min_limit_order_volume=100,
          max_limit_order_volume=1000000,
          created_at=now,
          updated_at=now,
        ),
        AccountTradingRollout(
          account_id="ACCOUNT-1",
          stage="CANARY",
          enabled=True,
          kill_switch=False,
          reconcile_status="READY",
          policy_version=1,
          acknowledged_policy_version=1,
          last_snapshot_id="snapshot-1",
          last_snapshot_hash="snapshot-hash-1",
          last_snapshot_at=utcnow(),
          controlled_window_active=True,
          controlled_window_snapshot_id="snapshot-1",
          controlled_window_snapshot_hash="snapshot-hash-1",
          created_at=now,
          updated_at=now,
        ),
      ]
    )
    await db.commit()

  tick = Tick(
    stock_code="600000.SH",
    period="tick",
    time=now,
    last_price=10.0,
    stock_status=0,
    ask_price=[10.01],
    bid_price=[9.99],
    ask_vol=[1000],
    bid_vol=[1000],
  )

  class QuoteCache:
    async def get_ticks(self, _codes):
      return [tick]

  monkeypatch.setattr(manual_order, "AsyncSessionLocal", session_factory)
  monkeypatch.setattr(manual_order, "latest_market_quote_cache", QuoteCache())
  monkeypatch.setattr(
    manual_order,
    "_trading_time_service",
    SimpleNamespace(is_trading_hours=AsyncMock(return_value=True)),
  )
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["ACCOUNT-1"],
  )

  result = await manual_order._preflight(_request())
  assert result.reference_price == 10.5
  assert result.estimated_amount == 1050.0
  assert result.estimated_fees is None
  assert result.available_cash == 100000.0
  assert result.requested_volume == 100
  assert result.final_volume == 100
  assert result.risk_action == "ALLOW"
  assert any("手续费" in warning for warning in result.warnings)

  capped_request = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    side="BUY",
    price_type="LIMIT",
    volume=150,
    limit_price=10.5,
    idempotency_key="ios-cap-real-preflight-1",
  )
  capped = await manual_order._preflight(
    capped_request,
    risk_decision_id="risk-cap-1",
  )
  assert capped.requested_volume == 150
  assert capped.final_volume == 100
  assert capped.risk_action == "CAP"
  assert capped.risk_reason_code == "BUY_LOT_NORMALIZED"
  assert capped.risk_decision_id == "risk-cap-1"

  manual_order._trading_time_service.is_trading_hours.return_value = False
  with pytest.raises(TradeApprovalChallengeError) as outside_hours:
    await manual_order._preflight(_request(key="ios-hours-1"))
  assert outside_hours.value.code == "OUTSIDE_TRADING_HOURS"
  manual_order._trading_time_service.is_trading_hours.return_value = True

  tick.last_price = 11.0
  limit_up_request = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    side="BUY",
    price_type="LIMIT",
    volume=100,
    limit_price=11.0,
    idempotency_key="ios-limit-up-1",
  )
  with pytest.raises(TradeApprovalChallengeError) as limit_up:
    await manual_order._preflight(limit_up_request)
  assert limit_up.value.code == "LIMIT_UP_BLOCKED"
  tick.last_price = 10.0

  async with session_factory() as db:
    account = (
      await db.execute(
        select(Account).where(Account.account_id == "ACCOUNT-1")
      )
    ).scalar_one()
    account.cash = 1053
    account.updated_at = time_utils.now()
    await db.commit()
  with pytest.raises(TradeApprovalChallengeError) as fee_buffer:
    await manual_order._preflight(_request(key="ios-fee-buffer-1"))
  assert fee_buffer.value.code == "INSUFFICIENT_CASH"
  async with session_factory() as db:
    account = (
      await db.execute(
        select(Account).where(Account.account_id == "ACCOUNT-1")
      )
    ).scalar_one()
    account.cash = 100000
    account.updated_at = time_utils.now()
    await db.commit()

  stale = now - timedelta(minutes=5)
  async with session_factory() as db:
    db.add(
      Position(
        id="position-row-1",
        account_id="ACCOUNT-1",
        account_type=AccountType.STOCK,
        stock_code="600000.SH",
        volume=1000,
        can_use_volume=1000,
        frozen_volume=0,
        on_road_volume=0,
        yesterday_volume=1000,
        created_at=stale,
        updated_at=stale,
      )
    )
    await db.commit()

  stale_sell = normalize_manual_order_request(
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    side="SELL",
    price_type="LIMIT",
    volume=100,
    limit_price=10.5,
    idempotency_key="ios-stale-sell-1",
  )
  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await manual_order._preflight(stale_sell)
  assert rejected.value.code == "POSITION_SNAPSHOT_STALE"
  await engine.dispose()
