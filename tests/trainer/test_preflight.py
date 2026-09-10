import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote, urlsplit

import pytest
from quantx_trainer import preflight as module
from quantx_trainer.config import TrainerConfig
from quantx_trainer.preflight import (
  REQUIRED_TABLE_GRANTS,
  TrainerPreflightError,
  validate_database_snapshot,
  validate_pool,
)


@pytest.fixture
def config(tmp_path, monkeypatch):
  # Control-plane unit tests do not inspect the test interpreter ACLs.
  monkeypatch.setattr(module, "check_runtime_permissions", lambda *args: None)
  return TrainerConfig(
    environment="development",
    code_root=tmp_path / "code",
    production_root=tmp_path / "production",
    state_root=tmp_path / "state",
    database_url="postgresql+asyncpg://trainer:private@localhost:5432/quantx_dev",
    prefect_api_url="http://localhost:4200/api",
    prefect_pool="quantx-train-pool",
    prefect_pool_id="084451cb-a87f-4f06-9eb2-cae3db39804d",
    transfer_config=tmp_path / "state" / "transfer.toml",
  )


@pytest.fixture
def snapshot():
  identity = dict(
    database="quantx_dev",
    role="trainer",
    session_role="trainer",
    elevated=False,
    database_owner=False,
    database_ddl=False,
    memberships=False,
  )
  tables = [
    dict(
      schema="public",
      name=name,
      owned=False,
      grant_option=False,
      schema_usage=True,
      grants=list(grants),
      column_grants=list(grants),
    )
    for name, grants in REQUIRED_TABLE_GRANTS.items()
  ]
  escapes = dict(
    schema_create=False,
    security_definer=False,
    sequence_access=False,
    other_database=False,
  )
  return identity, tables, escapes


def test_restricted_role_can_access_exact_research_tables(config, snapshot):
  validate_database_snapshot(config, *snapshot)


@pytest.mark.parametrize("key", ["database", "role", "session_role"])
def test_actual_identity_must_match_explicit_configuration(config, snapshot, key):
  snapshot[0][key] = "production"
  with pytest.raises(TrainerPreflightError, match="IDENTITY_MISMATCH"):
    validate_database_snapshot(config, *snapshot)


@pytest.mark.parametrize(
  "key", ["elevated", "database_owner", "database_ddl", "memberships"]
)
@pytest.mark.parametrize("value", [True, None])
def test_privileged_or_unknown_role_is_rejected(config, snapshot, key, value):
  snapshot[0][key] = value
  with pytest.raises(TrainerPreflightError, match="ROLE_TOO_POWERFUL"):
    validate_database_snapshot(config, *snapshot)


@pytest.mark.parametrize(
  "key", ["schema_create", "security_definer", "sequence_access", "other_database"]
)
def test_indirect_permission_escapes_are_rejected(config, snapshot, key):
  snapshot[2][key] = True
  with pytest.raises(TrainerPreflightError, match="PRIVILEGE_ESCAPE"):
    validate_database_snapshot(config, *snapshot)


@pytest.mark.parametrize("column_only", [True, False])
def test_any_trading_table_permission_is_rejected(config, snapshot, column_only):
  snapshot[1].append(
    dict(
      schema="public",
      name="orders",
      owned=False,
      grant_option=False,
      grants=[] if column_only else ["SELECT"],
      column_grants=["SELECT"] if column_only else [],
    )
  )
  with pytest.raises(TrainerPreflightError, match="UNRELATED_TABLE_ACCESS"):
    validate_database_snapshot(config, *snapshot)


@pytest.mark.parametrize(
  "change", ["write", "column_write", "missing", "owner", "grant", "schema", "usage"]
)
def test_research_role_cannot_mutate_specs_or_delegate_access(config, snapshot, change):
  row = next(
    row for row in snapshot[1] if row["name"] == "stock_selection_training_specs"
  )
  if change == "write":
    row["grants"].append("UPDATE")
  elif change == "column_write":
    row["column_grants"].append("UPDATE")
  elif change == "missing":
    snapshot[1].remove(row)
  elif change == "owner":
    row["owned"] = True
  elif change == "grant":
    row["grant_option"] = True
  elif change == "schema":
    row["schema"] = "untrusted"
  else:
    row["schema_usage"] = False
  with pytest.raises(TrainerPreflightError):
    validate_database_snapshot(config, *snapshot)


@pytest.mark.parametrize(
  "key,value",
  [("id", "wrong"), ("name", "quantx-pool"), ("type", "docker"), ("is_paused", True)],
)
def test_pool_identity_and_state_must_match(config, key, value):
  pool = dict(
    id=config.prefect_pool_id, name=config.prefect_pool, type="process", is_paused=False
  )
  validate_pool(config, pool)
  pool[key] = value
  with pytest.raises(TrainerPreflightError):
    validate_pool(config, pool)


@pytest.mark.asyncio
async def test_failed_database_check_never_contacts_prefect(config, monkeypatch):
  database = AsyncMock(
    side_effect=TrainerPreflightError("TRAINER_DATABASE_IDENTITY_MISMATCH")
  )
  prefect = AsyncMock()
  monkeypatch.setattr(module, "check_database", database)
  monkeypatch.setattr(module, "check_prefect", prefect)
  with pytest.raises(TrainerPreflightError):
    await module.preflight(config)
  prefect.assert_not_called()


@pytest.mark.asyncio
async def test_missing_store_identity_blocks_otherwise_valid_control_plane(
  config, monkeypatch
):
  monkeypatch.setattr(module, "check_database", AsyncMock())
  monkeypatch.setattr(module, "check_prefect", AsyncMock())
  with pytest.raises(TrainerPreflightError, match="^TRAINER_STORE_UNAVAILABLE$"):
    await module.preflight(config)


@pytest.mark.asyncio
async def test_database_catalog_check_is_readonly_and_closes_connection(
  config, snapshot, monkeypatch
):
  import asyncpg

  transaction = AsyncMock()
  connection = SimpleNamespace(
    transaction=lambda **kwargs: (
      transaction if kwargs == {"readonly": True} else pytest.fail("write transaction")
    ),
    fetchrow=AsyncMock(side_effect=[snapshot[0], snapshot[2]]),
    fetch=AsyncMock(return_value=snapshot[1]),
    close=AsyncMock(),
  )
  connect = AsyncMock(return_value=connection)
  monkeypatch.setattr(asyncpg, "connect", connect)
  monkeypatch.setenv("PGHOST", "production")
  monkeypatch.setenv("PGDATABASE", "production")
  await module.check_database(config)
  assert connect.call_args.kwargs["host"] == "localhost"
  assert connect.call_args.kwargs["database"] == "quantx_dev"
  assert connect.call_args.kwargs["user"] == "trainer"
  connection.close.assert_awaited_once()
  transaction.__aenter__.assert_awaited_once()


@pytest.mark.asyncio
async def test_dependency_exception_does_not_expose_dsn(config, monkeypatch):
  import asyncpg

  monkeypatch.setattr(
    asyncpg, "connect", AsyncMock(side_effect=RuntimeError(config.database_url))
  )
  with pytest.raises(TrainerPreflightError) as error:
    await module.check_database(config)
  assert "private" not in str(error.value)
  assert str(error.value) == "TRAINER_DATABASE_CHECK_UNAVAILABLE"


@pytest.mark.asyncio
async def test_catalog_queries_execute_on_local_test_database():
  """Read-only SQL validation against the dedicated pytest database, never production."""
  import asyncpg

  target = urlsplit(os.environ["DATABASE_URL"])
  database = unquote(target.path)[1:]
  if target.hostname not in {"localhost", "127.0.0.1", "::1"} or not (
    database.endswith("_test") or database.startswith("test_")
  ):
    pytest.skip("catalog validation requires a local dedicated test database")
  connection = await asyncpg.connect(
    host=target.hostname,
    port=target.port or 5432,
    user=unquote(target.username or ""),
    password=unquote(target.password or ""),
    database=database,
    timeout=5,
    command_timeout=5,
  )
  try:
    async with connection.transaction(readonly=True):
      identity = await connection.fetchrow(module.IDENTITY_SQL)
      tables = await connection.fetch(module.TABLES_SQL)
      escapes = await connection.fetchrow(module.ESCAPES_SQL)
      assert identity["database"] == database
      assert all("column_grants" in row and "grant_option" in row for row in tables)
      assert set(dict(escapes)) == {
        "schema_create",
        "security_definer",
        "sequence_access",
        "other_database",
      }
  finally:
    await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 302, 500])
async def test_prefect_is_readonly_and_does_not_inherit_proxy_or_follow_redirects(
  config, monkeypatch, status
):
  import httpx

  client_class = httpx.AsyncClient
  calls = []

  def handler(request):
    calls.append(request)
    return httpx.Response(
      status,
      headers={"Location": "http://production:4200/api"},
      json={
        "id": config.prefect_pool_id,
        "name": config.prefect_pool,
        "type": "process",
        "is_paused": False,
      },
    )

  def client(**kwargs):
    assert kwargs["trust_env"] is False
    assert kwargs["follow_redirects"] is False
    return client_class(transport=httpx.MockTransport(handler), **kwargs)

  monkeypatch.setattr(httpx, "AsyncClient", client)
  if status == 200:
    await module.check_prefect(config)
  else:
    with pytest.raises(TrainerPreflightError, match="PREFECT_CHECK_UNAVAILABLE"):
      await module.check_prefect(config)
  assert len(calls) == 1
  assert calls[0].method == "GET"
  assert calls[0].url.path == "/api/work_pools/quantx-train-pool"


def test_administrative_entrypoint_validates_local_identity_before_network(
  config, monkeypatch, capsys
):
  from quantx_trainer.main import main

  monkeypatch.setattr(TrainerConfig, "load", lambda filename: config)
  remote_check = AsyncMock()
  monkeypatch.setattr(module, "preflight", remote_check)
  assert main(["preflight", "--config", "ignored.toml"]) == 2
  remote_check.assert_not_called()
  assert "Conda interpreter" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_missing_worker_dependency_rejects_before_control_plane(config, monkeypatch):
  import importlib

  original = importlib.import_module

  def load(name, *args, **kwargs):
    if name == "prefect.workers.process":
      raise ModuleNotFoundError("synthetic-private-import-failure")
    return original(name, *args, **kwargs)

  database = AsyncMock()
  monkeypatch.setattr(importlib, "import_module", load)
  monkeypatch.setattr(module, "check_database", database)
  with pytest.raises(TrainerPreflightError, match="^TRAINER_WORKER_RUNTIME_UNAVAILABLE$"):
    await module.preflight(config)
  database.assert_not_called()
