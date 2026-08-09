from __future__ import annotations

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


def _payload():
  return {
    "operation": "divid_factors",
    "stock_list": ["600519.SH", "000001.SZ"],
    "start_time": "20200313",
    "end_time": "20260729",
  }


@pytest.mark.asyncio
async def test_uploaded_divid_factors_are_replaced_and_audited(
  tmp_path,
  monkeypatch,
):
  records = [
    {
      "code": "600519.SH",
      "ex_date": "20200624",
      "time": 1_592_928_000_000,
      "interest": 17.025,
      "stockBonus": 0,
      "stockGift": 0,
      "allotNum": 0,
      "allotPrice": 0,
      "gugai": 0,
      "dr": 1.011677,
    }
  ]
  store = FakeStore(
    request={"expected_chunks": 1, "request_payload": _payload()},
    manifest=[_transfer(tmp_path, records)],
  )
  calls = []

  class FakeService:
    async def replace_batch_divid_factors(self, frames, **kwargs):
      calls.append((frames, kwargs))
      return {
        "inserted_count": 1,
        "deleted_count": 0,
        "verified_count": 1,
      }

  monkeypatch.setattr(
    durable_agent_flows,
    "DividFactorService",
    FakeService,
  )

  result = await durable_agent_flows._ingest_uploaded_request(
    store,
    "request-1",
  )

  assert result["operation"] == "divid_factors"
  assert result["records_received"] == 1
  assert result["records_saved"] == 1
  assert result["replacement_audit"]["verified_count"] == 1
  frames, kwargs = calls[0]
  assert list(frames) == ["600519.SH"]
  assert frames["600519.SH"].index.tolist() == ["20200624"]
  assert kwargs == {
    "stock_codes": ["000001.SZ", "600519.SH"],
    "start_ex_date": "20200313",
    "end_ex_date": "20260729",
  }


@pytest.mark.asyncio
async def test_empty_divid_factor_result_still_clears_exact_window(
  tmp_path,
  monkeypatch,
):
  store = FakeStore(
    request={"expected_chunks": 1, "request_payload": _payload()},
    manifest=[_transfer(tmp_path, [])],
  )
  calls = []

  class FakeService:
    async def replace_batch_divid_factors(self, frames, **kwargs):
      calls.append((frames, kwargs))
      return {
        "inserted_count": 0,
        "deleted_count": 2,
        "verified_count": 0,
      }

  monkeypatch.setattr(
    durable_agent_flows,
    "DividFactorService",
    FakeService,
  )

  result = await durable_agent_flows._ingest_uploaded_request(
    store,
    "request-1",
  )

  assert result["records_saved"] == 0
  assert calls[0][0] == {}
  assert calls[0][1]["stock_codes"] == ["000001.SZ", "600519.SH"]


def test_divid_factor_transfer_rejects_unrequested_code():
  with pytest.raises(RuntimeError, match="unexpected"):
    durable_agent_flows._normalize_divid_factor_records(
      [
        {
          "code": "000002.SZ",
          "ex_date": "20200624",
          "time": 1_592_928_000_000,
          "interest": 0,
          "stockBonus": 0,
          "stockGift": 0,
          "allotNum": 0,
          "allotPrice": 0,
          "gugai": 0,
          "dr": 1,
        }
      ],
      _payload(),
    )
