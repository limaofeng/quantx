from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_worker.prefector.flows.t_trade_instrument_profile_flow as profile_flow
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalTickPaginationError,
)
from sqlalchemy.dialects import postgresql


class _TradingTimeService:
  async def get_previous_trading_day(self, market, from_date):
    assert market == "SH"
    assert from_date == date(2026, 8, 24)
    return date(2026, 8, 21)


class _TradingDates:
  trading_time_service = _TradingTimeService()

  async def is_trading_date(self, market, target):
    assert market == "SH"
    return target.weekday() < 5


class _DbContext:
  async def __aenter__(self):
    return object()

  async def __aexit__(self, exc_type, exc, traceback):
    return False


class _ProfileQueryResult:
  def __init__(self, rows):
    self.rows = rows

  def scalars(self):
    return self

  def all(self):
    return self.rows


class _ProfileQueryContext:
  def __init__(self, rows):
    self.rows = rows
    self.statement = None

  async def __aenter__(self):
    return self

  async def __aexit__(self, exc_type, exc, traceback):
    return False

  async def execute(self, statement):
    self.statement = statement
    return _ProfileQueryResult(self.rows)


@pytest.mark.asyncio
async def test_profile_as_of_uses_latest_fully_closed_trade_day() -> None:
  before_close = await profile_flow.resolve_profile_as_of(
    reference=datetime(2026, 8, 24, 14, 59),
    trading_dates=_TradingDates(),
  )
  after_close = await profile_flow.resolve_profile_as_of(
    reference=datetime(2026, 8, 24, 15, 1),
    trading_dates=_TradingDates(),
  )

  assert before_close == datetime(2026, 8, 21, 15, 0)
  assert after_close == datetime(2026, 8, 24, 15, 0)

  with pytest.raises(ValueError, match="尚未完整收盘"):
    await profile_flow.resolve_profile_as_of(
      "2026-08-24",
      reference=datetime(2026, 8, 24, 14, 59),
      trading_dates=_TradingDates(),
    )


@pytest.mark.asyncio
async def test_profile_flow_materializes_each_explicit_instrument(monkeypatch) -> None:
  ticks = [SimpleNamespace(time=datetime(2026, 8, 21, 14, 59))]
  page_calls = []

  async def iter_tick_pages(**kwargs):
    page_calls.append(kwargs)
    yield ticks

  async def save_profile(**kwargs):
    async for _page in kwargs["pages"]:
      pass
    return object()

  save_profile_mock = AsyncMock(side_effect=save_profile)

  monkeypatch.setattr(
    profile_flow,
    "resolve_profile_as_of",
    AsyncMock(return_value=datetime(2026, 8, 21, 15, 0)),
  )
  monkeypatch.setattr(
    profile_flow,
    "HistoricalMarketDataService",
    lambda: SimpleNamespace(iter_tick_pages=iter_tick_pages),
  )
  monkeypatch.setattr(
    profile_flow,
    "TTradeInstrumentProfileService",
    lambda: SimpleNamespace(
      build_and_save_profile_from_pages=save_profile_mock
    ),
  )
  monkeypatch.setattr(profile_flow, "AsyncSessionLocal", _DbContext)
  monkeypatch.setattr(profile_flow, "TTradeInstrumentProfileRepository", lambda db: db)
  monkeypatch.setattr(
    profile_flow,
    "get_run_logger",
    lambda: SimpleNamespace(warning=lambda *args: None, exception=lambda *args: None),
  )

  result = await profile_flow.t_trade_instrument_profile_flow.fn(
    stock_list=["600000.sh", "000001.SZ"],
  )

  assert result["status"] == "success"
  assert result["saved"] == 2
  assert result["failed"] == 0
  assert len(page_calls) == 2
  assert save_profile_mock.await_count == 2
  assert {
    call.kwargs["instrument_code"] for call in save_profile_mock.await_args_list
  } == {"600000.SH", "000001.SZ"}


@pytest.mark.asyncio
async def test_profile_flow_classifies_repository_integrity_as_failed(monkeypatch) -> None:
  async def iter_tick_pages(**_kwargs):
    raise HistoricalTickPaginationError("duplicate cursor identity")
    yield []

  async def consume_pages(**kwargs):
    async for _page in kwargs["pages"]:
      pass

  save_profile = AsyncMock(side_effect=consume_pages)
  monkeypatch.setattr(
    profile_flow,
    "resolve_profile_as_of",
    AsyncMock(return_value=datetime(2026, 8, 21, 15, 0)),
  )
  monkeypatch.setattr(
    profile_flow,
    "HistoricalMarketDataService",
    lambda: type("_MarketData", (), {"iter_tick_pages": iter_tick_pages})(),
  )
  monkeypatch.setattr(
    profile_flow,
    "TTradeInstrumentProfileService",
    lambda: type(
      "_ProfileService",
      (),
      {"build_and_save_profile_from_pages": save_profile},
    )(),
  )
  monkeypatch.setattr(profile_flow, "AsyncSessionLocal", _DbContext)
  monkeypatch.setattr(profile_flow, "TTradeInstrumentProfileRepository", lambda db: db)
  monkeypatch.setattr(
    profile_flow,
    "get_run_logger",
    lambda: type(
      "_Logger",
      (),
      {
        "warning": lambda self, *args: None,
        "error": lambda self, *args: None,
        "exception": lambda self, *args: None,
      },
    )(),
  )

  result = await profile_flow.t_trade_instrument_profile_flow.fn(
    stock_list=["600000.SH"],
  )

  assert result["status"] == "failed"
  assert result["failed"] == 1
  assert result["insufficient"] == 0
  assert result["saved"] == 0
  save_profile.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("scenario", "rows", "expected"),
  [
    ("disabled", [], []),
    ("stopped", [], []),
    ("running", [["600000.SH", "000001.SZ"]], ["000001.SZ", "600000.SH"]),
  ],
)
async def test_implicit_profile_scope_requires_enabled_running_config(
  monkeypatch,
  scenario,
  rows,
  expected,
) -> None:
  db = _ProfileQueryContext(rows)
  monkeypatch.setattr(profile_flow, "AsyncSessionLocal", lambda: db)

  result = await profile_flow.resolve_profile_instruments()

  assert result == expected, scenario
  assert db.statement is not None
  compiled = db.statement.compile(
    dialect=postgresql.dialect(),
    compile_kwargs={"literal_binds": True},
  )
  sql = str(compiled).lower()
  assert "t_trade_global_configs.enabled is true" in sql
  assert "strategy_runs.status" in sql
  assert "running" in sql


def test_worker_deployment_registers_post_close_profile_flow() -> None:
  root = Path(__file__).resolve().parents[2]
  content = (root / "apps" / "worker" / "prefect.yaml").read_text(encoding="utf-8")
  assert "name: t-trade-instrument-profile" in content
  assert 'cron: "50 15 * * 1-5"' in content
