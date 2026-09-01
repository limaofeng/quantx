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


def _code_audits(*, populated_digest: str = "a" * 64, populated_count: int = 1):
  empty_digest = hashlib.sha256(b"[]").hexdigest()
  populated = populated_digest if populated_count else empty_digest
  return {
    "000001.SZ": {
      "record_count": 0,
      "source_sha256": empty_digest,
      "persisted_sha256": empty_digest,
    },
    "600519.SH": {
      "record_count": populated_count,
      "source_sha256": populated,
      "persisted_sha256": populated,
    },
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
        "audit_schema_version": 2,
        "stock_count": 2,
        "stock_codes_sha256": durable_agent_flows.divid_factor_codes_sha256(
          ["000001.SZ", "600519.SH"]
        ),
        "prior_count": 0,
        "inserted_count": 1,
        "deleted_count": 0,
        "verified_count": 1,
        "start_ex_date": "20200313",
        "end_ex_date": "20260729",
        "source_sha256": "a" * 64,
        "persisted_sha256": "a" * 64,
        "code_audits": _code_audits(),
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
        "audit_schema_version": 2,
        "stock_count": 2,
        "stock_codes_sha256": durable_agent_flows.divid_factor_codes_sha256(
          ["000001.SZ", "600519.SH"]
        ),
        "prior_count": 2,
        "inserted_count": 0,
        "deleted_count": 2,
        "verified_count": 0,
        "start_ex_date": "20200313",
        "end_ex_date": "20260729",
        "source_sha256": "a" * 64,
        "persisted_sha256": "a" * 64,
        "code_audits": _code_audits(populated_count=0),
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


@pytest.mark.asyncio
async def test_divid_factor_ingestion_rejects_audit_before_completed(
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

  class FakeService:
    async def replace_batch_divid_factors(self, _frames, **_kwargs):
      return {
        "audit_schema_version": 2,
        "stock_count": 2,
        "stock_codes_sha256": durable_agent_flows.divid_factor_codes_sha256(
          ["000001.SZ", "600519.SH"]
        ),
        "prior_count": 0,
        "deleted_count": 0,
        "inserted_count": 1,
        "verified_count": 1,
        "start_ex_date": "20200313",
        "end_ex_date": "20260729",
        "source_sha256": "a" * 64,
        "persisted_sha256": "b" * 64,
        "code_audits": _code_audits(),
      }

  monkeypatch.setattr(durable_agent_flows, "DividFactorService", FakeService)

  with pytest.raises(RuntimeError, match="content digest mismatch"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.asyncio
async def test_divid_factor_ingestion_rejects_incomplete_per_code_audit(
  tmp_path,
  monkeypatch,
):
  store = FakeStore(
    request={"expected_chunks": 1, "request_payload": _payload()},
    manifest=[_transfer(tmp_path, [])],
  )

  class FakeService:
    async def replace_batch_divid_factors(self, _frames, **_kwargs):
      return {
        "audit_schema_version": 2,
        "stock_count": 2,
        "stock_codes_sha256": durable_agent_flows.divid_factor_codes_sha256(
          ["000001.SZ", "600519.SH"]
        ),
        "prior_count": 0,
        "deleted_count": 0,
        "inserted_count": 0,
        "verified_count": 0,
        "start_ex_date": "20200313",
        "end_ex_date": "20260729",
        "source_sha256": "a" * 64,
        "persisted_sha256": "a" * 64,
        "code_audits": {
          "600519.SH": {
            "record_count": 0,
            "source_sha256": hashlib.sha256(b"[]").hexdigest(),
            "persisted_sha256": hashlib.sha256(b"[]").hexdigest(),
          }
        },
      }

  monkeypatch.setattr(durable_agent_flows, "DividFactorService", FakeService)

  with pytest.raises(RuntimeError, match="per-code scope mismatch"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


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
