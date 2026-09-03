from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_domain.brokers.simulator import SimulatorBroker
from quantx_domain.strategies.base import StrategyRunMode
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.strategy_run_state import (
  StrategyRunPosition,
  StrategyRunState,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.entry_plan_service import EntryPlanService
from quantx_infrastructure.services.exit_plan_scope_lock import (
  lock_exit_plan_scope_for_plan,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def environment_database():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(
        db,
        tables=[
          AccountExecutionControl.__table__,
          Position.__table__,
          AutoExitPlanRecord.__table__,
          AutoExitPlanEvent.__table__,
          StrategyRunPosition.__table__,
          StrategyRunState.__table__,
        ],
      )
    )
  yield async_sessionmaker(engine, expire_on_commit=False)
  await engine.dispose()


def plan(key, mode, run, volume):
  environment = mode.upper()
  return AutoExitPlanRecord(
    plan_id=key,
    source_id=key,
    source_type="ENTRY_PLAN",
    account_id="account",
    instrument_code="600000.SH",
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id=run,
    source_execution_environment=environment,
    environment=environment,
    strategy_run_id=run,
    enabled=True,
    status="ACTIVE",
    protected_volume=volume,
    remaining_volume=volume,
    entry_avg_price=10,
    plan_state={
      "template": {
        "plan_id": key,
        "account_id": "account",
        "instrument_code": "600000.SH",
        "source_type": "ENTRY_PLAN",
        "source_id": key,
        "run_id": run,
        "metadata": {},
      },
    },
  )


@pytest.mark.asyncio
async def test_live_capacity_and_authority_exclude_paper_claims(environment_database):
  async with environment_database() as db:
    live = plan("live", "live", "live-run", 1000)
    live.auto_exit_authorized = True
    live.auto_exit_authorization_fingerprint = "f" * 64
    live.auto_exit_authorization_config_version = 1
    live.auto_exit_authorized_at = datetime(2026, 8, 31, 10)
    live.auto_exit_authorization_expires_at = datetime(2026, 8, 31, 15)
    live.auto_exit_authorization_challenge_id = "challenge"
    live.auto_exit_authorization_user_id = "user"
    live.auto_exit_authorization_device_session_id = "device"
    db.add_all(
      [
        Position(
          id="holding",
          account_id="account",
          stock_code="600000.SH",
          volume=1000,
          can_use_volume=1000,
        ),
        live,
        plan("paper", "paper", "paper-run", 100),
      ]
    )
    await db.commit()
    result = await AutoExitPlanService()._reconcile_capacity_locked(
      db,
      account_id="account",
      instrument_code="600000.SH",
    )
    assert result["ready"]
    assert result["protected_volume"] == 1000
    assert result["plan_ids"] == ["live"]
    assert live.auto_exit_authorized


@pytest.mark.asyncio
async def test_paper_capacity_reads_only_its_run_inventory(environment_database):
  async with environment_database() as db:
    db.add_all(
      [
        Position(
          id="holding", account_id="account", stock_code="600000.SH", volume=9999
        ),
        StrategyRunPosition(
          run_id="paper-a", instrument_code="600000.SH", long_volume=100
        ),
        StrategyRunPosition(
          run_id="paper-b", instrument_code="600000.SH", long_volume=200
        ),
        plan("a", "paper", "paper-a", 100),
        plan("b", "paper", "paper-b", 200),
        plan("live", "live", "live-run", 9999),
      ]
    )
    await db.commit()
    scope = await lock_exit_plan_scope_for_plan(db, "a")
    assert scope.position.volume == 100
    assert [record.plan_id for record in scope.plans] == ["a"]
    result = await AutoExitPlanService()._reconcile_capacity_locked(
      db,
      account_id="account",
      instrument_code="600000.SH",
      locked_scope=scope,
    )
    assert result["ready"]
    assert result["total_volume"] == 100


@pytest.mark.parametrize("cash", [0, 1200])
def test_paper_broker_restores_checkpoint_balance_and_does_not_replenish_inventory(
  cash,
):
  manager = RuntimeStateManager(run_id="paper", persist_enabled=False)
  manager.update_account(cash=cash, frozen_cash=0, total_asset=cash + 1000)
  manager.update_position(
    "600000.SH",
    long_volume=100,
    available_volume=100,
    market_value=1000,
    long_avg_price=10,
    last_price=10,
  )
  runtime = SimpleNamespace(
    run_id="paper",
    context=SimpleNamespace(
      mode=StrategyRunMode.PAPER, initial_capital=100000, parameters={}
    ),
    state_manager=manager,
    broker=SimulatorBroker(initial_capital=100000),
    strategy=None,
  )
  executor = StrategyExecutor()
  executor._seed_simulated_broker_positions(runtime)
  assert runtime.broker.cash == cash
  executor._sync_dynamic_holding_inventory(
    runtime, {"600000.SH": {"position_shares": 9000, "position_available_shares": 9000}}
  )
  assert manager.get_position("600000.SH")["long_volume"] == 100
  assert runtime.broker.positions["600000.SH"].long_volume == 100


@pytest.mark.asyncio
async def test_paper_entry_revision_uses_its_portfolio_without_reading_live_account():
  manager = RuntimeStateManager(run_id="paper", persist_enabled=False)
  manager.update_account(cash=800, frozen_cash=0, total_asset=1000)
  manager.update_position(
    "600000.SH",
    long_volume=20,
    available_volume=20,
    market_value=200,
    long_avg_price=10,
    last_price=10,
  )
  runtime = SimpleNamespace(state_manager=manager)

  def forbidden_database():
    raise AssertionError("PAPER revision must not read the LIVE account")

  service = EntryPlanService(
    SimpleNamespace(get_run=lambda _: runtime),
    session_factory=forbidden_database,
  )
  loaded = SimpleNamespace(
    run=SimpleNamespace(id="paper"),
    config=SimpleNamespace(
      instrument_code="600000.SH",
      target_policy=SimpleNamespace(
        baseline_snapshot=SimpleNamespace(reference_price=10),
      ),
    ),
  )
  baseline = await service._paper_run_baseline(loaded)
  assert baseline["position_volume"] == 20
  assert baseline["total_asset_cny"] == 1000
  assert baseline["paper_portfolio"]["initial_cash"] == 800
