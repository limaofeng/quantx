"""Minimal ASGI health listener owned by the QMT Agent process."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn

from .health import AgentHealthState

DEFAULT_QMT_AGENT_HEALTH_HOST = "0.0.0.0"
DEFAULT_QMT_AGENT_HEALTH_PORT = 18084

AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]


def health_server_address() -> tuple[str, int]:
  host = os.environ.get(
    "QMT_AGENT_HEALTH_HOST",
    DEFAULT_QMT_AGENT_HEALTH_HOST,
  ).strip()
  if not host:
    raise ValueError("QMT_AGENT_HEALTH_HOST must not be empty")
  raw_port = os.environ.get(
    "QMT_AGENT_HEALTH_PORT",
    str(DEFAULT_QMT_AGENT_HEALTH_PORT),
  ).strip()
  try:
    port = int(raw_port)
  except ValueError:
    raise ValueError("QMT_AGENT_HEALTH_PORT must be an integer") from None
  if not 1 <= port <= 65535:
    raise ValueError("QMT_AGENT_HEALTH_PORT must be between 1 and 65535")
  return host, port


class QmtAgentHealthApplication:
  def __init__(self, state: AgentHealthState) -> None:
    self._state = state

  async def __call__(self, scope, receive, send: AsgiSend) -> None:
    del receive
    if scope.get("type") != "http":
      raise RuntimeError("QMT Agent health application only supports HTTP")
    if scope.get("method") != "GET":
      await self._empty_response(send, 405, [(b"allow", b"GET")])
      return
    if scope.get("query_string"):
      await self._empty_response(send, 404)
      return

    path = scope.get("path")
    if path == "/health/live":
      response = self._state.live_response()
      status_code = 200
    elif path == "/health/ready":
      response = self._state.snapshot()
      status_code = 200 if response.status.value == "ready" else 503
    else:
      await self._empty_response(send, 404)
      return

    body = response.model_dump_json().encode("utf-8")
    await send(
      {
        "type": "http.response.start",
        "status": status_code,
        "headers": [
          (b"content-type", b"application/json"),
          (b"content-length", str(len(body)).encode("ascii")),
          (b"cache-control", b"no-store"),
          (b"x-content-type-options", b"nosniff"),
        ],
      }
    )
    await send({"type": "http.response.body", "body": body})

  @staticmethod
  async def _empty_response(
    send: AsgiSend,
    status_code: int,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
  ) -> None:
    await send(
      {
        "type": "http.response.start",
        "status": status_code,
        "headers": [
          (b"content-length", b"0"),
          (b"cache-control", b"no-store"),
          (b"x-content-type-options", b"nosniff"),
          *(extra_headers or []),
        ],
      }
    )
    await send({"type": "http.response.body", "body": b""})


class QmtAgentHealthServer:
  """Run Uvicorn on a pre-bound socket so bind errors stay ordinary errors."""

  def __init__(self, state: AgentHealthState, host: str, port: int) -> None:
    self.app = QmtAgentHealthApplication(state)
    self.host = host
    self.port = port

  async def run(self) -> None:
    listener = self._bind_listener()
    server = uvicorn.Server(
      uvicorn.Config(
        self.app,
        host=self.host,
        port=self.port,
        access_log=False,
        interface="asgi3",
        lifespan="off",
        log_level="info",
        server_header=False,
        date_header=False,
        timeout_graceful_shutdown=5,
      )
    )
    serving = asyncio.create_task(
      server.serve(sockets=[listener]),
      name="qmt-agent-health-uvicorn",
    )
    try:
      await asyncio.shield(serving)
    except asyncio.CancelledError:
      server.should_exit = True
      try:
        await asyncio.wait_for(asyncio.shield(serving), timeout=6)
      except asyncio.TimeoutError:
        server.force_exit = True
        await asyncio.gather(serving, return_exceptions=True)
      raise
    finally:
      listener.close()

  def _bind_listener(self) -> socket.socket:
    family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
      if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
      else:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      listener.bind((self.host, self.port))
      listener.listen(2048)
      listener.setblocking(False)
      listener.set_inheritable(False)
      return listener
    except BaseException:
      listener.close()
      raise
