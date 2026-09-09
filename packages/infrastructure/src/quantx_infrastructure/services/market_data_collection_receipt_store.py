"""API persists native facts; the leased Worker alone applies their transitions."""

import json
from uuid import UUID

from quantx_contracts.collection_permit import CollectionPermit
from quantx_contracts.collection_receipt import (
  CollectionReceipt,
  CollectionReceiptStatus,
)
from sqlalchemy import text

from .market_data_collection_permit_store import CollectionPermitStore


class CollectionReceiptConflict(ValueError):
  pass


class CollectionReceiptStore:
  def __init__(self, engine):
    self.engine = engine

  @staticmethod
  def _status(row):
    return CollectionReceiptStatus(
      permit_id=row["permit_id"],
      event=row["event"],
      status=(
        "PENDING"
        if row["processed_at"] is None
        else "REJECTED"
        if row["reason_code"]
        else "ACCEPTED"
      ),
      reason_code=row["reason_code"],
    )

  async def accept(self, *, permit_id: str, device_id: str, receipt: CollectionReceipt):
    permit_id, device_id = str(UUID(permit_id)), str(UUID(device_id))
    async with self.engine.begin() as connection:
      row = (
        (
          await connection.execute(
            text("""
        SELECT permit_payload,state FROM market_data_collection_permit WHERE permit_id=:id
      """),
            {"id": permit_id},
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None:
        raise KeyError("collection permit unavailable")
      permit = CollectionPermit.model_validate(row["permit_payload"])
      if str(permit.device_id) != device_id:
        raise KeyError("collection permit unavailable")
      if receipt.completion is not None and (
        receipt.completion.unit != permit.unit
        or row["state"] not in {"STARTED", "FINISHED"}
      ):
        raise CollectionReceiptConflict("completion does not match a started permit")
      if receipt.abort is not None and (
        receipt.abort.unit != permit.unit
        or row["state"] not in {"ISSUED", "STARTED", "ABORTED"}
      ):
        raise CollectionReceiptConflict("abort does not match an unfinished permit")
      payload = receipt.model_dump(mode="json")
      await connection.execute(
        text("""
        INSERT INTO market_data_collection_receipt(permit_id,event,device_id,payload)
        VALUES (:id,:event,:device,CAST(:payload AS jsonb))
        ON CONFLICT(permit_id,event) DO NOTHING
      """),
        {
          "id": permit_id,
          "event": receipt.event,
          "device": device_id,
          "payload": json.dumps(payload),
        },
      )
      saved = (
        (
          await connection.execute(
            text("""
        SELECT * FROM market_data_collection_receipt WHERE permit_id=:id AND event=:event
      """),
            {"id": permit_id, "event": receipt.event},
          )
        )
        .mappings()
        .one()
      )
      if saved["device_id"] != device_id or saved["payload"] != payload:
        raise CollectionReceiptConflict(
          "collection receipt conflicts with persisted evidence"
        )
      return self._status(saved)

  async def status(self, *, permit_id: str, device_id: str, event: str):
    async with self.engine.connect() as connection:
      row = (
        (
          await connection.execute(
            text("""
        SELECT * FROM market_data_collection_receipt
        WHERE permit_id=:id AND event=:event AND device_id=:device
      """),
            {
              "id": str(UUID(permit_id)),
              "event": event,
              "device": str(UUID(device_id)),
            },
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None:
        raise KeyError("collection receipt unavailable")
      return self._status(row)

  async def consume(self, worker, *, limit: int = 20) -> int:
    if not 1 <= limit <= 20:
      raise ValueError("collection receipt batch must be between 1 and 20")
    permits = CollectionPermitStore(worker)
    count = 0
    for _ in range(limit):
      async with self.engine.begin() as connection:
        await permits._lock(connection)
        row = (
          (
            await connection.execute(
              text("""
          SELECT * FROM market_data_collection_receipt WHERE processed_at IS NULL
          ORDER BY received_at,permit_id,event LIMIT 1 FOR UPDATE SKIP LOCKED
        """)
            )
          )
          .mappings()
          .one_or_none()
        )
        if row is None:
          break
        reason = None
        try:
          async with connection.begin_nested():
            receipt = CollectionReceipt.model_validate(row["payload"])
            if receipt.event != row["event"]:
              raise ValueError("collection receipt event mismatch")
            await permits.apply_receipt(
              connection,
              permit_id=row["permit_id"],
              device_id=row["device_id"],
              event=receipt.event,
              completion=receipt.completion,
              abort=receipt.abort,
            )
        except ValueError:
          reason = "COLLECTION_RECEIPT_REJECTED"
        await connection.execute(
          text("""
          UPDATE market_data_collection_receipt
          SET processed_at=clock_timestamp(),reason_code=:reason
          WHERE permit_id=:id AND event=:event
        """),
          {"id": row["permit_id"], "event": row["event"], "reason": reason},
        )
        await worker._guard_ingestion_owner(connection)
        count += 1
    return count
