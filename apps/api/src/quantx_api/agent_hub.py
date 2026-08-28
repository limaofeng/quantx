"""Single-owner relay between Redis market controls and outbound Agents."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_infrastructure.core.data.remote_market_data import (
  MARKET_DATA_ACTIVE_SUBSCRIPTIONS,
  MARKET_DATA_CONTROL_CHANNEL,
)
from quantx_infrastructure.database.redis_pubsub import redis_pubsub

from quantx_api.api_runtime import API_INSTANCE_ID, API_STARTED_AT

logger = logging.getLogger(__name__)
MARKET_DEVICE_LEASE_KEY = "agent:market-device:v2"
MARKET_DEVICE_LEASE_TTL_SECONDS = 30
MARKET_DEVICE_LEASE_REFRESH_SECONDS = 10
_MARKET_LEASE_UPDATE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
local operation = ARGV[1]
local api_instance_id = ARGV[2]
local api_started_at = tonumber(ARGV[3]) or 0

if operation == 'delete' then
  if not current then
    return 0
  end
  local decoded_ok, decoded = pcall(cjson.decode, current)
  if decoded_ok and decoded['api_instance_id'] == api_instance_id then
    return redis.call('DEL', KEYS[1])
  end
  return 0
end

if current then
  local decoded_ok, decoded = pcall(cjson.decode, current)
  if decoded_ok then
    local current_instance_id = tostring(decoded['api_instance_id'] or '')
    local current_started_at = tonumber(decoded['api_started_at_micros']) or 0
    if current_instance_id ~= api_instance_id and current_started_at >= api_started_at then
      return 0
    end
  end
end

redis.call('SET', KEYS[1], ARGV[4], 'EX', tonumber(ARGV[5]))
return 1
"""


@dataclass(frozen=True)
class MarketSessionLease:
  device_id: str
  api_instance_id: str
  agent_session_id: str


@dataclass
class AgentControlSession:
  device_id: str
  capabilities: set[str]
  authorized_account_ids: frozenset[str]
  queue: asyncio.Queue[AgentEnvelope]
  api_instance_id: str
  agent_session_id: str
  server_connected_at: datetime
  remote_address_summary: str
  revoked: asyncio.Event
  market_reconciliation_ready: bool = False


class AgentConnectionHub:
  def __init__(
    self,
    *,
    api_instance_id: str = API_INSTANCE_ID,
    api_started_at: datetime = API_STARTED_AT,
  ) -> None:
    self.api_instance_id = api_instance_id
    normalized_started_at = api_started_at
    if normalized_started_at.tzinfo is None:
      normalized_started_at = normalized_started_at.replace(tzinfo=timezone.utc)
    else:
      normalized_started_at = normalized_started_at.astimezone(timezone.utc)
    self.api_started_at_micros = int(normalized_started_at.timestamp() * 1_000_000)
    self._sessions: dict[str, AgentControlSession] = {}
    self._active_controls: dict[str, dict[str, Any]] = {}
    self._market_device_id: str | None = None
    self._lock = asyncio.Lock()

  async def _publish_market_lease(
    self,
    session: AgentControlSession | None,
  ) -> None:
    redis = await redis_pubsub.get_redis()
    if session is None:
      await redis.eval(
        _MARKET_LEASE_UPDATE_SCRIPT,
        1,
        MARKET_DEVICE_LEASE_KEY,
        "delete",
        self.api_instance_id,
        self.api_started_at_micros,
        "",
        MARKET_DEVICE_LEASE_TTL_SECONDS,
      )
      return
    serialized = json.dumps(
      {
        "device_id": session.device_id,
        "api_instance_id": session.api_instance_id,
        "api_started_at_micros": self.api_started_at_micros,
        "agent_session_id": session.agent_session_id,
      },
      separators=(",", ":"),
    )
    await redis.eval(
      _MARKET_LEASE_UPDATE_SCRIPT,
      1,
      MARKET_DEVICE_LEASE_KEY,
      "set",
      self.api_instance_id,
      self.api_started_at_micros,
      serialized,
      MARKET_DEVICE_LEASE_TTL_SECONDS,
    )

  async def _load_active_controls(self) -> None:
    redis = await redis_pubsub.get_redis()
    serialized = await redis.hgetall(MARKET_DATA_ACTIVE_SUBSCRIPTIONS)
    controls: dict[str, dict[str, Any]] = {}
    for subscription_id, value in serialized.items():
      try:
        control = json.loads(value)
      except (TypeError, json.JSONDecodeError):
        continue
      if isinstance(control, dict) and control.get("kind") == "quote":
        controls[str(subscription_id)] = control
    self._active_controls = controls

  @staticmethod
  def _control_envelope(control: dict[str, Any]) -> AgentEnvelope:
    message_type = (
      AgentMessageType.MARKET_SUBSCRIBE
      if control.get("action") == "SUBSCRIBE"
      else AgentMessageType.MARKET_UNSUBSCRIBE
    )
    return AgentEnvelope(message_type=message_type, payload=control)

  def _queue_snapshot(self, session: AgentControlSession) -> None:
    session.queue.put_nowait(
      AgentEnvelope(
        message_type=AgentMessageType.MARKET_RESET,
        payload={"reason": "session_reconciliation"},
      )
    )
    for control in self._active_controls.values():
      session.queue.put_nowait(self._control_envelope(control))

  def _select_market_session(self) -> AgentControlSession | None:
    eligible = sorted(
      (
        session
        for session in self._sessions.values()
        if (
          "market-data" in session.capabilities
          and session.market_reconciliation_ready
          and not session.revoked.is_set()
        )
      ),
      key=lambda value: value.device_id,
    )
    return eligible[0] if eligible else None

  def _current_market_session(self) -> AgentControlSession | None:
    session = self._sessions.get(self._market_device_id or "")
    if (
      session is None
      or session.revoked.is_set()
      or "market-data" not in session.capabilities
      or not session.market_reconciliation_ready
    ):
      return None
    return session

  async def register(
    self,
    device_id: str,
    capabilities: set[str],
    *,
    authorized_account_ids: set[str],
    connected_at: datetime,
    remote_address_summary: str,
  ) -> AgentControlSession:
    queue: asyncio.Queue[AgentEnvelope] = asyncio.Queue()
    normalized_capabilities = {
      str(value).strip().lower() for value in capabilities if str(value).strip()
    }
    session = AgentControlSession(
      device_id=device_id,
      capabilities=normalized_capabilities,
      authorized_account_ids=frozenset(authorized_account_ids),
      queue=queue,
      api_instance_id=self.api_instance_id,
      agent_session_id=str(uuid.uuid4()),
      server_connected_at=connected_at,
      remote_address_summary=remote_address_summary,
      revoked=asyncio.Event(),
      # Data-only and paper sessions do not have a live account snapshot to
      # reconcile. A live session becomes market-eligible only after Engine
      # has applied its new complete snapshot and a subsequent heartbeat keeps
      # the server-owned status at READY.
      market_reconciliation_ready="live" not in normalized_capabilities,
    )
    async with self._lock:
      previous = self._sessions.get(device_id)
      if previous is not None:
        previous.revoked.set()
      self._sessions[device_id] = session
      await self._load_active_controls()
      if self._current_market_session() is None:
        self._market_device_id = None
      if self._market_device_id is None:
        selected = self._select_market_session()
        self._market_device_id = selected.device_id if selected else None
      if self._market_device_id == device_id:
        self._queue_snapshot(session)
      else:
        queue.put_nowait(
          AgentEnvelope(
            message_type=AgentMessageType.MARKET_RESET,
            payload={"reason": "standby_agent"},
          )
        )
      selected = self._current_market_session()
      await self._publish_market_lease(selected)
    return session

  async def authorize_market_after_reconciliation(
    self,
    control_session: AgentControlSession,
  ) -> bool:
    """Make the current live session eligible only after durable reconciliation."""

    async with self._lock:
      session = self._sessions.get(control_session.device_id)
      if (
        session is not control_session
        or session.revoked.is_set()
        or "market-data" not in session.capabilities
      ):
        return False
      session.market_reconciliation_ready = True
      selected = self._current_market_session()
      if selected is None:
        selected = self._select_market_session()
        self._market_device_id = selected.device_id if selected else None
        if selected is not None:
          self._queue_snapshot(selected)
      await self._publish_market_lease(selected)
      return selected is session

  async def revoke(self, device_id: str) -> bool:
    """Wake the registered control-session guard after durable revocation."""
    async with self._lock:
      session = self._sessions.get(device_id)
      if session is None:
        return False
      session.revoked.set()
      if self._market_device_id == device_id:
        selected = self._select_market_session()
        self._market_device_id = selected.device_id if selected else None
        if selected is not None:
          self._queue_snapshot(selected)
        await self._publish_market_lease(selected)
      return True

  async def wait_until_revoked(
    self,
    session: AgentControlSession,
    *,
    timeout_seconds: float,
  ) -> bool:
    """Wait without touching PostgreSQL; ``True`` also covers replacement."""
    async with self._lock:
      current = self._sessions.get(session.device_id)
      if current is not session:
        return True
      revoked = current.revoked
    try:
      await asyncio.wait_for(revoked.wait(), timeout=max(0.0, timeout_seconds))
    except asyncio.TimeoutError:
      return False
    return True

  async def is_connected(
    self,
    device_id: str,
    *,
    agent_session_id: str = "",
  ) -> bool:
    """Return in-process session state without polling PostgreSQL."""
    async with self._lock:
      session = self._sessions.get(device_id)
      return bool(
        session is not None
        and not session.revoked.is_set()
        and (not agent_session_id or session.agent_session_id == agent_session_id)
      )

  async def current_session(self, device_id: str) -> AgentControlSession | None:
    async with self._lock:
      session = self._sessions.get(device_id)
      if session is None or session.revoked.is_set():
        return None
      return session

  async def unregister(
    self,
    session: AgentControlSession,
  ) -> bool:
    async with self._lock:
      current = self._sessions.get(session.device_id)
      if current is not session:
        return False
      current.revoked.set()
      self._sessions.pop(session.device_id, None)
      if self._market_device_id != session.device_id:
        return True
      selected = self._select_market_session()
      self._market_device_id = selected.device_id if selected else None
      if selected is not None:
        self._queue_snapshot(selected)
      await self._publish_market_lease(selected)
      return True

  async def market_lease(self, device_id: str) -> MarketSessionLease | None:
    redis = await redis_pubsub.get_redis()
    raw = await redis.get(MARKET_DEVICE_LEASE_KEY)
    if not raw:
      return None
    try:
      lease = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
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

  async def is_market_device(self, device_id: str) -> bool:
    return await self.market_lease(device_id) is not None

  async def is_market_session(self, lease: MarketSessionLease) -> bool:
    current = await self.market_lease(lease.device_id)
    return current == lease

  async def refresh_market_device(
    self,
    control_session: AgentControlSession,
  ) -> None:
    async with self._lock:
      session = self._sessions.get(control_session.device_id)
      if (
        session is not control_session
        or self._market_device_id != control_session.device_id
        or not session.market_reconciliation_ready
      ):
        return
      await self._publish_market_lease(session)

  async def _dispatch(self, control: dict[str, Any]) -> None:
    if control.get("kind") != "quote":
      return
    subscription_id = str(control.get("subscription_id") or "")
    if not subscription_id:
      return
    async with self._lock:
      if control.get("action") == "SUBSCRIBE":
        self._active_controls[subscription_id] = dict(control)
      else:
        self._active_controls.pop(subscription_id, None)
      session = self._current_market_session()
      if session is not None:
        session.queue.put_nowait(self._control_envelope(control))

  async def _reconcile_selected_session(self) -> None:
    async with self._lock:
      await self._load_active_controls()
      session = self._current_market_session()
      if session is not None:
        self._queue_snapshot(session)

  async def run_control_relay(self, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
      subscription = None
      try:
        subscription = await redis_pubsub.open_subscription(MARKET_DATA_CONTROL_CHANNEL)
        # The Redis hash closes the subscribe-before-listen race and lets an
        # API or Agent restart reconstruct Engine-owned live subscriptions.
        await self._reconcile_selected_session()
        async for control in subscription.messages():
          if stopped.is_set():
            break
          await self._dispatch(control)
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        logger.warning(
          "Market-data control relay unavailable: %s",
          exc.__class__.__name__,
        )
        try:
          await asyncio.wait_for(stopped.wait(), timeout=1.0)
        except asyncio.TimeoutError:
          pass
      finally:
        if subscription is not None:
          await subscription.close()


agent_connection_hub = AgentConnectionHub()
