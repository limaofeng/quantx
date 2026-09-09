"""Dedicated WS authentication and bounded delivery without a control websocket."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_contracts.collection_permit import CollectionPermit, CollectionUnit
from quantx_contracts.history_session import (
  HISTORY_SESSION_SUBPROTOCOL,
  HistoryGrant,
  HistoryHeartbeat,
  HistoryRequest,
)
from quantx_infrastructure.auth.tokens import issue_access_token
from quantx_market_data import history_session as api

from tests.infrastructure.test_market_gateway_auth import gateway_auth  # noqa: F401

SESSION = str(uuid4())


class Socket:
  def __init__(self, first, *, heartbeat=None):
    self.scope = {"subprotocols": [HISTORY_SESSION_SUBPROTOCOL]}
    self.app = SimpleNamespace(
      state=SimpleNamespace(store=SimpleNamespace(engine=object()))
    )
    self.first, self.heartbeat = (
      first,
      heartbeat or HistoryHeartbeat(xtdata_ready=True).model_dump_json(),
    )
    self.reads, self.messages, self.closed = 0, [], []
    self.delivered = asyncio.Event()

  async def accept(self, **kwargs):
    self.accepted = kwargs

  async def close(self, **kwargs):
    self.closed.append(kwargs)

  async def receive_text(self):
    self.reads += 1
    if self.reads == 1:
      return self.first
    if self.reads == 2:
      return self.heartbeat
    await self.delivered.wait()
    raise WebSocketDisconnect()

  async def send_text(self, raw):
    value = json.loads(raw)
    self.messages.append(value)
    if value["type"] == "GRANT":
      self.delivered.set()


@pytest.fixture
async def connection(gateway_auth, monkeypatch):  # noqa: F811
  configuration, sessions = gateway_auth
  monkeypatch.setattr(api, "settings", configuration)
  monkeypatch.setattr(api, "AsyncSessionLocal", sessions)
  request = str(uuid4())
  payload = {"operation": "bars", "stock_list": ["000001.SZ"]}
  now = datetime.now(timezone.utc)
  permit = CollectionPermit(
    permit_id=uuid4(),
    device_id=uuid4(),
    owner_epoch=1,
    unit=CollectionUnit.from_payload(request, 0, payload),
    issued_at=now,
    expires_at=now + timedelta(seconds=15),
  )
  store = SimpleNamespace(
    register=AsyncMock(return_value=SESSION),
    heartbeat=AsyncMock(),
    close=AsyncMock(),
    work=AsyncMock(
      return_value=[
        HistoryRequest(
          request_id=request, payload=payload, unit_count=1, completed_units=0
        ),
        HistoryGrant(permit=permit, state="ISSUED", unit_payload=payload),
      ]
    ),
  )
  monkeypatch.setattr(api, "HistorySessionStore", lambda _: store)
  return configuration, store


def auth(token):
  return AgentEnvelope(
    message_type=AgentMessageType.AUTH,
    payload={
      "device_id": "device",
      "access_token": token,
      "capabilities": ["market-data"],
    },
  ).model_dump_json()


async def test_history_token_opens_and_delivers_without_control_registration(
  connection,
):
  configuration, store = connection
  token, _ = issue_access_token(
    "user", "device", configuration, scopes={"agent:history"}
  )
  socket = Socket(auth(token))
  await asyncio.wait_for(api.history_websocket(socket), 2)
  assert [
    value["type"] for value in socket.messages if value["type"] in {"REQUEST", "GRANT"}
  ] == ["REQUEST", "GRANT"]
  store.register.assert_awaited_once()
  assert store.register.call_args.kwargs["device_id"] == "device"
  assert store.register.call_args.kwargs["user_id"] == "user"
  store.heartbeat.assert_awaited_once_with(SESSION, HistoryHeartbeat(xtdata_ready=True))
  store.close.assert_awaited_once_with(SESSION)
  assert token not in json.dumps(socket.messages)


@pytest.mark.parametrize(
  "scopes", [None, {"market-data:read"}, {"agent:history", "market-data:read"}]
)
async def test_other_credentials_cannot_open_history_session(connection, scopes):
  configuration, store = connection
  token, _ = issue_access_token("user", "device", configuration, scopes=scopes)
  socket = Socket(auth(token))
  await api.history_websocket(socket)
  store.register.assert_not_awaited()
  assert socket.closed[-1]["code"] == 4401
  assert socket.messages == []


async def test_missing_history_subprotocol_is_rejected(connection):
  _, store = connection
  socket = Socket("unused")
  socket.scope["subprotocols"] = []
  await api.history_websocket(socket)
  assert socket.closed == [{"code": 4406}]
  store.register.assert_not_awaited()


async def test_control_identity_is_not_accepted_in_history_auth(connection):
  configuration, store = connection
  token, _ = issue_access_token(
    "user", "device", configuration, scopes={"agent:history"}
  )
  envelope = AgentEnvelope.model_validate_json(auth(token))
  envelope.payload["agent_session_id"] = "control-session"
  socket = Socket(envelope.model_dump_json())
  await api.history_websocket(socket)
  store.register.assert_not_awaited()
  assert socket.closed[-1]["code"] == 4401


async def test_oversized_heartbeat_does_not_renew_and_releases_session(connection):
  configuration, store = connection
  token, _ = issue_access_token(
    "user", "device", configuration, scopes={"agent:history"}
  )
  socket = Socket(auth(token), heartbeat=" " * 4097)
  await asyncio.wait_for(api.history_websocket(socket), 2)
  store.heartbeat.assert_not_awaited()
  store.close.assert_awaited_once_with(SESSION)
  assert socket.closed[-1]["reason"] == "HISTORY_SESSION_UNAVAILABLE"


async def test_removed_request_precedes_replacement_delivery(connection):
  configuration, store = connection
  old = store.work.return_value[0]
  replacement = old.model_copy(update={"request_id": uuid4()})
  store.work.side_effect = [[old], [replacement]]
  token, _ = issue_access_token(
    "user", "device", configuration, scopes={"agent:history"}
  )

  class ReplacementSocket(Socket):
    async def send_text(self, raw):
      await super().send_text(raw)
      if json.loads(raw).get("request_id") == str(replacement.request_id):
        self.delivered.set()

  socket = ReplacementSocket(auth(token))
  await asyncio.wait_for(api.history_websocket(socket), 2)
  assert [
    message["type"]
    for message in socket.messages
    if message["type"].startswith("REQUEST")
  ] == ["REQUEST", "REQUEST_REMOVED", "REQUEST"]
  removed = next(
    message for message in socket.messages if message["type"] == "REQUEST_REMOVED"
  )
  assert removed["request_id"] == str(old.request_id)
