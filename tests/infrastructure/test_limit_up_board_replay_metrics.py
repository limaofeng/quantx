from datetime import datetime
from types import SimpleNamespace

from quantx_infrastructure.core.limit_up_board_replay_metrics import (
  build_limit_up_board_replay_metrics,
)


class _Broker:
  initial_capital = 100_000.0
  trades = []
  orders = {}
  positions = {
    "000001.SZ": SimpleNamespace(
      long_volume=100,
      available_volume=0,
      long_avg_price=10.0,
      last_price=9.0,
      market_value=900.0,
    )
  }
  replay_curve = [{"timestamp": datetime(2026, 8, 20, 15), "equity": 99_900.0}]

  @staticmethod
  def get_constraint_statistics():
    return {"limit_down_sell_blocked": 1}

  @staticmethod
  def get_performance_metrics():
    return {
      "final_equity": 99_900.0,
      "total_return_pct": -0.1,
      "max_drawdown_pct": 0.2,
    }


def test_metrics_keep_unresolved_limit_down_position_open() -> None:
  market = SimpleNamespace(
    price=9.0,
    limit_down=9.0,
    bid_price=[0.0, 0.0, 0.0, 0.0, 0.0],
  )
  runtime = SimpleNamespace(
    broker=_Broker(),
    latest_market_data={"000001.SZ": market},
    context=SimpleNamespace(
      parameters={
        "limit_up_board_replay_scenario_id": "BASE",
        "limit_up_board_replay_confirmation_delay_ms": 3_000,
        "participation_cap_pct": 0.02,
        "book_depth_participation_pct": 0.15,
      }
    ),
  )

  result = build_limit_up_board_replay_metrics(runtime)

  assert result["summary"]["open_position_count"] == 1
  assert result["summary"]["unsellable_position_count"] == 1
  assert result["open_positions"][0]["status"] == "OPEN_UNSELLABLE"
  assert result["summary"]["total_return_pct"] == -0.1
  assert result["summary"]["cvar95_loss_pct"] == 10.0
