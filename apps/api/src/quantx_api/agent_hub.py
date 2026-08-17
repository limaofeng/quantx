"""Single-owner relay between Redis market controls and outbound Agents."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_infrastructure.core.data.remote_market_data import (
  MARKET_DATA_ACTIVE_SUBSCRIPTIONS,
  MARKET_DATA_CONTROL_CHANNEL,
)
from quantx_infrastructure.database.redis_pubsub import redis_pubsub

logger = logging.getLogger(__name__)


@dataclass
class _Session:
  device_id: str
  capabilities: set[str]
  queue: asyncio.Queue[AgentEnvelope]


class AgentConnectionHub:
  def __init__(self) -> None:
    self._sessions: dict[str, _Session] = {}
    self._active_controls: dict[str, dict[str, Any]] = {}
    self._market_device_id: str | None = None
    self._lock = asyncio.Lock()

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

  def _queue_snapshot(self, session: _Session) -> None:
    session.queue.put_nowait(
      AgentEnvelope(
        message_type=AgentMessageType.MARKET_RESET,
        payload={"reason": "session_reconciliation"},
      )
    )
    for control in self._active_controls.values():
      session.queue.put_nowait(self._control_envelope(control))

  def _select_market_session(self) -> _Session | None:
    eligible = sorted(
      (
        session
        for session in self._sessions.values()
        if "market-data" in session.capabilities
      ),
      key=lambda value: value.device_id,
    )
    return eligible[0] if eligible else None

  async def register(
    self,
    device_id: str,
    capabilities: set[str],
  ) -> asyncio.Queue[AgentEnvelope]:
    queue: asyncio.Queue[AgentEnvelope] = asyncio.Queue()
    session = _Session(device_id, set(capabilities), queue)
    async with self._lock:
      self._sessions[device_id] = session
      await self._load_active_controls()
      if self._market_device_id not in self._sessions:
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
    return queue

  async def unregister(
    self,
    device_id: str,
    queue: asyncio.Queue[AgentEnvelope],
  ) -> None:
    async with self._lock:
      current = self._sessions.get(device_id)
      if current is None or current.queue is not queue:
        return
      self._sessions.pop(device_id, None)
      if self._market_device_id != device_id:
        return
      selected = self._select_market_session()
      self._market_device_id = selected.device_id if selected else None
      if selected is not None:
        self._queue_snapshot(selected)

  async def is_market_device(self, device_id: str) -> bool:
    async with self._lock:
      return self._market_device_id == device_id

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
      session = self._sessions.get(self._market_device_id or "")
      if session is not None:
        session.queue.put_nowait(self._control_envelope(control))

  async def _reconcile_selected_session(self) -> None:
    async with self._lock:
      await self._load_active_controls()
      session = self._sessions.get(self._market_device_id or "")
      if session is not None:
        self._queue_snapshot(session)

  async def run_control_relay(self, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
      subscription = None
      try:
        subscription = await redis_pubsub.open_subscription(
          MARKET_DATA_CONTROL_CHANNEL
        )
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
