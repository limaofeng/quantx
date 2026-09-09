"""Assemble canonical uploads from durable units with no native adapter."""

import asyncio
import gzip
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from quantx_contracts import HistoricalBarSummary, historical_bar_key
from quantx_contracts.collection_permit import CollectionPermit
from quantx_contracts.history_session import HistoryRequest
from quantx_qmt_agent.historical_worker import historical_work_units
from quantx_qmt_agent.history_jobs import HistoryJobs
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.native_unit_artifact import NativeUnitArtifacts
from quantx_qmt_agent.runtime import AgentRuntime

PAYLOAD = {
  "operation": "bars",
  "stock_list": ["000001.SZ", "600000.SH"],
  "periods": ["1m"],
  "start_time": "20260901",
  "end_time": "20260902",
}


@pytest.fixture
def assembly(tmp_path):
  device = str(uuid4())
  journal = LocalJournal(tmp_path / "journal.sqlite")
  root = tmp_path / "spool"
  root.mkdir()
  payloads = historical_work_units(PAYLOAD)
  request = HistoryRequest(
    request_id=uuid4(),
    payload=PAYLOAD,
    unit_count=len(payloads),
    completed_units=len(payloads),
  )
  job = HistoryJobs(root, device_id=device).retain(
    request, reserve=Mock(), release=Mock()
  )
  artifacts = NativeUnitArtifacts(
    job.artifacts_directory, max_bytes=100000, max_record_bytes=10000, max_records=100
  )
  now = datetime.now(timezone.utc)
  expected = []
  for index, unit in enumerate(job.units):
    records = []
    for code in payloads[index]["stock_list"]:
      timestamp = int(
        datetime.strptime(payloads[index]["start_time"][:8], "%Y%m%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
      )
      row = {"code": code, "period": "1m", "time": timestamp, "close": 10.0}
      key = historical_bar_key(
        code=code, period="1m", time_ms=timestamp, tick_ordinal=None
      )
      records.extend(
        [
          row,
          HistoricalBarSummary(
            code=code,
            period="1m",
            row_count=1,
            min_time=timestamp,
            max_time=timestamp,
            key_sha256=hashlib.sha256(key.encode()).hexdigest(),
            no_data_reason=None,
          ).model_dump(mode="json"),
        ]
      )
      expected.append(row)
    permit = CollectionPermit(
      permit_id=uuid4(),
      device_id=device,
      owner_epoch=1,
      unit=unit,
      issued_at=now,
      expires_at=now + timedelta(seconds=15),
    )
    journal.accept_collection_permit(permit, device_id=device, unit=unit, now=now)
    result = artifacts.seal(unit, records, reserve=Mock(), release=Mock())
    journal.record_collection_artifact(
      permit_id=str(permit.permit_id), artifacts=artifacts, artifact=result
    )
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(device_id=device)
  runtime.journal = journal
  runtime._market_spool_root = root
  runtime._historical_worker_lock = asyncio.Lock()
  yield runtime, job, artifacts, expected
  journal.connection.close()


async def test_windows_reassemble_in_canonical_series_order_and_recover_same_bytes(
  assembly,
):
  runtime, job, artifacts, expected = assembly
  async with runtime._historical_worker_lock:
    prepared = runtime._prepare_history_job_sync(job, artifacts)
    encoded = [chunk.path.read_bytes() for chunk in prepared.chunks]
    rows = [row for chunk in encoded for row in json.loads(gzip.decompress(chunk))]
    bars = [row for row in rows if "record_type" not in row]
    assert bars == sorted(
      expected, key=lambda row: (row["period"], row["code"], row["time"])
    )
    summaries = [row for row in rows if "record_type" in row]
    assert len(summaries) == 2
    assert [row["row_count"] for row in summaries] == [2, 2]
    recovered = runtime._prepare_history_job_sync(job, artifacts)
    assert [chunk.path.read_bytes() for chunk in recovered.chunks] == encoded
    assert len(list(job.artifacts_directory.glob("*.jsonl"))) == len(job.units)


async def test_assembly_cannot_bypass_unconfirmed_finish(assembly):
  runtime, job, artifacts, _ = assembly
  request = job.request.model_copy(
    update={"completed_units": job.request.unit_count - 1}
  )
  async with runtime._historical_worker_lock:
    with pytest.raises(ValueError, match="server-confirmed"):
      runtime._prepare_history_job_sync(replace(job, request=request), artifacts)


async def test_missing_later_unit_does_not_publish_partial_upload(assembly):
  runtime, job, artifacts, _ = assembly
  artifacts.inspect(job.units[-1]).path.unlink()
  async with runtime._historical_worker_lock:
    with pytest.raises(FileNotFoundError):
      runtime._prepare_history_job_sync(job, artifacts)
  assert not list(runtime._market_spool_root.glob("request-*/chunk-*.json.gz"))
  assert artifacts.inspect(job.units[0]).record_count > 0


def test_provider_summary_cannot_be_silently_recomputed_as_success():
  from quantx_qmt_agent.history_assembly import _verified_unit_records

  record = {"code": "000001.SZ", "period": "1m", "time": 1, "close": 10}
  summary = HistoricalBarSummary(
    code="000001.SZ",
    period="1m",
    row_count=1,
    min_time=1,
    max_time=1,
    key_sha256="0" * 64,
    no_data_reason=None,
  ).model_dump(mode="json")
  with pytest.raises(ValueError, match="summary differs"):
    list(
      _verified_unit_records(
        iter([record, summary]),
        {"operation": "bars", "stock_list": ["000001.SZ"], "periods": ["1m"]},
      )
    )


async def test_staging_and_compression_share_remaining_physical_budget(
  assembly, monkeypatch
):
  from quantx_qmt_agent import runtime as module

  runtime, job, artifacts, _ = assembly
  retained = module._managed_market_data_spool_bytes(runtime._market_spool_root)
  monkeypatch.setattr(module, "MAX_MARKET_DATA_UPLOAD_CACHE_BYTES", retained + 1)
  async with runtime._historical_worker_lock:
    with pytest.raises(ValueError, match="spool disk byte limit"):
      runtime._prepare_history_job_sync(job, artifacts)
  assert len(list(job.artifacts_directory.glob("*.jsonl"))) == len(job.units)
  assert not list(runtime._market_spool_root.glob("request-*/chunk-*.json.gz"))
