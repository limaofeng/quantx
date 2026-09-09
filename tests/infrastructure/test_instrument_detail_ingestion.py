"""Real local PG snapshots, fenced checkpoints and authenticated bounded reads."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion
from quantx_infrastructure.services.market_data_ingestion_progress import (
  IngestionProgress,
)
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_transfer_ingestion import _write_chunk

ROWS = [
  {"code": "000001.SZ", "InstrumentName": "平安银行", "PriceTick": 0.01},
  {"code": "600000.SH", "InstrumentName": "浦发银行", "Nested": {"available": True}},
]


@pytest.fixture
async def snapshots(durable_store, tmp_path):  # noqa: F811
  store, clock = durable_store
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0077_instrument_detail_snapshot.py"
  )
  spec = importlib.util.spec_from_file_location("snapshot_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)

  def upgrade(connection):
    operations = Operations(MigrationContext.configure(connection))
    migration.op = SimpleNamespace(
      create_table=lambda *args, **kwargs: operations.create_table(
        *args, **kwargs, prefixes=["TEMPORARY"]
      )
    )
    migration.upgrade()

  async with store.engine.begin() as connection:
    await connection.run_sync(upgrade)
    await connection.execute(
      text("""
      UPDATE market_data_request SET request_payload=CAST(:payload AS json)
      WHERE request_id='request-1'
    """),
      {
        "payload": json.dumps(
          {
            "operation": "instrument_details",
            "stock_list": [row["code"] for row in ROWS],
          }
        )
      },
    )
  store.manifest = [_write_chunk(tmp_path, ROWS)]
  return store, clock, migration


async def start(store):
  token = await store.claim_market_data_request("request-1")
  progress = IngestionProgress(store, "request-1", token)
  await progress.apply("begin")
  return token, progress


async def test_snapshot_completion_recovery_and_authenticated_read(
  snapshots, monkeypatch
):
  store, _, _ = snapshots
  token, progress = await start(store)
  audit = await ingestion.ingest_uploaded_market_data_request(
    store, "request-1", progress=progress
  )
  assert audit["records_verified"] == audit["records_saved"] == 2
  assert progress.state["phase"] == "READBACK"
  app = create_app(store=store, token="test-token")
  async with app.router.lifespan_context(app):
    async with AsyncClient(
      transport=ASGITransport(app), base_url="http://test"
    ) as client:
      url = "/market-data/internal/v1/requests/request-1/instruments/000001.SZ"
      assert (await client.get(url)).status_code == 401
      headers = {"Authorization": "Bearer test-token"}
      assert (await client.get(url, headers=headers)).status_code == 404
      await store.release_market_data_request_claim(
        "request-1", claim_token=token, error="completion lost"
      )
      token, progress = await start(store)

      async def no_write(*args, **kwargs):
        pytest.fail("READBACK recovery must reuse committed snapshots")

      monkeypatch.setattr(store, "persist_market_data_reference", no_write)
      assert (
        await ingestion.ingest_uploaded_market_data_request(
          store, "request-1", progress=progress
        )
        == audit
      )
      await store.finish_market_data_request(
        "request-1", status="COMPLETED", ingestion_result=audit, claim_token=token
      )
      response = await client.get(url, headers=headers)
      assert response.status_code == 200
      assert response.json()["record"] == ROWS[0]
      assert response.json()["manifest_sha256"] == audit["manifest_sha256"]
      assert (
        await client.get(url.replace("000001.SZ", "bad"), headers=headers)
      ).status_code == 422
      async with store.engine.begin() as connection:
        await connection.execute(
          text(
            "UPDATE market_data_instrument_snapshot SET record_json='{}' WHERE code='000001.SZ'"
          )
        )
      assert (await client.get(url, headers=headers)).status_code == 503


@pytest.mark.parametrize(
  "rows",
  [
    ROWS[:1],
    [ROWS[0], ROWS[0]],
    [*ROWS, {"code": "600001.SH", "Name": "extra"}],
    [{"code": "000001.SZ"}, ROWS[1]],
  ],
)
async def test_invalid_scope_never_publishes_snapshot(snapshots, tmp_path, rows):
  store, _, _ = snapshots
  store.manifest = [_write_chunk(tmp_path, rows)]
  _, progress = await start(store)
  with pytest.raises(ingestion.MarketDataValidationError):
    await ingestion.ingest_uploaded_market_data_request(
      store, "request-1", progress=progress
    )
  async with store.engine.connect() as connection:
    assert (
      await connection.scalar(
        text("SELECT count(*) FROM market_data_instrument_snapshot")
      )
      == 0
    )


async def test_fence_failure_rolls_back_rows_and_checkpoint(snapshots, monkeypatch):
  store, _, _ = snapshots
  _, progress = await start(store)
  original = store.mutate_market_data_ingestion

  async def fail_final_fence(*args, **kwargs):
    if kwargs.get("values", {}).get("phase") == "READBACK":
      raise RuntimeError("owner expired")
    return await original(*args, **kwargs)

  monkeypatch.setattr(store, "mutate_market_data_ingestion", fail_final_fence)
  with pytest.raises(RuntimeError, match="owner expired"):
    await ingestion.ingest_uploaded_market_data_request(
      store, "request-1", progress=progress
    )
  async with store.engine.connect() as connection:
    assert (
      await connection.scalar(
        text("SELECT count(*) FROM market_data_instrument_snapshot")
      )
      == 0
    )
  assert (await store.market_data_request("request-1"))["ingestion_progress"][
    "phase"
  ] == "WRITE"


async def test_migration_refuses_removal_of_source_evidence(snapshots):
  store, _, migration = snapshots
  _, progress = await start(store)
  await ingestion.ingest_uploaded_market_data_request(
    store, "request-1", progress=progress
  )

  def downgrade(connection):
    migration.op = Operations(MigrationContext.configure(connection))
    migration.downgrade()

  async with store.engine.begin() as connection:
    with pytest.raises(RuntimeError, match="persisted instrument snapshot"):
      await connection.run_sync(downgrade)


async def test_recovery_rejects_changed_persisted_content(snapshots):
  store, _, _ = snapshots
  token, progress = await start(store)
  await ingestion.ingest_uploaded_market_data_request(
    store, "request-1", progress=progress
  )
  await store.release_market_data_request_claim(
    "request-1", claim_token=token, error="completion lost"
  )
  async with store.engine.begin() as connection:
    await connection.execute(
      text("DELETE FROM market_data_instrument_snapshot WHERE code='000001.SZ'")
    )
  _, progress = await start(store)
  with pytest.raises(ingestion.MarketDataValidationError, match="persistence mismatch"):
    await ingestion.ingest_uploaded_market_data_request(
      store, "request-1", progress=progress
    )
  assert (await store.market_data_request("request-1"))["status"] != "COMPLETED"


async def test_aggregate_byte_budget_prevents_publication(snapshots, monkeypatch):
  from quantx_infrastructure.services import instrument_detail_ingestion as details

  store, _, _ = snapshots
  monkeypatch.setattr(details, "MAX_INSTRUMENT_DETAIL_RESULT_BYTES", 100)
  _, progress = await start(store)
  with pytest.raises(
    ingestion.MarketDataValidationError, match="result exceeds byte limit"
  ):
    await ingestion.ingest_uploaded_market_data_request(
      store, "request-1", progress=progress
    )

  async with store.engine.connect() as connection:
    assert (
      await connection.scalar(
        text("SELECT count(*) FROM market_data_instrument_snapshot")
      )
      == 0
    )


async def test_worker_claim_to_verified_result(snapshots):
  store, _, _ = snapshots
  result = await ingestion.claim_ingest_and_finish_market_data_request(
    store, "request-1"
  )
  assert result["records_saved"] == result["records_verified"] == 2
  request = await store.market_data_request("request-1")
  assert request["status"] == "COMPLETED"
  assert request["ingestion_progress"]["phase"] == "VERIFIED"


async def test_conflicting_snapshot_preserved_and_other_insert_rolled_back(snapshots):
  store, _, _ = snapshots
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      INSERT INTO market_data_instrument_snapshot
      VALUES ('request-1','000001.SZ',:digest,:digest,'{}',1)
    """),
      {"digest": "0" * 64},
    )
  _, progress = await start(store)
  with pytest.raises(ingestion.MarketDataValidationError, match="persistence mismatch"):
    await ingestion.ingest_uploaded_market_data_request(
      store, "request-1", progress=progress
    )
  async with store.engine.connect() as connection:
    assert (
      await connection.execute(
        text("SELECT code,record_json FROM market_data_instrument_snapshot")
      )
    ).all() == [("000001.SZ", "{}")]


async def test_empty_snapshot_migration_can_be_removed(snapshots):
  store, _, migration = snapshots

  def downgrade(connection):
    migration.op = Operations(MigrationContext.configure(connection))
    migration.downgrade()

  async with store.engine.begin() as connection:
    await connection.run_sync(downgrade)
    assert (
      await connection.scalar(
        text("SELECT to_regclass('pg_temp.market_data_instrument_snapshot')")
      )
      is None
    )
