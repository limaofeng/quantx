import asyncio
from datetime import date, datetime
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
    "idempotency_scope": "t-trade-replay-supplement-v2",
  }


@pytest.mark.asyncio
async def test_canonical_tick_sync_uses_scope_specific_durable_request(
  monkeypatch,
) -> None:
  captured = {}

  async def fake_request_agent_market_data(
    *,
    payload,
    timeout_seconds,
    idempotency_scope,
  ):
    captured.update(
      payload=payload,
      timeout_seconds=timeout_seconds,
      idempotency_scope=idempotency_scope,
    )
    return {"status": "success", "request_id": "canonical-request"}

  monkeypatch.setattr(
    market_data_request_service,
    "request_agent_market_data",
    fake_request_agent_market_data,
  )

  result = await market_data_request_service.request_canonical_tick_sync(
    stock_code="600887.SH",
    start_time="20260722",
    end_time="20260728",
    preparation_id="b" * 64,
    verification_pass=2,
    timeout_seconds=321,
  )

  assert result == {"status": "success", "request_id": "canonical-request"}
  assert captured == {
    "payload": {
      "operation": "bars",
      "download": True,
      "stock_list": ["600887.SH"],
      "start_time": "20260722",
      "end_time": "20260728",
      "periods": ["tick"],
      "destination": "canonical_tick_archive",
      "canonical_preparation_id": "b" * 64,
      "canonical_verification_pass": 2,
    },
    "timeout_seconds": 321,
    "idempotency_scope": (
      "canonical-tick-preparation:"
      f"{'b' * 64}:2:600887.SH:20260722:20260728"
    ),
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
  assert [call.kwargs["idempotency_scope"] for call in store.create.await_args_list] == [
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
async def test_same_optional_gap_is_reused_and_ingested_on_next_replay(
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
  ingestion = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-1",
      "records_received": 10,
      "records_saved": 10,
    }
  )
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    market_data_request_service,
    "claim_ingest_and_finish_market_data_request",
    ingestion,
  )
  request = {
    "stock_list": ["600887.SH"],
    "start_time": "20260803",
    "end_time": "20260804",
    "periods": ["tick"],
  }

  first = await market_data_request_service.queue_market_data_sync(**request)
  second = await market_data_request_service.queue_market_data_sync(**request)

  assert first["status"] == "queued"
  assert second["status"] == "success"
  assert second["records_saved"] == 10
  assert [call.kwargs["idempotency_scope"] for call in store.create.await_args_list] == [
    "t-trade-replay-supplement-v2",
    "t-trade-replay-supplement-v2",
  ]
  assert {call.args[0]["stock_list"][0] for call in store.create.await_args_list} == {
    "600887.SH"
  }
  ingestion.assert_awaited_once_with(store, "request-1")


@pytest.mark.asyncio
async def test_optional_queue_reopens_failed_complete_transfer_and_succeeds(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.status = "FAILED"
      self.create = AsyncMock(return_value="request-1")
      self.reopen = AsyncMock(side_effect=self._reopen)
      self.close = AsyncMock()

    async def available_market_data_device(self):
      return "device-1"

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

    async def market_data_request(self, request_id):
      assert request_id == "request-1"
      return {"status": self.status, "processing_error": "transient write error"}

    async def _reopen(self, request_id):
      assert request_id == "request-1"
      self.status = "UPLOADED"

    async def reopen_failed_market_data_request(self, request_id):
      return await self.reopen(request_id)

  store = FakeStore()
  ingestion = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-1",
      "records_received": 2,
      "records_saved": 2,
    }
  )
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    market_data_request_service,
    "claim_ingest_and_finish_market_data_request",
    ingestion,
  )

  result = await market_data_request_service.queue_agent_market_data(
    payload={"operation": "bars"},
    idempotency_scope="stable-replay-scope",
  )

  store.reopen.assert_awaited_once_with("request-1")
  ingestion.assert_awaited_once_with(store, "request-1")
  assert result["status"] == "success"
  assert result["request_id"] == "request-1"


@pytest.mark.asyncio
async def test_optional_queue_replaces_failed_incomplete_generation_and_succeeds(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.create = AsyncMock(side_effect=["request-1", "request-2"])
      self.reopen = AsyncMock(
        side_effect=RuntimeError("market-data request is not safely reopenable")
      )
      self.close = AsyncMock()

    async def available_market_data_device(self):
      return "device-1"

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

    async def market_data_request(self, request_id):
      if request_id == "request-1":
        return {"status": "FAILED", "processing_error": "incomplete upload"}
      assert request_id == "request-2"
      return {"status": "UPLOADED"}

    async def reopen_failed_market_data_request(self, request_id):
      return await self.reopen(request_id)

  store = FakeStore()
  ingestion = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-2",
      "records_received": 2,
      "records_saved": 2,
    }
  )
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    market_data_request_service,
    "claim_ingest_and_finish_market_data_request",
    ingestion,
  )

  result = await market_data_request_service.queue_agent_market_data(
    payload={"operation": "bars"},
    idempotency_scope="stable-replay-scope",
  )

  assert store.create.await_args_list[0].kwargs == {
    "device_id": "device-1",
    "idempotency_scope": "stable-replay-scope",
  }
  assert store.create.await_args_list[1].kwargs == {
    "device_id": "device-1",
    "idempotency_scope": "market-data-failed-retry:request-1",
  }
  store.reopen.assert_awaited_once_with("request-1")
  ingestion.assert_awaited_once_with(store, "request-2")
  assert result["status"] == "success"
  assert result["request_id"] == "request-2"


@pytest.mark.asyncio
async def test_optional_queue_failed_generation_chain_is_bounded(monkeypatch) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.create = AsyncMock(
        side_effect=["request-1", "request-2", "request-3"]
      )
      self.reopen = AsyncMock(
        side_effect=RuntimeError("market-data request is not safely reopenable")
      )
      self.close = AsyncMock()

    async def available_market_data_device(self):
      return "device-1"

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

    async def market_data_request(self, request_id):
      return {"status": "FAILED", "processing_error": f"poisoned {request_id}"}

    async def reopen_failed_market_data_request(self, request_id):
      return await self.reopen(request_id)

  store = FakeStore()
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(market_data_request_service, "_MAX_FAILED_REQUEST_RETRY_HOPS", 2)

  result = await market_data_request_service.queue_agent_market_data(
    payload={"operation": "bars"},
    idempotency_scope="stable-replay-scope",
  )

  assert result == {
    "status": "failed",
    "request_id": "request-3",
    "device_id": "device-1",
    "reason": "market-data failed-request retry chain exceeded safe limit",
  }
  assert store.create.await_count == 3
  assert store.reopen.await_count == 3
  store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_request_ingests_uploaded_transfer_before_returning(
  monkeypatch,
) -> None:
  class FakeStore:
    async def create_market_data_request(self, payload):
      assert payload == {"operation": "bars"}
      return "request-1"

    async def market_data_request(self, request_id):
      assert request_id == "request-1"
      return {"status": "UPLOADED"}

    async def close(self):
      return None

  store = FakeStore()
  converge = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-1",
      "operation": "bars",
      "records_received": 2,
      "records_saved": 2,
    }
  )
  monkeypatch.setattr(
    market_data_request_service,
    "DurableRuntimeStore",
    lambda: store,
  )
  monkeypatch.setattr(
    market_data_request_service,
    "claim_ingest_and_finish_market_data_request",
    converge,
  )

  result = await market_data_request_service.request_agent_market_data(
    payload={"operation": "bars"},
    timeout_seconds=1,
  )

  converge.assert_awaited_once_with(store, "request-1")
  assert result == {
    "status": "success",
    "request_id": "request-1",
    "operation": "bars",
    "records_received": 2,
    "records_saved": 2,
  }


@pytest.mark.asyncio
async def test_agent_request_reopens_complete_failed_transfer_before_retrying_agent(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.status = "FAILED"
      self.create = AsyncMock(return_value="request-1")
      self.reopen = AsyncMock(side_effect=self._reopen)

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

    async def market_data_request(self, request_id):
      assert request_id == "request-1"
      return {"status": self.status, "processing_error": "transient write error"}

    async def _reopen(self, request_id):
      assert request_id == "request-1"
      self.status = "UPLOADED"
      return {"request_id": request_id, "status": "UPLOADED"}

    async def reopen_failed_market_data_request(self, request_id):
      return await self.reopen(request_id)

    async def close(self):
      return None

  store = FakeStore()
  converge = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-1",
      "operation": "bars",
      "records_received": 2,
      "records_saved": 2,
    }
  )
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    market_data_request_service,
    "claim_ingest_and_finish_market_data_request",
    converge,
  )

  result = await market_data_request_service.request_agent_market_data(
    payload={"operation": "bars"},
    timeout_seconds=1,
  )

  store.create.assert_awaited_once_with({"operation": "bars"})
  store.reopen.assert_awaited_once_with("request-1")
  converge.assert_awaited_once_with(store, "request-1")
  assert result["status"] == "success"


@pytest.mark.asyncio
async def test_agent_request_replaces_incomplete_failed_transfer_generation(
  monkeypatch,
) -> None:
  class FakeStore:
    def __init__(self) -> None:
      self.create = AsyncMock(side_effect=["request-1", "request-2"])
      self.reopen = AsyncMock(
        side_effect=RuntimeError("market-data request is not safely reopenable")
      )

    async def create_market_data_request(self, payload, **kwargs):
      return await self.create(payload, **kwargs)

    async def market_data_request(self, request_id):
      if request_id == "request-1":
        return {"status": "FAILED", "processing_error": "incomplete upload"}
      assert request_id == "request-2"
      return {"status": "UPLOADED"}

    async def reopen_failed_market_data_request(self, request_id):
      return await self.reopen(request_id)

    async def close(self):
      return None

  store = FakeStore()
  converge = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-2",
      "operation": "bars",
      "records_received": 2,
      "records_saved": 2,
    }
  )
  monkeypatch.setattr(market_data_request_service, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(
    market_data_request_service,
    "claim_ingest_and_finish_market_data_request",
    converge,
  )

  result = await market_data_request_service.request_agent_market_data(
    payload={"operation": "bars"},
    timeout_seconds=1,
  )

  assert store.create.await_args_list[0].args == ({"operation": "bars"},)
  assert store.create.await_args_list[1].kwargs == {
    "idempotency_scope": "market-data-failed-retry:request-1"
  }
  store.reopen.assert_awaited_once_with("request-1")
  converge.assert_awaited_once_with(store, "request-2")
  assert result["status"] == "success"
  assert result["request_id"] == "request-2"
