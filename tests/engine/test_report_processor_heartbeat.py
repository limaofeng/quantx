from __future__ import annotations

import pytest
from quantx_engine.report_processor import _snapshot_can_promote_heartbeat


@pytest.mark.parametrize("status", ["RECONCILING", "reconciling"])
def test_reconciliation_snapshot_may_promote_agent_heartbeat(
  status: str,
) -> None:
  assert _snapshot_can_promote_heartbeat(status)


@pytest.mark.parametrize(
  "status",
  [
    None,
    "READY",
    "TRADING_UNAVAILABLE",
    "XTDATA_UNAVAILABLE",
    "EMERGENCY_STOP",
    "OFFLINE",
  ],
)
def test_snapshot_does_not_mask_newer_agent_failure(
  status: str | None,
) -> None:
  assert not _snapshot_can_promote_heartbeat(status)
