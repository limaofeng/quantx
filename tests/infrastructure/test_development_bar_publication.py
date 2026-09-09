"""Actual temporary PG publication plus immutable SDK/Arrow and reference IO."""
# ruff: noqa: F811

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.services import development_bar_publication as publication
from quantx_infrastructure.services.data_exchange_reference import (
  import_reference_in_transaction,
)
from quantx_infrastructure.services.development_history_import import ImportedTransfer
from quantx_infrastructure.services.development_ingestion_progress import (
  DevelopmentIngestionStore,
)
from quantx_infrastructure.services.immutable_bar_storage import (
  prepare_immutable_bar_version,
  verify_immutable_bar_version,
  write_immutable_bar_version,
)
from quantx_worker.prefector.flows.development_data_export_flow import (
  partition_records,
  publish,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.infrastructure.test_development_download_budget import install_budget_schema
from tests.infrastructure.test_development_reference_transaction import (  # noqa: F401
  reference,
  references,
)
from tests.infrastructure.test_immutable_bar_storage import VersionStorage
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401
from tests.worker.test_development_data_export import bar


@pytest.fixture
async def prepared(references, workers, tmp_path, monkeypatch, request):
  options = getattr(request, "param", True)
  start_write = (
    options.get("start_write", True) if isinstance(options, dict) else options
  )
  period = options.get("period", "1m") if isinstance(options, dict) else "1m"
  first, second = workers[0]
  await install_budget_schema(first.engine)
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0081_development_bar_publication.py"
  )
  spec = importlib.util.spec_from_file_location("bar_publication_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)
  async with first.engine.begin() as db:

    def upgrade(connection):
      operations = Operations(MigrationContext.configure(connection))
      migration.op = SimpleNamespace(
        create_table=lambda *args, **kwargs: operations.create_table(
          *args, prefixes=["TEMPORARY"], **kwargs
        )
      )
      migration.upgrade()

    await db.run_sync(upgrade)
  monkeypatch.setenv("QUANTX_DATA_EXPORT_ROOT", str(tmp_path))
  request = HistoryPartitionRequest(
    instrument="600000.SH", period=period, trading_date="2026-09-07"
  )
  source_row = {**bar(), "period": period}
  if period == "1d":
    source_row["time"] -= (9 * 60 + 31) * 60 * 1000
  chunks, ref = publish(partition_records([[source_row]], request)), reference()
  manifest = {
    "version": 1,
    "payload": request.agent_payload(),
    "chunks": chunks,
    "reference": ref,
    "source_request_id": "source-bars",
    "coverage": "SOURCE_VERIFIED",
    "rows": 1,
    "data_version": hashlib.sha256(
      json.dumps({"chunks": chunks, "reference": ref}, sort_keys=True).encode()
    ).hexdigest(),
  }
  async with first.engine.begin() as db:
    await db.execute(
      text("""
      INSERT INTO development_data_export(id,request,state,manifest,updated_at)
      VALUES ('delivery',CAST(:request AS JSON),'QUEUED',CAST(:manifest AS JSON),clock_timestamp())
    """),
      {"request": request.model_dump_json(), "manifest": json.dumps(manifest)},
    )
  factory = async_sessionmaker(first.engine)
  progress = await DevelopmentIngestionStore(factory, "delivery", owner=first).begin()
  if start_write:
    await progress.apply("manifest", sha256="a" * 64)
    await progress.apply("advance", phase="WRITE")
  version = await prepare_immutable_bar_version(ImportedTransfer(manifest), "delivery")
  return SimpleNamespace(
    first=first,
    second=second,
    factory=factory,
    request=request,
    manifest=manifest,
    version=version,
    progress=progress,
    migration=migration,
  )


async def bind(case):
  async with case.factory() as db:
    result = await publication.bind_delivery_bar_version(
      db,
      "delivery",
      case.version,
      claim_token=case.progress.claim_token,
      owner=case.first,
    )
    await db.commit()
  return result


async def prove(case):
  await bind(case)
  connection = VersionStorage()
  await write_immutable_bar_version(case.version, connection=connection)
  await case.progress.apply(
    "advance",
    phase="READBACK",
    write_result={"storage_version": case.version.storage_version, "records_saved": 1},
  )
  return await verify_immutable_bar_version(case.version, connection=connection)


async def resolve(case):
  async with case.factory() as db:
    return await publication.resolve_published_bar_version(db, case.request)


@pytest.mark.parametrize("rollback", [False, True])
async def test_publication_reference_and_receipt_commit_as_one_unit(prepared, rollback):
  case = prepared
  proof = await prove(case)
  assert await resolve(case) is None

  async def complete():
    async with case.factory() as db:
      ref = await import_reference_in_transaction(
        await db.connection(),
        case.manifest["reference"],
        code=case.request.instrument,
        owner=case.first,
      )
      await publication.publish_delivery_bar_version(
        db,
        "delivery",
        case.version,
        proof,
        claim_token=case.progress.claim_token,
        owner=case.first,
      )
      await case.progress.store.mutate_in_transaction(
        db,
        "delivery",
        claim_token=case.progress.claim_token,
        action="advance",
        values={"phase": "VERIFIED"},
      )
      receipt = {
        **case.manifest,
        "local_verification": {
          "immutable_storage": proof,
          "reference_verification": ref,
        },
      }
      await db.execute(
        text(
          "UPDATE development_data_export SET state='LOCAL_VERIFIED',manifest=CAST(:manifest AS JSON) WHERE id='delivery'"
        ),
        {"manifest": json.dumps(receipt)},
      )
      if rollback:
        raise RuntimeError("receipt failed")
      await db.commit()

  if rollback:
    with pytest.raises(RuntimeError, match="receipt failed"):
      await complete()
  else:
    await complete()
  published = await resolve(case)
  assert (published is None) == rollback
  async with case.factory() as db:
    assert await db.scalar(text("SELECT count(*) FROM divid_factors")) == (
      1 if rollback else 0
    )
    assert await db.scalar(
      text("SELECT progress->>'phase' FROM development_data_ingestion")
    ) == ("READBACK" if rollback else "VERIFIED")
  if not rollback:
    assert published["proof"] == proof
    assert published["storage_version"] == case.version.storage_version
    async with case.factory() as db:
      await db.execute(
        text(
          "UPDATE development_data_export SET manifest=manifest::jsonb #- '{local_verification,immutable_storage}' WHERE id='delivery'"
        )
      )
      await db.commit()
    assert await resolve(case) is None


async def test_same_binding_is_idempotent_but_source_version_change_is_rejected(
  prepared,
):
  case = prepared
  assert await bind(case) == await bind(case)
  changed = json.loads(json.dumps(case.manifest))
  changed["reference"]["extra"] = True
  changed["data_version"] = hashlib.sha256(
    json.dumps(
      {"chunks": changed["chunks"], "reference": changed["reference"]}, sort_keys=True
    ).encode()
  ).hexdigest()
  async with case.factory() as db:
    await db.execute(
      text(
        "UPDATE development_data_export SET manifest=CAST(:manifest AS JSON) WHERE id='delivery'"
      ),
      {"manifest": json.dumps(changed)},
    )
    await db.commit()
  with pytest.raises(publication.DeliveryVersionConflict, match="cannot be replaced"):
    await bind(case)


async def test_old_worker_and_old_claim_cannot_publish(prepared):
  case = prepared
  proof = await prove(case)
  await case.first.release()
  assert await case.second.acquire()
  async with case.factory() as db:
    with pytest.raises(RuntimeError, match="lease was lost"):
      await publication.publish_delivery_bar_version(
        db,
        "delivery",
        case.version,
        proof,
        claim_token=case.progress.claim_token,
        owner=case.first,
      )
  new = await DevelopmentIngestionStore(
    case.factory, "delivery", owner=case.second
  ).begin()
  async with case.factory() as db:
    with pytest.raises(RuntimeError, match="active ingestion claim"):
      await publication.publish_delivery_bar_version(
        db,
        "delivery",
        case.version,
        proof,
        claim_token=case.progress.claim_token,
        owner=case.second,
      )
  async with case.factory() as db:
    await publication.publish_delivery_bar_version(
      db,
      "delivery",
      case.version,
      proof,
      claim_token=new.claim_token,
      owner=case.second,
    )
    await db.commit()
  # A proof alone is not a committed local receipt.
  assert await resolve(case) is None


@pytest.mark.parametrize(
  "key,value",
  [
    ("records_verified", True),
    ("schema_version", True),
    ("fields_verified", 0),
    ("persisted_sha256", "a" * 64),
    ("storage_version", "b" * 64),
    ("period", "tick"),
  ],
)
async def test_invalid_proof_never_becomes_readable(prepared, key, value):
  case = prepared
  proof = await prove(case)
  proof[key] = value
  async with case.factory() as db:
    with pytest.raises(
      publication.DeliveryVersionConflict, match="proof is incomplete"
    ):
      await publication.publish_delivery_bar_version(
        db,
        "delivery",
        case.version,
        proof,
        claim_token=case.progress.claim_token,
        owner=case.first,
      )
  assert await resolve(case) is None


async def test_downgrade_cannot_remove_bound_source_intent(prepared):
  case = prepared
  await bind(case)
  async with case.first.engine.begin() as db:

    def downgrade(connection):
      case.migration.op = Operations(MigrationContext.configure(connection))
      case.migration.downgrade()

    with pytest.raises(RuntimeError, match="cannot remove fixed"):
      await db.run_sync(downgrade)


async def test_binding_cannot_be_created_retroactively_in_readback(prepared):
  case = prepared
  await case.progress.apply(
    "advance", phase="READBACK", write_result={"records_saved": 1}
  )
  with pytest.raises(publication.DeliveryVersionConflict, match="before READBACK"):
    await bind(case)
  async with case.factory() as db:
    assert (
      await db.scalar(text("SELECT count(*) FROM development_data_bar_version")) == 0
    )


async def test_owner_loss_before_publication_commit_rolls_back_proof(prepared):
  case = prepared
  proof = await prove(case)

  class ExpiringOwner:
    async def _guard_ingestion_owner(self, db, *, lock=True):
      if lock:
        await db.execute(
          text(
            "UPDATE market_data_worker_lease SET expires_at=clock_timestamp()-INTERVAL '1 second'"
          )
        )
      await case.first._guard_ingestion_owner(db, lock=lock)

  with pytest.raises(RuntimeError, match="lease was lost"):
    async with case.factory() as db:
      await publication.publish_delivery_bar_version(
        db,
        "delivery",
        case.version,
        proof,
        claim_token=case.progress.claim_token,
        owner=ExpiringOwner(),
      )
      await db.commit()
  async with case.factory() as db:
    assert (
      await db.scalar(text("SELECT proof FROM development_data_bar_version")) is None
    )
    await case.first._guard_ingestion_owner(db)
  assert await resolve(case) is None
