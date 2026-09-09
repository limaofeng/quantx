"""HTTP mounting and authentication boundaries for the independent historical uploader."""

import hashlib
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from quantx_api.agent_api import agent_router as control_router
from quantx_infrastructure.auth.tokens import issue_access_token
from quantx_market_data import agent_upload
from quantx_market_data.api import create_app

from tests.api.unit.auth.test_agent_service import _settings


@pytest.mark.parametrize(
  "scope,expected", [(None, 401), ({"agent:history"}, 404), ({"market-data:read"}, 401)]
)
async def test_only_history_credentials_reach_upload_storage(
  monkeypatch, scope, expected
):
  settings = _settings()
  monkeypatch.setattr(agent_upload, "settings", settings)

  class Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      pass

    async def execute(self, _):
      return SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(
          id="device", user_id="user", revoked_at=None
        )
      )

    async def scalar(self, _):
      return None

  monkeypatch.setattr(agent_upload, "AsyncSessionLocal", Session)
  token, _ = issue_access_token("user", "device", settings, scopes=scope)
  app = create_app(store=object(), reader=object(), token="internal")
  path = "/agent/market-data/11111111-1111-4111-8111-111111111111/chunks/0"
  assert not any(
    route.path.startswith("/agent/market-data/") for route in control_router.routes
  )
  async with AsyncClient(
    transport=ASGITransport(app), base_url="http://local"
  ) as client:
    result = await client.put(
      path,
      content=b"chunk",
      headers={
        "Authorization": "Bearer " + token,
        "Content-Encoding": "gzip",
        "X-Content-SHA256": hashlib.sha256(b"chunk").hexdigest(),
        "X-Record-Count": "1",
        "X-Total-Chunks": "1",
      },
    )
  assert result.status_code == expected


async def test_staging_cleanup_requires_live_owner_before_touching_files(
  monkeypatch, tmp_path
):
  from quantx_infrastructure.services import market_data_staging_cleanup as cleanup

  retained = tmp_path / "retained"
  retained.write_text("evidence")
  monkeypatch.setattr(cleanup, "MARKET_DATA_ROOT", tmp_path)

  async def lost(owner, connection=None):
    raise RuntimeError("lost lease")

  monkeypatch.setattr(cleanup, "_check_owner", lost)
  with pytest.raises(RuntimeError, match="lost lease"):
    await cleanup.sweep_market_data_staging_once(owner=object())
  assert retained.read_text() == "evidence"


async def test_cleanup_cancellation_joins_file_operation():
  import asyncio
  import threading

  from quantx_infrastructure.services.market_data_staging_cleanup import _joined_thread

  entered, release = threading.Event(), threading.Event()

  def remove():
    entered.set()
    release.wait(5)

  task = asyncio.create_task(_joined_thread(remove))
  try:
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
  finally:
    release.set()
  with pytest.raises(asyncio.CancelledError):
    await task
