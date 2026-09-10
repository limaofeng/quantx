"""Long-lived ingestion worker. PostgreSQL is the source of work and ownership."""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid

import httpx
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.database.timeseries import (
  init_timeseries,
  shutdown_timeseries,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  claim_ingest_and_finish_market_data_request,
)
from quantx_infrastructure.services.market_data_worker_store import (
  MarketDataWorkerStore,
)

logger = logging.getLogger(__name__)


async def advance_development_delivery(store) -> bool:
  from quantx_infrastructure.services.development_history_import import import_partition
  from quantx_infrastructure.services.development_reference_requests import (
    advance_reference_request,
  )

  reference = await advance_reference_request(store)
  item = await store.next_development_delivery()
  if item is None:
    return reference
  try:
    await import_partition(
      HistoryPartitionRequest.model_validate(item["request"]), owner=store
    )
  except (ValueError, KeyError, TypeError, httpx.HTTPStatusError) as exc:
    # Remote malformed content and permanent HTTP failures affect this delivery,
    # not every partition behind it. Retryable transport failures are persisted
    # by the importer; ownership/DB failures propagate to stop the worker.
    reason = (
      "DELIVERY_REMOTE_AUTH_BLOCKED"
      if (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in {401, 403}
      )
      else "DELIVERY_SOURCE_INVALID"
    )
    await store.fail_development_delivery(item["id"], reason=reason)
  return True


async def sweep(store, *, ingest=None) -> int:
  """Bound each discovery pass and process immutable uploads without Prefect."""
  count = 0
  await store.requeue_expired_market_data_delivery_leases(limit=20)
  for _ in range(20):
    if not await store.plan_history_demand():
      break
  for request_id in await store.recoverable_market_data_request_ids(limit=20):
    request = await store.market_data_request(request_id)
    if request is None:
      continue
    # Discovery applies supported-operation filtering before its LIMIT. Do not
    # keep a second category list here that can silently discard admitted work.
    result = await claim_ingest_and_finish_market_data_request(
      store,
      request_id,
      **({"ingest_request": ingest} if ingest is not None else {}),
    )
    if result is not None:
      count += 1
  return count


async def _pause(stop: asyncio.Event, seconds: float) -> None:
  try:
    await asyncio.wait_for(stop.wait(), seconds)
  except TimeoutError:
    pass


async def run(store, stop: asyncio.Event) -> None:
  acquired = False
  while not stop.is_set():
    if await asyncio.wait_for(store.acquire(), 5):
      acquired = True
      break
    await _pause(stop, 2)
  if stop.is_set():
    if acquired:
      await asyncio.wait_for(store.release(), 5)
    return

  async def renew():
    while not stop.is_set():
      await _pause(stop, 5)
      if not stop.is_set() and not await asyncio.wait_for(store.renew(), 3):
        raise RuntimeError("market-data worker lease was lost")

  async def consume():
    while not stop.is_set():
      await sweep(store)
      await _pause(stop, 1)

  async def receipts():
    # Native start grants are short-lived; a long Influx readback must not
    # prevent the independent receipt consumer from confirming START.
    while not stop.is_set():
      await store.consume_collection_receipts()
      await _pause(stop, 0.25)

  async def dispatch():
    while not stop.is_set():
      await store.dispatch_history_collection()
      await _pause(stop, 1)

  async def development():
    while not stop.is_set():
      await advance_development_delivery(store)
      await _pause(stop, 1)

  async def exports():
    from quantx_infrastructure.services.development_history_export import dispatch_once

    while not stop.is_set():
      await dispatch_once(store)
      await _pause(stop, 1)

  async def archives():
    from quantx_infrastructure.services.realtime_archive_worker import (
      advance_realtime_archive,
    )

    while not stop.is_set():
      advanced = await advance_realtime_archive(store)
      await _pause(stop, 0.05 if advanced else 1)

  async def archive_recovery():
    from quantx_infrastructure.services.archive_recovery import advance_archive_recovery

    while not stop.is_set():
      await advance_archive_recovery(store)
      await _pause(stop, 1)

  from quantx_infrastructure.services.market_data_staging_cleanup import (
    run_market_data_staging_sweeper,
  )

  tasks = [
    asyncio.create_task(run_market_data_staging_sweeper(stop, owner=store)),
    asyncio.create_task(renew()),
    asyncio.create_task(consume()),
    asyncio.create_task(receipts()),
    asyncio.create_task(dispatch()),
    asyncio.create_task(archives()),
    asyncio.create_task(archive_recovery()),
    asyncio.create_task(stop.wait()),
  ]
  if getattr(store, "demand_source_kind", None) == "REMOTE":
    tasks.append(asyncio.create_task(development()))
  elif getattr(store, "demand_source_kind", None) == "AGENT":
    tasks.append(asyncio.create_task(exports()))
  try:
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
      task.result()
  finally:
    for task in tasks:
      task.cancel()
    # In-flight synchronous writes/readers join before ownership is released.
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.wait_for(store.release(), 5)


async def _main() -> None:
  stop = asyncio.Event()
  loop = asyncio.get_running_loop()
  for signum in (signal.SIGINT, signal.SIGTERM):
    try:
      loop.add_signal_handler(signum, stop.set)
    except NotImplementedError:
      signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
  store = MarketDataWorkerStore(owner_id=str(uuid.uuid4()))
  try:
    init_timeseries()
    await run(store, stop)
  finally:
    await store.close()
    from quantx_infrastructure.database.relational_connection import close_database

    await close_database()
    shutdown_timeseries()


def main() -> None:
  logging.basicConfig(level=logging.INFO)
  try:
    asyncio.run(_main())
  except Exception as exc:
    logger.error("Market Data Worker stopped: %s", type(exc).__name__)
    raise SystemExit(1) from None


if __name__ == "__main__":
  main()
