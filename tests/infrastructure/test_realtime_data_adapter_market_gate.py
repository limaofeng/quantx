from types import SimpleNamespace

import pytest
from quantx_infrastructure.core.data.realtime import RealtimeDataAdapter
from quantx_infrastructure.core.data.whole_quote_hub import WholeQuoteStatus


@pytest.mark.asyncio
async def test_realtime_adapter_rejects_tick_while_hub_is_not_ready() -> None:
  adapter = RealtimeDataAdapter()
  adapter.subscription_manager = SimpleNamespace(
    hub=SimpleNamespace(
      is_ready=False,
      status=WholeQuoteStatus.SYNCING,
    )
  )

  await adapter._handle_xt_tick_data(
    "600000.SH",
    {"600000.SH": {"lastPrice": 10.2, "time": 3_000}},
  )

  assert adapter.price_cache == {}
  assert adapter.market_data_gate_rejections == 1
