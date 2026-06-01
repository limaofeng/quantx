"""
CandidatePool and TradingDateHelper unit tests.
"""

from datetime import date
import asyncio
from importlib import util
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

ROOT_DIR = Path(__file__).resolve().parents[4]


class DummyHolidayService:
  async def is_holiday(self, market, check_date):
    return False


services_stub = types.ModuleType("services")
services_stub.__path__ = [str(ROOT_DIR / "services")]
holiday_stub = types.ModuleType("services.holiday_service")
holiday_stub.HolidayService = DummyHolidayService
services_stub.holiday_service = holiday_stub
sys.modules.setdefault("services", services_stub)
sys.modules.setdefault("services.holiday_service", holiday_stub)

UNIVERSE_PATH = ROOT_DIR / "core" / "strategies" / "universe.py"

TRADING_TIME_PATH = ROOT_DIR / "services" / "trading_time_service.py"

def load_universe_module():
  if "core/strategies/universe" in sys.modules:
    return sys.modules["core/strategies/universe"]

  core_stub = types.ModuleType("core")
  core_stub.__path__ = [str(ROOT_DIR / "core")]
  strategies_stub = types.ModuleType("core.strategies")
  strategies_stub.__path__ = [str(ROOT_DIR / "core" / "strategies")]
  core_stub.strategies = strategies_stub
  sys.modules.setdefault("core", core_stub)
  sys.modules.setdefault("core.strategies", strategies_stub)

  module_name = "core/strategies/universe"
  spec = util.spec_from_file_location(module_name, UNIVERSE_PATH)
  module = util.module_from_spec(spec)
  sys.modules[module_name] = module
  sys.modules.setdefault("core.strategies.universe", module)
  spec.loader.exec_module(module)
  return module


def load_trading_time_module():
  if "trading_time_service" in sys.modules:
    return sys.modules["trading_time_service"]

  spec = util.spec_from_file_location("trading_time_service", TRADING_TIME_PATH)
  module = util.module_from_spec(spec)
  sys.modules["trading_time_service"] = module
  spec.loader.exec_module(module)
  return module

pytestmark = pytest.mark.unit


class TestCandidatePool:
  """CandidatePool tests."""

  @pytest.fixture
  def pool(self):
    return load_universe_module().CandidatePool()

  def test_apply_hard_filters(self, pool):
    universe = pd.DataFrame(
      [
        {"code": "000001", "name": "STAlpha", "turnover": 80_000_000},
        {
          "code": "000002",
          "name": "Beta",
          "turnover": 80_000_000,
          "trading_status": "suspended",
        },
        {"code": "000003", "name": "Gamma", "turnover": 20_000_000},
        {"code": "000004", "name": "Delta", "turnover": 80_000_000},
      ]
    )

    filtered = pool.apply_hard_filters(universe)

    assert list(filtered["code"]) == ["000004"]

  def test_apply_hard_filters_suspend_flag(self, pool):
    universe = pd.DataFrame(
      [
        {"code": "000010", "name": "Alpha", "turnover": 80_000_000, "suspend_flag": 1},
        {"code": "000011", "name": "Beta", "turnover": 80_000_000, "suspend_flag": 0},
      ]
    )

    filtered = pool.apply_hard_filters(universe)

    assert list(filtered["code"]) == ["000011"]

  def test_apply_hard_filters_empty(self, pool):
    filtered = pool.apply_hard_filters([])
    assert filtered.empty

  def test_is_sideways(self, pool):
    low_vol = [10, 10.02, 10.01, 9.99, 10.0, 10.01]
    high_vol = [10, 12, 9, 13, 8, 14]

    assert pool.is_sideways(low_vol)
    assert not pool.is_sideways(high_vol)

  def test_is_sideways_insufficient(self, pool):
    assert not pool.is_sideways([10, 10.01])
    assert not pool.is_sideways(None)

  def test_is_ma_converging(self, pool):
    stable_prices = [10.0] * 30
    trending_prices = list(range(1, 31))

    assert pool.is_ma_converging(stable_prices)
    assert not pool.is_ma_converging(trending_prices)

  def test_detect_box(self, pool):
    prices = [9.8, 10.2, 10.0, 10.5, 9.9]
    box = pool.detect_box(prices, window=5)

    assert box["is_valid"] is True
    assert box["support"] == pytest.approx(9.8)
    assert box["resistance"] == pytest.approx(10.5)
    assert box["width"] == pytest.approx((10.5 - 9.8) / 9.8, rel=1e-6)

  def test_detect_box_insufficient(self, pool):
    box = pool.detect_box([])
    assert box["is_valid"] is False
    assert box["support"] is None

  def test_build_candidates(self, pool):
    universe = [
      {"stock_code": "000020", "name": "Alpha", "turnover": 80_000_000},
      {"stock_code": "000021", "name": "Beta", "turnover": 80_000_000},
    ]
    price_map = {
      "000020": [10.0] * 30,
      "000021": list(range(1, 31)),
    }

    candidates = pool.build_candidates(universe, price_map)

    assert candidates.loc[candidates["code"] == "000020", "structure_ok"].iloc[0]
    assert not candidates.loc[candidates["code"] == "000021", "structure_ok"].iloc[0]


class TestTradingDateHelper:
  """TradingDateHelper tests."""

  def test_is_trading_date(self):
    helper = load_trading_time_module().TradingDateHelper()
    with patch.object(
      helper.trading_time_service, "is_trading_day", new=AsyncMock(return_value=True)
    ) as mock_is_trading_day:
      result = asyncio.run(helper.is_trading_date("SH", date(2024, 1, 2)))

    assert result is True
    mock_is_trading_day.assert_called_once()

  def test_get_next_trading_date(self):
    helper = load_trading_time_module().TradingDateHelper()
    expected_date = date(2024, 1, 3)
    with patch.object(
      helper.trading_time_service,
      "get_next_trading_day",
      new=AsyncMock(return_value=expected_date),
    ) as mock_get_next:
      result = asyncio.run(
        helper.get_next_trading_date("SH", date(2024, 1, 2))
      )

    assert result == expected_date
    mock_get_next.assert_called_once()

  def test_get_trading_calendar(self):
    helper = load_trading_time_module().TradingDateHelper()
    trading_days = {date(2024, 1, 2), date(2024, 1, 4)}

    async def mock_is_trading_date(market, check_date):
      return check_date in trading_days

    with patch.object(
      helper, "is_trading_date", new=AsyncMock(side_effect=mock_is_trading_date)
    ):
      result = asyncio.run(
        helper.get_trading_calendar("SH", date(2024, 1, 1), date(2024, 1, 4))
      )

    assert result == [date(2024, 1, 2), date(2024, 1, 4)]

  def test_get_trading_calendar_invalid_range(self):
    helper = load_trading_time_module().TradingDateHelper()
    result = asyncio.run(
      helper.get_trading_calendar("SH", date(2024, 1, 5), date(2024, 1, 4))
    )
    assert result == []
