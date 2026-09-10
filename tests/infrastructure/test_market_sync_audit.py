import os
from contextlib import asynccontextmanager

import pytest
from quantx_infrastructure.services.market_data_sync_audit import MarketDataSyncAudit
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.integration
async def test_paged_audit_survives_flow_exit_and_joins_later_ingestion():
  url = make_url(os.environ["DATABASE_URL"])
  assert url.database.endswith("_test") or url.database.startswith("test_")
  engine = create_async_engine(url)
  try:
    async with engine.begin() as connection:
      # Session-local temporary tables cannot alter any existing request data.
      await connection.execute(
        text("""CREATE TEMP TABLE market_data_sync_partition (
        run_id VARCHAR(64), batch_index INTEGER, scope JSON, request_id VARCHAR(36),
        coverage_status VARCHAR(16), summary JSON, updated_at TIMESTAMP,
        PRIMARY KEY(run_id,batch_index)) ON COMMIT DROP""")
      )
      await connection.execute(
        text("""CREATE TEMP TABLE market_data_request (
        request_id VARCHAR(36), status VARCHAR(16), completed_at TIMESTAMP,
        ingestion_result JSON, ingestion_progress JSON) ON COMMIT DROP""")
      )

      class BoundEngine:
        @asynccontextmanager
        async def begin(self):
          yield connection

        connect = begin

      audit = MarketDataSyncAudit.__new__(MarketDataSyncAudit)
      audit.run_id = "run"
      audit.store = type("Store", (), {"engine": BoundEngine()})()
      await audit.record(1, {"periods": ["tick"]}, "request-1", "PENDING", {})
      await audit.record(
        2, {"periods": ["tick"]}, "request-2", "INCOMPLETE", {"reason": "empty"}
      )
      await connection.execute(
        text("""INSERT INTO market_data_request(request_id,status,completed_at,ingestion_result) VALUES
        ('request-1','COMPLETED',timezone('utc',now()),json_build_object('records_saved',5166)),
        ('request-2','COMPLETED',timezone('utc',now()),json_build_object('records_saved',0))""")
      )
      page = await audit.page(offset=0, limit=1)
      assert len(page) == 1
      assert page[0]["coverage_status"] == "PENDING"
      assert page[0]["request_status"] == "COMPLETED"
      assert page[0]["records_saved"] == "5166"
      assert page[0]["scope"] == {"periods": ["tick"]}
      assert (await audit.page(offset=1, limit=1))[0]["batch_index"] == 2
      await connection.execute(
        text("""UPDATE market_data_request SET status='BLOCKED',
        ingestion_progress='{"phase":"READBACK","reason_code":"READBACK_BUDGET_EXHAUSTED"}'
        WHERE request_id='request-2'""")
      )
      blocked = (await audit.page(offset=1, limit=1))[0]
      assert blocked["request_status"] == "BLOCKED"
      assert blocked["request_phase"] == "READBACK"
      assert blocked["request_reason"] == "READBACK_BUDGET_EXHAUSTED"
      assert blocked["summary"]["reason"] == "empty"
      await audit.record(
        1, {"periods": ["tick"]}, "request-1", "VERIFIED", {"records_saved": 5166}
      )
      assert sum(row["count"] for row in await audit.counts()) == 2
      assert (await audit.page(offset=0, limit=1))[0]["coverage_status"] == "VERIFIED"
  finally:
    await engine.dispose()
