"""Durable demand submission and CLI waiting through local HTTP."""

# Imported fixtures are intentionally named by pytest test arguments.
# ruff: noqa: F811

import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from quantx_contracts.market_data_service import HistoryDemand
from quantx_infrastructure.services import local_market_data_client as module
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


@pytest.fixture
def cli():
  path = Path(__file__).resolve().parents[2] / "ops/t-assistant-backtest-data.py"
  spec = importlib.util.spec_from_file_location("local_demand_cli", path)
  value = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(value)
  return value


def demand():
  return HistoryDemand(
    instrument="600000.SH", period="tick", trading_date=date(2026, 9, 9)
  )


async def test_offline_cli_preserves_durable_id_after_timeout(
  workers, cli, monkeypatch, capsys
):  # noqa: F811
  (store, _), _ = workers
  store.demand_source_kind = "AGENT"
  forbid = AsyncMock(side_effect=AssertionError("must not select Agent on submission"))
  monkeypatch.setattr(store, "create_market_data_request", forbid)
  app = create_app(store=store, token="internal", reader=object())
  client_type = module.LocalMarketDataClient
  clients = []
  async with app.router.lifespan_context(app):

    def factory():
      client = client_type(token="internal", transport=httpx.ASGITransport(app))
      clients.append(client)
      return client

    monkeypatch.setattr(module, "LocalMarketDataClient", factory)
    result = await cli.supplement_partition("600000.SH", date(2026, 9, 9), 0.05)
    assert result["status"] == "pending" and result["reason"] == "HISTORY_WAIT_TIMEOUT"
    identity = result["demand_id"]
    assert identity in capsys.readouterr().out
    client = factory()
    try:
      assert await client.submit_history_demand(demand()) == identity
      state = await client.history_demand(identity, expected_partition=demand())
      assert state.state == "WAITING_SOURCE" and state.source_phase is None
    finally:
      await client.close()
    assert all(client.client.is_closed for client in clients)
  async with store.engine.connect() as connection:
    assert await connection.scalar(text("SELECT count(*) FROM market_data_demand")) == 1
  forbid.assert_not_awaited()


def status(kind, source, phase, delivery):
  now = datetime.now(timezone.utc)
  return {
    "demand_id": "a" * 64,
    "partition": demand().model_dump(mode="json"),
    "source_kind": kind,
    "state": "LINKED",
    "source_request_id": "original-request" if kind == "AGENT" else None,
    "delivery_id": "b" * 64 if kind == "REMOTE" else None,
    "source_status": source,
    "source_phase": phase,
    "delivery_status": delivery,
    "reason_code": None,
    "next_probe_at": None,
    "created_at": now,
    "last_progress_at": now,
    "observed_at": now,
  }


@pytest.mark.parametrize("phase", [None, "READBACK", "VERIFIED"])
async def test_source_phase_comes_from_persisted_checkpoint(workers, phase):
  (store, _), _ = workers
  store.demand_source_kind = "AGENT"
  identity = await store.submit_history_demand(demand())
  async with store.engine.begin() as connection:
    await connection.execute(
      text("UPDATE market_data_demand SET source_request_id='request-1'")
    )
    await connection.execute(
      text(
        "UPDATE market_data_request SET status='COMPLETED', ingestion_progress=CAST(:progress AS jsonb) WHERE request_id='request-1'"
      ),
      {"progress": json.dumps({"phase": phase}) if phase else None},
    )
  app = create_app(store=store, token="internal", reader=object())
  async with app.router.lifespan_context(app):
    client = module.LocalMarketDataClient(
      token="internal", transport=httpx.ASGITransport(app)
    )
    try:
      value = await client.history_demand(identity, expected_partition=demand())
      assert value.source_status == "COMPLETED" and value.source_phase == phase
    finally:
      await client.close()


@pytest.mark.parametrize(
  "kind,source,phase,delivery,outcome",
  [
    ("AGENT", "COMPLETED", "VERIFIED", None, "success"),
    ("AGENT", "COMPLETED", "READBACK", None, "pending"),
    ("AGENT", "COMPLETED", None, None, "pending"),
    ("AGENT", "FAILED", "WRITE", None, "failed"),
    ("REMOTE", None, None, "READY", "pending"),
    ("REMOTE", None, None, "LOCAL_VERIFIED", "success"),
    ("REMOTE", None, None, "INCOMPLETE", "failed"),
    ("REMOTE", None, None, "BLOCKED", "failed"),
    ("REMOTE", None, None, "WAITING_LOCAL_PROOF", "pending"),
  ],
)
async def test_cli_verification_gate(
  cli, monkeypatch, kind, source, phase, delivery, outcome
):
  store = SimpleNamespace(
    submit_history_demand=AsyncMock(return_value="a" * 64),
    history_demand=AsyncMock(return_value=status(kind, source, phase, delivery)),
  )
  app = create_app(store=store, token="internal", reader=object())
  client = module.LocalMarketDataClient(
    token="internal", transport=httpx.ASGITransport(app)
  )
  monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
  async with app.router.lifespan_context(app):
    result = await cli.supplement_partition("600000.SH", date(2026, 9, 9), 0.05)
  assert result["status"] == outcome and result["demand_id"] == "a" * 64
  assert result["source_phase"] == phase and result["delivery_status"] == delivery
  assert client.client.is_closed
  store.submit_history_demand.assert_awaited_once_with(demand())


async def test_remote_proof_failure_reason_is_exposed_by_local_api(workers):
  (store, _), _ = workers
  store.demand_source_kind = "REMOTE"
  identity = await store.submit_history_demand(demand())
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      INSERT INTO development_data_export(id,request,state,error,updated_at)
      VALUES (:id,'{}','BLOCKED','LOCAL_DELIVERY_PROOF_INVALID',clock_timestamp())
    """),
      {"id": "b" * 64},
    )
    await connection.execute(
      text("UPDATE market_data_demand SET delivery_id=:id"), {"id": "b" * 64}
    )
  app = create_app(store=store, token="internal", reader=object())
  async with app.router.lifespan_context(app):
    client = module.LocalMarketDataClient(
      token="internal", transport=httpx.ASGITransport(app)
    )
    try:
      value = await client.history_demand(identity, expected_partition=demand())
      assert value.delivery_status == "BLOCKED"
      assert value.reason_code == "LOCAL_DELIVERY_PROOF_INVALID"
    finally:
      await client.close()


@pytest.mark.parametrize("change", ["id", "partition"])
async def test_status_scope_mismatch_rejected(change):
  value = status("AGENT", "QUEUED", None, None)
  value = {
    key: item.isoformat() if isinstance(item, datetime) else item
    for key, item in value.items()
  }
  if change == "id":
    value["demand_id"] = "b" * 64
  else:
    value["partition"]["instrument"] = "000001.SZ"
  client = module.LocalMarketDataClient(
    token="internal",
    transport=httpx.MockTransport(lambda request: httpx.Response(200, json=value)),
  )
  try:
    with pytest.raises(ValueError, match="identity mismatch"):
      await client.history_demand("a" * 64, expected_partition=demand())
  finally:
    await client.close()
