"""Per-run partition evidence without retaining transfer manifests in a Flow."""

import json
from typing import Any

from sqlalchemy import text

from quantx_infrastructure.runtime_store import DurableRuntimeStore


class MarketDataSyncAudit:
  def __init__(self, run_id: str):
    self.run_id = run_id
    self.store = DurableRuntimeStore()

  async def record(
    self,
    batch: int,
    scope: dict[str, Any],
    request_id: str,
    status: str,
    summary: dict[str, Any],
  ) -> None:
    async with self.store.engine.begin() as connection:
      await connection.execute(
        text("""
        INSERT INTO market_data_sync_partition
          (run_id, batch_index, scope, request_id, coverage_status, summary, updated_at)
        VALUES (:run_id, :batch, CAST(:scope AS JSON), :request_id, :status,
                CAST(:summary AS JSON), timezone('utc', now()))
        ON CONFLICT (run_id, batch_index) DO UPDATE
        SET scope=EXCLUDED.scope, request_id=EXCLUDED.request_id,
            coverage_status=EXCLUDED.coverage_status, summary=EXCLUDED.summary,
            updated_at=EXCLUDED.updated_at
      """),
        {
          "run_id": self.run_id,
          "batch": batch,
          "scope": json.dumps(scope),
          "request_id": request_id,
          "status": status,
          "summary": json.dumps(summary),
        },
      )

  async def page(self, *, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    async with self.store.engine.connect() as connection:
      rows = (
        (
          await connection.execute(
            text("""
        SELECT p.batch_index, p.scope, p.request_id, p.coverage_status, p.summary,
               p.updated_at, r.status AS request_status, r.completed_at,
               COALESCE(p.summary ->> 'records_saved',
                        CASE WHEN r.status='COMPLETED'
                        THEN r.ingestion_result ->> 'records_saved' END) AS records_saved
        FROM market_data_sync_partition p
        LEFT JOIN market_data_request r ON r.request_id=p.request_id
        WHERE p.run_id=:run_id ORDER BY p.batch_index
        LIMIT :limit OFFSET :offset
      """),
            {
              "run_id": self.run_id,
              "limit": min(200, max(1, limit)),
              "offset": max(0, offset),
            },
          )
        )
        .mappings()
        .all()
      )
      return [dict(row) for row in rows]

  async def counts(self) -> list[dict[str, Any]]:
    async with self.store.engine.connect() as connection:
      rows = (
        (
          await connection.execute(
            text("""
        SELECT p.coverage_status, r.status AS request_status, count(*) AS count,
               max(COALESCE(r.completed_at,p.updated_at)) AS updated_at
        FROM market_data_sync_partition p
        LEFT JOIN market_data_request r ON r.request_id=p.request_id
        WHERE p.run_id=:run_id GROUP BY p.coverage_status,r.status
      """),
            {"run_id": self.run_id},
          )
        )
        .mappings()
        .all()
      )
      return [dict(row) for row in rows]

  async def close(self):
    await self.store.close()
