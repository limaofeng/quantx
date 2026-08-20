from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_infrastructure.services.t_trade_replay_service import TTradeReplayService


@pytest.mark.asyncio
async def test_get_does_not_fall_back_when_replay_projection_is_missing() -> None:
  service = TTradeReplayService()
  service._load_run_and_backtest = AsyncMock(
    return_value=(
      SimpleNamespace(parameters={"t_trade_replay": True}),
      SimpleNamespace(id="backtest-1"),
    )
  )

  with patch(
    "quantx_infrastructure.services.t_trade_replay_service."
    "t_trade_replay_projection_service.get",
    new_callable=AsyncMock,
    return_value=None,
  ):
    assert await service.get("replay-1") is None
