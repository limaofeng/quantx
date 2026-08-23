from __future__ import annotations

import pytest
from quantx_application.t_trade_v3.account_facts import (
  T_TRADE_ACCOUNT_SNAPSHOT_STALE,
  T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE,
  compute_t_trade_account_facts,
)


def _facts(
  states=None,
  reservations=None,
  *,
  amount=1_000.0,
  quota=None,
  **kwargs,
):
  kwargs.setdefault("max_concurrent_batches", 3)
  kwargs.setdefault("max_total_exposure_pct", 0.1)
  return compute_t_trade_account_facts(
    {} if states is None else states,
    {} if reservations is None else reservations,
    {"total_asset": 100_000.0} if quota is None else quota,
    amount,
    instrument_code="600000.SH",
    **kwargs,
  )


def test_other_instrument_awaiting_alone_does_not_block_with_capacity():
  result = _facts(
    {
      "000001.SZ": {
        "instrument_code": "000001.SZ",
        "entry_order_status": "AWAITING_APPROVAL",
        "pending_entry_intent_id": "other-intent",
      }
    }
  )

  assert result.authoritative is True
  assert result.same_instrument_pending_intent_exists is False
  assert result.account_concurrent_batch_limit_reached is False
  assert result.account_total_exposure_limit_reached is False


def test_same_instrument_pending_blocks_and_current_intent_is_excluded():
  state = {
    "instrument_code": "600000.SH",
    "entry_order_status": "AWAITING_APPROVAL",
    "pending_entry_intent_id": "intent-1",
  }
  blocked = _facts({"600000.SH": state})
  current = _facts({"600000.SH": state}, current_intent_id="intent-1")

  assert blocked.same_instrument_pending_intent_exists is True
  assert current.same_instrument_pending_intent_exists is False


def test_same_instrument_pending_without_valid_status_is_snapshot_stale():
  result = _facts(
    {
      "600000.SH": {
        "instrument_code": "600000.SH",
        "entry_order_status": "UNKNOWN",
        "pending_entry_intent_id": "intent-1",
      }
    }
  )

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)


def test_other_instrument_active_batch_reaches_concurrent_limit():
  result = _facts(
    {
      "000001.SZ": {
        "instrument_code": "000001.SZ",
        "batch_id": "batch-1",
        "entry_order_status": "FILLED",
        "entry_filled_volume": 100,
        "exit_filled_volume": 0,
        "entry_avg_price": 100.0,
      }
    },
    max_concurrent_batches=1,
  )

  assert result.account_concurrent_batch_limit_reached is True
  assert result.same_instrument_pending_intent_exists is False


def test_terminal_reservation_still_counts_until_state_reflects_fill():
  result = _facts(
    reservations={
      "intent-2": {
        "instrument_code": "000001.SZ",
        "batch_id": "batch-2",
        "terminal_status": "FILLED",
        "volume": 100,
        "price": 50.0,
        "amount": 0,
      }
    },
    max_concurrent_batches=1,
  )

  assert result.account_concurrent_batch_limit_reached is True
  assert result.total_exposure == 6_000.0


def test_partial_fill_merges_active_and_reserved_amount_by_batch():
  result = _facts(
    {
      "000001.SZ": {
        "instrument_code": "000001.SZ",
        "batch_id": "batch-partial",
        "entry_order_status": "PARTIAL_FILLED",
        "pending_entry_intent_id": "intent-partial",
        "entry_filled_volume": 100,
        "exit_filled_volume": 0,
        "entry_avg_price": 10.0,
      }
    },
    {
      "intent-partial": {
        "intent_id": "intent-partial",
        "instrument_code": "000001.SZ",
        "batch_id": "batch-partial",
        "amount": 10_000.0,
      }
    },
    amount=1_000.0,
    max_total_exposure_pct=0.1,
  )

  assert result.account_total_exposure_limit_reached is True
  assert result.total_exposure == 11_000.0


def test_inconsistent_exit_fill_snapshot_is_stale():
  result = _facts(
    {
      "000001.SZ": {
        "instrument_code": "000001.SZ",
        "batch_id": "batch-inconsistent",
        "entry_order_status": "FILLED",
        "entry_filled_volume": 100,
        "exit_filled_volume": 101,
        "entry_avg_price": 10.0,
      }
    }
  )

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)


def test_total_exposure_equal_to_cap_is_allowed_but_over_cap_blocks():
  equal = _facts(amount=10_000.0, max_total_exposure_pct=0.1)
  over = _facts(amount=10_001.0, max_total_exposure_pct=0.1)

  assert equal.account_total_exposure_limit_reached is False
  assert over.account_total_exposure_limit_reached is True


def test_missing_quota_is_snapshot_stale_and_fail_closed():
  result = _facts(quota={})

  assert result.authoritative is False
  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)
  assert result.account_concurrent_batch_limit_reached is None


@pytest.mark.parametrize(
  "limit_name",
  ["max_concurrent_batches", "max_total_exposure_pct"],
)
def test_missing_account_limits_are_snapshot_stale_and_fail_closed(limit_name):
  result = _facts(**{limit_name: None})

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)


def test_non_finite_quota_is_snapshot_stale_and_all_facts_unknown():
  result = _facts(quota={"total_asset": float("nan")})

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)
  assert result.to_gate_facts() == {
    "reconciliation_required": None,
    "account_concurrent_batch_limit_reached": None,
    "account_total_exposure_limit_reached": None,
    "same_instrument_pending_intent_exists": None,
  }


def test_finite_inputs_that_overflow_exposure_arithmetic_are_snapshot_stale():
  result = _facts(
    quota={"total_asset": 1e308},
    max_total_exposure_pct=1e308,
  )

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)


def test_non_finite_reservation_is_snapshot_stale():
  result = _facts(
    reservations={
      "intent-2": {
        "instrument_code": "000001.SZ",
        "batch_id": "batch-2",
        "amount": float("inf"),
      }
    }
  )

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)


def test_zero_reservation_without_positive_volume_price_is_snapshot_stale():
  result = _facts(
    reservations={
      "intent-2": {
        "instrument_code": "000001.SZ",
        "batch_id": "batch-2",
        "amount": 0,
        "volume": 0,
        "price": 10.0,
      }
    }
  )

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)


def test_active_exposure_requires_entry_average_price():
  result = _facts(
    {
      "000001.SZ": {
        "instrument_code": "000001.SZ",
        "batch_id": "batch-1",
        "entry_order_status": "FILLED",
        "entry_filled_volume": 100,
        "exit_filled_volume": 0,
        "last_price": 100.0,
      }
    }
  )

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_STALE,)


def test_complete_exit_with_cleared_batch_does_not_occupy_slot():
  result = _facts(
    {
      "000001.SZ": {
        "instrument_code": "000001.SZ",
        "batch_id": None,
        "entry_order_status": "FILLED",
        "entry_filled_volume": 100,
        "exit_filled_volume": 100,
      }
    },
    max_concurrent_batches=1,
  )

  assert result.active_batch_count == 0
  assert result.account_concurrent_batch_limit_reached is False


def test_account_snapshot_bounds_are_stable():
  too_many_states = {
    str(index): {"instrument_code": str(index)}
    for index in range(4097)
  }
  result = _facts(too_many_states)

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE,)
  assert result.authoritative is False


def test_reservation_snapshot_bounds_are_stable():
  too_many_reservations = {
    str(index): {
      "instrument_code": "000001.SZ",
      "batch_id": str(index),
      "amount": 1.0,
    }
    for index in range(4097)
  }
  result = _facts(reservations=too_many_reservations)

  assert result.blockers == (T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE,)
