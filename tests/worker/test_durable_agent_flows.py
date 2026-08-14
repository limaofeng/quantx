from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_worker.prefector.flows import durable_agent_flows


@pytest.mark.asyncio
async def test_market_universe_flow_uses_durable_request(monkeypatch) -> None:
  request = AsyncMock(return_value={"status": "completed", "request_id": "request-1"})
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)

  result = await durable_agent_flows.market_universe_request_flow.fn(
    sectors=["沪深A股"]
  )

  assert result["status"] == "completed"
  request.assert_awaited_once_with(
    {"operation": "sector_instruments", "sectors": ["沪深A股"]}
  )


@pytest.mark.asyncio
async def test_instrument_flow_rejects_empty_code_without_request(monkeypatch) -> None:
  request = AsyncMock()
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)

  result = await durable_agent_flows.instrument_request_flow.fn(stock_code="")

  assert result == {"status": "skipped", "reason": "stock_code is required"}
  request.assert_not_awaited()


@pytest.mark.asyncio
async def test_financial_flow_uses_persisted_universe(monkeypatch) -> None:
  monkeypatch.setattr(
    durable_agent_flows,
    "_persisted_stock_codes",
    AsyncMock(return_value=["600000.SH", "000001.SZ"]),
  )
  request = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-2",
      "replacement_audit": {
        "requested_codes": 2,
        "synced_codes": 2,
        "empty_codes": [],
        "rows_upserted": 8,
        "metric_rows_rebuilt": 4,
      },
    }
  )
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)

  updates = []

  class FakeSession:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, *_args):
      return None

  class FakeRunRepo:
    def __init__(self, _db):
      pass

    async def create_run(self, data):
      updates.append(("create", data))
      return SimpleNamespace(id=42)

    async def update_run(self, run_id, data):
      updates.append((run_id, data))
      return SimpleNamespace(id=run_id)

    async def upsert_code_audits(self, records):
      updates.append(("audits", records))
      return len(records)

  monkeypatch.setattr(durable_agent_flows, "AsyncSessionLocal", FakeSession)
  monkeypatch.setattr(
    durable_agent_flows,
    "FinancialSyncRunRepository",
    FakeRunRepo,
  )

  result = await durable_agent_flows.financial_request_flow.fn(
    start_time="20250101",
    end_time="20260101",
  )

  assert result["request_ids"] == ["request-2"]
  assert result["status"] == "success"
  request.assert_awaited_once_with(
    {
      "operation": "financial_data",
      "record_format": "financial-row-v1",
      "download": True,
      "stock_list": ["000001.SZ", "600000.SH"],
      "table_list": ["Balance", "Income", "CashFlow", "Capital"],
      "start_time": "20250101",
      "end_time": "20260101",
      "sync_run_id": 42,
      "batch_index": 1,
    },
    agent_device_id="",
    required_capabilities=["financial-data-v1"],
  )
  assert any(
    key == 42 and data.get("status") == "running" and data.get("synced_codes") == 2
    for key, data in updates
    if key != "create"
  )
  assert updates[-1][1]["status"] == "success"
  audit_records = next(data for key, data in updates if key == "audits")
  assert {record["stock_code"] for record in audit_records} == {
    "000001.SZ",
    "600000.SH",
  }
  assert {record["status"] for record in audit_records} == {"SUCCESS"}


@pytest.mark.asyncio
async def test_financial_flow_splits_205_codes_into_three_batches(monkeypatch) -> None:
  class FakeSession:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, *_args):
      return None

  class FakeRunRepo:
    def __init__(self, _db):
      pass

    async def create_run(self, _data):
      return SimpleNamespace(id=7)

    async def update_run(self, run_id, _data):
      return SimpleNamespace(id=run_id)

    async def upsert_code_audits(self, records):
      return len(records)

  async def request(payload, **_kwargs):
    code_count = len(payload["stock_list"])
    return {
      "status": "completed",
      "request_id": f"request-{payload['batch_index']}",
      "replacement_audit": {
        "requested_codes": code_count,
        "synced_codes": code_count,
        "empty_codes": [],
        "rows_upserted": code_count,
        "metric_rows_rebuilt": code_count,
      },
    }

  request_mock = AsyncMock(side_effect=request)
  monkeypatch.setattr(durable_agent_flows, "AsyncSessionLocal", FakeSession)
  monkeypatch.setattr(
    durable_agent_flows,
    "FinancialSyncRunRepository",
    FakeRunRepo,
  )
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request_mock)
  codes = [f"{index:06d}.SZ" for index in range(205)]

  result = await durable_agent_flows.financial_request_flow.fn(
    stock_codes=codes,
    start_time="20250101",
    end_time="20260101",
  )

  assert result["batch_count"] == 3
  assert result["synced_codes"] == 205
  assert [len(call.args[0]["stock_list"]) for call in request_mock.await_args_list] == [
    100,
    100,
    5,
  ]


@pytest.mark.asyncio
async def test_financial_flow_keeps_successful_code_audits_when_later_batch_fails(
  monkeypatch,
) -> None:
  audits = []

  class FakeSession:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, *_args):
      return None

  class FakeRunRepo:
    def __init__(self, _db):
      pass

    async def create_run(self, _data):
      return SimpleNamespace(id=9)

    async def update_run(self, run_id, _data):
      return SimpleNamespace(id=run_id)

    async def upsert_code_audits(self, records):
      audits.extend(records)
      return len(records)

  async def request(payload, **_kwargs):
    if payload["batch_index"] == 2:
      raise RuntimeError("second batch failed")
    codes = payload["stock_list"]
    return {
      "status": "completed",
      "request_id": "request-1",
      "replacement_audit": {
        "requested_codes": len(codes),
        "synced_codes": len(codes),
        "empty_codes": [],
        "rows_upserted": len(codes),
        "metric_rows_rebuilt": len(codes),
        "statement_rows_by_code": {code: 1 for code in codes},
        "metric_rows_by_code": {code: 1 for code in codes},
      },
    }

  monkeypatch.setattr(durable_agent_flows, "AsyncSessionLocal", FakeSession)
  monkeypatch.setattr(
    durable_agent_flows,
    "FinancialSyncRunRepository",
    FakeRunRepo,
  )
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)

  with pytest.raises(RuntimeError, match="second batch failed"):
    await durable_agent_flows.financial_request_flow.fn(
      stock_codes=["000001.SZ", "000002.SZ", "000003.SZ"],
      start_time="20250101",
      end_time="20260101",
      batch_size=2,
    )

  assert [record["status"] for record in audits] == [
    "SUCCESS",
    "SUCCESS",
    "FAILED",
  ]
  assert audits[-1]["stock_code"] == "000003.SZ"


@pytest.mark.asyncio
async def test_daily_market_flow_uses_agent_transfer_request(monkeypatch) -> None:
  request = AsyncMock(return_value={"status": "completed", "request_id": "request-3"})
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)

  result = await durable_agent_flows.daily_market_data_request_flow.fn(
    stock_list=["600000.SH"],
    periods=["1d"],
    start_time="20260720",
    end_time="20260725",
  )

  assert result["request_id"] == "request-3"
  request.assert_awaited_once_with(
    {
      "operation": "bars",
      "stock_list": ["600000.SH"],
      "periods": ["1d"],
      "start_time": "20260720",
      "end_time": "20260725",
    }
  )


@pytest.mark.asyncio
async def test_interrupted_market_ingestion_is_reclaimed(monkeypatch) -> None:
  class FakeStore:
    def __init__(self):
      self.claimed = False

    async def create_market_data_request(self, payload):
      assert payload == {"operation": "bars"}
      return "request-1"

    async def market_data_request_status(self, request_id):
      assert request_id == "request-1"
      return "PROCESSING"

    async def claim_market_data_request(self, request_id):
      assert request_id == "request-1"
      self.claimed = True
      return True

    async def finish_market_data_request(self, request_id, *, status, error=""):
      assert request_id == "request-1"
      assert status == "COMPLETED"
      assert error == ""

    async def close(self):
      return None

  store = FakeStore()
  monkeypatch.setattr(
    durable_agent_flows,
    "DurableRuntimeStore",
    lambda: store,
  )
  monkeypatch.setattr(
    durable_agent_flows,
    "_ingest_uploaded_request",
    AsyncMock(return_value={"records_received": 1, "records_saved": 1}),
  )

  result = await durable_agent_flows._request_and_wait(
    {"operation": "bars"},
    timeout_seconds=1,
  )

  assert store.claimed is True
  assert result == {
    "status": "completed",
    "request_id": "request-1",
    "records_received": 1,
    "records_saved": 1,
  }


@pytest.mark.asyncio
async def test_explicit_reprocess_claims_uploaded_request(monkeypatch) -> None:
  store = SimpleNamespace(
    market_data_request_status=AsyncMock(return_value="UPLOADED"),
    claim_market_data_request=AsyncMock(return_value=True),
    finish_market_data_request=AsyncMock(),
    close=AsyncMock(),
  )
  ingestion = AsyncMock(
    return_value={
      "operation": "bars",
      "records_received": 2,
      "records_saved": 2,
    }
  )
  monkeypatch.setattr(
    durable_agent_flows,
    "DurableRuntimeStore",
    lambda: store,
  )
  monkeypatch.setattr(
    durable_agent_flows,
    "_ingest_uploaded_request",
    ingestion,
  )

  result = await durable_agent_flows.reprocess_uploaded_market_data_request("request-1")

  store.claim_market_data_request.assert_awaited_once_with("request-1")
  ingestion.assert_awaited_once_with(store, "request-1")
  store.finish_market_data_request.assert_awaited_once_with(
    "request-1",
    status="COMPLETED",
  )
  store.close.assert_awaited_once()
  assert result == {
    "status": "completed",
    "request_id": "request-1",
    "operation": "bars",
    "records_received": 2,
    "records_saved": 2,
  }


@pytest.mark.asyncio
async def test_explicit_reprocess_rejects_request_not_reopened(
  monkeypatch,
) -> None:
  store = SimpleNamespace(
    market_data_request_status=AsyncMock(return_value="FAILED"),
    claim_market_data_request=AsyncMock(),
    finish_market_data_request=AsyncMock(),
    close=AsyncMock(),
  )
  monkeypatch.setattr(
    durable_agent_flows,
    "DurableRuntimeStore",
    lambda: store,
  )

  with pytest.raises(RuntimeError, match="not explicitly reopened"):
    await durable_agent_flows.reprocess_uploaded_market_data_request("request-1")

  store.claim_market_data_request.assert_not_awaited()
  store.finish_market_data_request.assert_not_awaited()
  store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_reprocess_returns_ingestion_failure_to_failed(
  monkeypatch,
) -> None:
  store = SimpleNamespace(
    market_data_request_status=AsyncMock(return_value="UPLOADED"),
    claim_market_data_request=AsyncMock(return_value=True),
    finish_market_data_request=AsyncMock(),
    close=AsyncMock(),
  )
  ingestion = AsyncMock(side_effect=RuntimeError("Influx unavailable"))
  monkeypatch.setattr(
    durable_agent_flows,
    "DurableRuntimeStore",
    lambda: store,
  )
  monkeypatch.setattr(
    durable_agent_flows,
    "_ingest_uploaded_request",
    ingestion,
  )

  with pytest.raises(RuntimeError, match="Influx unavailable"):
    await durable_agent_flows.reprocess_uploaded_market_data_request("request-1")

  store.finish_market_data_request.assert_awaited_once_with(
    "request-1",
    status="FAILED",
    error="RuntimeError: Influx unavailable",
  )
  store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_bond_repo_flow_is_fail_closed(monkeypatch) -> None:
  logger = type("Logger", (), {"warning": lambda self, message: None})()
  monkeypatch.setattr(durable_agent_flows, "get_run_logger", lambda: logger)

  result = await durable_agent_flows.bond_repo_trade_command_flow.fn()

  assert result["status"] == "skipped"
  assert "TradeCommand policy" in result["reason"]


@pytest.mark.asyncio
async def test_bond_repo_flow_enqueues_explicit_trade_command(monkeypatch) -> None:
  logger = type("Logger", (), {"warning": lambda self, message: None})()
  monkeypatch.setattr(durable_agent_flows, "get_run_logger", lambda: logger)
  queued = type(
    "Queued",
    (),
    {
      "status": "QUEUED",
      "client_order_id": "client-1",
      "message_id": "message-1",
    },
  )()
  enqueue = AsyncMock(return_value=queued)

  @asynccontextmanager
  async def session():
    yield object()

  monkeypatch.setattr(durable_agent_flows, "AsyncSessionLocal", session)
  monkeypatch.setattr(
    durable_agent_flows,
    "TradeCommandService",
    lambda db: SimpleNamespace(enqueue_order_for_account=enqueue),
  )

  result = await durable_agent_flows.bond_repo_trade_command_flow.fn(
    account_id="account-1",
    instrument_code="204001.SH",
    annualized_rate=1.8,
    volume=1000,
    idempotency_key="repo-20260726-account-1",
  )

  assert result == {
    "status": "queued",
    "client_order_id": "client-1",
    "message_id": "message-1",
  }
  enqueue.assert_awaited_once()
  assert enqueue.await_args.kwargs["side"] == "SELL"
  assert enqueue.await_args.kwargs["instrument_code"] == "204001.SH"


def _convergence_store(*, ready: bool = True):
  return SimpleNamespace(
    component_status=AsyncMock(
      return_value=[
        {
          "component": "qmt-agent:device-1",
          "status": "READY" if ready else "TRADING_UNAVAILABLE",
        }
      ]
    ),
    close=AsyncMock(),
  )


@pytest.mark.asyncio
async def test_agent_convergence_does_not_sync_when_disabled(monkeypatch) -> None:
  store = _convergence_store()
  position_codes = AsyncMock()
  request = AsyncMock()
  monkeypatch.setattr(durable_agent_flows, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    durable_agent_flows,
    "_persisted_position_codes",
    position_codes,
  )
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)

  result = await durable_agent_flows.agent_convergence_flow.fn()

  assert result["status"] == "ready"
  assert "market_data_sync" not in result
  position_codes.assert_not_awaited()
  request.assert_not_awaited()
  store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_convergence_syncs_positive_position_ticks(monkeypatch) -> None:
  store = _convergence_store()
  position_codes = AsyncMock(return_value=["000001.SZ", "600000.SH"])
  request = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-tick-1",
      "records_received": 123,
      "records_saved": 123,
    }
  )
  trading_dates = SimpleNamespace(is_trading_date=AsyncMock(return_value=True))
  monkeypatch.setattr(durable_agent_flows, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    durable_agent_flows,
    "_persisted_position_codes",
    position_codes,
  )
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)
  monkeypatch.setattr(
    durable_agent_flows,
    "TradingDateHelper",
    lambda: trading_dates,
  )

  result = await durable_agent_flows.agent_convergence_flow.fn(
    sync_market_data=True,
    target_date="20260812",
  )

  request.assert_awaited_once_with(
    {
      "operation": "bars",
      "download": True,
      "stock_list": ["000001.SZ", "600000.SH"],
      "periods": ["tick"],
      "start_time": "20260812",
      "end_time": "20260812",
    }
  )
  assert result["market_data_sync"] == {
    "status": "completed",
    "request_id": "request-tick-1",
    "records_received": 123,
    "records_saved": 123,
    "target_date": "20260812",
    "stock_count": 2,
    "stock_codes": ["000001.SZ", "600000.SH"],
  }


@pytest.mark.asyncio
async def test_agent_convergence_rejects_tick_sync_without_ready_agent(
  monkeypatch,
) -> None:
  store = _convergence_store(ready=False)
  position_codes = AsyncMock()
  request = AsyncMock()
  monkeypatch.setattr(durable_agent_flows, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    durable_agent_flows,
    "_persisted_position_codes",
    position_codes,
  )
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)

  with pytest.raises(RuntimeError, match="no READY QMT Agent"):
    await durable_agent_flows.agent_convergence_flow.fn(
      sync_market_data=True,
      target_date="20260812",
    )

  position_codes.assert_not_awaited()
  request.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_convergence_rejects_incomplete_tick_persistence(
  monkeypatch,
) -> None:
  store = _convergence_store()
  monkeypatch.setattr(durable_agent_flows, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    durable_agent_flows,
    "_persisted_position_codes",
    AsyncMock(return_value=["600000.SH"]),
  )
  monkeypatch.setattr(
    durable_agent_flows,
    "_request_and_wait",
    AsyncMock(
      return_value={
        "status": "completed",
        "request_id": "request-tick-2",
        "records_received": 100,
        "records_saved": 99,
      }
    ),
  )
  monkeypatch.setattr(
    durable_agent_flows,
    "TradingDateHelper",
    lambda: SimpleNamespace(is_trading_date=AsyncMock(return_value=True)),
  )

  with pytest.raises(RuntimeError, match="未完整入库"):
    await durable_agent_flows.agent_convergence_flow.fn(
      sync_market_data=True,
      target_date="20260812",
    )
