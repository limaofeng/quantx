from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.entry_plan_authorization import (
  EntryAutomationGate,
  EntryPlanAuthorizationConsumption,
  EntryPlanAuthorizationEvent,
  EntryPlanAuthorizationGrant,
)
from quantx_infrastructure.models.strategy_run import StrategyRun  # noqa: F401
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services.entry_plan_authorization_service import (
  MAX_UNBOUNDED_ENTRY_PLAN_VALID_UNTIL,
  EntryPlanAuthorizationError,
  EntryPlanAuthorizationScope,
  EntryPlanAuthorizationService,
  scope_from_managed_entry_config,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def authorization_database():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AuthUser.__table__,
          AuthDeviceSession.__table__,
          AuthUserAccountAccess.__table__,
          TradeConfirmationChallenge.__table__,
          EntryPlanAuthorizationGrant.__table__,
          EntryPlanAuthorizationConsumption.__table__,
          EntryPlanAuthorizationEvent.__table__,
          EntryAutomationGate.__table__,
        ],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    now = datetime(2026, 8, 20, 10, 0)
    db.add(
      AuthUser(
        id="user-1",
        username="entry-user",
        display_name="Entry User",
        password_hash="unused-test-hash",
        is_active=True,
        permissions=["strategy:control", "trade:approve"],
      )
    )
    db.add(
      AuthDeviceSession(
        id="session-1",
        user_id="user-1",
        refresh_token_hash="a" * 64,
        expires_at=now + timedelta(days=30),
        last_used_at=now,
        device_name="test-device",
        granted_permissions=["strategy:control", "trade:approve"],
      )
    )
    db.add(
      AuthUserAccountAccess(user_id="user-1", account_id="account-1", is_default=True)
    )
    await db.commit()
  yield sessions
  await engine.dispose()


def _scope(**overrides) -> EntryPlanAuthorizationScope:
  values = {
    "plan_id": "run-1",
    "run_id": "run-1",
    "config_version": 3,
    "plan_fingerprint": "1" * 64,
    "rule_fingerprint": "2" * 64,
    "instrument_code": "605499.SH",
    "bucket": "swing",
    "account_snapshot_version": "account-snapshot-v7",
    "max_total_amount_cny": Decimal("50000"),
    "max_single_amount_cny": Decimal("12000"),
    "max_daily_amount_cny": Decimal("20000"),
    "max_position_pct": Decimal("0.20"),
    "max_buy_price": Decimal("130.50"),
    "max_slippage_bps": 35,
    "max_price_deviation_bps": 50,
    "plan_valid_until": datetime(2026, 8, 28, 15, 0),
  }
  values.update(overrides)
  return EntryPlanAuthorizationScope(**values)


def _managed_config(*, expire_at_ms=None):
  return {
    "template_version": 1,
    "config_version": 3,
    "instrument_code": "605499.SH",
    "bucket": "core",
    "target_policy": {
      "mode": "INCREMENTAL_AMOUNT_CNY",
      "incremental_amount_cny": 30000,
      "max_total_amount_cny": 50000,
      "max_position_pct": 0.2,
      "baseline_snapshot": {
        "position_volume": 0,
        "market_value_cny": 0,
        "total_asset_cny": 200000,
        "reference_price": 120,
        "account_snapshot_version": "account-snapshot-v7",
      },
    },
    "trigger_rules": [
      {
        "rule_id": "manual",
        "rule_type": "MANUAL_TRIGGER",
        "priority": 100,
        "parameters": {},
      }
    ],
    "pacing_policy": {
      "tranche_count": 3,
      "max_single_intent_amount_cny": 12000,
      "max_daily_filled_amount_cny": 20000,
      "max_orders_per_day": 3,
      "max_open_orders": 1,
    },
    "execution_policy": {
      "environment": "LIVE",
      "authorization_mode": "AUTO",
      "max_slippage_bps": 35,
      "max_price_deviation_bps": 50,
    },
    "completion_policy": {
      "max_buy_price": 130.5,
      "expire_at_ms": expire_at_ms,
    },
    "exit_plan_template": {"rules": [{"rule_type": "HARD_STOP"}]},
  }


def test_scope_builder_is_canonical_and_has_one_unbounded_expiry_rule() -> None:
  first = scope_from_managed_entry_config(plan_id="run-1", config=_managed_config())
  second = scope_from_managed_entry_config(
    plan_id="run-1", config={"managed_entry_plan": _managed_config()}
  )
  assert first == second
  assert first.plan_valid_until == MAX_UNBOUNDED_ENTRY_PLAN_VALID_UNTIL
  assert first.account_snapshot_version == "account-snapshot-v7"
  assert first.max_price_deviation_bps == 50

  expires_ms = int(
    datetime.fromisoformat("2026-08-25T15:00:00+08:00").timestamp() * 1000
  )
  expiring = scope_from_managed_entry_config(
    plan_id="run-1", config=_managed_config(expire_at_ms=expires_ms)
  )
  assert expiring.plan_valid_until == datetime(2026, 8, 25, 15, 0)
  assert expiring.plan_fingerprint != first.plan_fingerprint


async def _grant(db, scope: EntryPlanAuthorizationScope | None = None):
  now = datetime(2026, 8, 20, 10, 1)
  service = EntryPlanAuthorizationService(db)
  selected = scope or _scope()
  preview = await service.preview(
    scope=selected,
    user_id="user-1",
    device_session_id="session-1",
    account_id="account-1",
    idempotency_key=f"grant-{uuid.uuid4()}",
    now=now,
  )
  grant = await service.confirm(
    scope=selected,
    user_id="user-1",
    device_session_id="session-1",
    account_id="account-1",
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
    now=now + timedelta(seconds=1),
  )
  return service, grant, now


@pytest.mark.asyncio
async def test_exact_grant_binds_subject_device_account_plan_rule_and_limits(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    service, grant, now = await _grant(db)

    assert grant.plan_id == grant.run_id == "run-1"
    assert grant.subject_user_id == "user-1"
    assert grant.device_session_id == "session-1"
    assert grant.account_fingerprint != "account-1"
    assert grant.account_snapshot_version == "account-snapshot-v7"
    assert grant.plan_fingerprint == "1" * 64
    assert grant.rule_fingerprint == "2" * 64
    assert grant.max_total_amount_cny == Decimal("50000.0000")

    validation = await service.validate_or_invalidate(
      plan_id="run-1",
      current_scope=_scope(),
      account_id="account-1",
      proposed_amount_cny=Decimal("10000"),
      proposed_buy_price=Decimal("129.80"),
      proposed_slippage_bps=20,
      proposed_price_deviation_bps=30,
      resulting_position_pct=Decimal("0.18"),
      now=now + timedelta(minutes=1),
    )
    assert validation.valid
    assert validation.balance is not None
    assert validation.balance.remaining_total_amount_cny == Decimal("50000")


@pytest.mark.asyncio
async def test_confirm_fails_closed_when_preview_scope_changes(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    service = EntryPlanAuthorizationService(db)
    now = datetime(2026, 8, 20, 10, 1)
    preview = await service.preview(
      scope=_scope(),
      user_id="user-1",
      device_session_id="session-1",
      account_id="account-1",
      idempotency_key="changed-preview",
      now=now,
    )
    with pytest.raises(EntryPlanAuthorizationError) as error:
      await service.confirm(
        scope=_scope(max_buy_price=Decimal("131")),
        user_id="user-1",
        device_session_id="session-1",
        account_id="account-1",
        challenge_id=preview.challenge_id,
        confirmation_token=preview.confirmation_token,
        now=now + timedelta(seconds=1),
      )
    assert error.value.code == "AUTHORIZATION_SCOPE_CHANGED"
    assert (
      await db.scalar(select(func.count(EntryPlanAuthorizationGrant.grant_id))) == 0
    )


@pytest.mark.asyncio
async def test_validate_invalidates_config_or_rule_change_and_revoked_session(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    service, grant, now = await _grant(db)
    result = await service.validate_or_invalidate(
      plan_id="run-1",
      current_scope=_scope(rule_fingerprint="3" * 64),
      account_id="account-1",
      now=now + timedelta(minutes=1),
    )
    assert not result.valid
    assert result.code == "ENTRY_AUTHORIZATION_SCOPE_CHANGED"
    await db.refresh(grant)
    assert grant.invalidated_at is not None

  async with authorization_database() as db:
    service, grant, now = await _grant(db)
    session = await db.get(AuthDeviceSession, "session-1")
    session.revoked_at = now + timedelta(seconds=2)
    await db.commit()
    result = await service.validate_or_invalidate(
      plan_id="run-1",
      current_scope=_scope(),
      account_id="account-1",
      now=now + timedelta(minutes=1),
    )
    assert not result.valid
    assert result.code == "ENTRY_AUTHORIZATION_SUBJECT_REVOKED"
    await db.refresh(grant)
    assert grant.invalidated_at is not None


@pytest.mark.asyncio
async def test_real_trade_consumption_is_idempotent_monotonic_and_bounded(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    service, grant, now = await _grant(db)
    first = await service.consume_real_fill(
      grant_id=grant.grant_id,
      trade_business_key="qmt-trade-1",
      filled_amount_cny=Decimal("10000"),
      filled_volume=100,
      fill_price=Decimal("100"),
      filled_at=now + timedelta(minutes=2),
    )
    replay = await service.consume_real_fill(
      grant_id=grant.grant_id,
      trade_business_key="qmt-trade-1",
      filled_amount_cny=Decimal("10000"),
      filled_volume=100,
      fill_price=Decimal("100"),
      filled_at=now + timedelta(minutes=2),
    )
    assert first.consumed_total_amount_cny == Decimal("10000")
    assert replay.consumed_total_amount_cny == Decimal("10000")
    assert (
      await db.scalar(
        select(func.count(EntryPlanAuthorizationConsumption.consumption_id))
      )
      == 1
    )
    with pytest.raises(EntryPlanAuthorizationError) as replay_error:
      await service.consume_real_fill(
        grant_id=grant.grant_id,
        trade_business_key="qmt-trade-1",
        filled_amount_cny=Decimal("10001"),
        filled_volume=100,
        fill_price=Decimal("100"),
        filled_at=now + timedelta(minutes=2),
      )
    assert replay_error.value.code == "REAL_FILL_REPLAY_MISMATCH"

    validation = await service.validate_or_invalidate(
      plan_id="run-1",
      current_scope=_scope(),
      account_id="account-1",
      proposed_amount_cny=Decimal("11000"),
      now=now + timedelta(minutes=3),
    )
    assert not validation.valid
    assert validation.code == "DAILY_AMOUNT_LIMIT"


@pytest.mark.asyncio
async def test_reauthorization_cannot_reset_plan_consumption(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    service, grant, now = await _grant(db)
    await service.consume_real_fill(
      grant_id=grant.grant_id,
      trade_business_key="qmt-trade-before-regrant",
      filled_amount_cny=Decimal("10000"),
      filled_volume=100,
      fill_price=Decimal("100"),
      filled_at=now + timedelta(minutes=2),
    )
    service, replacement, _ = await _grant(db)
    assert replacement.grant_id != grant.grant_id
    assert replacement.consumed_total_amount_cny == Decimal("10000")
    validation = await service.validate_or_invalidate(
      plan_id="run-1",
      current_scope=_scope(),
      account_id="account-1",
      proposed_amount_cny=Decimal("11000"),
      now=now + timedelta(minutes=3),
    )
    assert not validation.valid
    assert validation.code == "DAILY_AMOUNT_LIMIT"


@pytest.mark.asyncio
async def test_global_pause_gate_persists_and_invalidates_auto_entry_grant(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    service, grant, now = await _grant(db)
    state = await service.set_paused(
      account_id="account-1",
      paused=True,
      reason="operator kill switch",
      actor_user_id="user-1",
      now=now + timedelta(minutes=1),
    )
    assert state.paused
    assert (await service.get_gate("account-1")).reason == "operator kill switch"

    validation = await service.validate_or_invalidate(
      plan_id="run-1",
      current_scope=_scope(),
      account_id="account-1",
      now=now + timedelta(minutes=2),
    )
    assert not validation.valid
    assert validation.code == "ENTRY_AUTOMATION_PAUSED"
    await db.refresh(grant)
    assert grant.invalidated_at is None

    resumed = await service.set_paused(
      account_id="account-1",
      paused=False,
      reason="operator resumed",
      actor_user_id="user-1",
      now=now + timedelta(minutes=3),
    )
    assert not resumed.paused
    restored = await service.validate_or_invalidate(
      plan_id="run-1",
      current_scope=_scope(),
      account_id="account-1",
      now=now + timedelta(minutes=4),
    )
    assert restored.valid
    assert (
      await db.scalar(select(func.count(EntryAutomationGate.account_fingerprint))) == 1
    )


@pytest.mark.asyncio
async def test_preview_requires_one_exact_account_and_valid_permissions(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    db.add(
      AuthUserAccountAccess(user_id="user-1", account_id="account-2", is_default=False)
    )
    await db.commit()
    with pytest.raises(EntryPlanAuthorizationError) as error:
      await EntryPlanAuthorizationService(db).preview(
        scope=_scope(),
        user_id="user-1",
        device_session_id="session-1",
        account_id="account-1",
        idempotency_key="multiple-account-fails",
        now=datetime(2026, 8, 20, 10, 1),
      )
    assert error.value.code == "UNIQUE_PRIMARY_ACCOUNT_REQUIRED"
