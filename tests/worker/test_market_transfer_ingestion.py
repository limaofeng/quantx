import gzip
import hashlib
import json

import pytest
from quantx_worker.prefector.flows import durable_agent_flows


class FakeStore:
  def __init__(self, *, request, manifest):
    self.request = request
    self.manifest = manifest

  async def market_data_request(self, request_id):
    assert request_id == "request-1"
    return self.request

  async def market_data_transfers(self, request_id):
    assert request_id == "request-1"
    return self.manifest


def _transfer(tmp_path, records):
  compressed = gzip.compress(json.dumps(records).encode("utf-8"))
  path = tmp_path / "00000000.json.gz"
  path.write_bytes(compressed)
  return {
    "chunk_index": 0,
    "checksum_sha256": hashlib.sha256(compressed).hexdigest(),
    "record_count": len(records),
    "compressed": True,
    "storage_reference": str(path),
  }


@pytest.mark.asyncio
async def test_uploaded_bars_are_validated_and_saved(tmp_path, monkeypatch):
  records = [
    {
      "code": "600000.SH",
      "period": "1d",
      "time": 1_700_000_000_000,
      "open": 10,
      "high": 11,
      "low": 9,
      "close": 10.5,
      "volume": 100,
      "amount": 1050,
    }
  ]
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {"operation": "bars"},
    },
    manifest=[_transfer(tmp_path, records)],
  )
  calls = []

  async def fake_save_market_data(*, period, market_data):
    calls.append((period, market_data))
    return {
      "saved_count": len(market_data["600000.SH"]),
      "status": "success",
    }

  monkeypatch.setattr(
    durable_agent_flows,
    "save_market_data",
    fake_save_market_data,
  )

  result = await durable_agent_flows._ingest_uploaded_request(
    store,
    "request-1",
  )

  assert result == {
    "operation": "bars",
    "records_received": 1,
    "records_saved": 1,
  }
  assert calls[0][0] == "1d"
  assert calls[0][1]["600000.SH"].iloc[0]["close"] == 10.5


@pytest.mark.asyncio
async def test_uploaded_chunk_checksum_mismatch_fails(tmp_path):
  transfer = _transfer(tmp_path, [])
  transfer["checksum_sha256"] = "0" * 64
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {"operation": "bars"},
    },
    manifest=[transfer],
  )

  with pytest.raises(RuntimeError, match="checksum mismatch"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")
