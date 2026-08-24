from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
import quantx_worker.prefector.flows.core_index_intraday_repair_flow as repair_flow
from quantx_infrastructure.models.kline import KLine


class FakeLogger:
  def info(self, *args, **kwargs):
    return None

  def warning(self, *args, **kwargs):
    return None

  def error(self, *args, **kwargs):
    return None

  def exception(self, *args, **kwargs):
    return None


class FakeTradingTimeService:
  async def get_previous_trading_day(self, market, from_date):
    assert market == "SH"
    current = from_date - timedelta(days=1)
    while current.weekday() >= 5:
      current -= timedelta(days=1)
    return current


class FakeTradingDates:
  def __init__(self):
    self.trading_time_service = FakeTradingTimeService()

  async def is_trading_date(self, market, check_date):
    assert market == "SH"
    return check_date.weekday() < 5


def _bar(code: str, timestamp: datetime, *, close: float = 10.0) -> KLine:
  return KLine(
    stock_code=code,
    period="1m",
    time=timestamp,
    open=close,
    high=close + 0.1,
    low=close - 0.1,
    close=close,
    pre_close=close,
    volume=100.0,
    amount=1_000.0,
    settelement_price=0.0,
    open_interest=0,
    suspend_flag=0,
  )


def _complete_bars(code: str, target: date) -> list[KLine]:
  return [
    _bar(code, datetime(target.year, target.month, target.day, hour, minute))
    for hour, minute in repair_flow.REQUIRED_MINUTE_SLOTS
  ]


def _audit(*, complete: bool, incomplete_codes=None):
  codes = list(incomplete_codes or [])
  return {
    "target_date": "2026-08-17",
    "complete": complete,
    "expected_minutes_per_code": 241,
    "incomplete_codes": codes,
    "codes": {},
  }


def test_required_index_minutes_match_qmt_continuous_session():
  assert len(repair_flow.REQUIRED_MINUTE_SLOTS) == 241
  assert (9, 30) in repair_flow.REQUIRED_MINUTE_SLOTS
  assert (11, 30) in repair_flow.REQUIRED_MINUTE_SLOTS
  assert (13, 0) not in repair_flow.REQUIRED_MINUTE_SLOTS
  assert (13, 1) in repair_flow.REQUIRED_MINUTE_SLOTS
  assert (15, 0) in repair_flow.REQUIRED_MINUTE_SLOTS


def test_intraday_coverage_accepts_complete_session_and_optional_rows():
  target = date(2026, 8, 17)
  code = "000001.SH"
  records = _complete_bars(code, target)
  records.extend(
    [
      _bar(code, datetime(2026, 8, 17, 9, 25)),
      _bar(code, datetime(2026, 8, 17, 13, 0)),
    ]
  )

  result = repair_flow.assess_intraday_coverage(
    records,
    target_date=target,
    stock_codes=[code],
  )

  assert result["complete"] is True
  assert result["incomplete_codes"] == []
  assert result["codes"][code]["row_count"] == 243
  assert result["codes"][code]["valid_required_minutes"] == 241


def test_intraday_coverage_rejects_single_close_point_and_invalid_bar():
  target = date(2026, 8, 17)
  code = "000001.SH"
  close_only = [_bar(code, datetime(2026, 8, 17, 15, 0))]

  close_result = repair_flow.assess_intraday_coverage(
    close_only,
    target_date=target,
    stock_codes=[code],
  )
  assert close_result["complete"] is False
  assert close_result["codes"][code]["missing_minutes"] == 240

  invalid_records = _complete_bars(code, target)
  invalid_records = [
    (
      _bar(code, item.time, close=-1.0)
      if (item.time.hour, item.time.minute) == (10, 0)
      else item
    )
    for item in invalid_records
  ]
  invalid_result = repair_flow.assess_intraday_coverage(
    invalid_records,
    target_date=target,
    stock_codes=[code],
  )
  assert invalid_result["complete"] is False
  assert invalid_result["codes"][code]["missing_minutes"] == 1
  assert invalid_result["codes"][code]["invalid_required_rows"] == 1


@pytest.mark.asyncio
async def test_repair_dates_switch_after_close_and_include_lookback():
  helper = FakeTradingDates()

  before_close = await repair_flow.resolve_repair_dates(
    lookback_trading_days=2,
    reference=datetime(2026, 8, 17, 15, 9, 59),
    trading_dates=helper,
  )
  after_close = await repair_flow.resolve_repair_dates(
    lookback_trading_days=2,
    reference=datetime(2026, 8, 17, 15, 10),
    trading_dates=helper,
  )

  assert before_close == [date(2026, 8, 13), date(2026, 8, 14)]
  assert after_close == [date(2026, 8, 14), date(2026, 8, 17)]


@pytest.mark.asyncio
async def test_repair_flow_downloads_only_incomplete_codes_and_rechecks(
  monkeypatch,
):
  target = date(2026, 8, 17)
  before = _audit(complete=False, incomplete_codes=["000001.SH"])
  after = _audit(complete=True)
  audit = Mock(side_effect=[before, after])
  request = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "repair-request-1",
      "records_received": 241,
      "records_saved": 241,
    }
  )
  monkeypatch.setattr(
    repair_flow,
    "resolve_repair_dates",
    AsyncMock(return_value=[target]),
  )
  monkeypatch.setattr(repair_flow, "audit_core_index_intraday", audit)
  monkeypatch.setattr(repair_flow, "_request_and_wait", request)
  monkeypatch.setattr(repair_flow, "get_run_logger", FakeLogger)
  monkeypatch.setattr(
    repair_flow.flow_run_runtime,
    "get_id",
    lambda: "repair-flow-run-1",
  )
  monkeypatch.setattr(
    repair_flow,
    "_scheduled_start_time",
    lambda: datetime(2026, 8, 18, 8, 50),
  )
  monkeypatch.setattr(
    repair_flow.time_utils,
    "now",
    lambda: datetime(2026, 8, 18, 8, 51),
  )

  result = await repair_flow.core_index_intraday_repair_flow.fn(target_date="20260817")

  assert result["status"] == "success"
  assert result["dates"][0]["status"] == "repaired"
  assert audit.call_count == 2
  payload = request.await_args.args[0]
  assert payload == {
    "operation": "bars",
    "download": True,
    "stock_list": ["000001.SH"],
    "periods": ["1m"],
    "start_time": "20260817",
    "end_time": "20260817",
  }
  assert request.await_args.kwargs["idempotency_scope"] == (
    "core-index-intraday-repair:v2:2026-08-17:run:repair-flow-run-1"
  )


@pytest.mark.asyncio
async def test_repair_flow_skips_download_when_history_is_complete(monkeypatch):
  target = date(2026, 8, 17)
  request = AsyncMock()
  monkeypatch.setattr(
    repair_flow,
    "resolve_repair_dates",
    AsyncMock(return_value=[target]),
  )
  monkeypatch.setattr(
    repair_flow,
    "audit_core_index_intraday",
    Mock(return_value=_audit(complete=True)),
  )
  monkeypatch.setattr(repair_flow, "_request_and_wait", request)
  monkeypatch.setattr(repair_flow, "get_run_logger", FakeLogger)
  monkeypatch.setattr(
    repair_flow,
    "_scheduled_start_time",
    lambda: datetime(2026, 8, 18, 8, 50),
  )

  result = await repair_flow.core_index_intraday_repair_flow.fn()

  assert result["dates"][0]["status"] == "complete"
  request.assert_not_awaited()
