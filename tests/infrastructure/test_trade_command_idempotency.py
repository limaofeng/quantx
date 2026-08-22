from datetime import timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRollout,
  AgentDevice,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.services import trade_command_service as command_module
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TABLES = [
  AuthUser.__table__,
  AgentDevice.__table__,
  PendingTradeOrder.__table__,
  StrategyOrderCorrelation.__table__,
  TTradeBatch.__table__,
  TradeCommandOutbox.__table__,
]


def _ready_rollout(**overrides):
  values = {
    "kill_switch": False,
    "stage": "CANARY",
    "enabled": True,
    "reconcile_status": "READY",
    "policy_version": 3,
    "acknowledged_policy_version": 3,
    "last_snapshot_id": "snapshot-1",
    "last_snapshot_hash": "snapshot-hash-1",
    "last_snapshot_at": utcnow(),
    "controlled_window_active": True,
    "controlled_window_snapshot_id": "snapshot-1",
    "controlled_window_snapshot_hash": "snapshot-hash-1",
  }
  values.update(overrides)
  return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_manual_live_authorization_requires_global_gate_and_allowlist(
  monkeypatch,
) -> None:
  db = SimpleNamespace(get=AsyncMock(return_value=None))
  service = TradeCommandService(db)
  monkeypatch.setattr(command_module.settings, "enable_real_trading", False)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  with pytest.raises(AgentUnavailableError, match="总开关"):
    await service._require_manual_live_authorization(
      "account-1",
      risk_reducing=False,
    )

  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(command_module.settings, "real_trading_account_allowlist", [])
  with pytest.raises(AgentUnavailableError, match="白名单"):
    await service._require_manual_live_authorization(
      "account-1",
      risk_reducing=False,
    )


@pytest.mark.asyncio
async def test_manual_live_kill_switch_blocks_buy_but_keeps_sell_risk_reducing(
  monkeypatch,
) -> None:
  rollout = _ready_rollout(
    kill_switch=True,
    stage="KILL_SWITCHED",
    enabled=False,
    acknowledged_policy_version=0,
    controlled_window_active=False,
  )
  db = SimpleNamespace(get=AsyncMock(return_value=rollout))
  service = TradeCommandService(db)
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  with pytest.raises(AgentUnavailableError, match="禁止新增风险"):
    await service._require_manual_live_authorization(
      "account-1",
      risk_reducing=False,
    )

  await service._require_manual_live_authorization(
    "account-1",
    risk_reducing=True,
  )
  db.get.assert_awaited_with(
    AccountTradingRollout,
    "account-1",
    with_for_update=True,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("rollout", "message"),
  [
    (None, "尚未配置"),
    (_ready_rollout(reconcile_status="PENDING"), "尚未完成对账"),
    (
      _ready_rollout(last_snapshot_at=utcnow() - timedelta(minutes=3)),
      "超过 90 秒",
    ),
    (_ready_rollout(controlled_window_active=False), "账户实盘窗口"),
    (
      _ready_rollout(controlled_window_snapshot_hash="different"),
      "与最新完整快照不一致",
    ),
    (_ready_rollout(stage="SHADOW"), "CANARY/LIVE"),
    (_ready_rollout(enabled=False), "CANARY/LIVE"),
    (_ready_rollout(acknowledged_policy_version=2), "尚未确认"),
  ],
)
async def test_manual_buy_requires_every_rollout_and_snapshot_gate(
  monkeypatch,
  rollout,
  message,
) -> None:
  db = SimpleNamespace(get=AsyncMock(return_value=rollout))
  service = TradeCommandService(db)
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  with pytest.raises(AgentUnavailableError, match=message):
    await service._require_manual_live_authorization(
      "account-1",
      risk_reducing=False,
    )


@pytest.mark.asyncio
async def test_manual_sell_still_rejects_stale_reconciliation(monkeypatch) -> None:
  rollout = _ready_rollout(
    kill_switch=True,
    stage="KILL_SWITCHED",
    enabled=False,
    last_snapshot_at=utcnow() - timedelta(minutes=3),
    acknowledged_policy_version=0,
    controlled_window_active=False,
  )
  db = SimpleNamespace(get=AsyncMock(return_value=rollout))
  service = TradeCommandService(db)
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  with pytest.raises(AgentUnavailableError, match="超过 90 秒"):
    await service._require_manual_live_authorization(
      "account-1",
      risk_reducing=True,
    )


@pytest.mark.asyncio
async def test_manual_live_enqueue_locks_rollout_before_outbox_lookup(
  monkeypatch,
) -> None:
  events: list[str] = []

  async def get(_model, _key, **kwargs):
    assert kwargs == {"with_for_update": True}
    events.append("rollout-lock")
    return _ready_rollout()

  class Result:
    @staticmethod
    def scalar_one_or_none():
      return None

  async def execute(_statement):
    events.append("outbox-lookup")
    return Result()

  db = SimpleNamespace(
    get=get,
    execute=execute,
    add=lambda _value: None,
    flush=AsyncMock(),
  )
  service = TradeCommandService(db)
  service._device_for = AsyncMock(
    return_value=SimpleNamespace(id="device-1")
  )
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  await service.enqueue_order(
    user_id="user-1",
    account_id="account-1",
    instrument_code="600000.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price=Decimal("10"),
    volume=100,
    idempotency_key="manual-1",
    execution_mode="live",
    manual_live=True,
    commit_transaction=False,
  )

  assert events[:2] == ["rollout-lock", "outbox-lookup"]


@pytest.mark.asyncio
async def test_multiple_ready_live_agents_fail_closed() -> None:
  devices = [
    SimpleNamespace(
      id=f"device-{index}",
      authorized_account_ids=["account-1"],
      capabilities=["live"],
    )
    for index in (1, 2)
  ]

  class Scalars:
    @staticmethod
    def all():
      return devices

  class Result:
    @staticmethod
    def scalars():
      return Scalars()

  heartbeat = SimpleNamespace(status="READY", updated_at=utcnow())
  db = SimpleNamespace(
    execute=AsyncMock(return_value=Result()),
    get=AsyncMock(return_value=heartbeat),
  )

  with pytest.raises(AgentUnavailableError, match="多个就绪 live"):
    await TradeCommandService(db)._device_for(
      user_id="user-1",
      account_id="account-1",
      execution_mode="live",
    )

  assert db.get.await_count == 2
  assert all(
    call.args[0] is RuntimeComponentHeartbeat
    for call in db.get.await_args_list
  )


@pytest.mark.asyncio
async def test_live_device_requires_heartbeat_from_current_managed_launch(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  device = SimpleNamespace(
    id="device-1",
    authorized_account_ids=["account-1"],
    capabilities=["live"],
  )

  class Scalars:
    @staticmethod
    def all():
      return [device]

  class Result:
    @staticmethod
    def scalars():
      return Scalars()

  launch_started_at = utcnow() - timedelta(seconds=10)
  heartbeat = SimpleNamespace(
    status="READY",
    updated_at=launch_started_at - timedelta(seconds=1),
  )
  db = SimpleNamespace(
    execute=AsyncMock(return_value=Result()),
    get=AsyncMock(return_value=heartbeat),
  )
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv(
    "QMT_AGENT_LAUNCH_STARTED_AT",
    launch_started_at.replace(tzinfo=timezone.utc).isoformat(),
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="具备交易能力"):
    await service._device_for(
      user_id="user-1",
      account_id="account-1",
      execution_mode="live",
    )

  heartbeat.updated_at = launch_started_at + timedelta(seconds=1)

  assert (
    await service._device_for(
      user_id="user-1",
      account_id="account-1",
      execution_mode="live",
    )
  ) is device


@pytest.mark.asyncio
async def test_trade_command_business_key_is_deduplicated() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="test",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper"],
      )
    )
    await db.commit()
    service = TradeCommandService(db)
    first = await service.enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10"),
      volume=100,
      idempotency_key="ui-request-1",
    )
    second = await service.enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10"),
      volume=100,
      idempotency_key="ui-request-1",
    )

    assert second == first
    assert (
      await db.scalar(select(func.count()).select_from(TradeCommandOutbox))
      == 1
    )
    assert (
      await db.scalar(select(func.count()).select_from(PendingTradeOrder))
      == 1
    )
  await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_command_business_key_is_deduplicated() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="test",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper"],
      )
    )
    await db.commit()
    service = TradeCommandService(db)
    first = await service.enqueue_cancel(
      user_id="user-1",
      account_id="account-1",
      broker_order_id="broker-order-1",
    )
    second = await service.enqueue_cancel(
      user_id="user-1",
      account_id="account-1",
      broker_order_id="broker-order-1",
    )

    assert second == first
    assert (
      await db.scalar(select(func.count()).select_from(TradeCommandOutbox))
      == 1
    )
  await engine.dispose()


@pytest.mark.asyncio
async def test_data_only_agent_cannot_receive_trade_commands() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="device-data",
        user_id="user-1",
        name="data-only",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["market-data", "data-only"],
      )
    )
    await db.commit()

    with pytest.raises(AgentUnavailableError, match="具备交易能力"):
      await TradeCommandService(db).enqueue_order(
        user_id="user-1",
        account_id="account-1",
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price=Decimal("10"),
        volume=100,
      )

  await engine.dispose()


@pytest.mark.asyncio
async def test_strategy_order_context_is_preserved_without_manual_bucket() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="paper-device",
        user_id="user-1",
        name="paper",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper"],
      )
    )
    await db.commit()
    queued = await TradeCommandService(db).enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10.50"),
      volume=100,
      strategy_run_id="run-1",
      strategy_order_id="strategy-order-1",
      intent_id="intent-1",
      batch_id="batch-1",
      bucket="swing",
      t_trade_role="entry",
      risk_decision_id="risk-1",
      substitution_plan={"source_bucket": "core", "volume": 100},
      request_metadata={"instrument_code": "600000.SH", "config_version": 3},
    )

    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    correlation = (
      await db.execute(
        select(StrategyOrderCorrelation).where(
          StrategyOrderCorrelation.client_order_id == queued.client_order_id
        )
      )
    ).scalar_one()
    batch = await db.get(TTradeBatch, "batch-1")

    assert outbox.payload["execution_mode"] == "paper"
    assert outbox.payload["bucket"] == "swing"
    assert outbox.payload["batch_id"] == "batch-1"
    assert outbox.payload["substitution_plan"]["source_bucket"] == "core"
    assert pending.strategy_run_id == "run-1"
    assert correlation.strategy_order_id == "strategy-order-1"
    assert correlation.t_trade_role == "ENTRY"
    assert batch.status == "ENTRY_QUEUED"
  await engine.dispose()


@pytest.mark.asyncio
async def test_paper_command_never_routes_to_live_only_agent() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="live-device",
        user_id="user-1",
        name="live",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["live"],
      )
    )
    await db.commit()
    with pytest.raises(AgentUnavailableError, match="paper"):
      await TradeCommandService(db).enqueue_order(
        user_id="user-1",
        account_id="account-1",
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price=Decimal("10"),
        volume=100,
        execution_mode="paper",
      )
  await engine.dispose()
