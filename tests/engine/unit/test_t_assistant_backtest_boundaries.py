"""Adversarial shared-cash, causality and durable-prefix BACKTEST cases."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from quantx_application.t_trade_v3.daily_t_valuation import TValuationMark
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_engine.t_assistant_backtest_run import execute_backtest
from quantx_infrastructure.services.t_assistant_backtest_store import (
  TAssistantBacktestStore,
)

from tests.engine.unit.test_t_assistant_backtest_runtime import (
  AT,
  CODES,
  runtime,
  ticks,
)


def move(item, delta, *, depth=None):
  at = item.decision_time + delta
  sample = item.tick.sample
  ms_delta = int(delta.total_seconds() * 1000)
  sample = replace(
    sample,
    source_time_ms=sample.source_time_ms + ms_delta,
    received_at_ms=sample.received_at_ms + ms_delta,
    trade_date=at.date().isoformat(),
    bid_volume=sample.bid_volume if depth is None else depth,
    ask_volume=sample.ask_volume if depth is None else depth,
  )
  tick = replace(
    item.tick, sample=sample, received_at_ms=item.tick.received_at_ms + ms_delta
  )
  market = replace(
    item.market,
    timestamp=item.market.timestamp + delta,
    bid_vol=item.market.bid_vol if depth is None else [depth] * 5,
    ask_vol=item.market.ask_vol if depth is None else [depth] * 5,
  )
  return replace(item, decision_time=at, tick=tick, market=market)


async def test_partial_fills_charge_minimum_once_and_substitute_core():
  events = [
    move(e, timedelta(0), depth=200) if e.tick.accepted_sequence == 6 else e
    for e in ticks()
  ]
  run = runtime()
  frames = await run.run(events)
  assert len(run.broker.trades) == 6
  assert [f.volume for f in run.broker.trades[:2]] == [50, 50]
  for order in run.broker.orders.values():
    assert order.commission == pytest.approx(
      sum(f.commission for f in run.broker.trades if f.order_id == order.order_id)
    )
    assert order.filled_volume == 100
  for code in CODES:
    holding = run._holding(code)
    assert holding["core_volume"] == 1000
    assert holding["core_today_buy_volume"] == 100
    assert holding["available_volume"] == 900
    assert holding["locked_core_volume"] == 0
  assert frames[-1]["evidence"]["cash_error"] == 0
  assert any(
    a["value"].get("substitution_plan")
    for f in frames
    for a in f["audit"]
    if a["type"] == "RISK"
  )


async def test_simultaneous_signals_share_scarce_cash_in_stable_rank():
  request = runtime(request_only=True)
  request.runtime_options["initial_cash"] = 15000
  run = request.runtime("cash-scarcity")
  frames = await run.run(ticks())
  buys = [
    o for o in run.broker.orders.values() if o.request.order_type is OrderType.BUY
  ]
  assert len(buys) == 1
  assert buys[0].request.instrument_code == "000001.SZ"
  assert min(f["evidence"]["cash"] for f in frames) >= 0
  assert any(
    d["action"] in {"REJECT", "DELAY", "CAP"}
    for f in frames
    for a in f["audit"]
    if a["type"] == "ALLOCATION"
    for d in a["value"]
  )


async def test_exact_old_inventory_capacity_is_not_double_reserved():
  request = runtime(request_only=True)
  for code in CODES:
    request.runtime_options["initial_positions"][code] = replace(
      request.runtime_options["initial_positions"][code],
      long_volume=100,
      available_volume=100,
      market_value=10000,
    )
    request.runtime_options["initial_buckets"][code]["core"].update(
      total_volume=100, available_volume=100
    )
  run = request.runtime("exact-capacity")
  await run.run(ticks())
  assert len(run.broker.orders) == 4
  for code in CODES:
    assert run._holding(code)["core_volume"] == 100
    assert run._holding(code)["available_volume"] == 0


async def test_protected_core_cannot_be_used_for_entry():
  request = runtime(request_only=True)
  for code in CODES:
    request.runtime_options["envelope_policies"][code] = replace(
      request.runtime_options["envelope_policies"][code], protected_core_volume=1000
    )
  run = request.runtime("protected")
  await run.run(ticks())
  assert not run.broker.orders


async def test_overnight_uses_calendar_marks_and_settles_inventory():
  request = runtime(request_only=True)
  request.runtime_options["trading_days"] += (date(2026, 9, 4),)
  request.runtime_options["prior_close_marks"] = {
    date(2026, 9, 3): {
      code: TValuationMark(
        code, Decimal("99.31"), AT + timedelta(hours=5, minutes=30), "official-close"
      )
      for code in CODES
    }
  }
  events = [
    e if e.tick.accepted_sequence <= 8 else move(e, timedelta(days=1)) for e in ticks()
  ]
  run = request.runtime("carry")
  await run.run(events)
  assert len(run.broker.orders) == 4
  assert all(p.remaining_volume == 0 for p in run.plans.plans.values())
  for code in CODES:
    assert run._holding(code)["available_volume"] == 1000
    assert run._holding(code)["core_today_buy_volume"] == 0


async def test_cutoff_cancels_buy_before_matching_new_quote():
  # Signal at 14:49:59, first possible fill at 14:50:00.
  delta = timedelta(hours=5, minutes=19, seconds=35)
  run = runtime()
  frames = await run.run([move(e, delta) for e in ticks()])
  assert len(run.broker.orders) == 2
  assert not run.broker.trades
  assert all(o.status.value == "CANCELLED" for o in run.broker.orders.values())
  assert frames[-1]["evidence"]["cash"] == 25000


async def test_no_entry_at_or_after_cutoff():
  run = runtime()
  await run.run([move(e, timedelta(hours=5, minutes=20)) for e in ticks()])
  assert not run.broker.orders


@pytest.mark.parametrize(
  "environment", [ExecutionEnvironment.PAPER, ExecutionEnvironment.LIVE]
)
async def test_broker_rejects_other_environments(environment):
  run = runtime()
  run.broker.current_time = AT
  request = OrderRequest(
    CODES[0],
    OrderType.BUY,
    PriceType.LIMIT,
    100,
    run.execution.execution_ref,
    environment,
    price=100,
  )
  with pytest.raises(ValueError, match="SCOPE_OR_CLOCK"):
    await run.broker.place_order(request)
  assert not run.broker.orders


async def test_broker_rejects_other_execution_and_unknown_exit_owner():
  run = runtime()
  run.broker.current_time = AT
  for owner in (
    ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "other"),
    ExecutionOwnerRef("EXIT_PLAN", "unknown"),
  ):
    request = OrderRequest(
      CODES[0],
      OrderType.BUY,
      PriceType.LIMIT,
      100,
      owner,
      ExecutionEnvironment.BACKTEST,
      price=100,
      metadata={"source_execution_ref": run.execution.execution_ref.to_dict()},
    )
    with pytest.raises(ValueError, match="SCOPE_OR_CLOCK"):
      await run.broker.place_order(request)


async def test_future_profile_and_duplicate_source_are_rejected():
  run = runtime()
  run.profiles[CODES[0]] = replace(
    run.profiles[CODES[0]], as_of_trade_date="2026-09-03"
  )
  with pytest.raises(ValueError, match="FUTURE_PROFILE"):
    await run.run(ticks())
  with pytest.raises(ValueError, match="DUPLICATE_SOURCE"):
    await runtime().run(ticks() + ticks()[:1])
  with pytest.raises(ValueError, match="FUTURE_OR_MIXED_MARKET"):
    replace(ticks()[0], decision_time=AT - timedelta(seconds=1))


@pytest.mark.parametrize("crash_index", [6, 9])
async def test_crash_prefix_resume_and_changed_input_block(
  tmp_path, monkeypatch, crash_index
):
  request = runtime(request_only=True)
  events = [
    move(e, timedelta(0), depth=200) if e.tick.accepted_sequence == 6 else e
    for e in ticks()
  ]
  kwargs = dict(
    request=request, events=events, code_manifest={"test_code": "v1"}, root=tmp_path
  )
  original = TAssistantBacktestStore.commit_frame

  def crash(self, **values):
    if values["index"] == crash_index:
      raise RuntimeError("injected crash before publication")
    return original(self, **values)

  monkeypatch.setattr(TAssistantBacktestStore, "commit_frame", crash)
  with pytest.raises(RuntimeError, match="injected crash"):
    await execute_backtest(**kwargs)
  directory = next(tmp_path.iterdir())
  assert len(list(TAssistantBacktestStore(directory).frames())) == crash_index
  monkeypatch.setattr(TAssistantBacktestStore, "commit_frame", original)
  store, recovered, result = await execute_backtest(
    **kwargs, resume_directory=directory
  )
  assert len(recovered.broker.orders) == 4
  assert len(recovered.broker.trades) == 6
  assert len(list(store.frames())) == 12
  assert result["material"]["result"]["conservation"]["cash_error"] == 0
  with pytest.raises(ValueError, match="RESUME_INPUT_CHANGED"):
    await execute_backtest(
      **{**kwargs, "code_manifest": {"test_code": "v2"}}, resume_directory=directory
    )
