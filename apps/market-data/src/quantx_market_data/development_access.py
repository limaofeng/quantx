"""Restricted production credentials for development market consumers."""

import hmac
import os

from fastapi import Header, HTTPException


def require_data_access(authorization: str = Header(default="")) -> None:
  if not authorized(authorization):
    raise HTTPException(403, "Market data access denied")


def authorized(authorization: str) -> bool:
  token = os.environ.get("QUANTX_MARKET_DATA_TOKEN", "")
  return (
    os.environ.get("ENV") == "production"
    and len(token) >= 32
    and "CHANGE_ME" not in token
    and hmac.compare_digest(authorization, f"Bearer {token}")
  )
