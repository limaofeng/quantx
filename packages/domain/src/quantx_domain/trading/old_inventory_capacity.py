"""Shared old-inventory claim policy; no account storage access."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class OldInventoryClaimResult:
  claimable_volume: int
  unclaimed_volume: int
  protected_floor: int
  allocation: Mapping[str, int]
  unclaimed_by_bucket: Mapping[str, int]


def allocate_old_inventory_claims(
  *,
  available_by_bucket: Mapping[str, int],
  required_claim_qty: int,
  protected_core_floor: int = 0,
  allow_core_claim: bool = False,
) -> OldInventoryClaimResult:
  """Apply the frozen swing -> core -> never locked_core claim order."""

  available = {
    name: max(0, int(available_by_bucket.get(name, 0) or 0))
    for name in ("locked_core", "core", "swing")
  }
  protected_core = max(0, int(protected_core_floor or 0))
  core_claimable = max(0, available["core"] - protected_core) if allow_core_claim else 0
  claimable = available["swing"] + core_claimable
  remaining_claim = max(0, int(required_claim_qty or 0))
  swing_claim = min(remaining_claim, available["swing"])
  remaining_claim -= swing_claim
  core_claim = min(remaining_claim, core_claimable)
  remaining_claim -= core_claim
  if remaining_claim > 0:
    raise ValueError(
      "T_TRADE_BUCKET_CAPACITY_EXCEEDED:旧仓认领将侵占保护底仓或 locked_core"
    )
  return OldInventoryClaimResult(
    claimable_volume=claimable,
    unclaimed_volume=max(0, claimable - int(required_claim_qty or 0)),
    protected_floor=available["locked_core"] + min(protected_core, available["core"]),
    allocation={"swing": swing_claim, "core": core_claim, "locked_core": 0},
    unclaimed_by_bucket={
      "swing": max(0, available["swing"] - swing_claim),
      "core": max(0, core_claimable - core_claim),
      "locked_core": 0,
    },
  )
