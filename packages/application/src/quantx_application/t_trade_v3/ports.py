"""Ports for T-trade V3 adapters.

Implementations belong to Infrastructure or the Engine composition root.  No
port in this module knows how a row, transaction, subscription, or broker is
implemented.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Protocol

from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityReferenceProfile,
)

ProfilePayload = Optional[
  OpportunityReferenceProfile | Mapping[str, Any]
]


class D1ReferenceProfilePort(Protocol):
  """Read the latest complete profile strictly before the evaluation date."""

  async def load_reference_profile(
    self,
    *,
    instrument_code: str,
    evaluated_at: datetime,
    required_version: Optional[str] = None,
  ) -> ProfilePayload: ...


class OpportunityEvaluationMaterializerPort(Protocol):
  """Append a post-CAS evaluation event idempotently."""

  async def materialize_evaluation(
    self,
    *,
    event: Mapping[str, Any],
    account_id: str,
    strategy_run_id: str,
  ) -> Any: ...


__all__ = [
  "D1ReferenceProfilePort",
  "OpportunityEvaluationMaterializerPort",
  "ProfilePayload",
]
