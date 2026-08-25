from __future__ import annotations

from datetime import timedelta

import pytest
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControlEvent,
  EngineCommandOutbox,
  OperationalAlert,
  PendingTradeOrder,
  StrategyRuntimeEvent,
)
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.ios_notifications import (
  IosBusinessNotificationReceipt,
  IosNotificationEvent,
  IosNotificationOutbox,
  IosPushCategoryPreference,
  IosPushRegistration,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.ios_business_notification_projector import (
  IosBusinessNotificationProjector,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SIGNING_KEY = b"ios-projection-test-key-that-is-longer-than-thirty-two-bytes"
ACCOUNT_ID = "ACCOUNT-1"


@pytest.fixture
async def projection_database():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    AuthUserAccountAccess.__table__,
    AuthDeviceSession.__table__,
    AutoExitPlanRecord.__table__,
    AutoExitPlanEvent.__table__,
    TradeIntentRecord.__table__,
    PendingTradeOrder.__table__,
    StrategyRuntimeEvent.__table__,
    AccountExecutionControlEvent.__table__,
    EngineCommandOutbox.__table__,
    OperationalAlert.__table__,
    IosPushRegistration.__table__,
    IosPushCategoryPreference.__table__,
    IosBusinessNotificationReceipt.__table__,
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
    # The projector only reads these two StrategyRun columns. A small SQLite
    # stand-in avoids compiling PostgreSQL ARRAY columns unrelated to this test.
    await connection.exec_driver_sql(
      "CREATE TABLE strategy_runs (id VARCHAR(36) PRIMARY KEY, mode VARCHAR(16))"
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  yield sessions
  await engine.dispose()


async def _seed_registration(
  db,
  *,
  now,
  authorized: bool = True,
  enabled_categories: tuple[str, ...] = (
    "ACTION_REQUIRED",
    "ORDER_UPDATE",
    "RISK_SAFETY",
    "AUTOMATION_ERROR",
    "CONNECTION_DATA",
  ),
) -> None:
  db.add(
    AuthUser(
      id="user-1",
      username="owner",
      display_name="Owner",
      password_hash="hash",
      is_active=True,
      permissions=["notification:manage"],
      created_at=now,
      updated_at=now,
    )
  )
  if authorized:
    db.add(
      AuthUserAccountAccess(
        user_id="user-1",
        account_id=ACCOUNT_ID,
        is_default=True,
        created_at=now,
        updated_at=now,
      )
    )
  db.add(
    AuthDeviceSession(
      id="session-1",
      user_id="user-1",
      refresh_token_hash="1" * 64,
      expires_at=now + timedelta(hours=1),
      revoked_at=None,
      last_used_at=now,
      device_name="iPhone",
      granted_permissions=["notification:manage"],
      created_at=now,
      updated_at=now,
    )
  )
  db.add(
    IosPushRegistration(
      id="registration-1",
      user_id="user-1",
      device_session_id="session-1",
      account_id=ACCOUNT_ID,
      device_install_id="3bc6b00f-934d-4c76-a7b1-443deeb4a461",
      app_bundle_id="com.quantx.personal",
      app_version="1.0",
      apns_environment="SANDBOX",
      token_ciphertext="opaque-ciphertext",
      token_fingerprint="f" * 64,
      registered_at=now,
      last_seen_at=now,
      invalidated_at=None,
      created_at=now,
      updated_at=now,
    )
  )
  db.add_all(
    IosPushCategoryPreference(
      registration_id="registration-1",
      category=category,
      enabled=True,
      created_at=now,
      updated_at=now,
    )
    for category in enabled_categories
  )
  await db.flush()


def _exit_plan(now, *, execution_mode: str = "paper"):
  return AutoExitPlanRecord(
    plan_id="plan-1",
    account_id=ACCOUNT_ID,
    instrument_code="600000.SH",
    bucket="manual",
    source_type="MANUAL_POSITION",
    source_id="position-1",
    enabled=True,
    status="ACTIVE",
    execution_mode=execution_mode,
    auto_exit_authorized=False,
    config_version=1,
    protected_volume=100,
    exited_volume=0,
    remaining_volume=100,
    entry_avg_price=10.0,
    plan_state={},
    created_at=now,
    updated_at=now,
  )


def _intent(
  intent_id: str,
  now,
  *,
  owner_id: str = "plan-1",
  owner_type: str = "EXIT_PLAN",
  strategy_run_id: str | None = None,
):
  return TradeIntentRecord(
    id=intent_id,
    strategy_run_id=strategy_run_id,
    owner_type=owner_type,
    owner_id=owner_id,
    account_id=ACCOUNT_ID,
    strategy_id="exit-plan",
    instrument_code="600000.SH",
    direction="SELL",
    bucket="manual",
    reason="risk-trigger",
    priority="HIGH",
    confidence=1.0,
    status="AWAITING_APPROVAL",
    intent_metadata={"approval_ttl_ms": 900_000},
    created_at=now,
    updated_at=now,
  )


def _pending_order(now, *, execution_mode: str = "paper"):
  return PendingTradeOrder(
    client_order_id="client-order-1",
    user_id="user-1",
    account_id=ACCOUNT_ID,
    instrument_code="600000.SH",
    side="SELL",
    order_type="LIMIT",
    limit_price="10.00",
    volume=100,
    status="FILLED",
    broker_order_id="broker-order-sensitive",
    execution_mode=execution_mode,
    bucket="active",
    request_metadata={"amount": 1000},
    last_source_sequence=2,
    last_source_event_at=now,
    created_at=now,
    updated_at=now,
  )


def _runtime_event(now, *, status: str = "APPLIED"):
  return StrategyRuntimeEvent(
    event_id="runtime-event-1",
    business_key="trade:broker-order-sensitive:execution-sensitive",
    strategy_run_id="strategy-run-1",
    client_order_id="client-order-1",
    broker_order_id="broker-order-sensitive",
    event_type="TRADE",
    payload={"symbol": "600000.SH", "amount": 1000},
    application_status=status,
    application_attempts=1,
    created_at=now,
    applied_at=now if status == "APPLIED" else None,
  )


def _connection_alert(alert_id: str, now):
  return OperationalAlert(
    id=alert_id,
    fingerprint=(alert_id[-1] or "0") * 64,
    severity="SEV2",
    source="ENGINE",
    code="AGENT_REPORT_DEAD_LETTER",
    account_id=ACCOUNT_ID,
    business_id=f"report-{alert_id}",
    message="sensitive report failure",
    details={"error": "sensitive"},
    status="OPEN",
    occurrences=1,
    first_seen_at=now,
    last_seen_at=now,
  )


@pytest.mark.asyncio
async def test_projects_all_categories_from_durable_sources(
  projection_database,
) -> None:
  now = utcnow()
  async with projection_database() as db:
    await _seed_registration(db, now=now)
    db.add(_exit_plan(now))
    db.add(_intent("intent-sensitive-id", now))
    db.add(_pending_order(now))
    db.add(_runtime_event(now))
    db.add(
      AccountExecutionControlEvent(
        event_id="rollout-event-1",
        account_id=ACCOUNT_ID,
        event_type="HARD_KILL_ACTIVATED",
        details={"reason": "sensitive"},
        created_at=now,
      )
    )
    db.add(
      EngineCommandOutbox(
        message_id="command-failure-1",
        idempotency_key="command-failure-1",
        command_type="STRATEGY_START",
        aggregate_id="strategy-run-1",
        payload={"account_id": ACCOUNT_ID, "symbol": "600000.SH"},
        processing_status="FAILED",
        processing_attempts=1,
        processing_error="sensitive failure",
        available_at=now,
        processed_at=now,
        created_at=now,
        updated_at=now,
      )
    )
    db.add(_connection_alert("alert-1", now))
    await db.commit()

  async with projection_database() as db:
    summary = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=1))
    await db.commit()
    events = (await db.execute(select(IosNotificationEvent))).scalars().all()
    receipts = (
      await db.execute(select(IosBusinessNotificationReceipt))
    ).scalars().all()

    assert summary.discovered == summary.projected == summary.queued == 5
    assert {(item.category, item.route_type) for item in events} == {
      ("ACTION_REQUIRED", "today.action"),
      ("ORDER_UPDATE", "trading.orders"),
      ("RISK_SAFETY", "trading.safety"),
      ("AUTOMATION_ERROR", "quant.workspace"),
      ("CONNECTION_DATA", "system.status"),
    }
    assert len(receipts) == 5
    assert all(len(item.source_event_key_hash) == 64 for item in receipts)
    assert all("sensitive" not in item.source_event_key_hash for item in receipts)
    assert await db.scalar(select(func.count(IosNotificationOutbox.id))) == 5

  async with projection_database() as db:
    repeated = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=2))
    await db.commit()
    assert repeated.discovered == repeated.projected == repeated.queued == 0
    assert await db.scalar(select(func.count(IosNotificationOutbox.id))) == 5


@pytest.mark.asyncio
async def test_disabled_preference_never_catches_up_old_source(
  projection_database,
) -> None:
  now = utcnow()
  async with projection_database() as db:
    await _seed_registration(db, now=now, enabled_categories=())
    db.add(
      IosPushCategoryPreference(
        registration_id="registration-1",
        category="CONNECTION_DATA",
        enabled=False,
        created_at=now,
        updated_at=now,
      )
    )
    db.add(_connection_alert("alert-1", now))
    await db.commit()

  async with projection_database() as db:
    first = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=1))
    await db.commit()
    assert first.projected == 1
    assert first.queued == 0

  async with projection_database() as db:
    preference = await db.get(
      IosPushCategoryPreference,
      ("registration-1", "CONNECTION_DATA"),
    )
    preference.enabled = True
    await db.commit()
  async with projection_database() as db:
    repeated = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=2))
    await db.commit()
    assert repeated.projected == repeated.queued == 0
    assert await db.scalar(select(func.count(IosNotificationOutbox.id))) == 0

  async with projection_database() as db:
    db.add(_connection_alert("alert-2", now + timedelta(seconds=3)))
    await db.commit()
  async with projection_database() as db:
    fresh = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=4))
    await db.commit()
    assert fresh.projected == fresh.queued == 1


@pytest.mark.asyncio
async def test_projection_requires_current_account_authorization(
  projection_database,
) -> None:
  now = utcnow()
  async with projection_database() as db:
    await _seed_registration(db, now=now, authorized=False)
    db.add(_connection_alert("alert-1", now))
    await db.commit()
  async with projection_database() as db:
    result = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=1))
    await db.commit()
    assert result.projected == 1
    assert result.queued == 0
    assert await db.scalar(select(func.count(IosNotificationEvent.id))) == 0


@pytest.mark.asyncio
async def test_projection_requires_current_user_notification_permission(
  projection_database,
) -> None:
  now = utcnow()
  async with projection_database() as db:
    await _seed_registration(db, now=now)
    user = await db.get(AuthUser, "user-1")
    assert user is not None
    user.permissions = []
    db.add(_connection_alert("alert-1", now))
    await db.commit()

  async with projection_database() as db:
    result = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=1))
    await db.commit()
    assert result.projected == 1
    assert result.queued == 0
    assert await db.scalar(select(func.count(IosNotificationEvent.id))) == 0


@pytest.mark.asyncio
async def test_order_update_waits_for_applied_runtime_event(
  projection_database,
) -> None:
  now = utcnow()
  async with projection_database() as db:
    await _seed_registration(db, now=now)
    db.add(_pending_order(now))
    db.add(_runtime_event(now, status="PENDING"))
    await db.commit()
  async with projection_database() as db:
    pending = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=1))
    await db.commit()
    assert pending.discovered == pending.queued == 0

  async with projection_database() as db:
    event = await db.get(StrategyRuntimeEvent, "runtime-event-1")
    event.application_status = "APPLIED"
    event.applied_at = now + timedelta(seconds=2)
    await db.commit()
  async with projection_database() as db:
    applied = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=3))
    await db.commit()
    assert applied.discovered == applied.queued == 1
    assert (await db.scalar(select(IosNotificationEvent))).category == "ORDER_UPDATE"


@pytest.mark.asyncio
async def test_receipt_filter_prevents_batch_starvation(projection_database) -> None:
  now = utcnow()
  async with projection_database() as db:
    await _seed_registration(db, now=now)
    db.add_all(
      AccountExecutionControlEvent(
        event_id=f"rollout-{index:03d}",
        account_id=ACCOUNT_ID,
        event_type="RISK_INCREASE_PAUSED",
        details={},
        created_at=now + timedelta(milliseconds=index),
      )
      for index in range(125)
    )
    await db.commit()

  projected = 0
  for offset in range(3):
    async with projection_database() as db:
      result = await IosBusinessNotificationProjector(
        db,
        signing_key=SIGNING_KEY,
        source_batch_limit=50,
      ).project_once(now=now + timedelta(seconds=offset + 1))
      await db.commit()
      projected += result.projected
  assert projected == 125
  async with projection_database() as db:
    assert await db.scalar(
      select(func.count(IosBusinessNotificationReceipt.source_event_key_hash))
    ) == 125
    assert await db.scalar(select(func.count(IosNotificationOutbox.id))) == 125


@pytest.mark.asyncio
async def test_action_and_order_allow_paper_but_exclude_backtest(
  projection_database,
) -> None:
  now = utcnow()
  async with projection_database() as db:
    await _seed_registration(db, now=now)
    db.add(_exit_plan(now, execution_mode="paper"))
    db.add(_intent("paper-intent", now))
    db.add(_pending_order(now, execution_mode="paper"))
    db.add(_runtime_event(now))
    db.add(
      _intent(
        "backtest-intent",
        now,
        strategy_run_id="backtest-run",
        owner_type="STRATEGY_RUN",
        owner_id="backtest-run",
      )
    )
    await db.execute(
      text("INSERT INTO strategy_runs (id, mode) VALUES (:id, :mode)"),
      {"id": "backtest-run", "mode": "BACKTEST"},
    )
    await db.commit()

  async with projection_database() as db:
    result = await IosBusinessNotificationProjector(
      db,
      signing_key=SIGNING_KEY,
    ).project_once(now=now + timedelta(seconds=1))
    await db.commit()
    categories = set(
      (await db.execute(select(IosNotificationEvent.category))).scalars()
    )
    assert result.projected == result.queued == 2
    assert categories == {"ACTION_REQUIRED", "ORDER_UPDATE"}
