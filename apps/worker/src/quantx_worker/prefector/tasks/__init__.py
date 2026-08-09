"""Lazy exports for independently deployable Prefect tasks."""

from importlib import import_module
from typing import Any

_EXPORTS = {
  "collect_disclosure_sync_symbols": "announcement_tasks",
  "save_market_data": "market_data_tasks",
  "sync_stock_disclosures_task": "announcement_tasks",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
  module_name = _EXPORTS.get(name)
  if module_name is None:
    raise AttributeError(name)
  value = getattr(
    import_module(f"{__name__}.{module_name}"),
    name,
  )
  globals()[name] = value
  return value
