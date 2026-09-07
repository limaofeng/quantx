"""Shared interpretation of a still-active T intent's bounded order attempts."""

from datetime import datetime, timedelta

from quantx_domain.clock import to_naive_utc
from quantx_domain.trading.t_order_policy import TEntryOrderPolicy, TExitOrderPolicy


def t_order_lifecycle_pending(pending) -> bool:
  role = str(getattr(pending, "t_trade_role", "") or "").upper()
  original = getattr(pending, "t_order_original_created_at", None)
  if role not in {"ENTRY", "EXIT"} or original is None:
    return False
  if dict(getattr(pending, "request_metadata", None) or {}).get(
    "t_order_lifecycle_finished"
  ):
    return False
  return True


def t_order_lifecycle_active(pending, now: datetime) -> bool:
  if not t_order_lifecycle_pending(pending):
    return False
  policy = TEntryOrderPolicy() if pending.t_trade_role == "ENTRY" else TExitOrderPolicy()
  return to_naive_utc(now) < to_naive_utc(pending.t_order_original_created_at) + timedelta(
    seconds=policy.total_ttl_seconds
  )
