import asyncio
import importlib
from unittest.mock import AsyncMock, Mock

from quantx_worker.prefector.flows import market_sync_observation as progress
from quantx_worker.prefector.flows.market_data_sync_partitions import (
  iter_market_partitions,
)

from tests.worker.market_sync_helpers import trading_days

flow = importlib.import_module(
  "quantx_worker.prefector.flows.daily_market_data_sync_flow"
)


async def test_heartbeat_runs_while_no_batch_completes(monkeypatch):
  monkeypatch.setattr(progress, "PROGRESS_INTERVAL_SECONDS", 0.01)
  logger = Mock()
  async with progress.observe_market_sync(logger):
    progress.report_request("request", "排队", "等待其他历史请求")
    await asyncio.sleep(0.045)
  calls = [call for call in logger.info.call_args_list if "心跳" in call.args[0]]
  assert len(calls) >= 3
  assert progress.observation.get() is None
  count = logger.info.call_count
  await asyncio.sleep(0.02)
  assert logger.info.call_count == count


async def test_retry_remains_inside_one_observed_flow(monkeypatch):
  logger = Mock()
  monkeypatch.setattr(flow, "get_run_logger", lambda: logger)
  monkeypatch.setattr(flow, "MARKET_DATA_RETRY_DELAY_SECONDS", 0.025)
  monkeypatch.setattr(progress, "PROGRESS_INTERVAL_SECONDS", 0.01)
  attempt = AsyncMock(side_effect=[RuntimeError("temporary"), {"status": "success"}])
  monkeypatch.setattr(flow, "_daily_market_data_sync_attempt", attempt)
  result = await flow.daily_market_data_sync_flow.fn(idempotency_scope="stable")
  assert result["status"] == "success"
  assert flow.daily_market_data_sync_flow.retries == 0
  assert attempt.await_count == 2
  assert {call.kwargs["idempotency_scope"] for call in attempt.await_args_list} == {
    "stable"
  }
  assert any("等待内部重试" in str(call) for call in logger.info.call_args_list)


def test_tick_groups_ten_and_other_periods_are_independent():
  codes = [f"{i:06d}.SZ" for i in range(11)]
  days = trading_days("20260801", "20260831")
  plans = list(
    iter_market_partitions(
      codes, days, "20260801", "20260831", ["tick", "1m", "1d"], {}
    )
  )
  assert len([p for p in plans if p.periods == ["tick"]]) == 42
  assert len([p for p in plans if p.periods == ["1m"]]) == 1
  assert len([p for p in plans if p.periods == ["1d"]]) == 1
  assert max(len(p.codes) for p in plans if p.periods == ["tick"]) == 10


def test_failure_summary_is_bounded():
  error = flow.MarketDataSyncIncomplete(
    [{"batch_index": i, "reason": "missing"} for i in range(1000)], 1000, 1000
  )
  assert len(error.failures) == 10
  assert error.failed_count == 1000
  assert "1000/1000" in str(error)


async def test_pipeline_releases_full_audits_after_each_batch(monkeypatch):
  import gc
  import weakref

  from tests.worker.market_sync_helpers import completed_transfer

  references = []
  peak = 0

  class Transfer(dict):
    pass

  async def request(payload, **kwargs):
    nonlocal peak
    result = Transfer(completed_transfer(payload, str(len(references))))
    result["unused_large_audit"] = bytearray(1024 * 1024)
    references.append(weakref.ref(result))
    peak = max(peak, sum(ref() is not None for ref in references))
    return result

  days = trading_days("20260801", "20260831")
  monkeypatch.setattr(
    flow,
    "TradingDateHelper",
    lambda: Mock(get_trading_calendar=AsyncMock(return_value=days)),
  )
  monkeypatch.setattr(flow, "_request_and_wait", request)
  result = await flow._request_market_data_batches(
    codes=[f"{i:06d}.SZ" for i in range(11)],
    periods=["tick"],
    start_time="20260801",
    end_time="20260831",
    agent_device_id="",
    idempotency_scope="memory",
    logger=Mock(),
    lifetimes={},
  )
  gc.collect()
  assert result["batch_count"] == 42
  assert peak <= 2
  assert all(ref() is None for ref in references)
  assert "batches" not in result and "request_ids" not in result


async def test_indicator_phase_is_visible_when_download_is_skipped(monkeypatch):
  monkeypatch.setattr(flow, "get_run_logger", Mock)
  monkeypatch.setattr(flow, "resolve_instruments", AsyncMock(return_value=[{"code":"000001.SZ"}]))
  phases = []
  async def indicator(**kwargs):
    phases.append(progress.observation.get().phase)
    return {"status":"success", "dates":[]}
  monkeypatch.setattr(flow, "daily_indicator_snapshot_flow", indicator)
  await flow.daily_market_data_sync_flow.fn(start_time="20260803", end_time="20260803",
    periods=["1d"], skip_download=True, compute_daily_signals=True)
  assert phases == ["日级指标计算"]


async def test_nested_durable_requests_do_not_accumulate_in_observer(monkeypatch):
  from quantx_worker.prefector.flows import durable_agent_flows as durable
  store = Mock(create_market_data_request=AsyncMock(return_value="request"),
    market_data_request=AsyncMock(return_value={"status":"COMPLETED", "ingestion_result":{"records_saved":1}}),
    close=AsyncMock())
  monkeypatch.setattr(durable, "LocalMarketDataClient", lambda: store)
  async with progress.observe_market_sync(Mock()) as observer:
    result = await durable._request_and_wait({"operation":"bars"})
    assert result["status"] == "completed"
    assert observer.requests == {}


def test_development_long_range_partitions_fit_delivery_capacity_without_gaps():
  from datetime import datetime

  from quantx_contracts.data_exchange import MAX_REMOTE_HISTORY_PARTITIONS

  codes = [f"{i:06d}.SZ" for i in range(300)]
  days = trading_days("20260101", "20260331")
  plans = list(
    iter_market_partitions(
      codes,
      days,
      "20260101",
      "20260331",
      ["1d", "1m"],
      {},
      max_delivery_partitions=MAX_REMOTE_HISTORY_PARTITIONS,
    )
  )
  for plan in plans:
    span = (
      datetime.strptime(plan.end, "%Y%m%d") - datetime.strptime(plan.start, "%Y%m%d")
    ).days + 1
    assert span * len(plan.codes) * len(plan.periods) <= MAX_REMOTE_HISTORY_PARTITIONS
  actual = [
    (code, period, day)
    for plan in plans
    for code in plan.codes
    for period in plan.periods
    for day in plan.days
  ]
  expected = {
    (code, period, day) for code in codes for period in ["1d", "1m"] for day in days
  }
  assert len(actual) == len(set(actual)) and set(actual) == expected
  production = list(
    iter_market_partitions(codes, days, "20260101", "20260331", ["1d"], {})
  )
  assert len(production) == 1 and production[0].codes == codes
