"""Worker-owned native permits. Transport persists facts through the owning worker.

A start deadline is not a native execution timeout: STARTED grants retain the
single native slot until explicit completion, including across worker failover.
Candidate selection uses persistent unit progress; session health/window eligibility
is supplied by the history transport. The store does not infer those facts.
"""

import asyncio
from uuid import UUID, uuid4

from quantx_contracts.collection_permit import (
  COLLECTION_PLAN_VERSION,
  CollectionPermit,
  CollectionUnit,
  native_payload_sha256,
  plan_historical_work_units,
)
from sqlalchemy import text

from quantx_infrastructure.services.market_data_capacity import (
  MAX_MARKET_DATA_INFLIGHT_REQUESTS_PER_DEVICE,
  collection_has_capacity,
)
from quantx_infrastructure.services.market_data_staging import market_data_staging_root


class CollectionPermitStore:
  def __init__(self, worker_store):
    self.worker = worker_store
    self.staging_root = market_data_staging_root()

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
      return await self._issue(connection, device_id=device_id, unit=unit)

  async def _admitted(self, connection):
    return (
      (
        await connection.execute(
          text("""
      SELECT request_id,device_id,status FROM market_data_request r
      WHERE status IN ('DELIVERED','RECEIVING','UPLOADED','PROCESSING')
        OR (status='QUEUED' AND (
          COALESCE(received_chunks,0)>0 OR EXISTS (
            SELECT 1 FROM market_data_collection_permit p WHERE p.request_id=r.request_id)))
    """)
        )
      )
      .mappings()
      .all()
    )

  async def _issue(self, connection, *, device_id, unit):
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
    admitted = await self._admitted(connection)
    device_requests = {
      row["request_id"] for row in admitted if row["device_id"] == device_id
    }
    if (
      str(unit.request_id) not in device_requests
      and len(device_requests) >= MAX_MARKET_DATA_INFLIGHT_REQUESTS_PER_DEVICE
    ):
      await self.worker._guard_ingestion_owner(connection)
      return None
    collecting = {
      row["request_id"]
      for row in admitted
      if row["status"] in {"QUEUED", "DELIVERED", "RECEIVING"}
    }
    collecting.add(str(unit.request_id))
    if not await asyncio.to_thread(
      collection_has_capacity, self.staging_root, collecting
    ):
      await self.worker._guard_ingestion_owner(connection)
      return None
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
    plan = await self._register_plan(connection, str(unit.request_id), payload, units)
    if plan["next_unit_index"] != unit.unit_index:
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
    await connection.execute(
      text("""
      UPDATE market_data_request SET status='DELIVERED',updated_at=clock_timestamp()
      WHERE request_id=:request AND status='QUEUED'
    """),
      {"request": str(unit.request_id)},
    )
    await self.worker._guard_ingestion_owner(connection)
    return grant

  async def _register_plan(self, connection, request_id, payload, units):
    previous = (
      (
        await connection.execute(
          text("""
      SELECT DISTINCT unit_index,unit_id,state FROM market_data_collection_permit
      WHERE request_id=:request
    """),
          {"request": request_id},
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
        or CollectionUnit.from_payload(request_id, index, units[index]).unit_id
        != record["unit_id"]
      ):
        raise ValueError("collection request changed after permission issuance")
      if record["state"] == "FINISHED":
        completed.add(index)
    if completed != set(range(len(completed))):
      raise ValueError("collection completion sequence is not contiguous")
    await connection.execute(
      text("""
      INSERT INTO market_data_collection_plan(request_id,plan_version,payload_sha256,unit_count,next_unit_index)
      VALUES (:request,:version,:sha,:count,:next) ON CONFLICT DO NOTHING
    """),
      {
        "request": request_id,
        "version": COLLECTION_PLAN_VERSION,
        "sha": native_payload_sha256(payload),
        "count": len(units),
        "next": len(completed),
      },
    )
    plan = (
      (
        await connection.execute(
          text("""
      SELECT plan_version,payload_sha256,unit_count,next_unit_index FROM market_data_collection_plan
      WHERE request_id=:request FOR UPDATE
    """),
          {"request": request_id},
        )
      )
      .mappings()
      .one()
    )
    if (
      plan["plan_version"] != COLLECTION_PLAN_VERSION
      or plan["payload_sha256"] != native_payload_sha256(payload)
      or plan["unit_count"] != len(units)
    ):
      raise ValueError("collection request changed after plan registration")
    if plan["next_unit_index"] != len(completed):
      raise ValueError("collection plan and completion facts disagree")
    return plan

  async def register(self, request_id: str) -> None:
    """Catalog an existing source without issuing permission or advancing it."""
    request_id = str(UUID(request_id))
    async with self.worker.engine.begin() as connection:
      await self._lock(connection)
      row = (
        (
          await connection.execute(
            text("""
        SELECT request_payload,status FROM market_data_request WHERE request_id=:id FOR UPDATE
      """),
            {"id": str(UUID(request_id))},
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None or row["status"] not in {"QUEUED", "DELIVERED", "RECEIVING"}:
        raise ValueError("collection source is not eligible")
      await self._register_plan(
        connection,
        request_id,
        row["request_payload"],
        plan_historical_work_units(row["request_payload"]),
      )
      await self.worker._guard_ingestion_owner(connection)

  async def issue_next(
    self,
    *,
    device_id: str,
    collection_allowed: bool,
    development_allowed: bool,
    history_session_id: str | None = None,
  ) -> CollectionPermit | None:
    """Select and issue atomically, using trusted session/QoS/window eligibility.

    Only completed native units affect the streak. The caller must derive these
    eligibility facts from the authenticated history session, never HTTP input.
    """
    if not collection_allowed:
      return None
    device_id = str(UUID(device_id))
    async with self.worker.engine.begin() as connection:
      await self._lock(connection)
      if history_session_id is not None:
        session = await connection.scalar(
          text("""
          SELECT s.session_id FROM market_data_history_session s
          JOIN agent_devices d ON d.id=s.device_id AND d.user_id=s.user_id
          WHERE s.session_id=:session AND s.device_id=:device
            AND s.expires_at > clock_timestamp() AND d.revoked_at IS NULL
            AND s.heartbeat->>'xtdata_ready'='true'
            AND s.heartbeat->>'qos_reason' IS NULL
          FOR SHARE OF s,d
        """),
          {"session": history_session_id, "device": device_id},
        )
        if session is None:
          return None
      blocked = (
        await connection.execute(
          text("""
        SELECT EXISTS(SELECT 1 FROM market_data_request WHERE status='BLOCKED'
          AND ingestion_progress->>'reason_code' IN (
            'DEPENDENCY_QUERY_CAPACITY_BLOCKED','DEPENDENCY_AUTH_BLOCKED',
            'DEPENDENCY_WRITE_CAPACITY_BLOCKED','DEPENDENCY_READBACK_UNAVAILABLE',
            'DEPENDENCY_WRITE_UNAVAILABLE'))
      """)
        )
      ).scalar_one()
      if blocked:
        await self.worker._guard_ingestion_owner(connection)
        return None
      admitted = await self._admitted(connection)
      device_requests = [
        row["request_id"] for row in admitted if row["device_id"] == device_id
      ]
      row = (
        (
          await connection.execute(
            text("""
        SELECT r.request_id,r.request_payload,p.next_unit_index
        FROM market_data_collection_plan p
        JOIN market_data_request r ON r.request_id=p.request_id
        CROSS JOIN market_data_collection_schedule s
        WHERE r.device_id=:device AND r.status IN ('QUEUED','DELIVERED','RECEIVING')
          AND p.next_unit_index < p.unit_count
          AND (NOT r.development_only OR :development)
          AND (:has_slot OR r.request_id=ANY(CAST(:admitted AS TEXT[])))
        ORDER BY CASE WHEN s.production_streak >= 4 THEN NOT r.development_only
                      ELSE r.development_only END,
          r.created_at,r.request_id
        LIMIT 1 FOR UPDATE OF r
      """),
            {
              "device": device_id,
              "development": development_allowed,
              "has_slot": len(device_requests)
              < MAX_MARKET_DATA_INFLIGHT_REQUESTS_PER_DEVICE,
              "admitted": device_requests,
            },
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None:
        await self.worker._guard_ingestion_owner(connection)
        return None
      units = plan_historical_work_units(row["request_payload"])
      index = row["next_unit_index"]
      if index >= len(units):
        raise ValueError("collection request changed after plan registration")
      return await self._issue(
        connection,
        device_id=device_id,
        unit=CollectionUnit.from_payload(row["request_id"], index, units[index]),
      )

  async def acknowledge(
    self, *, permit_id: str, device_id: str, event: str, completion=None
  ) -> bool:
    """Apply an authenticated native start/completion fact under the live owner.

    Duplicate facts are harmless. FINISH may refer to a previous owner's STARTED
    grant; takeover alone cannot prove that its native call has stopped.
    """
    if event not in {"START", "FINISH"}:
      raise ValueError("unknown collection event")
    async with self.worker.engine.begin() as connection:
      await self._lock(connection)
      return await self.apply_receipt(
        connection,
        permit_id=permit_id,
        device_id=device_id,
        event=event,
        completion=completion,
      )

  async def apply_receipt(
    self, connection, *, permit_id: str, device_id: str, event: str, completion=None
  ) -> bool:
    """Apply a fact inside the caller's fenced receipt transaction."""
    if event not in {"START", "FINISH"}:
      raise ValueError("unknown collection event")
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
    if event == "FINISH" and (completion is None or completion.unit != grant.unit):
      raise ValueError("collection completion evidence does not match the permit")
    if event == "START" and completion is not None:
      raise ValueError("collection START cannot carry completion evidence")
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
      advanced = (
        await connection.execute(
          text("""
        UPDATE market_data_collection_plan SET next_unit_index=next_unit_index+1
        WHERE request_id=:request AND next_unit_index=:index AND next_unit_index<unit_count
        RETURNING next_unit_index
      """),
          {"request": str(grant.unit.request_id), "index": grant.unit.unit_index},
        )
      ).scalar_one_or_none()
      if advanced is None:
        raise ValueError("collection plan and completion facts disagree")
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
