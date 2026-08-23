import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_application.t_trade_v3 import normalize_signal_policy
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.services.t_trade_service import TTradeService


def signal_policy(**overrides):
  payload = OpportunityPolicy().to_dict()
  payload.update(overrides)
  return payload


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
  with pytest.raises(ValueError, match="momentum_min_move_seconds"):
    TTradeService._validate_parameters(
      {
        "signal_policy": signal_policy(
          momentum_window_seconds=30,
          momentum_min_move_seconds=31,
        )
      },
      StrategyRunMode.PAPER,
    )


def test_t_trade_momentum_vwap_band_must_be_ordered():
  with pytest.raises(ValueError, match="momentum VWAP premium band"):
    TTradeService._validate_parameters(
      {
        "signal_policy": signal_policy(
          momentum_min_vwap_premium_pct=3.5,
          momentum_max_vwap_premium_pct=3.5,
        )
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


def test_session_projection_maps_only_server_signal_snapshot():
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
    "opportunity": {
      "latest_evaluation": {
        "evaluated_at_ms": int(now.timestamp() * 1000),
        "data_health": "READY",
        "opportunity_score": 61.0,
        "features": {"session_vwap": None},
      }
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
  missing = service._project_session(
    run=run,
    run_status="RUNNING",
    error_message=None,
    params=params,
    stock_code="000001.SZ",
    state={},
  )

  assert projected["signal_snapshot"] == state["opportunity"]["latest_evaluation"]
  assert projected["signal_snapshot"]["features"]["session_vwap"] is None
  assert missing["signal_snapshot"] is None


def test_signal_policy_normalization_assigns_deterministic_version():
  first = TTradeService._normalize_signal_policy(signal_policy(candidate_score=74.0))
  second = TTradeService._normalize_signal_policy(signal_policy(candidate_score=74.0))

  assert first == second
  assert first["policy_version"].startswith("t_trade_opportunity_v3.")
  assert first["policy_version"] != "t_trade_opportunity_v3.0.0"

  version_payload = {
    key: value for key, value in first.items() if key != "policy_version"
  }
  encoded = json.dumps(
    version_payload,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  ).encode("utf-8")
  assert first["policy_version"] == (
    f"t_trade_opportunity_v3.{hashlib.sha256(encoded).hexdigest()[:12]}"
  )

  spoofed = signal_policy(candidate_score=74.0)
  spoofed["policy_version"] = "client-controlled-version"
  assert TTradeService._normalize_signal_policy(spoofed) == first


def test_signal_policy_normalization_rejects_partial_unknown_and_old_feature_schema():
  with pytest.raises(ValueError, match="signal_policy missing fields"):
    TTradeService._normalize_signal_policy({"candidate_score": 74.0})
  with pytest.raises(ValueError, match="signal_policy has unknown fields"):
    TTradeService._normalize_signal_policy({**signal_policy(), "hidden_magic": 1})
  with pytest.raises(ValueError, match="feature_schema_version is not current"):
    TTradeService._normalize_signal_policy(signal_policy(feature_schema_version=999))


@pytest.mark.parametrize(
  "payload",
  [
    None,
    signal_policy(),
    signal_policy(candidate_score=74.0, policy_version="client-version"),
  ],
)
def test_signal_policy_normalization_matches_application_canonical(payload):
  assert TTradeService._normalize_signal_policy(payload) == normalize_signal_policy(
    payload
  )


@pytest.mark.parametrize(
  "payload",
  [
    "not-a-policy",
    [],
    {"candidate_score": 74.0},
    {**signal_policy(), "hidden_magic": 1},
  ],
)
def test_signal_policy_validation_errors_match_application_canonical(payload):
  with pytest.raises(ValueError) as application_error:
    normalize_signal_policy(payload)
  with pytest.raises(type(application_error.value)) as service_error:
    TTradeService._normalize_signal_policy(payload)
  assert str(service_error.value) == str(application_error.value)


def test_policy_version_hash_covers_every_configuration_category():
  variants = [
    {"max_quote_age_ms": 3_001},
    {"pullback_min_samples": 4},
    {"pullback_required_fields": ["bid_price", "ask_price"]},
    {"allowed_session_codes": ["CONTINUOUS_AM"]},
    {"continuous_am_start_time": "09:31:00"},
    {"pullback_lookback_seconds": 301},
    {"profile_pullback_threshold_min_multiplier": 0.8},
    {"pullback_depth_weight": 24.0, "pullback_rebound_weight": 21.0},
    {"pullback_rebound_score_max_pct": 0.25},
    {"pullback_data_quality_penalty_points": 11.0},
    {"candidate_confirm_seconds": 3},
  ]
  versions = {
    TTradeService._normalize_signal_policy(signal_policy(**overrides))["policy_version"]
    for overrides in variants
  }

  assert len(versions) == len(variants)
  assert "t_trade_opportunity_v3.0.0" not in versions


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


@pytest.mark.asyncio
async def test_block_account_strategy_entries_clears_only_entry_authorization():
  runtime = SimpleNamespace(
    context=SimpleNamespace(parameters={"account_id": "account-1"}),
  )
  invalidate = AsyncMock(return_value=True)
  manager = SimpleNamespace(
    get_run=lambda _run_id: runtime,
    executor=SimpleNamespace(invalidate_t_trade_entry_authority=invalidate),
  )
  service = TTradeService(manager)

  await service.block_account_strategy_entries(
    "run-global",
    reason="CONFIG_APPLY_PENDING",
  )

  invalidate.assert_called_once_with(
    "run-global",
    account_id="account-1",
    reason="CONFIG_APPLY_PENDING",
  )


@pytest.mark.asyncio
async def test_reject_entry_captures_stock_before_terminalizing_intent():
  calls: list[tuple[str, str | None, str | None]] = []

  async def get_session(
    run_id: str,
    *,
    intent_id: str | None = None,
    stock_code: str | None = None,
  ):
    calls.append((run_id, intent_id, stock_code))
    if intent_id:
      return {"stock_code": "600000.SH", "status": "AWAITING_APPROVAL"}
    return {"stock_code": stock_code, "status": "REJECTED"}

  reject = AsyncMock(return_value={"success": True, "code": "REJECTED"})
  service = TTradeService(
    SimpleNamespace(executor=SimpleNamespace(reject_trade_intent=reject))
  )
  service.get_session = get_session

  result = await service.reject_entry("run-1", "intent-1")

  assert result["session"]["stock_code"] == "600000.SH"
  assert calls == [
    ("run-1", "intent-1", None),
    ("run-1", None, "600000.SH"),
  ]
  reject.assert_awaited_once_with("run-1", "intent-1", reason="USER_REJECTED")


@pytest.mark.asyncio
async def test_update_account_strategy_forwards_explicit_configuration_change():
  manager = SimpleNamespace(
    reconcile_run_instruments=AsyncMock(
      return_value={"added": [], "removed": [], "instruments": ["600000.SH"]}
    )
  )
  service = TTradeService(manager)

  await service.update_account_strategy(
    "run-global",
    {},
    ["600000.SH"],
    {},
    configuration_changed=False,
  )

  manager.reconcile_run_instruments.assert_awaited_once()
  assert (
    manager.reconcile_run_instruments.await_args.kwargs["configuration_changed"]
    is False
  )
