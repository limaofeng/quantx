from datetime import datetime, timezone

import pytest
from quantx_engine import subscription_bridge
from quantx_engine.warm_cache import (
  intraday_warm_cache,
)
from quantx_infrastructure.models.kline import KLine


@pytest.mark.asyncio
async def test_engine_runtime_market_query_reads_owned_warm_cache(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  value = KLine(
    stock_code="600000.SH",
    period="1m",
    time=datetime(2026, 7, 26, 1, 30, tzinfo=timezone.utc),
    open=10,
    high=11,
    low=9,
    close=10.5,
    pre_close=10,
    volume=100,
    amount=1000,
    settelement_price=0,
    open_interest=0,
    suspend_flag=0,
  )
  monkeypatch.setattr(
    intraday_warm_cache,
    "get_klines",
    lambda *_args, **_kwargs: [value],
  )

  result = await subscription_bridge._market_query_result(
    {
      "operation": "warm_klines",
      "payload": {"stock_code": "600000.SH"},
    }
  )

  assert result["items"][0]["stock_code"] == "600000.SH"
  assert result["items"][0]["time"] == value.time.isoformat()
