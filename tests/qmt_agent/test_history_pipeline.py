"""Exercise runtime composition through real local files and HTTP serialization."""

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from quantx_contracts import HistoricalBarSummary, historical_bar_key
from quantx_contracts.collection_permit import CollectionPermit, CollectionUnit
from quantx_contracts.history_session import (
  HistoryGrant,
  HistoryRequest,
  HistoryRequestRemoved,
)
from quantx_qmt_agent.historical_worker import historical_work_units
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime

PAYLOAD = {
  "operation": "bars",
  "stock_list": ["000001.SZ"],
  "periods": ["1d"],
  "start_time": "20260901",
  "end_time": "20260901",
}


@pytest.mark.parametrize("lose_finish", [False, True])
@pytest.mark.parametrize("lose_complete", [False, True])
async def test_retained_request_to_native_file_and_http_upload(
  tmp_path, lose_finish, lose_complete
):
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(
    device_id=str(uuid4()), api_url="https://history.test"
  )
  runtime.journal = LocalJournal(tmp_path / "journal.sqlite")
  runtime._market_spool_root = tmp_path / "spool"
  runtime._market_spool_root.mkdir()
  runtime._history_access_token = AsyncMock(return_value="history-only-token")
  request = HistoryRequest(
    request_id=uuid4(), payload=PAYLOAD, unit_count=1, completed_units=0
  )
  now = datetime.now(timezone.utc)
  unit_payload = historical_work_units(PAYLOAD)[0]
  permit = CollectionPermit(
    permit_id=uuid4(),
    device_id=runtime.configuration.device_id,
    owner_epoch=1,
    unit=CollectionUnit.from_payload(str(request.request_id), 0, unit_payload),
    issued_at=now,
    expires_at=now + timedelta(seconds=15),
  )
  events, captured = [], []
  lost = False
  frozen, lost_complete, uploaded = False, False, []

  async def http(message):
    nonlocal lost, frozen, lost_complete
    assert message.headers["authorization"] == "Bearer history-only-token"
    body = await message.aread()
    if message.method == "GET":
      assert message.url.path.endswith("/upload")
      return httpx.Response(
        200,
        json={
          "request_id": str(request.request_id),
          "status": "UPLOADED" if frozen else "RECEIVING",
          "total_chunks": len(uploaded) if frozen else None,
          "chunks": uploaded,
        },
      )
    if message.url.path.endswith("/receipts"):
      receipt = json.loads(body)
      events.append(receipt["event"])
      if receipt["event"] == "FINISH" and lose_finish and not lost:
        lost = True
        raise httpx.ReadError("lost finish response")
      return httpx.Response(
        202,
        json={
          "permit_id": str(permit.permit_id),
          "event": receipt["event"],
          "status": "ACCEPTED",
        },
      )
    if "/chunks/" in message.url.path:
      assert message.headers["x-total-chunks"] == "0"
      assert message.headers["x-content-sha256"] == hashlib.sha256(body).hexdigest()
      captured.extend(json.loads(gzip.decompress(body)))
      uploaded.append(
        {
          "index": 0,
          "sha256": hashlib.sha256(body).hexdigest(),
          "record_count": int(message.headers["x-record-count"]),
          "byte_count": len(body),
        }
      )
      events.append("PUT")
    else:
      assert message.url.path.endswith("/complete")
      assert message.headers["x-total-chunks"] == "1"
      events.append("COMPLETE")
      frozen = True
      if lose_complete and not lost_complete:
        lost_complete = True
        raise httpx.ReadError("lost complete response")
    return httpx.Response(202, json={"accepted": True, "duplicate": False})

  def native(grant, payload):
    assert grant == permit and payload == PAYLOAD
    assert runtime.journal.collection_execution_started(permit)
    events.append("NATIVE")
    row = {"code": "000001.SZ", "period": "1d", "time": 1788220800000, "close": 10}
    key = historical_bar_key(
      code=row["code"], period=row["period"], time_ms=row["time"], tick_ordinal=None
    )
    yield row
    yield HistoricalBarSummary(
      code=row["code"],
      period=row["period"],
      row_count=1,
      min_time=row["time"],
      max_time=row["time"],
      key_sha256=hashlib.sha256(key.encode()).hexdigest(),
      no_data_reason=None,
    ).model_dump(mode="json")

  runtime._collect_history_unit_sync = native
  async with httpx.AsyncClient(transport=httpx.MockTransport(http)) as client:
    runtime._market_data_http_client = client
    runtime._ensure_market_upload_state()
    try:
      await runtime._handle_history_work(request)
      grant = HistoryGrant(permit=permit, state="ISSUED", unit_payload=unit_payload)
      if lose_finish:
        with pytest.raises(httpx.ReadError):
          await runtime._handle_history_work(grant)
        grant = grant.model_copy(update={"state": "STARTED"})
      await runtime._handle_history_work(grant)
      assert "PUT" not in events  # local FINISH alone is not server progress
      completed = request.model_copy(update={"completed_units": 1})
      if lose_complete:
        with pytest.raises(httpx.ReadError, match="lost complete"):
          await runtime._handle_history_work(completed)
        from quantx_qmt_agent.history_pipeline import HistoryPipeline

        runtime._history_pipeline = HistoryPipeline(runtime)
        assert await runtime._history_pipeline.recover_retained_uploads() == {
          str(request.request_id): "UPLOAD_ACCEPTED"
        }
      else:
        await runtime._handle_history_work(completed)
      assert events.count("NATIVE") == 1
      assert events.count("START") == 1
      assert events.count("PUT") == 1
      assert events.count("COMPLETE") == 1
      assert events[-2:] == ["PUT", "COMPLETE"]
      assert captured[0]["close"] == 10
      await runtime._handle_history_work(
        HistoryRequestRemoved(request_id=request.request_id)
      )
      assert runtime._history_pipeline.active == {}
      assert list(runtime._market_spool_root.glob("history-jobs/*/units/*.jsonl"))
    finally:
      runtime._history_upload_io_executor.shutdown(wait=True)
      runtime._historical_ipc_executor.shutdown(wait=True)
      runtime.journal.connection.close()


@pytest.mark.parametrize(
  "status, body",
  [
    (200, {"accepted": True, "duplicate": False}),
    (202, {"accepted": False, "duplicate": False}),
    (202, {"accepted": 1, "duplicate": False}),
    (202, {"accepted": True}),
  ],
)
def test_upload_ack_must_match_contract(status, body):
  from quantx_qmt_agent.history_pipeline import HistoryPipeline

  with pytest.raises(ValueError):
    HistoryPipeline._require_upload_ack(httpx.Response(status, json=body))


def test_upload_snapshot_cannot_skip_different_local_bytes():
  from quantx_contracts.history_upload import HistoryUploadSnapshot
  from quantx_qmt_agent.history_pipeline import HistoryPipeline

  snapshot = HistoryUploadSnapshot(
    request_id=uuid4(),
    status="RECEIVING",
    total_chunks=None,
    chunks=[{"index": 0, "sha256": "0" * 64, "record_count": 1, "byte_count": 10}],
  )
  prepared = SimpleNamespace(
    chunks=[SimpleNamespace(digest="1" * 64, record_count=1, compressed_bytes=10)]
  )
  with pytest.raises(ValueError, match="differs from local bytes"):
    HistoryPipeline._matching_chunks(snapshot, prepared)
