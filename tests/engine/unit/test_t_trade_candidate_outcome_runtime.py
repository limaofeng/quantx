from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderResponse,
  OrderStatus,
  OrderType,
  PriceType,
  TradeRecord,
)
from quantx_domain.strategies.base import (
  MarketDataContext,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyRunMode,
  TradeExecutionEvent,
)
from quantx_domain.trading import MarketDataSnapshot
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.models.t_trade_candidate_outcome import (
  TTradeCandidateOutcome,
)
from quantx_infrastructure.repositories.t_trade_candidate_outcome_repository import (
  TTradeCandidateOutcomeRepository,
)
from quantx_infrastructure.services.t_trade_candidate_outcome_service import (
  CandidateOutcomeReconciliationResult,
  TTradeCandidateOutcomePersistenceFacade,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _candidate_event() -> dict:
  return {
    "record_kind": "MATERIAL",
    "event_key": "candidate-1-latched",
    "event_type": "CANDIDATE_LATCHED",
    "instrument_code": "600000.SH",
    "signal_snapshot": {
      "candidate_id": "candidate-1",
      "candidate_fingerprint": "a" * 64,
      "source_time_ms": 1_000_000,
      "tick_ordinal": 10,
      "continuity_generation": "1",
      "policy_version": "policy-3",
      "feature_schema_version": "1",
      "profile_version": "profile-1",
      "profile_fingerprint": "b" * 64,
      "features": {"price": 10.0},
    },
  }


def _runtime(broker) -> SimpleNamespace:
  return SimpleNamespace(
    run_id="run-1",
    status=ExecutionStatus.RUNNING,
    error_message=None,
    strategy=None,
    strategy_class=SimpleNamespace(USES_T_TRADE_OPPORTUNITY_PROFILE=True),
    broker=broker,
    replay_clock=None,
    context=SimpleNamespace(
      mode=StrategyRunMode.BACKTEST,
      parameters={"account_id": "account-1", "t_trade_opportunity_v3": True},
      current_time=datetime.fromtimestamp(1_000),
    ),
  )


def _input(
  source_time_ms: int,
  ordinal: int,
  *,
  instrument_code: str = "600000.SH",
) -> StrategyInput:
  return StrategyInput(
    run_id="run-1",
    strategy_id="1",
    timestamp=datetime.fromtimestamp(source_time_ms / 1000),
    cadence=StrategyCadence.TICK,
    instrument_code=instrument_code,
    market_data_context=MarketDataContext(
      continuity_generation=1,
      source_time_ms=source_time_ms,
      tick_ordinal=ordinal,
    ),
  )


def _empty_repair_result(
  *,
  has_more: bool = False,
  deferred_count: int = 0,
) -> CandidateOutcomeReconciliationResult:
  return CandidateOutcomeReconciliationResult(
    states=(),
    examined_count=deferred_count,
    repaired_count=0,
    idempotent_count=0,
    skipped_count=0,
    quarantined_count=0,
    deferred_count=deferred_count,
    issue_counts=(),
    issues=(),
    has_more=has_more,
  )


def _inactive_outcome_state(instrument_code: str = "600000.SH") -> SimpleNamespace:
  return SimpleNamespace(
    definition=SimpleNamespace(instrument_code=instrument_code),
    status=SimpleNamespace(value="COMPLETED"),
    post_fill=SimpleNamespace(status=SimpleNamespace(value="COMPLETED")),
  )


@pytest.mark.asyncio
async def test_executor_side_channel_is_restart_safe_and_arms_on_full_entry() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(TTradeCandidateOutcome.__table__.create)
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  facade = TTradeCandidateOutcomePersistenceFacade(sessions)

  request = OrderRequest(
    instrument_code="600000.SH",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=200,
  )
  order = OrderResponse(
    order_id="order-1",
    request=request,
    status=OrderStatus.PARTIAL_FILLED,
    submit_time=datetime.fromtimestamp(1_000),
    filled_volume=100,
  )
  broker = SimpleNamespace(get_order=lambda _order_id: None)

  async def get_order(_order_id: str):
    return order

  broker.get_order = get_order
  runtime = _runtime(broker)
  executor = StrategyExecutor(candidate_outcome_facade=facade)
  await executor._seed_t_trade_candidate_outcome(
    runtime,
    account_id="account-1",
    event=_candidate_event(),
  )

  metadata = {
    "account_id": "account-1",
    "strategy_run_id": "run-1",
    "instrument_code": "600000.SH",
    "candidate_id": "candidate-1",
    "candidate_fingerprint": "a" * 64,
    "policy_version": "policy-3",
    "intent_id": "intent-1",
    "t_trade_role": "entry",
  }
  first_fill = TradeRecord(
    trade_id="trade-1",
    order_id="order-1",
    instrument_code="600000.SH",
    trade_type=OrderType.BUY,
    price=10.0,
    volume=100,
    amount=1_000.0,
    commission=2.0,
    trade_time=datetime.fromtimestamp(1_001),
    metadata=metadata,
  )
  await executor._record_t_trade_candidate_fill(
    runtime, first_fill, durable_event=False
  )
  order.status = OrderStatus.FILLED
  order.filled_volume = 200
  second_fill = TradeRecord(
    trade_id="trade-2",
    order_id="order-1",
    instrument_code="600000.SH",
    trade_type=OrderType.BUY,
    price=10.2,
    volume=100,
    amount=1_020.0,
    commission=3.0,
    trade_time=datetime.fromtimestamp(1_002),
    metadata=metadata,
  )
  await executor._record_t_trade_candidate_fill(
    runtime, second_fill, durable_event=False
  )

  for ordinal, source_time_ms in enumerate(range(1_002_000, 1_903_000, 60_000), 12):
    runtime.context.current_time = datetime.fromtimestamp(source_time_ms / 1000)
    await executor._observe_t_trade_candidate_outcomes(
      runtime,
      input_snapshot=_input(source_time_ms, ordinal),
      market_data=MarketDataSnapshot(
        instrument_code="600000.SH",
        price=10.0 + ordinal / 1_000,
      ),
    )
  await executor._observe_t_trade_candidate_outcomes(
    runtime,
    input_snapshot=_input(1_902_000, 99),
    market_data=MarketDataSnapshot(instrument_code="600000.SH", price=10.3),
  )

  restarted = StrategyExecutor(candidate_outcome_facade=facade)
  await restarted._record_t_trade_candidate_fill(
    runtime, second_fill, durable_event=False
  )
  async with sessions() as db:
    row = await TTradeCandidateOutcomeRepository(db).get(
      strategy_run_id="run-1", candidate_id="candidate-1"
    )
    assert row is not None
    state = TTradeCandidateOutcomeRepository.state_from_row(row)
  assert state.execution.entry_volume == 200
  assert state.execution.entry_frozen is True
  assert state.post_fill.available is True
  assert state.post_fill.net_available is False
  assert state.post_fill.reference_price == pytest.approx(10.1)

  await engine.dispose()


@pytest.mark.asyncio
async def test_analytics_failure_is_best_effort_live_but_fatal_for_strict_replay() -> (
  None
):
  facade = SimpleNamespace(
    reconcile_applied_trade_events=AsyncMock(return_value=_empty_repair_result()),
    observe_tick=AsyncMock(side_effect=RuntimeError("db down")),
  )
  executor = StrategyExecutor(candidate_outcome_facade=facade)
  market_data = MarketDataSnapshot(instrument_code="600000.SH", price=10.0)

  live = _runtime(SimpleNamespace())
  live.context.mode = StrategyRunMode.LIVE
  await executor._observe_t_trade_candidate_outcomes(
    live,
    input_snapshot=_input(1_001_000, 11),
    market_data=market_data,
  )
  assert live.status is ExecutionStatus.RUNNING

  strict = _runtime(SimpleNamespace())
  strict.context.parameters["t_trade_replay"] = True
  with pytest.raises(RuntimeError, match="db down"):
    await executor._observe_t_trade_candidate_outcomes(
      strict,
      input_snapshot=_input(1_001_000, 11),
      market_data=market_data,
    )
  assert strict.status is ExecutionStatus.ERROR
  assert strict.error_message == "T_TRADE_CANDIDATE_OUTCOME_TICK_FAILED"


@pytest.mark.asyncio
async def test_live_outcome_repair_scans_once_per_run_until_a_fill_marks_it_dirty() -> (
  None
):
  facade = SimpleNamespace(
    reconcile_applied_trade_events=AsyncMock(return_value=_empty_repair_result()),
    observe_tick=AsyncMock(return_value=[]),
    record_trade_fact=AsyncMock(return_value=None),
  )
  executor = StrategyExecutor(candidate_outcome_facade=facade)
  live = _runtime(SimpleNamespace())
  live.context.mode = StrategyRunMode.LIVE
  market_data = MarketDataSnapshot(instrument_code="600000.SH", price=10.0)

  for source_time_ms, ordinal in ((1_001_000, 11), (1_002_000, 12)):
    await executor._observe_t_trade_candidate_outcomes(
      live,
      input_snapshot=_input(source_time_ms, ordinal),
      market_data=market_data,
    )
  await executor._observe_t_trade_candidate_outcomes(
    live,
    input_snapshot=_input(
      1_003_000,
      13,
      instrument_code="000001.SZ",
    ),
    market_data=MarketDataSnapshot(instrument_code="000001.SZ", price=8.0),
  )
  facade.reconcile_applied_trade_events.assert_awaited_once_with(
    strategy_run_id="run-1",
  )

  trade = TradeRecord(
    trade_id="trade-live-1",
    order_id="order-1",
    instrument_code="600000.SH",
    trade_type=OrderType.BUY,
    price=10.0,
    volume=100,
    amount=1_000.0,
    commission=0.0,
    trade_time=datetime.fromtimestamp(1_003),
    metadata={
      "candidate_id": "candidate-1",
      "intent_id": "intent-1",
      "t_trade_role": "entry",
      "requested_entry_volume": 100,
    },
  )
  await executor._record_t_trade_candidate_fill(live, trade, durable_event=True)
  await executor._observe_t_trade_candidate_outcomes(
    live,
    input_snapshot=_input(1_004_000, 14),
    market_data=market_data,
  )

  assert facade.reconcile_applied_trade_events.await_count == 2


@pytest.mark.asyncio
async def test_single_inactive_candidate_fact_cannot_disable_instrument_activity() -> (
  None
):
  inactive = _inactive_outcome_state()
  facade = SimpleNamespace(
    seed_material_event=AsyncMock(return_value=inactive),
    record_trade_fact=AsyncMock(return_value=inactive),
  )
  executor = StrategyExecutor(candidate_outcome_facade=facade)
  live = _runtime(SimpleNamespace())
  live.context.mode = StrategyRunMode.LIVE
  activity_key = (live.run_id, "600000.SH")
  executor._candidate_outcome_activity[activity_key] = True

  assert await executor._seed_t_trade_candidate_outcome(
    live,
    account_id="account-1",
    event=_candidate_event(),
  )
  trade = TradeRecord(
    trade_id="trade-inactive-candidate",
    order_id="order-1",
    instrument_code="600000.SH",
    trade_type=OrderType.BUY,
    price=10.0,
    volume=100,
    amount=1_000.0,
    commission=0.0,
    trade_time=datetime.fromtimestamp(1_003),
    metadata={
      "candidate_id": "candidate-inactive",
      "t_trade_role": "entry",
    },
  )
  assert await executor._record_t_trade_candidate_fill(
    live,
    trade,
    durable_event=True,
  )

  assert executor._candidate_outcome_activity[activity_key] is True


@pytest.mark.asyncio
async def test_paper_candidate_fill_uses_simulator_order_and_fee_facts() -> None:
  request = OrderRequest(
    instrument_code="600000.SH",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=200,
  )
  order = OrderResponse(
    order_id="order-paper-1",
    request=request,
    status=OrderStatus.FILLED,
    submit_time=datetime.fromtimestamp(1_000),
    filled_volume=200,
  )
  broker = SimpleNamespace(get_order=AsyncMock(return_value=order))
  facade = SimpleNamespace(record_trade_fact=AsyncMock(return_value=None))
  executor = StrategyExecutor(candidate_outcome_facade=facade)
  paper = _runtime(broker)
  paper.context.mode = StrategyRunMode.PAPER
  trade = TradeRecord(
    trade_id="trade-paper-1",
    order_id=order.order_id,
    instrument_code="600000.SH",
    trade_type=OrderType.BUY,
    price=10.1,
    volume=200,
    amount=2_020.0,
    commission=5.0,
    trade_time=datetime.fromtimestamp(1_001),
    metadata={
      "account_id": "account-1",
      "strategy_run_id": "run-1",
      "instrument_code": "600000.SH",
      "candidate_id": "candidate-1",
      "candidate_fingerprint": "a" * 64,
      "policy_version": "policy-3",
      "intent_id": "intent-paper-1",
      "t_trade_role": "entry",
    },
  )

  await executor._record_t_trade_candidate_fill(
    paper,
    trade,
    durable_event=False,
  )

  broker.get_order.assert_awaited_once_with(order.order_id)
  facade.record_trade_fact.assert_awaited_once_with(
    strategy_run_id="run-1",
    trade=trade,
    entry_complete=True,
    authoritative_fee=5.0,
    fee_is_authoritative=True,
    entry_target_volume=200,
  )


@pytest.mark.asyncio
async def test_paper_fill_outbox_replays_once_after_process_restart() -> None:
  class _PaperOutboxState:
    def __init__(self, initial: dict[str, dict] | None = None) -> None:
      self.facts = dict(initial or {})
      self.last_snapshot_failure_code = None
      self.force_saves = 0

    def enqueue_t_trade_paper_fill_fact(self, fact: dict) -> None:
      self.facts.setdefault(str(fact["fact_key"]), dict(fact))

    def pending_t_trade_paper_fill_facts(self) -> list[dict]:
      return [dict(fact) for fact in self.facts.values()]

    def acknowledge_t_trade_paper_fill_facts(self, fact_keys: list[str]) -> None:
      for fact_key in fact_keys:
        self.facts.pop(fact_key, None)

    async def force_save(self) -> bool:
      self.force_saves += 1
      return True

  request = OrderRequest(
    instrument_code="600000.SH",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=200,
  )
  order = OrderResponse(
    order_id="order-paper-restart",
    request=request,
    status=OrderStatus.FILLED,
    submit_time=datetime.fromtimestamp(1_000),
    filled_volume=200,
  )
  first_broker = SimpleNamespace(get_order=AsyncMock(return_value=order))
  first_runtime = _runtime(first_broker)
  first_runtime.context.mode = StrategyRunMode.PAPER
  first_runtime.state_manager = _PaperOutboxState()
  trade = TradeRecord(
    trade_id="trade-paper-restart",
    order_id=order.order_id,
    instrument_code="600000.SH",
    trade_type=OrderType.BUY,
    price=10.1,
    volume=200,
    amount=2_020.0,
    commission=5.0,
    trade_time=datetime.fromtimestamp(1_001),
    metadata={
      "account_id": "account-1",
      "strategy_run_id": "run-1",
      "instrument_code": "600000.SH",
      "candidate_id": "candidate-1",
      "candidate_fingerprint": "a" * 64,
      "policy_version": "policy-3",
      "intent_id": "intent-paper-restart",
      "t_trade_role": "entry",
    },
  )
  failing_facade = SimpleNamespace(
    record_trade_fact=AsyncMock(side_effect=RuntimeError("db unavailable"))
  )
  first_executor = StrategyExecutor(candidate_outcome_facade=failing_facade)
  fact = await first_executor._build_t_trade_paper_fill_fact(first_runtime, trade)
  assert fact is not None
  first_runtime.state_manager.enqueue_t_trade_paper_fill_fact(fact)

  with pytest.raises(RuntimeError, match="db unavailable"):
    await first_executor._replay_pending_t_trade_paper_fill_facts(first_runtime)
  durable_facts = dict(first_runtime.state_manager.facts)
  assert list(durable_facts) == ["paper-fill:run-1:trade-paper-restart"]

  restarted_broker = SimpleNamespace(
    get_order=AsyncMock(side_effect=AssertionError("restart must not query broker"))
  )
  restarted_runtime = _runtime(restarted_broker)
  restarted_runtime.context.mode = StrategyRunMode.PAPER
  restarted_runtime.state_manager = _PaperOutboxState(durable_facts)
  outcome_state = SimpleNamespace(
    definition=SimpleNamespace(instrument_code="600000.SH"),
    status=None,
    post_fill=None,
  )
  recovered_facade = SimpleNamespace(
    record_trade_fact=AsyncMock(return_value=outcome_state)
  )
  restarted_executor = StrategyExecutor(candidate_outcome_facade=recovered_facade)

  await restarted_executor._replay_pending_t_trade_paper_fill_facts(
    restarted_runtime
  )
  await restarted_executor._replay_pending_t_trade_paper_fill_facts(
    restarted_runtime
  )

  recovered_facade.record_trade_fact.assert_awaited_once()
  recovered = recovered_facade.record_trade_fact.await_args.kwargs
  assert recovered["trade"].trade_id == trade.trade_id
  assert recovered["trade"].metadata == trade.metadata
  assert recovered["entry_complete"] is True
  assert recovered["entry_target_volume"] == 200
  assert recovered["authoritative_fee"] == 5.0
  assert restarted_runtime.state_manager.facts == {}
  assert restarted_runtime.state_manager.force_saves == 1
  restarted_broker.get_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_paper_fill_outbox_replay_orders_reversed_storage_facts_entry_before_exit() -> None:
  class PaperOutboxState:
    def __init__(self, facts: dict[str, dict]) -> None:
      self.facts = dict(facts)
      self.last_snapshot_failure_code = None

    def pending_t_trade_paper_fill_facts(self) -> list[dict]:
      return [dict(item) for item in self.facts.values()]

    def acknowledge_t_trade_paper_fill_facts(self, keys: list[str]) -> None:
      for key in keys:
        self.facts.pop(key, None)

    async def force_save(self) -> bool:
      return True

  def fact(*, trade_id: str, role: str, at: int) -> dict:
    metadata = {
      "strategy_run_id": "run-1",
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "candidate_id": "candidate-1",
      "candidate_fingerprint": "a" * 64,
      "policy_version": "policy-3",
      "intent_id": "intent-1",
      "t_trade_role": role.lower(),
    }
    entry = role == "ENTRY"
    return {
      "schema_version": 1,
      "fact_key": f"paper-fill:run-1:{trade_id}",
      "trade_id": trade_id,
      "order_id": f"order-{trade_id}",
      "instrument_code": "600000.SH",
      "trade_type": "BUY" if entry else "SELL",
      "price": 10.0,
      "volume": 100,
      "amount": 1_000.0,
      "commission": 1.0,
      "trade_time": datetime.fromtimestamp(at).isoformat(),
      "metadata": metadata,
      "entry_complete": True if entry else None,
      "entry_target_volume": 100 if entry else None,
    }

  entry = fact(trade_id="z-entry", role="ENTRY", at=1_001)
  exit = fact(trade_id="a-exit", role="EXIT", at=1_002)
  runtime = _runtime(SimpleNamespace())
  runtime.context.mode = StrategyRunMode.PAPER
  # Deliberately model JSON object retrieval with EXIT before ENTRY.
  runtime.state_manager = PaperOutboxState({
    exit["fact_key"]: exit,
    entry["fact_key"]: entry,
  })
  applied_roles: list[str] = []

  async def record_trade_fact(*, trade, **_kwargs):
    applied_roles.append(str(trade.metadata["t_trade_role"]))
    return SimpleNamespace(
      definition=SimpleNamespace(instrument_code="600000.SH"),
      status=None,
      post_fill=None,
    )

  executor = StrategyExecutor(
    candidate_outcome_facade=SimpleNamespace(record_trade_fact=record_trade_fact)
  )
  await executor._replay_pending_t_trade_paper_fill_facts(runtime)

  assert applied_roles == ["entry", "exit"]
  assert runtime.state_manager.facts == {}


@pytest.mark.asyncio
async def test_corrupt_paper_fill_fact_is_not_acknowledged() -> None:
  class PaperOutboxState:
    def __init__(self, fact: dict) -> None:
      self.facts = {fact["fact_key"]: dict(fact)}
      self.last_snapshot_failure_code = None

    def pending_t_trade_paper_fill_facts(self) -> list[dict]:
      return [dict(item) for item in self.facts.values()]

    def acknowledge_t_trade_paper_fill_facts(self, keys: list[str]) -> None:
      for key in keys:
        self.facts.pop(key, None)

    async def force_save(self) -> bool:
      return True

  fact = {
    "schema_version": 1,
    "fact_key": "paper-fill:run-1:corrupt-fill",
    "trade_id": "corrupt-fill",
    "order_id": "order-corrupt",
    "instrument_code": "600000.SH",
    "trade_type": "BUY",
    "price": 10.0,
    "volume": 100,
    "amount": 1_000.0,
    "commission": 1.0,
    "trade_time": datetime.fromtimestamp(1_001).isoformat(),
    "metadata": {
      "strategy_run_id": "run-1",
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      # candidate_id intentionally missing: this fact must never be treated
      # as a successfully applied candidate result.
      "candidate_fingerprint": "a" * 64,
      "policy_version": "policy-3",
      "intent_id": "intent-1",
      "t_trade_role": "entry",
    },
    "entry_complete": True,
    "entry_target_volume": 100,
  }
  runtime = _runtime(SimpleNamespace())
  runtime.context.mode = StrategyRunMode.PAPER
  runtime.state_manager = PaperOutboxState(fact)
  executor = StrategyExecutor(
    candidate_outcome_facade=SimpleNamespace(record_trade_fact=AsyncMock())
  )

  with pytest.raises(ValueError, match="candidate|候选"):
    await executor._replay_pending_t_trade_paper_fill_facts(runtime)

  assert runtime.state_manager.facts == {fact["fact_key"]: fact}


@pytest.mark.asyncio
async def test_paper_fill_build_failure_fail_stops_before_a_later_tick_can_checkpoint_mutated_state() -> None:
  class PaperStrategy(StrategyBase):
    USES_T_TRADE_OPPORTUNITY_PROFILE = True

    @property
    def name(self) -> str:
      return "paper outbox regression strategy"

    @property
    def version(self) -> str:
      return "1"

    @property
    def description(self) -> str:
      return "test"

    @classmethod
    def get_parameter_schema(cls) -> dict:
      return {}

    async def on_init(self) -> None:
      return None

    async def on_stop(self) -> None:
      return None

    async def step(self, _input) -> object:
      raise AssertionError("tick is replaced in this regression test")

    async def on_trade(self, _event: TradeExecutionEvent) -> RuntimeStatePatch:
      return RuntimeStatePatch(set={"post_fill_state": "MUTATED"})

  class PaperStateManager:
    def __init__(self) -> None:
      self.checkpoints = 0

    def apply_trade(self, _trade) -> None:
      return None

    async def update_trade_intent_status(self, *_args, **_kwargs) -> None:
      return None

    async def checkpoint_strategy_state_changes(self) -> bool:
      self.checkpoints += 1
      return True

  context = StrategyContext(
    run_id="run-paper-build-failure",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1", "t_trade_opportunity_v3": True},
  )
  strategy = PaperStrategy(context)
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="paper-build-failure",
    strategy_id=1,
    strategy_class=PaperStrategy,
    context=context,
    strategy=strategy,
    broker=SimpleNamespace(),
    status=ExecutionStatus.RUNNING,
  )
  manager = PaperStateManager()
  runtime.state_manager = manager
  executor = StrategyExecutor()
  processed_ticks = 0

  async def later_tick(_runtime, _tick) -> None:
    nonlocal processed_ticks
    processed_ticks += 1
    await manager.checkpoint_strategy_state_changes()

  executor._process_tick = later_tick
  trade = TradeRecord(
    trade_id="paper-bad-fill",
    order_id="paper-bad-order",
    instrument_code="600000.SH",
    trade_type=OrderType.BUY,
    price=10.0,
    volume=100,
    amount=1_000.0,
    commission=1.0,
    trade_time=datetime.fromtimestamp(1_001),
    metadata={
      "account_id": "account-1",
      "strategy_run_id": context.run_id,
      "instrument_code": "600000.SH",
      "candidate_id": "candidate-1",
      # candidate_fingerprint deliberately missing so fact construction fails
      # only after the strategy's on_trade state patch has been applied.
      "policy_version": "policy-3",
      "intent_id": "intent-1",
      "t_trade_role": "entry",
    },
  )
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  try:
    await runtime.event_queue.put(("trade", trade))
    await runtime.event_queue.put(("tick", object()))
    await asyncio.wait_for(runtime.event_queue.join(), timeout=1.0)

    assert runtime.status is ExecutionStatus.ERROR
    assert strategy.state.get("post_fill_state") is None
    assert processed_ticks == 0
    assert manager.checkpoints == 0
  finally:
    runtime.status = ExecutionStatus.STOPPED
    runtime.event_task.cancel()
    await asyncio.gather(runtime.event_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_candidate_fill_rejects_non_durable_broker_callback() -> None:
  facade = SimpleNamespace(record_trade_fact=AsyncMock(return_value=None))
  executor = StrategyExecutor(candidate_outcome_facade=facade)
  live = _runtime(SimpleNamespace())
  live.context.mode = StrategyRunMode.LIVE
  trade = TradeRecord(
    trade_id="trade-live-undurable",
    order_id="order-1",
    instrument_code="600000.SH",
    trade_type=OrderType.BUY,
    price=10.0,
    volume=100,
    amount=1_000.0,
    commission=0.0,
    trade_time=datetime.fromtimestamp(1_003),
    metadata={"candidate_id": "candidate-1", "t_trade_role": "entry"},
  )

  await executor._record_t_trade_candidate_fill(
    live,
    trade,
    durable_event=False,
  )

  facade.record_trade_fact.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_outcome_repair_system_failure_uses_bounded_retry_backoff() -> None:
  facade = SimpleNamespace(
    reconcile_applied_trade_events=AsyncMock(side_effect=RuntimeError("db down")),
    observe_tick=AsyncMock(return_value=[]),
  )
  executor = StrategyExecutor(candidate_outcome_facade=facade)
  live = _runtime(SimpleNamespace())
  live.context.mode = StrategyRunMode.LIVE
  for source_time_ms, ordinal, instrument_code in (
    (1_001_000, 11, "600000.SH"),
    (1_002_000, 12, "000001.SZ"),
    (1_006_000, 13, "000001.SZ"),
  ):
    await executor._observe_t_trade_candidate_outcomes(
      live,
      input_snapshot=_input(
        source_time_ms,
        ordinal,
        instrument_code=instrument_code,
      ),
      market_data=MarketDataSnapshot(
        instrument_code=instrument_code,
        price=10.0,
      ),
    )

  assert facade.reconcile_applied_trade_events.await_count == 2
  assert executor._candidate_outcome_repair_attempts["run-1"] == 2
  assert executor._candidate_outcome_repair_retry_at_ms["run-1"] == 1_016_000


@pytest.mark.asyncio
async def test_finalize_failure_is_best_effort_live_but_fatal_for_strict_replay() -> (
  None
):
  facade = SimpleNamespace(finalize_run=AsyncMock(side_effect=RuntimeError("db down")))
  executor = StrategyExecutor(candidate_outcome_facade=facade)

  live = _runtime(SimpleNamespace())
  live.context.mode = StrategyRunMode.LIVE
  await executor._finalize_t_trade_candidate_outcomes(live)
  assert live.status is ExecutionStatus.RUNNING

  strict = _runtime(SimpleNamespace())
  strict.context.parameters["t_trade_replay"] = True
  with pytest.raises(RuntimeError, match="db down"):
    await executor._finalize_t_trade_candidate_outcomes(strict)
  assert strict.status is ExecutionStatus.ERROR
  assert strict.error_message == "T_TRADE_CANDIDATE_OUTCOME_FINALIZE_FAILED"
