from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.gqlapi.resolvers.t_trade import TTradeResolver
from quantx_api.gqlapi.types.t_trade_types import (
  TTradeGlobalMonitor,
  TTradeRolloutTarget,
  TTradeTimeExitMode,
)
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.services.t_trade_service import TTradeService


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
      "future_internal_projection_field": "ignored by GraphQL boundary",
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
        "future_internal_readiness_field": "ignored by GraphQL boundary",
        "checked_at": "2026-07-28T04:32:00Z",
        "checks": [
          {
            "code": "ENGINE_ONLINE",
            "passed": True,
            "message": "Engine online",
            "future_internal_check_field": "ignored by GraphQL boundary",
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
  assert monitor.max_exit_slippage_bps == 30.0
  assert monitor.momentum_enabled is True
  assert monitor.momentum_window_seconds == 60
  assert monitor.momentum_min_amount_velocity_ratio == 2.0
  assert monitor.momentum_min_vwap_premium_pct == 2.0
  assert monitor.momentum_max_vwap_premium_pct == 3.5
  assert monitor.limit_up_touch_exit_enabled is True
  assert monitor.limit_up_touch_tolerance_ticks == 0
  assert monitor.high_profit_lock_enabled is True
  assert monitor.high_profit_arm_pct == 4.0
  assert monitor.high_profit_max_drawdown_pct == 1.2
  assert monitor.rapid_reversal_enabled is True
  assert monitor.rapid_reversal_window_seconds == 15
  assert monitor.rapid_reversal_drawdown_pct == 0.8
  assert monitor.rapid_reversal_confirm_ticks == 2


def test_graphql_projection_keeps_known_fields_and_drops_internal_fields():
  payload = TTradeResolver._graphql_kwargs(
    TTradeGlobalMonitor,
    {
      "max_exit_slippage_bps": 42.0,
      "future_internal_projection_field": "not public",
    },
  )

  assert payload == {"max_exit_slippage_bps": 42.0}


def test_session_graphql_projection_maps_latest_evaluation_and_legacy_null():
  now = datetime(2026, 8, 13, 10, 5)
  run = SimpleNamespace(
    id="run-telemetry",
    mode=StrategyRunMode.PAPER,
    created_at=now,
    updated_at=now,
  )
  service = TTradeService()
  base = dict(
    run=run,
    run_status="RUNNING",
    error_message=None,
    params={"account_id": "account-1"},
    stock_code="600000.SH",
  )
  projected = service._project_session(
    **base,
    state={
      "monitoring_telemetry": {
        "phase": "ENTRY_SCAN",
        "last_tick_at_ms": int(now.timestamp() * 1000),
        "processed_tick_count": 7,
        "reason": "INSUFFICIENT_TICKS",
      }
    },
  )
  legacy = service._project_session(**base, state={})

  session = TTradeResolver._session_type(projected)
  legacy_session = TTradeResolver._session_type(legacy)

  assert session.latest_evaluation is not None
  assert session.latest_evaluation.processed_tick_count == 7
  assert isinstance(session.latest_evaluation.last_tick_at, datetime)
  assert legacy_session.latest_evaluation is None


@pytest.mark.asyncio
async def test_live_activation_failure_is_audited(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = SimpleNamespace(
    activate_rollout=AsyncMock(
      side_effect=ValueError("正式 LIVE 需要精确确认 LIVE:account-1")
    ),
    record_event=AsyncMock(),
  )
  monkeypatch.setattr(TTradeResolver, "operations_service", service)

  result = await TTradeResolver.activate_live(
    "account-1",
    user_id="user-1",
    policy_version=3,
    target_stage=TTradeRolloutTarget.LIVE,
    confirmation="wrong",
  )

  assert result.success is False
  assert result.code == "LIVE_NOT_READY"
  service.record_event.assert_awaited_once_with(
    "account-1",
    "LIVE_ACTIVATION_REJECTED",
    actor_user_id="user-1",
    details={
      "targetStage": "LIVE",
      "reason": "正式 LIVE 需要精确确认 LIVE:account-1",
    },
  )


@pytest.mark.asyncio
async def test_controlled_window_failure_is_audited(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = SimpleNamespace(
    begin_controlled_window=AsyncMock(
      side_effect=ValueError("当前仍有 3 笔 QMT 手工委托可能成交")
    ),
    record_event=AsyncMock(),
  )
  monkeypatch.setattr(TTradeResolver, "operations_service", service)

  result = await TTradeResolver.begin_controlled_window(
    "account-1",
    user_id="user-1",
    snapshot_id="snapshot-1",
  )

  assert result.success is False
  assert result.code == "CONTROLLED_WINDOW_NOT_READY"
  service.record_event.assert_awaited_once_with(
    "account-1",
    "CONTROLLED_WINDOW_REJECTED",
    actor_user_id="user-1",
    details={
      "snapshotId": "snapshot-1",
      "reason": "当前仍有 3 笔 QMT 手工委托可能成交",
    },
  )
