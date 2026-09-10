"""Actual PG source ordering for export reuse, independent of completion order."""
# ruff: noqa: F811

from datetime import timedelta

import pytest
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.services.development_history_export import (
  SourceReuseConflict,
  find_reusable_source_request,
)

from tests.infrastructure.test_engine_archive_generation import archive_db  # noqa: F401
from tests.infrastructure.test_realtime_archive_delivery import (
  archive_case,  # noqa: F401
)
from tests.infrastructure.test_realtime_archive_reader import (  # noqa: F401
  original,
  publish_native,
  reader_case,
)


@pytest.mark.parametrize(
  "kind", ["late_old", "conflict", "same_version", "third_conflict"]
)
async def test_reuse_orders_source_creation_and_rejects_ambiguous_versions(
  reader_case, kind
):
  case = reader_case
  created = case.request.minute.replace(tzinfo=None)
  await publish_native(
    case, [original(case, close=10.0)], created_at=created + timedelta(hours=1)
  )
  await publish_native(
    case,
    [original(case, close=10.0 if kind == "same_version" else 9.5)],
    created_at=created + timedelta(hours=0 if kind == "late_old" else 1),
  )
  if kind == "third_conflict":
    await publish_native(
      case, [original(case, close=9.5)], created_at=created + timedelta(hours=1)
    )
  request = HistoryPartitionRequest(
    instrument=case.request.instrument,
    period="1m",
    trading_date=case.scope.start_minute.date(),
  )
  async with case.engine.connect() as db:
    if kind in {"conflict", "third_conflict"}:
      with pytest.raises(SourceReuseConflict, match="SOURCE_VERSION_ORDER_AMBIGUOUS"):
        await find_reusable_source_request(db, request, request.agent_payload())
    else:
      selected = await find_reusable_source_request(
        db, request, request.agent_payload()
      )
      assert selected == ("native-0" if kind == "late_old" else "native-1")
