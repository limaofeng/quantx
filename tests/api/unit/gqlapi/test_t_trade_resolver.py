from datetime import datetime

from quantx_api.gqlapi.resolvers.t_trade import TTradeResolver
from quantx_api.gqlapi.types.t_trade_types import TTradeTimeExitMode


def test_global_monitor_projects_graphql_scalar_types():
  monitor = TTradeResolver._global_monitor_type(
    {
      "config_id": None,
      "strategy_run_id": None,
      "universe_revision": 0,
      "account_id": "account-1",
      "enabled": False,
      "mode": "paper",
      "auto_exit_acknowledged": False,
      "ignored_stock_codes": [],
      "config_version": 0,
      "target_trade_amount": 10_000.0,
      "max_trade_amount": 12_000.0,
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 0.1,
      "signal_lookback_seconds": 300,
      "stabilization_seconds": 15,
      "pullback_threshold_pct": 0.8,
      "rebound_threshold_pct": 0.2,
      "max_spread_ticks": 3,
      "approval_ttl_seconds": 30,
      "max_price_deviation_pct": 0.3,
      "target_profit_pct": 2.0,
      "base_floor_pct": 0.5,
      "initial_gap_pct": 1.5,
      "trailing_gap_slope": 0.25,
      "max_gap_pct": 3.0,
      "hard_stop_enabled": False,
      "hard_stop_pct": -0.8,
      "time_exit_mode": "UNLIMITED",
      "time_exit_time": "14:50",
      "max_holding_trading_days": 5,
      "cooldown_seconds": 300,
      "holding_count": 0,
      "eligible_count": 0,
      "ignored_count": 0,
      "monitored_count": 0,
      "pending_signal_count": 0,
      "active_batch_count": 0,
      "draining_count": 0,
      "holdings": [],
      "sessions": [],
      "position_snapshot_source": None,
      "position_snapshot_sequence": "0",
      "position_snapshot_reported_at": "2026-07-28T04:30:00Z",
      "position_snapshot_received_at": "2026-07-28T04:30:01+00:00",
      "position_snapshot_complete": False,
      "position_snapshot_error": None,
      "last_reconciled_at": "2026-07-28T04:31:00Z",
      "last_error": None,
      "created_at": None,
      "updated_at": "2026-07-28T04:32:00Z",
      "projection_generated_at": "2026-07-28T04:32:01Z",
      "readiness": {
        "account_id": "account-1",
        "ready": True,
        "stage": "PAPER",
        "engine_status": "ONLINE",
        "agent_status": "ONLINE",
        "agent_device_id": "device-1",
        "reconcile_status": "READY",
        "kill_switch": False,
        "policy_version": 3,
        "can_approve": True,
        "can_activate_live": False,
        "blocked_reasons": [],
        "checked_at": "2026-07-28T04:32:00Z",
        "checks": [
          {
            "code": "ENGINE_ONLINE",
            "passed": True,
            "message": "Engine online",
          }
        ],
      },
    }
  )

  assert monitor.time_exit_mode is TTradeTimeExitMode.UNLIMITED
  assert isinstance(monitor.position_snapshot_reported_at, datetime)
  assert isinstance(monitor.position_snapshot_received_at, datetime)
  assert isinstance(monitor.last_reconciled_at, datetime)
  assert isinstance(monitor.updated_at, datetime)
  assert isinstance(monitor.projection_generated_at, datetime)
  assert monitor.readiness is not None
  assert isinstance(monitor.readiness.checked_at, datetime)
  assert monitor.readiness.checks[0].passed is True
