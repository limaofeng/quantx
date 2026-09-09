"""Resume factor uploads against real, session-local PostgreSQL content."""

import json
import re
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.models.divid_factor import DividFactorTable
from quantx_infrastructure.repositories.divid_factor_repository import (
  DividFactorRepository,
)
from quantx_infrastructure.services.market_data_ingestion_progress import (
  IngestionProgress,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  ingest_uploaded_market_data_request,
)
from sqlalchemy import MetaData, event, text
from sqlalchemy.schema import CreateTable

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_transfer_ingestion import _write_chunk


@pytest.fixture
async def factors(durable_store, monkeypatch):  # noqa: F811
  store, _ = durable_store
  table = DividFactorTable.__table__.to_metadata(MetaData())
  table._prefixes = ["TEMPORARY"]
  async with store.engine.begin() as connection:
    await connection.execute(CreateTable(table))
  # Snapshot invalidation is unrelated to this storage-content test. Factor
  # writes, advisory locks, readback, checkpoints and claim recovery are real.
  invalidation = AsyncMock()
  monkeypatch.setattr(
    DividFactorRepository, "_invalidate_published_snapshots", invalidation
  )
  return store, invalidation


async def prepare(store, tmp_path, *, code_count=2):
  codes = [f"{i:06}.SZ" for i in range(1, code_count + 1)]
  payload = {
    "operation": "divid_factors",
    "stock_list": codes,
    "start_time": "20200101",
    "end_time": "20201231",
  }
  records = [
    {
      "code": codes[0],
      "ex_date": "20200624",
      "time": 1_592_928_000_000,
      "interest": 17.025,
      "stockBonus": 0,
      "stockGift": 0,
      "allotNum": 0,
      "allotPrice": 0,
      "gugai": 0,
      "dr": 1.011677,
    }
  ]
  store.manifest = [_write_chunk(tmp_path, records)]
  async with store.engine.begin() as connection:
    await connection.execute(
      text("UPDATE market_data_request SET request_payload=CAST(:payload AS json)"),
      {"payload": json.dumps(payload)},
    )
  token = await store.claim_market_data_request("request-1")
  progress = IngestionProgress(store, "request-1", token)
  await progress.apply("begin")
  result = await ingest_uploaded_market_data_request(
    store, "request-1", progress=progress
  )
  assert progress.state["phase"] == "READBACK"
  await store.release_market_data_request_claim(
    "request-1", claim_token=token, error="completion confirmation lost"
  )
  return result


@pytest.mark.parametrize(
  "change",
  [None, "value", "time", "delete", "duplicate", "empty_code", "outside_scope"],
)
async def test_recovery_checks_original_window_without_replacing_it(
  factors,
  tmp_path,
  monkeypatch,
  change,
):
  store, invalidation = factors
  result = await prepare(store, tmp_path)
  mutations = {
    "value": "UPDATE divid_factors SET dr=9",
    "time": "UPDATE divid_factors SET time=time + INTERVAL '1 hour'",
    "delete": "DELETE FROM divid_factors",
    "duplicate": "INSERT INTO divid_factors (stock_code,time,ex_date,interest,stock_bonus,stock_gift,allot_num,allot_price,gugai,dr) SELECT stock_code,time,ex_date,interest,stock_bonus,stock_gift,allot_num,allot_price,gugai,dr FROM divid_factors",
    "empty_code": "INSERT INTO divid_factors (stock_code,time,ex_date,interest,stock_bonus,stock_gift,allot_num,allot_price,gugai,dr) SELECT '000002.SZ',time,ex_date,interest,stock_bonus,stock_gift,allot_num,allot_price,gugai,dr FROM divid_factors",
    "outside_scope": "INSERT INTO divid_factors (stock_code,time,ex_date,dr) VALUES ('600000.SH','2020-06-24','20200624',1), ('000002.SZ','2021-06-24','20210624',1)",
  }
  if change:
    async with store.engine.begin() as connection:
      await connection.execute(text(mutations[change]))

  async def no_write(*args, **kwargs):
    pytest.fail("factor recovery must not rewrite the committed version")

  monkeypatch.setattr(store, "persist_market_data_reference", no_write)
  token = await store.claim_market_data_request("request-1")
  recovery = IngestionProgress(store, "request-1", token)
  await recovery.apply("begin")
  if change not in {None, "outside_scope"}:
    with pytest.raises(RuntimeError, match="persisted content mismatch"):
      await ingest_uploaded_market_data_request(store, "request-1", progress=recovery)
    row = await store.market_data_request("request-1")
    assert row["status"] != "COMPLETED"
    assert row["ingestion_progress"]["phase"] == "READBACK"
  else:
    assert (
      await ingest_uploaded_market_data_request(store, "request-1", progress=recovery)
      == result
    )
    await store.finish_market_data_request(
      "request-1",
      status="COMPLETED",
      ingestion_result=result,
      claim_token=token,
    )
    assert (await store.market_data_request("request-1"))["status"] == "COMPLETED"
  assert invalidation.await_count == 1


@pytest.mark.parametrize("scope", ["whole", "code"])
async def test_recovery_rejects_audit_not_matching_original_upload(
  factors, tmp_path, scope
):
  store, _ = factors
  await prepare(store, tmp_path)
  row = await store.market_data_request("request-1")
  state = row["ingestion_progress"]
  proof = state["write_result"]["replacement_audit"]
  if scope == "code":
    proof = proof["code_audits"]["000001.SZ"]
  proof["source_sha256"] = proof["persisted_sha256"] = "0" * 64
  async with store.engine.begin() as connection:
    await connection.execute(
      text("UPDATE market_data_request SET ingestion_progress=CAST(:value AS jsonb)"),
      {"value": json.dumps(state)},
    )
  token = await store.claim_market_data_request("request-1")
  recovery = IngestionProgress(store, "request-1", token)
  await recovery.apply("begin")
  with pytest.raises(RuntimeError, match="source.*mismatch"):
    await ingest_uploaded_market_data_request(store, "request-1", progress=recovery)


async def test_recovery_bounds_reads_including_empty_requested_codes(factors, tmp_path):
  store, _ = factors
  await prepare(store, tmp_path, code_count=65)
  token = await store.claim_market_data_request("request-1")
  recovery = IngestionProgress(store, "request-1", token)
  await recovery.apply("begin")
  queries = []

  def capture(connection, cursor, statement, parameters, context, executemany):
    if statement.startswith("SELECT divid_factors.stock_code"):
      queries.append((statement, parameters))

  event.listen(store.engine.sync_engine, "before_cursor_execute", capture)
  try:
    await ingest_uploaded_market_data_request(store, "request-1", progress=recovery)
  finally:
    event.remove(store.engine.sync_engine, "before_cursor_execute", capture)
  assert len(queries) == 2
  assert all(
    "LIMIT" in sql and "stock_code IN" in sql and "ex_date >=" in sql
    for sql, _ in queries
  )
  # One populated chunk and one wholly empty chunk: both probe an extra row.
  assert [
    parameters[int(re.search(r"LIMIT \$(\d+)", sql).group(1)) - 1]
    for sql, parameters in queries
  ] == [2, 1]
