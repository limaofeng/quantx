from datetime import datetime, timedelta

import pytest
from quantx_api.gqlapi.resolvers.strategies import StrategyResolver
from quantx_api.gqlapi.types.strategy_types import (
  StrategyApprovalIntent,
  StrategyExitPlanView,
  StrategyRunMode,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord


def test_pending_board_intent_uses_original_signal_time_for_expiry():
  created_at = datetime(2026, 7, 31, 10, 0)
  record = TradeIntentRecord(
    id="intent-1",
    strategy_run_id="run-1",
    strategy_id="board",
    instrument_code="000001.SZ",
    direction="BUY",
    bucket="swing",
    reason="limit_up_board_entry",
    status="AWAITING_APPROVAL",
    confidence=0.8,
    intent_metadata={
      "execution_mode": "MANUAL_CONFIRM",
      "approval_ttl_ms": 15000,
      "intent_created_at": created_at.isoformat(),
      "signal_price": 10.99,
      "limit_up": 11.0,
    },
  )
  record.created_at = created_at + timedelta(seconds=2)

  view = StrategyApprovalIntent.from_record(record)

  assert view.created_at == created_at
  assert view.approval_expires_at == created_at + timedelta(seconds=15)
  assert view.signal_price == 10.99
  assert view.limit_up_price == 11.0


def test_exit_plan_projection_keeps_t1_and_rules_visible():
  view = StrategyExitPlanView.from_projection(
    {
      "status": "ACTIVE",
      "entry_filled_volume": 500,
      "remaining_volume": 500,
      "holding_trading_days": 1,
      "template": {
        "plan_id": "plan-1",
        "instrument_code": "000001.SZ",
        "source_type": "LIMIT_UP_BOARD",
        "bucket": "swing",
        "t1_policy": "WAIT_UNTIL_SELLABLE",
        "auto_exit_authorized": False,
        "execution": {"execution_mode": "MANUAL_CONFIRM"},
        "rules": [
          {"strategy": "LIMIT_UP_BREAK"},
          {"strategy": "MAX_HOLDING_DAYS"},
        ],
      },
    }
  )

  assert view.id == "plan-1"
  assert view.remaining_volume == 500
  assert view.t1_policy == "WAIT_UNTIL_SELLABLE"
  assert view.rule_types == ["LIMIT_UP_BREAK", "MAX_HOLDING_DAYS"]


def test_backtest_range_rejects_reversed_dates():
  with pytest.raises(ValueError, match="结束时间不能早于开始时间"):
    StrategyResolver._validate_backtest_time_range(
      StrategyRunMode.BACKTEST,
      datetime(2026, 7, 31, 0, 0),
      datetime(2026, 7, 30, 23, 59),
    )


def test_backtest_range_requires_both_boundaries():
  with pytest.raises(ValueError, match="必须指定开始和结束时间"):
    StrategyResolver._validate_backtest_time_range(
      StrategyRunMode.BACKTEST,
      datetime(2026, 7, 31, 0, 0),
      None,
    )
