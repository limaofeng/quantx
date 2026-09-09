"""Persistent reservations and Worker fencing using temporary PostgreSQL tables."""
# ruff: noqa: F811

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_infrastructure.services import development_download_budget as budgets
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


async def install_budget_schema(engine):
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0078_development_download_budget.py"
  )
  spec = importlib.util.spec_from_file_location("download_budget_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)
  async with engine.begin() as connection:

    def upgrade(sync_connection):
      operations = Operations(MigrationContext.configure(sync_connection))
      migration.op = SimpleNamespace(
        create_table=lambda *args, **kwargs: operations.create_table(
          *args, prefixes=["TEMPORARY"], **kwargs
        ),
      )
      migration.upgrade()

    await connection.run_sync(upgrade)
    schedule_path = path.with_name("20260910_0079_development_delivery_schedule.py")
    schedule_spec = importlib.util.spec_from_file_location(
      "delivery_schedule_migration", schedule_path
    )
    schedule_migration = importlib.util.module_from_spec(schedule_spec)
    schedule_spec.loader.exec_module(schedule_migration)

    def upgrade_schedule(sync_connection):
      schedule_migration.op = Operations(MigrationContext.configure(sync_connection))
      schedule_migration.upgrade()

    await connection.run_sync(upgrade_schedule)
  return migration


@pytest.fixture
async def download(workers):
  (first, second), _ = workers
  first.budget_migration = await install_budget_schema(first.engine)
  async with first.engine.begin() as connection:
    await connection.execute(
      text("ALTER TABLE development_data_export ADD COLUMN error text")
    )
    await connection.execute(
      text("""
      INSERT INTO development_data_export(id,request,state,updated_at)
      VALUES ('delivery','{}','QUEUED',clock_timestamp())
    """)
    )
  assert await first.acquire()
  factory = async_sessionmaker(first.engine)
  return first, second, factory


async def snapshot(engine):
  async with engine.connect() as connection:
    return (
      (
        await connection.execute(
          text(
            "SELECT * FROM development_data_download_budget WHERE delivery_id='delivery'"
          )
        )
      )
      .mappings()
      .one()
    )


async def test_restart_and_cancellation_never_refund_reserved_work(download):
  first, _, factory = download
  initial = budgets.DevelopmentDownloadBudget(factory, "delivery", owner=first)
  with pytest.raises(asyncio.CancelledError):
    async with initial.attempt(100):
      assert (await snapshot(first.engine))["attempts"] == 1
      raise asyncio.CancelledError
  restarted = budgets.DevelopmentDownloadBudget(factory, "delivery", owner=first)
  async with restarted.attempt(200):
    pass
  row = await snapshot(first.engine)
  assert row["attempts"] == 2
  assert row["reserved_bytes"] == 300
  assert row["reserved_seconds"] == 2 * budgets.DOWNLOAD_ATTEMPT_SECONDS


@pytest.mark.parametrize(
  "field,maximum",
  [
    ("attempts", budgets.MAX_DOWNLOAD_ATTEMPTS),
    ("reserved_bytes", budgets.MAX_DOWNLOAD_RESERVED_BYTES),
    ("reserved_seconds", budgets.MAX_DOWNLOAD_RESERVED_SECONDS),
  ],
)
async def test_exhaustion_is_sticky_and_visible_without_starting_work(
  download, field, maximum
):
  first, _, factory = download
  budget = budgets.DevelopmentDownloadBudget(factory, "delivery", owner=first)
  await budget.reserve(1)
  async with first.engine.begin() as connection:
    await connection.execute(
      text(f"UPDATE development_data_download_budget SET {field}=:value"),
      {"value": maximum},
    )
  before = await snapshot(first.engine)
  for _ in range(2):
    with pytest.raises(budgets.DeliveryDownloadBudgetExhausted):
      async with budgets.DevelopmentDownloadBudget(
        factory, "delivery", owner=first
      ).attempt(1):
        pytest.fail("exhausted reservation entered network operation")
  after = await snapshot(first.engine)
  for key in ("attempts", "reserved_bytes", "reserved_seconds"):
    assert after[key] == before[key]
  assert after["reason_code"] == "DELIVERY_DOWNLOAD_BUDGET_EXHAUSTED"
  async with first.engine.connect() as connection:
    assert (
      await connection.scalar(text("SELECT error FROM development_data_export"))
      == after["reason_code"]
    )


async def test_successor_preserves_budget_and_old_owner_cannot_reserve(download):
  first, second, factory = download
  old = budgets.DevelopmentDownloadBudget(factory, "delivery", owner=first)
  await old.reserve(10)
  await first.release()
  assert await second.acquire()
  with pytest.raises(RuntimeError, match="lease was lost"):
    await old.reserve(20)
  await budgets.DevelopmentDownloadBudget(factory, "delivery", owner=second).reserve(30)
  row = await snapshot(first.engine)
  assert row["attempts"] == 2
  assert row["reserved_bytes"] == 40


async def test_total_attempt_deadline_covers_body_consumption(download, monkeypatch):
  first, _, factory = download
  monkeypatch.setattr(budgets, "DOWNLOAD_ATTEMPT_SECONDS", 1)
  with pytest.raises(budgets.DeliveryRemoteUnavailable) as error:
    async with budgets.DevelopmentDownloadBudget(
      factory, "delivery", owner=first
    ).attempt(100):
      await asyncio.sleep(2)
  assert isinstance(error.value.__cause__, TimeoutError)
  assert (await snapshot(first.engine))["reserved_seconds"] == 1


async def test_downgrade_refuses_to_erase_reserved_work(download):
  first, _, factory = download
  await budgets.DevelopmentDownloadBudget(factory, "delivery", owner=first).reserve(10)
  async with first.engine.begin() as connection:

    def downgrade(sync_connection):
      first.budget_migration.op = Operations(
        MigrationContext.configure(sync_connection)
      )
      first.budget_migration.downgrade()

    with pytest.raises(RuntimeError, match="cannot remove persisted download budgets"):
      await connection.run_sync(downgrade)
  assert (await snapshot(first.engine))["attempts"] == 1


async def test_submission_and_wait_survive_restart_without_budget_use(download):
  first, second, factory = download
  budget = budgets.DevelopmentDownloadBudget(factory, "delivery", owner=first)
  assert (await budget.schedule())["due"]
  await budget.schedule("submitted")
  pending = await budget.schedule("pending")
  assert not pending["due"]
  await first.release()
  assert await second.acquire()
  with pytest.raises(RuntimeError, match="lease was lost"):
    await budget.schedule("ready")
  state = await budgets.DevelopmentDownloadBudget(
    factory, "delivery", owner=second
  ).schedule()
  assert state["remote_submitted"]
  assert state["next_probe_at"] == pending["next_probe_at"]
  assert state["wait_reason"] == "DELIVERY_SOURCE_PENDING"
  assert (await snapshot(first.engine))["attempts"] == 0


async def test_network_failure_backoff_grows_and_caps_without_reset_on_metadata_ready(
  download,
):
  first, _, factory = download
  for expected in (30, 60, 120, 240, 480, 900, 900):
    budget = budgets.DevelopmentDownloadBudget(factory, "delivery", owner=first)
    await budget.schedule("failed")
    async with first.engine.connect() as connection:
      seconds = await connection.scalar(
        text("""
        SELECT extract(epoch FROM next_probe_at-clock_timestamp())
        FROM development_data_download_budget WHERE delivery_id='delivery'
      """)
      )
    assert expected - 2 <= seconds <= expected
    # A metadata response is not proof that the subsequent body download works.
    await budget.schedule("ready")
  assert (await snapshot(first.engine))["transient_failures"] == 6


@pytest.mark.parametrize("status", [429, 503, 401])
async def test_only_transient_http_statuses_schedule_retry(download, status):
  first, _, factory = download
  budget = budgets.DevelopmentDownloadBudget(factory, "delivery", owner=first)
  transport = httpx.MockTransport(lambda request: httpx.Response(status))
  expected = (
    httpx.HTTPStatusError if status == 401 else budgets.DeliveryRemoteUnavailable
  )
  async with httpx.AsyncClient(transport=transport) as client:
    with pytest.raises(expected):
      async with budget.attempt(100):
        response = await client.get("http://test/status")
        response.raise_for_status()
  state = await budget.schedule()
  assert state["due"] is (status == 401)
  assert state["wait_reason"] == (
    None if status == 401 else "DELIVERY_REMOTE_UNAVAILABLE"
  )
