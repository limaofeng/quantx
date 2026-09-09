"""Read-only view of the control-owned Redis market lease."""

import json
from dataclasses import dataclass
from typing import Any

from quantx_infrastructure.database.redis_pubsub import redis_pubsub

MARKET_DEVICE_LEASE_KEY = "agent:market-device:v2"


@dataclass(frozen=True)
class MarketSessionLease:
  device_id: str
  api_instance_id: str
  agent_session_id: str


class MarketLeaseReader:
  async def market_lease(self, device_id: str) -> MarketSessionLease | None:
    redis = await redis_pubsub.get_redis()
    raw = await redis.get(MARKET_DEVICE_LEASE_KEY)
    if not raw:
      return None
    try:
      lease = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
      return None
    if not isinstance(lease, dict):
      return None
    parsed = MarketSessionLease(
      device_id=str(lease.get("device_id") or ""),
      api_instance_id=str(lease.get("api_instance_id") or ""),
      agent_session_id=str(lease.get("agent_session_id") or ""),
    )
    if (
      parsed.device_id != device_id
      or not parsed.api_instance_id
      or not parsed.agent_session_id
    ):
      return None
    return parsed

  async def is_market_session(self, lease: MarketSessionLease) -> bool:
    return await self.market_lease(lease.device_id) == lease

  async def market_lease_diagnostic(self, device_id: str) -> dict[str, Any]:
    redis = await redis_pubsub.get_redis()
    raw = await redis.get(MARKET_DEVICE_LEASE_KEY)
    try:
      value = json.loads(raw) if raw else None
    except (TypeError, json.JSONDecodeError):
      value = None
    valid = isinstance(value, dict) and all(
      value.get(key) for key in ("device_id", "api_instance_id", "agent_session_id")
    )
    actual_device = str(value.get("device_id") or "") if isinstance(value, dict) else ""
    reason = "MARKET_LEASE_NOT_PUBLISHED"
    if raw:
      reason = (
        "MARKET_LEASE_INVALID"
        if not valid
        else (
          "MARKET_LEASE_DEVICE_MISMATCH"
          if actual_device != device_id
          else "MARKET_LEASE_READY"
        )
      )
    return {
      "reasonCode": reason,
      "deviceId": device_id,
      "controlSessionRegistered": False,
      "controlSessionRevoked": False,
      "marketDataCapability": False,
      "selectedDeviceId": None,
      "redisLeasePresent": bool(raw),
      "redisLeaseValid": bool(valid),
      "redisLeaseDeviceId": actual_device or None,
    }


market_lease_reader = MarketLeaseReader()
