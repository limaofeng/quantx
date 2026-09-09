"""Offline demand catalog; submission never selects an Agent or makes HTTP calls."""

import hashlib
import json

from quantx_contracts.market_data_service import HistoryDemand
from sqlalchemy import text

from quantx_infrastructure.runtime_store import DurableRuntimeStore


class MarketDataDemandCapacity(RuntimeError):
  pass


class MarketDataDemandStore(DurableRuntimeStore):
  def __init__(self, database_url=None):
    super().__init__(database_url, pool_size=4, max_overflow=0)
    from quantx_infrastructure.config.settings import settings

    self.demand_source_kind = (
      "REMOTE" if settings.environment == "development" else "AGENT"
    )

  async def submit_history_demand(self, demand: HistoryDemand) -> str:
    encoded = json.dumps(
      demand.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    identity = hashlib.sha256(
      f"history-demand-v1:{self.demand_source_kind}:{encoded}".encode()
    ).hexdigest()
    async with self.engine.begin() as connection:
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
    result["state"] = (
      "LINKED"
      if result["source_request_id"] or result["delivery_id"]
      else "WAITING_SOURCE"
    )
    if result["state"] == "LINKED":
      result["next_probe_at"] = None
    return result
