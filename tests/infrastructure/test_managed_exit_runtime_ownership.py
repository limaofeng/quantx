from types import SimpleNamespace

import pytest
from quantx_infrastructure.services.auto_exit_plan_service import (
  AutoExitPlanService,
  is_monitor_owned_exit_plan,
)


def _record(
  *,
  source_type: str,
  run_id: str = "",
  metadata: dict | None = None,
  template_overrides: dict | None = None,
):
  template = {
    "plan_id": "plan-1",
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "source_type": source_type,
    "source_id": "source-1",
    "run_id": run_id,
    "metadata": dict(metadata or {}),
  }
  template.update(dict(template_overrides or {}))
  source_owner_type = (
    "STRATEGY_RUN"
    if run_id and source_type in {"T_TRADE_BATCH", "ENTRY_PLAN"}
    else "STRATEGY_RUN"
    if run_id
    else "MANUAL_COMMAND"
  )
  source_owner_id = run_id or "source-1"
  group_id = "source-1" if source_type == "MANUAL_LIQUIDATION" else None
  return SimpleNamespace(
    plan_id="plan-1",
    account_id="account-1",
    instrument_code="600000.SH",
    source_id="source-1",
    group_id=group_id,
    source_type=source_type,
    strategy_run_id=run_id or None,
    source_execution_owner_type=source_owner_type,
    source_execution_owner_id=source_owner_id,
    source_execution_environment="LIVE",
    environment="LIVE",
    plan_state={"template": template},
    last_error=None,
  )


@pytest.mark.parametrize("source_type", ["MANUAL_POSITION", "MANUAL_LIQUIDATION"])
def test_manual_plan_without_run_is_monitor_owned(source_type: str) -> None:
  record = _record(source_type=source_type)
  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: None))

  assert is_monitor_owned_exit_plan(record)
  assert service._strategy_owner_kind(record) == "MONITOR"


@pytest.mark.parametrize("command_id", ["managed-command", ""])
def test_legacy_marker_never_hides_unbound_manual_plan(command_id: str) -> None:
  record = _record(
    source_type="MANUAL_POSITION",
    metadata={"managed_runtime_command_id": command_id},
  )
  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: None))

  assert is_monitor_owned_exit_plan(record)
  assert service._strategy_owner_kind(record) == "MONITOR"


@pytest.mark.parametrize("metadata", [{}, {"managed_runtime_command_id": "x"}])
def test_manual_plan_bound_to_run_requires_migration(metadata: dict) -> None:
  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: None))

  with pytest.raises(RuntimeError, match="必须先完成迁移"):
    service._strategy_owner_kind(
      _record(
        source_type="MANUAL_POSITION",
        run_id="legacy-managed-run",
        metadata=metadata,
      )
    )


def test_runtime_book_remains_owner_of_strategy_plan() -> None:
  class RuntimeBookOwner:
    OWNS_RUNTIME_EXIT_PLAN_BOOK = True

  runtime = SimpleNamespace(strategy_class=RuntimeBookOwner, strategy=None)
  service = AutoExitPlanService(
    SimpleNamespace(get_run=lambda run_id: runtime if run_id == "run-1" else None)
  )
  record = _record(source_type="T_TRADE_BATCH", run_id="run-1")

  assert not is_monitor_owned_exit_plan(record)
  assert service._strategy_owner_kind(record) == "RUNTIME_BOOK"


@pytest.mark.parametrize("command_id", ["managed-command", ""])
def test_runtime_book_rejects_any_managed_command_marker(command_id: str) -> None:
  class RuntimeBookOwner:
    OWNS_RUNTIME_EXIT_PLAN_BOOK = True

  runtime = SimpleNamespace(strategy_class=RuntimeBookOwner, strategy=None)
  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: runtime))

  with pytest.raises(RuntimeError, match="未知执行所有者"):
    service._strategy_owner_kind(
      _record(
        source_type="T_TRADE_BATCH",
        run_id="run-1",
        metadata={"managed_runtime_command_id": command_id},
      )
    )


def test_manual_plan_bound_to_unknown_run_fails_closed() -> None:
  runtime = SimpleNamespace(strategy_class=object, strategy=None)
  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: runtime))

  with pytest.raises(RuntimeError, match="必须先完成迁移"):
    service._strategy_owner_kind(
      _record(
        source_type="MANUAL_POSITION",
        run_id="legacy-run",
        metadata={"managed_runtime_command_id": "managed-command"},
      )
    )


@pytest.mark.parametrize("source_type", ["T_TRADE_BATCH", "ENTRY_PLAN"])
def test_strategy_source_without_run_fails_closed(source_type: str) -> None:
  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: None))

  with pytest.raises(RuntimeError, match="缺少 StrategyRun"):
    service._strategy_owner_kind(_record(source_type=source_type))


@pytest.mark.parametrize(
  ("template_field", "template_value"),
  [
    ("plan_id", "other-plan"),
    ("account_id", "other-account"),
    ("instrument_code", "000001.SZ"),
    ("source_type", "T_TRADE_BATCH"),
    ("run_id", "other-run"),
  ],
)
def test_projection_template_identity_drift_is_never_monitor_owned(
  template_field: str,
  template_value: str,
) -> None:
  record = _record(
    source_type="MANUAL_POSITION",
    template_overrides={template_field: template_value},
  )
  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: None))

  assert not is_monitor_owned_exit_plan(record)
  with pytest.raises(RuntimeError, match="持久化身份不一致"):
    service._strategy_owner_kind(record)
