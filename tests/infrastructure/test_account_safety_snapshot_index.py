from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from quantx_infrastructure.models.agent_runtime import (
  AGENT_REPORT_SNAPSHOT_ID,
  AgentReportInbox,
)
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex


def test_snapshot_index_migration_matches_model(monkeypatch):
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions"
    / "20260831_0041_account_safety_snapshot_index.py"
  )
  spec = importlib.util.spec_from_file_location("snapshot_index_revision", path)
  assert spec is not None and spec.loader is not None
  revision = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(revision)
  calls = []
  monkeypatch.setattr(revision.op, "create_index", lambda *args: calls.append(args))
  revision.upgrade()
  assert revision.down_revision == "20260830_0040"
  assert len(calls) == 1
  name, table, columns = calls[0]
  assert table == AgentReportInbox.__tablename__
  index = next(item for item in AgentReportInbox.__table__.indexes if item.name == name)
  ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
  assert [str(item) for item in columns] == [
    "message_type",
    "protocol_version",
    "CAST(payload ->> 'snapshot_id' AS VARCHAR)",
    "received_at DESC",
  ]
  assert (
    "(message_type, protocol_version, (CAST(payload ->> 'snapshot_id' AS VARCHAR)), received_at DESC)"
    in ddl
  )
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_snapshot_lookup_fixes_only_the_json_path_not_the_value(dialect):
  snapshot_id = "snapshot-with-'quotes'"
  query = select(AgentReportInbox).where(AGENT_REPORT_SNAPSHOT_ID == snapshot_id)
  compiled = query.compile(dialect=dialect, compile_kwargs={"render_postcompile": True})
  sql = str(compiled)
  assert snapshot_id not in sql
  assert list(compiled.params.values()) == [snapshot_id]
  if dialect.name == "postgresql":
    assert "CAST((agent_report_inbox.payload ->> 'snapshot_id') AS VARCHAR)" in sql
  else:
    assert "JSON_EXTRACT(agent_report_inbox.payload, '$.\"snapshot_id\"')" in sql
