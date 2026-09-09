"""Independent historical session selection against local temporary PostgreSQL."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_contracts.history_session import HistoryHeartbeat
from quantx_infrastructure.services.market_data_history_session_store import (
  HistorySessionStore,
  HistorySessionUnavailable,
)

from tests.infrastructure.test_market_data_collection_permits import (  # noqa: F401
  durable_store,
  execute,
  permits,
  workers,
)


@pytest.fixture
async def history(permits, monkeypatch):  # noqa: F811
  first, second, grants, device, units = permits
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0072_market_data_history_session.py"
  )
  spec = importlib.util.spec_from_file_location("history_session_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)

  def upgrade(sync):
    operations = Operations(MigrationContext.configure(sync))
    migration.op = SimpleNamespace(
      create_table=lambda *args, **kwargs: operations.create_table(
        *args, prefixes=["TEMPORARY"], **kwargs
      )
    )
    migration.upgrade()

  async with first.engine.begin() as connection:
    await connection.run_sync(upgrade)
  await execute(
    first,
    "CREATE TEMP TABLE agent_devices (id varchar(36) PRIMARY KEY,user_id varchar(36),revoked_at timestamptz)",
  )
  await execute(
    first,
    "INSERT INTO agent_devices(id,user_id) VALUES (:id,'history-user')",
    {"id": device},
  )
  store = HistorySessionStore(first.engine)
  session = await store.register(
    device_id=device,
    user_id="history-user",
    capabilities=["market-data"],
    token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
  )
  monkeypatch.setattr(
    "quantx_infrastructure.services.market_data_collection_dispatch.history_window_open",
    AsyncMock(return_value=True),
  )
  return first, second, grants, store, device, units, session


async def test_authenticated_heartbeat_is_required_before_worker_issues(history):
  first, _, _, store, _, units, session = history
  assert await first.dispatch_history_collection() is None
  await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=True))
  grant = await first.dispatch_history_collection()
  assert grant.unit == units[0]
  messages = await store.work(session)
  assert [item.type for item in messages] == ["REQUEST", "GRANT"]
  assert messages[1].permit == grant
  assert messages[1].state == "ISSUED"
  assert (
    await execute(first, "SELECT state FROM market_data_collection_permit")
  ).scalar_one() == "ISSUED"
  # No business API heartbeat or control session exists in these tables.


async def test_session_conflict_replacement_and_late_close(history):
  first, _, _, store, device, _, session = history
  expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
  with pytest.raises(HistorySessionUnavailable):
    await store.register(
      device_id=device,
      user_id="history-user",
      capabilities=["market-data"],
      token_expires_at=expiry,
    )
  await execute(
    first,
    "UPDATE market_data_history_session SET expires_at=clock_timestamp()-INTERVAL '1 second'",
  )
  replacement = await store.register(
    device_id=device,
    user_id="history-user",
    capabilities=["market-data"],
    token_expires_at=expiry,
  )
  assert replacement != session
  with pytest.raises(HistorySessionUnavailable):
    await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=True))
  await store.close(session)
  await store.heartbeat(replacement, HistoryHeartbeat(xtdata_ready=True))
  assert await first.dispatch_history_collection() is not None


@pytest.mark.parametrize(
  "change", ["revoked", "reassigned", "expired", "qos", "xtdata", "development"]
)
async def test_invalid_identity_or_health_cannot_issue_native_permission(
  history, change
):
  first, _, _, store, _, _, session = history
  await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=True))
  if change == "revoked":
    await execute(first, "UPDATE agent_devices SET revoked_at=clock_timestamp()")
  elif change == "reassigned":
    await execute(first, "UPDATE agent_devices SET user_id='other'")
  elif change == "expired":
    await execute(
      first,
      "UPDATE market_data_history_session SET expires_at=clock_timestamp()-INTERVAL '1 second'",
    )
  elif change == "qos":
    await store.heartbeat(
      session, HistoryHeartbeat(xtdata_ready=True, qos_reason="TRADE_COMMAND_PENDING")
    )
  elif change == "xtdata":
    await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=False))
  else:
    first.demand_source_kind = "REMOTE"
  assert await first.dispatch_history_collection() is None
  assert (
    await execute(first, "SELECT count(*) FROM market_data_collection_permit")
  ).scalar_one() == 0


async def test_permit_transaction_rechecks_session_after_discovery(history):
  first, _, grants, store, device, units, session = history
  await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=True))
  await grants.register(str(units[0].request_id))
  await store.close(session)
  assert (
    await grants.issue_next(
      device_id=device,
      history_session_id=session,
      collection_allowed=True,
      development_allowed=True,
    )
    is None
  )
  assert (
    await execute(first, "SELECT count(*) FROM market_data_collection_permit")
  ).scalar_one() == 0


async def test_invalid_queued_plan_is_isolated_from_valid_work(history):
  first, _, _, store, device, units, session = history
  invalid = str(uuid4())
  await execute(
    first,
    """
    INSERT INTO market_data_request(request_id,device_id,status,request_payload,created_at)
    VALUES (:id,:device,'QUEUED',CAST(:payload AS json),'2000-01-01')
  """,
    {
      "id": invalid,
      "device": device,
      "payload": json.dumps(
        {"operation": "bars", "stock_list": ["000001.SZ"], "periods": ["unsupported"]}
      ),
    },
  )
  await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=True))
  grant = await first.dispatch_history_collection()
  assert grant.unit == units[0]
  result = (
    (
      await execute(
        first,
        "SELECT status,processing_error FROM market_data_request WHERE request_id=:id",
        {"id": invalid},
      )
    )
    .mappings()
    .one()
  )
  assert result == {"status": "FAILED", "processing_error": "COLLECTION_PLAN_INVALID"}


async def test_development_window_is_checked_before_fair_dispatch(history, monkeypatch):
  first, _, _, store, _, units, session = history
  await execute(
    first,
    "UPDATE market_data_request SET development_only=true WHERE request_id=:id",
    {"id": str(units[0].request_id)},
  )
  await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=True))
  monkeypatch.setattr(
    "quantx_infrastructure.services.market_data_collection_dispatch.history_window_open",
    AsyncMock(return_value=False),
  )
  assert await first.dispatch_history_collection() is None


async def test_heartbeat_never_renews_past_token_expiry(history):
  first, _, _, store, _, _, session = history
  await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=True))
  await execute(
    first,
    "UPDATE market_data_history_session SET token_expires_at=clock_timestamp()-INTERVAL '1 second',expires_at=clock_timestamp()-INTERVAL '1 second'",
  )
  with pytest.raises(HistorySessionUnavailable):
    await store.heartbeat(session, HistoryHeartbeat(xtdata_ready=True))
  with pytest.raises(HistorySessionUnavailable):
    await store.work(session)
