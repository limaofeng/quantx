from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest


def _load_module():
  script_path = (
    Path(__file__).parents[2]
    / "apps"
    / "worker"
    / "scripts"
    / "backfill_daily_market_data.py"
  )
  spec = importlib.util.spec_from_file_location(
    "backfill_daily_market_data",
    script_path,
  )
  assert spec and spec.loader
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _agent_store(status: str):
  class Store:
    async def component_status(self, prefix):
      assert prefix == "qmt-agent:"
      return [
        {
          "status": status,
          "instance_id": "device-1",
          "updated_at": datetime.now(timezone.utc),
          "details": {
            "capabilities": ["market-data", "data-only"],
          },
        }
      ]

    async def close(self):
      return None

  return Store


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["READY", "RECONCILING"])
async def test_data_only_readiness_accepts_fresh_market_data_status(
  monkeypatch,
  status,
):
  module = _load_module()
  monkeypatch.setattr(module, "DurableRuntimeStore", _agent_store(status))

  assert await module.ensure_data_only_agent_ready() == "device-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "status",
  ["XTDATA_UNAVAILABLE", "EMERGENCY_STOP"],
)
async def test_data_only_readiness_rejects_unavailable_or_stopped_agent(
  monkeypatch,
  status,
):
  module = _load_module()
  monkeypatch.setattr(module, "DurableRuntimeStore", _agent_store(status))

  with pytest.raises(RuntimeError, match="没有新鲜"):
    await module.ensure_data_only_agent_ready()


def test_annual_windows_are_inclusive_and_non_overlapping():
  module = _load_module()

  assert module.annual_windows(
    date(2020, 3, 13),
    date(2022, 2, 2),
  ) == [
    (date(2020, 3, 13), date(2020, 12, 31)),
    (date(2021, 1, 1), date(2021, 12, 31)),
    (date(2022, 1, 1), date(2022, 2, 2)),
  ]


def test_calendar_month_windows_are_inclusive_and_non_overlapping():
  module = _load_module()

  assert module.calendar_month_windows(
    date(2024, 1, 30),
    date(2024, 3, 2),
  ) == [
    (date(2024, 1, 30), date(2024, 1, 31)),
    (date(2024, 2, 1), date(2024, 2, 29)),
    (date(2024, 3, 1), date(2024, 3, 2)),
  ]


def test_build_jobs_batches_stocks_and_separates_benchmark():
  module = _load_module()

  jobs = module.build_jobs(
    codes=["600000.SH", "000001.SZ", "000002.SZ"],
    start=date(2025, 1, 1),
    end=date(2026, 1, 2),
    batch_size=2,
  )

  assert len(jobs) == 6
  assert jobs[0]["codes"] == ["000001.SZ", "000002.SZ"]
  assert jobs[1]["codes"] == ["600000.SH"]
  assert jobs[2]["codes"] == ["000300.SH"]
  assert jobs[3]["start_date"] == "20260101"
  assert jobs[5]["end_date"] == "20260102"


def test_build_jobs_excludes_pre_listing_and_post_expiry_windows():
  module = _load_module()

  jobs = module.build_jobs(
    instruments=[
      {
        "code": "000001.SZ",
        "open_date": "1991-04-03",
        "expire_date": None,
      },
      {
        "code": "001999.SZ",
        "open_date": "2026-01-02",
        "expire_date": None,
      },
      {
        "code": "600999.SH",
        "open_date": "2000-01-01",
        "expire_date": "2025-06-30",
      },
    ],
    start=date(2025, 1, 1),
    end=date(2026, 12, 31),
    batch_size=10,
  )

  stock_jobs = [job for job in jobs if job["kind"] == "stocks"]
  assert stock_jobs[0]["codes"] == ["000001.SZ", "600999.SH"]
  assert stock_jobs[1]["codes"] == ["000001.SZ", "001999.SZ"]


def test_split_job_changes_payload_by_codes_then_dates():
  module = _load_module()
  parent = {
    "id": "parent",
    "kind": "stocks",
    "codes": ["000001.SZ", "000002.SZ", "600000.SH"],
    "start_date": "20250101",
    "end_date": "20251231",
    "attempt": 0,
  }

  children = module.split_job(parent)

  assert [child["codes"] for child in children] == [
    ["000001.SZ"],
    ["000002.SZ", "600000.SH"],
  ]
  assert all(
    module.request_payload(child) != module.request_payload(parent)
    for child in children
  )

  single_code = {
    **parent,
    "codes": ["000001.SZ"],
    "start_date": "20250101",
    "end_date": "20250104",
  }
  date_children = module.split_job(single_code)
  assert [
    (child["start_date"], child["end_date"])
    for child in date_children
  ] == [
    ("20250101", "20250102"),
    ("20250103", "20250104"),
  ]


def test_request_idempotency_key_ignores_mapping_order():
  module = _load_module()
  first = {
    "operation": "bars",
    "stock_list": ["000001.SZ"],
    "periods": ["1d"],
  }
  second = {
    "periods": ["1d"],
    "stock_list": ["000001.SZ"],
    "operation": "bars",
  }

  assert module.request_idempotency_key(first) == (
    module.request_idempotency_key(second)
  )


def test_prefect_client_recovers_flow_run_by_idempotency_key():
  module = _load_module()
  requests: list[httpx.Request] = []

  def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    return httpx.Response(
      200,
      json=[
        {
          "id": "a25b1774-861f-4010-a8ed-b554b36fa95d",
          "idempotency_key": "campaign:job",
        }
      ],
    )

  client = object.__new__(module.PrefectClient)
  client.deployment_id = "1fe80113-5df8-4312-9709-5d9b1b9cff92"
  client.client = httpx.Client(
    base_url="http://prefect.test/api",
    transport=httpx.MockTransport(handler),
  )
  try:
    result = client.flow_run_by_idempotency_key("campaign:job")
  finally:
    client.close()

  assert result is not None
  assert result["id"] == "a25b1774-861f-4010-a8ed-b554b36fa95d"
  assert len(requests) == 1
  assert requests[0].url.path == "/api/flow_runs/filter"
  payload = json.loads(requests[0].content)
  assert payload["flow_runs"]["idempotency_key"]["any_"] == [
    "campaign:job"
  ]
  assert payload["deployments"]["id"]["any_"] == [
    "1fe80113-5df8-4312-9709-5d9b1b9cff92"
  ]


def test_prefect_submit_confirms_provisional_id_by_idempotency_key():
  module = _load_module()
  requests: list[httpx.Request] = []

  def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    if request.url.path.endswith("/create_flow_run"):
      return httpx.Response(
        201,
        json={"id": "11111111-1111-4111-8111-111111111111"},
      )
    payload = json.loads(request.content)
    key = payload["flow_runs"]["idempotency_key"]["any_"][0]
    return httpx.Response(
      200,
      json=[
        {
          "id": "22222222-2222-4222-8222-222222222222",
          "deployment_id": (
            "1fe80113-5df8-4312-9709-5d9b1b9cff92"
          ),
          "idempotency_key": key,
          "parameters": {
            "stock_list": ["000001.SZ"],
            "sectors": [],
            "start_time": "20250101",
            "end_time": "20251231",
            "periods": ["1d"],
            "skip_download": False,
            "compute_daily_signals": False,
            "agent_device_id": "device-1",
          },
        }
      ],
    )

  client = object.__new__(module.PrefectClient)
  client.deployment_id = "1fe80113-5df8-4312-9709-5d9b1b9cff92"
  client.client = httpx.Client(
    base_url="http://prefect.test/api",
    transport=httpx.MockTransport(handler),
  )
  try:
    run_id = client.submit(
      {
        "id": "stocks-20250101-20251231-test",
        "codes": ["000001.SZ"],
        "start_date": "20250101",
        "end_date": "20251231",
      },
      agent_device_id="device-1",
      idempotency_key="campaign:job",
    )
  finally:
    client.close()

  assert run_id == "22222222-2222-4222-8222-222222222222"
  assert [request.url.path for request in requests] == [
    (
      "/api/deployments/"
      "1fe80113-5df8-4312-9709-5d9b1b9cff92/create_flow_run"
    ),
    "/api/flow_runs/filter",
  ]


def test_prefect_client_binds_exact_flow_and_deployment_identity():
  module = _load_module()
  requests: list[httpx.Request] = []

  def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    if "/deployments/name/" in request.url.path:
      return httpx.Response(
        200,
        json={
          "id": "1fe80113-5df8-4312-9709-5d9b1b9cff92",
          "name": "daily-market-data-sync",
          "flow_id": "662662b7-b003-4174-902d-4884616291d5",
          "version": "0.1.0",
          "entrypoint": module.EXPECTED_ENTRYPOINT,
          "work_pool_name": module.EXPECTED_WORK_POOL_NAME,
          "parameter_openapi_schema": {
            "properties": {
              name: {} for name in module.EXPECTED_PARAMETER_NAMES
            }
          },
        },
      )
    return httpx.Response(
      200,
      json={
        "id": "662662b7-b003-4174-902d-4884616291d5",
        "name": module.EXPECTED_FLOW_NAME,
      },
    )

  client = object.__new__(module.PrefectClient)
  client.client = httpx.Client(
    base_url="http://prefect.test/api",
    transport=httpx.MockTransport(handler),
  )
  client.deployment_name = "daily-market-data-sync"
  try:
    deployment = client._deployment()
  finally:
    client.close()

  assert deployment["entrypoint"] == module.EXPECTED_ENTRYPOINT
  assert len(requests) == 2
  assert requests[0].method == "GET"
  assert "/deployments/name/" in requests[0].url.path
  assert requests[1].url.path == (
    "/api/flows/662662b7-b003-4174-902d-4884616291d5"
  )


def test_prefect_client_rejects_wrong_entrypoint():
  module = _load_module()

  def handler(request: httpx.Request) -> httpx.Response:
    if "/deployments/name/" in request.url.path:
      return httpx.Response(
        200,
        json={
          "id": "deployment-id",
          "name": "daily-market-data-sync",
          "flow_id": "flow-id",
          "entrypoint": "wrong.py:wrong_flow",
          "work_pool_name": module.EXPECTED_WORK_POOL_NAME,
          "parameter_openapi_schema": {
            "properties": {
              name: {} for name in module.EXPECTED_PARAMETER_NAMES
            }
          },
        },
      )
    return httpx.Response(
      200,
      json={"id": "flow-id", "name": module.EXPECTED_FLOW_NAME},
    )

  client = object.__new__(module.PrefectClient)
  client.client = httpx.Client(
    base_url="http://prefect.test/api",
    transport=httpx.MockTransport(handler),
  )
  client.deployment_name = "daily-market-data-sync"
  try:
    with pytest.raises(RuntimeError, match="entrypoint"):
      client._deployment()
  finally:
    client.close()


@pytest.mark.parametrize("status", ["pending", "running"])
def test_v2_nonterminal_state_requires_manual_prefect_reconciliation(
  tmp_path,
  status,
):
  module = _load_module()
  state_path = tmp_path / "state.json"
  state_path.write_text(
    json.dumps(
      {
        "schema_version": 2,
        "jobs": [{"id": "job-1", "status": status}],
      }
    ),
    encoding="utf-8",
  )
  args = argparse.Namespace(
    state_file=str(state_path),
    prefect_api_url=module.DEFAULT_PREFECT_API_URL,
  )

  with pytest.raises(RuntimeError, match="无法证明原 Prefect API"):
    asyncio.run(module._load_or_create_state(args))


def test_outdated_completed_job_is_queued_for_read_only_verification():
  module = _load_module()
  state = {
    "jobs": [
      {
        "id": "job-1",
        "status": "completed",
        "influx_verification": {"verification_version": 1},
      }
    ]
  }

  assert module._queue_outdated_verifications(state) == 1
  job = state["jobs"][0]
  assert job["status"] == "verification_pending"
  assert job["verification_only"] is True
  assert module._next_job(state) is job


def test_expected_daily_keys_rechecks_sha_and_exact_timestamps(tmp_path):
  module = _load_module()
  records = [
    {
      "code": "000001.SZ",
      "period": "1d",
      "time": 1735660800000,
      "close": 10.0,
    },
    {
      "code": "000001.SZ",
      "period": "1d",
      "time": 1735747200000,
      "close": 10.1,
    },
  ]
  compressed = gzip.compress(
    json.dumps(records).encode("utf-8"),
    mtime=0,
  )
  path = tmp_path / "00000000.json.gz"
  path.write_bytes(compressed)
  manifest = [
    {
      "chunk_index": 0,
      "checksum_sha256": hashlib.sha256(compressed).hexdigest(),
      "record_count": 2,
      "compressed": True,
      "storage_reference": str(path),
    }
  ]

  result = module.expected_daily_keys(
    {"codes": ["000001.SZ"]},
    manifest,
  )

  assert result["record_count"] == 2
  summary = result["symbols"]["000001.SZ"]
  assert summary["row_count"] == 2
  assert summary["distinct_times"] == 2
  assert summary["min_time_ms"] == 1735660800000
  assert summary["max_time_ms"] == 1735747200000


def test_verify_influx_merges_month_windows_before_exact_key_acceptance(
  monkeypatch,
):
  module = _load_module()
  calls = []
  summaries = {
    date(2025, 1, 30): {
      "000001.SZ": {
        "row_count": 2,
        "distinct_times": 2,
        "invalid_rows": 1,
      }
    },
    date(2025, 2, 1): {
      "000001.SZ": {
        "row_count": 1,
        "distinct_times": 1,
        "invalid_rows": 0,
      },
      "600000.SH": {
        "row_count": 1,
        "distinct_times": 1,
        "invalid_rows": 0,
      },
    },
    date(2025, 3, 1): {
      "000001.SZ": {
        "row_count": 1,
        "distinct_times": 1,
        "invalid_rows": 0,
      }
    },
  }
  keys = {
    date(2025, 1, 30): {"000001.SZ": [1000, 2000]},
    date(2025, 2, 1): {
      "000001.SZ": [3000],
      "600000.SH": [4000],
    },
    date(2025, 3, 1): {"000001.SZ": [5000]},
  }

  class Repository:
    def summarize_daily_batch(
      self,
      codes,
      start,
      end,
      *,
      use_cache,
    ):
      calls.append(("summary", tuple(codes), start, end, use_cache))
      return summaries[start.date()]

    def find_daily_keys_batch(
      self,
      codes,
      start,
      end,
      *,
      use_cache,
    ):
      calls.append(("keys", tuple(codes), start, end, use_cache))
      return keys[start.date()]

  monkeypatch.setattr(module, "KLineRepository", Repository)
  expected = {
    "record_count": 5,
    "symbols": {
      "000001.SZ": {
        "row_count": 4,
        "distinct_times": 4,
        "min_time_ms": 1000,
        "max_time_ms": 5000,
        "key_sha256": module._key_digest([1000, 2000, 3000, 5000]),
      },
      "600000.SH": {
        "row_count": 1,
        "distinct_times": 1,
        "min_time_ms": 4000,
        "max_time_ms": 4000,
        "key_sha256": module._key_digest([4000]),
      },
    },
  }

  result = module.verify_influx(
    {
      "codes": ["000001.SZ", "600000.SH"],
      "start_date": "20250130",
      "end_date": "20250302",
    },
    expected,
  )

  assert result["ok"] is True
  assert result["verification_version"] == 2
  assert result["query_window"] == "calendar_month"
  assert result["query_window_count"] == 3
  assert result["rows"] == 5
  assert result["duplicate_rows"] == 0
  assert result["invalid_rows"] == 1
  assert result["quality_warning"] is not None
  assert [(item[0], item[2].date(), item[3].date()) for item in calls] == [
    ("summary", date(2025, 1, 30), date(2025, 1, 31)),
    ("keys", date(2025, 1, 30), date(2025, 1, 31)),
    ("summary", date(2025, 2, 1), date(2025, 2, 28)),
    ("keys", date(2025, 2, 1), date(2025, 2, 28)),
    ("summary", date(2025, 3, 1), date(2025, 3, 2)),
    ("keys", date(2025, 3, 1), date(2025, 3, 2)),
  ]
  assert all(item[4] is False for item in calls)


def test_verify_influx_preserves_duplicate_missing_and_unexpected_failures(
  monkeypatch,
):
  module = _load_module()

  class Repository:
    def summarize_daily_batch(self, *_args, **_kwargs):
      return {
        "000001.SZ": {
          "row_count": 2,
          "distinct_times": 1,
          "invalid_rows": 1,
        },
        "300001.SZ": {
          "row_count": 1,
          "distinct_times": 1,
          "invalid_rows": 0,
        },
      }

    def find_daily_keys_batch(self, *_args, **_kwargs):
      return {
        "000001.SZ": [1000, 1000],
        "300001.SZ": [3000],
      }

  monkeypatch.setattr(module, "KLineRepository", Repository)
  expected = {
    "record_count": 2,
    "symbols": {
      "000001.SZ": {
        "row_count": 1,
        "distinct_times": 1,
        "min_time_ms": 1000,
        "max_time_ms": 1000,
        "key_sha256": module._key_digest([1000]),
      },
      "600000.SH": {
        "row_count": 1,
        "distinct_times": 1,
        "min_time_ms": 2000,
        "max_time_ms": 2000,
        "key_sha256": module._key_digest([2000]),
      },
    },
  }

  result = module.verify_influx(
    {
      "codes": ["000001.SZ", "600000.SH"],
      "start_date": "20250101",
      "end_date": "20250131",
    },
    expected,
  )

  assert result["ok"] is False
  assert result["duplicate_rows"] == 1
  assert result["invalid_rows"] == 1
  assert result["missing_codes"] == ["600000.SH"]
  assert result["unexpected_codes"] == ["300001.SZ"]
  assert result["mismatched_codes"] == ["000001.SZ", "600000.SH"]


def test_explicit_failed_ingestion_retry_records_proof_before_reopen(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  state_path = tmp_path / "state.json"
  job = {
    "id": "stocks-20250101-20250131-test",
    "kind": "stocks",
    "codes": ["000001.SZ"],
    "start_date": "20250101",
    "end_date": "20250131",
    "status": "flow_failed",
    "prefect_run_id": "flow-failed-1",
    "flow_message": "Influx unavailable",
  }
  state = {"jobs": [job]}
  payload = module.request_payload(job)
  audit = {
    "ok": False,
    "request_id": "request-1",
    "status": "FAILED",
    "expected_chunks": 1,
    "received_chunks": 1,
    "actual_chunks": 1,
    "records": 2,
    "processing_error": "WriteError: Influx unavailable",
  }
  manifest = [
    {
      "chunk_index": 0,
      "checksum_sha256": "a" * 64,
      "record_count": 2,
      "compressed": True,
      "storage_reference": str(tmp_path / "chunk.json.gz"),
    }
  ]
  expected = {
    "record_count": 2,
    "symbols": {"000001.SZ": {"row_count": 2}},
  }
  monkeypatch.setattr(
    module,
    "request_audit",
    AsyncMock(return_value=audit),
  )
  monkeypatch.setattr(
    module,
    "market_data_request_details",
    AsyncMock(
      return_value={
        "status": "FAILED",
        "request_payload": payload,
        "processing_error": "WriteError: Influx unavailable",
      }
    ),
  )
  monkeypatch.setattr(
    module,
    "transfer_manifest",
    AsyncMock(return_value=manifest),
  )
  monkeypatch.setattr(
    module,
    "expected_daily_keys",
    lambda _job, _manifest: expected,
  )

  class Store:
    async def reopen_failed_market_data_request(self, request_id):
      assert request_id == "request-1"
      persisted = json.loads(state_path.read_text(encoding="utf-8"))
      history = persisted["jobs"][0]["ingestion_retry_history"][-1]
      assert history["status"] == "validated"
      assert history["old_processing_error"] == (
        "WriteError: Influx unavailable"
      )
      assert history["chunk_checksums"] == [
        {
          "chunk_index": 0,
          "checksum_sha256": "a" * 64,
          "record_count": 2,
        }
      ]
      return {
        "request_id": request_id,
        "status": "UPLOADED",
        "old_processing_error": history["old_processing_error"],
        "expected_chunks": 1,
        "received_chunks": 1,
        "manifest_count": 1,
        "manifest_records": 2,
      }

    async def close(self):
      return None

  monkeypatch.setattr(module, "DurableRuntimeStore", Store)
  reprocess = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-1",
      "operation": "bars",
      "records_received": 2,
      "records_saved": 2,
    }
  )
  monkeypatch.setattr(
    module,
    "reprocess_uploaded_market_data_request",
    reprocess,
  )

  async def verify(retried_job):
    retried_job["request_audit"] = {
      "ok": True,
      "request_id": "request-1",
      "records": 2,
    }
    retried_job["influx_verification"] = {
      "ok": True,
      "verification_version": 2,
    }
    return True

  monkeypatch.setattr(module, "_audit_and_verify_job", verify)

  result = asyncio.run(
    module._retry_failed_ingestion_job(state_path, state, job)
  )

  assert result is True
  assert job["status"] == "completed"
  assert job["ingestion_retry_history"][-1]["status"] == "completed"
  reprocess.assert_awaited_once_with("request-1")
  persisted = json.loads(state_path.read_text(encoding="utf-8"))
  assert persisted["summary"]["status_counts"] == {"completed": 1}
  assert persisted["summary"]["verified_records"] == 2


def test_failed_ingestion_retry_resumes_verification_after_request_completed(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  state_path = tmp_path / "state.json"
  job = {
    "id": "stocks-20250101-20250131-test",
    "kind": "stocks",
    "codes": ["000001.SZ"],
    "start_date": "20250101",
    "end_date": "20250131",
    "status": "flow_failed",
    "ingestion_retry_history": [
      {
        "attempt": 1,
        "request_id": "request-1",
        "status": "failed",
        "reprocessed_at": "2026-07-30T16:00:00+08:00",
        "reprocess_result": {"status": "completed"},
      }
    ],
  }
  state = {"jobs": [job]}
  payload = module.request_payload(job)
  monkeypatch.setattr(
    module,
    "request_audit",
    AsyncMock(
      return_value={
        "ok": True,
        "request_id": "request-1",
        "status": "COMPLETED",
        "expected_chunks": 1,
        "received_chunks": 1,
        "actual_chunks": 1,
        "records": 2,
      }
    ),
  )
  monkeypatch.setattr(
    module,
    "market_data_request_details",
    AsyncMock(
      return_value={
        "status": "COMPLETED",
        "request_payload": payload,
        "processing_error": None,
      }
    ),
  )
  monkeypatch.setattr(
    module,
    "transfer_manifest",
    AsyncMock(
      return_value=[
        {
          "chunk_index": 0,
          "checksum_sha256": "b" * 64,
          "record_count": 2,
          "compressed": True,
          "storage_reference": "unused",
        }
      ]
    ),
  )
  monkeypatch.setattr(
    module,
    "expected_daily_keys",
    lambda _job, _manifest: {
      "record_count": 2,
      "symbols": {"000001.SZ": {"row_count": 2}},
    },
  )
  verify = AsyncMock(side_effect=[False, True])
  monkeypatch.setattr(module, "_audit_and_verify_job", verify)
  reprocess = AsyncMock()
  monkeypatch.setattr(
    module,
    "reprocess_uploaded_market_data_request",
    reprocess,
  )

  first = asyncio.run(
    module._retry_failed_ingestion_job(state_path, state, job)
  )
  second = asyncio.run(
    module._retry_failed_ingestion_job(state_path, state, job)
  )

  assert first is False
  assert second is True
  assert job["status"] == "completed"
  assert [
    item.get("mode") for item in job["ingestion_retry_history"][1:]
  ] == ["verification_resume", "verification_resume"]
  assert job["ingestion_retry_history"][1]["status"] == "failed"
  assert job["ingestion_retry_history"][2]["status"] == "completed"
  reprocess.assert_not_awaited()


def test_failed_ingestion_retry_rejects_unproven_completed_request(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  job = {
    "id": "stocks-20250101-20250131-test",
    "codes": ["000001.SZ"],
    "start_date": "20250101",
    "end_date": "20250131",
    "status": "flow_failed",
  }
  payload = module.request_payload(job)
  monkeypatch.setattr(
    module,
    "request_audit",
    AsyncMock(
      return_value={
        "ok": True,
        "request_id": "request-1",
        "status": "COMPLETED",
        "expected_chunks": 1,
        "received_chunks": 1,
        "actual_chunks": 1,
        "records": 2,
      }
    ),
  )
  monkeypatch.setattr(
    module,
    "market_data_request_details",
    AsyncMock(
      return_value={
        "status": "COMPLETED",
        "request_payload": payload,
      }
    ),
  )
  monkeypatch.setattr(
    module,
    "transfer_manifest",
    AsyncMock(
      return_value=[
        {
          "chunk_index": 0,
          "checksum_sha256": "c" * 64,
          "record_count": 2,
          "compressed": True,
          "storage_reference": "unused",
        }
      ]
    ),
  )
  monkeypatch.setattr(
    module,
    "expected_daily_keys",
    lambda _job, _manifest: {
      "record_count": 2,
      "symbols": {"000001.SZ": {"row_count": 2}},
    },
  )

  with pytest.raises(RuntimeError, match="缺少本 controller"):
    asyncio.run(
      module._retry_failed_ingestion_job(
        tmp_path / "state.json",
        {"jobs": [job]},
        job,
      )
    )


def test_failed_ingestion_retry_rejects_incomplete_transfer(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  job = {
    "id": "stocks-20250101-20250131-test",
    "codes": ["000001.SZ"],
    "start_date": "20250101",
    "end_date": "20250131",
    "status": "flow_failed",
  }
  reopen = AsyncMock()
  monkeypatch.setattr(
    module,
    "request_audit",
    AsyncMock(
      return_value={
        "request_id": "request-1",
        "status": "FAILED",
        "expected_chunks": 2,
        "received_chunks": 1,
        "actual_chunks": 1,
        "records": 2,
      }
    ),
  )
  monkeypatch.setattr(
    module,
    "DurableRuntimeStore",
    lambda: type(
      "Store",
      (),
      {
        "reopen_failed_market_data_request": reopen,
        "close": AsyncMock(),
      },
    )(),
  )

  with pytest.raises(RuntimeError, match="不完整传输"):
    asyncio.run(
      module._retry_failed_ingestion_job(
        tmp_path / "state.json",
        {"jobs": [job]},
        job,
      )
    )

  reopen.assert_not_awaited()


def test_failed_ingestion_retry_rejects_payload_mismatch(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  job = {
    "id": "stocks-20250101-20250131-test",
    "codes": ["000001.SZ"],
    "start_date": "20250101",
    "end_date": "20250131",
    "status": "flow_failed",
  }
  monkeypatch.setattr(
    module,
    "request_audit",
    AsyncMock(
      return_value={
        "request_id": "request-1",
        "status": "FAILED",
        "expected_chunks": 1,
        "received_chunks": 1,
        "actual_chunks": 1,
        "records": 2,
      }
    ),
  )
  monkeypatch.setattr(
    module,
    "market_data_request_details",
    AsyncMock(
      return_value={
        "status": "FAILED",
        "request_payload": {
          **module.request_payload(job),
          "stock_list": ["600000.SH"],
        },
      }
    ),
  )

  with pytest.raises(RuntimeError, match="exact payload 不一致"):
    asyncio.run(
      module._retry_failed_ingestion_job(
        tmp_path / "state.json",
        {"jobs": [job]},
        job,
      )
    )


@pytest.mark.parametrize(
  "status",
  ["verification_failed", "pending", "completed"],
)
def test_failed_ingestion_retry_rejects_non_flow_failed_job(
  status,
  tmp_path,
):
  module = _load_module()
  job = {
    "id": "job-1",
    "codes": ["000001.SZ"],
    "start_date": "20250101",
    "end_date": "20250131",
    "status": status,
  }

  with pytest.raises(RuntimeError, match="只允许处理 flow_failed"):
    asyncio.run(
      module._retry_failed_ingestion_job(
        tmp_path / "state.json",
        {"jobs": [job]},
        job,
      )
    )


def test_cli_default_prefect_api_ignores_dotenv_pollution(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  monkeypatch.setenv(
    "PREFECT_API_URL",
    "http://127.0.0.1:4200/api",
  )
  monkeypatch.setattr(
    sys,
    "argv",
    [
      "backfill_daily_market_data.py",
      "--start-date",
      "20250101",
      "--end-date",
      "20250131",
      "--state-file",
      str(tmp_path / "state.json"),
    ],
  )

  args = module.parse_args()

  assert args.prefect_api_url == "http://192.168.101.4:30420/api"
  assert args.retry_failed_ingestion is False
