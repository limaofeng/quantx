from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.endpoints import (
  configured_tls_context,
  httpx_verify,
  masked_account_id,
  normalize_api_url,
  websocket_url,
)
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime


@pytest.mark.parametrize(
  "value",
  (
    "ftp://api.example.test",
    "https://user@api.example.test",
    "http://user@api.example.test",
    "https://api.example.test/graphql",
    "http://api.example.test/graphql",
    "https://api.example.test?fallback=http://local",
    "https://api.example.test/#fragment",
  ),
)
def test_api_url_rejects_noncanonical_or_fallback_endpoints(value: str) -> None:
  with pytest.raises(ValueError):
    normalize_api_url(value)


@pytest.mark.parametrize(
  ("value", "api_url", "websocket_root"),
  (
    (
      "HTTPS://API.Example.Test:8443/",
      "https://api.example.test:8443",
      "wss://api.example.test:8443",
    ),
    (
      "HTTP://API.Example.Test:8080/",
      "http://api.example.test:8080",
      "ws://api.example.test:8080",
    ),
  ),
)
def test_api_url_derives_websockets_from_the_exact_same_scheme_and_authority(
  value: str,
  api_url: str,
  websocket_root: str,
) -> None:
  assert normalize_api_url(value) == api_url
  assert websocket_url(api_url) == f"{websocket_root}/ws/agent"
  assert (
    websocket_url(api_url, "/ws/agent/market") == f"{websocket_root}/ws/agent/market"
  )


def test_https_uses_explicit_ca_without_enabling_environment_proxy(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  certificate = tmp_path / "macos-caddy-root.crt"
  certificate.write_text("test certificate", encoding="utf-8")
  observed: dict[str, object] = {}
  tls_context = object()

  def create_default_context(*, cafile: str):
    observed["cafile"] = cafile
    return tls_context

  monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
  monkeypatch.setattr(
    "quantx_qmt_agent.endpoints.ssl.create_default_context",
    create_default_context,
  )

  assert configured_tls_context() is tls_context
  assert httpx_verify("https://api.example.test") is tls_context
  assert httpx_verify("http://api.example.test") is True
  assert observed["cafile"] == str(certificate)


def test_invalid_explicit_ca_file_fails_closed(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-root.crt"))

  with pytest.raises(ValueError, match="SSL_CERT_FILE"):
    configured_tls_context()


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
  tls_context = object()
  monkeypatch.setattr(
    "quantx_qmt_agent.runtime.configured_tls_context",
    lambda: tls_context,
  )
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
  assert connect_kwargs["ssl"] is tls_context
  redirect = RuntimeError("redirect")
  assert connections[0].process_redirect(redirect) is redirect
  runtime._whole_market_encode_executor.shutdown(wait=False, cancel_futures=True)
