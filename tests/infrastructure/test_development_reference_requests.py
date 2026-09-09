"""Default reference API and Worker with real temporary PG writes and recovery."""
# ruff: noqa: F811

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from quantx_contracts.development_reference import (
  CalendarRequest,
  FactorReferenceRequest,
)
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.services import development_history_import as importer
from quantx_infrastructure.services import local_market_data_client as clients
from quantx_infrastructure.services.development_reference_requests import (
  DevelopmentReferenceStore,
  advance_reference_request,
)
from quantx_infrastructure.services.holiday_service import HolidayService
from quantx_market_data.api import create_app
from sqlalchemy import event, text

from tests.infrastructure.test_development_reference_transaction import (  # noqa: F401
  reference,
  references,
)
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


@pytest.fixture
async def ref_case(references, workers, monkeypatch):
  first, second = workers[0]
  first.demand_source_kind = second.demand_source_kind = "REMOTE"
  monkeypatch.setattr(settings, "environment", "development")
  monkeypatch.setenv("QUANTX_MARKET_DATA_URL", "http://source")
  monkeypatch.setenv("QUANTX_MARKET_DATA_TOKEN", "source-token")
  state = SimpleNamespace(
    calls=[],
    code=200,
    calendar={
      "market": "SH",
      "year": 2026,
      "holidays": [{"date": "2026-01-01", "description": "元旦"}],
    },
    source=reference(),
    first=first,
    second=second,
    store=DevelopmentReferenceStore(first.engine, first),
  )
  client_class = httpx.AsyncClient

  def remote(request):
    state.calls.append(request.url.path)
    return httpx.Response(
      state.code,
      json=state.calendar if "/calendar/" in request.url.path else state.source,
    )

  def client(**kwargs):
    if kwargs.get("base_url") == "http://source":
      kwargs["transport"] = httpx.MockTransport(remote)
    return client_class(**kwargs)

  monkeypatch.setattr(httpx, "AsyncClient", client)
  return state


async def due(case):
  async with case.first.engine.begin() as db:
    await db.execute(
      text("UPDATE development_reference_request SET next_probe_at=clock_timestamp()")
    )


def factor_request():
  return FactorReferenceRequest(
    instrument="600000.SH", start_date="2026-01-01", end_date="2026-09-07"
  )


async def test_empty_database_calendar_bootstrap_is_worker_owned(ref_case, monkeypatch):
  case = ref_case
  app = create_app(store=case.first, token="internal")
  client_class = clients.LocalMarketDataClient
  monkeypatch.setattr(
    clients,
    "LocalMarketDataClient",
    lambda: client_class(transport=httpx.ASGITransport(app), token="internal"),
  )
  monkeypatch.setattr(
    HolidayService,
    "get_holidays",
    AsyncMock(side_effect=AssertionError("caller queried calendar database")),
  )
  monkeypatch.setattr(
    HolidayService,
    "bulk_save_holidays",
    AsyncMock(side_effect=AssertionError("caller imported calendar")),
  )
  payload = {
    "operation": "bars",
    "stock_list": ["600000.SH"],
    "periods": ["1d"],
    "start_time": "20260907",
    "end_time": "20260907",
  }
  async with app.router.lifespan_context(app):
    pending = await importer.request_remote_history(payload, timeout_seconds=0)
    assert pending["reason"] == "DEVELOPMENT_REFERENCE_PENDING"
    identity = pending["reference_requests"][0]["request_id"]
    assert pending["reference_requests"][0]["attempts"] == 0
    assert case.calls == []
    assert await advance_reference_request(case.first)
    status = await case.store.status(identity)
    assert status.state == "VERIFIED" and len(status.result.holidays) == 1
    result = await importer.request_remote_history(payload, timeout_seconds=0)
    assert result["reason"] == "DEVELOPMENT_HISTORY_PENDING"
    assert result["expected_partitions"] == 1
    assert case.calls == ["/market-data/v1/calendar/2026"]
    assert await case.store.submit(CalendarRequest(year=2026)) == identity
    assert not await advance_reference_request(case.first)
    async with case.first.engine.connect() as db:
      assert await db.scalar(text("SELECT count(*) FROM holidays")) == 1
      assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 1


async def test_factor_caller_only_submits_then_reads_worker_result(
  ref_case, monkeypatch
):
  case = ref_case
  app = create_app(store=case.first, token="internal")
  client_class = clients.LocalMarketDataClient
  monkeypatch.setattr(
    clients,
    "LocalMarketDataClient",
    lambda: client_class(transport=httpx.ASGITransport(app), token="internal"),
  )
  payload = {
    "operation": "divid_factors",
    "stock_list": ["600000.SH"],
    "start_time": "20260101",
    "end_time": "20260907",
  }
  async with app.router.lifespan_context(app):
    first = await importer.request_remote_history(payload, timeout_seconds=0)
    assert first["status"] == "timeout" and not case.calls
    identity = first["reference_requests"][0]["request_id"]
    assert await advance_reference_request(case.first)
    result = await importer.request_remote_history(payload, timeout_seconds=0)
    assert result["status"] == "success"
    assert (
      result["records_received"]
      == result["records_saved"]
      == result["records_verified"]
      == 0
    )
    assert result["reference_requests"][0]["request_id"] == identity
    assert len(case.calls) == 1
  async with case.first.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM divid_factors")) == 0
    assert await db.scalar(text("SELECT count(*) FROM instruments")) == 1


async def test_receipt_failure_rolls_back_reference_and_successor_reuses_source(
  ref_case,
):
  case = ref_case
  identity = await case.store.submit(factor_request())

  def reject(_connection, _cursor, statement, *_args):
    if "SET state='VERIFIED'" in statement:
      raise RuntimeError("receipt unavailable")

  event.listen(case.first.engine.sync_engine, "before_cursor_execute", reject)
  try:
    with pytest.raises(RuntimeError, match="receipt unavailable"):
      await advance_reference_request(case.first)
  finally:
    event.remove(case.first.engine.sync_engine, "before_cursor_execute", reject)
  async with case.first.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM divid_factors")) == 1
    assert await db.scalar(text("SELECT count(*) FROM instruments")) == 0
    assert (
      await db.scalar(text("SELECT source FROM development_reference_request"))
      == case.source
    )
  assert (await case.store.status(identity)).attempts == 1
  await case.first.release()
  assert await case.second.acquire()
  await due(case)
  case.code = 503  # Recovery must use the already pinned source, without HTTP.
  assert await advance_reference_request(case.second)
  status = await case.store.status(identity)
  assert status.state == "VERIFIED" and status.attempts == 2
  assert len(case.calls) == 1
  with pytest.raises(RuntimeError, match="lease was lost"):
    await case.store.claim()


async def test_transient_retry_budget_is_persistent_and_finite(ref_case):
  case = ref_case
  case.code = 503
  identity = await case.store.submit(CalendarRequest(year=2026))
  for count in range(1, 5):
    assert await advance_reference_request(case.first)
    status = await case.store.status(identity)
    assert status.attempts == count
    assert status.state == ("WAITING" if count < 4 else "BLOCKED")
    assert not await advance_reference_request(case.first)
    await due(case)
  assert await case.store.submit(CalendarRequest(year=2026)) == identity
  assert not await advance_reference_request(case.first)
  assert len(case.calls) == 4


@pytest.mark.parametrize("change", ["year", "duplicate", "empty", "oversize"])
async def test_invalid_calendar_cannot_be_published(ref_case, change):
  case = ref_case
  case.calendar = copy.deepcopy(case.calendar)
  if change == "year":
    case.calendar["year"] = 2025
  elif change == "duplicate":
    case.calendar["holidays"] *= 2
  elif change == "empty":
    case.calendar["holidays"] = []
  else:
    case.calendar["oversize"] = "a" * (2 * 1024 * 1024)
  identity = await case.store.submit(CalendarRequest(year=2026))
  assert await advance_reference_request(case.first)
  assert (await case.store.status(identity)).state == "BLOCKED"
  async with case.first.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM holidays")) == 0


async def test_reference_api_requires_auth_and_development_environment(
  ref_case, monkeypatch
):
  case = ref_case
  app = create_app(store=case.first, token="internal")
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app), base_url="http://local"
    ) as client:
      path = "/market-data/internal/v1/reference-requests"
      body = CalendarRequest(year=2026).model_dump(mode="json")
      assert (await client.post(path, json=body)).status_code == 401
      client.headers["Authorization"] = "Bearer internal"
      monkeypatch.setattr(settings, "environment", "production")
      assert (await client.post(path, json=body)).status_code == 403
      monkeypatch.setattr(settings, "environment", "development")
      assert (
        await client.post(path, json=body | {"account_id": "forbidden"})
      ).status_code == 422
      assert (await client.post(path, json=body)).status_code == 202


async def test_abandoned_attempts_cannot_reset_budget_or_drop_evidence(ref_case):
  import importlib.util
  from pathlib import Path

  from alembic.migration import MigrationContext
  from alembic.operations import Operations

  case = ref_case
  identity = await case.store.submit(CalendarRequest(year=2026))
  for count in range(1, 5):
    item = await case.store.claim()
    assert item["attempts"] == count
    await due(case)  # Simulate restart after each abandoned reserved invocation.
  assert await case.store.claim() is None
  assert (
    await case.store.status(identity)
  ).reason == "REFERENCE_RETRY_BUDGET_EXHAUSTED"
  assert case.calls == []
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0083_development_reference_requests.py"
  )
  spec = importlib.util.spec_from_file_location("reference_downgrade", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)

  def downgrade(connection):
    module.op = Operations(MigrationContext.configure(connection))
    module.downgrade()

  async with case.first.engine.begin() as db:
    with pytest.raises(RuntimeError, match="cannot remove persisted"):
      await db.run_sync(downgrade)
  assert (await case.store.status(identity)).attempts == 4


@pytest.mark.parametrize("change", ["as_of", "coverage", "instrument", "extra"])
async def test_invalid_factor_snapshot_never_replaces_local_rows(ref_case, change):
  case = ref_case
  case.source = copy.deepcopy(case.source)
  if change == "as_of":
    case.source["as_of"] = "2026-09-06"
  elif change == "coverage":
    case.source["factor_coverage"]["start_date"] = "20260701"
  elif change == "instrument":
    case.source["instrument"]["id"] = "600036.SH"
  else:
    case.source["unexpected_field"] = "rejected"
  identity = await case.store.submit(factor_request())
  assert await advance_reference_request(case.first)
  assert (await case.store.status(identity)).state == "BLOCKED"
  async with case.first.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM divid_factors")) == 1
    assert await db.scalar(text("SELECT count(*) FROM instruments")) == 0
    assert (
      await db.scalar(text("SELECT source FROM development_reference_request")) is None
    )


async def test_missing_source_configuration_does_not_consume_request_budget(
  ref_case, monkeypatch
):
  case = ref_case
  identity = await case.store.submit(CalendarRequest(year=2026))
  monkeypatch.delenv("QUANTX_MARKET_DATA_TOKEN")
  with pytest.raises(RuntimeError, match="REFERENCE_SOURCE_CONFIGURATION_UNAVAILABLE"):
    await advance_reference_request(case.first)
  status = await case.store.status(identity)
  assert status.state == "QUEUED" and status.attempts == 0
