from types import SimpleNamespace

import pytest
from quantx_api.gqlapi.types import liquidation_types
from quantx_api.gqlapi.types.liquidation_types import (
  ExitPlanView,
  _exit_plan_execution_owner,
)


def _plan_state(
  managed_runtime_command_id=None,
  *,
  error_message=None,
  source_type="MANUAL_POSITION",
  strategy_run_id=None,
):
  metadata = {}
  if managed_runtime_command_id is not None:
    metadata["managed_runtime_command_id"] = managed_runtime_command_id
  state = {
    "template": {
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "metadata": metadata,
      "plan_id": "plan-1",
      "rules": [],
      "run_id": strategy_run_id,
      "source_type": source_type,
    }
  }
  if error_message is not None:
    state["error_message"] = error_message
  return state


@pytest.mark.parametrize(
  ("source_type", "strategy_run_id", "managed_runtime_command_id", "expected"),
  [
    ("MANUAL_POSITION", None, None, "EXIT_PLAN_MONITOR"),
    ("MANUAL_LIQUIDATION", None, None, "EXIT_PLAN_MONITOR"),
    ("T_TRADE_BATCH", "t-run-1", None, "STRATEGY_RUNTIME"),
    ("LIMIT_UP_BOARD", "board-run-1", None, "STRATEGY_RUNTIME"),
    (
      "FIRST_BOARD_PROMOTION_V2",
      "first-board-run-1",
      None,
      "STRATEGY_RUNTIME",
    ),
    ("ENTRY_PLAN", "entry-run-1", None, "STRATEGY_RUNTIME"),
    (
      "MANUAL_POSITION",
      "managed-exit-run-1",
      "create-command-1",
      "INVALID_OWNER",
    ),
  ],
)
def test_exit_plan_execution_owner_accepts_only_positive_matrix(
  source_type,
  strategy_run_id,
  managed_runtime_command_id,
  expected,
):
  model = SimpleNamespace(
    account_id="account-1",
    instrument_code="600000.SH",
    plan_id="plan-1",
    plan_state=_plan_state(
      managed_runtime_command_id,
      source_type=source_type,
      strategy_run_id=strategy_run_id,
    ),
    source_type=source_type,
    strategy_run_id=strategy_run_id,
  )

  assert _exit_plan_execution_owner(model) == expected


@pytest.mark.parametrize(
  ("source_type", "strategy_run_id", "managed_runtime_command_id"),
  [
    ("T_TRADE_BATCH", None, None),
    ("LIMIT_UP_BOARD", None, None),
    ("FIRST_BOARD_PROMOTION_V2", None, None),
    ("ENTRY_PLAN", None, None),
    ("MANUAL_LIQUIDATION", "unexpected-run-1", None),
    ("TAKE_PROFIT", None, None),
    ("UNKNOWN_SOURCE", "unknown-run-1", None),
    (None, None, None),
  ],
)
def test_exit_plan_execution_owner_rejects_mismatches_and_unknown_sources(
  source_type,
  strategy_run_id,
  managed_runtime_command_id,
):
  model = SimpleNamespace(
    account_id="account-1",
    instrument_code="600000.SH",
    plan_id="plan-1",
    plan_state=_plan_state(
      managed_runtime_command_id,
      source_type=source_type,
      strategy_run_id=strategy_run_id,
    ),
    source_type=source_type,
    strategy_run_id=strategy_run_id,
  )

  assert _exit_plan_execution_owner(model) == "INVALID_OWNER"


def _view_model(*, source_type, strategy_run_id, managed_runtime_command_id=None):
  return SimpleNamespace(
    account_id="account-1",
    auto_exit_authorized=False,
    bucket="manual",
    capacity_error=None,
    capacity_status="READY",
    completion_strategy=None,
    config_version=1,
    cost_basis_snapshot={},
    created_at=None,
    data_quality="PRICE_UNAVAILABLE",
    enabled=True,
    entry_avg_price=10.0,
    execution_mode="paper",
    exited_volume=0,
    group_id=None,
    instrument_code="600000.SH",
    last_decision=None,
    last_error=None,
    last_evaluated_at=None,
    peak_drawdown_pct=0.0,
    peak_price=0.0,
    pending_client_order_id=None,
    phase="WAITING_ARM",
    plan_id="plan-1",
    plan_state=_plan_state(
      managed_runtime_command_id,
      source_type=source_type,
      strategy_run_id=strategy_run_id,
    ),
    protected_volume=100,
    remaining_volume=100,
    source_id="source-1",
    source_type=source_type,
    status="ACTIVE",
    strategy_run_id=strategy_run_id,
    trailing_floor_pct=None,
    updated_at=None,
  )


@pytest.mark.parametrize(
  ("source_type", "strategy_run_id", "managed_runtime_command_id", "can_edit"),
  [
    ("MANUAL_POSITION", "managed-run-1", "create-command-1", False),
    ("MANUAL_POSITION", None, None, True),
    ("MANUAL_POSITION", None, "orphaned-create-command-1", True),
    ("T_TRADE_BATCH", "t-run-1", None, False),
  ],
)
def test_exit_plan_rules_are_editable_only_for_monitor_owned_manual_plan(
  source_type,
  strategy_run_id,
  managed_runtime_command_id,
  can_edit,
  monkeypatch,
):
  monkeypatch.setattr(
    liquidation_types,
    "ExitPlanView",
    lambda **fields: SimpleNamespace(**fields),
  )
  view = ExitPlanView.from_model(
    _view_model(
      source_type=source_type,
      strategy_run_id=strategy_run_id,
      managed_runtime_command_id=managed_runtime_command_id,
    )
  )

  assert view.can_edit_rules is can_edit


def test_repaired_quarantine_projects_an_actionable_rebuild_instead_of_resume(
  monkeypatch,
):
  monkeypatch.setattr(
    liquidation_types,
    "ExitPlanView",
    lambda **fields: SimpleNamespace(**fields),
  )
  model = _view_model(
    source_type="MANUAL_POSITION",
    strategy_run_id=None,
  )
  model.enabled = False
  model.status = "PAUSED"
  model.last_error = "MARKET_DATA_STALE"
  model.plan_state = _plan_state(
    error_message="QUARANTINE_REPAIRED:intent-old",
    source_type="MANUAL_POSITION",
    strategy_run_id=None,
  )

  view = ExitPlanView.from_model(model)

  assert view.enabled is False
  assert view.status == "ERROR"
  assert view.can_edit_rules is False
  assert view.last_error == "QUARANTINE_REPAIRED:intent-old"
  assert view.recovery_action == "CANCEL_AND_REBUILD"
  assert view.recovery_message == (
    "隔离委托已完成券商事实修复。旧计划不能恢复；请取消旧计划，"
    "再按最新持仓重新创建并授权。"
  )


def test_exit_plan_view_requires_reconciliation_before_rebuild(
  monkeypatch,
) -> None:
  monkeypatch.setattr(
    liquidation_types,
    "ExitPlanView",
    lambda **fields: SimpleNamespace(**fields),
  )
  model = _view_model(
    source_type="MANUAL_POSITION",
    strategy_run_id=None,
  )
  model.enabled = False
  model.status = "ERROR"
  model.last_error = "ACCOUNT_WIDE_STALE_SELL:intent-old"
  model.plan_state = _plan_state(
    error_message="ACCOUNT_WIDE_STALE_SELL:intent-old",
    source_type="MANUAL_POSITION",
    strategy_run_id=None,
  )

  view = ExitPlanView.from_model(model)

  assert view.recovery_action == "COMPLETE_RECONCILIATION"
  assert view.recovery_message == (
    "计划存在尚未解除的券商事实隔离，不能恢复、立即检查或修改；"
    "请先完成账户对账，再取消旧计划并按最新持仓重建。"
  )
