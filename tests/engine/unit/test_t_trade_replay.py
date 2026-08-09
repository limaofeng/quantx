from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import OrderType, Position, TradeRecord
from quantx_domain.strategies.base import (
  StrategyContext,
  StrategyOutput,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_engine.strategy_executor import (
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.t_trade_replay_metrics import (
  build_t_trade_replay_metrics,
)
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
  assert instruments["000001.SZ"]["status"] == "OK"
  assert instruments["000002.SZ"]["status"] == "NO_TRADE"
  assert instruments["000003.SZ"]["status"] == "DATA_INSUFFICIENT"
