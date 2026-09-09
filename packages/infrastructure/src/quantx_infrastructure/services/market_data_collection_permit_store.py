"""Worker-owned native permits. Transport persists facts through the owning worker.

A start deadline is not a native execution timeout: STARTED grants retain the
single native slot until explicit completion, including across worker failover.
This store does not select candidates or infer Agent health/window eligibility.
"""

from uuid import UUID, uuid4

from quantx_contracts.collection_permit import (
  CollectionPermit,
  CollectionUnit,
  plan_historical_work_units,
)
from sqlalchemy import text


class CollectionPermitStore:
  def __init__(self, worker_store):
    self.worker = worker_store

  async def _lock(self, connection):
    await self.worker._guard_ingestion_owner(connection)
    await connection.execute(
      text("""
      INSERT INTO market_data_collection_schedule(id) VALUES (1)
      ON CONFLICT DO NOTHING
    """)
    )
    await connection.execute(
      text("""
      SELECT id FROM market_data_collection_schedule WHERE id=1 FOR UPDATE
    """)
    )

    await self.worker._guard_ingestion_owner(connection)

  async def issue(
    self, *, device_id: str, unit: CollectionUnit
  ) -> CollectionPermit | None:
    """Grant a scheduler-selected unit; never change its original native identity."""
    device_id = str(UUID(device_id))
    async with self.worker.engine.begin() as connection:
      await self._lock(connection)
      request = (
        (
          await connection.execute(
            text("""
        SELECT request_payload,device_id,status,development_only FROM market_data_request
        WHERE request_id=:id FOR UPDATE
      """),
            {"id": str(unit.request_id)},
          )
        )
        .mappings()
        .one_or_none()
      )
      if request is None or request["device_id"] != device_id:
        raise ValueError("collection source identity mismatch")
      if request["status"] not in {"QUEUED", "DELIVERED", "RECEIVING"}:
        raise ValueError("collection source is not eligible")
      payload = request["request_payload"]
      units = plan_historical_work_units(payload)
      if (
        unit.unit_index >= len(units)
        or CollectionUnit.from_payload(
          str(unit.request_id), unit.unit_index, units[unit.unit_index]
        )
        != unit
      ):
        raise ValueError("collection unit does not match original request")
      previous = (
        (
          await connection.execute(
            text("""
        SELECT unit_index,unit_id,state FROM market_data_collection_permit
        WHERE request_id=:request
      """),
            {"request": str(unit.request_id)},
          )
        )
        .mappings()
        .all()
      )
      completed = set()
      for record in previous:
        index = record["unit_index"]
        if (
          index >= len(units)
          or CollectionUnit.from_payload(
            str(unit.request_id), index, units[index]
          ).unit_id
          != record["unit_id"]
        ):
          raise ValueError("collection request changed after permission issuance")
        if record["state"] == "FINISHED":
          completed.add(index)
      if any(index not in completed for index in range(unit.unit_index)):
        await self.worker._guard_ingestion_owner(connection)
        return None
      await connection.execute(
        text("""
        UPDATE market_data_collection_permit SET state='EXPIRED'
        WHERE state='ISSUED' AND expires_at <= clock_timestamp()
      """)
      )
      existing = (
        (
          await connection.execute(
            text("""
        SELECT permit_payload,state FROM market_data_collection_permit
        WHERE state IN ('ISSUED','STARTED') OR (unit_id=:unit AND state='FINISHED')
      """),
            {"unit": unit.unit_id},
          )
        )
        .mappings()
        .all()
      )
      for row in existing:
        grant = CollectionPermit.model_validate(row["permit_payload"])
        if (
          row["state"] == "ISSUED"
          and grant.unit == unit
          and grant.owner_epoch == self.worker.epoch
        ):
          await self.worker._guard_ingestion_owner(connection)
          return grant
      if existing:
        await self.worker._guard_ingestion_owner(connection)
        return None
      # Never authorize a start beyond the current lease, even if renewal stops.
      times = (
        (
          await connection.execute(
            text("""
        SELECT clock_timestamp() AS issued_at,
          LEAST(expires_at,clock_timestamp()+INTERVAL '30 seconds') AS expires_at
        FROM market_data_worker_lease WHERE id=1
      """)
          )
        )
        .mappings()
        .one()
      )
      grant = CollectionPermit(
        permit_id=uuid4(),
        device_id=UUID(device_id),
        owner_epoch=self.worker.epoch,
        unit=unit,
        issued_at=times["issued_at"],
        expires_at=times["expires_at"],
      )
      await connection.execute(
        text("""
        INSERT INTO market_data_collection_permit
          (permit_id,request_id,unit_id,unit_index,permit_payload,development_only,state,expires_at)
        VALUES (:id,:request,:unit,:index,CAST(:permit_payload AS jsonb),:development,'ISSUED',:expires)
      """),
        {
          "id": str(grant.permit_id),
          "request": str(unit.request_id),
          "unit": unit.unit_id,
          "index": unit.unit_index,
          "permit_payload": grant.model_dump_json(),
          "development": request["development_only"],
          "expires": grant.expires_at,
        },
      )
      await self.worker._guard_ingestion_owner(connection)
      return grant

  async def acknowledge(self, *, permit_id: str, device_id: str, event: str) -> bool:
    """Apply an authenticated native start/completion fact under the live owner.

    Duplicate facts are harmless. FINISH may refer to a previous owner's STARTED
    grant; takeover alone cannot prove that its native call has stopped.
    """
    if event not in {"START", "FINISH"}:
      raise ValueError("unknown collection event")
    async with self.worker.engine.begin() as connection:
      await self._lock(connection)
      row = (
        (
          await connection.execute(
            text("""
        SELECT permit_payload,state FROM market_data_collection_permit WHERE permit_id=:id FOR UPDATE
      """),
            {"id": str(UUID(permit_id))},
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None:
        raise ValueError("unknown collection permit")
      grant = CollectionPermit.model_validate(row["permit_payload"])
      if grant.device_id != UUID(device_id):
        raise ValueError("collection device mismatch")
      if (event == "START" and row["state"] in {"STARTED", "FINISHED"}) or (
        event == "FINISH" and row["state"] == "FINISHED"
      ):
        await self.worker._guard_ingestion_owner(connection)
        return False
      if event == "START":
        now = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        grant.validate_start(
          device_id=device_id, unit=grant.unit, now=now, minimum_epoch=self.worker.epoch
        )
        if row["state"] != "ISSUED":
          raise ValueError("collection permit cannot start")
        sql = "UPDATE market_data_collection_permit SET state='STARTED',started_at=clock_timestamp() WHERE permit_id=:id"
      else:
        if row["state"] != "STARTED":
          raise ValueError("collection permit has not started")
        sql = "UPDATE market_data_collection_permit SET state='FINISHED',finished_at=clock_timestamp() WHERE permit_id=:id"
        await connection.execute(
          text("""
          UPDATE market_data_collection_schedule SET production_streak=
            CASE WHEN (SELECT development_only FROM market_data_collection_permit WHERE permit_id=:id)
              THEN 0 ELSE LEAST(4,production_streak+1) END WHERE id=1
        """),
          {"id": str(grant.permit_id)},
        )
      await connection.execute(text(sql), {"id": str(grant.permit_id)})
      await self.worker._guard_ingestion_owner(connection)
      return True
