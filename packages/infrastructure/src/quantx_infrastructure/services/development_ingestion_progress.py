"""Local delivery adapter for the shared ingestion state machine.

The caller holds the partition advisory lock for the complete execution. A new
claim invalidates previous callbacks; Worker callers additionally supply owner.
This fences PG evidence, not outstanding external Influx writes.
"""

import json
from datetime import datetime
from uuid import uuid4

from quantx_application.market_data.ingestion import transition
from sqlalchemy import text

from .market_data_ingestion_progress import IngestionProgress


class DevelopmentIngestionStore:
  def __init__(self, session_factory, delivery_id, *, owner=None):
    self.session_factory, self.delivery_id = session_factory, delivery_id
    self.owner = owner

  async def guard(self, connection):
    if self.owner is not None:
      await self.owner._guard_ingestion_owner(connection)

  async def status(self):
    async with self.session_factory() as db:
      row = (
        (
          await db.execute(
            text("""
        SELECT progress,clock_timestamp() AS now FROM development_data_ingestion
        WHERE delivery_id=:id
      """),
            {"id": self.delivery_id},
          )
        )
        .mappings()
        .one_or_none()
      )
    if row is None:
      return None
    state = row["progress"]
    retry = state["next_retry_at"]
    return {
      "progress": state,
      "due": retry is None or datetime.fromisoformat(retry) <= row["now"],
    }

  async def begin(self):
    token = str(uuid4())
    async with self.session_factory() as db:
      await self.guard(db)
      # Catalog locking serializes creation with receipt publication.
      await db.execute(
        text("SELECT id FROM development_data_export WHERE id=:id FOR UPDATE"),
        {"id": self.delivery_id},
      )
      previous = await db.scalar(
        text(
          "SELECT progress FROM development_data_ingestion WHERE delivery_id=:id FOR UPDATE"
        ),
        {"id": self.delivery_id},
      )
      if previous and previous["phase"] == "VERIFIED":
        raise RuntimeError("verified delivery requires readonly recovery")
      state = transition(
        previous, "begin", {}, await db.scalar(text("SELECT clock_timestamp()"))
      )
      await db.execute(
        text("""
        INSERT INTO development_data_ingestion(delivery_id,claim_token,progress)
        VALUES (:id,:token,CAST(:progress AS JSONB))
        ON CONFLICT(delivery_id) DO UPDATE SET claim_token=EXCLUDED.claim_token,
          progress=EXCLUDED.progress,updated_at=clock_timestamp()
      """),
        {"id": self.delivery_id, "token": token, "progress": json.dumps(state)},
      )
      await self._publish_state(db, state)
      await self.guard(db)
      await db.commit()
    progress = IngestionProgress(self, self.delivery_id, token)
    progress.state = state
    return progress

  async def _publish_state(self, db, state):
    await db.execute(
      text("""
      UPDATE development_data_export SET state=:state,error=:reason,
        updated_at=clock_timestamp() WHERE id=:id
    """),
      {
        "id": self.delivery_id,
        "state": "BLOCKED" if state["blocked"] else "WAITING_LOCAL_INGESTION",
        "reason": state["reason_code"],
      },
    )

  async def mutate_market_data_ingestion(
    self, request_id, *, claim_token, action, values=None
  ):
    async with self.session_factory() as db:
      state = await self.mutate_in_transaction(
        db, request_id, claim_token=claim_token, action=action, values=values
      )
      await db.commit()
    return state

  async def mutate_in_transaction(
    self, db, request_id, *, claim_token, action, values=None
  ):
    if request_id != self.delivery_id:
      raise RuntimeError("development ingestion claim was lost")
    await self.guard(db)
    await db.execute(
      text("SELECT id FROM development_data_export WHERE id=:id FOR UPDATE"),
      {"id": request_id},
    )
    previous = await db.scalar(
      text("""
      SELECT progress FROM development_data_ingestion
      WHERE delivery_id=:id AND claim_token=:token FOR UPDATE
    """),
      {"id": request_id, "token": claim_token},
    )
    if previous is None:
      raise RuntimeError("development ingestion claim was lost")
    if previous["blocked"] or previous["phase"] == "VERIFIED":
      raise RuntimeError("development ingestion claim is terminal")
    state = transition(
      previous, action, values or {}, await db.scalar(text("SELECT clock_timestamp()"))
    )
    await db.execute(
      text("""
      UPDATE development_data_ingestion SET progress=CAST(:progress AS JSONB),updated_at=clock_timestamp()
      WHERE delivery_id=:id AND claim_token=:token
    """),
      {"id": request_id, "token": claim_token, "progress": json.dumps(state)},
    )
    if action in {"begin", "defer"}:
      await self._publish_state(db, state)
    await self.guard(db)
    return state
