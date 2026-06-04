"""GA-facing evolvable strategy adapter.

The adapter exposes parameter sampling and evaluation surfaces only; trading
semantics stay inside strategies, risk layers, and ledgers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.utils import time_utils


@dataclass(frozen=True)
class EvolvableParameter:
  name: str
  minimum: float
  maximum: float
  step: Optional[float] = None

  def clamp(self, value: float) -> float:
    value = max(self.minimum, min(self.maximum, float(value)))
    if self.step and self.step > 0:
      units = round((value - self.minimum) / self.step)
      value = self.minimum + units * self.step
    return max(self.minimum, min(self.maximum, value))


@dataclass
class EvolutionCandidate:
  strategy_name: str
  parameters: Dict[str, Any]
  fitness: Optional[float] = None
  metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvolutionWindow:
  name: str
  start: Optional[str] = None
  end: Optional[str] = None
  weight: float = 1.0


@dataclass
class EvolutionTask:
  task_id: str
  strategy_name: str = "ashare_dynamic_balance_dual_bucket"
  population_size: int = 12
  generations: int = 1
  windows: List[EvolutionWindow] = field(default_factory=list)
  seed: Optional[int] = None
  base_parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GhostDcaResult:
  total_invested: float
  final_asset: float
  shares: int
  cash: float
  total_return: float
  trades: List[Dict[str, Any]] = field(default_factory=list)

  def to_metrics(self) -> Dict[str, Any]:
    return {
      "ghost_dca_invested": self.total_invested,
      "ghost_dca_final_asset": self.final_asset,
      "ghost_dca_shares": self.shares,
      "ghost_dca_cash": self.cash,
      "ghost_dca_return": self.total_return,
      "ghost_dca_trades": len(self.trades),
    }


class GhostDcaBaseline:
  """Conservative fixed-budget DCA baseline for A-share GA comparisons."""

  def __init__(self, *, per_bar_budget: float = 10_000.0, lot_size: int = 100) -> None:
    self.per_bar_budget = float(per_bar_budget or 0.0)
    self.lot_size = int(lot_size or 100)

  def evaluate(self, bars: Iterable[Any]) -> GhostDcaResult:
    cash = 0.0
    shares = 0
    invested = 0.0
    trades: List[Dict[str, Any]] = []
    last_price = 0.0
    for bar in bars:
      price = float(_get(bar, "close", _get(bar, "price", 0.0)) or 0.0)
      if price <= 0:
        continue
      last_price = price
      budget = self.per_bar_budget
      volume = int(budget / price / self.lot_size) * self.lot_size
      if volume <= 0:
        cash += budget
        invested += budget
        continue
      amount = volume * price
      cash += budget - amount
      invested += budget
      shares += volume
      trades.append(
        {
          "timestamp": _get(bar, "timestamp", _get(bar, "time", None)),
          "price": price,
          "volume": volume,
          "amount": amount,
        }
      )
    final_asset = cash + shares * last_price
    total_return = (final_asset - invested) / invested if invested > 0 else 0.0
    return GhostDcaResult(
      total_invested=invested,
      final_asset=final_asset,
      shares=shares,
      cash=cash,
      total_return=total_return,
      trades=trades,
    )


class EvolvableStrategy:
  """Conservative adapter used by GA/champion-challenger workflows."""

  DYNAMIC_BALANCE_PARAMETERS = {
    "ema20_weight": EvolvableParameter("ema20_weight", 0.1, 0.8, 0.05),
    "ema60_weight": EvolvableParameter("ema60_weight", 0.1, 0.7, 0.05),
    "ema120_weight": EvolvableParameter("ema120_weight", 0.0, 0.6, 0.05),
    "balance_beta": EvolvableParameter("balance_beta", 0.5, 4.0, 0.1),
    "inventory_gamma": EvolvableParameter("inventory_gamma", 0.0, 2.0, 0.1),
    "neutral_position_pct": EvolvableParameter("neutral_position_pct", 0.1, 0.6, 0.01),
    "core_base_share": EvolvableParameter("core_base_share", 0.5, 0.95, 0.01),
    "grid_atr_multiplier": EvolvableParameter("grid_atr_multiplier", 0.5, 3.0, 0.05),
  }

  HARD_PARAMETER_LIMITS = {
    "max_position_pct": (0.0, 1.0),
    "min_cash_buffer_pct": (0.0, 0.8),
    "max_new_buy_pct_today": (0.0, 0.2),
  }

  def __init__(
    self,
    strategy_name: str = "ashare_dynamic_balance_dual_bucket",
    *,
    seed: Optional[int] = None,
  ) -> None:
    self.strategy_name = strategy_name
    self.random = random.Random(seed)

  def parameter_space(self) -> Dict[str, EvolvableParameter]:
    if self.strategy_name != "ashare_dynamic_balance_dual_bucket":
      return {}
    return dict(self.DYNAMIC_BALANCE_PARAMETERS)

  def sample(self, base_parameters: Optional[Dict[str, Any]] = None) -> EvolutionCandidate:
    params = dict(base_parameters or {})
    for name, spec in self.parameter_space().items():
      params[name] = spec.clamp(self.random.uniform(spec.minimum, spec.maximum))
    return EvolutionCandidate(strategy_name=self.strategy_name, parameters=params)

  def mutate(
    self,
    candidate: EvolutionCandidate,
    *,
    mutation_rate: float = 0.2,
    strength: float = 0.15,
  ) -> EvolutionCandidate:
    params = dict(candidate.parameters)
    for name, spec in self.parameter_space().items():
      if self.random.random() > mutation_rate:
        continue
      span = spec.maximum - spec.minimum
      current = float(params.get(name, (spec.minimum + spec.maximum) / 2.0))
      params[name] = spec.clamp(current + self.random.uniform(-span, span) * strength)
    return EvolutionCandidate(strategy_name=self.strategy_name, parameters=params)

  def evaluate_result(
    self,
    candidate: EvolutionCandidate,
    metrics: Dict[str, Any],
    *,
    hard_rule_violations: Optional[Dict[str, Any]] = None,
  ) -> EvolutionCandidate:
    violations = dict(hard_rule_violations or {})
    violations.update(self.validate_hard_constraints(candidate.parameters))
    if any(bool(value) for value in violations.values()):
      candidate.fitness = -1_000_000.0
      candidate.metrics = {**dict(metrics or {}), "hard_rule_violations": violations}
      return candidate

    total_return = float(metrics.get("total_return", metrics.get("return", 0.0)) or 0.0)
    max_drawdown = abs(float(metrics.get("max_drawdown", 0.0) or 0.0))
    constraint_penalty = float(metrics.get("constraint_penalty", 0.0) or 0.0)
    fake_fill_penalty = float(metrics.get("fake_fill_penalty", 0.0) or 0.0)
    ghost_excess = float(metrics.get("ghost_dca_excess_return", 0.0) or 0.0)
    sharpe = float(metrics.get("sharpe_ratio", 0.0) or 0.0)
    candidate.fitness = (
      total_return * 100.0
      + ghost_excess * 25.0
      + sharpe * 2.0
      - max_drawdown * 80.0
      - constraint_penalty
      - fake_fill_penalty * 10.0
    )
    candidate.metrics = dict(metrics or {})
    return candidate

  def validate_hard_constraints(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
    violations: Dict[str, Any] = {}
    for name, (minimum, maximum) in self.HARD_PARAMETER_LIMITS.items():
      if name not in parameters:
        continue
      value = float(parameters.get(name) or 0.0)
      if value < minimum or value > maximum:
        violations[name] = {"value": value, "minimum": minimum, "maximum": maximum}
    if bool(parameters.get("allow_sell_locked_core")):
      violations["allow_sell_locked_core"] = "locked_core directional sell is hard-blocked"
    return violations

  def evaluate_windows(
    self,
    candidate: EvolutionCandidate,
    window_metrics: Dict[str, Dict[str, Any]],
    windows: Optional[List[EvolutionWindow]] = None,
  ) -> EvolutionCandidate:
    windows = windows or [EvolutionWindow(name=name) for name in window_metrics]
    total_weight = sum(max(0.0, window.weight) for window in windows) or 1.0
    aggregate: Dict[str, Any] = {"windows": {}}
    score_metrics = {
      "total_return": 0.0,
      "max_drawdown": 0.0,
      "constraint_penalty": 0.0,
      "fake_fill_penalty": 0.0,
      "ghost_dca_excess_return": 0.0,
      "sharpe_ratio": 0.0,
    }
    hard_violations: Dict[str, Any] = {}
    for window in windows:
      metrics = dict(window_metrics.get(window.name, {}) or {})
      weight = max(0.0, window.weight) / total_weight
      aggregate["windows"][window.name] = metrics
      for key in score_metrics:
        value = abs(float(metrics.get(key, 0.0) or 0.0)) if key == "max_drawdown" else float(metrics.get(key, 0.0) or 0.0)
        if key == "max_drawdown":
          score_metrics[key] = max(score_metrics[key], value)
        else:
          score_metrics[key] += value * weight
      if str(metrics.get("data_quality", "OK")).upper() in {"MISSING", "INSUFFICIENT"}:
        hard_violations[f"{window.name}.data_quality"] = metrics.get("data_quality")
    aggregate.update(score_metrics)
    result = self.evaluate_result(candidate, aggregate, hard_rule_violations=hard_violations)
    result.metrics.setdefault("evaluated_at", time_utils.now().isoformat())
    return result

  def champion_challenger_payload(
    self, champion: EvolutionCandidate, challenger: EvolutionCandidate
  ) -> Dict[str, Any]:
    return {
      "strategy_name": self.strategy_name,
      "champion": {
        "parameters": dict(champion.parameters),
        "fitness": champion.fitness,
        "metrics": dict(champion.metrics),
      },
      "challenger": {
        "parameters": dict(challenger.parameters),
        "fitness": challenger.fitness,
        "metrics": dict(challenger.metrics),
      },
      "auto_replace_live": False,
    }


class EvolutionTaskRunner:
  """Small orchestration shell around an external backtest/evaluation callback."""

  def __init__(
    self,
    task: EvolutionTask,
    evaluator: Callable[[EvolutionCandidate, EvolutionWindow], Dict[str, Any]],
  ) -> None:
    self.task = task
    self.evaluator = evaluator
    self.adapter = EvolvableStrategy(strategy_name=task.strategy_name, seed=task.seed)

  def run_generation(self, population: Optional[List[EvolutionCandidate]] = None) -> List[EvolutionCandidate]:
    if population is None:
      population = [
        self.adapter.sample(self.task.base_parameters)
        for _ in range(max(1, self.task.population_size))
      ]
    windows = self.task.windows or [EvolutionWindow(name="default")]
    evaluated: List[EvolutionCandidate] = []
    for candidate in population:
      window_metrics = {
        window.name: self.evaluator(candidate, window)
        for window in windows
      }
      evaluated.append(
        self.adapter.evaluate_windows(candidate, window_metrics, windows=windows)
      )
    return sorted(
      evaluated,
      key=lambda item: item.fitness if item.fitness is not None else -1_000_000.0,
      reverse=True,
    )

  def next_population(self, evaluated: List[EvolutionCandidate]) -> List[EvolutionCandidate]:
    if not evaluated:
      return []
    elite_count = max(1, len(evaluated) // 4)
    elites = evaluated[:elite_count]
    next_population = list(elites)
    while len(next_population) < max(1, self.task.population_size):
      parent = self.adapter.random.choice(elites)
      next_population.append(self.adapter.mutate(parent))
    return next_population


def _get(source: Any, key: str, default: Any = None) -> Any:
  if source is None:
    return default
  if isinstance(source, dict):
    return source.get(key, default)
  return getattr(source, key, default)
