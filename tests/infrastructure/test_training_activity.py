import hashlib
import json

import pytest
from quantx_infrastructure.training_activity import read_training_activity
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_activity_union_is_readonly_bounded_and_excludes_worker_tasks(tmp_path):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  try:
    async with engine.begin() as db:
      await db.execute(
        text(
          "CREATE TABLE stock_selection_training_runs (run_id TEXT, run_kind TEXT, status TEXT, phase TEXT, completed_units INTEGER, total_units INTEGER)"
        )
      )
      await db.execute(
        text(
          "CREATE TABLE research_preparation_jobs (job_id TEXT, kind TEXT, status TEXT, phase TEXT, request JSON)"
        )
      )
      await db.execute(
        text(
          "INSERT INTO stock_selection_training_runs VALUES (:id, 'DEVELOPMENT', :status, 'PREFLIGHT', 0, 10)"
        ),
        [
          {"id": f"run-{i:03}", "status": "RUNNING" if i == 60 else "QUEUED"}
          for i in range(61)
        ],
      )
      jobs = [
        ("gpu", "GPU", {}),
        ("frozen", "CERTIFY", {"certification_input": {"bundle_id": "x"}}),
        ("worker-cert", "CERTIFY", {}),
        ("empty-input", "CERTIFY", {"certification_input": {}}),
        ("download", "DOWNLOAD", {}),
      ]
      await db.execute(
        text(
          "INSERT INTO research_preparation_jobs VALUES (:id, :kind, 'RUNNING', '等待 Trainer', :request)"
        ),
        [
          {"id": id, "kind": kind, "request": json.dumps(request)}
          for id, kind, request in jobs
        ],
      )
      statements = []
      event.listen(
        engine.sync_engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, executemany: (
          statements.append(statement)
        ),
      )
      value = await read_training_activity(db)
      assert len(statements) == 1 and statements[0].lstrip().startswith("SELECT")
      assert value["truncated"] is True and len(value["tasks"]) == 50
      assert {row["id"] for row in value["tasks"][:3]} == {"gpu", "frozen", "run-060"}
      assert all(
        row["id"] not in {"worker-cert", "empty-input", "download"}
        for row in value["tasks"]
      )
      assert {
        row["phase"] for row in value["tasks"] if row["type"] == "PREPARATION"
      } == {"等待 Trainer"}
      from quantx_trainer.backend_status import (
        read_backend_status,
        write_backend_status,
      )

      config = tmp_path / "config.toml"
      config.write_text("fixture")
      write_backend_status(
        tmp_path,
        config,
        {"status": "CPU_AVAILABLE", "cpu_available": True},
        value,
        observed_at=100,
        expected_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
      )
      cached = read_backend_status(tmp_path, config, now=110)
      assert cached["activity"] == value
      assert "activity" not in read_backend_status(tmp_path, config, now=191)
  finally:
    await engine.dispose()
