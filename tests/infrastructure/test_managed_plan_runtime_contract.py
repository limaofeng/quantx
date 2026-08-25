from pathlib import Path

from quantx_infrastructure.models.entry_plan_authorization import (
  EntryPlanAuthorizationGrant,
)
from quantx_infrastructure.models.managed_plan import (
  ManagedPlanConfigRevision,
  ManagedPlanRecord,
)
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.repositories.managed_plan_repository import (
  managed_plan_config_fingerprint,
)


def test_managed_plan_fingerprint_is_canonical_and_sensitive() -> None:
  first = managed_plan_config_fingerprint(
    {"rules": [{"threshold": 12.5}], "instrument_code": "600000.SH"}
  )
  reordered = managed_plan_config_fingerprint(
    {"instrument_code": "600000.SH", "rules": [{"threshold": 12.5}]}
  )
  changed = managed_plan_config_fingerprint(
    {"instrument_code": "600000.SH", "rules": [{"threshold": 12.6}]}
  )

  assert first == reordered
  assert first != changed
  assert len(first) == 64


def test_managed_plan_and_strategy_run_schema_keep_distinct_identities() -> None:
  assert ManagedPlanRecord.__table__.name == "managed_plans"
  assert ManagedPlanConfigRevision.__table__.name == (
    "managed_plan_config_revisions"
  )
  assert {
    "plan_id",
    "plan_kind",
    "plan_config_version",
    "frozen_config_snapshot",
    "frozen_config_fingerprint",
    "supersedes_run_id",
    "parent_run_id",
    "input_event_watermark",
  } <= set(StrategyRun.__table__.columns.keys())
  constraint_names = {
    constraint.name
    for constraint in EntryPlanAuthorizationGrant.__table__.constraints
    if constraint.name
  }
  assert "ck_entry_plan_auth_plan_run" not in constraint_names


def test_managed_plan_migration_is_single_authoritative_cutover() -> None:
  source = Path(
    "packages/infrastructure/alembic/versions/"
    "20260825_0034_managed_plan_runtime.py"
  ).read_text(encoding="utf-8")

  assert 'revision = "20260825_0034"' in source
  assert 'down_revision = "20260825_0033"' in source
  assert '"managed_plans"' in source
  assert '"managed_plan_config_revisions"' in source
  assert '"ck_entry_plan_auth_plan_run"' in source
  assert "op.drop_constraint(" in source
  assert "PLAN_STOPPED_FOR_MANAGED_RUNTIME_MIGRATION" in source
