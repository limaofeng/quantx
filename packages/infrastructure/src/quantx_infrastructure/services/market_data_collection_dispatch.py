"""Worker plans and grants work only to a fresh authenticated history session."""

from sqlalchemy import text

from .development_history_window import history_window_open
from .market_data_collection_permit_store import CollectionPermitStore


async def dispatch_history_collection(worker):
  if worker.demand_source_kind != "AGENT":
    return None
  async with worker.engine.begin() as connection:
    await worker._guard_ingestion_owner(connection)
    session = (
      (
        await connection.execute(
          text("""
      SELECT s.device_id,s.session_id FROM market_data_history_session s
      JOIN agent_devices d ON d.id=s.device_id AND d.user_id=s.user_id
      WHERE s.expires_at > clock_timestamp() AND d.revoked_at IS NULL
        AND s.heartbeat->>'xtdata_ready'='true' AND s.heartbeat->>'qos_reason' IS NULL
      ORDER BY s.device_id LIMIT 1
    """)
        )
      )
      .mappings()
      .one_or_none()
    )
    if session is None:
      return None
    requests = (
      (
        await connection.execute(
          text("""
      SELECT request_id FROM market_data_request
      WHERE device_id=:device AND status='QUEUED'
      ORDER BY created_at,request_id LIMIT 20
    """),
          {"device": session["device_id"]},
        )
      )
      .scalars()
      .all()
    )
  permits = CollectionPermitStore(worker)
  for request_id in requests:
    try:
      await permits.register(request_id)
    except (ValueError, KeyError, TypeError, OverflowError):
      async with worker.engine.begin() as connection:
        await worker._guard_ingestion_owner(connection)
        failed = await connection.scalar(
          text("""
          UPDATE market_data_request SET status='FAILED',
            processing_error='COLLECTION_PLAN_INVALID',updated_at=clock_timestamp()
          WHERE request_id=:request AND status='QUEUED'
            AND NOT EXISTS(SELECT 1 FROM market_data_collection_permit
              WHERE request_id=:request AND state IN ('ISSUED','STARTED'))
          RETURNING request_id
        """),
          {"request": request_id},
        )
        await worker._guard_ingestion_owner(connection)
        if failed is None:
          raise
  return await permits.issue_next(
    device_id=session["device_id"],
    history_session_id=session["session_id"],
    collection_allowed=True,
    development_allowed=await history_window_open(),
  )
