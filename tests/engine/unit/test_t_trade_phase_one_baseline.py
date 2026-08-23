from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.base import (
  MarketDataContext,
  MarketDataSession,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
)
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_engine.t_trade_phase_one_baseline import (
  TTradePhaseOneBaselineAccumulator,
)
from quantx_infrastructure.core.t_trade_replay_metrics import (
  build_t_trade_replay_metrics,
)


def _input(
  second: int,
  price: float,
  *,
  ordinal: int,
  amount: float,
  pvolume: float,
  generation: int = 1,
  cadence: StrategyCadence = StrategyCadence.TICK,
) -> StrategyInput:
  timestamp = datetime(2026, 8, 21, 9, 30) + timedelta(seconds=second)
  source_time_ms = 1_000_000 + second * 1_000
  tick = SimpleNamespace(
    last_price=price,
    bid_price=[price - 0.01],
    ask_price=[price],
    bid_vol=[1000],
    ask_vol=[1000],
    amount=amount,
    pvolume=pvolume,
    stock_status=0,
  )
  return StrategyInput(
    run_id="run-phase-one",
    strategy_id="strategy-1",
    timestamp=timestamp,
    cadence=cadence,
    instrument_code="600000.SH",
    market_data_context=MarketDataContext(
      source="REPLAY",
      stream_id="replay-1",
      continuity_generation=generation,
      source_sequence=ordinal,
      source_time_ms=source_time_ms,
      tick_ordinal=ordinal,
      received_at_ms=source_time_ms,
      quote_stale=False,
      session=MarketDataSession.CONTINUOUS_AM,
      trade_date=date(2026, 8, 21),
    ),
    event=tick,
    market_context={"price_tick": 0.01},
  )


def _seed_pullback(accumulator: TTradePhaseOneBaselineAccumulator) -> None:
  assert accumulator.observe(_input(0, 100.0, ordinal=1, amount=100_000, pvolume=1_000))
  assert accumulator.observe(_input(1, 99.0, ordinal=2, amount=109_900, pvolume=1_100))
  evaluation = accumulator.observe(
    _input(16, 99.3, ordinal=3, amount=119_830, pvolume=1_200)
  )
  assert evaluation is not None and evaluation.trigger_edge is True


def test_accumulator_matures_candidate_reference_windows_without_fake_fees() -> None:
  accumulator = TTradePhaseOneBaselineAccumulator("run-phase-one")
  _seed_pullback(accumulator)

  ordinal = 4
  amount = 119_830.0
  pvolume = 1_200.0
  for second in range(76, 917, 60):
    amount += 10_000
    pvolume += 100
    price = 100.0 if second == 76 else 98.5 if second == 916 else 99.4
    accumulator.observe(
      _input(
        second,
        price,
        ordinal=ordinal,
        amount=amount,
        pvolume=pvolume,
      )
    )
    ordinal += 1

  accumulator.finalize(1_000_000 + 916_000)
  snapshot = accumulator.snapshot()
  performance = snapshot["candidate_reference_performance"]
  assert performance["candidate_count"] >= 1
  original = next(
    item for item in performance["items"] if item["source_time_ms"] == 1_016_000
  )
  assert original["status"] == "MATURED"
  assert all(item["return_pct"] is not None for item in original["horizons"])
  assert [item["horizon_seconds"] for item in performance["fixed_windows"]] == [
    60,
    300,
    900,
  ]
  assert all(item["sample_count"] >= 1 for item in performance["fixed_windows"])
  assert snapshot["fee_adjusted_performance"]["available"] is False
  assert (
    snapshot["fee_adjusted_performance"]["reason_code"]
    == "SHADOW_BASELINE_NOT_EXECUTED"
  )


def test_accumulator_continuity_change_fails_open_candidate_closed() -> None:
  accumulator = TTradePhaseOneBaselineAccumulator("run-phase-one")
  _seed_pullback(accumulator)
  accumulator.observe(
    _input(
      17,
      99.4,
      ordinal=4,
      amount=129_770,
      pvolume=1_300,
      generation=2,
    )
  )
  snapshot = accumulator.snapshot()
  performance = snapshot["candidate_reference_performance"]
  assert performance["status_counts"] == {"UNAVAILABLE": 1}
  assert performance["unavailable_reason_counts"] == {"CONTINUITY_CHANGED": 1}


def test_accumulator_reports_rule_intersections_and_ready_time() -> None:
  accumulator = TTradePhaseOneBaselineAccumulator("run-phase-one")
  _seed_pullback(accumulator)
  accumulator.observe(_input(17, 99.31, ordinal=4, amount=129_761, pvolume=1_300))
  snapshot = accumulator.snapshot()
  assert snapshot["evaluations_total"] == 4
  assert snapshot["candidate_edges"] == {"PULLBACK_REBOUND": 1}
  assert snapshot["raw_trigger_observations"]["PULLBACK_REBOUND"] == 2
  assert (
    snapshot["condition_passes"]["PULLBACK_REBOUND"]["PULLBACK_DEPTH_AT_LEAST"] == 2
  )
  assert snapshot["denominator"] == {
    "code": "BASELINE_DATA_READY_INSTRUMENT_SECONDS",
    "value": 1.0,
  }


def test_accumulator_compares_candidates_only_on_common_ready_exposure() -> None:
  accumulator = TTradePhaseOneBaselineAccumulator("run-phase-one")
  accumulator.observe(
    _input(0, 100.0, ordinal=1, amount=100_000, pvolume=1_000),
    v3_data_ready=True,
  )
  accumulator.observe(
    _input(1, 99.0, ordinal=2, amount=109_900, pvolume=1_100),
    v3_data_ready=True,
  )
  triggered = accumulator.observe(
    _input(16, 99.3, ordinal=3, amount=119_830, pvolume=1_200),
    v3_data_ready=True,
    v3_candidate_path="PULLBACK_REBOUND",
  )
  assert triggered is not None and triggered.trigger_edge is True
  accumulator.observe(
    _input(17, 99.31, ordinal=4, amount=129_761, pvolume=1_300),
    v3_data_ready=True,
  )

  comparison = accumulator.snapshot()["common_ready_comparison"]
  assert comparison["available"] is True
  assert comparison["denominator"] == {
    "code": "COMMON_READY_INSTRUMENT_SECONDS",
    "value": 1.0,
  }
  assert comparison["v3_candidate_edges"] == {"PULLBACK_REBOUND": 1}
  assert comparison["phase_one_candidate_edges"] == {"PULLBACK_REBOUND": 1}


def test_common_ready_comparison_excludes_one_sided_ready_ticks() -> None:
  accumulator = TTradePhaseOneBaselineAccumulator("run-phase-one")
  accumulator.observe(
    _input(0, 100.0, ordinal=1, amount=100_000, pvolume=1_000),
    v3_data_ready=False,
  )
  accumulator.observe(
    _input(1, 99.0, ordinal=2, amount=109_900, pvolume=1_100),
    v3_data_ready=False,
  )
  accumulator.observe(
    _input(16, 99.3, ordinal=3, amount=119_830, pvolume=1_200),
    v3_data_ready=False,
    v3_candidate_path="PULLBACK_REBOUND",
  )
  comparison = accumulator.snapshot()["common_ready_comparison"]
  assert comparison["available"] is False
  assert comparison["denominator"]["value"] == 0.0
  assert comparison["v3_candidate_edges"] == {}
  assert comparison["phase_one_candidate_edges"] == {}


def test_executor_reads_v3_comparison_fact_only_from_same_source_identity() -> None:
  strategy_input = _input(
    16,
    99.3,
    ordinal=3,
    amount=119_830,
    pvolume=1_200,
  )
  context = strategy_input.market_data_context
  evaluation = {
    "source_time_ms": context.source_time_ms,
    "tick_ordinal": context.tick_ordinal,
    "continuity_generation": str(context.continuity_generation),
    "data_health": "READY",
    "candidate_id": "candidate-1",
    "candidate_created_at_ms": context.source_time_ms,
    "selected_path": "PULLBACK_REBOUND",
  }
  runtime = SimpleNamespace(
    strategy=SimpleNamespace(
      state={
        "instrument_states": {
          "600000.SH": {
            "opportunity": {"latest_evaluation": evaluation},
          }
        }
      }
    )
  )

  assert StrategyExecutor._t_trade_phase_one_v3_comparison_fact(
    runtime,
    strategy_input,
  ) == (True, "PULLBACK_REBOUND")

  evaluation["tick_ordinal"] = context.tick_ordinal - 1
  assert StrategyExecutor._t_trade_phase_one_v3_comparison_fact(
    runtime,
    strategy_input,
  ) == (None, None)


def test_accumulator_ignores_non_tick_inputs_and_rejects_cross_run() -> None:
  accumulator = TTradePhaseOneBaselineAccumulator("run-phase-one")
  assert (
    accumulator.observe(
      _input(
        0,
        100.0,
        ordinal=1,
        amount=100_000,
        pvolume=1_000,
        cadence=StrategyCadence.BAR,
      )
    )
    is None
  )
  wrong = _input(0, 100.0, ordinal=1, amount=100_000, pvolume=1_000)
  wrong.run_id = "other-run"
  try:
    accumulator.observe(wrong)
  except ValueError as exc:
    assert "跨策略运行" in str(exc)
  else:
    raise AssertionError("cross-run baseline input must be rejected")


def test_executor_initializes_baseline_only_for_strict_t_trade_replay() -> None:
  executor = StrategyExecutor()

  def runtime(mode: StrategyRunMode, replay: bool) -> SimpleNamespace:
    return SimpleNamespace(
      run_id=f"{mode.value}-{replay}",
      t_trade_phase_one_baseline=object(),
      context=SimpleNamespace(
        mode=mode,
        parameters={"t_trade_replay": replay},
      ),
    )

  strict = runtime(StrategyRunMode.BACKTEST, True)
  executor._initialize_t_trade_phase_one_baseline(strict)
  assert isinstance(
    strict.t_trade_phase_one_baseline,
    TTradePhaseOneBaselineAccumulator,
  )

  for item in (
    runtime(StrategyRunMode.BACKTEST, False),
    runtime(StrategyRunMode.PAPER, True),
    runtime(StrategyRunMode.LIVE, True),
  ):
    executor._initialize_t_trade_phase_one_baseline(item)
    assert item.t_trade_phase_one_baseline is None


@pytest.mark.asyncio
async def test_executor_observes_baseline_after_strategy_output(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  strategy_input = _input(
    0,
    100.0,
    ordinal=1,
    amount=100_000,
    pvolume=1_000,
  )
  tick = strategy_input.event
  tick.stock_code = "600000.SH"
  tick.time = strategy_input.timestamp
  order: list[str] = []

  async def step(_input: StrategyInput) -> StrategyOutput:
    order.append("step")
    return StrategyOutput()

  async def process_output(*_args, **_kwargs) -> None:
    order.append("output")

  baseline = SimpleNamespace(
    observe=lambda _input, **_comparison: order.append("baseline")
  )
  context = StrategyContext(
    run_id="run-phase-one",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters={"t_trade_replay": True},
    current_time=strategy_input.timestamp,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="phase-one-order",
    strategy_id=1,
    strategy_class=object,
    context=context,
    strategy=SimpleNamespace(step=step),
    broker=SimpleNamespace(),
    status=ExecutionStatus.RUNNING,
    t_trade_phase_one_baseline=baseline,
  )
  monkeypatch.setattr(
    executor, "_build_strategy_input", lambda *_args, **_kwargs: strategy_input
  )
  monkeypatch.setattr(executor, "_process_strategy_output", process_output)
  monkeypatch.setattr(executor, "_expire_pending_approvals", AsyncMock())
  monkeypatch.setattr(executor, "_cancel_expired_strategy_orders", AsyncMock())
  monkeypatch.setattr(executor, "_process_auto_exit_plans", AsyncMock())
  monkeypatch.setattr(executor, "_board_replay_report_barrier", AsyncMock())
  monkeypatch.setattr(executor, "_ensure_t_trade_opportunity_profile", AsyncMock())
  monkeypatch.setattr(executor, "_observe_t_trade_candidate_outcomes", AsyncMock())
  monkeypatch.setattr(executor, "_report_t_trade_replay_progress", AsyncMock())

  await executor._process_tick(runtime, tick)

  assert order == ["step", "output", "baseline"]


def test_executor_finalizes_baseline_before_metrics_snapshot() -> None:
  executor = StrategyExecutor()
  start = datetime(2026, 8, 21, 9, 30)
  end = datetime(2026, 8, 21, 15, 0)
  context = StrategyContext(
    run_id="run-phase-one",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters={"t_trade_replay": True},
    backtest_start_time=start,
    backtest_end_time=end,
    current_time=end,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="phase-one-finalize",
    strategy_id=1,
    strategy_class=object,
    context=context,
    broker=SimpleNamespace(
      initial_capital=100_000.0,
      current_time=end,
      trades=[],
      replay_curve=[],
      get_performance_metrics=lambda: {"max_drawdown_pct": 0.0},
    ),
  )
  executor._initialize_t_trade_phase_one_baseline(runtime)
  assert runtime.t_trade_phase_one_baseline is not None
  _seed_pullback(runtime.t_trade_phase_one_baseline)

  executor._finalize_t_trade_phase_one_baseline(runtime)
  metrics = build_t_trade_replay_metrics(runtime)

  baseline = metrics["phase_one_baseline"]
  assert baseline["available"] is True
  assert baseline["finalized_at_ms"] == int(end.timestamp() * 1000)
  assert baseline["candidate_reference_performance"]["status_counts"] == {
    "UNAVAILABLE": 1
  }


@pytest.mark.asyncio
async def test_strategy_loop_finalizes_baseline_before_liquidation_and_metrics(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="phase-one-finalize-order",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters={"t_trade_replay": True},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="phase-one-finalize-order",
    strategy_id=1,
    strategy_class=object,
    context=context,
    status=ExecutionStatus.STOPPED,
  )
  order: list[str] = []

  async def replay(_runtime: StrategyRuntime) -> None:
    order.append("tick_replay")

  def baseline(_runtime: StrategyRuntime) -> None:
    order.append("baseline_finalize")

  async def liquidation(_runtime: StrategyRuntime) -> None:
    order.append("forced_liquidation")

  async def outcome(_runtime: StrategyRuntime) -> None:
    order.append("outcome_finalize")

  monkeypatch.setattr(executor, "_run_backtest_loop", replay)
  monkeypatch.setattr(executor, "_finalize_t_trade_phase_one_baseline", baseline)
  monkeypatch.setattr(executor, "_finalize_t_trade_replay", liquidation)
  monkeypatch.setattr(executor, "_finalize_t_trade_candidate_outcomes", outcome)

  await executor._run_strategy_loop(runtime)

  assert order == [
    "tick_replay",
    "baseline_finalize",
    "forced_liquidation",
    "outcome_finalize",
  ]
