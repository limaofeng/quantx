"""Bounded, offline RULE_ONLY reducer measurements; no data or trading services.

Run with the quantx Conda Python. JSON timings describe this machine only and
do not approve P7 performance, AUTO release, or model evaluation.
"""

import argparse
import json
import math
import platform
from time import perf_counter

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_domain.trading.t_assistant_market_state import (
  AcceptedTMarketTick,
  SymbolDecisionSnapshot,
  SymbolDeltaCoverage,
  SymbolMarketDeltaRing,
  SymbolMarketStateReducer,
  TAssistantSymbolState,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityGateContext,
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
)


def tick(code, sequence, *, at=None):
  at = 1_000_000 + sequence * 200 if at is None else at
  price = 10 + math.sin(sequence / 50) * 0.1
  return AcceptedTMarketTick(
    stream_id="offline", accepted_sequence=sequence, received_at_ms=at,
    sample=OpportunitySample(
      instrument_code=code, trade_date="2026-09-10", source_time_ms=at,
      tick_ordinal=sequence, price=price, continuity_generation="1",
      received_at_ms=at, bid_price=price - 0.01, ask_price=price + 0.01,
      bid_volume=1000, ask_volume=1000,
      cumulative_volume=sequence * 1000, cumulative_amount=sequence * 10000,
    ),
  )


def initial(code, policy):
  return TAssistantSymbolState.initial(
    execution_id="offline", instrument_code=code,
    policy_version=policy.policy_version, feature_schema_version=policy.feature_schema_version,
    trade_date="2026-09-10",
  )


def snapshot(code, state, delta):
  return SymbolDecisionSnapshot(
    instrument_code=code, state=state, delta_slice=delta,
    gate_context=OpportunityGateContext(),
    reference_profile=OpportunityReferenceProfile(
      profile_version="synthetic-prior", profile_schema_version=1,
      as_of_trade_date="2026-09-09", pullback_threshold_pct=0.5,
      momentum_rise_threshold_pct=0.5, momentum_amount_velocity_ratio=1.2,
      pullback_max_spread_ticks=3, momentum_max_spread_ticks=3,
    ),
  )


def measure(symbols, ticks, batch_size):
  policy, reducer = OpportunityPolicy(), SymbolMarketStateReducer()
  timings, hashes = [], []
  evaluated = 0
  started = perf_counter()
  for symbol in range(symbols):
    code = f"{600000 + symbol}.SH"
    ring, state = SymbolMarketDeltaRing(code), initial(code, policy)
    for sequence in range(1, ticks + 1):
      item = tick(code, sequence)
      ring.accept(item, capture_time_ms=item.received_at_ms)
      if sequence % batch_size and sequence != ticks:
        continue
      before = perf_counter()
      delta = ring.slice_after(state.cursor, decision_time_ms=item.received_at_ms)
      if delta.coverage is not SymbolDeltaCoverage.READY:
        raise RuntimeError(f"unexpected coverage: {delta.coverage}")
      reduction = reducer.reduce(snapshot(code, state, delta), policy=policy)
      timings.append((perf_counter() - before) * 1000)
      evaluated += len(reduction.evaluations)
      state = reduction.next_state
    if state.cursor.accepted_sequence != ticks:
      raise RuntimeError("reducer did not consume the complete input")
    hashes.append(stable_manifest_hash(state.opportunity_state.to_dict()))
  timings.sort()
  if evaluated != symbols * ticks:
    raise RuntimeError("accepted ticks were skipped or evaluated more than once")
  return {
    "batch_size": batch_size, "accepted_ticks": symbols * ticks,
    "evaluated_ticks": evaluated, "reductions": len(timings),
    "wall_seconds": perf_counter() - started,
    "reduction_ms": {
      "p50": timings[math.ceil(len(timings) * 0.5) - 1],
      "p95": timings[math.ceil(len(timings) * 0.95) - 1],
      "max": timings[-1],
    },
    "final_opportunity_hashes": hashes,
  }


def safety_checks():
  results = {}
  for label, count, delay, expected in (
    ("tick_lag", 514, 0, SymbolDeltaCoverage.REDUCER_LAG_EXCEEDED),
    ("time_lag", 2, 2001, SymbolDeltaCoverage.REDUCER_LAG_EXCEEDED),
    ("ring_overflow", 4098, 0, SymbolDeltaCoverage.RING_OVERFLOW),
  ):
    code, policy = "600000.SH", OpportunityPolicy()
    ring, state = SymbolMarketDeltaRing(code), initial(code, policy)
    ring.accept(tick(code, 1, at=1000), capture_time_ms=1000)
    first_delta = ring.slice_after(None, decision_time_ms=1000)
    state = SymbolMarketStateReducer().reduce(snapshot(code, state, first_delta), policy=policy).next_state
    for sequence in range(2, count + 1):
      ring.accept(tick(code, sequence, at=1001), capture_time_ms=1001)
    delta = ring.slice_after(state.cursor, decision_time_ms=1001 + delay)
    reduction = SymbolMarketStateReducer().reduce(snapshot(code, state, delta), policy=policy)
    if delta.coverage is not expected or reduction.opportunities or reduction.evaluations:
      raise RuntimeError(f"fail-closed check failed: {label}")
    if reduction.next_state.rewarm_reason != expected.value:
      raise RuntimeError(f"missing explicit rewarm reason: {label}")
    results[label] = delta.coverage.value
  return results


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--symbols", type=int, default=3)
  parser.add_argument("--ticks", type=int, default=1024, help="ticks per symbol")
  args = parser.parse_args()
  if not 1 <= args.symbols <= 32 or not 64 <= args.ticks <= 4096 or args.symbols * args.ticks > 32768:
    parser.error("require 1..32 symbols, 64..4096 ticks, at most 32768 total ticks")
  runs = [measure(args.symbols, args.ticks, batch) for batch in (1, 8)]
  if runs[0]["final_opportunity_hashes"] != runs[1]["final_opportunity_hashes"]:
    raise RuntimeError("batching changed the final opportunity state")
  print(json.dumps({
    "schema": "t-reducer-offline-benchmark.v1", "platform": platform.platform(),
    "python": platform.python_version(), "scorer_mode": "RULE_ONLY",
    "performance_gate": "NOT_EVALUATED", "symbols": args.symbols,
    "ticks_per_symbol": args.ticks, "runs": runs,
    "batching_state_equal": True, "fail_closed": safety_checks(),
  }, indent=2))


if __name__ == "__main__":
  main()
