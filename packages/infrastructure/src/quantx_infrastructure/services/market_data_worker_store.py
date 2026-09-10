"""PostgreSQL lease and transactional fences for the historical data worker."""

from quantx_contracts import PROTOCOL_VERSION
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_contracts.market_data_service import HistoryDemand
from sqlalchemy import text

from quantx_infrastructure.runtime_store import MarketDataSourceUnavailable
from quantx_infrastructure.services.data_exchange import (
  ExportQueueCapacity,
  submit_in_transaction,
)
from quantx_infrastructure.services.market_data_demand_store import (
  MarketDataDemandStore,
)


class MarketDataWorkerStore(MarketDataDemandStore):
  async def block_legacy_verified_deliveries(self) -> int:
    """Retire at most twenty obsolete success labels without touching evidence."""
    if self.demand_source_kind != "REMOTE":
      return 0
    async with self.engine.begin() as db:
      await self._guard_ingestion_owner(db)
      rows = await db.execute(
        text("""
        UPDATE development_data_export SET state='BLOCKED',
          error='SOURCE_PROVENANCE_MIGRATION_REQUIRED',updated_at=clock_timestamp()
        WHERE id IN (
          SELECT id FROM development_data_export
          WHERE state='LOCAL_VERIFIED' AND manifest::jsonb->'version'='1'::jsonb
          ORDER BY updated_at,id LIMIT 20 FOR UPDATE SKIP LOCKED
        ) RETURNING id
      """)
      )
      count = len(rows.all())
      await self._guard_ingestion_owner(db)
      return count

  async def next_development_delivery(self):
    """Select one due local import; successful/terminal deliveries are never swept."""
    if self.demand_source_kind != "REMOTE":
      return None
    async with self.engine.begin() as db:
      await self._guard_ingestion_owner(db)
      row = (
        (
          await db.execute(
            text("""
        SELECT e.id,e.request FROM development_data_export e
        LEFT JOIN development_data_download_budget b ON b.delivery_id=e.id
        LEFT JOIN development_data_ingestion i ON i.delivery_id=e.id
        WHERE e.state IN ('QUEUED','WAITING_SOURCE','WAITING_LOCAL_INGESTION','WAITING_LOCAL_PROOF')
          AND ((e.state='WAITING_LOCAL_INGESTION' AND i.delivery_id IS NOT NULL)
            OR b.delivery_id IS NULL OR (b.reason_code IS NULL AND b.next_probe_at <= clock_timestamp()))
          AND (i.delivery_id IS NULL OR (
            NOT (i.progress->>'blocked')::boolean AND
            (i.progress->>'next_retry_at' IS NULL OR
              (i.progress->>'next_retry_at')::timestamptz <= clock_timestamp())))
        ORDER BY e.updated_at,e.id LIMIT 1 FOR UPDATE OF e SKIP LOCKED
      """)
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None:
        return None
      # Rotate admission fairly without changing the immutable receipt or budgets.
      await db.execute(
        text(
          "UPDATE development_data_export SET updated_at=clock_timestamp() WHERE id=:id"
        ),
        {"id": row["id"]},
      )
      await self._guard_ingestion_owner(db)
      return dict(row)

  async def fail_development_delivery(self, identity, *, reason):
    async with self.engine.begin() as db:
      await self._guard_ingestion_owner(db)
      await db.execute(
        text(
          "UPDATE development_data_export SET state='BLOCKED',error=:reason,updated_at=clock_timestamp() "
          "WHERE id=:id AND state IN ('QUEUED','WAITING_SOURCE','WAITING_LOCAL_INGESTION','WAITING_LOCAL_PROOF')"
        ),
        {"id": identity, "reason": reason},
      )
      await self._guard_ingestion_owner(db)

  async def dispatch_history_collection(self):
    from .market_data_collection_dispatch import dispatch_history_collection

    return await dispatch_history_collection(self)

  async def consume_collection_receipts(self) -> int:
    from .market_data_collection_receipt_store import CollectionReceiptStore

    return await CollectionReceiptStore(self.engine).consume(self)

  async def plan_history_demand(self) -> bool:
    """Link one queued demand atomically under the current worker lease."""
    async with self.engine.begin() as connection:
      await self._guard_ingestion_owner(connection)
      row = (
        (
          await connection.execute(
            text("""
        SELECT demand_id,partition,source_kind FROM market_data_demand
        WHERE source_request_id IS NULL AND delivery_id IS NULL
          AND next_probe_at <= clock_timestamp() AND source_kind=:kind
        ORDER BY next_probe_at,created_at,demand_id LIMIT 1 FOR UPDATE SKIP LOCKED
      """),
            {"kind": self.demand_source_kind},
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None:
        return False
      demand = HistoryDemand.model_validate(row["partition"])
      source_id = delivery_id = reason = None
      try:
        if row["source_kind"] == "REMOTE":
          delivery_id = await submit_in_transaction(
            HistoryPartitionRequest.model_validate(row["partition"]), connection
          )
        else:
          source_id = await self.create_market_data_request(
            demand.agent_payload(),
            idempotency_scope=f"history-demand-source-v1:{PROTOCOL_VERSION}:{row['demand_id']}",
            _connection=connection,
          )
      except MarketDataSourceUnavailable:
        reason = "HISTORY_SOURCE_OFFLINE"
      except ExportQueueCapacity:
        reason = "HISTORY_DELIVERY_CAPACITY"
      await self._guard_ingestion_owner(connection)
      await connection.execute(
        text("""
        UPDATE market_data_demand SET source_request_id=CAST(:source AS TEXT),delivery_id=CAST(:delivery AS TEXT),
          reason_code=:reason,next_probe_at=clock_timestamp() + INTERVAL '30 seconds',
          last_progress_at=CASE
            WHEN CAST(:source AS TEXT) IS NOT NULL OR CAST(:delivery AS TEXT) IS NOT NULL THEN clock_timestamp()
            ELSE last_progress_at END
        WHERE demand_id=:id
      """),
        {
          "source": source_id,
          "delivery": delivery_id,
          "reason": reason,
          "id": row["demand_id"],
        },
      )
      return True

  async def recoverable_market_data_request_ids(
    self,
    *,
    limit=20,
    operations=(
      "bars",
      "sector_instruments",
      "divid_factors",
      "financial_data",
      "instrument_details",
    ),
  ):
    return await super().recoverable_market_data_request_ids(
      limit=limit, operations=operations
    )

  def __init__(self, database_url=None, *, owner_id: str):
    super().__init__(database_url)
    self.owner_id = owner_id
    self.epoch: int | None = None

  async def acquire(self) -> bool:
    async with self.engine.begin() as connection:
      epoch = (
        await connection.execute(
          text("""
        INSERT INTO market_data_worker_lease(id,owner_id,epoch,expires_at)
        VALUES (1,:owner,1,clock_timestamp() + INTERVAL '15 seconds')
        ON CONFLICT (id) DO UPDATE SET owner_id = EXCLUDED.owner_id,
          epoch = market_data_worker_lease.epoch + 1,
          expires_at = EXCLUDED.expires_at
        WHERE market_data_worker_lease.expires_at <= clock_timestamp()
        RETURNING epoch
      """),
          {"owner": self.owner_id},
        )
      ).scalar_one_or_none()
    self.epoch = int(epoch) if epoch is not None else None
    self.ingestion_owner_epoch = self.epoch
    return self.epoch is not None

  async def renew(self) -> bool:
    async with self.engine.begin() as connection:
      value = (
        await connection.execute(
          text("""
        UPDATE market_data_worker_lease
        SET expires_at = clock_timestamp() + INTERVAL '15 seconds'
        WHERE id = 1 AND owner_id = :owner AND epoch = :epoch
          AND expires_at > clock_timestamp() RETURNING epoch
      """),
          {"owner": self.owner_id, "epoch": self.epoch},
        )
      ).scalar_one_or_none()
    return value is not None

  async def release(self) -> None:
    async with self.engine.begin() as connection:
      await connection.execute(
        text("""
        UPDATE market_data_worker_lease SET expires_at = clock_timestamp()
        WHERE id = 1 AND owner_id = :owner AND epoch = :epoch
      """),
        {"owner": self.owner_id, "epoch": self.epoch},
      )

  async def _guard_ingestion_owner(self, connection, *, lock=True) -> None:
    value = (
      await connection.execute(
        text(
          """
      SELECT epoch FROM market_data_worker_lease
      WHERE id = 1 AND owner_id = :owner AND epoch = :epoch
        AND expires_at > clock_timestamp()
    """
          + (" FOR SHARE" if lock else "")
        ),
        {"owner": self.owner_id, "epoch": self.epoch},
      )
    ).scalar_one_or_none()
    if value is None:
      raise RuntimeError("market-data worker lease was lost")
