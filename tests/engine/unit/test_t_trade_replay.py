import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import OrderType, Position, TradeRecord
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  StrategyContext,
  StrategyOutput,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.t_trade_replay_metrics import (
  build_t_trade_replay_metrics,
)
from quantx_infrastructure.core.t_trade_replay_report import (
  write_t_trade_replay_report,
)
from quantx_infrastructure.models import ExecutionMetrics
from quantx_infrastructure.models.enums import StrategyRunMode


def _trade(
  *,
  trade_id: str,
  batch_id: str,
  stock_code: str,
  side: OrderType,
  price: float,
  volume: int,
  fee: float,
  minute: int,
) -> TradeRecord:
  return TradeRecord(
    trade_id=trade_id,
    order_id=f"order-{trade_id}",
    instrument_code=stock_code,
    trade_type=side,
    price=price,
    volume=volume,
    amount=price * volume,
    commission=fee,
    trade_time=datetime(2024, 1, 2, 10, minute),
    metadata={
      "t_batch_id": batch_id,
      "costs": {
        "commission": fee,
        "stamp_tax": 0.0,
        "transfer_fee": 0.0,
        "total": fee,
      },
    },
  )


def test_backtest_broker_applies_full_ashare_costs() -> None:
  broker = BacktestBroker(
    commission_rate=0.0003,
    min_commission=5.0,
    stamp_tax_rate=0.0005,
    transfer_fee_rate=0.00001,
  )

  buy = broker._calculate_costs(1_000.0, OrderType.BUY)
  sell = broker._calculate_costs(1_000.0, OrderType.SELL)

  assert buy == pytest.approx(
    {
      "commission": 5.0,
      "transfer_fee": 0.01,
      "stamp_tax": 0.0,
      "total": 5.01,
    }
  )
  assert sell == pytest.approx(
    {
      "commission": 5.0,
      "transfer_fee": 0.01,
      "stamp_tax": 0.5,
      "total": 5.51,
    }
  )


@pytest.mark.asyncio
async def test_initial_portfolio_uses_total_asset_and_passive_baseline() -> None:
  broker = BacktestBroker(initial_capital=100_000.0)
  broker.positions["000001.SZ"] = Position(
    instrument_code="000001.SZ",
    long_volume=1_000,
    available_volume=1_000,
    long_avg_price=10.0,
    last_price=10.0,
    market_value=10_000.0,
  )
  broker.configure_initial_portfolio(
    cash=90_000.0,
    total_asset=100_000.0,
    positions=broker.positions,
  )

  await broker.update_market_data(
    "000001.SZ",
    11.0,
    datetime(2024, 1, 2, 10, 0),
  )

  assert broker.initial_capital == 100_000.0
  assert broker.replay_curve[-1]["equity"] == 101_000.0
  assert broker.replay_curve[-1]["passive_equity"] == 101_000.0


@pytest.mark.asyncio
async def test_replay_auto_confirms_manual_intent_through_executor(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="replay-run",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"auto_approve_manual_intents": True},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=context.run_id,
    instrument_code="000001.SZ",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="test",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
  )
  approved = []

  async def approve(run_id: str, intent_id: str):
    approved.append((run_id, intent_id))
    return {"success": True, "code": "APPROVED"}

  monkeypatch.setattr(executor, "approve_trade_intent", approve)
  monkeypatch.setattr(executor, "_runtime_log", lambda *args, **kwargs: None)

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(trade_intents=[intent]),
  )

  assert approved == [(context.run_id, intent.intent_id)]
  assert runtime.pending_approvals[intent.intent_id] is intent


def test_replay_metrics_group_cycles_and_keep_no_trade_and_skipped_symbols() -> None:
  broker = SimpleNamespace(
    initial_capital=100_000.0,
    trades=[
      _trade(
        trade_id="1",
        batch_id="batch-complete",
        stock_code="000001.SZ",
        side=OrderType.BUY,
        price=10.0,
        volume=100,
        fee=5.01,
        minute=0,
      ),
      _trade(
        trade_id="2",
        batch_id="batch-complete",
        stock_code="000001.SZ",
        side=OrderType.SELL,
        price=11.0,
        volume=100,
        fee=5.56,
        minute=5,
      ),
      _trade(
        trade_id="3",
        batch_id="batch-open",
        stock_code="000001.SZ",
        side=OrderType.BUY,
        price=10.5,
        volume=100,
        fee=5.01,
        minute=10,
      ),
    ],
    replay_curve=[
      {
        "timestamp": datetime(2024, 1, 2, 15, 0),
        "equity": 100_100.0,
        "passive_equity": 100_000.0,
      }
    ],
    get_performance_metrics=lambda: {"max_drawdown_pct": 1.25},
  )
  runtime = SimpleNamespace(
    broker=broker,
    context=SimpleNamespace(
      parameters={
        "initial_total_asset": 100_000.0,
        "initial_positions": [
          {"stock_code": "000001.SZ", "instrument_name": "平安银行"},
          {"stock_code": "000002.SZ", "instrument_name": "万科A"},
        ],
        "initial_instrument_metadata": {
          "000001.SZ": {"instrument_name": "平安银行"},
          "000002.SZ": {"instrument_name": "万科A"},
        },
        "replay_skipped_instruments": [
          {
            "stock_code": "000003.SZ",
            "instrument_name": "测试标的",
            "reason": "历史 Tick 数据不足",
          }
        ],
      }
    ),
  )

  metrics = build_t_trade_replay_metrics(runtime)

  assert metrics["data_quality"] == "PARTIAL"
  assert metrics["summary"]["completed_cycles"] == 1
  assert metrics["summary"]["open_cycles"] == 1
  assert metrics["summary"]["winning_cycles"] == 1
  assert metrics["summary"]["t_net_profit"] == 100.0
  assert metrics["cycles"][0]["net_profit"] == pytest.approx(89.43)
  assert metrics["cycles"][1]["status"] == "OPEN"
  instruments = {item["stock_code"]: item for item in metrics["instruments"]}
  assert instruments["000001.SZ"]["status"] == "LIQUIDATION_FAILED"
  assert instruments["000002.SZ"]["status"] == "NO_TRADE"
  assert instruments["000003.SZ"]["status"] == "DATA_INSUFFICIENT"


def _metrics_runtime(exit_time: datetime):
  entry_time = datetime(2024, 1, 2, 10, 0)
  broker = SimpleNamespace(
    initial_capital=100_000.0,
    current_time=exit_time,
    trades=[
      TradeRecord(
        trade_id="entry",
        order_id="order-entry",
        instrument_code="000001.SZ",
        trade_type=OrderType.BUY,
        price=10.0,
        volume=100,
        amount=1_000.0,
        commission=5.0,
        trade_time=entry_time,
        metadata={"t_batch_id": "batch"},
      ),
      TradeRecord(
        trade_id="exit",
        order_id="order-exit",
        instrument_code="000001.SZ",
        trade_type=OrderType.SELL,
        price=10.2,
        volume=100,
        amount=1_020.0,
        commission=5.0,
        trade_time=exit_time,
        metadata={"t_batch_id": "batch", "exit_reason": "TARGET"},
      ),
    ],
    replay_curve=[
      {
        "timestamp": exit_time,
        "equity": 100_010.0,
        "passive_equity": 100_000.0,
      }
    ],
    get_performance_metrics=lambda: {"max_drawdown_pct": 0.0},
  )
  return SimpleNamespace(
    broker=broker,
    context=SimpleNamespace(
      backtest_start_time=entry_time,
      backtest_end_time=exit_time,
      parameters={
        "initial_total_asset": 100_000.0,
        "initial_cash": 50_000.0,
        "max_total_t_exposure_pct": 0.1,
        "replay_start_time": entry_time.isoformat(),
        "replay_end_time": exit_time.isoformat(),
      },
    ),
  )


def test_capital_utilization_falls_when_exit_wait_is_longer() -> None:
  four_hours = build_t_trade_replay_metrics(
    _metrics_runtime(datetime(2024, 1, 2, 14, 0))
  )
  twenty_hours = build_t_trade_replay_metrics(
    _metrics_runtime(datetime(2024, 1, 3, 6, 0))
  )

  assert four_hours["summary"]["capital_utilization_pct"] == pytest.approx(100.0)
  assert twenty_hours["summary"]["capital_utilization_pct"] == pytest.approx(20.0)
  assert (
    twenty_hours["summary"]["capital_utilization_pct"]
    < four_hours["summary"]["capital_utilization_pct"]
  )


def test_completed_replay_writes_versioned_html_and_json_report(tmp_path) -> None:
  run_dir = tmp_path / "backtests" / "run-1" / "v1"
  run_dir.mkdir(parents=True)
  manifest_path = run_dir / "manifest.json"
  manifest_path.write_text(
    '{"schema_version": 3, "artifacts": {"execution_summary": "execution_summary.jsonl"}}',
    encoding="utf-8",
  )
  metrics = build_t_trade_replay_metrics(_metrics_runtime(datetime(2024, 1, 2, 14, 0)))

  report = write_t_trade_replay_report(
    str(manifest_path),
    metrics,
    run_id="run-1",
    backtest_id="backtest-1",
    start_time=datetime(2024, 1, 2, 10, 0),
    end_time=datetime(2024, 1, 2, 14, 0),
  )

  assert report["status"] == "GENERATED"
  assert report["conclusion_code"] == "INSUFFICIENT_SAMPLE"
  assert (
    (run_dir / "t-trade-report.html")
    .read_text(encoding="utf-8")
    .startswith("<!doctype html>")
  )
  assert (run_dir / "t-trade-report.json").is_file()
  manifest = manifest_path.read_text(encoding="utf-8")
  assert '"t_trade_report_html": "t-trade-report.html"' in manifest


def test_backtest_end_exit_intent_uses_market_and_keeps_batch_attribution() -> None:
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="replay-run",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  plan = SimpleNamespace(
    plan_id="plan-1",
    remaining_volume=100,
    template=SimpleNamespace(
      strategy_id="1",
      run_id=context.run_id,
      instrument_code="000001.SZ",
      bucket="swing",
      source_type="T_TRADE_BATCH",
      source_id="batch-1",
      config_version=1,
      t1_policy=SimpleNamespace(value="ALLOW_SAME_INSTRUMENT_SUBSTITUTION"),
      execution=SimpleNamespace(max_slippage_bps=30.0),
      metadata={"t_batch_id": "batch-1"},
    ),
  )
  market_data = SimpleNamespace(price=10.0, close=10.0, bid_price=[9.99])

  decision, intent = executor._build_backtest_end_exit_intent(
    runtime,
    plan,
    market_data,
  )

  assert decision.volume == 100
  assert intent.direction is TradeIntentDirection.SELL
  assert intent.metadata["price_type"] == "MARKET"
  assert intent.metadata["backtest_forced_close"] is True
  assert intent.metadata["t_batch_id"] == "batch-1"
  assert intent.metadata["allow_t1_substitution"] is True


@pytest.mark.asyncio
async def test_replay_finalizer_closes_open_batch_through_broker_reports() -> None:
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="replay-finalize",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={
      "t_trade_replay": True,
      "account_id": "backtest",
      "max_total_t_exposure_pct": 0.1,
    },
    initial_capital=100_000.0,
    current_time=datetime(2024, 1, 2, 14, 55),
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  broker = BacktestBroker(initial_capital=100_000.0)
  broker.positions["000001.SZ"] = Position(
    instrument_code="000001.SZ",
    long_volume=1_100,
    available_volume=1_000,
    today_buy_volume=100,
    long_avg_price=10.0,
    last_price=10.0,
    market_value=11_000.0,
  )
  broker.cash = 89_000.0
  await broker.update_market_data(
    "000001.SZ",
    10.0,
    context.current_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    broker=broker,
    status=ExecutionStatus.RUNNING,
    metrics=ExecutionMetrics(
      start_time=context.current_time,
      last_heartbeat=context.current_time,
    ),
  )
  runtime.latest_market_data["000001.SZ"] = broker.market_snapshots["000001.SZ"]
  template = strategy.build_exit_plan_template(
    instrument_code="000001.SZ",
    batch_id="batch-finalize",
    plan_id="plan-finalize",
  )
  plan = runtime.exit_plan_book.register_entry_fill(
    template,
    volume=100,
    price=10.0,
    trade_time=datetime(2024, 1, 2, 10, 0),
  )
  broker.subscribe_order_updates(
    lambda order: runtime.event_queue.put_nowait(("order", order))
  )
  broker.subscribe_trade_updates(
    lambda trade: runtime.event_queue.put_nowait(("trade", trade))
  )
  event_task = asyncio.create_task(executor._process_event_queue(runtime))

  try:
    await executor._finalize_t_trade_replay(runtime)
  finally:
    runtime.status = ExecutionStatus.COMPLETED
    event_task.cancel()
    await asyncio.gather(event_task, return_exceptions=True)

  assert plan.remaining_volume == 0
  assert broker.positions["000001.SZ"].long_volume == 1_000
  forced_trade = broker.trades[-1]
  assert forced_trade.trade_type is OrderType.SELL
  assert forced_trade.metadata["backtest_forced_close"] is True
  assert forced_trade.metadata["t_batch_id"] == "batch-finalize"
  assert context.parameters["replay_forced_liquidation"]["failed_cycles"] == 0
