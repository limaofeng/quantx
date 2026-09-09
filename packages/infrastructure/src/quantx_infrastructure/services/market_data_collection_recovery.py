"""Explicitly requeue only a source whose original native failure was confirmed."""

import json
from uuid import UUID

from quantx_contracts.collection_permit import CollectionPermit
from quantx_contracts.collection_receipt import CollectionReceipt
from sqlalchemy import text


async def resume_collection(connection, request_id, request, *, reason):
  # Caller holds the original request row lock. No worker lease is needed to
  # submit recovery intent; only the Worker may subsequently issue permission.
  if (
    request["ingestion_progress"]
    or request["ingestion_result"]
    or request["expected_chunks"] is not None
    or (request["received_chunks"] or 0) != 0
  ):
    raise RuntimeError("collection recovery cannot replace uploaded evidence")
  if await connection.scalar(
    text("SELECT EXISTS(SELECT 1 FROM market_data_transfer WHERE request_id=:request)"),
    {"request": request_id},
  ):
    raise RuntimeError("collection recovery cannot replace persisted upload chunks")
  try:
    failure = json.loads(request["processing_error"] or "null")
    permit_id = str(UUID(str(failure["collection_permit_id"])))
  except (ValueError, TypeError, KeyError):
    raise RuntimeError("collection recovery lacks original failure identity") from None
  row = (
    (
      await connection.execute(
        text("""
    SELECT p.permit_payload,p.state,p.resumed_at,r.payload,r.processed_at,r.reason_code,
      r.device_id AS receipt_device_id
    FROM market_data_collection_permit p
    JOIN market_data_collection_receipt r ON r.permit_id=p.permit_id AND r.event='ABORT'
    WHERE p.permit_id=:permit AND p.request_id=:request FOR UPDATE OF p
  """),
        {"permit": permit_id, "request": request_id},
      )
    )
    .mappings()
    .one_or_none()
  )
  if (
    row is None
    or row["state"] != "ABORTED"
    or row["resumed_at"] is not None
    or row["processed_at"] is None
    or row["reason_code"] is not None
  ):
    raise RuntimeError("collection recovery requires accepted original ABORT")
  try:
    permit = CollectionPermit.model_validate(row["permit_payload"])
    receipt = CollectionReceipt.model_validate(row["payload"])
    if (
      receipt.event != "ABORT"
      or receipt.abort is None
      or receipt.abort.unit != permit.unit
      or str(permit.permit_id) != permit_id
      or str(permit.unit.request_id) != request_id
      or str(permit.device_id) != request["device_id"]
      or row["receipt_device_id"] != request["device_id"]
      or receipt.abort.reason_code != failure["reason_code"]
    ):
      raise ValueError("inconsistent abort identity")
  except (ValueError, KeyError, TypeError):
    raise RuntimeError(
      "collection recovery has inconsistent failure evidence"
    ) from None
  # Another active attempt is uncertainty, never permission to reset the source.
  if await connection.scalar(
    text("""
    SELECT EXISTS(SELECT 1 FROM market_data_collection_permit
    WHERE request_id=:request AND state IN ('ISSUED','STARTED'))
  """),
    {"request": request_id},
  ):
    raise RuntimeError("collection recovery has an unresolved native permit")
  await connection.execute(
    text("""
    UPDATE market_data_collection_permit SET resumed_at=clock_timestamp(),resume_reason=:reason
    WHERE permit_id=:permit
  """),
    {"permit": permit_id, "reason": reason},
  )
  await connection.execute(
    text("""
    UPDATE market_data_request SET status='QUEUED',processing_error=NULL,completed_at=NULL,
      updated_at=timezone('UTC',clock_timestamp()) WHERE request_id=:request
  """),
    {"request": request_id},
  )
  attempt = 1 + await connection.scalar(
    text("""
    SELECT COUNT(*) FROM market_data_collection_permit
    WHERE request_id=:request AND resumed_at IS NOT NULL
  """),
    {"request": request_id},
  )
  return {"status": "QUEUED", "attempt": attempt}
