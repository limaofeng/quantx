"""Formal preflight fails before expensive scenario runs; thresholds are fixtures."""

import json

import pytest
from quantx_engine import t_assistant_backtest_evaluation as evaluation

from tests.engine.unit.test_t_assistant_backtest_runtime import CODES, runtime, ticks


def policy(comparison="GT", minute_coverage=0.99):
  return evaluation.BacktestAdmissionPolicy(
    "unit-test-only",
    "synthetic-fixture-not-user-approval",
    1,
    1,
    1,
    {"base": (0.0, 1.0, -1.0, 1.0)},
    {"base": comparison},
    minute_coverage,
    1.0,
  )


@pytest.mark.parametrize("comparison, blocked", [("GT", True), ("GTE", False)])
def test_zero_return_uses_frozen_comparison(comparison, blocked):
  metrics = {
    "trading_days": 1,
    "closed_batches": 1,
    "groups": {"symbol": {"closed": 1}},
    "incremental_return": 0.0,
    "incremental_max_drawdown": 0.0,
    "worst_group_return": 0.0,
    "worst_group_drawdown": 0.0,
  }
  reasons = evaluation.admission_reasons(
    metrics, policy=policy(comparison), scenario="base"
  )
  assert ("INCREMENTAL_RETURN_BELOW_THRESHOLD" in reasons) is blocked


async def test_all_symbols_must_meet_daily_coverage():
  report = await evaluation.qualify_backtest_data(
    runtime(request_only=True),
    [e for e in ticks() if e.market.instrument_code == CODES[0]],
    policy(minute_coverage=0.001),
  )
  assert report["complete_days"] == 0
  missing = next(p for p in report["partitions"] if p["code"] == CODES[1])
  assert missing["coverage"] == 0


async def test_acquisition_coverage_matches_formal_qualification(tmp_path):
  from tests.engine.unit.test_t_assistant_backtest_evaluation import History, acquire

  dataset = await acquire(tmp_path, History())
  report = await evaluation.qualify_backtest_data(runtime(request_only=True), dataset, policy())
  material = dataset.manifest["material"]
  assert material["coverage_metric"] == report["version"]
  for part, qualified in zip(material["parts"], report["partitions"], strict=True):
    assert part["code"] == qualified["code"]
    observed = part["continuous_minute_coverage"]
    assert observed["observed_minutes"] == qualified["observed_minutes"] == 1
    assert observed["expected_minutes"] == report["expected_minutes_per_day"]
    assert observed["ratio"] == qualified["coverage"]


async def test_sparse_day_saves_blocker_without_running_scenarios(
  tmp_path, monkeypatch
):
  async def forbidden(**_kwargs):
    raise AssertionError("An unqualified dataset must not start a scenario")

  monkeypatch.setattr(evaluation, "execute_backtest", forbidden)
  directory, report = await evaluation.evaluate_backtest_comparison(
    request=runtime(request_only=True),
    events=ticks(),
    scenarios={"base": 0.0},
    code_manifest={"fixture": "v1"},
    root=tmp_path,
    policy=policy(),
  )
  assert report["strategy_admission"] == "DATA_BLOCKED"
  assert not report["cases"] and not report["p6_allowed"]
  quality = json.loads((directory / "data-qualification.json").read_text())
  assert quality["complete_days"] == 0
  assert all(p["observed_minutes"] == 1 for p in quality["partitions"])
