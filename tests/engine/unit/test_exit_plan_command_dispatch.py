from types import SimpleNamespace

import pytest
import quantx_engine.command_processor as command_processor


class _ExitPlanService:
  def __init__(self, _manager) -> None:
    self.calls = []

  async def create_manual_exit_plan(self, payload, *, command_id: str):
    self.calls.append(("create", payload, command_id))
    return SimpleNamespace(
      plan_id="manual-position:command-create",
      strategy_run_id="run-create",
      config_version=1,
    )

  async def update_manual_exit_plan(self, payload, *, command_id: str):
    self.calls.append(("update", payload, command_id))
    return SimpleNamespace(
      plan_id="manual-position:command-create",
      strategy_run_id="run-update",
      config_version=2,
    )

  async def set_enabled(
    self,
    plan_id,
    enabled,
    *,
    account_id,
    config_version,
    command_id,
  ):
    self.calls.append(
      (
        "set_enabled",
        plan_id,
        enabled,
        account_id,
        config_version,
        command_id,
      )
    )
    return SimpleNamespace(plan_id=plan_id, config_version=config_version)


@pytest.mark.asyncio
async def test_manual_exit_plan_commands_forward_durable_command_id(monkeypatch):
  service = _ExitPlanService(None)
  monkeypatch.setattr(
    command_processor,
    "AutoExitPlanService",
    lambda manager=None: service,
  )
  create_payload = {"account_id": "acct-1", "instrument_code": "600000.SH"}
  update_payload = {
    "account_id": "acct-1",
    "plan_id": "manual-position:command-create",
    "config_version": 1,
  }

  created = await command_processor._dispatch(
    "EXIT_PLAN_CREATE_MANUAL",
    create_payload,
    command_id="command-create",
  )
  updated = await command_processor._dispatch(
    "EXIT_PLAN_UPDATE_MANUAL",
    update_payload,
    command_id="command-update",
  )

  assert service.calls == [
    ("create", create_payload, "command-create"),
    ("update", update_payload, "command-update"),
  ]
  assert created == {
    "plan_id": "manual-position:command-create",
    "run_id": "run-create",
    "config_version": 1,
  }
  assert updated == {
    "plan_id": "manual-position:command-create",
    "run_id": "run-update",
    "config_version": 2,
  }


@pytest.mark.asyncio
async def test_managed_exit_enable_command_forwards_durable_command_id(monkeypatch):
  service = _ExitPlanService(None)
  monkeypatch.setattr(
    command_processor,
    "AutoExitPlanService",
    lambda manager=None: service,
  )

  result = await command_processor._dispatch(
    "EXIT_PLAN_SET_ENABLED",
    {
      "plan_id": "manual-plan-1",
      "enabled": True,
      "account_id": "acct-1",
      "config_version": 2,
    },
    command_id="command-enable",
  )

  assert service.calls == [
    (
      "set_enabled",
      "manual-plan-1",
      True,
      "acct-1",
      2,
      "command-enable",
    )
  ]
  assert result == {"plan_id": "manual-plan-1", "config_version": 2}
