"""Deterministic planning over persisted standard TradeIntent references.

These inputs are read projections, not a second submission protocol. The
repository owns intent versions, claims, attempts and atomic state transitions.
No budget returned here reserves funds or determines legal order volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from quantx_application.t_trade_v3.portfolio_snapshot import (
  PortfolioTDecisionSnapshot,
  _amount,
  _hash,
  _identity,
  _volume,
)


class TAllocationAction(StrEnum):
  ALLOW = "ALLOW"
  CAP = "CAP"
  DELAY = "DELAY"
  REJECT = "REJECT"


@dataclass(frozen=True)
class TAllocationCandidate:
  intent_id: str
  intent_version: int
  candidate_id: str
  candidate_fingerprint: str
  instrument_code: str
  rank_score: Decimal
  rule_score: Decimal
  liquidity_quality: Decimal
  observed_at: datetime
  expires_at: datetime
  requested_amount_ceiling: Decimal
  conservative_lot_cost: Decimal
  minimum_entry_volume: int
  data_healthy: bool
  next_eligible_at: datetime | None = None

  def __post_init__(self) -> None:
    _identity(
      self.intent_id,
      self.candidate_id,
      self.candidate_fingerprint,
      self.instrument_code,
    )
    _volume(self.intent_version, self.minimum_entry_volume)
    _amount(
      self.rank_score,
      self.rule_score,
      self.liquidity_quality,
      self.requested_amount_ceiling,
      self.conservative_lot_cost,
    )
    if (
      self.requested_amount_ceiling == 0
      or self.conservative_lot_cost == 0
      or self.minimum_entry_volume == 0
    ):
      raise ValueError("T_ALLOCATION_POSITIVE_BUDGET_REQUIRED")
    times = (self.observed_at, self.expires_at, self.next_eligible_at)
    if any(
      v is not None
      and (not isinstance(v, datetime) or (v.tzinfo is None or v.utcoffset() is None))
      for v in times
    ):
      raise ValueError("T_ALLOCATION_AWARE_TIME_REQUIRED")
    if self.expires_at <= self.observed_at:
      raise ValueError("T_ALLOCATION_INVALID_TTL")
    if type(self.data_healthy) is not bool:
      raise ValueError("T_ALLOCATION_DATA_HEALTH_REQUIRED")


@dataclass(frozen=True)
class TAllocationDecision:
  decision_id: str
  cycle_id: str
  allocation_attempt: int
  intent_id: str
  intent_version: int
  candidate_id: str
  instrument_code: str
  rank: int
  rank_score: Decimal
  action: TAllocationAction
  requested_amount_ceiling: Decimal
  allocated_amount_cap: Decimal
  portfolio_input_fingerprint: str
  envelope_id: str | None
  blockers: tuple[str, ...]
  created_at: datetime
  next_eligible_at: datetime | None


def allocate_portfolio(
  snapshot: PortfolioTDecisionSnapshot,
  candidates: tuple[TAllocationCandidate, ...],
  *,
  allocation_attempt: int,
  now: datetime,
) -> tuple[TAllocationDecision, ...]:
  """Freeze every result, including expired and rejected inputs, in stable rank."""
  _volume(allocation_attempt)
  if allocation_attempt < 1:
    raise ValueError("T_ALLOCATION_ATTEMPT_REQUIRED")
  if (
    not isinstance(now, datetime)
    or (now.tzinfo is None or now.utcoffset() is None)
    or now < snapshot.cut.as_of
  ):
    raise ValueError("T_ALLOCATION_INVALID_EVALUATION_TIME")
  if snapshot.scorer_binding != "RULE_ONLY":
    raise ValueError("T_ALLOCATION_SCORER_NOT_ENABLED")
  if len({v.intent_id for v in candidates}) != len(candidates) or len(
    {v.candidate_id for v in candidates}
  ) != len(candidates):
    raise ValueError("T_ALLOCATION_DUPLICATE_INPUT")
  ordered = sorted(
    candidates,
    key=lambda v: (
      -v.rank_score,
      -v.rule_score,
      -v.liquidity_quality,
      v.observed_at,
      v.instrument_code,
      v.candidate_id,
    ),
  )
  envelopes = {
    v.observed_position_projection.instrument_code: v for v in snapshot.envelopes
  }
  remaining = snapshot.planning_amount_cap
  slots = max(0, snapshot.policy.max_concurrent_batches - snapshot.active_batch_count)
  selected_symbols: set[str] = set()
  industry_used = {
    v.primary_industry: v.current_t_exposure + v.uncovered_buy_amount
    for v in snapshot.industry_exposures
  }
  decisions: list[TAllocationDecision] = []
  for rank, candidate in enumerate(ordered, 1):
    envelope = envelopes.get(candidate.instrument_code)
    action = TAllocationAction.REJECT
    cap = Decimal(0)
    blockers: tuple[str, ...] = ()
    next_at = None
    if candidate.observed_at > snapshot.cut.as_of:
      blockers = ("T_CANDIDATE_NOT_CAUSAL",)
    elif now >= candidate.expires_at:
      blockers = ("T_INTENT_EXPIRED",)
    elif envelope is None:
      blockers = ("T_ENVELOPE_MISSING",)
    elif snapshot.entry_blockers:
      blockers = snapshot.entry_blockers
    elif not candidate.data_healthy:
      action, blockers = TAllocationAction.DELAY, ("T_CANDIDATE_DATA_UNHEALTHY",)
    elif candidate.next_eligible_at is not None and now < candidate.next_eligible_at:
      action, blockers = TAllocationAction.DELAY, ("T_ALLOCATION_NOT_YET_ELIGIBLE",)
      next_at = candidate.next_eligible_at
    elif not envelope.positive_t_eligible:
      blockers = envelope.reason_codes
    elif envelope.planning_entry_volume_ceiling < candidate.minimum_entry_volume:
      blockers = ("T_OLD_POSITION_BELOW_MINIMUM_ENTRY",)
    elif candidate.instrument_code in selected_symbols:
      blockers = ("T_SAME_SYMBOL_ALLOCATED",)
    elif slots == 0:
      action, blockers = TAllocationAction.DELAY, ("T_MAX_CONCURRENT_BATCHES",)
    else:
      industry = envelope.observed_position_projection.primary_industry
      industry_remaining = max(
        Decimal(0),
        snapshot.policy.max_industry_t_amount - industry_used.get(industry, Decimal(0)),
      )
      cap = min(
        candidate.requested_amount_ceiling,
        remaining,
        envelope.max_incremental_t_amount,
        industry_remaining,
      )
      if cap < candidate.conservative_lot_cost:
        cap, blockers = Decimal(0), ("T_BUDGET_BELOW_CONSERVATIVE_LOT_COST",)
      else:
        action = (
          TAllocationAction.ALLOW
          if cap == candidate.requested_amount_ceiling
          else TAllocationAction.CAP
        )
        if action == TAllocationAction.CAP:
          blockers = tuple(
            reason
            for bound, reason in (
              (remaining, "T_PORTFOLIO_AMOUNT_CAP"),
              (envelope.max_incremental_t_amount, "T_SYMBOL_AMOUNT_CAP"),
              (industry_remaining, "T_INDUSTRY_AMOUNT_CAP"),
            )
            if bound == cap
          )
        remaining -= cap
        industry_used[industry] = industry_used.get(industry, Decimal(0)) + cap
        slots -= 1
        selected_symbols.add(candidate.instrument_code)
    if action == TAllocationAction.DELAY:
      next_at = max(
        now + timedelta(seconds=1), next_at or now, candidate.next_eligible_at or now
      )
      if next_at >= candidate.expires_at:
        action, blockers, next_at = (
          TAllocationAction.REJECT,
          ("T_DELAY_EXCEEDS_INTENT_TTL",),
          None,
        )
    identity = _hash(
      (
        snapshot.cut.execution_ref,
        snapshot.cut.environment,
        snapshot.cycle_id,
        allocation_attempt,
        candidate.intent_id,
        snapshot.portfolio_input_fingerprint,
      )
    )
    decisions.append(
      TAllocationDecision(
        "talloc:" + identity,
        snapshot.cycle_id,
        allocation_attempt,
        candidate.intent_id,
        candidate.intent_version,
        candidate.candidate_id,
        candidate.instrument_code,
        rank,
        candidate.rank_score,
        action,
        candidate.requested_amount_ceiling,
        cap,
        snapshot.portfolio_input_fingerprint,
        envelope.envelope_id if envelope else None,
        blockers,
        now,
        next_at,
      )
    )
  return tuple(decisions)
