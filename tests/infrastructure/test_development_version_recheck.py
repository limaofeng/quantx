"""Completed version recovery must re-read content without repairing storage."""
# ruff: noqa: F811

import copy

import pytest
from quantx_infrastructure.services.development_bar_publication import (
  resolve_published_bar_version,
)
from quantx_infrastructure.services.development_download_budget import (
  DevelopmentDownloadBudget,
)
from quantx_infrastructure.services.development_version_ingestion import (
  ingest_development_storage_version,
  recheck_development_storage_version,
)
from sqlalchemy import text

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_immutable_bar_storage import VersionStorage
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


@pytest.mark.parametrize("prepared", [False], indirect=True)
@pytest.mark.parametrize("change", [None, "bar", "reference", "receipt", "directory"])
async def test_recheck_rejects_changed_evidence_without_rewriting(prepared, change):
  case, connection = prepared, VersionStorage()
  receipt = await ingest_development_storage_version(
    case.request, case.manifest, case.progress, connection=connection
  )
  budget = DevelopmentDownloadBudget(case.factory, "delivery", owner=case.first)
  if change == "bar":
    next(iter(connection.points.values()))[1]["close"] += 1
  elif change == "reference":
    async with case.factory() as db:
      await db.execute(text("UPDATE instruments SET instrument_name='changed'"))
      await db.commit()
  elif change == "receipt":
    receipt = copy.deepcopy(receipt)
    del receipt["local_verification"]["immutable_storage"]
  elif change == "directory":
    async with case.factory() as db:
      await db.execute(
        text(
          "UPDATE development_data_bar_version SET proof=jsonb_set(proof,'{fields_verified}','0')"
        )
      )
      await db.commit()
  lines, queries = len(connection.lines), len(connection.queries)
  result = await recheck_development_storage_version(
    case.request, receipt, "delivery", budget, connection=connection
  )
  assert len(connection.lines) == lines
  async with case.factory() as db:
    selected = await resolve_published_bar_version(db, case.request)
    if change is None:
      assert result == receipt and selected is not None
    else:
      assert result == {
        "id": "delivery",
        "status": "BLOCKED",
        "reason": "LOCAL_DELIVERY_PROOF_INVALID",
      }
      assert selected is None
    if change == "reference":
      assert (
        await db.scalar(text("SELECT instrument_name FROM instruments")) == "changed"
      )
  if change in {"receipt", "directory"}:
    assert len(connection.queries) == queries
  else:
    assert len(connection.queries) == queries + 1


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_transient_recheck_persists_wait_and_recovers_original_version(prepared):
  case = prepared

  class Connection(VersionStorage):
    failing = False

    def query(self, **kwargs):
      if self.failing:
        raise RuntimeError("temporarily unavailable")
      return super().query(**kwargs)

  connection = Connection()
  receipt = await ingest_development_storage_version(
    case.request, case.manifest, case.progress, connection=connection
  )
  budget = DevelopmentDownloadBudget(case.factory, "delivery", owner=case.first)
  connection.failing = True
  pending = await recheck_development_storage_version(
    case.request, receipt, "delivery", budget, connection=connection
  )
  assert pending["status"] == "WAITING_LOCAL_PROOF"
  connection.failing = False
  calls = len(connection.queries)
  assert (
    await recheck_development_storage_version(
      case.request, receipt, "delivery", budget, connection=connection
    )
    == pending
  )
  assert len(connection.queries) == calls
  async with case.factory() as db:
    assert await resolve_published_bar_version(db, case.request) is None
    await db.execute(
      text(
        "UPDATE development_data_download_budget SET next_probe_at=clock_timestamp()"
      )
    )
    await db.commit()
  assert (
    await recheck_development_storage_version(
      case.request, receipt, "delivery", budget, connection=connection
    )
    == receipt
  )
  assert len(connection.lines) == 1
  async with case.factory() as db:
    assert (await resolve_published_bar_version(db, case.request))[
      "storage_version"
    ] == receipt["local_verification"]["immutable_storage"]["storage_version"]


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_old_owner_cannot_recheck_or_change_completed_receipt(prepared):
  case, connection = prepared, VersionStorage()
  receipt = await ingest_development_storage_version(
    case.request, case.manifest, case.progress, connection=connection
  )
  await case.first.release()
  assert await case.second.acquire()
  queries = len(connection.queries)
  with pytest.raises(RuntimeError, match="lease was lost"):
    await recheck_development_storage_version(
      case.request,
      receipt,
      "delivery",
      DevelopmentDownloadBudget(case.factory, "delivery", owner=case.first),
      connection=connection,
    )
  assert len(connection.queries) == queries
  async with case.factory() as db:
    assert (await resolve_published_bar_version(db, case.request)) is not None


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_permanent_capacity_reason_survives_version_recovery(
  prepared, monkeypatch
):
  from unittest.mock import AsyncMock

  from quantx_infrastructure.services import development_version_ingestion as workflow
  from quantx_infrastructure.services.market_data_persistence_verification import (
    MarketDataPersistenceBlockedError,
  )

  case, connection = prepared, VersionStorage()
  receipt = await ingest_development_storage_version(
    case.request, case.manifest, case.progress, connection=connection
  )
  monkeypatch.setattr(
    workflow,
    "verify_immutable_bar_version",
    AsyncMock(
      side_effect=MarketDataPersistenceBlockedError("DEPENDENCY_QUERY_CAPACITY_BLOCKED")
    ),
  )
  result = await recheck_development_storage_version(
    case.request,
    receipt,
    "delivery",
    DevelopmentDownloadBudget(case.factory, "delivery", owner=case.first),
    connection=connection,
  )
  assert result == {
    "id": "delivery",
    "status": "BLOCKED",
    "reason": "DEPENDENCY_QUERY_CAPACITY_BLOCKED",
  }
  assert len(connection.lines) == 1
  assert (
    await recheck_development_storage_version(
      case.request,
      receipt,
      "delivery",
      DevelopmentDownloadBudget(case.factory, "delivery", owner=case.first),
      connection=connection,
    )
    == result
  )
  assert workflow.verify_immutable_bar_version.await_count == 1


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_wait_state_and_backoff_are_atomic(prepared, monkeypatch):
  from unittest.mock import AsyncMock

  from quantx_infrastructure.services import development_version_ingestion as workflow
  from quantx_infrastructure.services.market_data_persistence_verification import (
    MarketDataPersistenceQueryError,
  )
  from sqlalchemy import event

  case, connection = prepared, VersionStorage()
  receipt = await ingest_development_storage_version(
    case.request, case.manifest, case.progress, connection=connection
  )
  budget = DevelopmentDownloadBudget(case.factory, "delivery", owner=case.first)
  monkeypatch.setattr(
    workflow,
    "verify_immutable_bar_version",
    AsyncMock(side_effect=MarketDataPersistenceQueryError("not available")),
  )

  def reject(_connection, _cursor, statement, *_args):
    if "transient_failures=LEAST" in statement:
      raise RuntimeError("schedule update failed")

  event.listen(case.first.engine.sync_engine, "before_cursor_execute", reject)
  try:
    with pytest.raises(RuntimeError, match="schedule update failed"):
      await recheck_development_storage_version(
        case.request, receipt, "delivery", budget, connection=connection
      )
  finally:
    event.remove(case.first.engine.sync_engine, "before_cursor_execute", reject)
  async with case.factory() as db:
    assert (
      await db.scalar(
        text("SELECT state FROM development_data_export WHERE id='delivery'")
      )
      == "LOCAL_VERIFIED"
    )
    assert (
      await db.scalar(
        text(
          "SELECT transient_failures FROM development_data_download_budget WHERE delivery_id='delivery'"
        )
      )
      == 0
    )
  result = await recheck_development_storage_version(
    case.request, receipt, "delivery", budget, connection=connection
  )
  assert result["status"] == "WAITING_LOCAL_PROOF"
  async with case.factory() as db:
    assert (
      await db.scalar(
        text(
          "SELECT transient_failures FROM development_data_download_budget WHERE delivery_id='delivery'"
        )
      )
      == 1
    )
