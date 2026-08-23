"""
StrategyBase V2 contract tests.
"""

import asyncio
from datetime import datetime

import pytest
from quantx_domain.strategies.base import (
  MarketDataContext,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentType,
)
from quantx_domain.trading.decision_trace import summarize_intent
from quantx_infrastructure.models.parameter_schema import (
  ParameterProperty,
  ParameterSchema,
)


class MockStrategy(StrategyBase):
  @property
  def name(self) -> str:
    return "MockStrategy"

  @property
  def version(self) -> str:
    return "1.0.0"

  @property
  def description(self) -> str:
    return "Test strategy for unit tests"

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
      type="object",
      properties={
        "test_param": ParameterProperty(type="string", default="test_value")
      },
    )

  async def on_init(self) -> None:
    self.initialized = True

  async def step(self, input: StrategyInput) -> StrategyOutput:
    intent = TradeIntent(
      strategy_id=self.name,
      run_id=self.context.run_id,
      instrument_code=input.instrument_code,
      direction=TradeIntentDirection.BUY,
      bucket="swing",
      reason="unit_test_buy",
      target_amount=10000,
      limit_price_hint=10.5,
      metadata={"source": input.cadence.value},
    )
    return StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=RuntimeStatePatch(set={"last_step": input.timestamp.isoformat()}),
      decision_tags=["unit_test"],
    )

  async def on_stop(self) -> None:
    self.stopped = True


@pytest.fixture
def strategy_context():
  return StrategyContext(
    run_id="test-instance",
    mode=StrategyRunMode.BACKTEST,
    backtest_start_time=datetime.now(),
    instruments=["000001"],
    parameters={"test_param": "test_value"},
  )


@pytest.fixture
def strategy(strategy_context):
  return MockStrategy(strategy_context)


def make_input(strategy: StrategyBase) -> StrategyInput:
  return StrategyInput(
    run_id=strategy.context.run_id,
    strategy_id="1",
    timestamp=datetime(2024, 1, 2, 10, 0),
    cadence=StrategyCadence.BAR,
    instrument_code="000001",
    strategy_state=strategy.state.to_dict(),
    parameters=strategy.context.parameters,
  )


def test_summarize_intent_includes_limit_price_hint(strategy_context):
  intent = TradeIntent(
    strategy_id="MockStrategy",
    run_id=strategy_context.run_id,
    instrument_code="000001",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="unit_test_buy",
    target_volume=100,
    limit_price_hint=10.5,
  )

  summary = summarize_intent(intent)

  assert summary["limit_price_hint"] == 10.5


def test_strategy_initialization(strategy):
  assert strategy.context.run_id == "test-instance"
  assert strategy.context.mode == StrategyRunMode.BACKTEST
  assert not strategy.is_initialized
  assert not strategy.is_running
  assert len(strategy.trade_intents) == 0


def test_strategy_lifecycle(strategy):
  asyncio.run(strategy.initialize())
  assert strategy.is_initialized
  assert strategy.initialized

  asyncio.run(strategy.start())
  assert strategy.is_running

  asyncio.run(strategy.stop())
  assert not strategy.is_running
  assert strategy.stopped


def test_get_parameter(strategy):
  assert strategy.get_parameter("test_param") == "test_value"
  assert strategy.get_parameter("nonexistent", "default") == "default"


def test_step_generates_trade_intent(strategy):
  output = asyncio.run(strategy.step(make_input(strategy)))

  assert len(output.trade_intents) == 1
  intent = output.trade_intents[0]
  assert intent.direction == TradeIntentDirection.BUY
  assert intent.instrument_code == "000001"
  assert intent.bucket == "swing"
  assert intent.reason == "unit_test_buy"
  assert intent.target_amount == 10000
  assert output.runtime_state_patch.set["last_step"] == "2024-01-02T10:00:00"

  strategy.record_trade_intent(intent)
  assert len(strategy.trade_intents) == 1


def test_trade_intent_requires_bucket_reason_and_size():
  with pytest.raises(ValueError):
    TradeIntent(
      strategy_id="s",
      run_id="r",
      instrument_code="000001",
      direction=TradeIntentDirection.BUY,
      bucket="",
      reason="test",
      target_amount=100,
    )

  with pytest.raises(ValueError):
    TradeIntent(
      strategy_id="s",
      run_id="r",
      instrument_code="000001",
      direction=TradeIntentDirection.BUY,
      bucket="swing",
      reason="",
      target_amount=100,
    )

  with pytest.raises(ValueError):
    TradeIntent(
      strategy_id="s",
      run_id="r",
      instrument_code="000001",
      direction=TradeIntentDirection.BUY,
      bucket="swing",
      reason="test",
    )


def test_trade_intent_infers_intent_type_and_strategy_input_trace_fields(strategy):
  amount_intent = TradeIntent(
    strategy_id="s",
    run_id="r",
    instrument_code="000001",
    direction=TradeIntentDirection.BUY,
    bucket="core",
    reason="amount_buy",
    target_amount=1000,
  )
  assert amount_intent.intent_type == TradeIntentType.TARGET_AMOUNT

  pct_intent = TradeIntent(
    strategy_id="s",
    run_id="r",
    instrument_code="000001",
    direction=TradeIntentDirection.BUY,
    bucket="core",
    reason="pct_buy",
    target_position_pct=0.2,
  )
  assert pct_intent.intent_type == TradeIntentType.TARGET_POSITION_PCT

  strategy_input = make_input(strategy)
  assert strategy_input.input_id
  assert strategy_input.trace_id
  assert strategy_input.decision_time_ms == int(strategy_input.timestamp.timestamp() * 1000)
  assert strategy_input.trade_date == "2024-01-02"


def test_market_data_context_has_stable_causal_identity():
  context = MarketDataContext(
    source="REPLAY",
    stream_id="run-1:replay",
    continuity_generation=2,
    source_sequence=17,
    source_time_ms=1_704_186_000_123,
    tick_ordinal=3,
    received_at_ms=1_704_186_000_123,
    session="CONTINUOUS_AM",
    trade_date="2024-01-02",
  )

  assert context.source_identity == (2, 1_704_186_000_123, 3)
  assert context.session.is_continuous is True
  assert context.trade_date.isoformat() == "2024-01-02"


def test_runtime_state_patch_rejects_account_fields():
  with pytest.raises(ValueError):
    RuntimeStatePatch(set={"available_cash": 1000})


def test_runtime_state_patch_rejects_nested_account_fields_with_paths():
  with pytest.raises(ValueError) as exc_info:
    RuntimeStatePatch(
      set={
        "instrument_states": {
          "600000.SH": {
            "snapshots": [
              {"algorithm_phase": "READY"},
              {"available_volume": 100},
            ]
          }
        }
      }
    )

  assert "$.instrument_states.600000.SH.snapshots[1].available_volume" in str(
    exc_info.value
  )


@pytest.mark.parametrize(
  "field_name",
  (
    "position_shares",
    "position_available_shares",
    "available_shares",
    "sellable_volume",
    "requested_entry_volume",
    "final_volume",
  ),
)
def test_runtime_state_patch_rejects_v3_account_and_legal_volume_aliases(
  field_name,
):
  with pytest.raises(ValueError) as exc_info:
    RuntimeStatePatch(
      set={"instrument_states": {"600000.SH": {field_name: 100}}}
    )

  assert f"$.instrument_states.600000.SH.{field_name}" in str(exc_info.value)


def test_runtime_state_patch_rejects_account_fields_nested_in_append_events():
  with pytest.raises(ValueError) as exc_info:
    RuntimeStatePatch(
      append_events=[
        {
          "event_type": "DECISION",
          "payload": {"risk": [{"SeLlAbLe_VoLuMe": 100}]},
        }
      ]
    )

  assert "$.append_events[0].payload.risk[0].SeLlAbLe_VoLuMe" in str(
    exc_info.value
  )


def test_runtime_state_patch_allows_algorithm_and_execution_projection_fields():
  patch = RuntimeStatePatch(
    set={
      "instrument_states": {
        "600000.SH": {
          "active_volume": 100,
          "entry_filled_volume": 100,
          "opportunity": {"sample_count": 20},
        }
      }
    },
    append_events=[
      {
        "event_type": "EXECUTION_PROJECTION",
        "payload": {"active_volume": 100, "entry_filled_volume": 100},
      }
    ],
  )

  assert patch.set["instrument_states"]["600000.SH"]["active_volume"] == 100


@pytest.mark.asyncio
async def test_state_subscriber_queue_drop_balances_unfinished_tasks(strategy):
  queue = strategy.subscribe_state(maxsize=1)
  strategy.state.set("checkpoint", 1)
  strategy.state.set("checkpoint", 2)

  event = queue.get_nowait()
  assert event.changes == {"checkpoint": 2}
  queue.task_done()
  await asyncio.wait_for(queue.join(), timeout=0.1)


def test_get_statistics(strategy):
  stats = strategy.get_statistics()

  assert "trade_intents_count" in stats
  assert "orders_count" in stats
  assert "positions" in stats
  assert stats["trade_intents_count"] == 0
