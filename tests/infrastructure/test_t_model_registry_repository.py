"""Isolated registry persistence; synthetic evidence grants no release approval."""

from datetime import UTC, datetime

import pytest
from quantx_infrastructure.models.t_model_registry import (
  TModelRegistryEventRecord,
  TModelVersionRecord,
)
from quantx_infrastructure.repositories.t_model_registry_repository import (
  TModelRegistryConflict,
  TModelRegistryRepository,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 9, 10, tzinfo=UTC)


@pytest.fixture
async def sessions():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as conn:
    for table in (TModelVersionRecord.__table__, TModelRegistryEventRecord.__table__):
      await conn.run_sync(table.create)
  yield async_sessionmaker(engine, expire_on_commit=False)
  await engine.dispose()


def registration(version="v1", gate="ACTIVE_ELIGIBLE"):
  return dict(model_id="t-model", model_version=version, run_key=f"synthetic-final-{version}",
    artifact_sha256="a" * 64, policy_compatibility_hash="b" * 64, gate_conclusion=gate,
    evidence={"synthetic": True, "run_manifest_hash": "c" * 64}, actor_id="reviewer", now=NOW)


async def stage(repo, value, revision, version="v1"):
  return await repo.set_stage(model_id="t-model", model_version=version,
    expected_revision=revision, stage=value, actor_id="reviewer", reason="fixture transition", now=NOW)


async def authorize(repo, mode, revision, version="v1", **changes):
  args = dict(model_id="t-model", model_version=version, expected_revision=revision,
    mode=mode, artifact_sha256="a" * 64, policy_compatibility_hash="b" * 64)
  args.update(changes)
  return await repo.authorize(**args)


async def test_register_idempotent_evidence_immutable_and_restart_revocation(sessions):
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    record = await repo.append_candidate(**registration())
    digest = record.registration_hash
    assert record.registry_stage == "CANDIDATE" and record.authorization_revision == 1
    assert await repo.append_candidate(**registration()) is record
    await stage(repo, "SHADOW", 1)
    assert (await authorize(repo, "SHADOW", 2)).authorization_revision == 2
    await stage(repo, "ACTIVE", 2)
    assert (await authorize(repo, "ACTIVE", 3)).registration_hash == digest
    replay = await repo.append_candidate(**registration())
    assert replay.registry_stage == "ACTIVE" and replay.authorization_revision == 3
    with pytest.raises(TModelRegistryConflict, match="REGISTRATION_CONFLICT"):
      await repo.append_candidate(**(registration() | {"artifact_sha256": "d" * 64}))
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    await stage(repo, "SUSPENDED", 3)
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    with pytest.raises(TModelRegistryConflict, match="REVOKED"):
      await authorize(repo, "ACTIVE", 3)
    row = await repo.get("t-model", "v1")
    assert row.registration_hash == digest and row.evidence == registration()["evidence"]
    assert row.authorization_revision == 4
    events = (await db.scalars(select(TModelRegistryEventRecord).order_by(TModelRegistryEventRecord.authorization_revision))).all()
    assert [e.registry_stage for e in events] == ["CANDIDATE", "SHADOW", "ACTIVE", "SUSPENDED"]
    assert {e.registration_hash for e in events} == {digest}


@pytest.mark.parametrize("change", [
  {"expected_revision": 1}, {"expected_revision": True}, {"mode": "ACTIVE"},
  {"artifact_sha256": "c" * 64}, {"policy_compatibility_hash": "c" * 64}, {"model_version": "missing"},
])
async def test_authorization_requires_exact_frozen_binding(sessions, change):
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    await repo.append_candidate(**registration())
    await stage(repo, "SHADOW", 1)
    args = dict(model_id="t-model", model_version="v1", expected_revision=2, mode="SHADOW",
      artifact_sha256="a" * 64, policy_compatibility_hash="b" * 64) | change
    with pytest.raises(TModelRegistryConflict, match="REVOKED"):
      await repo.authorize(**args)


async def test_stage_cas_gate_and_terminal_state(sessions):
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    await repo.append_candidate(**registration(gate="SHADOW_ELIGIBLE"))
    with pytest.raises(TModelRegistryConflict, match="STAGE_FORBIDDEN"):
      await stage(repo, "ACTIVE", 1)
    await stage(repo, "SHADOW", 1)
    with pytest.raises(TModelRegistryConflict, match="REVISION_CONFLICT"):
      await stage(repo, "RETIRED", 1)
    with pytest.raises(TModelRegistryConflict, match="STAGE_FORBIDDEN"):
      await stage(repo, "ACTIVE", 2)
    await stage(repo, "RETIRED", 2)
    with pytest.raises(TModelRegistryConflict, match="STAGE_FORBIDDEN"):
      await stage(repo, "SHADOW", 3)
    assert await db.scalar(select(func.count()).select_from(TModelRegistryEventRecord)) == 3


async def test_database_prevents_two_active_and_transaction_rolls_back(sessions):
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    for version in ("v1", "v2"):
      await repo.append_candidate(**registration(version))
      await stage(repo, "SHADOW", 1, version)
    await stage(repo, "ACTIVE", 2)
  with pytest.raises(IntegrityError):
    async with sessions() as db, db.begin():
      await stage(TModelRegistryRepository(db), "ACTIVE", 2, "v2")
  async with sessions() as db:
    second = await TModelRegistryRepository(db).get("t-model", "v2")
    assert second.registry_stage == "SHADOW" and second.authorization_revision == 2
    assert await db.scalar(select(func.count()).select_from(TModelRegistryEventRecord)) == 5


async def test_audit_failure_rolls_back_stage_and_revision(sessions, monkeypatch):
  async with sessions() as db, db.begin():
    await TModelRegistryRepository(db).append_candidate(**registration())
  with pytest.raises(RuntimeError, match="audit unavailable"):
    async with sessions() as db, db.begin():
      original = db.add
      def add(record):
        if isinstance(record, TModelRegistryEventRecord):
          raise RuntimeError("audit unavailable")
        original(record)
      monkeypatch.setattr(db, "add", add)
      await stage(TModelRegistryRepository(db), "SHADOW", 1)
  async with sessions() as db:
    row = await TModelRegistryRepository(db).get("t-model", "v1")
    assert row.registry_stage == "CANDIDATE" and row.authorization_revision == 1
    assert await db.scalar(select(func.count()).select_from(TModelRegistryEventRecord)) == 1


@pytest.mark.parametrize("change", [{"gate_conclusion": "BLOCKED"}, {"evidence": {}}, {"artifact_sha256": "invalid"}, {"now": NOW.replace(tzinfo=None)}])
async def test_invalid_registration_never_creates_record(sessions, change):
  async with sessions() as db, db.begin():
    with pytest.raises(TModelRegistryConflict):
      await TModelRegistryRepository(db).append_candidate(**(registration() | change))
    assert await db.scalar(select(func.count()).select_from(TModelVersionRecord)) == 0


async def test_registration_tampering_blocks_authorization_and_transition(sessions):
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    record = await repo.append_candidate(**registration())
    await stage(repo, "SHADOW", 1)
    record.evidence = {"tampered": True}
    await db.flush()
    with pytest.raises(TModelRegistryConflict, match="REGISTRATION_CORRUPT"):
      await authorize(repo, "SHADOW", 2)
    with pytest.raises(TModelRegistryConflict, match="REGISTRATION_CORRUPT"):
      await stage(repo, "ACTIVE", 2)


def test_actual_migration_matches_models_and_reverses():
  import importlib.util
  import io
  from pathlib import Path

  from alembic.autogenerate import compare_metadata
  from alembic.migration import MigrationContext
  from alembic.operations import Operations
  from sqlalchemy import MetaData, create_engine, inspect

  path = Path(__file__).resolve().parents[2] / "packages/infrastructure/alembic/versions/20260910_0086_t_model_registry.py"
  spec = importlib.util.spec_from_file_location("t_registry_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)
  metadata = MetaData()
  for table in (TModelVersionRecord.__table__, TModelRegistryEventRecord.__table__):
    table.to_metadata(metadata)
  engine = create_engine("sqlite:///:memory:")
  try:
    with engine.begin() as connection:
      context = MigrationContext.configure(connection)
      migration.op = Operations(context)
      migration.upgrade()
      assert compare_metadata(context, metadata) == []
      assert {item["name"] for item in inspect(connection).get_check_constraints("t_model_versions")} == {
        "ck_t_model_stage", "ck_t_model_gate", "ck_t_model_active_gate", "ck_t_model_revision",
      }
      migration.downgrade()
      assert inspect(connection).get_table_names() == []
  finally:
    engine.dispose()
  # Compile the same real migration for PostgreSQL without connecting to a DB.
  output = io.StringIO()
  migration.op = Operations(MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}))
  migration.upgrade()
  sql = output.getvalue()
  assert "CREATE UNIQUE INDEX uq_t_model_one_active" in sql
  assert "WHERE registry_stage = 'ACTIVE'" in sql
  assert "REFERENCES t_model_versions (model_id, model_version)" in sql
