"""Predeclared RULE_ONLY comparison and admission; never an optimizer."""

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from uuid import uuid4

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.services.t_assistant_backtest_store import (
  TAssistantBacktestStore,
)

from quantx_engine.t_assistant_backtest_data import FrozenBacktestDataset
from quantx_engine.t_assistant_backtest_run import (
  backtest_code_evidence,
  execute_backtest,
)
from quantx_engine.t_assistant_backtest_runtime import json_value


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

  def __post_init__(self):
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
  if metrics["incremental_return"] < minimum_return:
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
  streamed = isinstance(events, FrozenBacktestDataset)
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
      "metrics": metrics,
      "execution_id": run.execution.execution_id,
      "result_hash": result["hash"],
      "single_symbol_controls": controls,
      "duplicated_cash": sum(c["initial_cash"] for c in controls) - run.initial_cash,
    }
    # Preserve each completed scenario even if a later source or execution fails.
    TAssistantBacktestStore._create(directory / f"case-{index}.json", cases[scenario])
  report = {
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
