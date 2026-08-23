from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_api.gqlapi.resolvers.t_trade as resolver_module
from quantx_api.gqlapi.resolvers.t_trade import TTradeResolver
from quantx_api.gqlapi.types.t_trade_types import (
  TTradeGlobalMonitor,
  TTradeReplayStartInput,
  TTradeRolloutTarget,
  TTradeSignalPolicyInput,
  TTradeTimeExitMode,
)
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.services.engine_command_service import EngineCommandReceipt
from quantx_infrastructure.services.t_trade_service import TTradeService


def _policy_input() -> TTradeSignalPolicyInput:
  payload = OpportunityPolicy().to_dict()
  payload.pop("policy_version")
  payload.pop("feature_schema_version")
  return TTradeSignalPolicyInput(**payload)


def _policy_projection() -> dict:
  return TTradeResolver._policy_from_input(_policy_input()).to_dict()


def _signal_snapshot() -> dict:
  return {
    "instrument_code": "600000.SH",
    "trade_date": "2026-08-13",
    "evaluated_at_ms": 1_765_000_000_250,
    "source_time_ms": 1_765_000_000_000,
    "tick_ordinal": 9_007_199_254_740_993,
    "continuity_generation": 9_007_199_254_740_995,
    "data_health": "READY",
    "data_health_reasons": [],
    "features": {"sample_count": 7, "coverage_seconds": 15.4},
    "pullback": {
      "phase": "REBOUND_CONFIRMING",
      "score": 76.0,
      "preview": True,
      "candidate_ready": True,
      "components": [
        {
          "name": "PULLBACK_DEPTH",
          "raw_value": 1.1,
          "contribution": 22.0,
          "weight": 25.0,
          "detail": "",
        }
      ],
      "hard_gates": [{"code": "DATA_READY", "passed": True, "detail": ""}],
      "blockers": [],
    },
    "momentum": {
      "phase": "BASELINING",
      "score": None,
      "preview": False,
      "candidate_ready": False,
      "components": [],
      "hard_gates": [],
      "blockers": ["MOMENTUM_PATTERN_NOT_CONFIRMED"],
    },
    "selected_path": "PULLBACK_REBOUND",
    "opportunity_score": 76.0,
    "hard_gates": [{"code": "DATA_READY", "passed": True, "detail": ""}],
    "blockers": [],
    "candidate_status": "AWAITING_APPROVAL",
    "candidate_id": "candidate-1",
    "candidate_fingerprint": "fingerprint-1",
    "episode_id": "episode-1",
    "candidate_created_at_ms": 1_765_000_000_000,
    "candidate_expires_at_ms": 1_765_000_030_000,
    "preview_threshold": 55.0,
    "candidate_threshold": 72.0,
    "revalidate_threshold": 60.0,
    "rearm_threshold": 45.0,
    "signal_version": 4,
    "candidate_state_version": 4,
    "state_schema_version": "3",
    "feature_schema_version": "1",
    "policy_version": "t_trade_opportunity_v3.0.0",
    "config_version": 8,
    "profile_version": "profile-20260812",
    "profile_fingerprint": "profile-fingerprint",
  }


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
      "signal_policy": _policy_projection(),
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
  assert monitor.signal_policy.momentum_enabled is True
  assert monitor.signal_policy.momentum_window_seconds == 60
  assert monitor.signal_policy.momentum_min_amount_velocity_ratio == 2.0
  assert monitor.signal_policy.momentum_min_vwap_premium_pct == 2.0
  assert monitor.signal_policy.momentum_max_vwap_premium_pct == 3.5
  assert monitor.limit_up_touch_exit_enabled is True
  assert monitor.limit_up_touch_tolerance_ticks == 0
  assert monitor.high_profit_lock_enabled is True
  assert monitor.high_profit_arm_pct == 4.0
  assert monitor.high_profit_max_drawdown_pct == 1.2
  assert monitor.rapid_reversal_enabled is True
  assert monitor.rapid_reversal_window_seconds == 15
  assert monitor.rapid_reversal_drawdown_pct == 0.8
  assert monitor.rapid_reversal_confirm_ticks == 2


def test_global_monitor_masks_stale_ready_projection_when_qmt_launch_is_blocked(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_ENROLLMENT_REQUIRED")
  projected = TTradeResolver._apply_qmt_launch_block_to_monitor(
    {
      "agent_status": "READY",
      "can_approve": True,
      "can_activate_live": True,
      "blocked_reasons": [],
      "readiness": {
        "ready": True,
        "status": "READY",
        "preparation_ready": True,
        "automation_ready": True,
        "agent_status": "READY",
        "agent_device_id": "stale-device",
        "agent_mode": "live",
        "protocol_version": "1.1",
        "can_approve": True,
        "can_activate_live": True,
        "blocked_reasons": [],
        "preparation_blocked_reasons": [],
        "checks": [
          {
            "code": "LIVE_AGENT_READY",
            "passed": True,
            "message": "",
            "scope": "PREPARATION",
          }
        ],
      },
    }
  )

  assert projected["agent_status"] == "BLOCKED"
  assert projected["can_approve"] is False
  assert projected["can_activate_live"] is False
  readiness = projected["readiness"]
  assert readiness["status"] == "BLOCKED"
  assert readiness["ready"] is False
  assert readiness["preparation_ready"] is False
  assert readiness["automation_ready"] is False
  assert readiness["agent_status"] == "BLOCKED"
  assert readiness["agent_device_id"] is None
  assert readiness["agent_mode"] == "offline"
  assert readiness["protocol_version"] == ""
  assert readiness["can_approve"] is False
  assert readiness["can_activate_live"] is False
  assert "QMT_ENROLLMENT_REQUIRED" in readiness["blocked_reasons"][-1]
  checks = {item["code"]: item for item in readiness["checks"]}
  assert checks["LIVE_AGENT_READY"]["passed"] is False
  assert checks["QMT_AGENT_LAUNCH_ALLOWED"]["passed"] is False


def test_global_monitor_block_override_preserves_missing_nested_readiness(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_RUNTIME_UNAVAILABLE")

  projected = TTradeResolver._apply_qmt_launch_block_to_monitor(
    {
      "agent_status": "READY",
      "can_approve": True,
      "can_activate_live": True,
    }
  )

  assert projected["agent_status"] == "BLOCKED"
  assert projected["can_approve"] is False
  assert projected["can_activate_live"] is False
  assert "readiness" not in projected


def test_global_monitor_masks_projection_from_before_current_qmt_launch(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv(
    "QMT_AGENT_LAUNCH_STARTED_AT",
    "2026-08-20T04:00:00Z",
  )
  stale = {
    "agent_status": "READY",
    "can_approve": True,
    "can_activate_live": True,
    "readiness": {
      "checked_at": "2026-08-20T03:59:59Z",
      "ready": True,
      "status": "READY",
      "agent_status": "READY",
      "checks": [],
    },
  }

  projected = TTradeResolver._apply_qmt_launch_block_to_monitor(stale)

  assert projected["agent_status"] == "BLOCKED"
  assert projected["readiness"]["status"] == "BLOCKED"
  assert "QMT_LAUNCH_PENDING_CURRENT_HEARTBEAT" in projected["blocked_reasons"][-1]


def test_global_monitor_accepts_projection_from_current_qmt_launch(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv(
    "QMT_AGENT_LAUNCH_STARTED_AT",
    "2026-08-20T04:00:00Z",
  )
  current = {
    "agent_status": "READY",
    "can_approve": True,
    "can_activate_live": True,
    "readiness": {
      "checked_at": "2026-08-20T04:00:01Z",
      "ready": True,
      "status": "READY",
    },
  }

  assert TTradeResolver._apply_qmt_launch_block_to_monitor(current) is current


def test_graphql_projection_keeps_known_fields_and_drops_internal_fields():
  payload = TTradeResolver._graphql_kwargs(
    TTradeGlobalMonitor,
    {
      "max_exit_slippage_bps": 42.0,
      "future_internal_projection_field": "not public",
    },
  )

  assert payload == {"max_exit_slippage_bps": 42.0}


def test_replay_projection_exposes_generated_report_and_capital_metrics():
  replay = TTradeResolver._replay_type(
    {
      "run_id": "replay-1",
      "backtest_id": "backtest-1",
      "account_id": "account-1",
      "status": "COMPLETED",
      "progress_pct": 100.0,
      "revision": "7",
      "processed_until": "2026-08-02T15:00:00",
      "start_time": "2026-08-01T09:30:00",
      "end_time": "2026-08-02T15:00:00",
      "snapshot_id": None,
      "snapshot_date": None,
      "created_at": None,
      "updated_at": None,
      "error_message": None,
      "data_quality": "OK",
      "data_quality_message": "历史回放与期末清算完整",
      "skipped_stock_codes": [],
      "summary": {
        "initial_equity": 100_000.0,
        "final_equity": 100_100.0,
        "t_net_profit": 100.0,
        "total_return_pct": 0.1,
        "passive_final_equity": 100_000.0,
        "passive_return_pct": 0.0,
        "excess_return_pct": 0.1,
        "max_drawdown_pct": 0.0,
        "total_fees": 10.0,
        "turnover": 2_000.0,
        "completed_cycles": 1,
        "open_cycles": 0,
        "winning_cycles": 1,
        "win_rate_pct": 100.0,
        "capital_utilization_pct": 50.0,
        "forced_exit_cycles": 1,
      },
      "instruments": [],
      "curve": [],
      "report": {
        "status": "GENERATED",
        "schema_version": 1,
        "generated_at": "2026-08-02T15:01:00",
        "conclusion_code": "INSUFFICIENT_SAMPLE",
        "conclusion": "需要扩大回放区间",
        "html_artifact": "t-trade-report.html",
        "json_artifact": "t-trade-report.json",
      },
    }
  )

  assert replay.summary is not None
  assert replay.summary.capital_utilization_pct == 50.0
  assert replay.summary.forced_exit_cycles == 1
  assert replay.report is not None
  assert replay.report.generated_at == datetime(2026, 8, 2, 15, 1)
  assert replay.report.html_artifact == "t-trade-report.html"


@pytest.mark.asyncio
async def test_replay_start_processing_receipt_is_explicitly_pending(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  receipt = EngineCommandReceipt(
    message_id="00000000-0000-0000-0000-000000000123",
    command_type="T_TRADE_REPLAY_START",
    aggregate_id="account-1",
    status="PROCESSING",
  )
  request = AsyncMock(return_value=receipt)
  monkeypatch.setattr(resolver_module.engine_command_service, "request", request)
  monkeypatch.setattr(
    TTradeResolver.replay_service,
    "get",
    AsyncMock(return_value=None),
  )

  result = await TTradeResolver.start_replay(
    TTradeReplayStartInput(
      account_id="account-1",
      idempotency_key="  replay-request-1  ",
      start_time=datetime(2026, 7, 23, 9, 30),
      end_time=datetime(2026, 8, 19, 15, 0),
      signal_policy=_policy_input(),
    )
  )

  assert result.success is False
  assert result.code == "T_TRADE_REPLAY_START_COMMAND_PENDING"
  assert result.replay is None
  assert "尚不知是否已提交" in result.message
  request.assert_awaited_once()
  assert request.await_args.kwargs["idempotency_key"] == (
    resolver_module._namespaced_client_idempotency_key(
      "replay-start", "account-1", "replay-request-1"
    )
  )


@pytest.mark.asyncio
async def test_replay_start_failed_receipt_is_not_mislabeled_as_validation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    resolver_module.engine_command_service,
    "request",
    AsyncMock(
      return_value=EngineCommandReceipt(
        message_id="00000000-0000-0000-0000-000000000124",
        command_type="T_TRADE_REPLAY_START",
        aggregate_id="account-1",
        status="FAILED",
        error="历史数据服务不可用",
      )
    ),
  )

  result = await TTradeResolver.start_replay(
    TTradeReplayStartInput(
      account_id="account-1",
      idempotency_key="replay-request-2",
      start_time=datetime(2026, 7, 23, 9, 30),
      end_time=datetime(2026, 8, 19, 15, 0),
      signal_policy=_policy_input(),
    )
  )

  assert result.success is False
  assert result.code == "REPLAY_START_FAILED"
  assert result.message == "历史数据服务不可用"


@pytest.mark.asyncio
async def test_replay_start_succeeded_command_with_pending_run_is_accepted(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    resolver_module.engine_command_service,
    "request",
    AsyncMock(
      return_value=EngineCommandReceipt(
        message_id="00000000-0000-0000-0000-000000000127",
        command_type="T_TRADE_REPLAY_START",
        aggregate_id="account-1",
        status="SUCCEEDED",
        result={
          "run_id": "00000000-0000-0000-0000-000000000127",
          "backtest_id": "backtest-127",
          "account_id": "account-1",
          "status": "PENDING",
          "progress_pct": 0.0,
          "revision": "1",
          "processed_until": None,
          "start_time": "2026-07-23T09:30:00",
          "end_time": "2026-08-19T15:00:00",
          "snapshot_id": None,
          "snapshot_date": None,
          "created_at": None,
          "updated_at": None,
          "error_message": None,
          "data_quality": "PENDING",
          "data_quality_message": "正在准备历史数据",
          "skipped_stock_codes": [],
          "summary": None,
          "instruments": [],
          "curve": [],
          "report": None,
        },
      )
    ),
  )

  result = await TTradeResolver.start_replay(
    TTradeReplayStartInput(
      account_id="account-1",
      idempotency_key="replay-request-4",
      start_time=datetime(2026, 7, 23, 9, 30),
      end_time=datetime(2026, 8, 19, 15, 0),
      signal_policy=_policy_input(),
    )
  )

  assert result.success is True
  assert result.code == "REPLAY_ACCEPTED"
  assert result.replay is not None
  assert result.replay.status == "PENDING"


@pytest.mark.asyncio
async def test_replay_start_rejects_blank_idempotency_key(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = AsyncMock()
  monkeypatch.setattr(resolver_module.engine_command_service, "request", request)

  result = await TTradeResolver.start_replay(
    TTradeReplayStartInput(
      account_id="account-1",
      idempotency_key="   ",
      start_time=datetime(2026, 7, 23, 9, 30),
      end_time=datetime(2026, 8, 19, 15, 0),
      signal_policy=_policy_input(),
    )
  )

  assert result.success is False
  assert result.code == "INVALID_IDEMPOTENCY_KEY"
  request.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_start_submission_exception_is_structured(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    resolver_module.engine_command_service,
    "request",
    AsyncMock(side_effect=RuntimeError("outbox unavailable")),
  )

  result = await TTradeResolver.start_replay(
    TTradeReplayStartInput(
      account_id="account-1",
      idempotency_key="replay-request-3",
      start_time=datetime(2026, 7, 23, 9, 30),
      end_time=datetime(2026, 8, 19, 15, 0),
      signal_policy=_policy_input(),
    )
  )

  assert result.success is False
  assert result.code == "T_TRADE_REPLAY_START_OUTCOME_UNKNOWN"
  assert "尚不知是否已提交" in result.message


def test_session_graphql_projection_maps_typed_signal_snapshot_and_null():
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
    state={},
  )
  projected["signal_snapshot"] = _signal_snapshot()
  without_snapshot = service._project_session(**base, state={})

  session = TTradeResolver._session_type(projected)
  empty_session = TTradeResolver._session_type(without_snapshot)

  assert session.signal_snapshot is not None
  assert session.signal_snapshot.sample_count == 7
  assert session.signal_snapshot.source_time_ms == "1765000000000"
  assert session.signal_snapshot.tick_ordinal == "9007199254740993"
  assert session.signal_snapshot.pending_entry_intent_id is None
  assert empty_session.signal_snapshot is None


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
    snapshot_id="snapshot-1",
    target_stage=TTradeRolloutTarget.LIVE,
    confirmation="wrong",
    idempotency_key="activate-operation-1",
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
    policy_version=3,
    snapshot_id="snapshot-1",
    idempotency_key="begin-operation-1",
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


@pytest.mark.asyncio
async def test_live_activation_readback_failure_converges_from_committed_marker(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = SimpleNamespace(
    activate_rollout=AsyncMock(
      side_effect=ValueError("readiness JSON 暂时无法验证")
    ),
    operation_marker_exists=AsyncMock(return_value=True),
    record_event=AsyncMock(),
  )
  monkeypatch.setattr(TTradeResolver, "operations_service", service)

  result = await TTradeResolver.activate_live(
    "account-1",
    user_id="user-1",
    policy_version=3,
    snapshot_id="snapshot-1",
    target_stage=TTradeRolloutTarget.LIVE,
    confirmation="LIVE:account-1",
    idempotency_key="activate-operation-committed",
  )

  assert result.success is True
  assert result.code == "LIVE_ACTIVATED"
  assert result.readiness is None
  service.operation_marker_exists.assert_awaited_once()
  service.record_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_controlled_window_readback_failure_converges_from_committed_marker(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = SimpleNamespace(
    begin_controlled_window=AsyncMock(
      side_effect=ValueError("readiness JSON 暂时无法验证")
    ),
    operation_marker_exists=AsyncMock(return_value=True),
    record_event=AsyncMock(),
  )
  monkeypatch.setattr(TTradeResolver, "operations_service", service)

  result = await TTradeResolver.begin_controlled_window(
    "account-1",
    user_id="user-1",
    policy_version=3,
    snapshot_id="snapshot-1",
    idempotency_key="begin-operation-committed",
  )

  assert result.success is True
  assert result.code == "CONTROLLED_WINDOW_STARTED"
  assert result.readiness is None
  service.operation_marker_exists.assert_awaited_once()
  service.record_event.assert_not_awaited()
