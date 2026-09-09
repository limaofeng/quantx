"""Reserve network work durably before starting it; crashes never refund work."""

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import text

MAX_DOWNLOAD_ATTEMPTS = 512
MAX_DOWNLOAD_RESERVED_BYTES = 768 * 1024 * 1024
MAX_DOWNLOAD_RESERVED_SECONDS = 10800
DOWNLOAD_ATTEMPT_SECONDS = 30


class DeliveryDownloadBudgetExhausted(ValueError):
  def __init__(self, delivery_id):
    self.delivery_id = delivery_id
    super().__init__("DELIVERY_DOWNLOAD_BUDGET_EXHAUSTED")


class DevelopmentDownloadBudget:
  def __init__(self, session_factory, delivery_id, *, owner=None):
    self.session_factory = session_factory
    self.delivery_id = delivery_id
    self.owner = owner

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
          UPDATE development_data_export SET error='DELIVERY_DOWNLOAD_BUDGET_EXHAUSTED',
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
    async with asyncio.timeout(DOWNLOAD_ATTEMPT_SECONDS):
      yield
