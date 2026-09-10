"""Fixed remote ingestion/readback and recovery closure in isolated PostgreSQL."""

# ruff: noqa: F811
import json

import pytest
from quantx_contracts.market_data_service import HistoryDemand
from quantx_infrastructure.services.archive_recovery import verify_archive_recovery
from quantx_infrastructure.services.development_source_proof import delivery_version
from quantx_infrastructure.services.development_version_ingestion import (
  ingest_development_storage_version,
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
@pytest.mark.parametrize(
  "kind", ["complete", "intraday", "cached", "wrong_delivery", "corrupt", "unpublished"]
)
async def test_fixed_remote_delivery_closes_only_with_full_session_content(
  prepared, kind
):
  case = prepared
  if kind in {"intraday", "cached"}:
    case.manifest["source_proof"]["source_created_at"] = "2026-09-07T06:00:00+00:00"
    if kind == "cached":
      from quantx_infrastructure.services.market_data_ingestion_progress import (
        evidence_hash,
      )

      source = case.manifest["source_proof"]
      source["source_created_at"] = "2026-09-07T08:00:00+00:00"
      source["source_payload"] = {**source["source_payload"], "download": False}
      source["native_storage_version"] = evidence_hash(
        {
          "storage_format": "native-bars-v1",
          "payload": source["source_payload"],
          "content_sha256": source["source_content_sha256"],
        }
      )
    case.manifest["data_version"] = delivery_version(case.manifest)
    async with case.first.engine.begin() as db:
      await db.execute(
        text(
          "UPDATE development_data_export SET manifest=CAST(:m AS JSON) WHERE id='delivery'"
        ),
        {"m": json.dumps(case.manifest)},
      )
  await ingest_development_storage_version(
    case.request, case.manifest, case.progress, connection=VersionStorage()
  )
  # The shared Worker fixture applies actual archive/recovery migrations.
  async with case.first.engine.begin() as db:
    await db.execute(
      text(
        "INSERT INTO engine_archive_generation(generation,registration_id,backend_pid,backend_start) VALUES (1,'source-generation',pg_backend_pid(),clock_timestamp())"
      )
    )
    await db.execute(
      text(
        "INSERT INTO engine_archive_scope(generation,instrument,start_minute,next_day) VALUES (1,:code,:start,:day)"
      ),
      {
        "code": case.request.instrument,
        "start": case.request.trading_date,
        "day": case.request.trading_date,
      },
    )
  demand = await case.first.submit_history_demand(
    HistoryDemand.model_validate(case.request.model_dump())
  )
  async with case.first.engine.begin() as db:
    if kind == "wrong_delivery":
      await db.execute(
        text(
          "INSERT INTO development_data_export(id,request,state,manifest,updated_at) SELECT 'other',request,state,manifest,updated_at FROM development_data_export WHERE id='delivery'"
        )
      )
    await db.execute(
      text(
        "UPDATE market_data_demand SET source_kind='REMOTE',delivery_id=:delivery WHERE demand_id=:id"
      ),
      {"delivery": "other" if kind == "wrong_delivery" else "delivery", "id": demand},
    )
    await db.execute(
      text(
        "INSERT INTO engine_archive_recovery(generation,instrument,trading_date,demand_id,state) VALUES (1,:code,:day,:id,'WAITING')"
      ),
      {"code": case.request.instrument, "day": case.request.trading_date, "id": demand},
    )
    if kind == "corrupt":
      await db.execute(
        text(
          "UPDATE development_data_export SET manifest=jsonb_set(manifest::jsonb,'{source_proof,source_created_at}', '\"2026-09-07T09:00:00+00:00\"'::jsonb)"
        )
      )
    if kind == "unpublished":
      await db.execute(
        text("UPDATE development_data_export SET state='WAITING_LOCAL_PROOF'")
      )
  assert await verify_archive_recovery(case.first) is (kind == "complete")
  async with case.first.engine.connect() as db:
    row = (
      (await db.execute(text("SELECT * FROM engine_archive_recovery"))).mappings().one()
    )
    assert row["state"] == ("VERIFIED" if kind == "complete" else "WAITING")
    assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 1
    if kind == "complete":
      assert row["evidence"]["delivery_id"] == "delivery"
      assert row["evidence"]["demand_id"] == demand
      assert row["evidence"]["source_version"] == case.manifest["data_version"]
      assert row["evidence"]["content_sha256"] == case.version.content_sha256
    else:
      assert row["evidence"] is None
  assert not await verify_archive_recovery(case.first)
