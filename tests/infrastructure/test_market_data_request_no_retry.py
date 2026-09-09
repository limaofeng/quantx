"""A bounded cache supplement must not reopen or replace a failed request."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.services import market_data_request_service as service


@pytest.mark.parametrize("status", ["FAILED", "BLOCKED", "CANCELLED"])
@pytest.mark.parametrize("queued", [False, True])
async def test_failed_request_returns_once_without_reopening(
  monkeypatch, status, queued
):
  store = SimpleNamespace(
    create_market_data_request=AsyncMock(return_value="request-1"),
    market_data_request=AsyncMock(
      return_value={"status": status, "processing_error": "date unavailable"}
    ),
    available_market_data_device=AsyncMock(return_value="device-1"),
    reopen_failed_market_data_request=AsyncMock(
      side_effect=AssertionError("must not reopen")
    ),
    close=AsyncMock(),
  )
  monkeypatch.setattr(service, "DurableRuntimeStore", lambda: store)
  request = (
    service.queue_agent_market_data if queued else service.request_agent_market_data
  )
  result = await request(
    payload={"operation": "bars"},
    idempotency_scope="fixture-no-retry",
  )
  assert result["status"] == status.lower()
  assert result["request_id"] == "request-1"
  store.create_market_data_request.assert_awaited_once()
  store.market_data_request.assert_awaited_once()
  store.reopen_failed_market_data_request.assert_not_awaited()
  store.close.assert_awaited_once()
