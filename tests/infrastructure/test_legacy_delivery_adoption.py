"""Legacy adoption preserves original delivery evidence in real isolated PG."""

# ruff: noqa: F811
import json

import pytest
from quantx_contracts.market_data_service import HistoryDemand
from quantx_infrastructure.services.legacy_delivery_adoption import (
  apply_adoption,
  plan_adoption,
)
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


async def seed(store):
  async with store.engine.begin() as db:
    await db.execute(
      text(
        "ALTER TABLE development_data_export ADD COLUMN manifest json, ADD COLUMN source_request_id text"
      )
    )
    for code, state, identity in (
      ("600000.SH", "QUEUED", "a" * 64),
      ("000001.SZ", "INCOMPLETE", "b" * 64),
    ):
      await db.execute(
        text("""
        INSERT INTO development_data_export(id,request,state,updated_at,error,manifest,source_request_id)
        VALUES (:id,CAST(:request AS json),:state,'2026-09-09',:error,CAST(:manifest AS json),'original-source')
      """),
        {
          "id": identity,
          "request": json.dumps(
            HistoryDemand(
              instrument=code, period="tick", trading_date="2026-09-08"
            ).model_dump(mode="json")
          ),
          "state": state,
          "error": "DATA_UNAVAILABLE" if state == "INCOMPLETE" else None,
          "manifest": json.dumps({"original_bytes_sha256": "c" * 64, "attempts": 4}),
        },
      )


async def exports(store):
  async with store.engine.connect() as db:
    return (
      (
        await db.execute(
          text("SELECT to_jsonb(e) FROM development_data_export e ORDER BY id")
        )
      )
      .scalars()
      .all()
    )


async def test_adoption_preserves_terminal_evidence_and_uses_existing_submission_identity(
  workers,
):
  (store, _), _ = workers
  await seed(store)
  before = await exports(store)
  plan = await plan_adoption(store.engine, ["a" * 64, "b" * 64])
  assert await exports(store) == before
  applied = await apply_adoption(store.engine, plan)
  assert [row["action"] for row in applied] == ["insert", "insert"]
  assert await exports(store) == before
  again = await apply_adoption(store.engine, plan)
  assert all(row["action"] == "already_adopted" for row in again)
  store.demand_source_kind = "REMOTE"
  for entry in plan.entries:
    assert await store.submit_history_demand(entry.partition) == entry.demand_id
    status = await store.history_demand(entry.demand_id)
    assert status["delivery_id"] == entry.delivery_id
    assert status["delivery_status"] == entry.state
  assert await exports(store) == before


@pytest.mark.parametrize("change", ["export", "demand", "identity", "active_worker"])
async def test_adoption_rejects_conflicts_without_partial_links(workers, change):
  (store, _), _ = workers
  await seed(store)
  plan = await plan_adoption(store.engine, ["a" * 64, "b" * 64])
  if change == "export":
    async with store.engine.begin() as db:
      await db.execute(
        text("UPDATE development_data_export SET manifest='{}' WHERE id=:id"),
        {"id": "b" * 64},
      )
  elif change == "demand":
    store.demand_source_kind = "REMOTE"
    await store.submit_history_demand(plan.entries[-1].partition)
  elif change == "identity":
    plan.entries[-1].demand_id = "f" * 64
  else:
    assert await store.acquire()
  before = await exports(store)
  with pytest.raises(ValueError, match="ADOPTION_"):
    await apply_adoption(store.engine, plan)
  assert await exports(store) == before
  async with store.engine.connect() as db:
    assert (
      await db.scalar(
        text("SELECT count(*) FROM market_data_demand WHERE delivery_id IS NOT NULL")
      )
      == 0
    )


async def test_adoption_links_preexisting_unbound_demand_without_reset(workers):
  (store, _), _ = workers
  await seed(store)
  store.demand_source_kind = "REMOTE"
  demand = HistoryDemand(
    instrument="600000.SH", period="tick", trading_date="2026-09-08"
  )
  identity = await store.submit_history_demand(demand)
  async with store.engine.begin() as db:
    await db.execute(
      text(
        "UPDATE market_data_demand SET reason_code='HISTORY_SOURCE_OFFLINE',next_probe_at='2099-01-01' WHERE demand_id=:id"
      ),
      {"id": identity},
    )
  plan = await plan_adoption(store.engine, ["a" * 64])
  result = await apply_adoption(store.engine, plan)
  assert result[0]["action"] == "link"
  async with store.engine.connect() as db:
    row = (
      (
        await db.execute(
          text("SELECT * FROM market_data_demand WHERE demand_id=:id"), {"id": identity}
        )
      )
      .mappings()
      .one()
    )
    assert row["reason_code"] is None
    assert row["next_probe_at"].year == 2099
    assert row["delivery_id"] == "a" * 64


async def test_adoption_rejects_two_deliveries_for_same_partition(workers):
  (store, _), _ = workers
  await seed(store)
  async with store.engine.begin() as db:
    await db.execute(
      text(
        "UPDATE development_data_export SET request=(SELECT request FROM development_data_export WHERE id=:first) WHERE id=:second"
      ),
      {"first": "a" * 64, "second": "b" * 64},
    )
  with pytest.raises(ValueError, match="ADOPTION_SCOPE_CONFLICT"):
    await plan_adoption(store.engine, ["a" * 64, "b" * 64])
