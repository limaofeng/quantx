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
  report = await evaluation.qualify_backtest_data(
    runtime(request_only=True), dataset, policy()
  )
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


@pytest.mark.parametrize("damage", [None, "input", "quality", "expected", "scope"])
async def test_report_links_frozen_inputs_and_qualification(tmp_path, damage):
  directory, _ = await evaluation.evaluate_backtest_comparison(
    request=runtime(request_only=True),
    events=ticks(),
    scenarios={"base": 0.0},
    code_manifest={"fixture": "v1"},
    root=tmp_path,
    policy=policy(),
  )
  report_path = directory / "report.json"
  report = json.loads(report_path.read_text())
  expected = report["hash"]
  if damage in {"input", "quality"}:
    path = directory / (
      "evaluation.json" if damage == "input" else "data-qualification.json"
    )
    value = json.loads(path.read_text())
    if damage == "input":
      value["material"]["scenarios"] = {"other": 0.01}
      value["hash"] = evaluation.stable_manifest_hash(value["material"])
    else:
      value["complete_days"] = 999
    path.write_text(json.dumps(value))
  elif damage == "expected":
    expected = "0" * 64
  elif damage == "scope":
    report["material"]["evidence"]["evaluation_hash"] = "1" * 64
    report["hash"] = expected = evaluation.stable_manifest_hash(report["material"])
    report_path.write_text(json.dumps(report))
  if damage:
    with pytest.raises(ValueError, match="BACKTEST_EVALUATION_"):
      evaluation.read_backtest_evaluation_evidence(
        directory, expected_report_hash=expected
      )
  else:
    result = evaluation.read_backtest_evaluation_evidence(
      directory, expected_report_hash=expected
    )
    assert result["report"]["material"]["strategy_admission"] == "DATA_BLOCKED"
    assert result["report"]["material"]["evidence"]["admission_policy_hash"]


@pytest.mark.parametrize("damage", [None, "pass_flag", "reasons", "policy", "count"])
async def test_frozen_policy_conclusion_is_recomputed(tmp_path, damage):
  from dataclasses import replace

  frozen_policy = replace(
    policy(minute_coverage=0.001),
    scenario_thresholds={"base": (1.0, 0.001, 1.0, 0.001)},
  )
  directory, _ = await evaluation.evaluate_backtest_comparison(
    request=runtime(request_only=True),
    events=ticks(),
    scenarios={"base": 0.0},
    code_manifest={"fixture": "v1"},
    root=tmp_path,
    policy=frozen_policy,
  )
  path = directory / "report.json"
  report = json.loads(path.read_text())
  policy_hash = report["material"]["evidence"]["admission_policy_hash"]
  assert report["material"]["strategy_admission"] == "FAIL"
  if damage == "pass_flag":
    report["material"].update(strategy_admission="PASS", p6_allowed=True, failures=[])
  elif damage == "reasons":
    report["material"]["failures"] = []
  elif damage == "policy":
    policy_hash = "0" * 64
  elif damage == "count":
    report["material"]["cases"]["base"]["metrics"]["closed_batches"] = True
  # Even a freshly hashed inconsistent report must not pass conclusion validation.
  report["hash"] = evaluation.stable_manifest_hash(report["material"])
  path.write_text(json.dumps(report))
  if damage:
    with pytest.raises(ValueError, match="BACKTEST_ADMISSION_"):
      evaluation.verify_backtest_admission_conclusion(
        directory, expected_report_hash=report["hash"], expected_policy_hash=policy_hash
      )
  else:
    result = evaluation.verify_backtest_admission_conclusion(
      directory, expected_report_hash=report["hash"], expected_policy_hash=policy_hash
    )
    assert result["report"]["material"]["p6_allowed"] is False


@pytest.mark.parametrize("minimum_return", [-1.0, 1.0])
async def test_conclusion_handles_multiple_scenarios_in_original_order(
  tmp_path, minimum_return
):
  from dataclasses import replace

  approved = replace(
    policy(minute_coverage=0.001),
    scenario_thresholds={name: (minimum_return, 1.0, -1.0, 1.0) for name in ("z", "a")},
    return_comparisons={name: "GTE" for name in ("z", "a")},
  )
  directory, report = await evaluation.evaluate_backtest_comparison(
    request=runtime(request_only=True),
    events=ticks(),
    scenarios={"z": 0.0, "a": 0.0},
    code_manifest={"fixture": "v1"},
    root=tmp_path,
    policy=approved,
  )
  saved = json.loads((directory / "report.json").read_text())
  checked = evaluation.verify_backtest_admission_conclusion(
    directory,
    expected_report_hash=saved["hash"],
    expected_policy_hash=report["evidence"]["admission_policy_hash"],
  )
  assert checked["report"]["material"]["p6_allowed"] is (minimum_return < 0)
