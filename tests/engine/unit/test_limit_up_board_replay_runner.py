from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from quantx_domain.market import Tick
from quantx_domain.strategies.base import (
  StrategyContext,
  StrategyRunMode,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_engine.limit_up_board_replay import (
  LimitUpBoardReplayRunner,
  ReplayDelayScenario,
)
from quantx_engine.replay_clock import ReplayClock


class _ExitPlans:
  @staticmethod
  def active_plans():
    return []


class _ExecutionPort:
  def __init__(self, signal_times=()):
    self.calls = []
    self.signal_times = set(signal_times)
    self.signalled = set()

  async def advance_replay_time(self, runtime, timestamp):
    if runtime.replay_clock is None:
      runtime.replay_clock = ReplayClock(timestamp)
    else:
      runtime.replay_clock.advance_to(timestamp)
    runtime.context.current_time = timestamp
    self.calls.append(("advance", timestamp, ""))

  async def process_replay_tick(self, runtime, tick):
    if runtime.replay_clock is None:
      runtime.replay_clock = ReplayClock(tick.time)
    else:
      runtime.replay_clock.advance_to(tick.time)
    runtime.context.current_time = tick.time
    self.calls.append(("tick", tick.time, tick.stock_code))
    signal_key = (tick.time, tick.stock_code)
    if (
      tick.time not in self.signal_times
      or tick.stock_code not in runtime.context.instruments
      or signal_key in self.signalled
    ):
      return
    self.signalled.add(signal_key)
    expiry = int((tick.time + timedelta(seconds=15)).timestamp() * 1000)
    intent = TradeIntent(
      strategy_id="1",
      run_id=runtime.context.run_id,
      instrument_code=tick.stock_code,
      direction=TradeIntentDirection.BUY,
      bucket="swing",
      reason="replay-test",
      target_volume=100,
      execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
      approval_ttl_ms=15_000,
      expiry_policy={"type": "TTL_MS", "expire_at_ms": expiry},
      created_at=tick.time,
    )
    runtime.pending_approvals[intent.intent_id] = intent

  async def reconcile_replay_universe(
    self,
    runtime,
    instruments,
    instrument_metadata,
  ):
    runtime.context.instruments = list(instruments)
    self.calls.append(("universe", runtime.context.current_time, tuple(instruments)))
    return {"added": list(instruments), "removed": [], "instruments": instruments}

  async def approve_replay_intent(self, runtime, intent_id):
    self.calls.append(("approval", runtime.context.current_time, intent_id))
    if runtime.pending_approvals.pop(intent_id, None) is None:
      return {"success": False, "code": "INTENT_NOT_AWAITING_APPROVAL"}
    return {"success": True, "code": "APPROVED"}

  async def reject_replay_intent(self, runtime, intent_id, reason):
    self.calls.append(("reject", runtime.context.current_time, reason))
    existed = runtime.pending_approvals.pop(intent_id, None) is not None
    return {"success": existed, "code": "REJECTED"}

  async def cancel_replay_open_buy_orders(self, runtime, reason):
    self.calls.append(("cancel-buy", runtime.context.current_time, reason))
    return 0

  async def wait_replay_reports(self, runtime):
    return None

  async def report_replay_progress(self, runtime, processed_until):
    return None

  @staticmethod
  def replay_sticky_instruments(runtime):
    return set(runtime.broker.positions)


def _runtime(start, end, *, instruments=()):
  context = StrategyContext(
    run_id="board-replay",
    mode=StrategyRunMode.BACKTEST,
    instruments=list(instruments),
    parameters={
      "limit_up_board_replay": True,
      "max_ranked_candidates": 5,
      "max_single_position_pct": 0.02,
      "planned_tail_loss_pct": 0.0015,
      "liquidity_participation_pct": 0.005,
    },
    backtest_start_time=start,
    backtest_end_time=end,
  )
  return SimpleNamespace(
    context=context,
    replay_clock=ReplayClock(start),
    pending_approvals={},
    broker=SimpleNamespace(positions={}, orders={}),
    exit_plan_book=_ExitPlans(),
  )


def _candidate(code, observed_at):
  return {
    "code": code,
    "stage": "NEAR_LIMIT",
    "promotion_eligible": True,
    "is_stale": False,
    "blocked_reasons": [],
    "promotion_score": 90.0,
    "radar_score": 80.0,
    "updated_at": observed_at.isoformat(),
    "cvar95_loss_pct": 8.0,
    "amount": 10_000_000.0,
  }


@pytest.mark.asyncio
async def test_runner_orders_tick_then_universe_then_delayed_approval() -> None:
  started_at = datetime(2024, 1, 2, 9, 30)
  signal_at = started_at + timedelta(seconds=1)
  approval_at = signal_at + timedelta(milliseconds=500)
  runtime = _runtime(started_at, started_at + timedelta(seconds=2))
  execution = _ExecutionPort(signal_times={signal_at})
  runner = LimitUpBoardReplayRunner(
    execution,
    ReplayDelayScenario(
      scenario_id="FAST",
      label="快速确认",
      confirmation_delay_ms=500,
      participation_cap_pct=0.03,
      book_depth_participation_pct=0.25,
    ),
  )

  result = await runner.run(
    runtime,
    universe_events=[
      {
        "observed_at": started_at.isoformat(),
        "snapshot_id": "frame-1",
        "candidates": [_candidate("000001.SZ", started_at)],
      }
    ],
    ticks=[
      Tick(stock_code="000001.SZ", time=started_at, last_price=9.99),
      Tick(stock_code="000001.SZ", time=signal_at, last_price=10.0),
      Tick(stock_code="000001.SZ", time=approval_at, last_price=10.01),
    ],
  )

  trace = [(item["kind"], item["timestamp"]) for item in result.event_trace]
  assert trace[:2] == [
    ("MARKET_TICK", started_at.isoformat()),
    ("UNIVERSE_SNAPSHOT", started_at.isoformat()),
  ]
  assert trace[-2:] == [
    ("MARKET_TICK", approval_at.isoformat()),
    ("APPROVAL_DUE", approval_at.isoformat()),
  ]
  assert result.entry_intents == 1
  assert result.approval_approved == 1
  assert result.pending_approvals == 0


@pytest.mark.asyncio
async def test_runner_rejects_pending_buy_at_window_end_without_closing_position() -> None:
  started_at = datetime(2024, 1, 2, 9, 30)
  signal_at = started_at + timedelta(seconds=1)
  end_at = started_at + timedelta(seconds=2)
  runtime = _runtime(started_at, end_at, instruments=["000001.SZ"])
  runtime.broker.positions["600000.SH"] = SimpleNamespace(
    long_volume=100,
    available_volume=0,
    today_buy_volume=100,
    long_avg_price=10.0,
    last_price=10.0,
    market_value=1_000.0,
  )
  execution = _ExecutionPort(signal_times={signal_at})
  runner = LimitUpBoardReplayRunner(
    execution,
    ReplayDelayScenario(
      scenario_id="STRESS",
      label="压力情景",
      confirmation_delay_ms=10_000,
      participation_cap_pct=0.01,
      book_depth_participation_pct=0.05,
    ),
  )

  result = await runner.run(
    runtime,
    universe_events=[],
    ticks=[Tick(stock_code="000001.SZ", time=signal_at, last_price=10.0)],
  )

  assert result.approval_approved == 0
  assert result.approval_rejected == 1
  assert result.pending_approvals == 0
  assert result.open_positions["600000.SH"]["long_volume"] == 100
  assert any(call[0] == "reject" and call[2] == "REPLAY_WINDOW_END" for call in execution.calls)


@pytest.mark.asyncio
async def test_runner_uses_tick_ordinal_before_code_for_same_millisecond() -> None:
  timestamp = datetime(2024, 1, 2, 9, 30)
  runtime = _runtime(timestamp, timestamp, instruments=["000001.SZ", "600000.SH"])
  execution = _ExecutionPort()
  runner = LimitUpBoardReplayRunner(
    execution,
    ReplayDelayScenario(
      scenario_id="THEORETICAL",
      label="理论上界",
      confirmation_delay_ms=0,
      participation_cap_pct=0.05,
      book_depth_participation_pct=0.5,
      is_theoretical_upper_bound=True,
    ),
  )

  await runner.run(
    runtime,
    universe_events=[],
    ticks=[
      {
        "stock_code": "000001.SZ",
        "time": timestamp,
        "tick_ordinal": 2,
        "last_price": 10.0,
      },
      {
        "stock_code": "600000.SH",
        "time": timestamp,
        "tick_ordinal": 1,
        "last_price": 10.0,
      },
    ],
  )

  tick_codes = [call[2] for call in execution.calls if call[0] == "tick"]
  assert tick_codes == ["600000.SH", "000001.SZ"]
