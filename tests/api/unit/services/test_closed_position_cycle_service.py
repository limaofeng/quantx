from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from quantx_infrastructure.models.enums import OrderType
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.services.closed_position_cycle_service import (
  ClosedPositionCycleService,
  reconstruct_cycle,
)


def make_trade(
  trade_id: str,
  order_type: OrderType,
  volume: int,
  amount: float,
  minute: int,
) -> Trade:
  return Trade(
    id=trade_id,
    account_id="account-a",
    stock_code="600000.SH",
    order_id=minute,
    order_sysid=str(minute),
    order_type=int(order_type),
    time=datetime(2026, 7, 21, 10, minute),
    price=amount / volume,
    volume=volume,
    amount=amount,
  )


def test_reconstructs_complete_gross_cycle_with_partial_sells():
  result = reconstruct_cycle(
    [
      make_trade("buy-1", OrderType.BUY, 100, 1000, 1),
      make_trade("buy-2", OrderType.BUY, 100, 1200, 2),
      make_trade("sell-1", OrderType.SELL, 50, 650, 3),
      make_trade("sell-2", OrderType.SELL, 150, 1950, 4),
    ]
  )

  assert result.complete is True
  assert result.buy_volume == result.sell_volume == 200
  assert result.gross_sell_amount - result.gross_buy_amount == 400
  assert result.related_trade_ids == ["buy-1", "buy-2", "sell-1", "sell-2"]


def test_missing_open_history_is_kept_but_never_given_fake_pnl():
  result = reconstruct_cycle(
    [make_trade("sell-only", OrderType.SELL, 100, 1200, 1)]
  )

  assert result.complete is False
  assert "MISSING_OPEN_TRADES" in result.quality_flags
  assert "VOLUME_MISMATCH" in result.quality_flags


def test_partial_sell_does_not_form_a_complete_cycle():
  result = reconstruct_cycle(
    [
      make_trade("buy", OrderType.BUY, 200, 2000, 1),
      make_trade("sell", OrderType.SELL, 100, 1100, 2),
    ]
  )

  assert result.complete is False
  assert "VOLUME_MISMATCH" in result.quality_flags


def test_sell_before_open_is_never_marked_complete():
  result = reconstruct_cycle(
    [
      make_trade("sell", OrderType.SELL, 100, 1100, 1),
      make_trade("buy", OrderType.BUY, 100, 1000, 2),
    ]
  )

  assert result.complete is False
  assert "SELL_BEFORE_OPEN" in result.quality_flags


def test_cycle_id_is_stable_and_account_scoped():
  opened_at = datetime(2026, 7, 21, 9, 30)
  first = ClosedPositionCycleService.cycle_id(
    "account-a", "600000.SH", opened_at
  )
  duplicate = ClosedPositionCycleService.cycle_id(
    "account-a", "600000.SH", opened_at
  )
  other_account = ClosedPositionCycleService.cycle_id(
    "account-b", "600000.SH", opened_at
  )

  assert first == duplicate
  assert first != other_account


def test_trade_window_normalizes_mixed_timezone_values_to_naive_utc():
  cycle = SimpleNamespace(
    opened_at=datetime(2026, 7, 21, 9, 30),
    closed_at=datetime(
      2026, 7, 21, 18, 0, tzinfo=timezone(timedelta(hours=8))
    ),
  )

  start_at, end_at = ClosedPositionCycleService._trade_window(cycle)

  assert start_at == datetime(2026, 7, 21, 9, 20)
  assert end_at == datetime(2026, 7, 21, 10, 10)
  assert start_at.tzinfo is None
  assert end_at.tzinfo is None
