from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.enums import StrategyRunMode
from quantx_engine import strategy_executor
from quantx_infrastructure.models.agent_runtime import (
  TradeCommandOutbox,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.services.t_legacy_drain_guard import (
  legacy_t_drain_event_id,
  legacy_t_entry_is_draining,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from sqlalchemy import func, select

from tests.engine.test_entry_plan_broker_zero_fill_reconciliation import _database


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["ENTRY", ""])
@pytest.mark.parametrize("marker_state", ["valid", "missing", "conflicting"])
async def test_runtime_restores_fence_and_public_dispatch_rechecks_database(
  monkeypatch, marker_state, role
):
  engine, sessions = await _database(monkeypatch)
  async with engine.begin() as connection:
    await connection.run_sync(lambda sync: TTradeGlobalConfig.__table__.create(sync))
    await connection.run_sync(lambda sync: TTradeRolloutEvent.__table__.create(sync))
  async with sessions() as db, db.begin():
    db.add(
      TTradeGlobalConfig(id="head", account_id="account-1", strategy_run_id="legacy")
    )
    if marker_state != "missing":
      db.add(
        TTradeRolloutEvent(
          event_id=legacy_t_drain_event_id("legacy"),
          account_id="account-1",
          event_type="LEGACY_T_DRAIN_STARTED",
          next_stage="DRAINING",
          details={"run_id": "legacy" if marker_state == "valid" else "other"},
          created_at=datetime.now(UTC).replace(tzinfo=None),
        )
      )
  monkeypatch.setattr(strategy_executor, "AsyncSessionLocal", sessions)
  executor = strategy_executor.StrategyExecutor.__new__(
    strategy_executor.StrategyExecutor
  )
  runtime = SimpleNamespace(
    run_id="legacy",
    strategy=None,
    strategy_class=SimpleNamespace(USES_T_TRADE_OPPORTUNITY_PROFILE=True),
    context=SimpleNamespace(
      run_id="legacy", mode=StrategyRunMode.LIVE, parameters={"account_id": "account-1"}
    ),
    t_trade_intent_emission_by_instrument={"600000.SH": {"allowed": True}},
    legacy_t_draining=False,
  )
  try:
    if marker_state == "conflicting":
      with pytest.raises(ValueError, match="LEGACY_T_DRAIN_MARKER_CONFLICT"):
        await executor._refresh_legacy_t_drain(runtime)
    else:
      await executor._refresh_legacy_t_drain(runtime)
      assert runtime.legacy_t_draining is (marker_state == "valid")
      snapshot = executor._build_t_trade_intent_emission_snapshot(
        runtime,
        ["600000.SH"],
        {
          "600000.SH": {
            "eligible": True,
            "account_id": "account-1",
            "run_id": "legacy",
          },
        },
      )
      assert snapshot["600000.SH"]["allowed"] is (marker_state == "missing")
    assert runtime.t_trade_intent_emission_by_instrument == {}
    async with sessions() as db, db.begin():
      if marker_state == "missing":
        assert not await legacy_t_entry_is_draining(
          db, account_id="account-1", run_id="legacy", lock_head=True
        )
      else:
        service = TradeCommandService(db)
        # No device/platform/authorization boundary should be reached for a
        # new BUY once its durable old-owner fence exists.
        service._require_live_authorization = AsyncMock(
          side_effect=AssertionError("must stop before authorization")
        )
        with pytest.raises((AgentUnavailableError, ValueError), match="LEGACY_T_"):
          await service.enqueue_order_for_account(
            account_id="account-1",
            instrument_code="600000.SH",
            side="BUY",
            order_type="FIX_PRICE",
            limit_price=Decimal("10"),
            volume=100,
            execution_ref=ExecutionOwnerRef("STRATEGY_RUN", "legacy"),
            environment=ExecutionEnvironment.LIVE,
            idempotency_key="new-entry",
            strategy_run_id="legacy",
            strategy_order_id="order",
            intent_id="intent",
            batch_id="batch",
            t_trade_role=role,
          )
        service._require_live_authorization.assert_not_awaited()
      assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0
  finally:
    await engine.dispose()
