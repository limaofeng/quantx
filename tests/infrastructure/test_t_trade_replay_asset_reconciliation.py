from decimal import Decimal
from types import SimpleNamespace

from quantx_infrastructure.services.t_trade_replay_service import TTradeReplayService


def test_snapshot_reconciliation_preserves_residual_and_source_quality_flags() -> None:
  snapshot = SimpleNamespace(
    data_quality="ASSET_COMPONENT_MISMATCH",
    snapshot_metadata={
      "quality_flags": ["ASSET_COMPONENT_MISMATCH", "NO_PREVIOUS_SNAPSHOT"]
    },
    market_value_cny=Decimal("9000.00"),
  )

  result = TTradeReplayService._build_initial_asset_reconciliation(
    cash=80_000.0,
    total_asset=100_000.0,
    positions=[{"stock_code": "000001.SZ", "market_value": 10_000.0}],
    snapshot=snapshot,
  )

  assert result["position_market_value"] == 10_000.0
  assert result["snapshot_market_value"] == 9_000.0
  assert result["raw_residual"] == 10_000.0
  assert result["non_trading_asset"] == 10_000.0
  assert result["effective_initial_equity"] == 100_000.0
  assert result["negative_residual_clamped"] is False
  assert result["quality_flags"] == [
    "ASSET_COMPONENT_MISMATCH",
    "NON_TRADING_ASSET_RESIDUAL_PRESERVED",
    "NO_PREVIOUS_SNAPSHOT",
  ]


def test_negative_residual_is_clamped_and_effective_equity_uses_known_components() -> (
  None
):
  result = TTradeReplayService._build_initial_asset_reconciliation(
    cash=95_000.0,
    total_asset=100_000.0,
    positions=[{"stock_code": "000001.SZ", "market_value": 10_000.0}],
  )

  assert result["raw_residual"] == -5_000.0
  assert result["non_trading_asset"] == 0.0
  assert result["effective_initial_equity"] == 105_000.0
  assert result["negative_residual_clamped"] is True
  assert result["quality_flags"] == ["INITIAL_COMPONENTS_EXCEED_REPORTED_TOTAL"]
  assert result["policy"] == "PRESERVE_POSITIVE_RESIDUAL_CLAMP_NEGATIVE_TO_ZERO"
