import uuid

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.services import auto_exit_plan_service as service_module
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def native_liquidation_database(monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          Position.__table__,
          AutoExitPlanRecord.__table__,
          AutoExitPlanEvent.__table__,
          PendingTradeOrder.__table__,
        ],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(service_module, "AsyncSessionLocal", session_factory)
  yield session_factory
  await engine.dispose()


def _payload(group_id: str) -> dict:
  return {
    "account_id": "ACCOUNT-1",
    "completion_strategy": "UNTIL_SNAPSHOT_CLEARED",
    "conflict_strategy": "UNALLOCATED_ONLY",
    "confirm": True,
    "scope": "SELECTED",
    "requested_scope": "ALL",
    "instrument_codes": ["600000.SH"],
    "execution_mode": "paper",
    "auto_exit_authorized": True,
    "group_id": group_id,
    "authorization_challenge_id": "challenge-1",
    "authorization_snapshot_version": "snapshot-v1",
    "expected_items": [
      {
        "instrument_code": "600000.SH",
        "included": True,
        "max_protected_volume": 300,
        "conflicts": [],
      }
    ],
  }


@pytest.mark.asyncio
async def test_native_group_caps_post_confirmation_position_growth_and_is_idempotent(
  native_liquidation_database,
):
  group_id = str(uuid.uuid4())
  async with native_liquidation_database() as db:
    # The signed snapshot authorized at most 300 shares. A later increase to
    # 700 must not expand the group, and a new symbol must never join scope=ALL.
    db.add(
      Position(
        id="position-1",
        account_id="ACCOUNT-1",
        stock_code="600000.SH",
        instrument_name="浦发银行",
        volume=700,
        can_use_volume=700,
        frozen_volume=0,
        avg_price=10,
      )
    )
    db.add(
      Position(
        id="position-new-after-confirm",
        account_id="ACCOUNT-1",
        stock_code="000001.SZ",
        instrument_name="平安银行",
        volume=500,
        can_use_volume=500,
        frozen_volume=0,
        avg_price=12,
      )
    )
    await db.commit()

  first = await AutoExitPlanService().create_liquidation_group(_payload(group_id))
  assert first["success"]
  assert [item["instrument_code"] for item in first["items"]] == ["600000.SH"]
  assert first["items"][0]["protected_volume"] == 300

  async with native_liquidation_database() as db:
    records = list(
      (
        await db.execute(
          select(AutoExitPlanRecord).where(AutoExitPlanRecord.group_id == group_id)
        )
      )
      .scalars()
      .all()
    )
    assert len(records) == 1
    assert records[0].instrument_code == "600000.SH"
    assert records[0].protected_volume == 300
    assert records[0].auto_exit_authorized
    metadata = records[0].plan_state["template"]["metadata"]
    assert metadata["authorization_challenge_id"] == "challenge-1"
    assert metadata["authorization_snapshot_version"] == "snapshot-v1"
    assert metadata["authorized_max_protected_volume"] == 300

  replay = await AutoExitPlanService().create_liquidation_group(_payload(group_id))
  assert replay["group_id"] == group_id
  assert replay["items"] == first["items"]
  async with native_liquidation_database() as db:
    records = list(
      (
        await db.execute(
          select(AutoExitPlanRecord).where(AutoExitPlanRecord.group_id == group_id)
        )
      )
      .scalars()
      .all()
    )
    assert len(records) == 1


@pytest.mark.asyncio
async def test_native_group_rejects_new_exit_plan_conflict_after_confirmation(
  native_liquidation_database,
):
  group_id = str(uuid.uuid4())
  async with native_liquidation_database() as db:
    db.add(
      Position(
        id="position-conflict",
        account_id="ACCOUNT-1",
        stock_code="600000.SH",
        instrument_name="浦发银行",
        volume=500,
        can_use_volume=500,
        frozen_volume=0,
        avg_price=10,
      )
    )
    db.add(
      AutoExitPlanRecord(
        plan_id="new-conflict-after-confirm",
        account_id="ACCOUNT-1",
        instrument_code="600000.SH",
        bucket="manual",
        source_type="MANUAL_POSITION",
        source_id="new-conflict-after-confirm",
        enabled=True,
        status="ACTIVE",
        execution_mode="paper",
        auto_exit_authorized=False,
        config_version=1,
        protected_volume=100,
        exited_volume=0,
        remaining_volume=100,
        entry_avg_price=10,
        plan_state={},
      )
    )
    await db.commit()

  result = await AutoExitPlanService().create_liquidation_group(_payload(group_id))
  assert not result["success"]
  assert result["items"][0]["success"] is False
  assert "冲突" in result["items"][0]["error"]
  async with native_liquidation_database() as db:
    existing = await db.get(AutoExitPlanRecord, "new-conflict-after-confirm")
    assert existing.enabled
    created = list(
      (
        await db.execute(
          select(AutoExitPlanRecord).where(AutoExitPlanRecord.group_id == group_id)
        )
      )
      .scalars()
      .all()
    )
    assert created == []
