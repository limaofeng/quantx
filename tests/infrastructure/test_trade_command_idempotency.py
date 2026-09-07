from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.clock import utcnow
from quantx_domain.trading.t_order_policy import TOrderPolicyDecision
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentDevice,
  OrderCorrelation,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
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
  OrderCorrelation.__table__,
  TTradeBatch.__table__,
  TradeCommandOutbox.__table__,
  TradeIntentRecord.__table__,
]


def _ready_control(**overrides):
  values = {
    "authorization_state": "ENABLED",
    "reconcile_status": "READY",
    "last_snapshot_id": "snapshot-1",
    "last_snapshot_hash": "a" * 64,
    "last_snapshot_at": utcnow(),
    "controlled_window_active": True,
    "controlled_window_snapshot_id": "snapshot-1",
    "controlled_window_snapshot_hash": "a" * 64,
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


@pytest.mark.asyncio
async def test_live_admission_preview_validates_without_account_row_lock(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  db = SimpleNamespace(get=AsyncMock(return_value=_ready_control()))
  service = TradeCommandService(db)
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  await service._preview_live_authorization(
    "account-1",
    risk_reducing=False,
    require_controlled_window=True,
  )

  db.get.assert_awaited_once_with(
    AccountExecutionControl,
    "account-1",
    populate_existing=True,
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
  rollout = _ready_control(
    authorization_state="KILLED",
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

  with pytest.raises(AgentUnavailableError, match="禁止买入或加仓"):
    await service._require_manual_live_authorization(
      "account-1",
      risk_reducing=False,
    )

  await service._require_manual_live_authorization(
    "account-1",
    risk_reducing=True,
  )
  db.get.assert_awaited_with(
    AccountExecutionControl,
    "account-1",
    with_for_update=True,
    populate_existing=True,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("control_overrides", "message"),
  [
    (None, "尚未配置"),
    ({"reconcile_status": "PENDING"}, "尚未完成对账"),
    (
      {"last_snapshot_age": timedelta(minutes=3)},
      "超过 90 秒",
    ),
    ({"controlled_window_active": False}, "账户实盘窗口"),
    (
      {"controlled_window_snapshot_hash": "different"},
      "与最新完整快照不一致",
    ),
    ({"authorization_state": "DISABLED"}, "买入权限"),
  ],
)
async def test_manual_buy_requires_every_rollout_and_snapshot_gate(
  monkeypatch,
  control_overrides,
  message,
) -> None:
  if control_overrides is None:
    rollout = None
  else:
    overrides = dict(control_overrides)
    snapshot_age = overrides.pop("last_snapshot_age", None)
    if snapshot_age is not None:
      overrides["last_snapshot_at"] = utcnow() - snapshot_age
    rollout = _ready_control(**overrides)
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
  rollout = _ready_control(
    authorization_state="KILLED",
    last_snapshot_at=utcnow() - timedelta(minutes=3),
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
@pytest.mark.parametrize("manual_live", [True, False])
async def test_direct_manual_live_buy_stages_then_uses_public_dispatcher(
  monkeypatch, manual_live,
) -> None:
  events: list[str] = []

  async def get(_model, _key, **kwargs):
    if kwargs.get("with_for_update"):
      assert kwargs == {
        "with_for_update": True,
        "populate_existing": True,
      }
      events.append("rollout-lock")
    else:
      assert kwargs == {"populate_existing": True}
      events.append("rollout-preview")
    return _ready_control()

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
  service._device_for = AsyncMock(return_value=SimpleNamespace(id="device-1"))
  service._require_live_market_stream_ready = AsyncMock()
  service._require_account_capacity = AsyncMock(return_value={"snapshot_id": "snapshot-1"})
  staged_id = "00000000-0000-0000-0000-000000000501"
  queued = command_module.QueuedTradeCommand("client-1", "message-1", "QUEUED")

  async def stage(**_kwargs):
    events.append("ready-committed")
    return staged_id

  async def dispatch(**_kwargs):
    events.append("batch-claimed")
    await service._require_live_authorization("account-1", risk_reducing=False)
    return {staged_id: queued}

  service._stage_risk_increase_order_request = AsyncMock(side_effect=stage)
  service.dispatch_ready_risk_increase_orders = AsyncMock(side_effect=dispatch)
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  result = await service.enqueue_order(
    user_id="user-1",
    account_id="account-1",
    instrument_code="600000.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price=Decimal("10"),
    volume=100,
    idempotency_key="manual-1",
    execution_ref=ExecutionOwnerRef.manual_command("manual-1"),
    environment=ExecutionEnvironment.LIVE,
    manual_live=manual_live,
    commit_transaction=False,
  )

  assert result == queued
  assert events == [
    "rollout-preview",
    "outbox-lookup",
    "ready-committed",
    "batch-claimed",
    "rollout-lock",
  ]
  service._stage_risk_increase_order_request.assert_awaited_once()
  service.dispatch_ready_risk_increase_orders.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_admission_dispatch_failure_rolls_back_without_direct_enqueue(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Result:
    @staticmethod
    def scalar_one_or_none():
      return None

  db = SimpleNamespace(
    get=AsyncMock(return_value=_ready_control()),
    execute=AsyncMock(return_value=Result()),
    rollback=AsyncMock(),
  )
  service = TradeCommandService(db)
  service._preview_live_authorization = AsyncMock(return_value=_ready_control())
  service._stage_risk_increase_order_request = AsyncMock(return_value="intent-1")
  service.dispatch_ready_risk_increase_orders = AsyncMock(
    side_effect=RuntimeError("dispatch crashed")
  )
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  with pytest.raises(RuntimeError, match="dispatch crashed"):
    await service.enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10"),
      volume=100,
      idempotency_key="manual-failure",
      execution_ref=ExecutionOwnerRef.manual_command("manual-failure"),
      environment=ExecutionEnvironment.LIVE,
      manual_live=True,
    )

  db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_dispatch_loser_returns_winner_queued_order(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Result:
    @staticmethod
    def scalar_one_or_none():
      return None

  db = SimpleNamespace(
    execute=AsyncMock(return_value=Result()),
    rollback=AsyncMock(),
  )
  service = TradeCommandService(db)
  service._preview_live_authorization = AsyncMock(return_value=_ready_control())
  service._stage_risk_increase_order_request = AsyncMock(return_value="intent-1")
  service.dispatch_ready_risk_increase_orders = AsyncMock(
    side_effect=ValueError("RISK_ADMISSION_LEASE_HELD")
  )
  winner = command_module.QueuedTradeCommand("client-1", "message-1", "QUEUED")
  service._await_queued_risk_increase_order = AsyncMock(return_value=winner)
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )

  result = await service.enqueue_order(
    user_id="user-1",
    account_id="account-1",
    instrument_code="600000.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price=Decimal("10"),
    volume=100,
    idempotency_key="manual-concurrent",
    execution_ref=ExecutionOwnerRef.manual_command("manual-concurrent"),
    environment=ExecutionEnvironment.LIVE,
    manual_live=True,
  )

  assert result == winner
  service._await_queued_risk_increase_order.assert_awaited_once_with("intent-1")


@pytest.mark.asyncio
async def test_live_buy_requires_authoritative_ready_market_stream(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  heartbeat = SimpleNamespace(details={"marketStreamStatus": "SYNCING"})
  db = SimpleNamespace(get=AsyncMock(return_value=heartbeat))
  service = TradeCommandService(db)
  authoritative_ready = AsyncMock(return_value=False)
  monkeypatch.setattr(
    command_module,
    "authoritative_market_stream_tradable",
    authoritative_ready,
  )

  with pytest.raises(AgentUnavailableError, match="权威全市场行情"):
    await service._require_live_market_stream_ready(SimpleNamespace(id="device-1"))
  authoritative_ready.assert_not_awaited()

  heartbeat.details["marketStreamStatus"] = "READY"
  with pytest.raises(AgentUnavailableError, match="权威全市场行情"):
    await service._require_live_market_stream_ready(SimpleNamespace(id="device-1"))

  authoritative_ready.return_value = True
  await service._require_live_market_stream_ready(SimpleNamespace(id="device-1"))


def test_live_t_entry_create_gate_enforces_v1_cutoff_and_protected_limit(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  intent = SimpleNamespace(
    intent_metadata={
      "t_entry_order_policy_version": "TEntryOrderPolicy.v1",
      "t_order_reference_price": "10",
      "t_order_price_tick": "0.01",
      "t_order_limit_up": "11",
    }
  )
  monkeypatch.setattr(
    command_module.time_utils,
    "now",
    lambda: datetime(2026, 9, 3, 6, 49, tzinfo=timezone.utc),
  )

  evidence = TradeCommandService._require_t_order_new_policy(
    role="ENTRY",
    order_type="FIX_PRICE",
    limit_price=Decimal("10.03"),
    intent=intent,
    request_metadata={},
  )
  assert evidence["protected_limit_price"] == "10.03"

  with pytest.raises(AgentUnavailableError, match="PROTECTED_LIMIT"):
    TradeCommandService._require_t_order_new_policy(
      role="ENTRY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10.04"),
      intent=intent,
      request_metadata={},
    )

  monkeypatch.setattr(
    command_module.time_utils,
    "now",
    lambda: datetime(2026, 9, 3, 6, 50, tzinfo=timezone.utc),
  )
  with pytest.raises(AgentUnavailableError, match="T_ENTRY_CUTOFF_REACHED"):
    TradeCommandService._require_t_order_new_policy(
      role="ENTRY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10.03"),
      intent=intent,
      request_metadata={},
    )


def test_t_order_new_policy_secondary_gate_rejects_market_order() -> None:
  with pytest.raises(AgentUnavailableError, match="T_ORDER_FIX_PRICE_REQUIRED"):
    TradeCommandService._require_t_order_new_policy(
      role="EXIT",
      order_type="MARKET",
      limit_price=Decimal("9.97"),
      intent=None,
      request_metadata={},
    )


def test_t_entry_and_exit_wire_expiry_are_both_frozen_at_30_seconds() -> None:
  now = datetime(2026, 9, 3, 2)

  assert TradeCommandService._place_order_expires_at(
    now,
    t_trade_role="ENTRY",
  ) == now + timedelta(seconds=30)
  assert TradeCommandService._place_order_expires_at(
    now,
    t_trade_role="EXIT",
  ) == now + timedelta(seconds=30)
  assert TradeCommandService._place_order_expires_at(
    now,
    t_trade_role="",
  ) == now + timedelta(minutes=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("status", "reason"),
  [
    ("UNKNOWN", "ORDER_RESULT_UNKNOWN"),
    ("CANCEL_PENDING", "ORDER_CANCEL_UNCONFIRMED"),
  ],
)
async def test_t_replace_gate_never_replaces_unknown_or_unconfirmed_order(
  status: str,
  reason: str,
) -> None:
  created = datetime(2026, 9, 3, 10)
  pending = SimpleNamespace(
    t_trade_role="ENTRY",
    status=status,
    broker_order_id="",
    created_at=created,
    volume=100,
    request_metadata={
      "t_entry_order_policy_version": "TEntryOrderPolicy.v1",
      "t_order_replace_count": 0,
    },
  )
  db = SimpleNamespace(get=AsyncMock(return_value=pending))

  result = await TradeCommandService(db).evaluate_t_order_replacement(
    client_order_id="client-1",
    now=created + timedelta(seconds=31),
    reference_price=Decimal("10"),
    price_tick=Decimal("0.01"),
  )

  assert result.decision is TOrderPolicyDecision.WAIT_AUTHORITATIVE_TERMINAL
  assert result.reason_code == reason


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

  now = utcnow()
  api_heartbeat = SimpleNamespace(
    instance_id="api-instance-1",
    status="READY",
    updated_at=now,
  )
  heartbeat = SimpleNamespace(
    status="READY",
    updated_at=now,
    details={
      "apiInstanceId": "api-instance-1",
      "agentSessionId": "agent-session-1",
      "serverReceivedAt": now.isoformat(),
      "agentSentAt": now.isoformat(),
      "sessionActive": True,
    },
  )

  async def get(_model, key):
    return api_heartbeat if key == "api" else heartbeat

  db = SimpleNamespace(
    execute=AsyncMock(return_value=Result()),
    get=AsyncMock(side_effect=get),
  )

  with pytest.raises(AgentUnavailableError, match="多个就绪 live"):
    await TradeCommandService(db)._device_for(
      user_id="user-1",
      account_id="account-1",
      execution_mode="live",
    )

  assert db.get.await_count == 2
  assert all(
    call.args[0] is RuntimeComponentHeartbeat for call in db.get.await_args_list
  )

  db.get.reset_mock()
  with pytest.raises(AgentUnavailableError, match="多个就绪 live"):
    await TradeCommandService(db)._device_for_account(
      account_id="account-1",
      execution_mode="live",
    )
  assert db.get.await_count == 2


@pytest.mark.asyncio
async def test_live_device_uses_local_heartbeat_not_api_generation() -> None:
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

  now = utcnow()
  heartbeat = SimpleNamespace(
    status="READY",
    updated_at=now,
    details={
      "apiInstanceId": "api-instance-old",
      "agentSessionId": "agent-session-1",
      "serverReceivedAt": now.isoformat(),
      "agentSentAt": now.isoformat(),
      "sessionActive": True,
    },
  )

  async def get(_model, key):
    return heartbeat

  db = SimpleNamespace(
    execute=AsyncMock(return_value=Result()),
    get=AsyncMock(side_effect=get),
  )
  service = TradeCommandService(db)

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
      execution_ref=ExecutionOwnerRef.manual_command("manual-order-1"),
      environment=ExecutionEnvironment.PAPER,
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
      execution_ref=ExecutionOwnerRef.manual_command("manual-order-1"),
      environment=ExecutionEnvironment.PAPER,
      idempotency_key="ui-request-1",
    )

    assert second == first
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 1
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 1
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
    db.add(
      PendingTradeOrder(
        client_order_id="place-client-1",
        user_id="user-1",
        account_id="account-1",
        owner_type="MANUAL_COMMAND",
        owner_id="manual-cancel-1",
        environment="PAPER",
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price="10",
        volume=100,
        status="SUBMITTED",
        broker_order_id="broker-order-1",
        bucket="manual",
        request_metadata={},
      )
    )
    db.add(
      OrderCorrelation(
        id="place-correlation-1",
        client_order_id="place-client-1",
        broker_order_id="broker-order-1",
        account_id="account-1",
        owner_type="MANUAL_COMMAND",
        owner_id="manual-cancel-1",
        environment="PAPER",
        bucket="manual",
        trace_id="place-trace-1",
        request_metadata={},
      )
    )
    await db.commit()
    service = TradeCommandService(db)
    first = await service.enqueue_cancel(
      user_id="user-1",
      account_id="account-1",
      broker_order_id="broker-order-1",
      execution_ref=ExecutionOwnerRef.manual_command("manual-cancel-1"),
      environment=ExecutionEnvironment.PAPER,
    )
    second = await service.enqueue_cancel(
      user_id="user-1",
      account_id="account-1",
      broker_order_id="broker-order-1",
      execution_ref=ExecutionOwnerRef.manual_command("manual-cancel-1"),
      environment=ExecutionEnvironment.PAPER,
    )

    assert second == first
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 1
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
        execution_ref=ExecutionOwnerRef.manual_command("manual-data-only-1"),
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="manual-data-only-key",
      )

  await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "owner_type",
  [
    ExecutionOwnerType.T_ASSISTANT_EXECUTION,
    ExecutionOwnerType.ENTRY_PLAN,
    ExecutionOwnerType.BOARD_ASSISTANT_EXECUTION,
  ],
)
async def test_enqueue_rejects_unregistered_runtime_owner(owner_type) -> None:
  service = TradeCommandService(SimpleNamespace())

  with pytest.raises(AgentUnavailableError, match="OWNER_UNREGISTERED"):
    await service.enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10"),
      volume=100,
      execution_ref=ExecutionOwnerRef(owner_type, "owner-1"),
      environment=ExecutionEnvironment.PAPER,
      idempotency_key="unregistered-owner-key",
      strategy_order_id="strategy-order-1",
      intent_id="intent-1",
    )


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
    original_get = db.get

    async def get(model, key, **kwargs):
      if model is StrategyRun and key == "run-1":
        return SimpleNamespace(mode="paper", parameters={"account_id": "account-1"})
      return await original_get(model, key, **kwargs)

    db.get = get
    db.add(TradeIntentRecord(
      id="intent-1", strategy_run_id="run-1", owner_type="STRATEGY_RUN", owner_id="run-1",
      environment="PAPER", idempotency_key="intent-1",
      account_id="account-1", instrument_code="600000.SH", direction="BUY", bucket="swing",
      intent_metadata={"t_trade_role": "entry", "t_batch_id": "batch-1"},
    ))
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
      execution_ref=ExecutionOwnerRef.strategy_run("run-1"),
      environment=ExecutionEnvironment.PAPER,
      idempotency_key="strategy-context-key",
      strategy_run_id="run-1",
      strategy_order_id="strategy-order-1",
      intent_id="intent-1",
      batch_id="batch-1",
      bucket="swing",
      t_trade_role="entry",
      risk_decision_id="risk-1",
      substitution_plan={"source_bucket": "core", "volume": 100},
      request_metadata={"config_version": 3},
    )

    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    correlation = (
      await db.execute(
        select(OrderCorrelation).where(
          OrderCorrelation.client_order_id == queued.client_order_id
        )
      )
    ).scalar_one()
    batch = await db.get(TTradeBatch, "batch-1")

    assert outbox.payload["execution_mode"] == "paper"
    assert set(outbox.payload) == {
      "command_kind",
      "client_order_id",
      "account_id",
      "execution_mode",
      "instrument_code",
      "side",
      "price_type",
      "limit_price",
      "volume",
      "expires_at",
    }
    assert pending.strategy_run_id == "run-1"
    assert pending.environment == "PAPER"
    assert pending.batch_id == "batch-1"
    assert pending.substitution_plan["source_bucket"] == "core"
    assert correlation.strategy_order_id == "strategy-order-1"
    assert correlation.t_trade_role == "ENTRY"
    assert batch.status == "ENTRY_QUEUED"
  await engine.dispose()


@pytest.mark.asyncio
async def test_public_t_exit_reaches_final_policy_and_pending_role(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Result:
    @staticmethod
    def scalar_one_or_none():
      return None

  plan = SimpleNamespace(
    plan_id="exit-plan-1",
    source_type="T_TRADE_BATCH",
    source_id="batch-1",
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id="run-1",
    source_execution_environment="LIVE",
  )
  batch = SimpleNamespace(
    batch_id="batch-1",
    account_id="account-1",
    instrument_code="600000.SH",
    strategy_run_id="run-1",
    environment="LIVE",
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id="run-1",
    source_execution_environment="LIVE",
    exit_reason=None,
    status="ENTRY_FILLED",
  )
  intent = SimpleNamespace(
    id="intent-exit",
    owner_type="EXIT_PLAN",
    owner_id="exit-plan-1",
    status="APPROVED",
    intent_metadata={
      "t_trade_role": "exit",
      "t_batch_id": "batch-1",
      "t_exit_order_policy_version": "TExitOrderPolicy.v1",
      "t_order_reference_price": "10",
      "t_order_price_tick": "0.01",
      "t_exit_order_ttl_seconds": 30,
      "t_exit_total_ttl_seconds": 90,
      "t_exit_max_replace_count": 2,
      "t_exit_max_slippage_bps": 30,
      "price_type": "FIX_PRICE",
      "exit_reason": "HARD_STOP",
    },
  )
  added: list[object] = []

  async def get(model, key, **_kwargs):
    if model is TTradeBatch and key == "batch-1":
      return batch
    if model is command_module.AutoExitPlanRecord and key == "exit-plan-1":
      return plan
    return None

  db = SimpleNamespace(
    get=get,
    execute=AsyncMock(return_value=Result()),
    scalar=AsyncMock(return_value=None),
    add=added.append,
    flush=AsyncMock(),
  )
  service = TradeCommandService(db)
  service._require_durable_order_intent = AsyncMock(return_value=intent)
  service._require_account_capacity = AsyncMock(
    return_value={"snapshot_id": "snapshot-1"}
  )
  service._device_for = AsyncMock(
    return_value=SimpleNamespace(id="device-live", user_id="user-1")
  )
  service._require_live_market_stream_ready = AsyncMock()
  monkeypatch.setattr(
    command_module.time_utils,
    "now",
    lambda: datetime(2026, 9, 3, 10, tzinfo=timezone(timedelta(hours=8))),
  )
  fixed_utc = datetime(2026, 9, 3, 2)
  monkeypatch.setattr(command_module, "utcnow", lambda: fixed_utc)

  await service.enqueue_order(
    user_id="user-1",
    account_id="account-1",
    instrument_code="600000.SH",
    side="SELL",
    order_type="FIX_PRICE",
    limit_price=Decimal("9.97"),
    volume=100,
    execution_ref=ExecutionOwnerRef(ExecutionOwnerType.EXIT_PLAN, "exit-plan-1"),
    environment=ExecutionEnvironment.LIVE,
    idempotency_key="public-t-exit",
    intent_id="intent-exit",
    batch_id="batch-1",
    bucket="swing",
    t_trade_role="exit",
    request_metadata={
      "t_exit_order_policy_version": "TExitOrderPolicy.v1",
      "t_order_reference_price": "10",
      "t_order_price_tick": "0.01",
      "t_exit_order_ttl_seconds": 30,
      "t_exit_total_ttl_seconds": 90,
      "t_exit_max_replace_count": 2,
      "t_exit_max_slippage_bps": 30,
      "price_type": "FIX_PRICE",
      "exit_reason": "HARD_STOP",
    },
    commit_transaction=False,
    _locked_live_control=SimpleNamespace(account_id="account-1"),
  )

  pending = next(item for item in added if isinstance(item, PendingTradeOrder))
  correlation = next(item for item in added if isinstance(item, OrderCorrelation))
  outbox = next(item for item in added if isinstance(item, TradeCommandOutbox))
  assert pending.owner_type == "EXIT_PLAN"
  assert pending.batch_id == "batch-1"
  assert pending.t_trade_role == "EXIT"
  assert pending.request_metadata["t_exit_order_policy_version"] == (
    "TExitOrderPolicy.v1"
  )
  assert pending.request_metadata["t_exit_order_ttl_seconds"] == 30
  assert pending.request_metadata["t_exit_total_ttl_seconds"] == 90
  assert pending.request_metadata["t_exit_max_replace_count"] == 2
  assert pending.order_type == "FIX_PRICE"
  assert pending.request_metadata["price_type"] == pending.order_type
  assert correlation.batch_id == "batch-1"
  assert correlation.t_trade_role == "EXIT"
  assert correlation.request_metadata["price_type"] == pending.order_type
  assert outbox.expires_at == fixed_utc + timedelta(seconds=30)
  assert datetime.fromisoformat(outbox.payload["expires_at"]) == (
    fixed_utc + timedelta(seconds=30)
  ).replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_direct_public_t_exit_rejects_market_order() -> None:
  class Result:
    @staticmethod
    def scalar_one_or_none():
      return None

  batch = SimpleNamespace(
    account_id="account-1",
    instrument_code="600000.SH",
    strategy_run_id="run-1",
    environment="LIVE",
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id="run-1",
    source_execution_environment="LIVE",
  )
  plan = SimpleNamespace(
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id="run-1",
    source_execution_environment="LIVE",
  )
  intent = SimpleNamespace(
    owner_type="EXIT_PLAN",
    owner_id="exit-plan-1",
    status="APPROVED",
    intent_metadata={
      "t_trade_role": "exit",
      "t_batch_id": "batch-1",
      "t_exit_order_policy_version": "TExitOrderPolicy.v1",
    },
  )

  async def get(model, key, **_kwargs):
    if model is TTradeBatch and key == "batch-1":
      return batch
    if model is command_module.AutoExitPlanRecord and key == "exit-plan-1":
      return plan
    return None

  service = TradeCommandService(
    SimpleNamespace(
      get=get,
      execute=AsyncMock(return_value=Result()),
      scalar=AsyncMock(return_value=None),
    )
  )
  service._require_durable_order_intent = AsyncMock(return_value=intent)
  service._require_account_capacity = AsyncMock(return_value={})

  with pytest.raises(AgentUnavailableError) as rejected:
    await service.enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="SELL",
      order_type="MARKET",
      limit_price=Decimal("9.97"),
      volume=100,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        "exit-plan-1",
      ),
      environment=ExecutionEnvironment.LIVE,
      idempotency_key="public-t-exit-market",
      intent_id="intent-exit",
      batch_id="batch-1",
      bucket="swing",
      t_trade_role="exit",
      request_metadata={
        "t_exit_order_policy_version": "TExitOrderPolicy.v1",
      },
      commit_transaction=False,
      _locked_live_control=SimpleNamespace(account_id="account-1"),
    )
  assert "FIX_PRICE" in str(rejected.value)


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
        execution_ref=ExecutionOwnerRef.manual_command("manual-paper-1"),
        environment=ExecutionEnvironment.PAPER,
        idempotency_key="manual-paper-key",
      )
  await engine.dispose()
