from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi import manual_order, trade_approval
from quantx_api.gqlapi.manual_order import (
  ManualOrderChallengeService,
  ManualOrderPreflightData,
  normalize_manual_order_request,
)
from quantx_api.gqlapi.trade_approval import TradeApprovalChallengeError
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models import (
  Account,
  AccountTradingRollout,
  Instrument,
  Position,
  TradeCommandOutbox,
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.trade_command_service import QueuedTradeCommand
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _principal(*, device_session_id: str = "device-session-1") -> Principal:
  return Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id=device_session_id,
    access_token_expires_at=time_utils.now() + timedelta(minutes=5),
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


def _preflight_data() -> ManualOrderPreflightData:
  return ManualOrderPreflightData(
    quote_timestamp=time_utils.now_aware(),
    reference_price=10.5,
    estimated_amount=1050.0,
    estimated_fees=None,
    available_cash=100000.0,
    available_volume=None,
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
          TradeConfirmationChallenge.__table__,
          TradeCommandOutbox.__table__,
        ],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
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

  async def preflight(_request):
    nonlocal preflight_calls
    preflight_calls += 1
    return _preflight_data()

  queued_calls = []

  async def enqueue(service, **kwargs):
    queued_calls.append(kwargs)
    # Match the production service: its successful transaction also persists
    # the challenge's consumed_at field in the same session.
    await service.db.commit()
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

  async def preflight(_request):
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
  async def preflight(_request):
    return _preflight_data()

  async def enqueue(service, **_kwargs):
    await service.db.commit()
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
  async def preflight(_request):
    return _preflight_data()

  queued_calls = []

  async def enqueue(service, **kwargs):
    queued_calls.append(kwargs)
    await service.db.commit()
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
  async def preflight(_request):
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

  result = await manual_order._preflight(_request())
  assert result.reference_price == 10.5
  assert result.estimated_amount == 1050.0
  assert result.estimated_fees is None
  assert result.available_cash == 100000.0
  assert any("手续费" in warning for warning in result.warnings)

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
