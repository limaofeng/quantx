"""Canonical, monotonic lifecycle rules for broker-backed orders."""

from __future__ import annotations

from enum import Enum


class OrderLifecycleStatus(str, Enum):
  QUEUED = "QUEUED"
  PENDING = "PENDING"
  SUBMITTED = "SUBMITTED"
  PARTIAL_FILLED = "PARTIAL_FILLED"
  FILLED = "FILLED"
  CANCELLED = "CANCELLED"
  REJECTED = "REJECTED"
  EXPIRED = "EXPIRED"
  KILL_SWITCHED = "KILL_SWITCHED"


TERMINAL_ORDER_STATUSES = frozenset(
  {
    OrderLifecycleStatus.FILLED.value,
    OrderLifecycleStatus.CANCELLED.value,
    OrderLifecycleStatus.REJECTED.value,
    OrderLifecycleStatus.EXPIRED.value,
    OrderLifecycleStatus.KILL_SWITCHED.value,
  }
)

_PROGRESS = {
  OrderLifecycleStatus.QUEUED.value: 0,
  OrderLifecycleStatus.PENDING.value: 0,
  OrderLifecycleStatus.SUBMITTED.value: 1,
  OrderLifecycleStatus.PARTIAL_FILLED.value: 2,
  OrderLifecycleStatus.KILL_SWITCHED.value: 3,
  OrderLifecycleStatus.EXPIRED.value: 4,
  OrderLifecycleStatus.REJECTED.value: 5,
  OrderLifecycleStatus.CANCELLED.value: 6,
  OrderLifecycleStatus.FILLED.value: 7,
}


def normalize_order_status(value: object) -> str:
  text = str(getattr(value, "value", value) or "").split(".")[-1].upper()
  aliases = {
    "UNREPORTED": "PENDING",
    "WAIT_REPORTING": "SUBMITTED",
    "REPORTED": "SUBMITTED",
    "REPORTED_CANCEL": "SUBMITTED",
    "PARTSUCC_CANCEL": "PARTIAL_FILLED",
    "PART_SUCC": "PARTIAL_FILLED",
    "PART_CANCEL": "CANCELLED",
    "CANCELED": "CANCELLED",
    "SUCCEEDED": "FILLED",
    "JUNK": "REJECTED",
  }
  return aliases.get(text, text)


def can_transition_order_status(current: object, proposed: object) -> bool:
  current_value = normalize_order_status(current)
  proposed_value = normalize_order_status(proposed)
  if current_value == proposed_value:
    return True
  if proposed_value not in _PROGRESS:
    return False
  if current_value not in _PROGRESS:
    return True
  if current_value in TERMINAL_ORDER_STATUSES:
    if proposed_value not in TERMINAL_ORDER_STATUSES:
      return False
    return _PROGRESS[proposed_value] >= _PROGRESS[current_value]
  return _PROGRESS[proposed_value] >= _PROGRESS[current_value]
