"""Resume requested development imports without requiring an online CLI."""

import os

from prefect import flow
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.services.development_history_import import import_partition
from sqlalchemy import text


@flow(name="development-data-import", log_prints=False)
async def development_data_import_flow():
  if os.environ.get("ENV") != "development":
    return {"status": "disabled"}
  async with AsyncSessionLocal() as db:
    rows = (
      (
        await db.execute(
          text("""
      SELECT request FROM development_data_export WHERE state='QUEUED'
      ORDER BY updated_at LIMIT 20
    """)
        )
      )
      .scalars()
      .all()
    )
  for request in rows:
    await import_partition(HistoryPartitionRequest.model_validate(request))
  return {"partitions": len(rows)}
