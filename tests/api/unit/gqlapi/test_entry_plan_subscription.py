from __future__ import annotations

from types import SimpleNamespace

import pytest
from quantx_api.gqlapi.schemas import entry_plan_schema


@pytest.mark.asyncio
async def test_entry_plan_subscription_emits_initial_and_changed_projection_only(
  monkeypatch,
):
  plans = iter(["plan-v1", "plan-v1", "plan-v2"])

  async def get_plan(_account_id: str, _plan_id: str):
    return next(plans)

  async def stream(*_args, **_kwargs):
    yield {"event_type": "LOG"}
    yield {"event_type": "HEARTBEAT"}

  monkeypatch.setattr(entry_plan_schema, "_single_entry_account", lambda _info: "A")
  monkeypatch.setattr(entry_plan_schema.EntryPlanResolver, "get", get_plan)
  monkeypatch.setattr(entry_plan_schema.runtime_subscription_bridge, "stream", stream)

  subscription = entry_plan_schema.EntryPlanSubscription().entry_plan_updated(
    object(), "plan-1"
  )
  assert await anext(subscription) == "plan-v1"
  assert await anext(subscription) == "plan-v2"


@pytest.mark.asyncio
async def test_entry_intent_subscription_is_plan_scoped(monkeypatch):
  projections = iter(
    [
      [SimpleNamespace(plan_id="plan-1"), SimpleNamespace(plan_id="other")],
      [SimpleNamespace(plan_id="plan-1")],
      [],
    ]
  )

  async def get_plan(_account_id: str, _plan_id: str):
    return "owned-plan"

  async def pending(_account_id: str):
    return next(projections)

  async def stream(*_args, **_kwargs):
    yield {"event_type": "LOG"}
    yield {"event_type": "HEARTBEAT"}

  monkeypatch.setattr(entry_plan_schema, "_single_entry_account", lambda _info: "A")
  monkeypatch.setattr(entry_plan_schema.EntryPlanResolver, "get", get_plan)
  monkeypatch.setattr(
    entry_plan_schema.EntryPlanResolver,
    "pending_intents",
    pending,
  )
  monkeypatch.setattr(entry_plan_schema.runtime_subscription_bridge, "stream", stream)

  subscription = entry_plan_schema.EntryPlanSubscription().entry_intent_updated(
    object(), "plan-1"
  )
  initial = await anext(subscription)
  assert [str(intent.plan_id) for intent in initial] == ["plan-1"]
  assert await anext(subscription) == []
