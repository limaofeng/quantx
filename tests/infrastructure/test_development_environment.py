import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_contracts.runtime_environment import live_runtime_allowed
from quantx_infrastructure.services import development_history_window as window


def test_live_runtime_requires_windows_and_explicit_environment():
  for platform in ("darwin", "linux"):
    for environment in ("development", "production", "testing"):
      assert not live_runtime_allowed(environment, platform=platform)
  assert not live_runtime_allowed("development", platform="win32")
  assert live_runtime_allowed("production", platform="win32")
  assert live_runtime_allowed("testing", platform="win32")


def test_history_request_rejects_trading_fields():
  with pytest.raises(ValueError):
    HistoryPartitionRequest(
      instrument="600000.SH", period="tick", trading_date="2026-09-07", account_id="x"
    )


@pytest.mark.parametrize(
  "hour,minute,expected",
  [(8, 29, True), (8, 30, False), (12, 0, False), (15, 59, False), (16, 0, True)],
)
async def test_history_window_boundaries(monkeypatch, hour, minute, expected):
  monkeypatch.setattr(
    window.HolidayService,
    "get_holidays",
    AsyncMock(return_value=[SimpleNamespace(date=datetime(2026, 1, 1).date())]),
  )
  now = datetime(2026, 9, 8, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
  assert await window.history_window_open(now) is expected


async def test_missing_calendar_never_admits_download(monkeypatch):
  monkeypatch.setattr(window.HolidayService, "get_holidays", AsyncMock(return_value=[]))
  assert not await window.history_window_open(
    datetime(2026, 9, 8, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
  )


def test_development_config_never_uses_production_services(tmp_path):
  root = Path(__file__).resolve().parents[2]
  spec = importlib.util.spec_from_file_location(
    "runtime_config", root / "ops/runtime_config.py"
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  directory = tmp_path / "apps/api"
  directory.mkdir(parents=True)
  config = directory / ".env.development"
  config.write_text(
    "DATABASE_URL=postgresql://localhost/quantx_dev\nREDIS_URL=redis://localhost:6379\nINFLUXDB_HOST=http://localhost:8181\nINFLUXDB_DATABASE=quantx_dev\nPREFECT_API_URL=http://localhost:4200/api\nENABLE_REAL_TRADING=true\n"
  )
  values = module.load_environment(tmp_path, "development")
  assert values["ENABLE_REAL_TRADING"] == "false"
  config.write_text(
    config.read_text().replace("postgresql://localhost", "postgresql://192.168.5.6")
  )
  with pytest.raises(ValueError, match="local service"):
    module.load_environment(tmp_path, "development")


def test_selected_environment_is_required(tmp_path):
  root = Path(__file__).resolve().parents[2]
  spec = importlib.util.spec_from_file_location(
    "runtime_config", root / "ops/runtime_config.py"
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  with pytest.raises(ValueError, match="required"):
    module.load_environment(tmp_path, "production")


def test_production_resolves_wsl_endpoints_without_changing_credentials(
  tmp_path, monkeypatch
):
  root = Path(__file__).resolve().parents[2]
  spec = importlib.util.spec_from_file_location(
    "runtime_config", root / "ops/runtime_config.py"
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  directory = tmp_path / "apps/api"
  directory.mkdir(parents=True)
  config = directory / ".env.production"
  config.write_text(
    "QUANTX_EXTERNAL_DEPENDENCY_HOST=wsl\nDATABASE_URL=postgresql://user:p%40ss@127.0.0.1:5432/quantx\nREDIS_URL=redis://127.0.0.1:6379/0\nINFLUXDB_HOST=http://127.0.0.1:8181\nPREFECT_API_URL=http://127.0.0.1:4200/api\n"
  )
  monkeypatch.setattr(module.sys, "platform", "win32")
  monkeypatch.setattr(
    module.subprocess,
    "run",
    lambda *a, **k: SimpleNamespace(stdout="2: eth0 inet 172.26.114.93/20"),
  )
  values = module.load_environment(tmp_path, "production")
  assert values["DATABASE_URL"] == "postgresql://user:p%40ss@172.26.114.93:5432/quantx"
  assert values["REDIS_HOST"] == "172.26.114.93"
  assert values["PREFECT_API_URL"] == "http://172.26.114.93:4200/api"
  monkeypatch.setattr(
    module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="")
  )
  with pytest.raises(ValueError, match="unavailable"):
    module.load_environment(tmp_path, "production")


def test_production_migration_revokes_only_development_sessions():
  import sqlite3

  root = Path(__file__).resolve().parents[2]
  spec = importlib.util.spec_from_file_location(
    "migrate_config", root / "ops/migrate_production_config.py"
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  with sqlite3.connect(":memory:") as db:
    db.executescript("""
      CREATE TABLE auth_device_sessions (id TEXT, revoked_at TEXT);
      CREATE TABLE auth_audit_events (device_session_id TEXT, event_type TEXT, outcome TEXT);
      INSERT INTO auth_device_sessions VALUES ('dev',NULL),('password',NULL),('failed',NULL);
      INSERT INTO auth_audit_events VALUES
        ('dev','DEVELOPMENT_LOGIN','SUCCEEDED'),
        ('password','LOGIN','SUCCEEDED'),
        ('failed','DEVELOPMENT_LOGIN','DENIED');
    """)
    assert db.execute(module.REVOKE_DEVELOPMENT_SESSIONS_SQL).rowcount == 1
    assert db.execute(
      "SELECT id FROM auth_device_sessions WHERE revoked_at IS NOT NULL"
    ).fetchall() == [("dev",)]
    assert db.execute(module.REVOKE_DEVELOPMENT_SESSIONS_SQL).rowcount == 0
