"""Predeclared RULE_ONLY comparison and admission; never an optimizer."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time
from math import isfinite
from pathlib import Path
from uuid import UUID, uuid4

from quantx_domain.clock import SHANGHAI
from quantx_domain.trading.market_session import classify_market_data_session
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.services.t_assistant_backtest_store import (
  TAssistantBacktestStore,
)

from quantx_engine.t_assistant_backtest_data import BacktestDataset
from quantx_engine.t_assistant_backtest_run import (
  backtest_code_evidence,
  execute_backtest,
)
from quantx_engine.t_assistant_backtest_runtime import json_value
from quantx_engine.t_assistant_backtest_timeline import iterate_events


@dataclass(frozen=True)
class BacktestAdmissionPolicy:
  version: str
  approval_reference: str
  minimum_trading_days: int
  minimum_closed_batches: int
  minimum_group_closed_batches: int
  # Each scenario owns (minimum incremental return, maximum drawdown,
  # minimum worst-group return, maximum group drawdown); no default thresholds.
  scenario_thresholds: dict[str, tuple[float, float, float, float]]
  return_comparisons: dict[str, str]
  minimum_minute_coverage: float
  minimum_complete_day_fraction: float

  def __post_init__(self):
    if set(self.return_comparisons) != set(self.scenario_thresholds) or any(
      v not in {"GT", "GTE"} for v in self.return_comparisons.values()
    ):
      raise ValueError("BACKTEST_RETURN_COMPARISON_REQUIRED")
    if any(
      not isfinite(v) or not 0 < v <= 1
      for v in (self.minimum_minute_coverage, self.minimum_complete_day_fraction)
    ):
      raise ValueError("BACKTEST_DATA_COVERAGE_THRESHOLDS_REQUIRED")
    if not self.version or not self.approval_reference or not self.scenario_thresholds:
      raise ValueError("BACKTEST_APPROVED_POLICY_REQUIRED")
    if any(
      type(v) is not int or v < 1
      for v in (
        self.minimum_trading_days,
        self.minimum_closed_batches,
        self.minimum_group_closed_batches,
      )
    ):
      raise ValueError("BACKTEST_SAMPLE_THRESHOLDS_REQUIRED")
    if any(
      len(v) != 4
      or any(not isfinite(x) for x in v)
      or not 0 <= v[1] <= 1
      or not 0 <= v[3] <= 1
      for v in self.scenario_thresholds.values()
    ):
      raise ValueError("BACKTEST_METRIC_THRESHOLDS_INVALID")


async def qualify_backtest_data(request, events, policy):
  """Report continuous-session minute coverage before any scenario is run.

  Every configured symbol must meet the explicit minute threshold for a day
  to qualify. Suspended or absent data is not silently removed from the sample.
  This measures observed coverage, not a proof of exchange-wide Tick completeness.
  """
  codes = set(request.runtime_options["initial_positions"])
  if isinstance(events, BacktestDataset):
    days = [
      datetime.fromisoformat(d).date()
      for d in events.manifest["material"]["trading_days"]
    ]
    source = events.events()
  else:
    last = max(e.decision_time.astimezone(SHANGHAI).date() for e in events)
    days = [
      d
      for d in request.runtime_options["trading_days"]
      if request.start_at.astimezone(SHANGHAI).date() <= d <= last
    ]
    source = events
  if not days:
    raise ValueError("BACKTEST_QUALIFICATION_CALENDAR_EMPTY")
  allowed_days = set(days)
  expected = {
    minute
    for minute in range(1440)
    if classify_market_data_session(
      datetime.combine(days[0], time(minute // 60, minute % 60, 30), SHANGHAI)
    ).is_continuous
  }
  observed = {}
  async for event in iterate_events(source):
    at = event.market.timestamp.astimezone(SHANGHAI)
    code, minute = event.market.instrument_code, at.hour * 60 + at.minute
    if code not in codes or at.date() not in allowed_days:
      raise ValueError("BACKTEST_QUALIFICATION_SCOPE_MISMATCH")
    if minute in expected:
      observed.setdefault((at.date(), code), set()).add(minute)
  partitions = [
    {
      "day": day.isoformat(),
      "code": code,
      "observed_minutes": len(observed.get((day, code), set())),
      "coverage": len(observed.get((day, code), set())) / len(expected),
    }
    for day in days
    for code in sorted(codes)
  ]
  complete = sum(
    all(
      p["coverage"] >= policy.minimum_minute_coverage
      for p in partitions
      if p["day"] == day.isoformat()
    )
    for day in days
  )
  reasons = []
  if complete < policy.minimum_trading_days:
    reasons.append("INSUFFICIENT_COMPLETE_TRADING_DAYS")
  if complete / len(days) < policy.minimum_complete_day_fraction:
    reasons.append("COMPLETE_DAY_FRACTION_BELOW_THRESHOLD")
  return {
    "version": "continuous-minute-coverage.v1",
    "expected_minutes_per_day": len(expected),
    "expected_days": len(days),
    "complete_days": complete,
    "partitions": partitions,
    "reasons": reasons,
  }


def summarize_backtest(store, runtime):
  initial_equity = runtime.broker.initial_capital
  if initial_equity <= 0:
    raise ValueError("BACKTEST_POSITIVE_INITIAL_EQUITY_REQUIRED")
  peak, drawdown, incremental = 1.0, 0.0, 0.0
  days, groups = (
    set(),
    {f"symbol:{c}": {"closed": 0, "net_pnl": 0.0} for c in runtime.states},
  )
  peaks, group_returns, group_drawdowns, quarter_opening = {}, {}, {}, {}
  previous_incremental = 0.0
  for frame in store.frames():
    day = frame["facts"]["time"][:10]
    days.add(day)
    quarter = f"{day[:4]}-Q{(int(day[5:7]) - 1) // 3 + 1}"
    groups.setdefault(f"quarter:{quarter}", {"closed": 0, "net_pnl": 0.0})
    balances = frame["facts"]["evidence"]
    incremental = (balances["equity"] - balances["passive_equity"]) / initial_equity
    equity = 1 + incremental
    peak = max(peak, equity)
    drawdown = max(drawdown, (peak - equity) / peak)
    quarter_opening.setdefault(quarter, previous_incremental)
    marked = {
      f"symbol:{c}": pnl / initial_equity
      for c, pnl in balances["incremental_pnl_by_symbol"].items()
    }
    marked[f"quarter:{quarter}"] = incremental - quarter_opening[quarter]
    for key, value in marked.items():
      peaks[key] = max(peaks.get(key, 1.0), 1 + value)
      group_drawdowns[key] = max(
        group_drawdowns.get(key, 0.0), (peaks[key] - 1 - value) / peaks[key]
      )
      group_returns[key] = value
    previous_incremental = incremental
  closed = 0
  for plan in runtime.plans.plans.values():
    if plan.remaining_volume or plan.entry_filled_volume == 0:
      continue
    fills = [
      f for f in runtime.broker.trades if f.metadata["exit_plan_id"] == plan.plan_id
    ]
    pnl = sum(
      (f.amount if f.trade_type.value == "SELL" else -f.amount) - f.commission
      for f in fills
    )
    last = max(f.trade_time for f in fills)
    quarter = f"{last.year}-Q{(last.month - 1) // 3 + 1}"
    for key in (f"symbol:{plan.template.instrument_code}", f"quarter:{quarter}"):
      groups[key]["closed"] += 1
      groups[key]["net_pnl"] += pnl
    closed += 1
  for key, group in groups.items():
    group["return_on_initial_portfolio_equity"] = group["net_pnl"] / initial_equity
    group["marked_incremental_return"] = group_returns.get(key, 0.0)
    group["incremental_max_drawdown"] = group_drawdowns.get(key, 0.0)
  return {
    "initial_equity": initial_equity,
    "incremental_return": incremental,
    "incremental_max_drawdown": drawdown,
    "trading_days": len(days),
    "closed_batches": closed,
    "open_batches": len(
      {p.plan_id for p in runtime.plans.plans.values() if p.remaining_volume}
      | {o.request.metadata["exit_plan_id"] for o in runtime.broker.pending_orders}
    ),
    "groups": groups,
    "worst_group_return": min(g["marked_incremental_return"] for g in groups.values()),
    "worst_group_drawdown": max(g["incremental_max_drawdown"] for g in groups.values()),
    "fees": sum(f.commission for f in runtime.broker.trades),
    "cash_conservation_error": runtime._conservation()["cash_error"],
  }


def admission_reasons(metrics, *, policy, scenario):
  minimum_return, maximum_drawdown, minimum_group_return, maximum_group_drawdown = (
    policy.scenario_thresholds[scenario]
  )
  reasons = []
  if metrics["trading_days"] < policy.minimum_trading_days:
    reasons.append("INSUFFICIENT_TRADING_DAYS")
  if metrics["closed_batches"] < policy.minimum_closed_batches:
    reasons.append("INSUFFICIENT_CLOSED_BATCHES")
  if any(
    g["closed"] < policy.minimum_group_closed_batches
    for g in metrics["groups"].values()
  ):
    reasons.append("INSUFFICIENT_GROUP_SAMPLES")
  if metrics["incremental_return"] < minimum_return or (
    policy.return_comparisons[scenario] == "GT"
    and metrics["incremental_return"] == minimum_return
  ):
    reasons.append("INCREMENTAL_RETURN_BELOW_THRESHOLD")
  if metrics["incremental_max_drawdown"] > maximum_drawdown:
    reasons.append("INCREMENTAL_DRAWDOWN_EXCEEDED")
  if metrics["worst_group_return"] < minimum_group_return:
    reasons.append("WORST_GROUP_RETURN_BELOW_THRESHOLD")
  if metrics["worst_group_drawdown"] > maximum_group_drawdown:
    reasons.append("WORST_GROUP_DRAWDOWN_EXCEEDED")
  return reasons


async def evaluate_backtest_comparison(
  *,
  request,
  events,
  scenarios: dict[str, float],
  code_manifest: dict,
  root: Path,
  policy: BacktestAdmissionPolicy | None = None,
):
  """Run exactly the declared cases once, preserve failures and stop.

  Single-symbol cases each receive the whole initial cash budget. Their sum is
  explicitly a repeated-cash counterfactual, never a capital-comparable return.
  Groups are symbol and closing quarter; closed-batch net PnL uses the common
  initial portfolio equity denominator. Open carry remains in portfolio return.
  """
  if not scenarios or any(
    not isfinite(v) or not 0 <= v < 1 for v in scenarios.values()
  ):
    raise ValueError("BACKTEST_EXPLICIT_SLIPPAGE_SCENARIOS_REQUIRED")
  if policy and set(policy.scenario_thresholds) != set(scenarios):
    raise ValueError("BACKTEST_POLICY_SCENARIO_MISMATCH")
  request, policy, scenarios = deepcopy(request), deepcopy(policy), deepcopy(scenarios)
  streamed = isinstance(events, BacktestDataset)
  if not streamed:
    events = tuple(events)
  directory = Path(root) / str(uuid4())
  directory.mkdir(parents=True, exist_ok=False)
  frozen = {
    "request": json_value(request),
    "data_hash": stable_manifest_hash(
      events.input_manifest if streamed else {"ticks": json_value(events)}
    ),
    "scenarios": scenarios,
    "policy": json_value(policy),
    "code": {"declared": code_manifest, **backtest_code_evidence(__name__)},
    "metric_version": "incremental-vs-hold.marked-symbol-quarter.v1",
  }
  TAssistantBacktestStore._create(
    directory / "evaluation.json",
    {"material": frozen, "hash": stable_manifest_hash(frozen)},
  )
  evidence = {
    "evaluation_hash": stable_manifest_hash(frozen),
    "admission_policy_hash": stable_manifest_hash(frozen["policy"])
    if policy is not None
    else None,
    "data_qualification_hash": None,
  }
  if policy is not None:
    quality = await qualify_backtest_data(request, events, policy)
    TAssistantBacktestStore._create(directory / "data-qualification.json", quality)
    evidence["data_qualification_hash"] = stable_manifest_hash(quality)
    if quality["reasons"]:
      report = {
        "evidence": evidence,
        "cases": {},
        "failures": [{"reasons": quality["reasons"]}],
        "strategy_admission": "DATA_BLOCKED",
        "p6_allowed": False,
      }
      TAssistantBacktestStore._create(
        directory / "report.json",
        {"material": report, "hash": stable_manifest_hash(report)},
      )
      return directory, report
  cases, failures = {}, []
  for index, (scenario, slippage) in enumerate(scenarios.items()):
    candidate = deepcopy(request)
    candidate.runtime_options["broker_parameters"]["slippage_rate"] = slippage
    store, run, result = await execute_backtest(
      request=candidate,
      events=events,
      code_manifest=code_manifest,
      root=directory / str(index) / "portfolio",
    )
    metrics = summarize_backtest(store, run)
    controls = []
    for symbol_index, code in enumerate(
      sorted(candidate.runtime_options["initial_positions"])
    ):
      single = deepcopy(candidate)
      for name in (
        "initial_positions",
        "initial_buckets",
        "profiles",
        "industries",
        "envelope_policies",
      ):
        single.runtime_options[name] = {code: single.runtime_options[name][code]}
      _, control, control_result = await execute_backtest(
        request=single,
        events=events.subset((code,))
        if streamed
        else [e for e in events if e.market.instrument_code == code],
        code_manifest=code_manifest,
        root=directory / str(index) / f"single-{symbol_index}",
      )
      controls.append(
        {
          "code": code,
          "execution_id": control.execution.execution_id,
          "result_hash": control_result["hash"],
          "initial_cash": control.initial_cash,
          "final_cash": control.broker.cash,
        }
      )
    reasons = (
      admission_reasons(metrics, policy=policy, scenario=scenario)
      if policy
      else ["ADMISSION_POLICY_NOT_CONFIRMED"]
    )
    if reasons:
      failures.append({"scenario": scenario, "reasons": reasons})
    cases[scenario] = {
      "scenario_index": index,
      "metrics": metrics,
      "execution_id": run.execution.execution_id,
      "result_hash": result["hash"],
      "single_symbol_controls": controls,
      "duplicated_cash": sum(c["initial_cash"] for c in controls) - run.initial_cash,
    }
    # Preserve each completed scenario even if a later source or execution fails.
    TAssistantBacktestStore._create(directory / f"case-{index}.json", cases[scenario])
  report = {
    "evidence": evidence,
    "cases": cases,
    "failures": failures,
    "strategy_admission": "NOT_EVALUATED"
    if policy is None
    else "FAIL"
    if failures
    else "PASS",
    "p6_allowed": policy is not None and not failures,
  }
  TAssistantBacktestStore._create(
    directory / "report.json",
    {"material": report, "hash": stable_manifest_hash(report)},
  )
  return directory, report


def read_backtest_evaluation_evidence(directory, *, expected_report_hash):
  """Check artifact linkage only; this does not authorize a release or trust metrics.

  The expected report hash must come from the review record, not the uploaded
  artifact. Runtime facts and the operator's approved policy still need review.
  """
  directory = Path(directory)
  read = TAssistantBacktestStore._read
  evaluation = read(directory / "evaluation.json")
  report = read(directory / "report.json")
  if (
    report["hash"] != expected_report_hash
    or stable_manifest_hash(report["material"]) != report["hash"]
    or stable_manifest_hash(evaluation["material"]) != evaluation["hash"]
  ):
    raise ValueError("BACKTEST_EVALUATION_HASH_CONFLICT")
  material = report["material"]
  evidence = material.get("evidence", {})
  frozen = evaluation["material"]
  if evidence.get("evaluation_hash") != evaluation["hash"]:
    raise ValueError("BACKTEST_EVALUATION_REPORT_SCOPE_CONFLICT")
  if frozen["policy"] is None:
    if (
      evidence.get("admission_policy_hash") is not None
      or evidence.get("data_qualification_hash") is not None
      or material["strategy_admission"] != "NOT_EVALUATED"
      or material["p6_allowed"] is not False
    ):
      raise ValueError("BACKTEST_EVALUATION_UNCONFIRMED_POLICY")
  else:
    quality = read(directory / "data-qualification.json")
    if evidence.get("admission_policy_hash") != stable_manifest_hash(
      frozen["policy"]
    ) or evidence.get("data_qualification_hash") != stable_manifest_hash(quality):
      raise ValueError("BACKTEST_EVALUATION_QUALIFICATION_CONFLICT")
    if quality["reasons"] and (
      material["strategy_admission"] != "DATA_BLOCKED"
      or material["p6_allowed"] is not False
      or material["cases"]
    ):
      raise ValueError("BACKTEST_EVALUATION_DATA_BLOCKED")
  if material["strategy_admission"] != "DATA_BLOCKED":
    if set(material["cases"]) != set(frozen["scenarios"]):
      raise ValueError("BACKTEST_EVALUATION_SCENARIOS_CONFLICT")
  return {"evaluation": evaluation, "report": report}


def verify_backtest_admission_conclusion(
  directory, *, expected_report_hash, expected_policy_hash
):
  """Recompute the frozen-policy conclusion, without approving a LIVE release.

  Hashes identify externally reviewed artifacts. Portfolio metrics are rebuilt
  from durable audits and valuation frames after each result chain is verified.
  """
  evidence = read_backtest_evaluation_evidence(
    directory, expected_report_hash=expected_report_hash
  )
  frozen = evidence["evaluation"]["material"]
  report = evidence["report"]["material"]
  if (
    frozen["policy"] is None
    or not isinstance(expected_policy_hash, str)
    or stable_manifest_hash(frozen["policy"]) != expected_policy_hash
  ):
    raise ValueError("BACKTEST_ADMISSION_REVIEWED_POLICY_REQUIRED")
  policy = BacktestAdmissionPolicy(**frozen["policy"])
  if set(policy.scenario_thresholds) != set(frozen["scenarios"]):
    raise ValueError("BACKTEST_POLICY_SCENARIO_MISMATCH")
  if report["strategy_admission"] == "DATA_BLOCKED":
    raise ValueError("BACKTEST_ADMISSION_DATA_BLOCKED")
  failures = []
  for scenario, case in report["cases"].items():
    metrics = case["metrics"]
    counts = [metrics["trading_days"], metrics["closed_batches"]]
    groups = metrics["groups"]
    if not isinstance(groups, dict) or not groups:
      raise ValueError("BACKTEST_ADMISSION_GROUPS_REQUIRED")
    counts.extend(group["closed"] for group in groups.values())
    numbers = [
      metrics[key]
      for key in (
        "incremental_return",
        "incremental_max_drawdown",
        "worst_group_return",
        "worst_group_drawdown",
      )
    ]
    if any(type(n) is not int or n < 0 for n in counts) or any(
      type(n) not in {int, float} or not isfinite(n) for n in numbers
    ):
      raise ValueError("BACKTEST_ADMISSION_METRICS_INVALID")
    reasons = admission_reasons(metrics, policy=policy, scenario=scenario)
    if reasons:
      failures.append({"scenario": scenario, "reasons": reasons})
  expected_status = "FAIL" if failures else "PASS"
  if (
    report["strategy_admission"] != expected_status
    or report["p6_allowed"] is not (not failures)
    or sorted(report["failures"], key=lambda item: item["scenario"])
    != sorted(failures, key=lambda item: item["scenario"])
  ):
    raise ValueError("BACKTEST_ADMISSION_CONCLUSION_CONFLICT")
  _verify_comparison_result_artifacts(Path(directory), frozen, report)
  return evidence


def _verify_comparison_result_artifacts(directory, frozen, report):
  indices = set()
  symbols = sorted(frozen["request"]["runtime_options"]["initial_positions"])
  for scenario, case in report["cases"].items():
    index = case["scenario_index"]
    if type(index) is not int or index < 0 or index in indices:
      raise ValueError("BACKTEST_RESULT_SCENARIO_INDEX_INVALID")
    indices.add(index)
    controls = case["single_symbol_controls"]
    if [control["code"] for control in controls] != symbols:
      raise ValueError("BACKTEST_RESULT_CONTROL_SCOPE_CONFLICT")
    expected = deepcopy(frozen["request"])
    expected["runtime_options"]["broker_parameters"]["slippage_rate"] = frozen[
      "scenarios"
    ][scenario]
    targets = [("portfolio", case, expected)]
    for control_index, control in enumerate(controls):
      single = deepcopy(expected)
      for name in (
        "initial_positions",
        "initial_buckets",
        "profiles",
        "industries",
        "envelope_policies",
      ):
        single["runtime_options"][name] = {
          control["code"]: single["runtime_options"][name][control["code"]]
        }
      targets.append((f"single-{control_index}", control, single))
    for name, reference, config in targets:
      execution_id = reference["execution_id"]
      if not isinstance(execution_id, str) or str(UUID(execution_id)) != execution_id:
        raise ValueError("BACKTEST_RESULT_EXECUTION_ID_INVALID")
      store = TAssistantBacktestStore(directory / str(index) / name / execution_id)
      if store.manifest["material"]["frozen"]["config"] != config:
        raise ValueError("BACKTEST_RESULT_CONFIG_SCOPE_CONFLICT")
      store.read_verified_result(expected_hash=reference["result_hash"])
      if name == "portfolio" and summarize_persisted_backtest(store) != case["metrics"]:
        raise ValueError("BACKTEST_RESULT_METRICS_CONFLICT")
  if indices != set(range(len(report["cases"]))):
    raise ValueError("BACKTEST_RESULT_SCENARIO_INDEX_INVALID")


def summarize_persisted_backtest(store):
  """Rebuild the metric inputs from frozen account and durable fill/order audits."""
  from types import SimpleNamespace

  account = store.manifest["material"]["frozen"]["initial_account"]
  fills, orders, plans = [], {}, {}
  seen = set()
  final = None
  for frame in store.frames():
    final = frame["facts"]["evidence"]
    for event in frame["facts"]["audit"]:
      value = event.get("value")
      if event["type"] == "ORDER":
        orders[value["order_id"]] = value
      elif event["type"] == "FILL":
        if value["trade_id"] in seen:
          raise ValueError("BACKTEST_METRIC_DUPLICATE_FILL")
        seen.add(value["trade_id"])
        fill = SimpleNamespace(
          **{
            **value,
            "trade_type": SimpleNamespace(value=value["trade_type"]),
            "trade_time": datetime.fromisoformat(value["trade_time"]),
          }
        )
        fills.append(fill)
        plan_id = fill.metadata["exit_plan_id"]
        plan = plans.setdefault(
          plan_id,
          SimpleNamespace(
            plan_id=plan_id,
            remaining_volume=0,
            entry_filled_volume=0,
            template=SimpleNamespace(instrument_code=fill.instrument_code),
          ),
        )
        if plan.template.instrument_code != fill.instrument_code:
          raise ValueError("BACKTEST_METRIC_PLAN_SCOPE_CONFLICT")
        if fill.trade_type.value == "BUY":
          plan.entry_filled_volume += fill.volume
          plan.remaining_volume += fill.volume
        elif fill.trade_type.value == "SELL":
          plan.remaining_volume -= fill.volume
        else:
          raise ValueError("BACKTEST_METRIC_FILL_SIDE_INVALID")
        if plan.remaining_volume < 0:
          raise ValueError("BACKTEST_METRIC_NEGATIVE_PLAN_VOLUME")
  if final is None:
    raise ValueError("BACKTEST_METRIC_FRAMES_REQUIRED")
  pending = [
    SimpleNamespace(request=SimpleNamespace(metadata=o["request"]["metadata"]))
    for o in orders.values()
    if o["status"] in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}
  ]
  runtime = SimpleNamespace(
    states=account["initial_positions"],
    broker=SimpleNamespace(
      initial_capital=account["initial_cash"]
      + sum(p["market_value"] for p in account["initial_positions"].values()),
      trades=fills,
      pending_orders=pending,
    ),
    plans=SimpleNamespace(plans=plans),
    _conservation=lambda: final,
  )
  return summarize_backtest(store, runtime)
