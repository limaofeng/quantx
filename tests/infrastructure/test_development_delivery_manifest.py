"""Real catalog recovery and bounded remote metadata; no remote services."""
# ruff: noqa: F811

import copy
import hashlib
import json
from datetime import date

import httpx
import pytest
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.services import development_delivery_manifest as delivery
from quantx_infrastructure.services import development_history_import as importer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.infrastructure.test_development_download_budget import install_budget_schema
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)

REQUEST = HistoryPartitionRequest(
  instrument="600000.SH", period="1m", trading_date=date(2026, 9, 7)
)


def manifest():
  result = {
    "version": 1,
    "payload": REQUEST.agent_payload(),
    "chunks": [
      {
        "chunk_index": 0,
        "checksum_sha256": "a" * 64,
        "record_count": 2,
        "compressed": True,
        "compressed_bytes": 20,
      }
    ],
    "reference": {},
    "source_request_id": "source-request",
    "coverage": "SOURCE_VERIFIED",
    "rows": 1,
  }
  version(result)
  return result


def version(value):
  value["data_version"] = hashlib.sha256(
    json.dumps(
      {"chunks": value["chunks"], "reference": value["reference"]},
      sort_keys=True,
    ).encode()
  ).hexdigest()


@pytest.mark.parametrize(
  "field,value",
  [
    ("chunk_index", True),
    ("chunk_index", 1),
    ("compressed", False),
    ("compressed_bytes", 0),
    ("compressed_bytes", 32 * 1024 * 1024 + 1),
    ("record_count", 3),
    ("checksum_sha256", "../archive"),
  ],
)
def test_invalid_chunk_rejected_even_with_matching_version(field, value):
  item = manifest()
  item["chunks"][0][field] = value
  version(item)
  with pytest.raises(ValueError, match="DELIVERY_"):
    delivery.validate_delivery_manifest(item, REQUEST)


def test_manifest_scope_and_cumulative_budget():
  item = manifest()
  delivery.validate_delivery_manifest(item, REQUEST)
  item["payload"]["stock_list"] = ["000001.SZ"]
  with pytest.raises(ValueError, match="MANIFEST_INVALID"):
    delivery.validate_delivery_manifest(item, REQUEST)
  item = manifest()
  item["chunks"] = [
    dict(item["chunks"][0], chunk_index=i, compressed_bytes=32 * 1024 * 1024)
    for i in range(9)
  ]
  item["rows"] = 17
  version(item)
  with pytest.raises(ValueError, match="BUDGET"):
    delivery.validate_delivery_manifest(item, REQUEST)


async def test_metadata_limit_applies_to_decoded_stream(monkeypatch):
  monkeypatch.setattr(delivery, "MAX_DELIVERY_METADATA_BYTES", 128)
  transport = httpx.MockTransport(
    lambda request: httpx.Response(200, content=b" " * 129)
  )
  async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
    with pytest.raises(ValueError, match="METADATA_BUDGET"):
      await delivery.read_delivery_metadata(client, "GET", "/manifest")


def test_current_export_producer_satisfies_manifest_contract(monkeypatch, tmp_path):
  from quantx_worker.prefector.flows.development_data_export_flow import (
    partition_records,
    publish,
  )

  from tests.worker.test_development_data_export import bar

  monkeypatch.setenv("QUANTX_DATA_EXPORT_ROOT", str(tmp_path))
  records = partition_records([[bar()]], REQUEST)
  item = manifest()
  item["chunks"] = publish(records)
  item["rows"] = len(records) - 1
  version(item)
  delivery.validate_delivery_manifest(item, REQUEST)


async def test_download_failure_pins_version_and_restart_rejects_change(
  durable_store,
  monkeypatch,
  tmp_path,
):
  store, _ = durable_store
  identity = "b" * 64
  original = manifest()
  remote = copy.deepcopy(original)
  calls = []
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      CREATE TEMP TABLE development_data_export (
        id varchar(64) PRIMARY KEY, request json, manifest json, updated_at timestamptz, error text
      )
    """)
    )
    await connection.execute(
      text("""
      INSERT INTO development_data_export(id, request) VALUES (:id, CAST(:request AS JSON))
    """),
      {"id": identity, "request": REQUEST.model_dump_json()},
    )

  await install_budget_schema(store.engine)

  async def submit(request):
    return identity

  async def get_export(identity):
    return {"state": "QUEUED"}

  def handler(request):
    calls.append(request.url.path)
    if request.method == "POST":
      return httpx.Response(200, json={"id": identity})
    if "/chunks/" in request.url.path:
      raise httpx.ConnectError("offline", request=request)
    return httpx.Response(
      200, json={"id": identity, "state": "READY", "manifest": remote}
    )

  client_class = httpx.AsyncClient
  monkeypatch.setattr(importer, "AsyncSessionLocal", async_sessionmaker(store.engine))
  monkeypatch.setattr(importer, "submit", submit)
  monkeypatch.setattr(importer, "get_export", get_export)
  monkeypatch.setattr(
    importer.httpx,
    "AsyncClient",
    lambda **kwargs: client_class(
      **kwargs,
      transport=httpx.MockTransport(handler),
    ),
  )
  monkeypatch.setenv("ENV", "development")
  monkeypatch.setenv("QUANTX_MARKET_DATA_URL", "http://test")
  monkeypatch.setenv("QUANTX_MARKET_DATA_TOKEN", "test")
  monkeypatch.setenv("QUANTX_DATA_EXPORT_ROOT", str(tmp_path))
  for _ in range(2):
    with pytest.raises(httpx.ConnectError):
      await importer._import_partition_owned(REQUEST)
    async with store.engine.connect() as connection:
      assert (
        await connection.scalar(text("SELECT manifest FROM development_data_export"))
        == original
      )
  assert not list(tmp_path.iterdir())
  remote["reference"] = {"changed": True}
  version(remote)
  with pytest.raises(ValueError, match="DELIVERY_VERSION_CONFLICT"):
    await importer._import_partition_owned(REQUEST)
  assert sum("/chunks/" in path for path in calls) == 2
  async with store.engine.connect() as connection:
    assert (
      await connection.scalar(text("SELECT manifest FROM development_data_export"))
      == original
    )
