import asyncio
import copy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_engine.strategy_executor as strategy_executor_module
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderStatus,
  OrderType,
  Position,
  PriceType,
  TradeRecord,
)
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading import MarketDataSnapshot
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
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models import ExecutionMetrics
from quantx_infrastructure.models.enums import StrategyRunMode

_MISSING_SOURCE_TIME_MS = object()


def _tick_book(timestamp: datetime, *, price: float = 10.0) -> MarketDataSnapshot:
  return MarketDataSnapshot(
    instrument_code="000001.SZ",
    timestamp=timestamp,
    price=price,
    high=price,
    low=price,
    volume=100_000,
    limit_up=11.0,
    limit_down=9.0,
    ask_price=[10.01, 10.02, 10.03, 10.04, 10.05],
    ask_vol=[100, 100, 100, 100, 100],
    bid_price=[9.99, 9.98, 9.97, 9.96, 9.95],
    bid_vol=[100, 100, 100, 100, 100],
    source="tick",
  )


@pytest.mark.asyncio
async def test_replay_initial_cash_reaches_order_sizer_and_risk_before_broker(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Replay holdings and asset residual must never become spendable cash."""

  adapter = AsyncMock()
  adapter.connect = AsyncMock(return_value=True)
  monkeypatch.setattr(
    strategy_executor_module.adapter_manager,
    "get_adapter_for_mode",
    AsyncMock(return_value=adapter),
  )
  monkeypatch.setattr(
    strategy_executor_module.adapter_manager,
    "ensure_adapter_connected_for_mode",
    AsyncMock(return_value=True),
  )
  monkeypatch.setattr(
    strategy_executor_module.adapter_manager,
    "release_adapter_for_mode",
    AsyncMock(),
  )
  executor = StrategyExecutor()
  monkeypatch.setattr(
    StrategyExecutor,
    "_runtime_state_persistence_enabled",
    staticmethod(lambda _runtime: False),
  )

  async def keep_running(_runtime: StrategyRuntime) -> None:
    await asyncio.Event().wait()

  monkeypatch.setattr(executor, "_run_strategy_loop", keep_running)
  observed_sizer_account = {}
  original_draft_intent = strategy_executor_module.OrderSizer.draft_intent

  def capture_sizer_account(self, intent, order_type, price, account, position=None):
    observed_sizer_account.update(account)
    return original_draft_intent(
      self,
      intent,
      order_type,
      price,
      account,
      position,
    )

  monkeypatch.setattr(
    strategy_executor_module.OrderSizer,
    "draft_intent",
    capture_sizer_account,
  )
  current_time = datetime(2024, 1, 2, 10, 0)
  metadata = {
    "000001.SZ": {
      "position_shares": 3_300,
      "position_available_shares": 3_300,
      "position_frozen_shares": 0,
      "position_avg_price": 10.0,
      "position_market_value": 33_000.0,
    }
  }
  context = StrategyContext(
    run_id="replay-initial-cash-risk",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={
      "t_trade_replay": True,
      "account_id": "account-1",
      "initial_cash": 2_383.96,
      "initial_total_asset": 40_000.0,
      "initial_portfolio_metadata": metadata,
      "initial_instrument_metadata": metadata,
      "max_total_t_exposure_pct": 1.0,
    },
    initial_capital=40_000.0,
    current_time=current_time,
    backtest_start_time=current_time,
    backtest_end_time=datetime(2024, 1, 2, 15, 0),
  )
  runtime = executor.create(
    run_id=context.run_id,
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )

  try:
    assert await executor.start(runtime.run_id) is True
    account = runtime.state_manager.get_account()
    quota = runtime.state_manager.get_account_quota()
    assert account["cash"] == pytest.approx(2_383.96)
    assert account["total_asset"] == pytest.approx(40_000.0)
    assert account["non_trading_asset"] == pytest.approx(4_616.04)
    assert quota["available_cash"] == pytest.approx(2_383.96)
    assert quota["total_asset"] == pytest.approx(40_000.0)
    assert runtime.broker.cash == pytest.approx(2_383.96)
    assert runtime.state_manager.reserve_cash("residual-probe", 100.0) is True
    reserved_quota = runtime.state_manager.get_account_quota()
    assert reserved_quota["available_cash"] == pytest.approx(2_283.96)
    assert reserved_quota["frozen_cash"] == pytest.approx(100.0)
    assert reserved_quota["total_asset"] == pytest.approx(40_000.0)
    assert runtime.state_manager.release_cash("residual-probe") is True

    runtime.latest_market_data["000001.SZ"] = MarketDataSnapshot(
      instrument_code="000001.SZ",
      timestamp=current_time,
      price=10.0,
      close=10.0,
      limit_up=11.0,
      limit_down=9.0,
      bid_price=[9.99],
      ask_price=[10.0],
      source="tick",
    )
    intent = TradeIntent(
      strategy_id="1",
      run_id=runtime.run_id,
      instrument_code="000001.SZ",
      direction=TradeIntentDirection.BUY,
      bucket="swing",
      reason="test_replay_cash_boundary",
      target_amount=9_500.0,
      limit_price_hint=10.0,
    )
    await runtime.state_manager.record_trade_intent(intent)
    await executor._process_trade_intent(runtime, intent)

    record = runtime.state_manager._state["trade_intents"][intent.intent_id]
    assert observed_sizer_account["available_cash"] == pytest.approx(2_383.96)
    assert observed_sizer_account["total_asset"] == pytest.approx(40_000.0)
    assert record["status"] == "REJECTED"
    assert record["metadata"]["risk_reason_code"] == "INSUFFICIENT_CASH"
    assert "可用 2383.96" in record["notes"]
    assert runtime.broker.orders == {}
  finally:
    await executor.shutdown()


@pytest.mark.asyncio
async def test_t_trade_replay_market_exit_waits_for_next_tick_and_uses_bid_depth(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  adapter = AsyncMock()
  adapter.connect = AsyncMock(return_value=True)
  monkeypatch.setattr(
    strategy_executor_module.adapter_manager,
    "get_adapter_for_mode",
    AsyncMock(return_value=adapter),
  )
  monkeypatch.setattr(
    strategy_executor_module.adapter_manager,
    "ensure_adapter_connected_for_mode",
    AsyncMock(return_value=True),
  )
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="strict-t-replay",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={
      "t_trade_replay": True,
      "commission_rate": 0.0,
      "minimum_commission": 0.0,
      "stamp_tax_rate": 0.0,
      "transfer_fee_rate": 0.0,
      "slippage_rate": 0.0,
      "book_depth_participation_pct": 0.5,
    },
    initial_capital=100_000.0,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="strict replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )

  await executor._setup_broker_and_data(runtime)

  broker = runtime.broker
  assert isinstance(broker, BacktestBroker)
  assert broker.strict_book_depth is True
  assert broker.no_queue_credit is True
  assert broker.defer_new_orders_until_next_quote is True
  broker.positions["000001.SZ"] = Position(
    instrument_code="000001.SZ",
    long_volume=100,
    available_volume=100,
    long_avg_price=10.0,
    last_price=10.0,
    market_value=1_000.0,
  )
  signal_at = datetime(2024, 1, 2, 10, 0)
  await broker.update_market_data(
    "000001.SZ",
    10.0,
    signal_at,
    market_data=_tick_book(signal_at),
  )

  order = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.SELL,
      price_type=PriceType.MARKET,
      volume=100,
      price=9.99,
    )
  )

  assert order.status is OrderStatus.SUBMITTED
  assert broker.trades == []

  next_tick = datetime(2024, 1, 2, 10, 0, 1)
  await broker.update_market_data(
    "000001.SZ",
    10.0,
    next_tick,
    market_data=_tick_book(next_tick),
  )

  assert order.status is OrderStatus.FILLED
  assert order.filled_volume == 100
  assert order.avg_price == pytest.approx((50 * 9.99 + 50 * 9.98) / 100)
  assert broker.trades[0].trade_time == next_tick


@pytest.mark.asyncio
async def test_ordinary_backtest_keeps_immediate_market_order_semantics(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  adapter = AsyncMock()
  adapter.connect = AsyncMock(return_value=True)
  monkeypatch.setattr(
    strategy_executor_module.adapter_manager,
    "get_adapter_for_mode",
    AsyncMock(return_value=adapter),
  )
  monkeypatch.setattr(
    strategy_executor_module.adapter_manager,
    "ensure_adapter_connected_for_mode",
    AsyncMock(return_value=True),
  )
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="ordinary-backtest",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={
      "commission_rate": 0.0,
      "minimum_commission": 0.0,
      "stamp_tax_rate": 0.0,
      "transfer_fee_rate": 0.0,
      "slippage_rate": 0.0,
    },
    initial_capital=100_000.0,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="ordinary backtest",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )

  await executor._setup_broker_and_data(runtime)

  broker = runtime.broker
  assert isinstance(broker, BacktestBroker)
  assert broker.strict_book_depth is False
  assert broker.no_queue_credit is False
  assert broker.defer_new_orders_until_next_quote is False
  broker.positions["000001.SZ"] = Position(
    instrument_code="000001.SZ",
    long_volume=100,
    available_volume=100,
    long_avg_price=10.0,
    last_price=10.0,
    market_value=1_000.0,
  )
  signal_at = datetime(2024, 1, 2, 10, 0)
  await broker.update_market_data(
    "000001.SZ",
    10.0,
    signal_at,
    market_data=_tick_book(signal_at),
  )

  order = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.SELL,
      price_type=PriceType.MARKET,
      volume=100,
      price=10.0,
    )
  )

  assert order.status is OrderStatus.FILLED
  assert broker.trades[0].trade_time == signal_at


@pytest.mark.asyncio
async def test_replay_progress_projection_writes_only_at_forced_day_boundary(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="replay-progress",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True, "account_id": "account-1"},
    backtest_start_time=datetime(2024, 1, 2, 9, 30),
    backtest_end_time=datetime(2024, 1, 2, 15, 0),
    current_time=datetime(2024, 1, 2, 12, 15),
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  update = AsyncMock()
  monkeypatch.setattr(
    strategy_executor_module.t_trade_replay_projection_service,
    "update",
    update,
  )

  await executor._report_t_trade_replay_progress(runtime)
  context.current_time = datetime(2024, 1, 2, 13, 0)
  await executor._report_t_trade_replay_progress(runtime)
  await executor._report_t_trade_replay_progress(
    runtime,
    processed_until=datetime(2024, 1, 2, 14, 0),
    force=True,
  )

  update.assert_awaited_once()
  call = update.await_args.kwargs
  assert call["progress_pct"] > 50.0
  assert call["processed_until"] == datetime(2024, 1, 2, 14, 0)


@pytest.mark.asyncio
async def test_multi_instrument_replay_reports_empty_window_as_processed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class FakeHistoricalDataAdapter:
    async def get_ticks(self, **_kwargs):
      return []

  class FakeTradingDateHelper:
    async def get_trading_calendar(self, **_kwargs):
      return [datetime(2024, 1, 2).date()]

  monkeypatch.setattr(
    strategy_executor_module,
    "HistoricalDataAdapter",
    FakeHistoricalDataAdapter,
  )
  monkeypatch.setattr(
    strategy_executor_module,
    "TradingDateHelper",
    FakeTradingDateHelper,
  )
  executor = StrategyExecutor()
  executor._run_backtest_warmup_klines = AsyncMock()
  executor._report_t_trade_replay_progress = AsyncMock()
  executor._runtime_log = lambda *_args, **_kwargs: None
  end_time = datetime(2024, 1, 2, 15, 0)
  context = StrategyContext(
    run_id="replay-empty-window",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ", "600000.SH"],
    parameters={"t_trade_replay": True, "account_id": "account-1"},
    backtest_start_time=datetime(2024, 1, 2, 9, 30),
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
    data_adapter=FakeHistoricalDataAdapter(),
    status=ExecutionStatus.RUNNING,
  )

  await executor._run_backtest_multi_instrument_timeline(
    runtime,
    context.instruments,
    [],
    context.backtest_start_time,
    end_time,
    use_tick_data=True,
  )

  executor._report_t_trade_replay_progress.assert_awaited_once_with(
    runtime,
    processed_until=end_time,
    force=True,
  )


@pytest.mark.asyncio
async def test_multi_instrument_t_trade_replay_consumes_global_source_identity_order(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """A replay must feed the strategy one global source-identity timeline."""

  class FakeHistoricalDataAdapter:
    current_time = None

  class FakeTradingDateHelper:
    async def get_trading_calendar(self, **_kwargs):
      return [datetime(2024, 1, 2).date()]

  class RecordingState(dict):
    def to_dict(self):
      return dict(self)

  class RecordingStrategy:
    def __init__(self, consumed):
      self.state = RecordingState()
      self.consumed = consumed

    async def step(self, input_snapshot):
      self.consumed.append(input_snapshot)
      return StrategyOutput()

  monkeypatch.setattr(
    strategy_executor_module,
    "HistoricalDataAdapter",
    FakeHistoricalDataAdapter,
  )
  monkeypatch.setattr(
    strategy_executor_module,
    "TradingDateHelper",
    FakeTradingDateHelper,
  )

  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  generation = 7

  def source_time_ms(offset_ms: int) -> int:
    return int(
      time_utils.to_utc(start_time + timedelta(milliseconds=offset_ms)).timestamp()
      * 1000
    )

  def replay_tick(
    instrument_code: str,
    *,
    offset_ms: int,
    tick_ordinal: int,
    price: float,
  ) -> SimpleNamespace:
    source_ms = source_time_ms(offset_ms)
    tick = _legacy_replay_tick(
      start_time + timedelta(milliseconds=offset_ms),
      price=price,
      transaction_num=tick_ordinal,
      source_time_ms=source_ms,
    )
    tick.stock_code = instrument_code
    tick.tick_ordinal = tick_ordinal
    tick.continuity_generation = generation
    return tick

  # Each per-instrument input is intentionally grouped by stock.  Only the
  # Engine's global merge can produce the expected 600000, 000001, 000001,
  # 600000 order.
  ticks_by_instrument = {
    "600000.SH": [
      replay_tick("600000.SH", offset_ms=100, tick_ordinal=1, price=20.01),
      replay_tick("600000.SH", offset_ms=300, tick_ordinal=3, price=20.03),
    ],
    "000001.SZ": [
      replay_tick("000001.SZ", offset_ms=100, tick_ordinal=2, price=10.02),
      replay_tick("000001.SZ", offset_ms=200, tick_ordinal=1, price=10.01),
    ],
  }

  async def run_once(run_id: str):
    consumed = []
    executor = StrategyExecutor()
    executor._run_backtest_warmup_klines = AsyncMock()
    executor._report_t_trade_replay_progress = AsyncMock()
    executor._process_auto_exit_plans = AsyncMock()
    executor._ensure_t_trade_opportunity_profile = AsyncMock()
    executor._process_strategy_output = AsyncMock()
    executor._board_replay_report_barrier = AsyncMock()
    executor._observe_t_trade_candidate_outcomes = AsyncMock()
    executor._observe_t_trade_phase_one_baseline = lambda *_args, **_kwargs: None
    executor._runtime_log = lambda *_args, **_kwargs: None

    async def load_ticks(_runtime, _adapter, *, instrument_code, **_kwargs):
      return [copy.copy(tick) for tick in ticks_by_instrument[instrument_code]]

    executor._load_backtest_ticks = load_ticks
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=list(ticks_by_instrument),
      parameters={"t_trade_replay": True},
      backtest_start_time=start_time,
      backtest_end_time=end_time,
    )
    runtime = StrategyRuntime(
      run_id=run_id,
      name="global replay",
      strategy_id=1,
      strategy_class=object,
      context=context,
      data_adapter=FakeHistoricalDataAdapter(),
      status=ExecutionStatus.RUNNING,
    )
    runtime.strategy = RecordingStrategy(consumed)

    await executor._run_backtest_multi_instrument_timeline(
      runtime,
      context.instruments,
      [],
      start_time,
      end_time,
      use_tick_data=True,
    )

    return [
      (
        item.instrument_code,
        item.market_data_context.source_identity,
        item.event.time,
      )
      for item in consumed
    ]

  first_run = await run_once("global-replay-1")
  second_run = await run_once("global-replay-2")
  expected_identities = [
    (generation, source_time_ms(100), 1),
    (generation, source_time_ms(100), 2),
    (generation, source_time_ms(200), 1),
    (generation, source_time_ms(300), 3),
  ]

  assert [item[1] for item in first_run] == expected_identities
  assert [item[0] for item in first_run] == [
    "600000.SH",
    "000001.SZ",
    "000001.SZ",
    "600000.SH",
  ]
  # Canonical source identity and replay event timestamps are stable across
  # independent runs, even though the runtime IDs differ.
  assert first_run == second_run


def _legacy_replay_tick(
  timestamp: datetime,
  *,
  price: float,
  transaction_num: int,
  source_time_ms: object = _MISSING_SOURCE_TIME_MS,
) -> SimpleNamespace:
  fields = {
    "stock_code": "000001.SZ",
    "period": "tick",
    "time": timestamp,
    "last_price": price,
    "open": price,
    "high": price,
    "low": price,
    "last_close": price,
    "amount": 1_000.0 + transaction_num,
    "volume": 100.0 + transaction_num,
    "pvolume": 100.0 + transaction_num,
    "tickvol": 1.0,
    "stock_status": 0,
    "open_int": 0,
    "transaction_num": transaction_num,
    "ask_price": [price + 0.01],
    "bid_price": [price - 0.01],
    "ask_vol": [100.0],
    "bid_vol": [100.0],
  }
  if source_time_ms is not _MISSING_SOURCE_TIME_MS:
    fields["source_time_ms"] = source_time_ms
  return SimpleNamespace(**fields)


def _replay_runtime_for(
  run_id: str,
  start_time: datetime,
  end_time: datetime,
) -> StrategyRuntime:
  context = StrategyContext(
    run_id=run_id,
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True},
    backtest_start_time=start_time,
    backtest_end_time=end_time,
  )
  return StrategyRuntime(
    run_id=run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("record_count", "expected_offsets", "expected_queries"),
  [
    (3, [0, 3], 2),
    (4, [0, 3], 2),
  ],
)
async def test_t_trade_tick_reader_probes_exact_limit_and_paginates_beyond_it(
  monkeypatch: pytest.MonkeyPatch,
  record_count: int,
  expected_offsets: list[int],
  expected_queries: int,
) -> None:
  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 3)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  source = [
    SimpleNamespace(time=start_time + timedelta(seconds=index))
    for index in range(record_count)
  ]

  class PagedAdapter:
    def __init__(self) -> None:
      self.offsets = []

    async def get_ticks(self, **kwargs):
      offset = kwargs["offset"]
      limit = kwargs["limit"]
      self.offsets.append(offset)
      return source[offset : offset + limit]

  context = StrategyContext(
    run_id=f"replay-page-{record_count}",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True},
    backtest_start_time=start_time,
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  adapter = PagedAdapter()

  ticks = await StrategyExecutor()._load_t_trade_replay_ticks_paginated(
    runtime,
    adapter,
    instrument_code="000001.SZ",
    start_time=start_time,
    end_time=end_time,
  )

  assert len(ticks) == record_count
  assert all(int(getattr(tick, "source_time_ms", 0) or 0) > 0 for tick in ticks)
  assert [int(getattr(tick, "tick_ordinal", -1)) for tick in ticks] == [
    0
  ] * record_count
  assert adapter.offsets == expected_offsets
  audit = context.parameters["replay_tick_read_audit"]
  assert audit["records_read"] == record_count
  assert audit["queries"] == expected_queries
  assert audit["issues"] == []
  if record_count == 3:
    assert audit["boundary_probe_windows"] == 1
  else:
    assert audit["paginated_windows"] == 1


@pytest.mark.asyncio
async def test_t_trade_tick_reader_normalizes_legacy_identity_once_across_pages(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Legacy source fields must receive one ordinal sequence across offsets."""

  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 3)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  same_source_time = datetime(2024, 1, 2, 9, 30, 0, 123456)
  source = [
    _legacy_replay_tick(
      same_source_time,
      price=10.01 + index / 100,
      transaction_num=index + 1,
      source_time_ms=(0 if index == 1 else _MISSING_SOURCE_TIME_MS),
    )
    for index in range(6)
  ]

  class PagedAdapter:
    def __init__(self) -> None:
      self.offsets: list[int] = []

    async def get_ticks(self, **kwargs):
      offset = kwargs["offset"]
      limit = kwargs["limit"]
      self.offsets.append(offset)
      return source[offset : offset + limit]

  context = StrategyContext(
    run_id="replay-legacy-source-identity",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True},
    backtest_start_time=start_time,
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  executor = StrategyExecutor()
  adapter = PagedAdapter()

  ticks = await executor._load_t_trade_replay_ticks_paginated(
    runtime,
    adapter,
    instrument_code="000001.SZ",
    start_time=start_time,
    end_time=end_time,
  )

  expected_source_time_ms = int(time_utils.to_utc(same_source_time).timestamp() * 1000)
  assert adapter.offsets == [0, 3, 6]
  assert [tick.source_time_ms for tick in ticks] == [
    expected_source_time_ms
  ] * 6
  assert [tick.tick_ordinal for tick in ticks] == [0, 1, 2, 3, 4, 5]
  assert len({(tick.source_time_ms, tick.tick_ordinal) for tick in ticks}) == 6
  assert len({tick.time for tick in ticks}) == 6

  market_data_context = executor._build_market_data_context(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="000001.SZ",
    timestamp=ticks[0].time,
    event=ticks[0],
  )
  assert market_data_context.source_time_ms == expected_source_time_ms
  assert market_data_context.source_time_ms > 0
  opportunity_input = StrategyInput(
    run_id=runtime.run_id,
    strategy_id=str(runtime.strategy_id),
    timestamp=ticks[0].time,
    cadence=StrategyCadence.TICK,
    instrument_code="000001.SZ",
    event=ticks[0],
    market_data_context=market_data_context,
    market_context={},
  )
  assert AshareIntradayTAssistantStrategy._opportunity_sample(opportunity_input)

  audit = context.parameters["replay_tick_read_audit"]
  assert audit["records_read"] == 6
  assert audit["pages_read"] == 2
  assert audit["queries"] == 3
  assert audit["boundary_probe_windows"] == 1
  assert audit["issues"] == []


@pytest.mark.asyncio
async def test_t_trade_tick_reader_keeps_existing_source_identity_deterministic(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 3)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  source_time_ms = 1_704_160_800_123
  source = [
    _legacy_replay_tick(
      start_time,
      price=10.02 - index / 100,
      transaction_num=index + 1,
      source_time_ms=source_time_ms,
    )
    for index in range(2)
  ]

  class PagedAdapter:
    async def get_ticks(self, **kwargs):
      return source[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]]

  def runtime_for(run_id: str) -> StrategyRuntime:
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={"t_trade_replay": True},
      backtest_start_time=start_time,
      backtest_end_time=end_time,
    )
    return StrategyRuntime(
      run_id=run_id,
      name="replay",
      strategy_id=1,
      strategy_class=object,
      context=context,
    )

  executor = StrategyExecutor()
  first = await executor._load_t_trade_replay_ticks_paginated(
    runtime_for("replay-existing-identity-1"),
    PagedAdapter(),
    instrument_code="000001.SZ",
    start_time=start_time,
    end_time=end_time,
  )
  second = await executor._load_t_trade_replay_ticks_paginated(
    runtime_for("replay-existing-identity-2"),
    PagedAdapter(),
    instrument_code="000001.SZ",
    start_time=start_time,
    end_time=end_time,
  )

  first_identity = [
    (tick.source_time_ms, tick.tick_ordinal, tick.time) for tick in first
  ]
  second_identity = [
    (tick.source_time_ms, tick.tick_ordinal, tick.time) for tick in second
  ]
  assert first_identity == second_identity
  assert [item[0] for item in first_identity] == [source_time_ms, source_time_ms]
  assert [item[1] for item in first_identity] == [0, 1]


@pytest.mark.asyncio
async def test_t_trade_tick_reader_distinguishes_full_page_content(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 2)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  timestamp = start_time + timedelta(milliseconds=123)
  first_page = [
    _legacy_replay_tick(
      timestamp,
      price=10.01 + index / 100,
      transaction_num=index + 1,
    )
    for index in range(2)
  ]
  second_page = [copy.copy(item) for item in first_page]
  for index, item in enumerate(second_page):
    item.tickvol = 20.0 + index
    item.pvolume = 200.0 + index

  class PagedAdapter:
    def __init__(self) -> None:
      self.offsets: list[int] = []

    async def get_ticks(self, **kwargs):
      offset = kwargs["offset"]
      self.offsets.append(offset)
      if offset == 0:
        return first_page
      if offset == 2:
        return second_page
      return []

  runtime = _replay_runtime_for("replay-full-page-content", start_time, end_time)
  adapter = PagedAdapter()

  ticks = await StrategyExecutor()._load_t_trade_replay_ticks_paginated(
    runtime,
    adapter,
    instrument_code="000001.SZ",
    start_time=start_time,
    end_time=end_time,
  )

  assert adapter.offsets == [0, 2, 4]
  assert len(ticks) == 4
  assert sorted(tick.tickvol for tick in ticks) == [1.0, 1.0, 20.0, 21.0]
  assert sorted(tick.pvolume for tick in ticks) == [101.0, 102.0, 200.0, 201.0]


@pytest.mark.asyncio
async def test_t_trade_tick_reader_rejects_non_adjacent_repeated_page(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 2)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  timestamp = start_time + timedelta(milliseconds=123)
  page_a = [
    _legacy_replay_tick(
      timestamp,
      price=10.01 + index / 100,
      transaction_num=index + 1,
    )
    for index in range(2)
  ]
  page_b = [copy.copy(item) for item in page_a]
  for item in page_b:
    item.tickvol = 20.0

  class BrokenPagedAdapter:
    def __init__(self) -> None:
      self.offsets: list[int] = []

    async def get_ticks(self, **kwargs):
      offset = kwargs["offset"]
      self.offsets.append(offset)
      if offset in {0, 4}:
        return page_a
      if offset == 2:
        return page_b
      return []

  runtime = _replay_runtime_for("replay-page-a-b-a", start_time, end_time)
  adapter = BrokenPagedAdapter()

  with pytest.raises(RuntimeError, match="DATA_PARTIAL"):
    await StrategyExecutor()._load_t_trade_replay_ticks_paginated(
      runtime,
      adapter,
      instrument_code="000001.SZ",
      start_time=start_time,
      end_time=end_time,
    )

  assert adapter.offsets == [0, 2, 4]
  assert runtime.context.parameters["replay_tick_read_audit"]["issues"][0][
    "reason_code"
  ] == "TICK_PAGINATION_DID_NOT_ADVANCE"


@pytest.mark.asyncio
async def test_t_trade_tick_reader_rejects_negative_explicit_source_time(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 2)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  invalid_tick = _legacy_replay_tick(
    start_time,
    price=10.01,
    transaction_num=1,
    source_time_ms=-1,
  )

  class InvalidSourceAdapter:
    async def get_ticks(self, **_kwargs):
      return [invalid_tick]

  runtime = _replay_runtime_for("replay-negative-source-time", start_time, end_time)

  with pytest.raises(RuntimeError, match="DATA_PARTIAL"):
    await StrategyExecutor()._load_t_trade_replay_ticks_paginated(
      runtime,
      InvalidSourceAdapter(),
      instrument_code="000001.SZ",
      start_time=start_time,
      end_time=end_time,
    )

  assert runtime.context.parameters["replay_tick_read_audit"]["issues"][0][
    "reason_code"
  ] == "INVALID_TICK_SOURCE_TIME"


@pytest.mark.asyncio
async def test_t_trade_tick_reader_normalizes_missing_and_zero_source_time_for_repeats(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 1)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  timestamp = start_time + timedelta(milliseconds=123)
  missing_source = _legacy_replay_tick(
    timestamp,
    price=10.01,
    transaction_num=1,
  )
  zero_source = copy.copy(missing_source)
  zero_source.source_time_ms = 0

  class BrokenPagedAdapter:
    def __init__(self) -> None:
      self.offsets: list[int] = []

    async def get_ticks(self, **kwargs):
      offset = kwargs["offset"]
      self.offsets.append(offset)
      if offset == 0:
        return [missing_source]
      if offset == 1:
        return [zero_source]
      return []

  runtime = _replay_runtime_for("replay-source-time-equivalence", start_time, end_time)
  adapter = BrokenPagedAdapter()

  with pytest.raises(RuntimeError, match="DATA_PARTIAL"):
    await StrategyExecutor()._load_t_trade_replay_ticks_paginated(
      runtime,
      adapter,
      instrument_code="000001.SZ",
      start_time=start_time,
      end_time=end_time,
    )

  assert adapter.offsets == [0, 1]
  assert runtime.context.parameters["replay_tick_read_audit"]["issues"][0][
    "reason_code"
  ] == "TICK_PAGINATION_DID_NOT_ADVANCE"


@pytest.mark.asyncio
async def test_t_trade_tick_reader_fails_closed_when_identity_normalization_fails(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 3)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  invalid_tick = _legacy_replay_tick(
    start_time,
    price=10.01,
    transaction_num=1,
    source_time_ms="not-an-integer",
  )

  class InvalidIdentityAdapter:
    async def get_ticks(self, **_kwargs):
      return [invalid_tick]

  context = StrategyContext(
    run_id="replay-identity-normalization-failure",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True},
    backtest_start_time=start_time,
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )

  with pytest.raises(RuntimeError, match="DATA_PARTIAL"):
    await StrategyExecutor()._load_t_trade_replay_ticks_paginated(
      runtime,
      InvalidIdentityAdapter(),
      instrument_code="000001.SZ",
      start_time=start_time,
      end_time=end_time,
    )

  audit = context.parameters["replay_tick_read_audit"]
  assert audit["issues"][-1]["reason_code"] == "TICK_IDENTITY_NORMALIZATION_FAILED"
  assert audit["issues"][-1]["details"]["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_t_trade_tick_reader_rejects_non_advancing_pagination_before_metrics(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(strategy_executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 3)
  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  repeated_page = [
    SimpleNamespace(time=start_time + timedelta(seconds=index)) for index in range(3)
  ]

  class BrokenPagedAdapter:
    async def get_ticks(self, **_kwargs):
      return repeated_page

  context = StrategyContext(
    run_id="replay-page-stalled",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True},
    backtest_start_time=start_time,
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )

  with pytest.raises(RuntimeError, match="DATA_PARTIAL"):
    await StrategyExecutor()._load_t_trade_replay_ticks_paginated(
      runtime,
      BrokenPagedAdapter(),
      instrument_code="000001.SZ",
      start_time=start_time,
      end_time=end_time,
    )

  audit = context.parameters["replay_tick_read_audit"]
  assert audit["issues"][0]["reason_code"] == "TICK_PAGINATION_DID_NOT_ADVANCE"
  metrics = build_t_trade_replay_metrics(runtime)
  assert metrics["data_quality"] == "PARTIAL"
  assert "Tick 读取窗口未通过完整性校验" in metrics["data_quality_message"]


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
  runtime.status = ExecutionStatus.RUNNING
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

  async def approve(run_id: str, intent_id: str, *, approval_expectation=None):
    approved.append((run_id, intent_id, approval_expectation))
    return {"success": True, "code": "APPROVED"}

  monkeypatch.setattr(executor, "approve_trade_intent", approve)
  monkeypatch.setattr(executor, "_runtime_log", lambda *args, **kwargs: None)

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(trade_intents=[intent]),
  )

  assert approved == [(context.run_id, intent.intent_id, None)]
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


@pytest.mark.asyncio
async def test_replay_opportunity_diagnostics_are_scoped_to_exact_run(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  start = datetime(2024, 1, 2, 9, 30)
  end = datetime(2024, 1, 2, 15, 0)
  context = StrategyContext(
    run_id="replay-v3-run",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"account_id": "account-1", "t_trade_replay": True},
    backtest_start_time=start,
    backtest_end_time=end,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay-v3",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  diagnostics = {
    "available": True,
    "scope": {"strategy_run_id": runtime.run_id},
    "denominator": {
      "code": "READY_INSTRUMENT_SECONDS",
      "ready_instrument_seconds": 120.0,
    },
  }
  service = SimpleNamespace(signal_diagnostics=AsyncMock(return_value=diagnostics))
  db = object()

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, *_args):
      return False

  monkeypatch.setattr(
    strategy_executor_module,
    "AsyncSessionLocal",
    SessionContext,
  )
  executor = StrategyExecutor(opportunity_diagnostics_service=service)

  result = await executor._load_t_trade_replay_opportunity_diagnostics(runtime)

  assert result is diagnostics
  service.signal_diagnostics.assert_awaited_once_with(
    "account-1",
    stock_code=None,
    start_time=start,
    end_time=end,
    db=db,
    strategy_run_id="replay-v3-run",
  )


def test_derived_price_limits_are_disclosed_as_partial_data_quality() -> None:
  runtime = _metrics_runtime(datetime(2024, 1, 2, 14, 0))
  runtime.context.parameters.update(
    {
      "replay_price_limit_policy": {
        "schema_version": 1,
        "ambiguous_action": "STRICT_RISK_REJECT",
      },
      "replay_price_limit_source_counts": {
        "NATIVE_TICK": 10,
        "DERIVED_TICK": 25,
        "MISSING_TICK": 2,
      },
    }
  )

  metrics = build_t_trade_replay_metrics(runtime)

  assert metrics["data_quality"] == "PARTIAL"
  assert "25 个行情事件的涨跌停价" in metrics["data_quality_message"]
  assert "2 个行情事件缺少可确认的涨跌停价" in metrics["data_quality_message"]
  assert "原生 10、派生 25、缺失 2" in metrics["methodology"]["price_limits"]
  assert metrics["methodology"]["price_limit_source_counts"] == {
    "NATIVE_TICK": 10,
    "DERIVED_TICK": 25,
    "MISSING_TICK": 2,
  }


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
  assert report["conclusion_code"] == "DIAGNOSTICS_UNAVAILABLE"
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
async def test_replay_finalizer_discloses_unfilled_batch_without_a_next_tick() -> None:
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
  broker = BacktestBroker(
    initial_capital=100_000.0,
    strict_book_depth=True,
    no_queue_credit=True,
    defer_new_orders_until_next_quote=True,
  )
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
    market_data=_tick_book(context.current_time),
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

  assert plan.remaining_volume == 100
  assert broker.positions["000001.SZ"].long_volume == 1_100
  assert broker.trades == []
  forced_order = next(iter(broker.orders.values()))
  assert forced_order.status is OrderStatus.SUBMITTED
  liquidation = context.parameters["replay_forced_liquidation"]
  assert liquidation["closed_cycles"] == 0
  assert liquidation["failed_cycles"] == 1
  assert liquidation["attempts"] == [
    {
      "plan_id": "plan-finalize",
      "batch_id": "batch-finalize",
      "stock_code": "000001.SZ",
      "requested_volume": 100,
      "status": "FAILED_NOT_FULLY_LIQUIDATED",
      "remaining_volume": 100,
    }
  ]
  metrics = build_t_trade_replay_metrics(runtime)
  assert metrics["data_quality"] == "PARTIAL"
  assert "1 个批次期末未完成合法清算" in metrics["data_quality_message"]
  assert metrics["summary"]["liquidation_failed_cycles"] == 1
