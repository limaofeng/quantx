"""Synthetic engineering fixtures, never strategy admission evidence."""

import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from quantx_engine.t_assistant_backtest_data import (
  BacktestDataset,
  acquire_backtest_dataset,
)
from quantx_engine.t_assistant_backtest_evaluation import (
  BacktestAdmissionPolicy,
  evaluate_backtest_comparison,
  read_backtest_evaluation_evidence,
  summarize_persisted_backtest,
)

from tests.engine.unit.test_t_assistant_backtest_runtime import CODES, runtime, ticks


class Calendar:
  async def get_trading_calendar(self, **kwargs):
    assert kwargs["start_date"] == kwargs["end_date"] == date(2026, 9, 3)
    return [date(2026, 9, 3)]


class History:
  def __init__(self, *, broken=False):
    self.broken = broken
    self.calls = []

  async def get_kline_data(self, **kwargs):
    return [
      SimpleNamespace(
        time=kwargs["start_time"], up_stop_price=110.0, down_stop_price=90.0
      )
    ]

  async def iter_tick_pages(self, **kwargs):
    self.calls.append(kwargs)
    rows = []
    for item in ticks():
      if item.market.instrument_code != kwargs["stock_code"]:
        continue
      sample, market = item.tick.sample, item.market
      rows.append(
        SimpleNamespace(
          stock_code=market.instrument_code,
          source_time_ms=sample.source_time_ms,
          tick_ordinal=sample.tick_ordinal,
          last_price=sample.price,
          price_tick=0.01,
          bid_price=market.bid_price,
          ask_price=market.ask_price,
          bid_vol=market.bid_vol,
          ask_vol=[] if self.broken else market.ask_vol,
          amount=sample.cumulative_amount,
          volume=sample.cumulative_volume / 100,
          pvolume=sample.cumulative_volume,
          stock_status=0,
          up_stop_price=110.0,
          down_stop_price=90.0,
        )
      )
    yield rows[:5]
    yield rows[5:]


async def acquire(tmp_path, history):
  return await acquire_backtest_dataset(
    history=history,
    calendar=Calendar(),
    source_version="isolated-test-source-v1",
    instruments=CODES,
    start=date(2026, 9, 3),
    end=date(2026, 9, 3),
    root=tmp_path,
    latency_ms=0,
    freeze=True,
  )


async def test_acquisition_to_real_rule_only_shared_runtime(tmp_path):
  history = History()
  dataset = await acquire(tmp_path, history)
  assert len(history.calls) == 2
  assert dataset.manifest["material"]["status"] == "FROZEN"
  events = [event async for event in dataset.events()]
  assert len(events) == len(ticks())
  run = runtime()
  await run.run(events)
  assert len(run.broker.orders) == 4
  assert all(p.remaining_volume == 0 for p in run.plans.plans.values())
  part = (
    dataset.directory.parent
    / "objects"
    / (dataset.manifest["material"]["parts"][0]["hash"] + ".json")
  )
  part.write_text(json.dumps({"rows": []}), encoding="utf-8")
  with pytest.raises(ValueError, match="PART_CORRUPT"):
    [event async for event in BacktestDataset(dataset.directory).events()]


async def test_missing_depth_retains_failure_and_cannot_replay(tmp_path):
  dataset = await acquire(tmp_path, History(broken=True))
  assert dataset.manifest["material"]["status"] == "INCOMPLETE"
  assert len(dataset.manifest["material"]["failures"]) == 2
  with pytest.raises(ValueError, match="ACQUISITION_INCOMPLETE"):
    [event async for event in dataset.events()]


async def test_source_failure_preserves_partial_data_without_connection_details(
  tmp_path,
):
  class SourceFailure(Exception):
    pass

  class FailingHistory(History):
    async def iter_tick_pages(self, **kwargs):
      async for page in super().iter_tick_pages(**kwargs):
        yield page
        raise SourceFailure("sensitive-connection-detail")

  dataset = await acquire(tmp_path, FailingHistory())
  material = dataset.manifest["material"]
  assert material["status"] == "INCOMPLETE"
  assert all(p["count"] == 5 and not p["source_exhausted"] for p in material["parts"])
  assert all(f["reason"] == "SourceFailure" for f in material["failures"])
  assert "sensitive-connection-detail" not in json.dumps(dataset.manifest)


async def test_counterfactual_cash_and_unconfirmed_policy(tmp_path):
  dataset = await acquire(tmp_path / "data", History())
  directory, report = await evaluate_backtest_comparison(
    request=runtime(request_only=True),
    events=dataset,
    scenarios={"zero": 0.0, "stress": 0.0005},
    code_manifest={"synthetic": "v1"},
    root=tmp_path / "evaluation",
  )
  exported = json.loads((directory / "report.json").read_text())
  assert (
    read_backtest_evaluation_evidence(directory, expected_report_hash=exported["hash"])[
      "report"
    ]
    == exported
  )
  assert report["strategy_admission"] == "NOT_EVALUATED"
  assert report["p6_allowed"] is False
  assert report["cases"]["zero"]["duplicated_cash"] == 25000
  assert report["cases"]["zero"]["metrics"]["closed_batches"] == 2
  assert report["cases"]["stress"]["metrics"]["closed_batches"] == 0
  assert report["cases"]["stress"]["metrics"]["open_batches"] == 2
  from quantx_infrastructure.services.t_assistant_backtest_store import (
    TAssistantBacktestStore,
  )

  for case in report["cases"].values():
    store = TAssistantBacktestStore(
      directory / str(case["scenario_index"]) / "portfolio" / case["execution_id"]
    )
    assert summarize_persisted_backtest(store) == case["metrics"]


async def test_local_fact_constraints_and_corruption_detection(tmp_path):
  import sqlite3

  from quantx_engine.t_assistant_backtest_run import execute_backtest

  store, _, _ = await execute_backtest(
    request=runtime(request_only=True),
    events=ticks(),
    code_manifest={"synthetic": "v1"},
    root=tmp_path,
  )
  with sqlite3.connect(store.directory / "facts.sqlite3") as db:
    tables = {
      r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert tables == {
      "t_assistant_executions",
      "t_assistant_backtest_versions",
      "backtest_frames",
      "backtest_results",
      "backtest_failures",
    }
    with pytest.raises(sqlite3.IntegrityError):
      db.execute("UPDATE t_assistant_executions SET environment='LIVE'")
    db.execute("UPDATE backtest_frames SET facts='{}' WHERE frame_index=5")
  with pytest.raises(ValueError, match="FRAME_HASH_MISMATCH"):
    list(store.frames())


async def test_runtime_failure_is_durable_and_does_not_publish_result(
  tmp_path, monkeypatch
):
  import sqlite3

  from quantx_engine.t_assistant_backtest_run import execute_backtest
  from quantx_engine.t_assistant_backtest_runtime import TAssistantBacktestRuntime

  def fail(_self):
    raise ValueError("BACKTEST_INJECTED_FAILURE")

  monkeypatch.setattr(TAssistantBacktestRuntime, "_conservation", fail)
  with pytest.raises(ValueError, match="INJECTED_FAILURE"):
    await execute_backtest(
      request=runtime(request_only=True),
      events=ticks(),
      code_manifest={"synthetic": "v1"},
      root=tmp_path,
    )
  directory = next(tmp_path.iterdir())
  with sqlite3.connect(directory / "facts.sqlite3") as db:
    assert db.execute("SELECT status FROM t_assistant_executions").fetchone() == (
      "FAILED",
    )
    failure = json.loads(
      db.execute("SELECT manifest FROM backtest_failures").fetchone()[0]
    )
    assert failure["reason"] == "BACKTEST_INJECTED_FAILURE"
    assert db.execute("SELECT COUNT(*) FROM backtest_results").fetchone() == (0,)
  assert not (directory / "result.json").exists()


async def test_stale_valuation_blocks_admission_and_continues_market(monkeypatch):
  run = runtime()

  async def stale(_cycle_id):
    raise ValueError("T_VALUATION_MARK_STALE")

  monkeypatch.setattr(run, "_portfolio", stale)
  frames = await run.run(ticks())
  assert len(frames) == 12
  assert not run.broker.orders
  assert any(a["type"] == "ALLOCATION_BLOCKED" for f in frames for a in f["audit"])


async def test_partial_buy_does_not_reserve_minimum_commission_twice():
  from datetime import timedelta

  from tests.engine.unit.test_t_assistant_backtest_boundaries import move

  run = runtime()
  events = [
    move(e, timedelta(0), depth=200) if e.tick.accepted_sequence == 6 else e
    for e in ticks()
    if e.tick.accepted_sequence <= 6
  ]
  await run.run(events)
  assert [o.filled_volume for o in run.broker.pending_orders] == [50, 50]
  # Each order has already paid its 5 CNY minimum. Only the remaining 50-share
  # notional and incremental transfer fee are still reserved.
  assert run.broker._reserved_pending_buy_cash() == pytest.approx(2 * (4966 + 0.04966))


async def test_lunch_quotes_cannot_fill_pending_orders():
  from datetime import timedelta

  from tests.engine.unit.test_t_assistant_backtest_boundaries import move

  run = runtime()
  await run.run([move(e, timedelta(hours=2, seconds=-24)) for e in ticks()])
  assert len(run.broker.orders) == 2
  assert not run.broker.trades
  assert len(run.broker.pending_orders) == 2


async def test_failed_frozen_policy_is_saved_without_optimization(tmp_path):
  policy = BacktestAdmissionPolicy(
    "synthetic-test-only",
    "unit-test-not-user-approval",
    1,
    2,
    1,
    {"base": (1.0, 0.001, 1.0, 0.001)},
    {"base": "GT"},
    0.001,
    1.0,
  )
  directory, report = await evaluate_backtest_comparison(
    request=runtime(request_only=True),
    events=ticks(),
    scenarios={"base": 0.0},
    code_manifest={"synthetic": "v1"},
    root=tmp_path,
    policy=policy,
  )
  assert report["strategy_admission"] == "FAIL"
  assert report["p6_allowed"] is False
  assert "INCREMENTAL_RETURN_BELOW_THRESHOLD" in report["failures"][0]["reasons"]
  assert json.loads((directory / "evaluation.json").read_text())["material"][
    "policy"
  ] == {
    "version": policy.version,
    "approval_reference": policy.approval_reference,
    "minimum_trading_days": 1,
    "minimum_closed_batches": 2,
    "minimum_group_closed_batches": 1,
    "scenario_thresholds": {"base": [1.0, 0.001, 1.0, 0.001]},
    "return_comparisons": {"base": "GT"},
    "minimum_minute_coverage": 0.001,
    "minimum_complete_day_fraction": 1.0,
  }


async def test_strict_book_slippage_changes_fill_price():
  baseline = runtime()
  stressed = runtime()
  stressed.broker.strict_book_slippage_rate = 0.0005
  events = []
  for item in ticks():
    if item.tick.accepted_sequence in (6, 7, 8):
      # Keep the slippage-adjusted ask below the original candidate limit.
      price = 99.0
      item = replace(
        item,
        tick=replace(
          item.tick,
          sample=replace(
            item.tick.sample, price=price, bid_price=price - 0.01, ask_price=price
          ),
        ),
        market=replace(
          item.market,
          price=price,
          bid_price=[price - 0.01 - i * 0.01 for i in range(5)],
          ask_price=[price + i * 0.01 for i in range(5)],
        ),
      )
    events.append(item)
  await baseline.run(events)
  await stressed.run(events)
  assert len(stressed.broker.trades) == len(baseline.broker.trades) == 4
  assert stressed.broker.trades[0].price > baseline.broker.trades[0].price
  assert stressed.broker.trades[-1].price < baseline.broker.trades[-1].price
  assert stressed.broker.cash < baseline.broker.cash
