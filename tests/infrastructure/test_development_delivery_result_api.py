"""Published proofs travel through Data API; callers do not read source files."""
# ruff: noqa: F811

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from quantx_contracts.market_data_service import HistoryDemand
from quantx_infrastructure.services import development_history_import as importer
from quantx_infrastructure.services import local_market_data_client as clients
from quantx_infrastructure.services.holiday_service import HolidayService
from quantx_market_data.api import create_app
from quantx_market_data.worker import advance_development_delivery
from sqlalchemy import text

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_default_delivery import (
  delivery,  # noqa: F401
)
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401

pytestmark = pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)


async def test_demand_result_requires_actual_published_receipt(delivery):
  case = delivery
  case.first.demand_source_kind = "REMOTE"
  identity = await case.first.submit_history_demand(
    HistoryDemand.model_validate(case.request.model_dump())
  )
  assert await case.first.plan_history_demand()
  path = f"/market-data/internal/v1/demands/{identity}/result"
  app = create_app(store=case.first, token="internal")
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app), base_url="http://local"
    ) as client:
      assert (await client.get(path)).status_code == 401
      client.headers["Authorization"] = "Bearer internal"
      assert (await client.get(path)).status_code == 404
      assert await advance_development_delivery(case.first)
      response = await client.get(path)
      assert response.status_code == 200
      body = response.json()
      assert body["records_verified"] == 1
      assert body["partition"] == case.request.model_dump(mode="json")
      assert body["delivery_id"] == case.identity
      assert set(body) == {
        "demand_id",
        "partition",
        "delivery_id",
        "source_version",
        "storage_version",
        "content_sha256",
        "records_verified",
        "verified_at",
      }
      async with case.factory() as db:
        await db.execute(
          text(
            "UPDATE development_data_export SET state='WAITING_LOCAL_PROOF' WHERE id=:id"
          ),
          {"id": case.identity},
        )
        await db.commit()
      assert (await client.get(path)).status_code == 404
      async with case.factory() as db:
        await db.execute(
          text(
            "UPDATE development_data_export SET state='LOCAL_VERIFIED' WHERE id=:id"
          ),
          {"id": case.identity},
        )
        await db.execute(
          text("UPDATE development_data_bar_version SET records=records+1")
        )
        await db.commit()
      assert (await client.get(path)).status_code == 503


async def test_range_completion_uses_api_proofs_without_local_receipt_or_file_access(
  delivery, monkeypatch
):
  case = delivery
  case.first.demand_source_kind = "REMOTE"
  await case.first.submit_history_demand(
    HistoryDemand.model_validate(case.request.model_dump())
  )
  assert await case.first.plan_history_demand()
  assert await advance_development_delivery(case.first)
  calls, reads, writes = (
    len(case.calls),
    len(case.connection.queries),
    len(case.connection.lines),
  )
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
    AsyncMock(return_value=[SimpleNamespace(date=date(2026, 1, 1))]),
  )
  monkeypatch.setattr(
    importer,
    "get_export",
    AsyncMock(side_effect=AssertionError("caller read receipt directly")),
  )
  monkeypatch.setattr(
    importer.ImportedTransfer,
    "market_data_transfers",
    AsyncMock(side_effect=AssertionError("caller opened source files")),
  )
  monkeypatch.setattr(
    importer,
    "import_partition",
    AsyncMock(side_effect=AssertionError("caller executed import")),
  )
  async with app.router.lifespan_context(app):
    result = await importer.request_remote_history(
      case.request.agent_payload(), timeout_seconds=0
    )
  assert result["status"] == "success"
  assert (
    result["records_saved"]
    == result["records_received"]
    == result["records_verified"]
    == 1
  )
  assert result["verified_partitions"] == result["expected_partitions"] == 1
  assert result["code_summaries"] == [
    {"code": "600000.SH", "period": "1d", "row_count": 1}
  ]
  assert result["partition_proofs"][0]["delivery_id"] == case.identity
  assert result["data_versions"] == [case.manifest["data_version"]]
  assert (
    len(case.calls),
    len(case.connection.queries),
    len(case.connection.lines),
  ) == (calls, reads, writes)
