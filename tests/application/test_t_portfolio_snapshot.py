from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
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


def cut(**changes):
  value = PortfolioEvidenceCut(
    ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "paper-1"),
    ExecutionEnvironment.PAPER,
    NOW,
    "snapshot-1",
    "a" * 64,
    NOW - timedelta(seconds=1),
    "b" * 64,
    NOW,
    True,
  )
  return replace(value, **changes)


def envelope(*, source=None, code="600000.SH", **changes):
  position = TEnvelopePosition(
    code, "bank", 200, 500, 300, 1000, 100, D(1000), D(500), False
  )
  return build_t_trading_envelope(
    cut=source or cut(),
    config_version="config-1",
    policy=TTradingEnvelopePolicy("envelope-v1", 300, D(10000), 1000),
    position=replace(position, **changes),
  )


def snapshot(**changes):
  value = PortfolioTDecisionSnapshot(
    cut(),
    "cycle-1",
    "config-1",
    "rule-v3",
    "RULE_ONLY",
    TPortfolioPolicy("portfolio-v1", D(20000), D("0.3"), D(3000), D(10000), 4, D(2000)),
    (envelope(),),
    (IndustryTExposure("bank", D(1000), D(500)),),
    D(15000),
    D(100000),
    D(500),
    D(1000),
    D(0),
    D(0),
    0,
    True,
    False,
    False,
  )
  return replace(value, **changes)


def test_envelope_protects_locked_core_core_floor_and_uncovered_obligations():
  result = envelope()
  assert result.protected_old_position_floor == 500
  assert result.planning_replaceable_old_volume_ceiling == 400
  assert result.planning_entry_volume_ceiling == 400
  assert result.max_incremental_t_amount == D(8500)
  assert result.positive_t_eligible


def test_envelope_never_releases_protection_or_turns_today_stock_into_old_stock():
  result = envelope(old_sellable_volume=400)
  assert result.planning_entry_volume_ceiling == 0
  assert result.reason_codes == ("T_NO_REPLACEABLE_OLD_POSITION",)
  assert not result.positive_t_eligible


def test_same_symbol_obligation_is_explicitly_blocked():
  result = envelope(active_or_pending_entry=True)
  assert not result.positive_t_eligible
  assert "T_SAME_SYMBOL_ENTRY_OBLIGATION" in result.reason_codes


@pytest.mark.parametrize("field", ["account_snapshot_as_of", "obligations_as_of"])
def test_future_account_or_obligation_evidence_is_rejected(field):
  with pytest.raises(ValueError, match="FUTURE_EVIDENCE"):
    cut(**{field: NOW + timedelta(microseconds=1)})


def test_missing_complete_snapshot_fails_closed():
  with pytest.raises(ValueError, match="INCOMPLETE_SNAPSHOT"):
    cut(complete=False)


@pytest.mark.parametrize(
  "changes",
  [
    {"account_snapshot_id": "snapshot-2"},
    {"local_obligation_watermark": "c" * 64},
    {"environment": ExecutionEnvironment.LIVE},
    {"execution_ref": ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "paper-2")},
  ],
)
def test_mixed_snapshot_obligation_environment_or_owner_cannot_be_joined(changes):
  with pytest.raises(ValueError, match="MIXED_EVIDENCE_CUT"):
    snapshot(envelopes=(envelope(source=cut(**changes)),))


def test_snapshot_copies_collection_and_hash_is_order_independent():
  values = [
    envelope(),
    envelope(code="000001.SZ", current_t_exposure=D(0), uncovered_entry_amount=D(0)),
  ]
  first = snapshot(envelopes=values)
  second = snapshot(envelopes=list(reversed(values)))
  values.clear()
  assert len(first.envelopes) == 2
  assert first.portfolio_input_fingerprint == second.portfolio_input_fingerprint
  with pytest.raises(FrozenInstanceError):
    first.available_cash = D(100)


def test_new_facts_produce_new_envelope_and_snapshot_identity():
  first = envelope()
  second = envelope(uncovered_entry_amount=D(501))
  assert first.envelope_id != second.envelope_id
  assert (
    snapshot().portfolio_input_fingerprint
    != snapshot(
      envelopes=(second,),
      uncovered_buy_amount=D(501),
      industry_exposures=(IndustryTExposure("bank", D(1000), D(501)),),
    ).portfolio_input_fingerprint
  )
  assert (
    snapshot().portfolio_input_fingerprint
    != snapshot(available_cash=D(14000)).portfolio_input_fingerprint
  )


def test_decimal_scale_does_not_change_fingerprint():
  assert (
    snapshot().portfolio_input_fingerprint
    == snapshot(available_cash=D("15000.00")).portfolio_input_fingerprint
  )


def test_planning_cap_deducts_cash_buffer_and_uncovered_buys():
  assert snapshot().planning_amount_cap == D(11500)
  assert snapshot(total_assets=D(10000)).planning_amount_cap == D(1500)
  assert snapshot(available_cash=D(100)).planning_amount_cap == 0


@pytest.mark.parametrize(
  "changes",
  [
    {"entry_enabled": False},
    {"kill_switch": True},
    {"reconcile_required": True},
    {"realized_t_pnl": D(-2000)},
    {"unrealized_t_pnl": D(-2001)},
  ],
)
def test_execution_controls_and_loss_limit_fail_closed(changes):
  assert snapshot(**changes).planning_amount_cap == 0


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D(-1), 100.0])
def test_invalid_money_cannot_authorize_planning(value):
  with pytest.raises(ValueError, match="INVALID_AMOUNT"):
    snapshot(available_cash=value)


def test_duplicate_symbols_rejected():
  with pytest.raises(ValueError, match="DUPLICATE_SYMBOL"):
    snapshot(envelopes=(envelope(), envelope()))


def test_invalid_old_position_and_boolean_volume_rejected():
  with pytest.raises(ValueError, match="OLD_POSITION_INCONSISTENT"):
    envelope(old_sellable_volume=1001)
  with pytest.raises(ValueError, match="INVALID_VOLUME"):
    envelope(old_sellable_volume=True)


def test_derived_envelope_limits_cannot_be_overridden_with_same_identity():
  with pytest.raises(TypeError, match="init=False"):
    replace(envelope(), planning_entry_volume_ceiling=999999)
  with pytest.raises(TypeError, match="init=False"):
    replace(envelope(), reason_codes=[])


def test_aware_clock_matches_p3_and_hash_canonicalizes_timezone():
  with pytest.raises(ValueError, match="AWARE_TIME_REQUIRED"):
    cut(as_of=NOW.replace(tzinfo=None))
  local = NOW.astimezone(timezone(timedelta(hours=8)))
  shifted = cut(as_of=local, obligations_as_of=local)
  assert envelope(source=shifted).input_fingerprint == envelope().input_fingerprint


def test_decimal_fingerprint_preserves_digits_beyond_arithmetic_context():
  first = D("15000.1234567890123456789012345678901")
  second = D("15000.1234567890123456789012345678902")
  assert (
    snapshot(available_cash=first).portfolio_input_fingerprint
    != snapshot(available_cash=second).portfolio_input_fingerprint
  )


def test_industry_totals_and_candidate_projection_must_reconcile():
  with pytest.raises(ValueError, match="INDUSTRY_TOTAL_MISMATCH"):
    snapshot(industry_exposures=(IndustryTExposure("bank", D(999), D(500)),))
  with pytest.raises(ValueError, match="ENVELOPE_EXCEEDS_INDUSTRY"):
    snapshot(envelopes=(envelope(current_t_exposure=D(1001)),))
