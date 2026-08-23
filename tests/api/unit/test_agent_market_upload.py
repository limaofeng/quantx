import hashlib
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from quantx_api import agent_api
from quantx_api.agent_api import _matches_sha256_digest, _read_limited_body


class FakeRequest:
  def __init__(self, chunks: list[bytes], content_length: str = "") -> None:
    self.headers = {"content-length": content_length} if content_length else {}
    self.chunks = chunks

  async def stream(self):
    for chunk in self.chunks:
      yield chunk


class _RequeueSession:
  def __init__(self) -> None:
    self.statement = None
    self.committed = False

  async def __aenter__(self):
    return self

  async def __aexit__(self, exc_type, exc, traceback):
    return None

  async def execute(self, statement):
    self.statement = statement

  async def commit(self) -> None:
    self.committed = True


@pytest.mark.asyncio
async def test_limited_market_body_accepts_payload_at_limit() -> None:
  request = FakeRequest([b"ab", b"cd"], content_length="4")

  assert await _read_limited_body(request, limit=4) == b"abcd"


@pytest.mark.asyncio
async def test_limited_market_body_rejects_declared_oversize() -> None:
  request = FakeRequest([], content_length="5")

  with pytest.raises(HTTPException) as error:
    await _read_limited_body(request, limit=4)

  assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_limited_market_body_rejects_streamed_oversize() -> None:
  request = FakeRequest([b"abc", b"de"])

  with pytest.raises(HTTPException) as error:
    await _read_limited_body(request, limit=4)

  assert error.value.status_code == 413


def test_market_body_sha256_uses_constant_time_comparison() -> None:
  raw = b"market-data"
  digest = hashlib.sha256(raw).hexdigest()

  assert _matches_sha256_digest(digest, digest.upper()) is True
  assert _matches_sha256_digest(digest, "0" * 64) is False
  assert _matches_sha256_digest(digest, "") is False


@pytest.mark.asyncio
async def test_requeue_uses_expired_delivery_lease_to_preserve_active_uploads(
  monkeypatch,
) -> None:
  session = _RequeueSession()
  monkeypatch.setattr(agent_api, "AsyncSessionLocal", lambda: session)
  now = datetime(2026, 8, 24, 8, 0, 0)

  await agent_api._requeue_incomplete_market_requests("device-1", now=now)

  sql = str(session.statement)
  parameters = session.statement.compile().params
  assert sql.startswith("UPDATE market_data_request")
  assert "market_data_request.device_id =" in sql
  assert "market_data_request.status IN" in sql
  assert "market_data_request.updated_at <" in sql
  assert "QUEUED" in parameters.values()
  assert "device-1" in parameters.values()
  assert ["DELIVERED", "RECEIVING"] in parameters.values()
  assert now in parameters.values()
  assert (
    now - timedelta(seconds=agent_api.MARKET_DATA_RECONNECT_STALE_SECONDS)
  ) in parameters.values()
  assert session.committed is True
