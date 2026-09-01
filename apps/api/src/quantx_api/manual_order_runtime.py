"""Authoritative manual-order execution mode for the current runtime."""

from __future__ import annotations

from quantx_infrastructure.config.settings import settings


def _configured_live_accounts() -> list[str]:
  configured = settings.real_trading_account_allowlist or []
  values = configured.split(",") if isinstance(configured, str) else configured
  return [
    str(account_id).strip()
    for account_id in values
    if str(account_id).strip()
  ]


def configured_manual_order_execution_mode(account_id: str) -> str:
  """Return the sole manual-order mode allowed by ``liveTrading`` config."""

  normalized_account_id = str(account_id or "").strip()
  accounts = _configured_live_accounts()
  live_enabled = bool(
    str(settings.runtime_profile or "").strip().lower() == "full"
    and settings.enable_real_trading
    and len(accounts) == 1
    and accounts[0] == normalized_account_id
  )
  return "LIVE" if live_enabled else "PAPER"
