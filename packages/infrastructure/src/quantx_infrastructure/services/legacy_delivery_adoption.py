"""Explicit adoption of existing local deliveries, without recreating source work."""

import asyncio
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from quantx_contracts.market_data_service import HistoryDemand
from sqlalchemy import text

from .market_data_ingestion_progress import evidence_hash

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AdoptionEntry(BaseModel):
  model_config = ConfigDict(extra="forbid")
  delivery_id: Digest
  demand_id: Digest
  partition: HistoryDemand
  export_sha256: Digest
  existing_demand_sha256: Digest | None
  state: Literal["QUEUED", "INCOMPLETE"]


class AdoptionPlan(BaseModel):
  model_config = ConfigDict(extra="forbid")
  version: Literal[1] = 1
  entries: list[AdoptionEntry] = Field(min_length=1, max_length=1000)

  @model_validator(mode="after")
  def unique_scope(self):
    for field in ("delivery_id", "demand_id"):
      if len({getattr(row, field) for row in self.entries}) != len(self.entries):
        raise ValueError("ADOPTION_SCOPE_CONFLICT")
    return self


def _identity(partition):
  encoded = json.dumps(
    partition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
  )
  return hashlib.sha256(f"history-demand-v1:REMOTE:{encoded}".encode()).hexdigest()


async def _rows(db, ids):
  exports = dict(
    (
      await db.execute(
        text(
          "SELECT id,to_jsonb(e) FROM development_data_export e WHERE id=ANY(:ids) ORDER BY id"
        ),
        {"ids": ids},
      )
    ).all()
  )
  if len(exports) != len(ids):
    raise ValueError("ADOPTION_DELIVERY_MISSING")
  return exports


async def _demand(db, identity):
  return await db.scalar(
    text("SELECT to_jsonb(d) FROM market_data_demand d WHERE demand_id=:id"),
    {"id": identity},
  )


def _check_demand(row, partition, delivery_id):
  if row is None:
    return
  if (
    row["source_kind"] != "REMOTE"
    or row["source_request_id"] is not None
    or HistoryDemand.model_validate(row["partition"]) != partition
    or row["delivery_id"] not in (None, delivery_id)
  ):
    raise ValueError("ADOPTION_DEMAND_CONFLICT")


async def plan_adoption(engine, delivery_ids):
  if not 1 <= len(delivery_ids) <= 1000 or len(set(delivery_ids)) != len(delivery_ids):
    raise ValueError("ADOPTION_SCOPE_INVALID")
  async with asyncio.timeout(15), engine.connect() as db, db.begin():
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    exports = await _rows(db, delivery_ids)
    entries = []
    for identity, row in exports.items():
      partition = HistoryDemand.model_validate(row["request"])
      demand_id = _identity(partition)
      current = await _demand(db, demand_id)
      _check_demand(current, partition, identity)
      entries.append(
        AdoptionEntry(
          delivery_id=identity,
          demand_id=demand_id,
          partition=partition,
          export_sha256=evidence_hash(row),
          existing_demand_sha256=evidence_hash(current) if current else None,
          state=row["state"],
        )
      )
    return AdoptionPlan(entries=entries)


async def apply_adoption(engine, plan: AdoptionPlan):
  # Revalidate even a caller-created model; never trust an edited plan's identities.
  plan = AdoptionPlan.model_validate(plan.model_dump())
  async with asyncio.timeout(15), engine.begin() as db:
    await db.execute(text("SET LOCAL lock_timeout = '3s'"))
    await db.execute(text("SET LOCAL statement_timeout = '10s'"))
    # Prevent a new worker lease and all concurrent delivery/demand mutations until commit.
    await db.execute(text("LOCK TABLE market_data_worker_lease IN SHARE MODE"))
    if await db.scalar(
      text(
        "SELECT EXISTS(SELECT 1 FROM market_data_worker_lease WHERE expires_at>clock_timestamp())"
      )
    ):
      raise ValueError("ADOPTION_WORKER_ACTIVE")
    await db.execute(
      text(
        "LOCK TABLE development_data_export, market_data_demand IN SHARE ROW EXCLUSIVE MODE"
      )
    )
    exports = await _rows(db, [entry.delivery_id for entry in plan.entries])
    actions = []
    for entry in plan.entries:
      export = exports[entry.delivery_id]
      if (
        _identity(entry.partition) != entry.demand_id
        or HistoryDemand.model_validate(export["request"]) != entry.partition
        or export["state"] != entry.state
        or evidence_hash(export) != entry.export_sha256
      ):
        raise ValueError("ADOPTION_EXPORT_CHANGED")
      current = await _demand(db, entry.demand_id)
      _check_demand(current, entry.partition, entry.delivery_id)
      if current and current["delivery_id"] == entry.delivery_id:
        actions.append((entry, "already_adopted"))
        continue
      if (evidence_hash(current) if current else None) != entry.existing_demand_sha256:
        raise ValueError("ADOPTION_DEMAND_CHANGED")
      actions.append((entry, "link" if current else "insert"))
    for entry, action in actions:
      params = {
        "id": entry.demand_id,
        "delivery": entry.delivery_id,
        "partition": json.dumps(entry.partition.model_dump(mode="json")),
      }
      if action == "insert":
        await db.execute(
          text("""
          INSERT INTO market_data_demand(demand_id,partition,source_kind,delivery_id)
          VALUES (:id,CAST(:partition AS jsonb),'REMOTE',:delivery)
        """),
          params,
        )
      elif action == "link":
        await db.execute(
          text("""
          UPDATE market_data_demand SET delivery_id=:delivery, reason_code=NULL,
            last_progress_at=clock_timestamp() WHERE demand_id=:id
        """),
          params,
        )
    return [
      {"delivery_id": entry.delivery_id, "demand_id": entry.demand_id, "action": action}
      for entry, action in actions
    ]
