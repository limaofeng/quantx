from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from itertools import permutations

import pytest
from quantx_application.t_trade_v3.portfolio_allocation import (
  TAllocationAction,
  TAllocationCandidate,
  allocate_portfolio,
)
from quantx_application.t_trade_v3.portfolio_snapshot import (
  IndustryTExposure,
  PortfolioEvidenceCut,
  PortfolioTDecisionSnapshot,
  TEnvelopePosition,
  TPortfolioPolicy,
  TTradingEnvelopePolicy,
  build_t_trading_envelope,
)
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef

NOW = datetime(2026, 9, 7, 2, tzinfo=UTC)


def portfolio():
  cut = PortfolioEvidenceCut(
    ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "paper-1"),
    ExecutionEnvironment.PAPER,
    NOW,
    "snapshot-1",
    "a" * 64,
    NOW,
    "b" * 64,
    NOW,
    True,
  )
  envelopes = tuple(
    build_t_trading_envelope(
      cut=cut,
      config_version="config-1",
      policy=TTradingEnvelopePolicy("envelope-v1", 0, D(10000), 1000),
      position=TEnvelopePosition(code, "bank", 0, 0, 1000, 1000, 0, D(0), D(0), False),
    )
    for code in ("A", "B", "C")
  )
  return PortfolioTDecisionSnapshot(
    cut,
    "cycle-1",
    "config-1",
    "rules-v3",
    "RULE_ONLY",
    TPortfolioPolicy("portfolio-v1", D(12000), D("0.5"), D(1000), D(20000), 3, D(2000)),
    envelopes,
    (IndustryTExposure("bank", D(0), D(0)),),
    D(20000),
    D(100000),
    D(0),
    D(0),
    D(0),
    D(0),
    0,
    True,
    False,
    False,
  )


def candidate(code, score=90, **changes):
  return replace(
    TAllocationCandidate(
      "intent-" + code,
      1,
      "candidate-" + code,
      "fingerprint-" + code,
      code,
      D(score),
      D(score),
      D(1),
      NOW,
      NOW + timedelta(seconds=15),
      D(8000),
      D(1000),
      100,
      True,
    ),
    **changes,
  )


def allocate(values, *, source=None, now=NOW, attempt=1):
  return allocate_portfolio(
    source or portfolio(), tuple(values), allocation_attempt=attempt, now=now
  )


def test_stable_ranking_controls_caps_for_every_arrival_permutation():
  values = (candidate("A", 70), candidate("B", 90), candidate("C", 80))
  expected = allocate(values)
  assert [(v.instrument_code, v.action, v.allocated_amount_cap) for v in expected] == [
    ("B", TAllocationAction.ALLOW, D(8000)),
    ("C", TAllocationAction.CAP, D(4000)),
    ("A", TAllocationAction.REJECT, D(0)),
  ]
  assert expected[1].blockers == ("T_PORTFOLIO_AMOUNT_CAP",)
  for permutation in permutations(values):
    assert allocate(permutation) == expected


def test_stable_tie_breaks_rule_liquidity_time_symbol_and_candidate():
  a, b, c = candidate("A"), candidate("B"), candidate("C")
  assert [v.instrument_code for v in allocate((c, b, a))] == ["A", "B", "C"]
  assert allocate((a, replace(b, rule_score=D(91))))[0].instrument_code == "B"
  assert allocate((a, replace(b, liquidity_quality=D(2))))[0].instrument_code == "B"
  assert (
    allocate((a, replace(b, observed_at=NOW - timedelta(seconds=1))))[0].instrument_code
    == "B"
  )
  other = replace(a, intent_id="other", candidate_id="candidate-0")
  assert allocate((a, other))[0].candidate_id == "candidate-0"


def test_non_candidate_industry_obligation_consumes_industry_room():
  source = portfolio()
  source = replace(
    source,
    industry_exposures=(IndustryTExposure("bank", D(17000), D(0)),),
    current_t_exposure=D(17000),
    policy=replace(source.policy, max_total_t_amount=D(50000)),
  )
  decision = allocate((candidate("A"),), source=source)[0]
  assert decision.action == TAllocationAction.CAP
  assert decision.allocated_amount_cap == D(3000)
  assert decision.blockers == ("T_INDUSTRY_AMOUNT_CAP",)


def test_one_slot_and_same_symbol_eligibility_do_not_use_dispatch_order():
  source = portfolio()
  source = replace(source, policy=replace(source.policy, max_concurrent_batches=1))
  decisions = allocate((candidate("A", 80), candidate("B", 90)), source=source)
  assert decisions[0].instrument_code == "B"
  assert decisions[1].action == TAllocationAction.DELAY
  assert decisions[1].next_eligible_at == NOW + timedelta(seconds=1)
  a = candidate("A")
  decisions = allocate((a, replace(a, intent_id="other", candidate_id="other")))
  assert decisions[1].blockers == ("T_SAME_SYMBOL_ALLOCATED",)


@pytest.mark.parametrize(
  "changes, reason",
  [
    ({"observed_at": NOW + timedelta(seconds=1)}, "T_CANDIDATE_NOT_CAUSAL"),
    (
      {"observed_at": NOW - timedelta(seconds=2), "expires_at": NOW},
      "T_INTENT_EXPIRED",
    ),
    ({"instrument_code": "MISSING"}, "T_ENVELOPE_MISSING"),
    ({"conservative_lot_cost": D(9000)}, "T_BUDGET_BELOW_CONSERVATIVE_LOT_COST"),
    ({"minimum_entry_volume": 2000}, "T_OLD_POSITION_BELOW_MINIMUM_ENTRY"),
  ],
)
def test_rejected_candidates_have_auditable_zero_budget(changes, reason):
  decision = allocate((candidate("A", **changes),))[0]
  assert decision.action == TAllocationAction.REJECT
  assert decision.blockers == (reason,)
  assert decision.allocated_amount_cap == 0


def test_delay_cannot_extend_original_ttl_and_new_attempt_has_distinct_id():
  value = candidate("A", data_healthy=False)
  assert allocate((value,))[0].action == TAllocationAction.DELAY
  delayed = allocate((value,), now=value.expires_at - timedelta(milliseconds=500))[0]
  assert delayed.action == TAllocationAction.REJECT
  assert delayed.blockers == ("T_DELAY_EXCEEDS_INTENT_TTL",)
  assert delayed.next_eligible_at is None
  assert (
    allocate((value,), attempt=1)[0].decision_id
    != allocate((value,), attempt=2)[0].decision_id
  )


def test_not_yet_eligible_preserves_later_deadline_and_does_not_allocate():
  value = candidate("A", next_eligible_at=NOW + timedelta(seconds=5))
  result = allocate((value,))[0]
  assert result.next_eligible_at == value.next_eligible_at
  assert result.allocated_amount_cap == 0
  assert (
    allocate((value,), now=value.next_eligible_at, attempt=2)[0].action
    == TAllocationAction.ALLOW
  )


def test_duplicate_identity_rejects_entire_input():
  with pytest.raises(ValueError, match="DUPLICATE_INPUT"):
    allocate((candidate("A"), candidate("A")))


def test_unhealthy_data_cannot_bypass_existing_next_eligible_gate():
  deadline = NOW + timedelta(seconds=5)
  result = allocate((candidate("A", data_healthy=False, next_eligible_at=deadline),))[0]
  assert result.next_eligible_at == deadline
  assert result.action == TAllocationAction.DELAY


def test_allocation_requires_current_cut_and_enabled_scorer():
  with pytest.raises(ValueError, match="INVALID_EVALUATION_TIME"):
    allocate((candidate("A"),), now=NOW - timedelta(microseconds=1))
  with pytest.raises(ValueError, match="SCORER_NOT_ENABLED"):
    allocate((candidate("A"),), source=replace(portfolio(), scorer_binding="ACTIVE"))


@pytest.mark.parametrize(
  "changes, reason",
  [
    ({"entry_enabled": False}, "T_ACCOUNT_ENTRY_DISABLED"),
    ({"kill_switch": True}, "T_ACCOUNT_KILL_SWITCH"),
    ({"reconcile_required": True}, "T_ACCOUNT_RECONCILE_REQUIRED"),
    ({"realized_t_pnl": D(-2000)}, "T_DAILY_LOSS_LIMIT"),
  ],
)
def test_account_circuit_breakers_preserve_specific_audit_reason(changes, reason):
  result = allocate((candidate("A"),), source=replace(portfolio(), **changes))[0]
  assert result.action == TAllocationAction.REJECT
  assert result.blockers == (reason,)
  assert result.allocated_amount_cap == 0
