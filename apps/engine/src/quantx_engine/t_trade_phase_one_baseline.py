"""Replay/shadow accumulator for the frozen phase-one T-trade baseline."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from quantx_domain.strategies.base import StrategyCadence, StrategyInput
from quantx_domain.trading.t_trade_candidate_outcome import (
  CandidateOutcomeDefinition,
  CandidateOutcomeState,
  CandidatePriceObservation,
  finalize_candidate_outcome,
  observe_candidate_outcome,
  start_candidate_outcome,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityPath,
  OpportunitySample,
)
from quantx_domain.trading.t_trade_phase_one_baseline import (
  PHASE_ONE_BASELINE_VERSION,
  PhaseOneBaselineEvaluation,
  PhaseOneBaselineState,
  reduce_phase_one_baseline,
)

PHASE_ONE_BASELINE_REPORT_SCHEMA_VERSION = 1
_MAX_SOURCE_INTERVAL_MS = 60_000


@dataclass
class _InstrumentClock:
  trade_date: str
  continuity_generation: str
  source_time_ms: int
  data_ready: bool


@dataclass
class _CommonReadyClock:
  trade_date: str
  continuity_generation: str
  source_time_ms: int
  common_ready: bool


@dataclass
class TTradePhaseOneBaselineAccumulator:
  """Bounded run-local comparison evidence; never consulted by the strategy."""

  strategy_run_id: str
  max_candidates: int = 100_000
  states: dict[str, PhaseOneBaselineState] = field(default_factory=dict)
  clocks: dict[str, _InstrumentClock] = field(default_factory=dict)
  common_ready_clocks: dict[str, _CommonReadyClock] = field(default_factory=dict)
  common_ready_instrument_ms: int = 0
  common_ready_candidate_edges: dict[str, Counter[str]] = field(
    default_factory=lambda: {
      "V3": Counter(),
      "PHASE_ONE": Counter(),
    }
  )
  evaluations_total: int = 0
  accepted_evaluations_total: int = 0
  raw_trigger_observations: Counter[str] = field(default_factory=Counter)
  trigger_edges: Counter[str] = field(default_factory=Counter)
  blocker_counts: dict[str, Counter[str]] = field(
    default_factory=lambda: defaultdict(Counter)
  )
  unknown_counts: dict[str, Counter[str]] = field(
    default_factory=lambda: defaultdict(Counter)
  )
  condition_pass_counts: dict[str, Counter[str]] = field(
    default_factory=lambda: defaultdict(Counter)
  )
  data_ready_instrument_ms: int = 0
  outcomes: dict[str, CandidateOutcomeState] = field(default_factory=dict)
  finalized_at_ms: Optional[int] = None

  def __post_init__(self) -> None:
    self.strategy_run_id = str(self.strategy_run_id or "").strip()
    if not self.strategy_run_id:
      raise ValueError("一期基线比较器缺少策略运行标识")
    if self.max_candidates <= 0:
      raise ValueError("一期基线候选上界必须大于零")

  def observe(
    self,
    strategy_input: StrategyInput,
    *,
    v3_data_ready: Optional[bool] = None,
    v3_candidate_path: Optional[str] = None,
  ) -> Optional[PhaseOneBaselineEvaluation]:
    if strategy_input.cadence is not StrategyCadence.TICK:
      return None
    if strategy_input.run_id != self.strategy_run_id:
      raise ValueError("一期基线比较器不可跨策略运行复用")
    sample = _sample_from_input(strategy_input)
    if sample is None:
      return None

    # Existing candidates see only strictly later facts. A candidate created
    # below therefore cannot consume its own trigger Tick.
    observation = CandidatePriceObservation(
      source_time_ms=sample.source_time_ms,
      tick_ordinal=sample.tick_ordinal,
      continuity_generation=sample.continuity_generation,
      price=sample.price,
      trading_halted=_trading_halted(strategy_input),
    )
    for state in list(self.outcomes.values()):
      if state.definition.instrument_code != sample.instrument_code:
        continue
      observe_candidate_outcome(state, observation)

    previous = self.states.get(sample.instrument_code)
    reduction = reduce_phase_one_baseline(
      previous,
      sample,
      continuous_session=strategy_input.market_data_context.session.is_continuous,
      quote_stale=bool(strategy_input.market_data_context.quote_stale),
    )
    self.states[sample.instrument_code] = reduction.state
    evaluation = reduction.evaluation
    baseline_data_ready = self._record_evaluation(evaluation)
    self._record_common_ready_comparison(
      evaluation,
      baseline_data_ready=baseline_data_ready,
      v3_data_ready=v3_data_ready is True,
      v3_candidate_path=v3_candidate_path,
    )
    if evaluation.trigger_edge:
      self._start_outcome(evaluation, sample.price)
    return evaluation

  def finalize(self, finalized_at_ms: int) -> None:
    normalized = int(finalized_at_ms)
    if normalized < 0:
      raise ValueError("一期基线结束源时间不得为负数")
    for state in self.outcomes.values():
      finalize_candidate_outcome(state, finalized_at_ms=normalized)
    self.finalized_at_ms = normalized

  def snapshot(self) -> dict[str, Any]:
    horizon_values: dict[int, dict[str, list[float]]] = defaultdict(
      lambda: defaultdict(list)
    )
    status_counts: Counter[str] = Counter()
    unavailable_counts: Counter[str] = Counter()
    outcome_items: list[dict[str, Any]] = []
    for candidate_id, state in sorted(self.outcomes.items()):
      status_counts[state.status.value] += 1
      if state.unavailable_reason is not None:
        unavailable_counts[state.unavailable_reason.value] += 1
      for horizon in state.horizons:
        if horizon.return_pct is not None:
          horizon_values[horizon.horizon_seconds]["return_pct"].append(
            horizon.return_pct
          )
        if horizon.mfe_pct is not None:
          horizon_values[horizon.horizon_seconds]["mfe_pct"].append(horizon.mfe_pct)
        if horizon.mae_pct is not None:
          horizon_values[horizon.horizon_seconds]["mae_pct"].append(horizon.mae_pct)
      outcome_items.append(
        {
          "candidate_id": candidate_id,
          "instrument_code": state.definition.instrument_code,
          "source_time_ms": state.definition.source_time_ms,
          "status": state.status.value,
          "unavailable_reason": (
            state.unavailable_reason.value if state.unavailable_reason else None
          ),
          "horizons": [
            {
              "horizon_seconds": item.horizon_seconds,
              "return_pct": item.return_pct,
              "mfe_pct": item.mfe_pct,
              "mae_pct": item.mae_pct,
            }
            for item in state.horizons
          ],
        }
      )

    return {
      "schema_version": PHASE_ONE_BASELINE_REPORT_SCHEMA_VERSION,
      "available": True,
      "baseline_version": PHASE_ONE_BASELINE_VERSION,
      "strategy_run_id": self.strategy_run_id,
      "finalized_at_ms": self.finalized_at_ms,
      "denominator": {
        "code": "BASELINE_DATA_READY_INSTRUMENT_SECONDS",
        "value": self.data_ready_instrument_ms / 1000.0,
      },
      "evaluations_total": self.evaluations_total,
      "accepted_evaluations_total": self.accepted_evaluations_total,
      "raw_trigger_observations": dict(sorted(self.raw_trigger_observations.items())),
      "candidate_edges": dict(sorted(self.trigger_edges.items())),
      "blockers": _nested_counter(self.blocker_counts),
      "unknown_conditions": _nested_counter(self.unknown_counts),
      "condition_passes": _nested_counter(self.condition_pass_counts),
      "candidate_reference_performance": {
        "candidate_count": len(self.outcomes),
        "status_counts": dict(sorted(status_counts.items())),
        "unavailable_reason_counts": dict(sorted(unavailable_counts.items())),
        "fixed_windows": [
          {
            "horizon_seconds": horizon,
            "sample_count": len(values.get("return_pct", [])),
            "average_return_pct": _average(values.get("return_pct", [])),
            "average_mfe_pct": _average(values.get("mfe_pct", [])),
            "average_mae_pct": _average(values.get("mae_pct", [])),
          }
          for horizon, values in sorted(horizon_values.items())
        ],
        "items": outcome_items,
      },
      "fee_adjusted_performance": {
        "available": False,
        "reason_code": "SHADOW_BASELINE_NOT_EXECUTED",
        "reason": (
          "一期规则在本运行中仅作为市场机会影子对照，未经过独立 OrderSizer、"
          "风控、撮合和退出链，不能伪造费用后结果。"
        ),
        "required_data_codes": [
          "INDEPENDENT_BASELINE_EXECUTION_RUN",
          "AUTHORITATIVE_BASELINE_FILL_FEES",
          "BASELINE_EXIT_PLAN_RESULT",
        ],
      },
      "common_ready_comparison": {
        "available": self.common_ready_instrument_ms > 0,
        "denominator": {
          "code": "COMMON_READY_INSTRUMENT_SECONDS",
          "value": self.common_ready_instrument_ms / 1000.0,
        },
        "v3_candidate_edges": dict(
          sorted(self.common_ready_candidate_edges["V3"].items())
        ),
        "phase_one_candidate_edges": dict(
          sorted(self.common_ready_candidate_edges["PHASE_ONE"].items())
        ),
        "reason_code": (
          None
          if self.common_ready_instrument_ms > 0
          else "COMMON_READY_EXPOSURE_EMPTY"
        ),
        "reason": (
          None
          if self.common_ready_instrument_ms > 0
          else "本次回放没有形成一期与 V3 同时可决策的连续暴露时段。"
        ),
      },
    }

  def _record_evaluation(self, evaluation: PhaseOneBaselineEvaluation) -> bool:
    self.evaluations_total += 1
    accepted = evaluation.ignored_reason not in {
      "INVALID_SAMPLE",
      "OUT_OF_ORDER_SOURCE_IDENTITY",
      "DUPLICATE_SOURCE_IDENTITY",
      "CONTINUITY_CHANGED",
      "TRADE_DATE_CHANGED",
      "CUMULATIVE_COUNTER_ROLLBACK",
    }
    if accepted:
      self.accepted_evaluations_total += 1
    for path, checks in (
      (OpportunityPath.PULLBACK_REBOUND.value, evaluation.pullback_checks),
      (OpportunityPath.MOMENTUM_ACCELERATION.value, evaluation.momentum_checks),
    ):
      for check in checks:
        if check.passed is True:
          self.condition_pass_counts[path][check.code] += 1
        elif check.passed is False:
          self.blocker_counts[path][check.code] += 1
        else:
          self.unknown_counts[path][check.code] += 1
    if evaluation.raw_triggered:
      self.raw_trigger_observations[evaluation.selected_path.value] += 1
    if evaluation.trigger_edge:
      self.trigger_edges[evaluation.selected_path.value] += 1

    pullback = {item.code: item for item in evaluation.pullback_checks}
    data_ready = bool(
      accepted
      and pullback.get("CONTINUOUS_SESSION")
      and pullback["CONTINUOUS_SESSION"].passed is True
      and pullback.get("QUOTE_FRESH")
      and pullback["QUOTE_FRESH"].passed is True
      and pullback.get("PULLBACK_MINIMUM_TICKS")
      and pullback["PULLBACK_MINIMUM_TICKS"].passed is True
      and pullback.get("PULLBACK_BOOK_COMPLETE")
      and pullback["PULLBACK_BOOK_COMPLETE"].passed is True
    )
    previous = self.clocks.get(evaluation.instrument_code)
    if (
      previous is not None
      and previous.data_ready
      and data_ready
      and previous.trade_date == evaluation.trade_date
      and previous.continuity_generation == evaluation.continuity_generation
      and evaluation.source_time_ms > previous.source_time_ms
    ):
      self.data_ready_instrument_ms += min(
        _MAX_SOURCE_INTERVAL_MS,
        evaluation.source_time_ms - previous.source_time_ms,
      )
    self.clocks[evaluation.instrument_code] = _InstrumentClock(
      trade_date=evaluation.trade_date,
      continuity_generation=evaluation.continuity_generation,
      source_time_ms=evaluation.source_time_ms,
      data_ready=data_ready,
    )
    return data_ready

  def _record_common_ready_comparison(
    self,
    evaluation: PhaseOneBaselineEvaluation,
    *,
    baseline_data_ready: bool,
    v3_data_ready: bool,
    v3_candidate_path: Optional[str],
  ) -> None:
    common_ready = baseline_data_ready and v3_data_ready
    previous = self.common_ready_clocks.get(evaluation.instrument_code)
    if (
      previous is not None
      and previous.common_ready
      and common_ready
      and previous.trade_date == evaluation.trade_date
      and previous.continuity_generation == evaluation.continuity_generation
      and evaluation.source_time_ms > previous.source_time_ms
    ):
      self.common_ready_instrument_ms += min(
        _MAX_SOURCE_INTERVAL_MS,
        evaluation.source_time_ms - previous.source_time_ms,
      )
    self.common_ready_clocks[evaluation.instrument_code] = _CommonReadyClock(
      trade_date=evaluation.trade_date,
      continuity_generation=evaluation.continuity_generation,
      source_time_ms=evaluation.source_time_ms,
      common_ready=common_ready,
    )
    if not common_ready:
      return
    if evaluation.trigger_edge:
      self.common_ready_candidate_edges["PHASE_ONE"][
        evaluation.selected_path.value
      ] += 1
    normalized_v3_path = str(v3_candidate_path or "").strip().upper()
    if normalized_v3_path in {
      OpportunityPath.PULLBACK_REBOUND.value,
      OpportunityPath.MOMENTUM_ACCELERATION.value,
    }:
      self.common_ready_candidate_edges["V3"][normalized_v3_path] += 1

  def _start_outcome(
    self,
    evaluation: PhaseOneBaselineEvaluation,
    reference_price: float,
  ) -> None:
    if len(self.outcomes) >= self.max_candidates:
      raise RuntimeError("一期规则影子基线候选数量超过运行上界")
    seed = (
      f"{self.strategy_run_id}|{evaluation.instrument_code}|"
      f"{evaluation.continuity_generation}|{evaluation.source_time_ms}|"
      f"{evaluation.tick_ordinal}|{evaluation.selected_path.value}"
    )
    fingerprint = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    candidate_id = f"phase1:{fingerprint[:32]}"
    if candidate_id in self.outcomes:
      return
    self.outcomes[candidate_id] = start_candidate_outcome(
      CandidateOutcomeDefinition(
        candidate_id=candidate_id,
        candidate_fingerprint=fingerprint,
        strategy_run_id=self.strategy_run_id,
        instrument_code=evaluation.instrument_code,
        source_time_ms=evaluation.source_time_ms,
        tick_ordinal=evaluation.tick_ordinal,
        continuity_generation=evaluation.continuity_generation,
        reference_price=reference_price,
        policy_version=PHASE_ONE_BASELINE_VERSION,
        feature_schema_version="phase-one-causal-features-v1",
      )
    )


def _sample_from_input(strategy_input: StrategyInput) -> Optional[OpportunitySample]:
  tick = strategy_input.event
  context = strategy_input.market_data_context
  if tick is None:
    return None
  price = _positive(getattr(tick, "last_price", None))
  if price is None or int(context.source_time_ms or 0) <= 0:
    return None
  bids = list(getattr(tick, "bid_price", []) or [])
  asks = list(getattr(tick, "ask_price", []) or [])
  bid_volumes = list(getattr(tick, "bid_vol", []) or [])
  ask_volumes = list(getattr(tick, "ask_vol", []) or [])
  price_tick = _positive((strategy_input.market_context or {}).get("price_tick"))
  return OpportunitySample(
    instrument_code=str(strategy_input.instrument_code or "").strip().upper(),
    trade_date=context.trade_date.isoformat(),
    source_time_ms=int(context.source_time_ms),
    tick_ordinal=max(0, int(context.tick_ordinal or 0)),
    price=price,
    continuity_generation=str(context.continuity_generation),
    received_at_ms=(
      int(context.received_at_ms) if context.received_at_ms > 0 else None
    ),
    bid_price=_positive(bids[0] if bids else None),
    ask_price=_positive(asks[0] if asks else None),
    bid_volume=_non_negative(bid_volumes[0] if bid_volumes else None),
    ask_volume=_non_negative(ask_volumes[0] if ask_volumes else None),
    cumulative_amount=_non_negative(getattr(tick, "amount", None)),
    cumulative_volume=_non_negative(getattr(tick, "pvolume", None)),
    price_tick=price_tick or 0.01,
  )


def _trading_halted(strategy_input: StrategyInput) -> bool:
  market = strategy_input.market_data
  for source in (strategy_input.event, market):
    if source is None:
      continue
    for name in ("trading_halted", "is_halted", "suspended"):
      value = getattr(source, name, None)
      if value is not None:
        return bool(value)
  return False


def _nested_counter(value: Mapping[str, Counter[str]]) -> dict[str, dict[str, int]]:
  return {
    path: dict(sorted(counter.items())) for path, counter in sorted(value.items())
  }


def _average(values: list[float]) -> Optional[float]:
  return sum(values) / len(values) if values else None


def _positive(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if normalized > 0 else None


def _non_negative(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if normalized >= 0 else None


__all__ = [
  "PHASE_ONE_BASELINE_REPORT_SCHEMA_VERSION",
  "TTradePhaseOneBaselineAccumulator",
]
