import asyncio
import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import AgentDevice, TradeCommandOutbox
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TABLES = [
  AuthUser.__table__,
  AgentDevice.__table__,
  TradeCommandOutbox.__table__,
]


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
        idempotency_key="cancel-business-1",
      )
      original = await db.get(TradeCommandOutbox, first.message_id)
      assert original is not None
      original.delivery_status = "EXPIRED"
      original.expires_at = utcnow() - timedelta(seconds=1)
      await db.commit()

      retried = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        idempotency_key="cancel-business-1",
      )

      revived = await db.get(TradeCommandOutbox, first.message_id)
      assert revived is not None
      assert retried == first
      assert revived.delivery_status == "QUEUED"
      assert revived.expires_at > utcnow()
      assert revived.payload["cancel_attempt"] == 1
      assert revived.payload["cancel_business_identity"] == revived.idempotency_key
      assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 1
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
        idempotency_key="cancel-business-2",
      )
      original = await db.get(TradeCommandOutbox, first.message_id)
      assert original is not None
      original.delivery_status = "DELIVERED"
      original.delivered_at = utcnow()
      original.expires_at = utcnow() - timedelta(seconds=1)
      await db.commit()

      retried = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        idempotency_key="cancel-business-2",
      )

      old_attempt = await db.get(TradeCommandOutbox, first.message_id)
      new_attempt = await db.get(TradeCommandOutbox, retried.message_id)
      assert old_attempt is not None and new_attempt is not None
      assert old_attempt.delivery_status == "DELIVERED"
      assert new_attempt.delivery_status == "QUEUED"
      assert new_attempt.client_order_id != old_attempt.client_order_id
      assert new_attempt.message_id != old_attempt.message_id
      assert new_attempt.payload["cancel_business_identity"] == (
        old_attempt.payload["cancel_business_identity"]
      )
      assert new_attempt.payload["cancel_attempt"] == 2
      assert new_attempt.idempotency_key == hashlib.sha256(
        f"{old_attempt.idempotency_key}:attempt:2".encode("utf-8")
      ).hexdigest()
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
        idempotency_key="cancel-business-active",
      )
      original = await db.get(TradeCommandOutbox, first.message_id)
      assert original is not None
      original.delivery_status = delivery_status
      original.delivered_at = utcnow()
      if delivery_status == "ACKNOWLEDGED":
        original.acknowledged_at = utcnow()
      original.expires_at = utcnow() + timedelta(minutes=1)
      await db.commit()

      repeated = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        idempotency_key="cancel-business-active",
      )

      assert repeated.message_id == first.message_id
      assert repeated.status == delivery_status
      assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 1

      original.expires_at = utcnow() - timedelta(seconds=1)
      await db.commit()
      after_deadline = await service.enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        idempotency_key="cancel-business-active",
      )

      retry = await db.get(TradeCommandOutbox, after_deadline.message_id)
      assert retry is not None
      assert after_deadline.message_id != first.message_id
      assert retry.delivery_status == "QUEUED"
      assert retry.payload["cancel_attempt"] == 2
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
          idempotency_key="cancel-business-concurrent",
        ),
        second_service.enqueue_cancel(
          user_id="user-1",
          account_id="account-1",
          broker_order_id="broker-order-1",
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
      assert active[0].payload["cancel_business_identity"] == hashlib.sha256(
        b"cancel:user-1:account-1:cancel-business-concurrent"
      ).hexdigest()
  finally:
    await engine.dispose()
