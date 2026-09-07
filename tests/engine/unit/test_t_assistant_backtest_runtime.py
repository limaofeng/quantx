"""BACKTEST uses the actual strategy and shared execution rules without SQL."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionGatePolicy,
  MarketDataCapabilityManifest,
)
from quantx_application.t_trade_v3.portfolio_snapshot import (
  TPortfolioPolicy,
  TTradingEnvelopePolicy,
)
from quantx_domain.brokers.base import Position
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_execution import (
  TAssistantEntryReadinessProjection,
  TAssistantExecution,
)
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
)
from quantx_engine.t_assistant_backtest_runtime import TAssistantBacktestRuntime
from quantx_engine.t_assistant_backtest_timeline import BacktestTick

AT = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)
CODES = ("600000.SH", "000001.SZ")


def runtime(*, request_only=False):
  policy = OpportunityPolicy()
  execution = TAssistantExecution(
    "backtest-1",
    "config-1",
    "version-1",
    1,
    "a" * 64,
    "backtest-account",
    "BACKTEST",
    "MANUAL_CONFIRM",
    "CANARY",
    "RUNNING",
    TAssistantEntryReadinessProjection("READY", (), AT),
    policy.policy_version,
    policy.feature_schema_version,
    "RULE_ONLY",
    started_at=AT,
  )
  options = dict(
    parameters={"signal_policy": policy.to_dict(), "target_trade_amount": 10000},
    portfolio_policy=TPortfolioPolicy(
      "p-v1", Decimal(20000), Decimal(1), Decimal(0), Decimal(20000), 2, Decimal(1000)
    ),
    envelope_policies={
      c: TTradingEnvelopePolicy("e-v1", 0, Decimal(10000), 100) for c in CODES
    },
    industries={c: "bank" for c in CODES},
    profiles={
      c: OpportunityReferenceProfile("profile-1", 1, "2026-09-02", 0.8, 0.8, 2.0, 3, 10)
      for c in CODES
    },
    gate_policy=EntryExecutionGatePolicy("gate-v1", 3000, 100, 30),
    capabilities=MarketDataCapabilityManifest(
      "book-v1",
      frozenset({"price", "bid_price", "ask_price", "bid_volume", "ask_volume"}),
    ),
    initial_cash=25000,
    initial_positions={
      c: Position(
        c,
        long_volume=1000,
        available_volume=1000,
        long_avg_price=100,
        last_price=100,
        market_value=100000,
      )
      for c in CODES
    },
    initial_buckets={
      c: {
        "core": {
          "total_volume": 1000,
          "available_volume": 1000,
          "avg_price": 100,
          "last_price": 100,
        }
      }
      for c in CODES
    },
    broker_parameters={"slippage_rate": 0},
    trading_days=(date(2026, 9, 2), date(2026, 9, 3)),
    prior_close_marks={},
  )
  if request_only:
    from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
    from quantx_engine.t_assistant_backtest_run import BacktestRequest

    config = TAssistantConfigVersion.create(
      config_id="config-1",
      config_version_id="version-1",
      version=1,
      config_schema_version="v1",
      canonical_payload=options["parameters"],
      entry_authorization="MANUAL_CONFIRM",
      rollout_stage="CANARY",
      policy_version=policy.policy_version,
      feature_schema_version=policy.feature_schema_version,
    )
    return BacktestRequest(config, AT, options)
  return TAssistantBacktestRuntime(execution=execution, **options)


def ticks():
  shape = [
    (0, 100),
    (5, 99),
    (20, 99),
    (22, 99.30),
    (24, 99.32),
    (25, 99.32),
    (26, 99.31),
    (27, 99.31),
    (28, 105),
    (29, 102),
    (30, 102),
    (31, 102),
  ]
  events = []
  for index, (seconds, price) in enumerate(shape, 1):
    at = AT + timedelta(seconds=seconds)
    ms = int(at.timestamp() * 1000)
    for offset, code in enumerate(CODES):
      sample = OpportunitySample(
        code,
        "2026-09-03",
        ms,
        index,
        price,
        continuity_generation="7",
        received_at_ms=ms,
        bid_price=price - 0.01,
        ask_price=price,
        bid_volume=1000,
        ask_volume=1000,
        cumulative_amount=1000000 + index * 10000,
        cumulative_volume=10000 + index * 100,
      )
      tick = AcceptedTMarketTick(
        "stream-1", index, ms, sample, market_fence_sequence=index * 2 + offset
      )
      market = MarketDataSnapshot(
        code,
        at,
        price,
        source="backtest-tick",
        limit_up=110,
        limit_down=90,
        bid_price=[price - 0.01 - i * 0.01 for i in range(5)],
        ask_price=[price + i * 0.01 for i in range(5)],
        bid_vol=[1000] * 5,
        ask_vol=[1000] * 5,
      )
      events.append(
        BacktestTick(at, index * 2 + offset, f"{code}:{index}", tick, market)
      )
  return events


async def test_two_symbol_shared_account_rule_only():
  run = runtime()
  frames = await run.run(ticks())
  assert len(run.broker.orders) == 4, [
    (a["type"], a)
    for f in frames
    for a in f["audit"]
    if a["type"] in {"ALLOCATION", "GATE", "RISK"}
  ]
  assert all(p.remaining_volume == 0 for p in run.plans.plans.values())
  assert frames[-1]["evidence"]["volumes"] == {c: 1000 for c in CODES}
  replay = runtime()
  other = await replay.run(list(reversed(ticks())))

  def compare(left, right, path=""):
    if isinstance(left, dict):
      for key in left:
        compare(left[key], right[key], f"{path}/{key}")
    elif isinstance(left, list):
      assert len(left) == len(right), path
      for i, (a, b) in enumerate(zip(left, right)):
        compare(a, b, f"{path}/{i}")
    else:
      assert left == right, (path, left, right)

  compare(frames, other)


async def test_independent_version_and_recovery(tmp_path):
  from quantx_engine.t_assistant_backtest_run import execute_backtest

  request = runtime(request_only=True)
  kwargs = dict(
    request=request, events=ticks(), code_manifest={"test_code": "v1"}, root=tmp_path
  )
  store, first, result = await execute_backtest(**kwargs)
  _, recovered, same = await execute_backtest(
    **kwargs, resume_directory=store.directory
  )
  assert same == result
  assert len(recovered.broker.trades) == len(first.broker.trades)
  second_store, _, second = await execute_backtest(**kwargs)
  assert store.directory != second_store.directory
  assert (
    second["material"]["result"]["economic_hash"]
    == result["material"]["result"]["economic_hash"]
  )
