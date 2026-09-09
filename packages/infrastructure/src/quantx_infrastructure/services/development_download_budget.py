"""Reserve network work durably before starting it; crashes never refund work."""

import asyncio
from contextlib import asynccontextmanager, nullcontext

import httpx
from sqlalchemy import text

MAX_DOWNLOAD_ATTEMPTS = 512
MAX_DOWNLOAD_RESERVED_BYTES = 768 * 1024 * 1024
MAX_DOWNLOAD_RESERVED_SECONDS = 10800
DOWNLOAD_ATTEMPT_SECONDS = 30
MAX_PROOF_ATTEMPTS = 4


class DeliveryDownloadBudgetExhausted(ValueError):
  def __init__(self, delivery_id):
    self.delivery_id = delivery_id
    super().__init__("DELIVERY_DOWNLOAD_BUDGET_EXHAUSTED")


class DeliveryRemoteUnavailable(ValueError):
  def __init__(self, delivery_id):
    self.delivery_id = delivery_id
    super().__init__("DELIVERY_REMOTE_UNAVAILABLE")


class DevelopmentDownloadBudget:
  def __init__(self, session_factory, delivery_id, *, owner=None):
    self.session_factory = session_factory
    self.delivery_id = delivery_id
    self.owner = owner

  async def reserve_proof(self):
    """Each attempt reserves a full bounded content scan; never refund on exit."""
    async with self.session_factory() as db:
      if self.owner is not None:
        await self.owner._guard_ingestion_owner(db)
      row = (
        (
          await db.execute(
            text(
              "SELECT proof_attempts,reason_code FROM development_data_download_budget WHERE delivery_id=:id FOR UPDATE"
            ),
            {"id": self.delivery_id},
          )
        )
        .mappings()
        .one()
      )
      reason = row["reason_code"]
      if reason is None and row["proof_attempts"] >= MAX_PROOF_ATTEMPTS:
        reason = "DELIVERY_PROOF_BUDGET_EXHAUSTED"
      if reason:
        await db.execute(
          text(
            "UPDATE development_data_download_budget SET reason_code=:reason,updated_at=clock_timestamp() WHERE delivery_id=:id"
          ),
          {"id": self.delivery_id, "reason": reason},
        )
        await db.execute(
          text(
            "UPDATE development_data_export SET state='BLOCKED',error=:reason,updated_at=clock_timestamp() WHERE id=:id"
          ),
          {"id": self.delivery_id, "reason": reason},
        )
      else:
        await db.execute(
          text(
            "UPDATE development_data_download_budget SET proof_attempts=proof_attempts+1,updated_at=clock_timestamp() WHERE delivery_id=:id"
          ),
          {"id": self.delivery_id},
        )
      if self.owner is not None:
        await self.owner._guard_ingestion_owner(db)
      await db.commit()
    return reason

  async def schedule(self, action="read", *, _db=None):
    changes = {
      "read": None,
      "submitted": "remote_submitted=true",
      "expired": "reason_code='DELIVERY_REMOTE_EXPIRED'",
      "pending": "next_probe_at=clock_timestamp()+INTERVAL '60 seconds', "
      "wait_reason='DELIVERY_SOURCE_PENDING',transient_failures=0",
      "failed": "next_probe_at=clock_timestamp()+make_interval(secs => "
      "LEAST(900,30 * power(2,transient_failures))::double precision), "
      "transient_failures=LEAST(6,transient_failures+1),wait_reason='DELIVERY_REMOTE_UNAVAILABLE'",
      "ready": "wait_reason=NULL,next_probe_at=clock_timestamp()",
    }
    changes["local_failed"] = changes["failed"].replace(
      "DELIVERY_REMOTE_UNAVAILABLE", "LOCAL_READBACK_UNAVAILABLE"
    )
    if action not in changes:
      raise ValueError("invalid delivery schedule action")
    async with self.session_factory() if _db is None else nullcontext(_db) as db:
      if self.owner is not None:
        await self.owner._guard_ingestion_owner(db)
      await db.execute(
        text("""
        INSERT INTO development_data_download_budget(delivery_id) VALUES (:id)
        ON CONFLICT(delivery_id) DO NOTHING
      """),
        {"id": self.delivery_id},
      )
      if changes[action] is not None:
        await db.execute(
          text(
            "UPDATE development_data_download_budget SET "
            + changes[action]
            + ",updated_at=clock_timestamp() WHERE delivery_id=:id"
          ),
          {"id": self.delivery_id},
        )
      if action == "expired":
        await db.execute(
          text("""
          UPDATE development_data_export SET state='BLOCKED',error='DELIVERY_REMOTE_EXPIRED',
            updated_at=clock_timestamp() WHERE id=:id
        """),
          {"id": self.delivery_id},
        )
      row = (
        (
          await db.execute(
            text("""
        SELECT remote_submitted,next_probe_at,wait_reason,reason_code,
          next_probe_at <= clock_timestamp() AS due
        FROM development_data_download_budget WHERE delivery_id=:id
      """),
            {"id": self.delivery_id},
          )
        )
        .mappings()
        .one()
      )
      if self.owner is not None:
        await self.owner._guard_ingestion_owner(db)
      if _db is None:
        await db.commit()
    return dict(row)

  async def reserve(self, maximum_bytes):
    if (
      type(maximum_bytes) is not int
      or not 0 < maximum_bytes <= MAX_DOWNLOAD_RESERVED_BYTES
    ):
      raise ValueError("invalid download reservation")
    async with self.session_factory() as db:
      if self.owner is not None:
        await self.owner._guard_ingestion_owner(db)
      await db.execute(
        text("""
        INSERT INTO development_data_download_budget(delivery_id) VALUES (:id)
        ON CONFLICT(delivery_id) DO NOTHING
      """),
        {"id": self.delivery_id},
      )
      row = (
        (
          await db.execute(
            text("""
        SELECT * FROM development_data_download_budget WHERE delivery_id=:id FOR UPDATE
      """),
            {"id": self.delivery_id},
          )
        )
        .mappings()
        .one()
      )
      blocked = row["reason_code"] is not None or (
        row["attempts"] + 1 > MAX_DOWNLOAD_ATTEMPTS
        or row["reserved_bytes"] + maximum_bytes > MAX_DOWNLOAD_RESERVED_BYTES
        or row["reserved_seconds"] + DOWNLOAD_ATTEMPT_SECONDS
        > MAX_DOWNLOAD_RESERVED_SECONDS
      )
      if blocked:
        await db.execute(
          text("""
          UPDATE development_data_download_budget SET reason_code='DELIVERY_DOWNLOAD_BUDGET_EXHAUSTED',
            updated_at=clock_timestamp() WHERE delivery_id=:id
        """),
          {"id": self.delivery_id},
        )
        await db.execute(
          text("""
          UPDATE development_data_export SET state='BLOCKED',error='DELIVERY_DOWNLOAD_BUDGET_EXHAUSTED',
            updated_at=clock_timestamp() WHERE id=:id
        """),
          {"id": self.delivery_id},
        )
      else:
        await db.execute(
          text("""
          UPDATE development_data_download_budget SET attempts=attempts+1,
            reserved_bytes=reserved_bytes+:bytes,reserved_seconds=reserved_seconds+:seconds,
            updated_at=clock_timestamp() WHERE delivery_id=:id
        """),
          {
            "id": self.delivery_id,
            "bytes": maximum_bytes,
            "seconds": DOWNLOAD_ATTEMPT_SECONDS,
          },
        )
      if self.owner is not None:
        await self.owner._guard_ingestion_owner(db)
      await db.commit()
    if blocked:
      raise DeliveryDownloadBudgetExhausted(self.delivery_id)

  @asynccontextmanager
  async def attempt(self, maximum_bytes):
    await self.reserve(maximum_bytes)
    try:
      async with asyncio.timeout(DOWNLOAD_ATTEMPT_SECONDS):
        yield
    except (httpx.TransportError, TimeoutError) as exc:
      await self.schedule("failed")
      raise DeliveryRemoteUnavailable(self.delivery_id) from exc
    except httpx.HTTPStatusError as exc:
      if exc.response.status_code == 429 or exc.response.status_code >= 500:
        await self.schedule("failed")
        raise DeliveryRemoteUnavailable(self.delivery_id) from exc
      raise
