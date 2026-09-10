"""Migration metadata reads never submit source jobs or follow redirects."""

import importlib.util
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize("status", [200, 302, 401])
async def test_cli_uses_one_authenticated_get_without_redirects(monkeypatch, status):
  root = Path(__file__).resolve().parents[2]
  monkeypatch.syspath_prepend(str(root / "ops"))
  spec = importlib.util.spec_from_file_location(
    "legacy_import_cli", root / "ops/migrate_legacy_market_data_import.py"
  )
  cli = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(cli)
  monkeypatch.setenv("QUANTX_MARKET_DATA_URL", "http://producer")
  monkeypatch.setenv("QUANTX_MARKET_DATA_TOKEN", "test-migration-token")
  seen = []

  def serve(request):
    seen.append(request)
    return httpx.Response(
      status, json={"id": "a" * 64}, headers={"location": "http://other-host/forbidden"}
    )

  client = httpx.AsyncClient
  monkeypatch.setattr(
    httpx,
    "AsyncClient",
    lambda **kwargs: client(transport=httpx.MockTransport(serve), **kwargs),
  )
  if status == 200:
    assert await cli.producer("a" * 64) == {"id": "a" * 64}
  else:
    with pytest.raises(httpx.HTTPStatusError):
      await cli.producer("a" * 64)
  assert len(seen) == 1
  assert seen[0].method == "GET"
  assert seen[0].url.path == "/market-data/v1/history/" + "a" * 64
  assert seen[0].headers["Authorization"] == "Bearer test-migration-token"
