import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from quantx_contracts.collection_permit import CollectionPermit, CollectionUnit
from quantx_contracts.collection_receipt import CollectionAbort
from quantx_qmt_agent.collection_receipts import (
  CollectionReceiptRejected,
  HistoryReceiptClient,
)
from quantx_qmt_agent.native_unit_artifact import NativeUnitArtifact


def grant():
  now = datetime.now(timezone.utc)
  return CollectionPermit(
    permit_id=uuid4(),
    device_id=uuid4(),
    owner_epoch=1,
    unit=CollectionUnit.from_payload(str(uuid4()), 0, {}),
    issued_at=now,
    expires_at=now + timedelta(seconds=15),
  )


def response(permit, event="START", status="ACCEPTED"):
  return {
    "permit_id": str(permit.permit_id),
    "event": event,
    "status": status,
    "reason_code": "COLLECTION_RECEIPT_REJECTED" if status == "REJECTED" else None,
  }


async def test_start_waits_for_worker_confirmation_with_history_token():
  permit, requests = grant(), []

  def handler(request):
    requests.append(request)
    assert request.headers["Authorization"] == "Bearer history-only"
    return httpx.Response(
      202 if request.method == "POST" else 200,
      json=response(
        permit, status="PENDING" if request.method == "POST" else "ACCEPTED"
      ),
    )

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    receipts = HistoryReceiptClient(
      client,
      api_url="http://test",
      history_token=AsyncMock(return_value="history-only"),
    )
    await receipts.start(permit)
  assert [item.method for item in requests] == ["POST", "GET"]
  assert requests[1].url.path.endswith("/receipts/START")


@pytest.mark.parametrize(
  "kind", ["rejected", "wrong-permit", "wrong-event", "wrong-reason"]
)
async def test_invalid_or_rejected_confirmation_cannot_authorize_execution(kind):
  permit = grant()
  body = response(permit, status="REJECTED" if kind == "rejected" else "ACCEPTED")
  if kind == "wrong-permit":
    body["permit_id"] = str(uuid4())
  elif kind == "wrong-event":
    body["event"] = "FINISH"
  elif kind == "wrong-reason":
    body["reason_code"] = "COLLECTION_RECEIPT_REJECTED"
  async with httpx.AsyncClient(
    transport=httpx.MockTransport(lambda _: httpx.Response(202, json=body))
  ) as client:
    receipts = HistoryReceiptClient(
      client, api_url="http://test", history_token=AsyncMock(return_value="token")
    )
    with pytest.raises(CollectionReceiptRejected if kind == "rejected" else ValueError):
      await receipts.start(permit)


async def test_expired_original_permit_can_confirm_sealed_finish():
  permit = grant()
  permit = permit.model_copy(
    update={
      "issued_at": permit.issued_at - timedelta(minutes=1),
      "expires_at": permit.expires_at - timedelta(minutes=1),
    }
  )
  artifact = NativeUnitArtifact(permit.unit, Path("unused.jsonl"), "a" * 64, 100, 0)
  async with httpx.AsyncClient(
    transport=httpx.MockTransport(
      lambda _: httpx.Response(202, json=response(permit, event="FINISH"))
    )
  ) as client:
    receipts = HistoryReceiptClient(
      client, api_url="http://test", history_token=AsyncMock(return_value="token")
    )
    await receipts.finish(permit, artifact)
    with pytest.raises(ValueError, match="expired"):
      await receipts.start(permit)


async def test_token_refresh_is_inside_start_deadline():
  permit = grant()
  permit = permit.model_copy(
    update={"expires_at": datetime.now(timezone.utc) + timedelta(milliseconds=50)}
  )
  sent = []

  async def token():
    await asyncio.Event().wait()

  async with httpx.AsyncClient(
    transport=httpx.MockTransport(lambda request: sent.append(request))
  ) as client:
    receipts = HistoryReceiptClient(client, api_url="http://test", history_token=token)
    with pytest.raises(TimeoutError):
      await receipts.start(permit)
  assert sent == []


async def test_abort_replays_expired_permit_and_waits_for_worker():
  permit, sent = grant(), []
  permit = permit.model_copy(
    update={
      "issued_at": permit.issued_at - timedelta(minutes=1),
      "expires_at": permit.expires_at - timedelta(minutes=1),
    }
  )
  failure = CollectionAbort(
    unit=permit.unit,
    native_exit="CONFIRMED_STOPPED",
    reason_code="COLLECTION_NATIVE_FAILED",
  )

  def handler(request):
    sent.append(request)
    if request.method == "POST":
      assert json.loads(request.content)["abort"] == failure.model_dump(mode="json")
    return httpx.Response(
      202 if request.method == "POST" else 200,
      json=response(
        permit,
        event="ABORT",
        status="PENDING" if request.method == "POST" else "ACCEPTED",
      ),
    )

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    receipts = HistoryReceiptClient(
      client, api_url="http://test", history_token=AsyncMock(return_value="token")
    )
    await receipts.abort(permit, failure)
    with pytest.raises(ValueError, match="does not match"):
      await receipts.abort(permit, failure.model_copy(update={"unit": grant().unit}))
  assert [request.method for request in sent] == ["POST", "GET"]
  assert sent[1].url.path.endswith("/receipts/ABORT")


async def test_transport_failure_does_not_retry_or_replace_permit():
  permit = grant()
  calls = []

  def fail(request):
    calls.append(request)
    raise httpx.ConnectError("offline")

  async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
    receipts = HistoryReceiptClient(
      client, api_url="http://test", history_token=AsyncMock(return_value="token")
    )
    with pytest.raises(httpx.ConnectError):
      await receipts.start(permit)
  assert len(calls) == 1
  assert str(permit.permit_id) in calls[0].url.path
