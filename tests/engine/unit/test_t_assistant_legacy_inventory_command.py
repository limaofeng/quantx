"""Rejected preparations cannot create cutover review evidence."""

from datetime import UTC

import pytest
from quantx_engine.t_assistant_legacy_inventory import dispatch_legacy_inventory
from quantx_infrastructure.models.agent_runtime import (
  EngineCommandOutbox,
  TTradeRolloutEvent,
)

from tests.engine.unit.test_t_assistant_legacy_drain import seed_legacy_drain


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["class", "payload", "head"])
async def test_prepare_rejects_wrong_source_or_command(monkeypatch, damage):
  engine, sessions, now, _ = await seed_legacy_drain(
    monkeypatch, "class" if damage == "class" else None
  )
  try:
    async with engine.begin() as connection:
      await connection.run_sync(lambda sync: EngineCommandOutbox.__table__.create(sync))
    payload = dict(
      account_id="account-1",
      config_id="head",
      run_id="plan-1",
      expected_head_version=2 if damage == "head" else 1,
      actor_id="user-1",
    )
    async with sessions() as db, db.begin():
      db.add(
        EngineCommandOutbox(
          message_id="prepare",
          idempotency_key="prepare",
          command_type="T_ASSISTANT_PREPARE_LEGACY_INVENTORY",
          aggregate_id="plan-1",
          payload={**payload, "actor_id": "other"} if damage == "payload" else payload,
          available_at=now.astimezone(UTC).replace(tzinfo=None),
          processing_status="PENDING",
        )
      )
    with pytest.raises(ValueError, match="LEGACY_T_INVENTORY_"):
      async with sessions() as db, db.begin():
        await dispatch_legacy_inventory(
          db, command_id="prepare", payload=payload, now=now
        )
    async with sessions() as db:
      assert await db.get(TTradeRolloutEvent, "legacy-inventory:prepare") is None
  finally:
    await engine.dispose()
