"""Import completed Trainer result bundles for development API reads."""

import asyncio
import os
import threading
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

from prefect import flow
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
)
from quantx_infrastructure.services.research_preparation import root
from quantx_infrastructure.training_host_guard import HostPolicy, host_guard_root
from quantx_infrastructure.training_result_import import import_training_result
from quantx_infrastructure.training_transfer import open_store

from quantx_worker.prefector.flows.certification_transfer import export_transfer_config

FIELDS = (
  "run_id",
  "run_kind",
  "status",
  "run_key",
  "artifact_manifest_sha256",
  "artifact_bundle",
)
PAGE_SIZE = 100


async def _import_one(row, transfer, runs_root, cache_root, reserve_bytes):
  cancel = threading.Event()

  def copy():
    if (runs_root / row.run_id).exists():
      return import_training_result(
        row,
        None,
        runs_root=runs_root,
        cache_root=cache_root,
        reserve_bytes=reserve_bytes,
        cancel=cancel,
      )
    with open_store(transfer, cancel=cancel) as store:
      return import_training_result(
        row,
        store.artifacts,
        runs_root=runs_root,
        cache_root=cache_root,
        reserve_bytes=reserve_bytes,
        cancel=cancel,
      )

  task = asyncio.create_task(asyncio.to_thread(copy))
  try:
    return await asyncio.shield(task)
  except asyncio.CancelledError:
    cancel.set()
    while not task.done():
      try:
        await asyncio.shield(task)
      except asyncio.CancelledError:
        continue
      except Exception:
        break
    with suppress(Exception, asyncio.CancelledError):
      task.result()
    raise


@flow(name="research-result-import", retries=0, log_prints=False)
async def research_result_import_flow():
  # This helper enforces the configured development identity before DB access.
  transfer = export_transfer_config()
  reserve = HostPolicy.load(host_guard_root()).minimum_free_disk_mib * 1024**2
  runs_root = Path(
    os.environ.get("QUANTX_RESEARCH_RUNS_ROOT") or root() / ".runtime/research-runs"
  )
  if not runs_root.is_absolute():
    raise ValueError("RESULT_IMPORT_REQUIRES_ABSOLUTE_RUNS_ROOT")
  cache_root = root() / ".runtime/research-result-import"
  imported, pending = [], []
  offset = 0
  while True:
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      rows = await repository.list_runs(
        status="SUCCEEDED", limit=PAGE_SIZE, offset=offset
      )
      # Release the read transaction before potentially slow file/network work.
      snapshots = [
        SimpleNamespace(**{field: getattr(row, field) for field in FIELDS})
        for row in rows
      ]
    for row in snapshots:
      if row.artifact_bundle is None:
        continue
      try:
        await _import_one(row, transfer, runs_root, cache_root, reserve)
        imported.append(row.run_id)
      except Exception:
        pending.append(row.run_id)
    if len(snapshots) < PAGE_SIZE:
      break
    offset += len(snapshots)
  return {
    "status": "PENDING" if pending else "READY",
    "imported": imported,
    "pending": pending,
  }
