from types import SimpleNamespace

import pytest
from quantx_api.gqlapi.resolvers import liquidation as resolver_module
from quantx_api.gqlapi.resolvers.liquidation import LiquidationResolver


class CapacityDb:
  async def scalar(self, _statement):
    return SimpleNamespace(
      volume=500,
      can_use_volume=500,
      frozen_volume=0,
    )


class CapacityRepository:
  def __init__(self, _db):
    pass

  async def find_reserving(self, **_kwargs):
    return [
      SimpleNamespace(
        plan_id="plan-1",
        source_type="MANUAL_POSITION",
        status="ACTIVE",
        remaining_volume=300,
        pending_client_order_id=None,
        capacity_status="RECONCILE_REQUIRED",
        capacity_error="持仓容量已恢复，仍需显式重新对账后才能继续卖出",
      )
    ]


@pytest.mark.asyncio
async def test_capacity_query_keeps_explicit_reconciliation_gate(monkeypatch):
  async def fake_get_async_db():
    yield CapacityDb()

  monkeypatch.setattr(resolver_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    resolver_module,
    "AutoExitPlanRepository",
    CapacityRepository,
  )

  result = await LiquidationResolver.get_exit_plan_holding_capacity(
    "account-a",
    "600000.sh",
  )

  assert result.total_volume == 500
  assert result.protected_volume == 300
  assert result.capacity_status == "RECONCILE_REQUIRED"
  assert "显式重新对账" in str(result.capacity_error)
