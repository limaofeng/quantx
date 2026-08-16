import pytest
from quantx_infrastructure.services import market_data_request_service


@pytest.mark.asyncio
async def test_market_data_sync_forces_agent_history_download(monkeypatch) -> None:
  captured = {}

  async def fake_request_agent_market_data(*, payload, timeout_seconds):
    captured["payload"] = payload
    captured["timeout_seconds"] = timeout_seconds
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
  }
