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
    "_persisted_instrument_codes",
    AsyncMock(return_value=["600000.SH", "000001.SZ"]),
  )
  request = AsyncMock(return_value={"status": "completed", "request_id": "request-2"})
  monkeypatch.setattr(durable_agent_flows, "_request_and_wait", request)

  result = await durable_agent_flows.financial_request_flow.fn()

  assert result["request_id"] == "request-2"
  request.assert_awaited_once_with(
    {
      "operation": "financial_data",
      "stock_list": ["600000.SH", "000001.SZ"],
    }
  )


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

  result = (
    await durable_agent_flows.reprocess_uploaded_market_data_request(
      "request-1"
    )
  )

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
    await durable_agent_flows.reprocess_uploaded_market_data_request(
      "request-1"
    )

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
    await durable_agent_flows.reprocess_uploaded_market_data_request(
      "request-1"
    )

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
