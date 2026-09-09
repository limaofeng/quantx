"""History credentials renew independently of control transport and never fall back."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import quantx_qmt_agent.runtime as runtime_module


async def test_history_token_renewal_is_serialized_and_preserves_control():
  runtime = runtime_module.AgentRuntime.__new__(runtime_module.AgentRuntime)
  runtime._access_token = "control-token"
  runtime._issue_token = AsyncMock(
    return_value=("history-token", datetime.now(timezone.utc) + timedelta(minutes=5))
  )
  tokens = await asyncio.gather(*(runtime._history_access_token() for _ in range(5)))
  assert tokens == ["history-token"] * 5
  runtime._issue_token.assert_awaited_once_with(history=True)
  assert runtime._access_token == "control-token"
  runtime._history_token_expiry = datetime.now(timezone.utc) - timedelta(seconds=1)
  runtime._issue_token.side_effect = httpx.ConnectError("authentication unavailable")
  with pytest.raises(httpx.ConnectError):
    await runtime._history_access_token()
  assert runtime._access_token == "control-token"


async def test_history_issuance_uses_separate_endpoint(monkeypatch):
  runtime = runtime_module.AgentRuntime.__new__(runtime_module.AgentRuntime)
  runtime.configuration = SimpleNamespace(
    api_url="http://local.test", device_id="device"
  )
  runtime.device_secret = "test-device-secret"
  seen = []

  def respond(request):
    seen.append(request)
    return httpx.Response(
      200,
      json={
        "accessToken": "history-token",
        "accessTokenExpiresAt": "2030-01-01T00:00:00Z",
      },
    )

  real_client = httpx.AsyncClient
  monkeypatch.setattr(
    runtime_module.httpx,
    "AsyncClient",
    lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
  )
  token, _ = await runtime._issue_token(history=True)
  assert token == "history-token"
  assert seen[0].url.path == "/auth/agent/history-token"
  assert seen[0].headers.get("authorization") is None
