from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_infrastructure.services.t_trade_service as t_trade_service_module
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.services.t_trade_service import TTradeService


def test_t_trade_parameters_accept_safe_defaults():
  TTradeService._validate_parameters({}, StrategyRunMode.PAPER)


def test_t_trade_parameters_require_floor_below_target():
  with pytest.raises(ValueError, match="初始保护线必须低于止盈武装线"):
    TTradeService._validate_parameters(
      {"target_profit_pct": 2.0, "base_floor_pct": 2.0},
      StrategyRunMode.PAPER,
    )


def test_t_trade_amount_hard_cap_must_cover_target():
  with pytest.raises(ValueError, match="硬上限不能低于目标"):
    TTradeService._validate_parameters(
      {"target_trade_amount": 10_000, "max_trade_amount": 9_000},
      StrategyRunMode.PAPER,
    )


def test_t_trade_momentum_window_must_cover_minimum_move():
  with pytest.raises(ValueError, match="动量最短持续时间不能超过动量观察窗口"):
    TTradeService._validate_parameters(
      {
        "momentum_window_seconds": 30,
        "momentum_min_move_seconds": 31,
      },
      StrategyRunMode.PAPER,
    )


def test_t_trade_momentum_vwap_band_must_be_ordered():
  with pytest.raises(ValueError, match="动量 VWAP 最小溢价必须低于最大溢价"):
    TTradeService._validate_parameters(
      {
        "momentum_min_vwap_premium_pct": 3.5,
        "momentum_max_vwap_premium_pct": 3.5,
      },
      StrategyRunMode.PAPER,
    )


def test_t_trade_high_profit_arm_must_exceed_base_arm():
  with pytest.raises(ValueError, match="高利润保护武装线必须高于"):
    TTradeService._validate_parameters(
      {
        "target_profit_pct": 4.0,
        "high_profit_arm_pct": 4.0,
      },
      StrategyRunMode.PAPER,
    )


def test_t_trade_high_profit_drawdown_must_stay_below_arm():
  with pytest.raises(ValueError, match="高利润最大回吐必须低于"):
    TTradeService._validate_parameters(
      {
        "high_profit_arm_pct": 4.0,
        "high_profit_max_drawdown_pct": 4.0,
      },
      StrategyRunMode.PAPER,
    )


def test_live_t_trade_accepts_unlimited_protection():
  TTradeService._validate_parameters(
    {"time_exit_mode": "UNLIMITED", "hard_stop_enabled": False},
    StrategyRunMode.LIVE,
  )


def test_live_time_exit_requires_safe_afternoon_time():
  with pytest.raises(ValueError, match="14:30 到 14:57"):
    TTradeService._validate_parameters(
      {"time_exit_mode": "END_OF_DAY", "time_exit_time": "10:00"},
      StrategyRunMode.LIVE,
    )


def test_hard_stop_is_validated_only_when_enabled():
  TTradeService._validate_parameters(
    {"hard_stop_enabled": False, "hard_stop_pct": 1.0},
    StrategyRunMode.PAPER,
  )
  with pytest.raises(ValueError, match="大于 -10 且小于 0"):
    TTradeService._validate_parameters(
      {"hard_stop_enabled": True, "hard_stop_pct": 0.0},
      StrategyRunMode.PAPER,
    )


def test_legacy_exit_parameters_are_normalized():
  normalized = TTradeService._normalize_exit_settings(
    {
      "flatten_end_of_day": True,
      "end_of_day_exit_time": "14:48",
      "hard_stop_pct": -1.0,
    }
  )
  assert normalized["time_exit_mode"] == "END_OF_DAY"
  assert normalized["time_exit_time"] == "14:48"
  assert normalized["hard_stop_enabled"] is True


def test_t_trade_mapping_decodes_persisted_json_parameters():
  assert TTradeService._mapping(
    '{"account_id":"300000013250","target_trade_amount":10000}'
  ) == {
    "account_id": "300000013250",
    "target_trade_amount": 10000,
  }


def test_t_trade_mapping_rejects_non_object_json():
  assert TTradeService._mapping('["not", "an", "object"]') == {}


def test_session_projection_maps_monitoring_telemetry_and_keeps_legacy_null():
  now = datetime(2026, 8, 13, 10, 5, tzinfo=timezone.utc)
  run = SimpleNamespace(
    id="run-telemetry",
    mode=StrategyRunMode.PAPER,
    created_at=now,
    updated_at=now,
  )
  service = TTradeService()
  params = {"account_id": "account-1"}
  state = {
    "monitoring_telemetry": {
      "phase": "ENTRY_SCAN",
      "last_tick_at_ms": int(now.timestamp() * 1000),
      "processed_tick_count": 123,
      "window_sample_count": 45,
      "window_coverage_seconds": 119.0,
      "triggered": False,
      "reason": "WAITING_PULLBACK",
      "signal_type": "NONE",
      "signal_price": 10.12,
      "window_high": 10.2,
      "pullback_pct": 0.4,
      "vwap": None,
    }
  }

  projected = service._project_session(
    run=run,
    run_status="RUNNING",
    error_message=None,
    params=params,
    stock_code="600000.SH",
    state=state,
  )
  legacy = service._project_session(
    run=run,
    run_status="RUNNING",
    error_message=None,
    params=params,
    stock_code="000001.SZ",
    state={},
  )

  assert projected["latest_evaluation"]["processed_tick_count"] == 123
  assert projected["latest_evaluation"]["vwap"] is None
  assert projected["latest_evaluation"]["last_tick_at"].timestamp() == pytest.approx(
    now.timestamp()
  )
  assert legacy["latest_evaluation"] is None


@pytest.mark.asyncio
async def test_ensure_account_strategy_running_starts_restored_idle_runtime():
  runtime = SimpleNamespace(
    status=SimpleNamespace(value="PENDING"),
    task=None,
  )
  manager = SimpleNamespace(
    get_run=lambda _run_id: runtime,
    start_strategy=AsyncMock(return_value=True),
    resume_strategy=AsyncMock(),
  )
  service = TTradeService(manager)

  changed = await service.ensure_account_strategy_running("run-restored")

  assert changed is True
  manager.start_strategy.assert_awaited_once_with("run-restored")
  manager.resume_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_account_strategy_running_resumes_live_paused_runtime():
  runtime = SimpleNamespace(
    status=SimpleNamespace(value="PAUSED"),
    task=SimpleNamespace(done=lambda: False),
  )
  manager = SimpleNamespace(
    get_run=lambda _run_id: runtime,
    start_strategy=AsyncMock(),
    resume_strategy=AsyncMock(return_value=True),
  )
  service = TTradeService(manager)

  changed = await service.ensure_account_strategy_running("run-paused")

  assert changed is True
  manager.resume_strategy.assert_awaited_once_with("run-paused")
  manager.start_strategy.assert_not_awaited()


def test_signal_history_projection_keeps_expiry_and_audit_reason():
  created_at = datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc)
  record = SimpleNamespace(
    id="intent-1",
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    status="EXPIRED",
    notes="APPROVAL_TTL_EXPIRED",
    limit_price_hint=10.12,
    target_volume=900,
    created_at=created_at,
    updated_at=datetime(2026, 7, 22, 9, 30, 31, tzinfo=timezone.utc),
    intent_metadata={
      "approval_ttl_ms": 30_000,
      "intent_created_at": created_at.isoformat(),
      "requested_entry_volume": 800,
      "signal": {
        "signal_price": 10.1,
        "pullback_pct": 0.92,
        "rebound_pct": 0.24,
      },
    },
  )

  result = TTradeService._project_signal_history_entry(record)

  assert result["status"] == "EXPIRED"
  assert result["status_reason"] == "APPROVAL_TTL_EXPIRED"
  assert result["signal_price"] == 10.1
  assert result["requested_volume"] == 800
  assert (result["expires_at"] - result["created_at"]).total_seconds() == 30


@pytest.mark.asyncio
async def test_signal_history_is_scoped_to_account_across_runs(monkeypatch):
  runs = [
    SimpleNamespace(
      id="run-1",
      strategy=SimpleNamespace(class_name="AshareIntradayTAssistantStrategy"),
      parameters={"account_id": "account-1"},
    ),
    SimpleNamespace(
      id="run-2",
      strategy=SimpleNamespace(class_name="AshareIntradayTAssistantStrategy"),
      parameters='{"account_id":"account-1"}',
    ),
    SimpleNamespace(
      id="run-other",
      strategy=SimpleNamespace(class_name="AshareIntradayTAssistantStrategy"),
      parameters={"account_id": "account-2"},
    ),
  ]
  run_repo = SimpleNamespace(find_all_strategy_runs=AsyncMock(return_value=runs))
  intent_repo = SimpleNamespace(find_recent_t_trade_entries=AsyncMock(return_value=[]))

  async def fake_db():
    yield object()

  monkeypatch.setattr(t_trade_service_module, "get_async_db", fake_db)
  monkeypatch.setattr(
    t_trade_service_module, "StrategyRunRepository", lambda _db: run_repo
  )
  monkeypatch.setattr(
    t_trade_service_module, "TradeIntentRepository", lambda _db: intent_repo
  )

  result = await TTradeService().list_signal_history("account-1", limit=25)

  assert result == []
  intent_repo.find_recent_t_trade_entries.assert_awaited_once_with(
    ["run-1", "run-2"], 25
  )
