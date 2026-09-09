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
    operations=("bars", "sector_instruments", "divid_factors", "financial_data"),
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
