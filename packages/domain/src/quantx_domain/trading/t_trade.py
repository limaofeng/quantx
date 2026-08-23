"""Execution-side primitives retained by the A-share intraday T assistant.

Opportunity recognition lives exclusively in
``t_trade_opportunity_engine``.  This module intentionally contains no legacy
Signal DTO, evaluator, or strategy-side quantity sizing path.
"""

from __future__ import annotations

from dataclasses import dataclass

from quantx_domain.trading.exit_plan import TradingCostPolicy


@dataclass(frozen=True)
class TickSample:
  """Minimal price sample used when projecting an existing exit plan."""

  timestamp_ms: int
  price: float
  bid_price: float = 0.0
  ask_price: float = 0.0
  cumulative_amount: float = 0.0
  cumulative_volume: float = 0.0


__all__ = ["TickSample", "TradingCostPolicy"]
