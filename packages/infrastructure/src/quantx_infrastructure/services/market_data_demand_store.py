"""Offline demand catalog; submission never selects an Agent or makes HTTP calls."""

import asyncio
import hashlib
import json
from contextlib import nullcontext

from quantx_contracts.market_data_service import HistoryDemand, HistoryDemandResult
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from quantx_infrastructure.runtime_store import DurableRuntimeStore


class MarketDataDemandCapacity(RuntimeError):
  pass


class MarketDataDemandStore(DurableRuntimeStore):
  async def history_demand_result(self, demand_id: str):
    from quantx_contracts.data_exchange import HistoryPartitionRequest

    from .development_bar_publication import resolve_published_bar_version

    async with asyncio.timeout(3), async_sessionmaker(self.engine)() as db:
      demand = (
        (
          await db.execute(
            text(
              "SELECT partition,delivery_id FROM market_data_demand WHERE demand_id=:id AND source_kind='REMOTE'"
            ),
            {"id": demand_id},
          )
        )
        .mappings()
        .one_or_none()
      )
      if demand is None or demand["delivery_id"] is None:
        return None
      version = await resolve_published_bar_version(
        db, HistoryPartitionRequest.model_validate(demand["partition"])
      )
      if version is None or version["delivery_id"] != demand["delivery_id"]:
        return None
      expected = {
        "records_verified": version["records"],
        "storage_version": version["storage_version"],
        "source_sha256": version["content_sha256"],
        "persisted_sha256": version["content_sha256"],
        "code": version["stock_code"],
        "period": version["period"],
        "trading_date": version["trading_date"].isoformat(),
      }
      if any(version["proof"].get(key) != value for key, value in expected.items()):
        raise ValueError("Published delivery proof differs from its directory")
      return HistoryDemandResult(
        demand_id=demand_id,
        partition=demand["partition"],
        delivery_id=version["delivery_id"],
        source_version=version["source_version"],
        storage_version=version["storage_version"],
        content_sha256=version["content_sha256"],
        records_verified=version["records"],
        verified_at=version["verified_at"],
      )

  def __init__(self, database_url=None):
    super().__init__(database_url, pool_size=4, max_overflow=0)
    from quantx_infrastructure.config.settings import settings

    self.demand_source_kind = (
      "REMOTE" if settings.environment == "development" else "AGENT"
    )

  async def submit_history_demand(
    self, demand: HistoryDemand, *, _connection=None
  ) -> str:
    encoded = json.dumps(
      demand.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    identity = hashlib.sha256(
      f"history-demand-v1:{self.demand_source_kind}:{encoded}".encode()
    ).hexdigest()
    transaction = (
      nullcontext(_connection) if _connection is not None else self.engine.begin()
    )
    async with asyncio.timeout(3), transaction as connection:
      if _connection is not None:
        await self._guard_ingestion_owner(connection)
      # Serialize admission, not collection. The cap also applies across API processes.
      await connection.execute(text("SELECT pg_advisory_xact_lock(817234593)"))
      exists = await connection.scalar(
        text("SELECT demand_id FROM market_data_demand WHERE demand_id=:id"),
        {"id": identity},
      )
      if exists:
        return str(exists)
      pending = await connection.scalar(
        text("""
        SELECT count(*) FROM (
          SELECT 1 FROM market_data_demand
          WHERE source_request_id IS NULL AND delivery_id IS NULL LIMIT 5000
        ) AS pending
      """)
      )
      if pending >= 5000:
        raise MarketDataDemandCapacity("history demand admission capacity exhausted")
      await connection.execute(
        text("""
        INSERT INTO market_data_demand(demand_id,partition,source_kind)
        VALUES (:id,CAST(:partition AS JSONB),:kind)
      """),
        {"id": identity, "partition": encoded, "kind": self.demand_source_kind},
      )
      if _connection is not None:
        await self._guard_ingestion_owner(connection)
    return identity

  async def history_demand(self, demand_id: str) -> dict | None:
    async with self.engine.connect() as connection:
      row = (
        (
          await connection.execute(
            text("""
        SELECT d.*, r.status AS source_status,
               r.ingestion_progress->>'phase' AS source_phase,
               e.state AS delivery_status,
               e.error AS delivery_reason,
               clock_timestamp() AS observed_at
        FROM market_data_demand d
        LEFT JOIN market_data_request r ON r.request_id=d.source_request_id
        LEFT JOIN development_data_export e ON e.id=d.delivery_id
        WHERE d.demand_id=:id
      """),
            {"id": demand_id},
          )
        )
        .mappings()
        .one_or_none()
      )
    if row is None:
      return None
    result = dict(row)
    delivery_reason = result.pop("delivery_reason")
    if result["source_kind"] == "REMOTE" and delivery_reason:
      result["reason_code"] = delivery_reason
    result["state"] = (
      "LINKED"
      if result["source_request_id"] or result["delivery_id"]
      else "WAITING_SOURCE"
    )
    if result["state"] == "LINKED":
      result["next_probe_at"] = None
    return result
