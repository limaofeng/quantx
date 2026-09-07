from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from quantx_domain.trading.t_order_policy import (
  TEntryOrderPolicy,
  TExitOrderPolicy,
  TOrderPolicyDecision,
)


def test_v1_policies_freeze_limits_and_lifetimes() -> None:
  entry = TEntryOrderPolicy()
  exit_policy = TExitOrderPolicy()

  assert (entry.order_ttl_seconds, entry.max_replace_count, entry.total_ttl_seconds) == (
    30,
    1,
    60,
  )
  assert (
    exit_policy.order_ttl_seconds,
    exit_policy.max_replace_count,
    exit_policy.total_ttl_seconds,
  ) == (30, 2, 90)
  assert entry.protected_limit_price(
    reference_price="10",
    price_tick="0.01",
    limit_up="10.02",
  ) == Decimal("10.02")
  assert exit_policy.protected_limit_price(
    reference_price="10",
    price_tick="0.01",
    limit_down="9.98",
  ) == Decimal("9.98")


def test_entry_cutoff_uses_exchange_local_time_for_aware_timestamp() -> None:
  # 06:50 UTC is 14:50 in Shanghai.
  result = TEntryOrderPolicy().decide_new(
    now=datetime(2026, 9, 3, 6, 50, tzinfo=timezone.utc),
    reference_price=10,
    price_tick=0.01,
  )

  assert result.decision is TOrderPolicyDecision.CUTOFF
  assert result.reason_code == "T_ENTRY_CUTOFF_REACHED"


@pytest.mark.parametrize(
  ("overrides", "reason"),
  [
    ({"result_unknown": True}, "ORDER_RESULT_UNKNOWN"),
    ({"cancel_unconfirmed": True}, "ORDER_CANCEL_UNCONFIRMED"),
    ({"prior_order_authoritative_terminal": False}, "ORDER_CANCEL_UNCONFIRMED"),
  ],
)
def test_replace_never_crosses_unknown_or_unconfirmed_boundary(
  overrides,
  reason,
) -> None:
  created = datetime(2026, 9, 3, 10, 0)
  values = {
    "now": created + timedelta(seconds=31),
    "original_created_at": created,
    "replace_count": 0,
    "requested_volume": 300,
    "authoritative_filled_volume": 100,
    "prior_order_authoritative_terminal": True,
    "result_unknown": False,
    "cancel_unconfirmed": False,
    "reference_price": 10,
    "price_tick": 0.01,
  }
  values.update(overrides)

  result = TExitOrderPolicy().decide_replace(**values)

  assert not result.allowed
  assert result.reason_code == reason


def test_replace_requires_single_order_ttl_and_only_uses_remaining_volume() -> None:
  created = datetime(2026, 9, 3, 10, 0)
  policy = TExitOrderPolicy()
  active = policy.decide_replace(
    now=created + timedelta(seconds=29),
    original_created_at=created,
    replace_count=0,
    requested_volume=300,
    authoritative_filled_volume=100,
    prior_order_authoritative_terminal=True,
    result_unknown=False,
    cancel_unconfirmed=False,
    reference_price=10,
    price_tick=0.01,
  )
  allowed = policy.decide_replace(
    now=created + timedelta(seconds=31),
    original_created_at=created,
    replace_count=0,
    requested_volume=300,
    authoritative_filled_volume=100,
    prior_order_authoritative_terminal=True,
    result_unknown=False,
    cancel_unconfirmed=False,
    reference_price=10,
    price_tick=0.01,
  )
  expired = policy.decide_replace(
    now=created + timedelta(seconds=90),
    original_created_at=created,
    replace_count=0,
    requested_volume=300,
    authoritative_filled_volume=100,
    prior_order_authoritative_terminal=True,
    result_unknown=False,
    cancel_unconfirmed=False,
    reference_price=10,
    price_tick=0.01,
  )

  assert active.reason_code == "T_EXIT_ORDER_ACTIVE"
  assert allowed.allowed and allowed.remaining_volume == 200
  assert expired.reason_code == "T_EXIT_ORDER_EXPIRED"


def test_replace_limit_uses_frozen_reason_code() -> None:
  created = datetime(2026, 9, 3, 10, 0)
  result = TEntryOrderPolicy().decide_replace(
    now=created + timedelta(seconds=31),
    original_created_at=created,
    replace_count=1,
    requested_volume=100,
    authoritative_filled_volume=0,
    prior_order_authoritative_terminal=True,
    result_unknown=False,
    cancel_unconfirmed=False,
    reference_price=10,
    price_tick=0.01,
  )

  assert result.reason_code == "ORDER_REPLACE_LIMIT_REACHED"


@pytest.mark.parametrize(
  "factory",
  [
    lambda: TEntryOrderPolicy(max_slippage_bps=999),
    lambda: TEntryOrderPolicy(version="TEntryOrderPolicy.v1", total_ttl_seconds=61),
    lambda: TExitOrderPolicy(max_replace_count=99),
    lambda: TExitOrderPolicy(version="TExitOrderPolicy.v1", order_ttl_seconds=31),
  ],
)
def test_v1_policy_parameters_cannot_drift_under_the_same_version(factory) -> None:
  with pytest.raises(ValueError, match="ORDER_POLICY_V1_IMMUTABLE"):
    factory()
