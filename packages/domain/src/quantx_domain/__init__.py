"""Pure QuantX trading domain.

This package must remain free of database, filesystem, network, FastAPI,
Prefect, and QMT dependencies. Applications adapt persistence models into
these values.
"""

from .strategies.base import (
  StrategyBase,
  StrategyInput,
  StrategyOutput,
  TradeIntent,
)

__all__ = [
  "StrategyBase",
  "StrategyInput",
  "StrategyOutput",
  "TradeIntent",
]
