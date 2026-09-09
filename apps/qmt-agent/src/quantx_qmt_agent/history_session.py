"""Bounded history transport; native work never runs in the socket reader."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from pydantic import Field, TypeAdapter
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_contracts.collection_permit import CollectionUnit
from quantx_contracts.history_session import (
  HISTORY_SESSION_SUBPROTOCOL,
  HistoryAuthentication,
  HistoryAuthResult,
  HistoryGrant,
  HistoryHeartbeat,
  HistoryHeartbeatAck,
  HistoryRequest,
  HistoryRequestRemoved,
)

from .endpoints import websocket_url
from .historical_worker import historical_work_units

HistoryWork = HistoryRequest | HistoryRequestRemoved | HistoryGrant
_MESSAGE = TypeAdapter(
  Annotated[HistoryWork | HistoryHeartbeatAck, Field(discriminator="type")]
)


class HistorySessionClient:
  """One connection lifetime. The runtime owns reconnect and durable execution.

  connect must use the enrolled endpoint's TLS and redirect policy. A failed
  handler ends the session; replay uses persisted identities, never a new task.
  """

  def __init__(
    self,
    *,
    api_url: str,
    device_id: str,
    capabilities: list[str],
    connect,
    token: Callable[[], Awaitable[str]],
    health: Callable[[], HistoryHeartbeat],
    handle: Callable[[HistoryWork], Awaitable[None]],
    reset: Callable[[], Awaitable[None]],
  ):
    self.url = websocket_url(api_url, "/ws/agent/history")
    self.device_id = str(UUID(device_id))
    self.capabilities = list(capabilities)
    self.connect, self.token, self.health, self.handle = connect, token, health, handle
    self.reset = reset
    self.session_id: UUID | None = None
    self.last_ack_monotonic: float | None = None
    self._running = False

  async def run(self) -> None:
    if self._running:
      raise RuntimeError("history session already connected")
    self._running = True
    tasks = []
    cleanup_started = False
    try:
      async with asyncio.timeout(10):
        token = await self.token()
      authentication = HistoryAuthentication(
        device_id=self.device_id, access_token=token, capabilities=self.capabilities
      )
      async with self.connect(
        self.url,
        subprotocols=[HISTORY_SESSION_SUBPROTOCOL],
        max_size=256 * 1024,
        max_queue=8,
        open_timeout=10,
        close_timeout=3,
        ping_interval=20,
        ping_timeout=20,
      ) as socket:
        async with asyncio.timeout(10):
          await socket.send(
            AgentEnvelope(
              message_type=AgentMessageType.AUTH,
              payload=authentication.model_dump(mode="json"),
            ).model_dump_json()
          )
          result = HistoryAuthResult.model_validate_json(await socket.recv())
        self.session_id = result.session_id
        queue: asyncio.Queue[HistoryWork] = asyncio.Queue(maxsize=8)
        acknowledgement = asyncio.Event()
        awaiting_ack = False

        async def receive():
          while True:
            raw = await socket.recv()
            if not isinstance(raw, str) or len(raw.encode()) > 256 * 1024:
              raise ValueError("invalid history work frame")
            message = _MESSAGE.validate_json(raw)
            if isinstance(message, HistoryHeartbeatAck):
              if (
                message.session_id != self.session_id
                or not awaiting_ack
                or acknowledgement.is_set()
              ):
                raise ValueError("unexpected history heartbeat acknowledgement")
              self.last_ack_monotonic = asyncio.get_running_loop().time()
              acknowledgement.set()
            else:
              # Disconnect on overload instead of blocking heartbeat delivery.
              queue.put_nowait(message)

        async def heartbeat():
          nonlocal awaiting_ack
          while True:
            started = asyncio.get_running_loop().time()
            acknowledgement.clear()
            awaiting_ack = True
            async with asyncio.timeout(5):
              await socket.send(self.health().model_dump_json())
              await acknowledgement.wait()
            awaiting_ack = False
            await asyncio.sleep(
              max(0, 5 - (asyncio.get_running_loop().time() - started))
            )

        async def consume():
          active: dict[UUID, tuple[HistoryRequest, tuple[dict, ...]]] = {}
          while True:
            message = await queue.get()
            if isinstance(message, HistoryRequest):
              previous = active.get(message.request_id)
              if previous is None:
                if len(active) >= 2:
                  raise ValueError("history pipeline exceeds capacity")
                # Full-request validation precedes splitting. Cache the bounded
                # immutable plan rather than recomputing it on every progress frame.
                units = historical_work_units(message.payload)
                if len(units) != message.unit_count:
                  raise ValueError("history plan unit count mismatch")
              else:
                original, units = previous
                if (
                  original.payload != message.payload
                  or original.unit_count != message.unit_count
                  or message.completed_units < original.completed_units
                ):
                  raise ValueError("history request changed or progress regressed")
              active[message.request_id] = (message.model_copy(deep=True), units)
            elif isinstance(message, HistoryRequestRemoved):
              active.pop(message.request_id, None)
            elif (
              str(message.permit.device_id) != self.device_id
              or message.permit.unit.request_id not in active
            ):
              raise ValueError("history grant has no matching device/request")
            else:
              request, units = active[message.permit.unit.request_id]
              index = message.permit.unit.unit_index
              if (
                index != request.completed_units
                or index >= len(units)
                or CollectionUnit.from_payload(
                  str(request.request_id), index, units[index]
                )
                != message.permit.unit
                or CollectionUnit.from_payload(
                  str(request.request_id), index, message.unit_payload
                )
                != message.permit.unit
              ):
                raise ValueError("history grant does not match canonical plan")
            # The handler owns durable request/artifact state; removal only
            # changes routing eligibility.
            await self.handle(message)

        tasks = [
          asyncio.create_task(heartbeat()),
          asyncio.create_task(receive()),
          asyncio.create_task(consume()),
        ]
        try:
          done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
          for task in done:
            task.result()
        finally:
          cleanup_started = True
          await self._end_session(tasks)
    finally:
      try:
        if not cleanup_started:
          await self._end_session(tasks)
      finally:
        self._running = False
        self.session_id = None
        self.last_ack_monotonic = None

  async def _end_session(self, tasks):
    self.session_id = None
    self.last_ack_monotonic = None
    for task in tasks:
      task.cancel()

    async def cleanup():
      # Native work and disk writes must finish before dropping route ownership.
      await asyncio.gather(*tasks, return_exceptions=True)
      await self.reset()

    joining = asyncio.create_task(cleanup())
    cancelled = False
    while not joining.done():
      try:
        await asyncio.shield(joining)
      except asyncio.CancelledError:
        cancelled = True
    joining.result()
    if cancelled:
      raise asyncio.CancelledError
