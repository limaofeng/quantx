"""Version ingestion through durable recovery, actual references and published reads."""
# ruff: noqa: F811

import pytest
from quantx_application.market_data.ingestion import IngestionEvidenceConflict
from quantx_contracts.market_data_service import HistoryRead
from quantx_infrastructure.services.development_ingestion_progress import (
  DevelopmentIngestionStore,
)
from quantx_infrastructure.services.development_version_ingestion import (
  ingest_development_storage_version,
)
from quantx_infrastructure.services.local_history_reader import (
  HistoryReadInvalid,
  LocalHistoryReader,
)
from quantx_infrastructure.services.market_data_content_verification import (
  MarketDataPersistenceQueryError,
)
from sqlalchemy import event, text

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_immutable_bar_storage import VersionStorage
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


def query(case):
  return HistoryRead(
    instrument=case.request.instrument,
    period=case.request.period,
    trading_date=case.request.trading_date,
    page_size=10,
  )


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_complete_version_is_read_through_published_history_path(prepared):
  case, connection = prepared, VersionStorage()
  reader = LocalHistoryReader(connection)
  with pytest.raises(HistoryReadInvalid, match="UNAVAILABLE"):
    await reader.read_published(query(case), session_factory=case.factory)
  assert not connection.queries
  receipt = await ingest_development_storage_version(
    case.request, case.manifest, case.progress, connection=connection
  )
  page = await reader.read_published(query(case), session_factory=case.factory)
  assert len(page.records) == receipt["local_verification"]["records_verified"] == 1
  assert (
    page.records[0]["storage_version"]
    == receipt["local_verification"]["immutable_storage"]["storage_version"]
  )
  assert case.progress.state["phase"] == "VERIFIED"
  assert (
    connection.queries[-1]["query_parameters"]["storage_version"]
    == page.records[0]["storage_version"]
  )


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_successor_resumes_readback_without_rewriting_version(prepared):
  case = prepared

  class InitiallyUnavailable(VersionStorage):
    unavailable = True

    def query(self, **kwargs):
      if self.unavailable:
        raise RuntimeError("query unavailable")
      return super().query(**kwargs)

  connection = InitiallyUnavailable()
  with pytest.raises(MarketDataPersistenceQueryError):
    await ingest_development_storage_version(
      case.request, case.manifest, case.progress, connection=connection
    )
  assert case.progress.state["phase"] == "READBACK"
  assert len(connection.lines) == 1
  await case.first.release()
  assert await case.second.acquire()
  progress = await DevelopmentIngestionStore(
    case.factory, "delivery", owner=case.second
  ).begin()
  assert progress.state["executions"] == 2
  connection.unavailable = False
  await ingest_development_storage_version(
    case.request, case.manifest, progress, connection=connection
  )
  assert len(connection.lines) == 1
  assert (
    len(
      (
        await LocalHistoryReader(connection).read_published(
          query(case), session_factory=case.factory
        )
      ).records
    )
    == 1
  )


async def test_unversioned_checkpoints_cannot_prove_new_table_writes(prepared):
  case, connection = prepared, VersionStorage()
  with pytest.raises(IngestionEvidenceConflict, match="MANIFEST_CHANGED"):
    await ingest_development_storage_version(
      case.request, case.manifest, case.progress, connection=connection
    )
  assert not connection.lines
  async with case.factory() as db:
    assert (
      await db.scalar(text("SELECT count(*) FROM development_data_bar_version")) == 0
    )


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_failed_receipt_never_publishes_partial_reference_or_version(prepared):
  case, connection = prepared, VersionStorage()

  def reject(_connection, _cursor, statement, *_args):
    if "SET state='LOCAL_VERIFIED'" in statement:
      raise RuntimeError("receipt failed")

  event.listen(case.first.engine.sync_engine, "before_cursor_execute", reject)
  try:
    with pytest.raises(RuntimeError, match="receipt failed"):
      await ingest_development_storage_version(
        case.request, case.manifest, case.progress, connection=connection
      )
  finally:
    event.remove(case.first.engine.sync_engine, "before_cursor_execute", reject)
  assert case.progress.state["phase"] == "READBACK"
  async with case.factory() as db:
    assert (
      await db.scalar(text("SELECT proof FROM development_data_bar_version")) is None
    )
    assert await db.scalar(text("SELECT count(*) FROM divid_factors")) == 1
  with pytest.raises(HistoryReadInvalid, match="UNAVAILABLE"):
    await LocalHistoryReader(connection).read_published(
      query(case), session_factory=case.factory
    )
