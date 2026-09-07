"""A bounded cache supplement must not reopen or replace a failed request."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from quantx_infrastructure.services import market_data_request_service as service


async def test_failed_request_returns_once_without_reopening(monkeypatch):
  store = SimpleNamespace(
    create_market_data_request=AsyncMock(return_value="request-1"),
    market_data_request=AsyncMock(
      return_value={"status": "FAILED", "processing_error": "date unavailable"}
    ),
    close=AsyncMock(),
  )
  recover = AsyncMock(side_effect=AssertionError("retry was disabled"))
  monkeypatch.setattr(service, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(service, "recover_failed_market_data_request", recover)
  result = await service.request_agent_market_data(
    payload={"operation": "bars"},
    idempotency_scope="fixture-no-retry",
    retry_failed_requests=False,
  )
  assert result["status"] == "failed"
  store.create_market_data_request.assert_awaited_once()
  store.market_data_request.assert_awaited_once()
  recover.assert_not_awaited()
  store.close.assert_awaited_once()
