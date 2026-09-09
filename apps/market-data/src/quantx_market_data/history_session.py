"""History-only websocket: authenticate, publish liveness, deliver stored work."""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_contracts.history_session import (
  HISTORY_SESSION_SUBPROTOCOL,
  HistoryAuthentication,
  HistoryAuthResult,
  HistoryHeartbeat,
  HistoryHeartbeatAck,
)
from quantx_infrastructure.auth.agent_access import authenticate_agent_session
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.services.market_data_history_session_store import (
  HistorySessionStore,
)

router = APIRouter(tags=["history-session"])


async def _authenticate(token, device_id):
  async with AsyncSessionLocal() as db:
    return await authenticate_agent_session(
      db, settings, token=token, expected_device_id=device_id, history=True
    )


async def _read(socket):
  raw = await asyncio.wait_for(socket.receive_text(), 10)
  if len(raw.encode()) > 4096:
    raise ValueError("history control frame exceeds limit")
  return raw


@router.websocket("/ws/agent/history")
async def history_websocket(socket: WebSocket):
  if HISTORY_SESSION_SUBPROTOCOL not in socket.scope.get("subprotocols", []):
    await socket.close(code=4406)
    return
  await socket.accept(subprotocol=HISTORY_SESSION_SUBPROTOCOL)
  store = HistorySessionStore(socket.app.state.store.engine)
  session_id = None
  tasks = []
  send_lock = asyncio.Lock()

  async def send(payload):
    encoded = json.dumps(payload, separators=(",", ":"))
    if len(encoded.encode()) > 256 * 1024:
      raise ValueError("history work frame exceeds limit")
    async with send_lock:
      await asyncio.wait_for(socket.send_text(encoded), 3)

  try:
    first = AgentEnvelope.model_validate_json(await _read(socket))
    if first.message_type != AgentMessageType.AUTH:
      raise AuthError("UNAUTHENTICATED", "history AUTH required")
    authentication = HistoryAuthentication.model_validate(first.payload)
    token, device_id = authentication.access_token, authentication.device_id
    authenticated = await asyncio.wait_for(_authenticate(token, device_id), 3)
    capabilities = authentication.capabilities
    if "market-data" not in capabilities:
      raise AuthError("FORBIDDEN", "history market-data capability required")
    session_id = await asyncio.wait_for(
      store.register(
        device_id=authenticated.device.id,
        user_id=authenticated.device.user_id,
        token_expires_at=authenticated.expires_at,
        capabilities=capabilities,
      ),
      3,
    )
    await send(HistoryAuthResult(session_id=session_id).model_dump(mode="json"))

    async def receive():
      while True:
        heartbeat = HistoryHeartbeat.model_validate_json(await _read(socket))
        # Re-read persisted device identity and token expiry, not a cached
        # trade connection or Redis lease. Revocation cannot renew this session.
        await asyncio.wait_for(_authenticate(token, device_id), 3)
        await asyncio.wait_for(store.heartbeat(session_id, heartbeat), 3)
        await send(HistoryHeartbeatAck(session_id=session_id).model_dump(mode="json"))

    async def deliver():
      previous = {}
      while True:
        messages = await asyncio.wait_for(store.work(session_id), 3)
        current = {}
        for message in messages:
          value = message.model_dump(mode="json")
          key = (value["type"], value.get("request_id") or value["permit"]["permit_id"])
          current[key] = value
          if previous.get(key) != value:
            await send(value)
        previous = current
        await asyncio.sleep(0.5)

    tasks = [asyncio.create_task(receive()), asyncio.create_task(deliver())]
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
      task.result()
  except WebSocketDisconnect:
    pass
  except Exception:
    # Provider errors and tokens never enter control responses or diagnostics.
    try:
      await asyncio.wait_for(
        socket.close(code=4401, reason="HISTORY_SESSION_UNAVAILABLE"), 2
      )
    except Exception:
      pass
  finally:
    for task in tasks:
      task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if session_id is not None:
      try:
        await asyncio.wait_for(store.close(session_id), 2)
      except Exception:
        pass  # The persisted 15-second lease still expires without a heartbeat.
