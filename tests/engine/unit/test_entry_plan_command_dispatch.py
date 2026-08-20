from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import quantx_engine.command_processor as command_processor


class _EntryPlanService:
  def __init__(self) -> None:
    self.calls: list[tuple[str, Any]] = []

  async def create(
    self,
    payload: dict[str, Any],
    *,
    command_id: str,
  ) -> dict[str, Any]:
    self.calls.append(("create", (payload, command_id)))
    return {"plan_id": "plan-1", "config_version": 1}

  async def update(
    self,
    payload: dict[str, Any],
    *,
    command_id: str,
  ) -> dict[str, Any]:
    self.calls.append(("update", (payload, command_id)))
    return {"plan_id": "plan-1", "config_version": 2}

  async def set_enabled(
    self,
    plan_id: str,
    enabled: bool,
    **kwargs: Any,
  ) -> dict[str, Any]:
    self.calls.append(("set_enabled", (plan_id, enabled, kwargs)))
    return {"success": True, "code": "ENTRY_PLAN_ARMED"}

  async def cancel(self, plan_id: str, **kwargs: Any) -> dict[str, Any]:
    self.calls.append(("cancel", (plan_id, kwargs)))
    return {"success": True, "code": "ENTRY_PLAN_DRAINING"}

  async def trigger_manual(
    self, plan_id: str, rule_id: str, **kwargs: Any
  ) -> dict[str, Any]:
    self.calls.append(("trigger_manual", (plan_id, rule_id, kwargs)))
    return {"success": True, "code": "ENTRY_PLAN_MANUAL_TRIGGER_QUEUED"}

  async def set_automation_paused(self, **kwargs: Any) -> dict[str, Any]:
    self.calls.append(("set_automation_paused", kwargs))
    return {"account_id": kwargs["account_id"], "paused": kwargs["paused"]}


@pytest.mark.asyncio
async def test_entry_plan_commands_route_to_engine_owned_service(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = _EntryPlanService()
  monkeypatch.setattr(
    command_processor,
    "EntryPlanService",
    lambda manager: service,
  )

  created = await command_processor._dispatch(
    "ENTRY_PLAN_CREATE",
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "input": {"instrument_code": "605499.SH"},
    },
    command_id="command-create",
  )
  updated = await command_processor._dispatch(
    "ENTRY_PLAN_UPDATE",
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "input": {"plan_id": "plan-1", "config_version": 1},
    },
    command_id="command-update",
  )
  enabled = await command_processor._dispatch(
    "ENTRY_PLAN_SET_ENABLED",
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "plan_id": "plan-1",
      "config_version": 1,
      "enabled": True,
    },
  )
  cancelled = await command_processor._dispatch(
    "ENTRY_PLAN_CANCEL",
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "plan_id": "plan-1",
      "config_version": 1,
      "cancel_working_order": True,
    },
  )
  triggered = await command_processor._dispatch(
    "ENTRY_PLAN_TRIGGER_MANUAL",
    {
      "account_id": "acct-1",
      "plan_id": "plan-1",
      "rule_id": "manual-1",
    },
  )
  gate = await command_processor._dispatch(
    "ENTRY_AUTOMATION_SET_PAUSED",
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "paused": True,
      "reason": "USER_REQUESTED",
    },
  )

  assert created == {"plan_id": "plan-1", "config_version": 1}
  assert updated == {"plan_id": "plan-1", "config_version": 2}
  assert enabled["code"] == "ENTRY_PLAN_ARMED"
  assert cancelled["code"] == "ENTRY_PLAN_DRAINING"
  assert triggered["code"] == "ENTRY_PLAN_MANUAL_TRIGGER_QUEUED"
  assert gate == {"account_id": "acct-1", "paused": True}
  assert service.calls == [
    (
      "create",
      (
        {
          "account_id": "acct-1",
          "actor_user_id": "user-1",
          "input": {"instrument_code": "605499.SH"},
        },
        "command-create",
      ),
    ),
    (
      "update",
      (
        {
          "account_id": "acct-1",
          "actor_user_id": "user-1",
          "input": {"plan_id": "plan-1", "config_version": 1},
        },
        "command-update",
      ),
    ),
    (
      "set_enabled",
      (
        "plan-1",
        True,
        {
          "account_id": "acct-1",
          "config_version": 1,
          "actor_user_id": "user-1",
        },
      ),
    ),
    (
      "cancel",
      (
        "plan-1",
        {
          "account_id": "acct-1",
          "config_version": 1,
          "actor_user_id": "user-1",
          "cancel_working_order": True,
        },
      ),
    ),
    (
      "trigger_manual",
      (
        "plan-1",
        "manual-1",
        {"account_id": "acct-1"},
      ),
    ),
    (
      "set_automation_paused",
      {
        "account_id": "acct-1",
        "paused": True,
        "reason": "USER_REQUESTED",
        "actor_user_id": "user-1",
      },
    ),
  ]


@pytest.mark.asyncio
async def test_strategy_approval_command_preserves_device_challenge_audit(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  approve = AsyncMock(
    return_value={"success": False, "code": "ENTRY_PLAN_DEVICE_CHALLENGE_REQUIRED"}
  )
  monkeypatch.setattr(
    command_processor,
    "strategy_manager",
    SimpleNamespace(executor=SimpleNamespace(approve_trade_intent=approve)),
  )
  audit = {
    "actor_id": "user-1",
    "device_session_id": "session-1",
    "challenge_id": "challenge-1",
    "channel": "ENTRY_PLAN_DEVICE_CHALLENGE",
  }

  result = await command_processor._dispatch(
    "STRATEGY_APPROVE_TRADE_INTENT",
    {
      "run_id": "run-1",
      "intent_id": "intent-1",
      "approval_audit": audit,
    },
  )

  assert result["code"] == "ENTRY_PLAN_DEVICE_CHALLENGE_REQUIRED"
  approve.assert_awaited_once_with(
    "run-1",
    "intent-1",
    approval_audit=audit,
  )
