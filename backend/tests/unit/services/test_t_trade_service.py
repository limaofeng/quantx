import pytest

from models.enums import StrategyRunMode
from services.t_trade_service import TTradeService


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
