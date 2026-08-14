"""Lazy exports for Prefect flows.

Keeping package import side-effect free lets a deployment load one flow
without importing every database and provider adapter.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
  "announcement_sync_flow": "announcement_sync_flow",
  "apns_delivery_flow": "apns_delivery_flow",
  "agent_convergence_flow": "durable_agent_flows",
  "bond_repo_trade_command_flow": "durable_agent_flows",
  "daily_indicator_snapshot_flow": "daily_indicator_snapshot_flow",
  "daily_market_data_sync_flow": "daily_market_data_sync_flow",
  "daily_market_data_request_flow": "durable_agent_flows",
  "divid_factor_sync_flow": "divid_factor_sync_flow",
  "financial_request_flow": "durable_agent_flows",
  "holiday_sync_flow": "holiday_sync_flow",
  "instrument_request_flow": "durable_agent_flows",
  "market_universe_request_flow": "durable_agent_flows",
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
