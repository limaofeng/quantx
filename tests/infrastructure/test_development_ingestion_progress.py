"""Real PG delivery checkpoints and actual bar orchestration with isolated IO."""
# ruff: noqa: F811

import asyncio
import json

import pytest
from quantx_application.market_data.ingestion import IngestionEvidenceConflict
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion
from quantx_infrastructure.services.development_ingestion_progress import (
  DevelopmentIngestionStore,
)
from quantx_infrastructure.services.market_data_persistence_verification import (
  MarketDataPersistenceMismatchError,
)
from sqlalchemy import text

from tests.infrastructure.test_development_download_budget import download  # noqa: F401
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_transfer_ingestion import (
  ManifestStore,
  _payload,
  _summary,
  _tick_row,
  _write_chunk,
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


async def due(engine):
  async with engine.begin() as db:
    await db.execute(
      text("""
      UPDATE development_data_ingestion SET progress=jsonb_set(progress,'{next_retry_at}','null')
    """)
    )


async def test_readback_restart_skips_confirmed_write_and_preserves_retry_budget(
  download, tmp_path
):
  first, _, factory = download
  store = DevelopmentIngestionStore(factory, "delivery", owner=first)
  row = _tick_row()
  transfer = ManifestStore(
    payload=_payload(), manifest=[_write_chunk(tmp_path, [row, _summary([row])])]
  )
  writes = []

  async def save(**kwargs):
    writes.append(kwargs)
    return {"status": "success", "saved_count": 1}

  async def invisible(**kwargs):
    raise MarketDataPersistenceMismatchError("not visible")

  for attempt in range(4):
    # Simulate a fresh process adapter for each durable attempt.
    store = DevelopmentIngestionStore(factory, "delivery", owner=first)
    progress = await store.begin()
    with pytest.raises(MarketDataPersistenceMismatchError):
      await ingestion.ingest_uploaded_bar_request(
        transfer,
        "request-1",
        save_period=save,
        verify_persistence=invisible,
        progress=progress,
      )
    state = await progress.apply("defer", reason_code="PERSISTED_DATA_NOT_VISIBLE")
    assert state["phase"] == "READBACK"
    assert state["executions"] == attempt + 1
    assert len(writes) == 1
    if attempt < 3:
      assert not (await store.status())["due"]
      with pytest.raises(RuntimeError, match="not due"):
        await store.begin()
      await due(first.engine)
  assert state["blocked"]
  async with first.engine.connect() as db:
    assert (
      await db.scalar(
        text("SELECT state FROM development_data_export WHERE id='delivery'")
      )
      == "BLOCKED"
    )
  with pytest.raises(RuntimeError, match="explicit recovery"):
    await store.begin()


async def test_successor_keeps_checkpoints_and_rejects_old_owner(
  download,
):
  first, second, factory = download
  store = DevelopmentIngestionStore(factory, "delivery", owner=first)
  old = await store.begin()
  await old.apply("manifest", sha256="a" * 64)
  await old.apply("advance", phase="WRITE")
  await old.confirm(0, "b" * 64, 1)
  # The unfinished invocation must not refund the reserved execution.
  await first.release()
  assert await second.acquire()
  new = await DevelopmentIngestionStore(factory, "delivery", owner=second).begin()
  assert new.state["executions"] == 2
  assert await new.confirmed(0, "b" * 64, 1)
  with pytest.raises(RuntimeError, match="lease was lost"):
    await old.confirm(1, "c" * 64, 1)
  # Claim fencing remains effective even without a Worker owner guard.
  store.owner = None
  with pytest.raises(RuntimeError, match="claim was lost"):
    await old.confirm(1, "c" * 64, 1)
  with pytest.raises(IngestionEvidenceConflict, match="MANIFEST_CHANGED"):
    await new.apply("manifest", sha256="d" * 64)


async def test_cancellation_mid_write_resumes_only_unconfirmed_block(
  download, tmp_path, monkeypatch
):
  first, _, factory = download
  store = DevelopmentIngestionStore(factory, "delivery", owner=first)
  monkeypatch.setattr(ingestion, "MARKET_DATA_TICK_WRITE_BATCH_RECORDS", 1)
  first_row = _tick_row()
  rows = [first_row, _tick_row(time=first_row["time"] + 1)]
  transfer = ManifestStore(
    payload=_payload(), manifest=[_write_chunk(tmp_path, [*rows, _summary(rows)])]
  )
  writes = []

  async def save(**kwargs):
    writes.append(kwargs["market_data"]["600000.SH"]["time"].iloc[0])
    if len(writes) == 2:
      raise asyncio.CancelledError
    return {"status": "success", "saved_count": 1}

  async def readback(**kwargs):
    raise MarketDataPersistenceMismatchError("stop at readback")

  progress = await store.begin()
  with pytest.raises(asyncio.CancelledError):
    await ingestion.ingest_uploaded_bar_request(
      transfer,
      "request-1",
      save_period=save,
      verify_persistence=readback,
      progress=progress,
    )
  assert (await store.status())["progress"]["phase"] == "WRITE"
  resumed = await DevelopmentIngestionStore(factory, "delivery", owner=first).begin()
  assert resumed.state["executions"] == 2
  with pytest.raises(MarketDataPersistenceMismatchError):
    await ingestion.ingest_uploaded_bar_request(
      transfer,
      "request-1",
      save_period=save,
      verify_persistence=readback,
      progress=resumed,
    )
  assert writes == [rows[0]["time"], rows[1]["time"], rows[1]["time"]]
  assert len(resumed.state["checkpoints"]) == 2


async def test_receipt_rollback_keeps_readback_phase(download):
  first, _, factory = download
  store = DevelopmentIngestionStore(factory, "delivery", owner=first)
  progress = await store.begin()
  await progress.apply("manifest", sha256="a" * 64)
  await progress.apply("advance", phase="WRITE")
  await progress.apply("advance", phase="READBACK", write_result={"records_saved": 1})
  with pytest.raises(RuntimeError, match="receipt"):
    async with factory() as db:
      await store.mutate_in_transaction(
        db,
        "delivery",
        claim_token=progress.claim_token,
        action="advance",
        values={"phase": "VERIFIED"},
      )
      raise RuntimeError("receipt")
  state = (await store.status())["progress"]
  assert state["phase"] == "READBACK"
  assert state["write_result"] == {"records_saved": 1}
  assert "claim_token" not in json.dumps(state)


async def test_restart_after_stage_deadline_blocks_before_more_work(download):
  first, _, factory = download
  store = DevelopmentIngestionStore(factory, "delivery", owner=first)
  await store.begin()
  async with first.engine.begin() as db:
    await db.execute(
      text("""
      UPDATE development_data_ingestion SET progress=jsonb_set(progress,'{stage_started_at}',
        to_jsonb((clock_timestamp()-INTERVAL '6 minutes')::text))
    """)
    )
  recovered = await DevelopmentIngestionStore(factory, "delivery", owner=first).begin()
  assert recovered.state["blocked"]
  assert recovered.state["reason_code"] == "INGESTION_RETRY_BUDGET_EXHAUSTED"
  assert recovered.state["executions"] == 1
  with pytest.raises(RuntimeError, match="terminal"):
    await recovered.apply("manifest", sha256="a" * 64)


async def test_migration_cannot_drop_persisted_delivery_checkpoints(download):
  import importlib.util
  from pathlib import Path

  from alembic.migration import MigrationContext
  from alembic.operations import Operations

  first, _, factory = download
  store = DevelopmentIngestionStore(factory, "delivery", owner=first)
  await store.begin()
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0080_development_ingestion_progress.py"
  )
  spec = importlib.util.spec_from_file_location("development_progress_migration", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)

  def downgrade(connection):
    module.op = Operations(MigrationContext.configure(connection))
    module.downgrade()

  async with first.engine.begin() as db:
    with pytest.raises(RuntimeError, match="cannot remove persisted"):
      await db.run_sync(downgrade)
  assert (await store.status())["progress"]["executions"] == 1
