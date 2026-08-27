from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.endpoints import (
  masked_account_id,
  normalize_api_url,
  require_secure_api_url,
  websocket_url,
)
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime


@pytest.mark.parametrize(
  "value",
  (
    "ftp://api.example.test",
    "https://user@api.example.test",
    "https://api.example.test/graphql",
    "https://api.example.test?fallback=http://local",
    "https://api.example.test/#fragment",
  ),
)
def test_api_url_rejects_noncanonical_or_fallback_endpoints(value: str) -> None:
  with pytest.raises(ValueError):
    normalize_api_url(value)


def test_secure_api_url_derives_wss_from_the_same_authority() -> None:
  assert require_secure_api_url("HTTPS://API.Example.Test:8443/") == (
    "https://api.example.test:8443"
  )
  assert (
    websocket_url(
      "https://api.example.test:8443",
      "/ws/agent/market",
    )
    == "wss://api.example.test:8443/ws/agent/market"
  )
  with pytest.raises(ValueError, match="https"):
    require_secure_api_url("http://api.example.test")


@pytest.mark.parametrize(
  ("account_id", "masked"),
  (
    ("1", "*"),
    ("1234", "****"),
    ("12345", "***2345"),
    ("broker-account-5678", "***5678"),
  ),
)
def test_account_id_mask_never_exposes_more_than_the_last_four_characters(
  account_id: str,
  masked: str,
) -> None:
  assert masked_account_id(account_id) == masked


@pytest.mark.asyncio
async def test_native_broker_initializes_only_after_control_authentication(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  events: list[str] = []
  connect_kwargs: dict[str, object] = {}
  connections = []
  sent_envelopes: list[AgentEnvelope] = []

  class Broker:
    pass

  def broker_factory():
    events.append("broker")
    return Broker()

  journal = LocalJournal(tmp_path / "journal.sqlite3")
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="https://api.example.test",
      device_id="device-1",
    ),
    device_secret="secret",
    mode="data-only",
    allowed_accounts=set(),
    broker_factory=broker_factory,
    journal=journal,
    market_spool_base_directory=tmp_path,
  )
  assert events == []

  async def issue_token():
    events.append("token")
    return "access-token", datetime.now(timezone.utc) + timedelta(minutes=10)

  class Socket:
    async def send(self, value):
      events.append("auth-send")
      sent_envelopes.append(AgentEnvelope.model_validate_json(value))

    async def recv(self):
      events.append("auth-result")
      return AgentEnvelope(
        message_type=AgentMessageType.AUTH_RESULT,
        payload={"accepted": True},
      ).model_dump_json()

  class Connection:
    async def __aenter__(self):
      return Socket()

    async def __aexit__(self, *_args):
      return False

  def connect(*_args, **kwargs):
    connect_kwargs.update(kwargs)
    connection = Connection()
    connections.append(connection)
    return connection

  monkeypatch.setattr(runtime, "_issue_token", issue_token)
  monkeypatch.setattr(
    "quantx_qmt_agent.runtime.websockets.connect",
    connect,
  )

  async def no_op(*_args, **_kwargs):
    return None

  monkeypatch.setattr(runtime, "_ensure_trading_ready", no_op)
  monkeypatch.setattr(runtime, "_queue_full_snapshot", no_op)
  monkeypatch.setattr(runtime, "_heartbeat_checkpoint", no_op)
  monkeypatch.setattr(runtime, "_supervise_session_tasks", no_op)

  await runtime._run_session()

  assert events[:4] == ["token", "auth-send", "auth-result", "broker"]
  assert sent_envelopes[0].payload["capabilities"] == [
    "market-data",
    "divid-factors",
    "financial-data-v1",
    "data-only",
  ]
  assert connect_kwargs["proxy"] is None
  redirect = RuntimeError("redirect")
  assert connections[0].process_redirect(redirect) is redirect
  runtime._whole_market_encode_executor.shutdown(wait=False, cancel_futures=True)
