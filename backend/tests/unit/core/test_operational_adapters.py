"""Tests for non-semantic operational adapters."""

from datetime import datetime, timedelta

import pytest

from core.brokers.base import OrderRequest, OrderType, PriceType
from core.evolution import (
  EvolvableStrategy,
  EvolutionCandidate,
  EvolutionTask,
  EvolutionTaskRunner,
  EvolutionWindow,
  GhostDcaBaseline,
)
from miniqmt.local_agent import LocalAgentStatus, MiniQmtLocalAgent


pytestmark = pytest.mark.unit


class FakeTradingManager:
  is_connected = True

  def __init__(self):
    self.account = {"cash": 1000}
    self.orders = [{"order_id": "o1", "status": "REPORTED"}]
    self.trades = []
    self.positions = [{"stock_code": "000001.SZ", "volume": 100}]
    self.placed = []
    self.cancelled = []

  def get_account_info(self):
    return self.account

  def get_orders(self):
    return self.orders

  def get_trades(self):
    return self.trades

  def get_positions(self):
    return self.positions

  def place_order(
    self,
    stock_code,
    order_type,
    order_volume,
    price_type,
    price,
    strategy_name="",
    order_remark="",
  ):
    payload = {
      "stock_code": stock_code,
      "order_type": order_type,
      "order_volume": order_volume,
      "price_type": price_type,
      "price": price,
      "strategy_name": strategy_name,
      "order_remark": order_remark,
    }
    self.placed.append(payload)
    return {"success": True, "order_id": 123, **payload}

  def cancel_order(self, order_id):
    self.cancelled.append(order_id)
    return True


def test_local_agent_reconcile_requires_full_snapshot_match():
  manager = FakeTradingManager()
  agent = MiniQmtLocalAgent(manager)

  expected = agent.full_snapshot()
  manager.positions = [{"stock_code": "000001.SZ", "volume": 200}]
  report = agent.reconcile_snapshots(expected)

  assert report.status == LocalAgentStatus.RECONCILE_REQUIRED
  assert report.position_delta


def test_local_agent_kill_switch_when_reports_are_stale():
  agent = MiniQmtLocalAgent(FakeTradingManager(), max_report_lag_seconds=1)
  agent.last_report_time = datetime.now() - timedelta(seconds=5)

  assert agent.preflight_check()["status"] == LocalAgentStatus.KILL_SWITCH.value


def test_local_agent_places_and_cancels_orders_through_preflight():
  manager = FakeTradingManager()
  agent = MiniQmtLocalAgent(manager)

  result = agent.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=10.0,
      strategy_id="strategy",
      metadata={"order_remark": "intent-1"},
    )
  )
  cancel_result = agent.cancel_order("123")

  assert result["success"] is True
  assert result["stock_code"] == "000001.SZ"
  assert manager.placed[0]["order_volume"] == 100
  assert cancel_result["success"] is True
  assert manager.cancelled == [123]


def test_evolvable_strategy_stays_inside_declared_parameter_bounds():
  adapter = EvolvableStrategy(seed=42)
  candidate = adapter.sample()
  mutated = adapter.mutate(candidate, mutation_rate=1.0, strength=10.0)
  space = adapter.parameter_space()

  for name, value in mutated.parameters.items():
    if name in space:
      assert space[name].minimum <= value <= space[name].maximum


def test_evolvable_strategy_penalizes_hard_rule_violations():
  adapter = EvolvableStrategy(seed=42)
  candidate = EvolutionCandidate(
    strategy_name="ashare_dynamic_balance_dual_bucket",
    parameters={"max_position_pct": 0.7},
  )
  result = adapter.evaluate_result(
    candidate,
    {"total_return": 1.0, "max_drawdown": 0.01},
    hard_rule_violations={"locked_core_sold": True},
  )

  assert result.fitness == -1_000_000.0


def test_ghost_dca_baseline_uses_a_share_lots():
  result = GhostDcaBaseline(per_bar_budget=1_000).evaluate(
    [
      {"timestamp": "2024-01-02", "close": 9.0},
      {"timestamp": "2024-01-03", "close": 11.0},
    ]
  )

  assert result.shares == 100
  assert result.total_invested == 2_000
  assert result.final_asset == pytest.approx(2_200)
  assert result.to_metrics()["ghost_dca_trades"] == 1


def test_evolution_task_runner_ranks_multi_window_candidates():
  task = EvolutionTask(
    task_id="task",
    population_size=2,
    windows=[EvolutionWindow(name="bull"), EvolutionWindow(name="bear")],
    seed=7,
  )

  def evaluator(candidate, window):
    return {
      "total_return": 0.1 if window.name == "bull" else 0.02,
      "max_drawdown": 0.01,
      "sharpe_ratio": 1.0,
      "ghost_dca_excess_return": 0.01,
    }

  evaluated = EvolutionTaskRunner(task, evaluator).run_generation()

  assert len(evaluated) == 2
  assert evaluated[0].fitness is not None
  assert set(evaluated[0].metrics["windows"]) == {"bull", "bear"}


def test_evolvable_strategy_rejects_hard_constraint_breakout():
  adapter = EvolvableStrategy(seed=42)
  candidate = EvolutionCandidate(
    strategy_name="ashare_dynamic_balance_dual_bucket",
    parameters={"max_position_pct": 1.5},
  )

  result = adapter.evaluate_result(candidate, {"total_return": 1.0})

  assert result.fitness == -1_000_000.0
  assert "max_position_pct" in result.metrics["hard_rule_violations"]
