import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.services import market_data_request_service


@pytest.mark.asyncio
async def test_market_data_sync_forces_agent_history_download(monkeypatch) -> None:
  captured = {}

  async def fake_request_agent_market_data(
    *,
    payload,
    timeout_seconds,
    idempotency_scope,
  ):
    captured["payload"] = payload
    captured["timeout_seconds"] = timeout_seconds
    captured["idempotency_scope"] = idempotency_scope
    return {"status": "success", "request_id": "request-1"}

  monkeypatch.setattr(
    market_data_request_service,
    "request_agent_market_data",
    fake_request_agent_market_data,
  )

  result = await market_data_request_service.request_market_data_sync(
    stock_list=["600887.SH"],
    start_time="20260720",
    end_time="20260724",
    periods=["tick"],
    timeout_seconds=120,
    idempotency_scope="backtest-data-supplement-v1:backtest-1",
  )

  assert result == {"status": "success", "request_id": "request-1"}
  assert captured == {
    "payload": {
      "operation": "bars",
      "download": True,
      "stock_list": ["600887.SH"],
      "start_time": "20260720",
      "end_time": "20260724",
      "periods": ["tick"],
    },
    "timeout_seconds": 120,
    "idempotency_scope": "backtest-data-supplement-v1:backtest-1",
  }


@pytest.mark.asyncio
async def test_load_completed_empty_tick_days_keeps_only_canonical_store_proofs(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.close = AsyncMock()

    async def completed_tick_day_coverage(
      self,
      *,
      instrument_code,
      trading_dates,
    ):
      assert instrument_code == "600887.SH"
      assert trading_dates == [date(2026, 8, 3), date(2026, 8, 4)]
      return [
        {
          "trading_date": datetime(2026, 8, 3, 0, 0),
          "point_count": 0,
        },
        {
          "trading_date": "2026-08-04",
          "point_count": "1",
        },
        {
          "trading_date": "2026-08-04",
          "point_count": "00",
        },
        {
          "trading_date": "not-a-trading-day",
          "point_count": "0",
        },
        {
          "trading_date": "2026-08-05",
          "point_count": "0",
        },
      ]

  store = FakeStore()
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)

  empty_days = await market_data_request_service.load_completed_empty_tick_days(
    instrument_code="600887.SH",
    trading_dates=[date(2026, 8, 4), date(2026, 8, 3)],
  )

  assert empty_days == {date(2026, 8, 3)}
  store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_completed_empty_tick_days_does_not_mask_query_failure(
  monkeypatch,
) -> None:
  class FailingStore:
    def __init__(self) -> None:
      self.close = AsyncMock()

    async def completed_tick_day_coverage(self, **_kwargs):
      raise RuntimeError("daily empty-proof lookup unavailable")

  store = FailingStore()
  monkeypatch.setattr(
    market_data_request_service,
    "DurableRuntimeStore",
    lambda: store,
  )

  with pytest.raises(RuntimeError, match="empty-proof lookup unavailable"):
    await market_data_request_service.load_completed_empty_tick_days(
      instrument_code="600887.SH",
      trading_dates=[date(2026, 8, 3)],
    )

  store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_replay_waiter_rejoins_equivalent_nonterminal_request(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.create = AsyncMock(return_value="shared-request")
      self.close = AsyncMock()
      self.first_read = asyncio.Event()
      self.read_count = 0

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

    async def market_data_request(self, request_id):
      assert request_id == "shared-request"
      self.read_count += 1
      if self.read_count == 1:
        self.first_read.set()
        return {"status": "QUEUED"}
      return {
        "status": "COMPLETED",
        "ingestion_result": {"records_saved": 1},
      }

  store = FakeStore()
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  request = {
    "stock_list": ["600887.SH"],
    "start_time": "20260803",
    "end_time": "20260803",
    "periods": ["tick"],
  }

  first = asyncio.create_task(
    market_data_request_service.request_market_data_sync(
      **request,
      timeout_seconds=60,
    )
  )
  await store.first_read.wait()
  first.cancel()
  with pytest.raises(asyncio.CancelledError):
    await first

  restarted = await market_data_request_service.request_market_data_sync(
    **request,
    timeout_seconds=1,
  )

  assert restarted == {
    "status": "success",
    "request_id": "shared-request",
    "records_saved": 1,
  }
  assert [
    call.kwargs["idempotency_scope"] for call in store.create.await_args_list
  ] == [
    "t-trade-replay-supplement-v2",
    "t-trade-replay-supplement-v2",
  ]
  assert [call.args[0] for call in store.create.await_args_list] == [
    {
      "operation": "bars",
      "download": True,
      **request,
    },
    {
      "operation": "bars",
      "download": True,
      **request,
    },
  ]
  assert store.close.await_count == 2


@pytest.mark.asyncio
async def test_optional_agent_queue_does_not_create_request_when_agent_offline(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.create = AsyncMock()
      self.close = AsyncMock()

    async def available_market_data_device(self):
      return None

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

  store = FakeStore()
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)

  result = await market_data_request_service.queue_agent_market_data(
    payload={"operation": "bars"},
    idempotency_scope="replay-1",
  )

  assert result == {
    "status": "skipped",
    "reason": "market_data_agent_unavailable",
  }
  store.create.assert_not_awaited()
  store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_optional_agent_queue_returns_without_waiting_for_transfer(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.create = AsyncMock(return_value="request-1")
      self.close = AsyncMock()

    async def available_market_data_device(self):
      return "device-1"

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

    async def market_data_request(self, request_id):
      assert request_id == "request-1"
      return {"status": "QUEUED"}

  store = FakeStore()
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)

  result = await market_data_request_service.queue_agent_market_data(
    payload={"operation": "bars"},
    idempotency_scope="replay-1",
  )

  assert result == {
    "status": "queued",
    "request_id": "request-1",
    "device_id": "device-1",
  }
  store.create.assert_awaited_once_with(
    {"operation": "bars"},
    device_id="device-1",
    idempotency_scope="replay-1",
  )
  store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_optional_gap_is_reused_without_ingestion_on_next_replay(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.create = AsyncMock(return_value="request-1")
      self.read_count = 0
      self.close = AsyncMock()

    async def available_market_data_device(self):
      return "device-1"

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

    async def market_data_request(self, request_id):
      assert request_id == "request-1"
      self.read_count += 1
      return {"status": "QUEUED" if self.read_count == 1 else "UPLOADED"}

  store = FakeStore()
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  request = {
    "stock_list": ["600887.SH"],
    "start_time": "20260803",
    "end_time": "20260804",
    "periods": ["tick"],
  }

  first = await market_data_request_service.queue_market_data_sync(**request)
  second = await market_data_request_service.queue_market_data_sync(**request)

  assert first["status"] == "queued"
  assert second["status"] == "queued"
  assert [
    call.kwargs["idempotency_scope"] for call in store.create.await_args_list
  ] == [
    "t-trade-replay-supplement-v2",
    "t-trade-replay-supplement-v2",
  ]
  assert {call.args[0]["stock_list"][0] for call in store.create.await_args_list} == {
    "600887.SH"
  }


@pytest.mark.asyncio
async def test_agent_request_leaves_uploaded_transfer_to_worker(monkeypatch):
  store = SimpleNamespace(
    create_market_data_request=AsyncMock(return_value="request-1"),
    market_data_request=AsyncMock(return_value={"status": "UPLOADED"}),
    claim_market_data_request=AsyncMock(
      side_effect=AssertionError("caller cannot ingest")
    ),
    close=AsyncMock(),
  )
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  result = await market_data_request_service.request_agent_market_data(
    payload={"operation": "bars"},
    timeout_seconds=0.01,
  )
  assert result["status"] == "timeout"
  store.market_data_request.assert_awaited_once()
  store.claim_market_data_request.assert_not_called()
