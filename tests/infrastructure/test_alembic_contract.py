from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "packages" / "infrastructure" / "alembic" / "versions"


def _load_revision(filename: str, module_name: str) -> ModuleType:
  path = VERSIONS / filename
  spec = importlib.util.spec_from_file_location(module_name, path)
  assert spec is not None
  assert spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_baseline_metadata_is_fingerprint_locked() -> None:
  revision_path = (VERSIONS / "20260729_0001_production_baseline.py").as_posix()
  script = (
    "import importlib.util;"
    f"spec=importlib.util.spec_from_file_location('baseline',{revision_path!r});"
    "module=importlib.util.module_from_spec(spec);"
    "spec.loader.exec_module(module);"
    "print(module.metadata_sha256());"
    "print(module.EXPECTED_METADATA_SHA256)"
  )
  result = subprocess.run(
    [sys.executable, "-c", script],
    cwd=ROOT,
    check=True,
    capture_output=True,
    text=True,
  )
  actual, expected = result.stdout.splitlines()

  assert actual == expected
  assert len(expected) == 64


def test_baseline_clone_excludes_schema_owned_by_later_revisions() -> None:
  revision = _load_revision(
    "20260729_0001_production_baseline.py",
    "quantx_test_baseline_clone",
  )

  metadata = revision._baseline_metadata()
  assert not (set(metadata.tables) & revision.POST_BASELINE_TABLES)
  for table_name, column_names in revision.POST_BASELINE_COLUMNS.items():
    table = metadata.tables[table_name]
    assert not (set(table.c.keys()) & column_names)
    assert all(
      not (
        {str(getattr(expression, "name", "")) for expression in index.expressions}
        & column_names
      )
      for index in table.indexes
    )


def test_live_safety_revision_is_additive_and_downgrade_is_refused() -> None:
  revision = _load_revision(
    "20260729_0002_live_safety.py",
    "quantx_test_live_safety_revision",
  )

  assert revision.down_revision == "20260729_0001"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_asyncpg_trigger_ddl_is_split_into_single_commands() -> None:
  source = (VERSIONS / "20260729_0002_live_safety.py").read_text(encoding="utf-8")

  assert "ON pending_trade_orders;\n      CREATE TRIGGER" not in source
  assert source.count("op.execute(") >= 3


def test_all_relational_tables_have_chinese_comments() -> None:
  import quantx_infrastructure.models  # noqa: F401
  from quantx_infrastructure.database.relational_base import Base
  from quantx_infrastructure.models.divid_factor import DividFactorTable
  from quantx_infrastructure.models.table_comments import TABLE_COMMENTS

  assert DividFactorTable.__table__.name == "divid_factors"
  assert set(Base.metadata.tables) == set(TABLE_COMMENTS)
  for table in Base.metadata.tables.values():
    assert table.comment == TABLE_COMMENTS[table.name]
    assert re.search(r"[\u4e00-\u9fff]", table.comment)


def test_table_comment_revision_covers_metadata_and_refuses_downgrade(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import quantx_infrastructure.models  # noqa: F401
  from quantx_infrastructure.database.relational_base import Base
  from quantx_infrastructure.models.divid_factor import DividFactorTable

  revision = _load_revision(
    "20260730_0003_table_comments.py",
    "quantx_test_table_comment_revision",
  )
  calls: list[tuple[str, str]] = []
  statements: list[str] = []
  monkeypatch.setattr(
    revision.op,
    "create_table_comment",
    lambda table_name, comment: calls.append((table_name, comment)),
  )
  monkeypatch.setattr(revision.op, "execute", statements.append)

  assert revision.down_revision == "20260729_0002"
  assert DividFactorTable.__table__.name == "divid_factors"
  assert set(revision.TABLE_COMMENTS) <= set(Base.metadata.tables)
  revision.upgrade()
  assert calls == list(revision.REQUIRED_TABLE_COMMENTS.items())
  assert len(statements) == len(revision.OPTIONAL_TABLE_COMMENTS)
  assert "divid_factors" in statements[0]
  assert revision.OPTIONAL_TABLE_COMMENTS["divid_factors"] in statements[0]
  # The T-assistant rollout table is relabelled by revision 0033 after its
  # account-wide execution fields are moved to a dedicated table.
  later_comment_overrides = {"account_trading_rollouts"}
  for table_name, comment in revision.TABLE_COMMENTS.items():
    if table_name in later_comment_overrides:
      continue
    assert Base.metadata.tables[table_name].comment == comment
    assert re.search(r"[\u4e00-\u9fff]", comment)
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_roe_quality_revision_follows_financial_sync_and_refuses_downgrade() -> None:
  revision = _load_revision(
    "20260812_0006_roe_quality_audits.py",
    "quantx_test_roe_quality_revision",
  )

  assert revision.down_revision == "20260810_0005"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_controlled_live_window_revision_follows_roe_quality() -> None:
  revision = _load_revision(
    "20260813_0007_controlled_live_window.py",
    "quantx_test_controlled_live_window_revision",
  )

  assert revision.down_revision == "20260812_0006"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_sell_management_revision_follows_exit_and_board_revisions() -> None:
  auto_exit = _load_revision(
    "20260813_0008_auto_exit_plans.py",
    "quantx_test_auto_exit_plans",
  )
  board = _load_revision(
    "20260813_0009_limit_up_board_assistant.py",
    "quantx_test_limit_up_board_assistant",
  )
  sell_management = _load_revision(
    "20260813_0010_sell_management.py",
    "quantx_test_sell_management",
  )

  assert auto_exit.down_revision == "20260813_0007"
  assert board.down_revision == "20260813_0008"
  assert sell_management.down_revision == "20260813_0009"
  with pytest.raises(RuntimeError, match="downgrades"):
    sell_management.downgrade()


def test_limit_up_board_assistant_revision_is_reversible() -> None:
  revision = _load_revision(
    "20260813_0009_limit_up_board_assistant.py",
    "quantx_test_limit_up_board_assistant_revision",
  )
  source = (VERSIONS / "20260813_0009_limit_up_board_assistant.py").read_text(
    encoding="utf-8"
  )

  assert revision.down_revision == "20260813_0008"
  assert "RADAR_CANDIDATES" in source
  assert "RENAME TO strategy_instrument_universe_mode_with_radar" in source
  assert "DROP TYPE strategy_instrument_universe_mode_with_radar" in source


def test_ai_assistant_revision_follows_sell_management_and_refuses_downgrade() -> None:
  revision = _load_revision(
    "20260814_0011_ai_assistant.py",
    "quantx_test_ai_assistant_revision",
  )
  source = (VERSIONS / "20260814_0011_ai_assistant.py").read_text(encoding="utf-8")

  assert revision.down_revision == "20260813_0010"
  assert "existing_assistant_tables == ASSISTANT_TABLES" in source
  assert "Partial AI assistant schema detected" in source
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_first_board_revision_follows_ai_assistant_and_refuses_downgrade() -> None:
  revision = _load_revision(
    "20260814_0012_first_board_promotion.py",
    "quantx_test_first_board_revision",
  )

  assert revision.down_revision == "20260814_0011"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_first_board_release_gate_follows_market_facts_and_refuses_downgrade() -> None:
  revision = _load_revision(
    "20260814_0013_first_board_model_release.py",
    "quantx_test_first_board_release_gate_revision",
  )

  assert revision.down_revision == "20260814_0012"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_ai_runtime_settings_revision_follows_first_board_release() -> None:
  revision = _load_revision(
    "20260814_0014_ai_runtime_settings.py",
    "quantx_test_ai_runtime_settings_revision",
  )

  assert revision.down_revision == "20260814_0013"


def test_trade_confirmation_revision_follows_ai_runtime_and_refuses_downgrade() -> None:
  revision = _load_revision(
    "20260815_0015_trade_confirmation_challenges.py",
    "quantx_test_trade_confirmation_challenge_revision",
  )

  assert revision.down_revision == "20260814_0014"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_trade_confirmation_revision_validates_preexisting_schema() -> None:
  revision = _load_revision(
    "20260815_0015_trade_confirmation_challenges.py",
    "quantx_test_trade_confirmation_existing_schema",
  )

  class Inspector:
    def __init__(self, *, columns=None, uniques=None):
      self.columns = columns or [
        {
          "name": name,
          "nullable": name in {"consumed_at", "result_reference"},
        }
        for name in revision._REQUIRED_COLUMNS
      ]
      self.uniques = uniques or [
        {
          "name": "uq_trade_confirmation_challenge_idempotency",
          "column_names": [
            "user_id",
            "account_id",
            "action",
            "idempotency_key",
          ],
        }
      ]

    def get_columns(self, _table_name):
      return self.columns

    def get_unique_constraints(self, _table_name):
      return self.uniques

  revision._validate_existing_schema(Inspector())

  missing = Inspector()
  missing.columns = [
    column for column in missing.columns if column["name"] != "token_digest"
  ]
  with pytest.raises(RuntimeError, match="Partial.*missing columns=token_digest"):
    revision._validate_existing_schema(missing)

  nullable = Inspector()
  next(column for column in nullable.columns if column["name"] == "payload")[
    "nullable"
  ] = True
  with pytest.raises(RuntimeError, match="nullable required columns=payload"):
    revision._validate_existing_schema(nullable)

  no_unique = Inspector(uniques=[])
  no_unique.uniques = []
  with pytest.raises(RuntimeError, match="missing user/account/action/idempotency"):
    revision._validate_existing_schema(no_unique)


def test_graphql_write_permission_migration_is_complete_and_irreversible() -> None:
  revision = _load_revision(
    "20260816_0020_graphql_write_permissions.py",
    "quantx_test_graphql_write_permissions_revision",
  )

  assert revision.down_revision == "20260815_0019"
  assert revision.migrate_permissions(["market:read"]) == ["market:read"]
  migrated = revision.migrate_permissions(["mutation:write", "market:read"])
  assert "mutation:write" not in migrated
  assert set(revision.REPLACEMENT_WRITE_PERMISSIONS) <= set(migrated)
  assert revision.migrate_permissions(migrated) == migrated
  legacy_migrated = revision.migrate_permissions(
    [*revision.LEGACY_REPLACEMENT_WRITE_PERMISSIONS, "market:read"]
  )
  assert set(revision.REPLACEMENT_WRITE_PERMISSIONS) <= set(legacy_migrated)
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()
