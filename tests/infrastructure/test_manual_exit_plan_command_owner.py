from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.services import auto_exit_plan_service as service_module
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.exit_plan_execution_owner import (
  durable_exit_plan_source_binding,
)
from quantx_infrastructure.services.exit_plan_scope_lock import LockedExitPlanScope
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def _payload(**overrides):
  payload = {
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "protected_volume": 100,
    "enabled": True,
    "execution_mode": "paper",
    "source_id": "position-7",
    "rules": [
      {
        "rule_id": "stop-1",
        "strategy": "STOP_PRICE",
        "parameters": {"stop_price": 9.5},
      }
    ],
    "cost_basis": {
      "mode": "MANUAL_UNIT_COST",
      "unit_cost_cny": 10.0,
    },
  }
  payload.update(overrides)
  return payload


@pytest.fixture
async def manual_plan_database(monkeypatch: pytest.MonkeyPatch):
  engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
  )
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[AutoExitPlanRecord.__table__, AutoExitPlanEvent.__table__],
      )
    )
  factory = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(service_module, "AsyncSessionLocal", factory)

  async def locked_scope(_db, **_kwargs):
    return LockedExitPlanScope(
      position=SimpleNamespace(
        volume=100,
        can_use_volume=100,
        created_at=None,
      ),
      plans=[],
      target_plan=None,
    )

  monkeypatch.setattr(service_module, "lock_exit_plan_scope", locked_scope)
  yield factory
  await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("command_id", ["", " ", "x" * 129])
async def test_manual_create_rejects_invalid_command_id_before_db_or_lock(
  monkeypatch: pytest.MonkeyPatch,
  command_id: str,
) -> None:
  service = AutoExitPlanService()
  load = Mock(
    side_effect=AssertionError("record lookup must not run")
  )
  lock = Mock(side_effect=AssertionError("scope lock must not run"))
  monkeypatch.setattr(service, "_load_manual_plan_record", load)
  monkeypatch.setattr(service_module, "lock_exit_plan_scope", lock)

  with pytest.raises(ValueError, match="command_id"):
    await service.create_manual_exit_plan(
      _payload(),
      command_id=command_id,
    )

  load.assert_not_called()
  lock.assert_not_called()


@pytest.mark.asyncio
async def test_manual_create_requires_command_id_argument() -> None:
  with pytest.raises(TypeError, match="command_id"):
    await AutoExitPlanService().create_manual_exit_plan(_payload())  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_manual_create_uses_command_owner_and_replays_exactly(
  manual_plan_database,
) -> None:
  service = AutoExitPlanService()
  payload = _payload()
  command_id = "manual-command-7"

  created = await service.create_manual_exit_plan(payload, command_id=command_id)

  assert created.plan_id == service._manual_plan_id_for_command(command_id)
  assert created.source_id == "position-7"
  assert created.source_execution_owner_type == "MANUAL_COMMAND"
  assert created.source_execution_owner_id == command_id
  assert created.environment == "PAPER"
  assert created.source_execution_environment == "PAPER"
  assert durable_exit_plan_source_binding(created) is not None

  replayed = await service.create_manual_exit_plan(payload, command_id=command_id)
  assert replayed.plan_id == created.plan_id
  assert replayed.source_id == "position-7"
  assert replayed.source_execution_owner_id == command_id


@pytest.mark.asyncio
async def test_manual_create_rejects_changed_payload_or_requested_plan_id(
  manual_plan_database,
) -> None:
  service = AutoExitPlanService()
  payload = _payload()
  command_id = "manual-command-8"
  created = await service.create_manual_exit_plan(payload, command_id=command_id)

  with pytest.raises(ValueError, match="EXIT_COMMAND_REPLAY_CONFLICT"):
    await service.create_manual_exit_plan(
      {**payload, "source_id": "position-other"},
      command_id=command_id,
    )

  with pytest.raises(ValueError, match="EXIT_COMMAND_REPLAY_CONFLICT"):
    await service.create_manual_exit_plan(
      {**payload, "plan_id": "manual-position:unrelated"},
      command_id=command_id,
    )

  assert created.source_execution_owner_id == command_id
