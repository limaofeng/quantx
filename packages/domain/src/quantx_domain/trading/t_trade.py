"""Execution-side primitives retained by the A-share intraday T assistant.

Opportunity recognition lives exclusively in
``t_trade_opportunity_engine``.  This module intentionally contains no legacy
Signal DTO, evaluator, or strategy-side quantity sizing path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Any, Optional

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


class AShareCumulativeVolumeSource(StrEnum):
  """Provenance of the canonical cumulative A-share volume in shares."""

  NATIVE_PVOLUME = "NATIVE_PVOLUME"
  DERIVED_VOLUME_LOTS = "DERIVED_VOLUME_LOTS"
  UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class AShareCumulativeVolume:
  """Canonical cumulative volume used by A-share opportunity features.

  XTData exposes ``pvolume`` as the raw share count and ``volume`` as the
  exchange lot count for A-share Tick data. Historical transfers may contain
  a zero ``pvolume`` while retaining a valid ``volume``. The fallback is local
  to A-share strategy inputs and never mutates the vendor payload.
  """

  shares: Optional[float]
  source: AShareCumulativeVolumeSource


def normalize_ashare_cumulative_volume(
  *,
  pvolume: Any,
  volume: Any,
) -> AShareCumulativeVolume:
  native = _positive_finite(pvolume)
  if native is not None:
    return AShareCumulativeVolume(
      shares=native,
      source=AShareCumulativeVolumeSource.NATIVE_PVOLUME,
    )
  lots = _positive_finite(volume)
  if lots is not None:
    return AShareCumulativeVolume(
      shares=lots * 100.0,
      source=AShareCumulativeVolumeSource.DERIVED_VOLUME_LOTS,
    )
  return AShareCumulativeVolume(
    shares=None,
    source=AShareCumulativeVolumeSource.UNAVAILABLE,
  )


def _positive_finite(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if isfinite(normalized) and normalized > 0.0 else None


__all__ = [
  "AShareCumulativeVolume",
  "AShareCumulativeVolumeSource",
  "TickSample",
  "TradingCostPolicy",
  "normalize_ashare_cumulative_volume",
]
