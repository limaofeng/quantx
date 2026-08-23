"""Shared freshness contract for account and broker position snapshots."""

from datetime import timedelta

ACCOUNT_SNAPSHOT_STALE_CODE = "T_TRADE_PORTFOLIO_SNAPSHOT_STALE"

# EntryPlan and account-level T-trade coordination must agree on this window.
ACCOUNT_SNAPSHOT_MAX_AGE = timedelta(seconds=90)


__all__ = ["ACCOUNT_SNAPSHOT_MAX_AGE", "ACCOUNT_SNAPSHOT_STALE_CODE"]
