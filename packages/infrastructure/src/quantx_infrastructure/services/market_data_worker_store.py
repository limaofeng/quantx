"""PostgreSQL lease and transactional fences for the historical data worker."""

from sqlalchemy import text

from quantx_infrastructure.runtime_store import DurableRuntimeStore


class MarketDataWorkerStore(DurableRuntimeStore):
  async def recoverable_market_data_request_ids(
    self, *, limit=20, operations=("bars", "sector_instruments")
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

  async def _guard_ingestion_owner(self, connection) -> None:
    value = (
      await connection.execute(
        text("""
      SELECT epoch FROM market_data_worker_lease
      WHERE id = 1 AND owner_id = :owner AND epoch = :epoch
        AND expires_at > clock_timestamp() FOR SHARE
    """),
        {"owner": self.owner_id, "epoch": self.epoch},
      )
    ).scalar_one_or_none()
    if value is None:
      raise RuntimeError("market-data worker lease was lost")
