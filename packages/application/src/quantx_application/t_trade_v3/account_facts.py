"""Pure, bounded account facts used by the T-trade entry gate.

The Engine owns the mutable sources passed to this module.  This module only
normalizes a point-in-time copy of those sources and returns tri-state facts;
it never mutates RuntimeState and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Optional

T_TRADE_ACCOUNT_SNAPSHOT_STALE = "T_TRADE_PORTFOLIO_SNAPSHOT_STALE"
T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE = "T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE"
_MAX_ACCOUNT_FACT_INSTRUMENTS = 4096
_MAX_ACCOUNT_FACT_RESERVATIONS = 4096
_ACTIVE_ENTRY_STATUSES = frozenset(
  {
    "PENDING",
    "SUBMITTED",
    "ACCEPTED",
    "PARTIAL_FILLED",
    "FILLED",
    "RECONCILE_REQUIRED",
    "ENTRY_PENDING",
  }
)
_RECONCILIATION_STATUSES = frozenset({"RECONCILE_REQUIRED"})


@dataclass(frozen=True)
class TTradeAccountFacts:
  """The four external account facts and any explicit fail-closed reason."""

  reconciliation_required: Optional[bool]
  account_concurrent_batch_limit_reached: Optional[bool]
  account_total_exposure_limit_reached: Optional[bool]
  same_instrument_pending_intent_exists: Optional[bool]
  blockers: tuple[str, ...] = ()
  message: Optional[str] = None
  active_batch_count: Optional[int] = None
  total_exposure: Optional[float] = None
  total_asset: Optional[float] = None

  @property
  def authoritative(self) -> bool:
    return all(
      value is not None
      for value in (
        self.reconciliation_required,
        self.account_concurrent_batch_limit_reached,
        self.account_total_exposure_limit_reached,
        self.same_instrument_pending_intent_exists,
      )
    ) and not self.blockers

  def to_gate_facts(self) -> dict[str, Optional[bool]]:
    return {
      "reconciliation_required": self.reconciliation_required,
      "account_concurrent_batch_limit_reached": (
        self.account_concurrent_batch_limit_reached
      ),
      "account_total_exposure_limit_reached": (
        self.account_total_exposure_limit_reached
      ),
      "same_instrument_pending_intent_exists": (
        self.same_instrument_pending_intent_exists
      ),
    }


def compute_t_trade_account_facts(
  instrument_states: Any,
  entry_reservations: Any,
  account_quota: Any,
  requested_amount: Any,
  *,
  instrument_code: str,
  current_intent_id: Optional[str] = None,
  max_concurrent_batches: Any = None,
  max_total_exposure_pct: Any = None,
) -> TTradeAccountFacts:
  """Compute bounded account-level entry facts from authoritative snapshots.

  A malformed or incomplete source returns all four facts as ``None`` and a
  stable snapshot-stale blocker.  In particular, missing assets never become
  zero and therefore never accidentally authorize an entry.
  """

  try:
    states = _bounded_mapping(
      instrument_states,
      limit=_MAX_ACCOUNT_FACT_INSTRUMENTS,
      label="instrument_states",
    )
    reservations = _bounded_mapping(
      entry_reservations,
      limit=_MAX_ACCOUNT_FACT_RESERVATIONS,
      label="entry_reservations",
    )
    requested = _finite_non_negative(requested_amount, "requested_amount")
    if requested <= 0:
      raise ValueError("requested_amount must be positive")
    total_asset = _finite_positive(
      _first_present(account_quota, "total_asset", "total_value", "total_asset_cny"),
      "total_asset",
    )
    max_batches = _positive_int(max_concurrent_batches, "max_concurrent_batches")
    max_exposure_pct = _finite_positive(
      max_total_exposure_pct,
      "max_total_exposure_pct",
    )
    code = str(instrument_code or "").strip().upper()
    if not code:
      raise ValueError("instrument_code is required")
    current_id = str(current_intent_id or "").strip()

    active_batch_keys: set[str] = set()
    batch_by_intent: dict[str, str] = {}
    active_exposure_by_batch: dict[str, float] = {}
    reconciliation_required = False
    same_instrument_pending = False

    for raw_code, raw_state in states.items():
      if not isinstance(raw_state, Mapping):
        raise ValueError("instrument state must be a mapping")
      state = raw_state
      state_code = str(
        state.get("instrument_code") or raw_code or ""
      ).strip().upper()
      if not state_code:
        raise ValueError("instrument state code is required")
      entry_status = _status(state.get("entry_order_status"))
      exit_status = _status(state.get("exit_order_status"))
      if entry_status in _RECONCILIATION_STATUSES or exit_status in _RECONCILIATION_STATUSES:
        reconciliation_required = True

      entry_filled = _non_negative_int(
        state.get("entry_filled_volume", 0),
        "entry_filled_volume",
      )
      exit_filled = _non_negative_int(
        state.get("exit_filled_volume", 0),
        "exit_filled_volume",
      )
      if exit_filled > entry_filled:
        raise ValueError("exit_filled_volume exceeds entry_filled_volume")
      active_volume = entry_filled - exit_filled
      batch_id = str(state.get("batch_id") or "").strip()
      has_active_entry = active_volume > 0 or bool(
        batch_id and entry_status in _ACTIVE_ENTRY_STATUSES
      )
      pending_id = str(state.get("pending_entry_intent_id") or "").strip()
      is_current_intent = bool(current_id and pending_id == current_id)

      if has_active_entry:
        batch_key = batch_id
        if not batch_key:
          raise ValueError("active batch identity is unavailable")
        active_batch_keys.add(batch_key)
        if pending_id:
          batch_by_intent[pending_id] = batch_key
      if pending_id and entry_status not in (
        _ACTIVE_ENTRY_STATUSES | {"AWAITING_APPROVAL"}
      ):
        raise ValueError("pending entry intent status is unavailable")
      if state_code == code:
        if entry_status == "AWAITING_APPROVAL" and not pending_id:
          raise ValueError("awaiting entry intent identity is unavailable")
        if has_active_entry and not is_current_intent:
          same_instrument_pending = True
        elif pending_id and not is_current_intent:
          same_instrument_pending = True

      if active_volume > 0:
        # Entry exposure must use the authoritative fill basis.  A market
        # quote is not a safe substitute while a fill is being reconciled.
        price = _finite_positive(state.get("entry_avg_price"), "entry_avg_price")
        active_amount = _finite_positive(
          active_volume * price,
          "active_exposure",
        )
        active_exposure_by_batch[batch_id] = max(
          active_exposure_by_batch.get(batch_id, 0.0),
          active_amount,
        )

    reservation_exposure_by_batch: dict[str, float] = {}
    for reservation_id, raw_reservation in reservations.items():
      if not isinstance(raw_reservation, Mapping):
        raise ValueError("entry reservation must be a mapping")
      reservation = raw_reservation
      normalized_reservation_id = str(reservation_id or "").strip()
      if not normalized_reservation_id:
        raise ValueError("entry reservation identity is unavailable")
      reservation_intent_id = str(
        reservation.get("intent_id") or normalized_reservation_id
      ).strip()
      if current_id and current_id in {
        normalized_reservation_id,
        reservation_intent_id,
      }:
        continue
      reservation_code = str(reservation.get("instrument_code") or "").strip().upper()
      if not reservation_code:
        raise ValueError("entry reservation instrument is unavailable")
      explicit_batch_key = str(reservation.get("batch_id") or "").strip()
      mapped_batch_key = str(
        batch_by_intent.get(reservation_intent_id)
        or batch_by_intent.get(normalized_reservation_id)
        or ""
      ).strip()
      if (
        explicit_batch_key
        and mapped_batch_key
        and explicit_batch_key != mapped_batch_key
      ):
        raise ValueError("entry reservation batch identity mismatches state")
      batch_key = explicit_batch_key or mapped_batch_key
      if not batch_key:
        # An intent identity is sufficient to keep an unbatched reservation
        # distinct, but an instrument code alone is not a safe batch key.
        batch_key = f"intent:{reservation_intent_id or normalized_reservation_id}"
      if not batch_key:
        raise ValueError("entry reservation batch identity is unavailable")
      active_batch_keys.add(batch_key)
      if reservation_code == code:
        same_instrument_pending = True
      amount_value = _first_present(
        reservation,
        "amount",
        "requested_amount",
      )
      # A terminal reservation remains authoritative until strategy state
      # reflects it.  Treat a zero amount as incomplete and fall back to a
      # finite volume*price calculation; never authorize on an unmeasured
      # reservation.
      if amount_value is not None:
        parsed_amount = _finite_non_negative(amount_value, "reservation_amount")
        if parsed_amount > 0:
          amount_value = parsed_amount
        else:
          amount_value = None
      if amount_value is None:
        volume = _finite_non_negative(
          _first_present(reservation, "volume", "requested_volume"),
          "reservation_volume",
        )
        price = _finite_positive(reservation.get("price"), "reservation_price")
        amount_value = volume * price
      reservation_amount = _finite_non_negative(
        amount_value,
        "reservation_amount",
      )
      if reservation_amount <= 0:
        raise ValueError("reservation amount must be positive")
      reservation_exposure_by_batch[batch_key] = max(
        reservation_exposure_by_batch.get(batch_key, 0.0),
        reservation_amount,
      )

    active_exposure = _finite_non_negative(
      sum(active_exposure_by_batch.values()),
      "active_exposure",
    )
    reservation_exposure = _finite_non_negative(
      sum(
        max(
          reservation_amount,
          active_exposure_by_batch.get(batch_key, 0.0),
        )
        - active_exposure_by_batch.get(batch_key, 0.0)
        for batch_key, reservation_amount in reservation_exposure_by_batch.items()
      ),
      "reservation_exposure",
    )
    total_exposure = _finite_non_negative(
      active_exposure + reservation_exposure + requested,
      "total_exposure",
    )
    exposure_limit = _finite_positive(
      total_asset * max_exposure_pct,
      "exposure_limit",
    )
    return TTradeAccountFacts(
      reconciliation_required=reconciliation_required,
      account_concurrent_batch_limit_reached=len(active_batch_keys) >= max_batches,
      account_total_exposure_limit_reached=total_exposure > exposure_limit,
      same_instrument_pending_intent_exists=same_instrument_pending,
      active_batch_count=len(active_batch_keys),
      total_exposure=total_exposure,
      total_asset=total_asset,
    )
  except _SnapshotTooLarge as exc:
    return _unknown_facts(T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE, str(exc))
  except (TypeError, ValueError, OverflowError, KeyError) as exc:
    return _unknown_facts(T_TRADE_ACCOUNT_SNAPSHOT_STALE, str(exc))


class _SnapshotTooLarge(ValueError):
  pass


def _unknown_facts(code: str, detail: str) -> TTradeAccountFacts:
  return TTradeAccountFacts(
    reconciliation_required=None,
    account_concurrent_batch_limit_reached=None,
    account_total_exposure_limit_reached=None,
    same_instrument_pending_intent_exists=None,
    blockers=(code,),
    message=f"{code}: {detail}" if detail else code,
  )


def _bounded_mapping(value: Any, *, limit: int, label: str) -> Mapping[str, Any]:
  if not isinstance(value, Mapping):
    raise ValueError(f"{label} is unavailable")
  if len(value) > limit:
    raise _SnapshotTooLarge(f"{label} exceeds {limit} entries")
  return value


def _first_present(mapping: Any, *keys: str) -> Any:
  if not isinstance(mapping, Mapping):
    return None
  for key in keys:
    if key in mapping and mapping[key] is not None:
      return mapping[key]
  return None


def _status(value: Any) -> str:
  if value is None:
    return ""
  return str(getattr(value, "value", value) or "").strip().upper()


def _finite_non_negative(value: Any, label: str) -> float:
  if isinstance(value, bool) or value is None:
    raise ValueError(f"{label} is unavailable")
  parsed = float(value)
  if not isfinite(parsed) or parsed < 0:
    raise ValueError(f"{label} is not finite")
  return parsed


def _finite_positive(value: Any, label: str) -> float:
  parsed = _finite_non_negative(value, label)
  if parsed <= 0:
    raise ValueError(f"{label} must be positive")
  return parsed


def _positive_int(value: Any, label: str) -> int:
  if isinstance(value, bool):
    raise ValueError(f"{label} is unavailable")
  parsed = float(value)
  if not isfinite(parsed) or not parsed.is_integer():
    raise ValueError(f"{label} is not an integer")
  parsed = int(parsed)
  if parsed <= 0:
    raise ValueError(f"{label} must be positive")
  return parsed


def _non_negative_int(value: Any, label: str) -> int:
  if isinstance(value, bool):
    raise ValueError(f"{label} is unavailable")
  parsed = float(value)
  if not isfinite(parsed) or not parsed.is_integer():
    raise ValueError(f"{label} is not an integer")
  parsed = int(parsed)
  if parsed < 0:
    raise ValueError(f"{label} is negative")
  return parsed


__all__ = [
  "TTradeAccountFacts",
  "T_TRADE_ACCOUNT_SNAPSHOT_STALE",
  "T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE",
  "compute_t_trade_account_facts",
]
