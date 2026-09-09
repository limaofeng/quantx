"""One fenced immutable delivery: durable writes, readback, reference and receipt."""

import json

from sqlalchemy import text

from .data_exchange_reference import (
  import_reference_in_transaction,
  verify_imported_reference,
)
from .development_bar_publication import (
  DeliveryVersionConflict,
  bind_delivery_bar_version,
  check_delivery_bar_publication,
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


async def recheck_development_storage_version(
  request, receipt, identity, budget, *, connection
):
  """Re-read one completed version; never invoke its writer or reference importer."""
  from sqlalchemy.exc import SQLAlchemyError

  from .market_data_persistence_verification import (
    MarketDataPersistenceBlockedError,
    MarketDataPersistenceQueryError,
    MarketDataPersistenceVerificationError,
  )
  from .market_data_transfer_ingestion import MarketDataValidationError

  if budget.owner is None:
    raise RuntimeError("version recovery requires an execution owner")
  async with budget.session_factory() as db:
    await budget.owner._guard_ingestion_owner(db, lock=False)
    terminal = (
      (
        await db.execute(
          text("SELECT state,error FROM development_data_export WHERE id=:id"),
          {"id": identity},
        )
      )
      .mappings()
      .one()
    )
  if terminal["state"] == "BLOCKED":
    return {"id": identity, "status": "BLOCKED", "reason": terminal["error"]}
  schedule = await budget.schedule()
  if schedule["reason_code"]:
    return {"id": identity, "status": "BLOCKED", "reason": schedule["reason_code"]}
  if not schedule["due"]:
    return {
      "id": identity,
      "status": "WAITING_LOCAL_PROOF",
      "reason": schedule["wait_reason"],
    }
  reason = await budget.reserve_proof()
  if reason:
    return {"id": identity, "status": "BLOCKED", "reason": reason}
  try:
    if not isinstance(receipt, dict) or not isinstance(
      receipt.get("local_verification"), dict
    ):
      raise ValueError("missing version receipt")
    manifest = {
      key: value for key, value in receipt.items() if key != "local_verification"
    }
    version = await prepare_immutable_bar_version(ImportedTransfer(manifest), identity)
    if (version.code, version.period, version.trading_date) != (
      request.instrument,
      request.period,
      request.trading_date.isoformat(),
    ):
      raise ValueError("recovery scope changed")
    prior = receipt["local_verification"]["immutable_storage"]
    # Reject a missing or changed directory before spending an Influx query.
    async with budget.session_factory() as db:
      current = await check_delivery_bar_publication(
        db, identity, version, prior, owner=budget.owner
      )
      if current != receipt:
        raise DeliveryVersionConflict("recovery receipt changed")
    proof = await verify_immutable_bar_version(version, connection=connection)
    if proof != prior:
      raise DeliveryVersionConflict("recovery content proof changed")
    async with budget.session_factory() as db:
      current = await check_delivery_bar_publication(
        db, identity, version, proof, owner=budget.owner
      )
      if current != receipt:
        raise DeliveryVersionConflict("recovery receipt changed")
      await verify_imported_reference(
        await db.connection(),
        manifest["reference"],
        receipt["local_verification"]["reference_verification"],
        code=request.instrument,
      )
      await db.execute(
        text(
          "UPDATE development_data_export SET state='LOCAL_VERIFIED',error=NULL,updated_at=clock_timestamp() WHERE id=:id"
        ),
        {"id": identity},
      )
      await db.execute(
        text(
          "UPDATE development_data_download_budget SET wait_reason=NULL,transient_failures=0,next_probe_at=clock_timestamp(),updated_at=clock_timestamp() WHERE delivery_id=:id"
        ),
        {"id": identity},
      )
      await budget.owner._guard_ingestion_owner(db)
      await db.commit()
    return receipt
  except MarketDataPersistenceBlockedError as exc:
    state, reason = "BLOCKED", exc.reason_code
  except (MarketDataPersistenceQueryError, SQLAlchemyError):
    state, reason = "WAITING_LOCAL_PROOF", "LOCAL_READBACK_UNAVAILABLE"
  except (
    ValueError,
    KeyError,
    TypeError,
    DeliveryVersionConflict,
    MarketDataValidationError,
    MarketDataPersistenceVerificationError,
    FileNotFoundError,
  ):
    state, reason = "BLOCKED", "LOCAL_DELIVERY_PROOF_INVALID"
  async with budget.session_factory() as db:
    await budget.owner._guard_ingestion_owner(db)
    await db.execute(
      text(
        "UPDATE development_data_export SET state=:state,error=:reason,updated_at=clock_timestamp() WHERE id=:id"
      ),
      {"id": identity, "state": state, "reason": reason},
    )
    if state == "WAITING_LOCAL_PROOF":
      await budget.schedule("local_failed", _db=db)
    await budget.owner._guard_ingestion_owner(db)
    await db.commit()
  return {"id": identity, "status": state, "reason": reason}
