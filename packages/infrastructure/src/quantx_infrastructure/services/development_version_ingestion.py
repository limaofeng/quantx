"""One fenced immutable delivery: durable writes, readback, reference and receipt."""

import json

from sqlalchemy import text

from .data_exchange_reference import import_reference_in_transaction
from .development_bar_publication import (
  bind_delivery_bar_version,
  publish_delivery_bar_version,
)
from .development_history_import import ImportedTransfer
from .immutable_bar_storage import (
  prepare_immutable_bar_version,
  verify_immutable_bar_version,
  write_immutable_bar_version,
)
from .market_data_ingestion_progress import evidence_hash


async def ingest_development_storage_version(
  request, manifest, progress, *, connection
):
  """Caller began a durable attempt under the partition lock and handles defer."""
  store, identity = progress.store, progress.request_id
  if store.owner is None:
    raise RuntimeError("version ingestion requires an execution owner")
  version = await prepare_immutable_bar_version(ImportedTransfer(manifest), identity)
  if (version.code, version.period, version.trading_date) != (
    request.instrument,
    request.period,
    request.trading_date.isoformat(),
  ):
    raise ValueError("version ingestion request scope changed")
  # This realm marker prevents old unversioned checkpoints from proving writes
  # into the version tables, even when their frozen source files are identical.
  await progress.apply(
    "manifest",
    sha256=evidence_hash(
      {
        "storage_version": version.storage_version,
        "manifest": json.loads(version.manifest_json),
      }
    ),
  )
  if progress.state["phase"] == "VALIDATE":
    await progress.apply("advance", phase="WRITE")
  async with store.session_factory() as db:
    await bind_delivery_bar_version(
      db, identity, version, claim_token=progress.claim_token, owner=store.owner
    )
    await db.commit()
  if progress.state["phase"] == "WRITE":
    records = await write_immutable_bar_version(
      version, connection=connection, progress=progress
    )
    await progress.apply(
      "advance",
      phase="READBACK",
      write_result={
        "storage_version": version.storage_version,
        "records_saved": records,
      },
    )
  if progress.state["phase"] != "READBACK" or progress.state["write_result"] != {
    "storage_version": version.storage_version,
    "records_saved": version.records,
  }:
    raise ValueError("version ingestion has no matching write evidence")
  proof = await verify_immutable_bar_version(version, connection=connection)
  # No Worker lease row lock is held while the external write/readback runs.
  async with store.session_factory() as db:
    reference = await import_reference_in_transaction(
      await db.connection(),
      manifest["reference"],
      code=request.instrument,
      owner=store.owner,
    )
    await publish_delivery_bar_version(
      db, identity, version, proof, claim_token=progress.claim_token, owner=store.owner
    )
    state = await store.mutate_in_transaction(
      db,
      identity,
      claim_token=progress.claim_token,
      action="advance",
      values={"phase": "VERIFIED"},
    )
    receipt = {
      **manifest,
      "local_verification": {
        "records_verified": version.records,
        "immutable_storage": proof,
        "reference_verification": reference,
      },
    }
    await db.execute(
      text("""
      UPDATE development_data_export SET state='LOCAL_VERIFIED',error=NULL,
        manifest=CAST(:manifest AS JSON),updated_at=clock_timestamp() WHERE id=:id
    """),
      {"id": identity, "manifest": json.dumps(receipt, allow_nan=False)},
    )
    await store.guard(db)
    await db.commit()
  progress.state = state
  return receipt
