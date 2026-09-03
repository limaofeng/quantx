import asyncio
import hashlib
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from quantx_contracts import (
  CancelCommandPayload,
  ExecutionEnvironment,
  ExecutionOwnerRef,
)
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  OrderCorrelation,
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TABLES = [
  AuthUser.__table__,
  AgentDevice.__table__,
  PendingTradeOrder.__table__,
  OrderCorrelation.__table__,
  TradeCommandOutbox.__table__,
]

CANCEL_OWNER = ExecutionOwnerRef.manual_command("manual-cancel-retry")


def _set_expiry(row: TradeCommandOutbox, expires_at) -> None:
  row.expires_at = expires_at
  row.payload = {
    **dict(row.payload or {}),
    "expires_at": expires_at.replace(tzinfo=timezone.utc).isoformat(),
  }


async def _database(path: str = ":memory:"):
  engine = create_async_engine(
    f"sqlite+aiosqlite:///{path}",
    connect_args={"timeout": 5},
  )
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="test",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper"],
      )
    )
    await db.commit()
    db.add(
      PendingTradeOrder(
        client_order_id="place-client-1",
        user_id="user-1",
        account_id="account-1",
        owner_type=CANCEL_OWNER.owner_type.value,
        owner_id=CANCEL_OWNER.owner_id,
        environment=ExecutionEnvironment.PAPER.value,
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price="10",
        volume=100,
        status="SUBMITTED",
        broker_order_id="broker-order-1",
        bucket="manual",
        request_metadata={},
      )
    )
    db.add(
      OrderCorrelation(
        id="place-correlation-1",
        client_order_id="place-client-1",
        broker_order_id="broker-order-1",
        account_id="account-1",
        owner_type=CANCEL_OWNER.owner_type.value,
        owner_id=CANCEL_OWNER.owner_id,
        environment=ExecutionEnvironment.PAPER.value,
        bucket="manual",
        trace_id="place-trace-1",
        request_metadata={},
      )
    )
    await db.commit()
  return sessions, engine


@pytest.mark.asyncio
async def test_expired_never_delivered_cancel_reuses_same_attempt_safely() -> None:
  sessions, engine = await _database()
  try:
    async with sessions() as db:
      service = TradeCommandService(db)
      first = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_ref=CANCEL_OWNER,
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="cancel-business-1",
      )
      original = await db.get(TradeCommandOutbox, first.message_id)
      assert original is not None
      original.delivery_status = "EXPIRED"
      _set_expiry(original, utcnow() - timedelta(seconds=1))
      await db.commit()

      retried = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_ref=CANCEL_OWNER,
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="cancel-business-1",
      )

      revived = await db.get(TradeCommandOutbox, first.message_id)
      assert revived is not None
      assert retried == first
      assert revived.delivery_status == "QUEUED"
      assert revived.expires_at > utcnow()
      assert revived.idempotency_key == hashlib.sha256(
        b"cancel:user-1:account-1:PAPER:MANUAL_COMMAND:manual-cancel-retry:"
        b"cancel-business-1"
      ).hexdigest()
      assert CancelCommandPayload.model_validate(revived.payload)
      assert "cancel_attempt" not in revived.payload
      assert "cancel_business_identity" not in revived.payload
      assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 1
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_requires_unique_authoritative_target_chain() -> None:
  sessions, engine = await _database()
  try:
    async with sessions() as db:
      service = TradeCommandService(db)
      with pytest.raises(AgentUnavailableError, match="TARGET_UNPROVEN"):
        await service.enqueue_cancel(
          user_id="user-1",
          account_id="account-1",
          broker_order_id="unknown-broker-order",
          execution_ref=CANCEL_OWNER,
          environment=ExecutionEnvironment.PAPER,
          idempotency_key="cancel-unproven-target",
        )

      with pytest.raises(AgentUnavailableError, match="OWNER_CONFLICT"):
        await service.enqueue_cancel(
          user_id="user-1",
          account_id="account-1",
          broker_order_id="broker-order-1",
          execution_ref=ExecutionOwnerRef.manual_command("different-owner"),
          environment=ExecutionEnvironment.PAPER,
          idempotency_key="cancel-owner-mismatch",
        )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_retry_same_key_cannot_switch_broker_target() -> None:
  sessions, engine = await _database()
  try:
    async with sessions() as db:
      service = TradeCommandService(db)
      await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_ref=CANCEL_OWNER,
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="cancel-target-switch",
      )
      db.add(
        PendingTradeOrder(
          client_order_id="place-client-2",
          user_id="user-1",
          account_id="account-1",
          owner_type=CANCEL_OWNER.owner_type.value,
          owner_id=CANCEL_OWNER.owner_id,
          environment=ExecutionEnvironment.PAPER.value,
          instrument_code="600001.SH",
          side="BUY",
          order_type="FIX_PRICE",
          limit_price="11",
          volume=100,
          status="SUBMITTED",
          broker_order_id="broker-order-2",
          bucket="manual",
          request_metadata={},
        )
      )
      db.add(
        OrderCorrelation(
          id="place-correlation-2",
          client_order_id="place-client-2",
          broker_order_id="broker-order-2",
          account_id="account-1",
          owner_type=CANCEL_OWNER.owner_type.value,
          owner_id=CANCEL_OWNER.owner_id,
          environment=ExecutionEnvironment.PAPER.value,
          bucket="manual",
          trace_id="place-trace-2",
          request_metadata={},
        )
      )
      await db.commit()

      with pytest.raises(AgentUnavailableError, match="IDEMPOTENCY_KEY_CONFLICT"):
        await service.enqueue_cancel(
          user_id="user-1",
          account_id="account-1",
          broker_order_id="broker-order-2",
          execution_ref=CANCEL_OWNER,
          environment=ExecutionEnvironment.PAPER,
          idempotency_key="cancel-target-switch",
        )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_uncertain_cancel_attempt_creates_new_deterministic_identity() -> None:
  sessions, engine = await _database()
  try:
    async with sessions() as db:
      service = TradeCommandService(db)
      first = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_ref=CANCEL_OWNER,
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="cancel-business-2",
      )
      original = await db.get(TradeCommandOutbox, first.message_id)
      assert original is not None
      original.delivery_status = "DELIVERED"
      original.delivered_at = utcnow()
      _set_expiry(original, utcnow() - timedelta(seconds=1))
      await db.commit()

      retried = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_ref=CANCEL_OWNER,
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="cancel-business-2",
      )

      old_attempt = await db.get(TradeCommandOutbox, first.message_id)
      new_attempt = await db.get(TradeCommandOutbox, retried.message_id)
      assert old_attempt is not None and new_attempt is not None
      assert old_attempt.delivery_status == "DELIVERED"
      assert new_attempt.delivery_status == "QUEUED"
      assert new_attempt.client_order_id != old_attempt.client_order_id
      assert new_attempt.message_id != old_attempt.message_id
      assert new_attempt.idempotency_key == (
        f"{old_attempt.idempotency_key}:attempt:2"
      )
      assert CancelCommandPayload.model_validate(old_attempt.payload)
      assert CancelCommandPayload.model_validate(new_attempt.payload)
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery_status", ["DELIVERED", "ACKNOWLEDGED"])
async def test_nonexpired_dispatched_cancel_remains_the_only_active_attempt(
  delivery_status: str,
) -> None:
  sessions, engine = await _database()
  try:
    async with sessions() as db:
      service = TradeCommandService(db)
      first = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_ref=CANCEL_OWNER,
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="cancel-business-active",
      )
      original = await db.get(TradeCommandOutbox, first.message_id)
      assert original is not None
      original.delivery_status = delivery_status
      original.delivered_at = utcnow()
      if delivery_status == "ACKNOWLEDGED":
        original.acknowledged_at = utcnow()
      _set_expiry(original, utcnow() + timedelta(minutes=1))
      await db.commit()

      repeated = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_ref=CANCEL_OWNER,
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="cancel-business-active",
      )

      assert repeated.message_id == first.message_id
      assert repeated.status == delivery_status
      assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 1

      _set_expiry(original, utcnow() - timedelta(seconds=1))
      await db.commit()
      after_deadline = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_ref=CANCEL_OWNER,
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="cancel-business-active",
      )

      retry = await db.get(TradeCommandOutbox, after_deadline.message_id)
      assert retry is not None
      assert after_deadline.message_id != first.message_id
      assert retry.delivery_status == "QUEUED"
      assert retry.idempotency_key == f"{original.idempotency_key}:attempt:2"
      assert CancelCommandPayload.model_validate(retry.payload)
      assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 2
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_cancel_requests_leave_only_one_active_attempt(
  tmp_path,
) -> None:
  sessions, engine = await _database(str(tmp_path / "cancel-retry.sqlite3"))
  barrier = asyncio.Barrier(2)

  async def device_after_both_lookups(**_kwargs):
    await barrier.wait()
    return SimpleNamespace(id="device-1")

  try:
    async with sessions() as first_db, sessions() as second_db:
      first_service = TradeCommandService(first_db)
      second_service = TradeCommandService(second_db)
      first_service._device_for = device_after_both_lookups
      second_service._device_for = device_after_both_lookups

      first, second = await asyncio.gather(
        first_service.enqueue_cancel(
          user_id="user-1",
          account_id="account-1",
          broker_order_id="broker-order-1",
          execution_ref=CANCEL_OWNER,
          environment=ExecutionEnvironment.PAPER,
          idempotency_key="cancel-business-concurrent",
        ),
        second_service.enqueue_cancel(
          user_id="user-1",
          account_id="account-1",
          broker_order_id="broker-order-1",
          execution_ref=CANCEL_OWNER,
          environment=ExecutionEnvironment.PAPER,
          idempotency_key="cancel-business-concurrent",
        ),
      )
      assert first == second

    async with sessions() as db:
      active = list(
        (
          await db.execute(
            select(TradeCommandOutbox).where(
              TradeCommandOutbox.delivery_status == "QUEUED"
            )
          )
        )
        .scalars()
        .all()
      )
      assert len(active) == 1
      assert active[0].idempotency_key == hashlib.sha256(
        b"cancel:user-1:account-1:PAPER:MANUAL_COMMAND:manual-cancel-retry:"
        b"cancel-business-concurrent"
      ).hexdigest()
      assert CancelCommandPayload.model_validate(active[0].payload)
  finally:
    await engine.dispose()
