from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.clock import utcnow
from quantx_domain.trading.entry_plan import ManagedEntryPlanConfig
from quantx_engine import report_processor
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.models.entry_plan_authorization import (
  EntryPlanAuthorizationGrant,
)
from quantx_infrastructure.models.enums import OrderStatus, OrderType
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import trade_command_service as command_module
from quantx_infrastructure.services.entry_plan_authorization_service import (
  scope_from_managed_entry_config,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  QueuedTradeCommand,
  TradeCommandService,
)


def _managed_entry_parameters() -> dict:
  return {
    "entry_plan_enabled": True,
    "managed_entry_plan": {
      "template_version": 1,
      "config_version": 3,
      "instrument_code": "605499.SH",
      "bucket": "swing",
      "target_policy": {
        "mode": "INCREMENTAL_AMOUNT_CNY",
        "incremental_amount_cny": 50_000,
        "max_total_amount_cny": 50_000,
        "max_position_pct": 0.5,
        "baseline_snapshot": {
          "position_volume": 100,
          "market_value_cny": 10_000,
          "total_asset_cny": 100_000,
          "reference_price": 100,
          "account_snapshot_version": "snapshot-7",
        },
      },
      "trigger_rules": [
        {
          "rule_id": "manual-1",
          "rule_type": "MANUAL_TRIGGER",
          "parameters": {},
        }
      ],
      "pacing_policy": {
        "tranche_count": 5,
        "max_single_intent_amount_cny": 12_000,
        "max_daily_filled_amount_cny": 20_000,
        "max_orders_per_day": 5,
        "max_open_orders": 1,
      },
      "execution_policy": {
        "environment": "LIVE",
        "authorization_mode": "AUTO",
        "max_slippage_bps": 35,
        "max_price_deviation_bps": 50,
      },
      "completion_policy": {
        "max_buy_price": 130.5,
        "expire_at_ms": 1_788_000_000_000,
      },
      "exit_plan_template": {"enabled": True},
    }
  }


class _Result:
  def __init__(self, value):
    self.value = value

  def one_or_none(self):
    return self.value

  def scalar_one_or_none(self):
    return self.value

  def scalars(self):
    return SimpleNamespace(all=lambda: self.value)


def _ready_rollout() -> SimpleNamespace:
  return SimpleNamespace(
    authorization_state="ENABLED",
    reconcile_status="READY",
    last_snapshot_id="snapshot-7",
    last_snapshot_hash="a" * 64,
    last_snapshot_at=utcnow(),
    controlled_window_active=False,
  )


@pytest.mark.asyncio
async def test_enqueue_detects_managed_auto_entry_from_persisted_intent() -> None:
  persisted_intent = SimpleNamespace(
    direction="BUY",
    intent_metadata={
      "execution_mode": "AUTO",
      "entry_plan_id": "run-1",
    },
  )
  db = SimpleNamespace(get=AsyncMock(return_value=persisted_intent))
  service = TradeCommandService(db)
  device = SimpleNamespace(id="device-1", user_id="authorized-user")
  service._exact_auto_entry_device = AsyncMock(return_value=device)
  service._device_for_account = AsyncMock()
  service.enqueue_order = AsyncMock(
    return_value=QueuedTradeCommand("client-1", "message-1", "QUEUED")
  )

  result = await service.enqueue_order_for_account(
    account_id="account-1",
    instrument_code="605499.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price=100,
    volume=100,
    execution_mode="live",
    strategy_run_id="run-1",
    intent_id="intent-1",
    bucket="swing",
    policy_version=3,
    request_metadata={
      "entry_plan_id": "run-1",
      "auto_entry_authorization_grant_id": "grant-1",
    },
  )

  assert result.client_order_id == "client-1"
  service._exact_auto_entry_device.assert_awaited_once()
  service._device_for_account.assert_not_awaited()
  assert service.enqueue_order.await_args.kwargs["user_id"] == "authorized-user"


@pytest.mark.asyncio
async def test_enqueue_keeps_managed_manual_entry_on_existing_path() -> None:
  persisted_intent = SimpleNamespace(
    direction="BUY",
    intent_metadata={
      "execution_mode": "MANUAL_CONFIRM",
      "entry_plan_id": "run-1",
    },
  )
  db = SimpleNamespace(get=AsyncMock(return_value=persisted_intent))
  service = TradeCommandService(db)
  device = SimpleNamespace(id="device-1", user_id="manual-user")
  service._exact_auto_entry_device = AsyncMock()
  service._managed_manual_entry_device = AsyncMock(return_value=device)
  service._device_for_account = AsyncMock()
  service.enqueue_order = AsyncMock(
    return_value=QueuedTradeCommand("client-1", "message-1", "QUEUED")
  )

  await service.enqueue_order_for_account(
    account_id="account-1",
    instrument_code="605499.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price=100,
    volume=100,
    execution_mode="live",
    strategy_run_id="run-1",
    intent_id="intent-1",
    bucket="swing",
    policy_version=3,
    request_metadata={"entry_plan_id": "run-1"},
  )

  service._exact_auto_entry_device.assert_not_awaited()
  service._managed_manual_entry_device.assert_awaited_once()
  service._device_for_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_auto_entry_fails_closed_when_durable_plan_is_disabled(
  monkeypatch,
) -> None:
  parameters = _managed_entry_parameters()
  parameters["entry_plan_enabled"] = False
  persisted_intent = SimpleNamespace(
    direction="BUY",
    intent_metadata={
      "execution_mode": "AUTO",
      "entry_plan_id": "run-1",
    },
  )
  run = SimpleNamespace(
    mode="live",
    status="running",
    instruments=["605499.SH"],
    parameters=parameters,
  )
  strategy = SimpleNamespace(class_name="AshareManagedEntryPlanStrategy")

  async def get(model, _key, **_kwargs):
    if model is AccountExecutionControl:
      return _ready_rollout()
    if model is TradeIntentRecord:
      return persisted_intent
    return None

  db = SimpleNamespace(
    get=get,
    execute=AsyncMock(return_value=_Result((run, strategy))),
  )
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )
  service = TradeCommandService(db)
  service.enqueue_order = AsyncMock()

  with pytest.raises(AgentUnavailableError, match="已暂停、终止"):
    await service.enqueue_order_for_account(
      account_id="account-1",
      instrument_code="605499.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("100"),
      volume=100,
      execution_mode="live",
      strategy_run_id="run-1",
      intent_id="intent-1",
      bucket="swing",
      policy_version=3,
      request_metadata={
        "entry_plan_id": "run-1",
        "auto_entry_authorization_grant_id": "grant-1",
      },
    )

  service.enqueue_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_auto_entry_rejects_missing_authoritative_plan_state(
  monkeypatch,
) -> None:
  parameters = _managed_entry_parameters()
  scope = scope_from_managed_entry_config(plan_id="run-1", config=parameters)
  run = SimpleNamespace(
    mode="live",
    status="running",
    instruments=["605499.SH"],
    parameters=parameters,
  )
  strategy = SimpleNamespace(class_name="AshareManagedEntryPlanStrategy")
  intent = SimpleNamespace(
    strategy_run_id="run-1",
    instrument_code="605499.SH",
    direction="BUY",
    bucket="swing",
    status="PENDING",
    target_volume=100,
    target_amount=None,
    intent_metadata={
      "execution_mode": "AUTO",
      "entry_plan_id": "run-1",
      "entry_config_version": 3,
      "exact_auto_entry_authorized": True,
      "auto_entry_authorization_grant_id": "grant-1",
      "auto_entry_plan_fingerprint": scope.plan_fingerprint,
      "auto_entry_rule_fingerprint": scope.rule_fingerprint,
    },
  )

  async def get(model, _key, **_kwargs):
    if model is AccountExecutionControl:
      return _ready_rollout()
    if model is TradeIntentRecord:
      return intent
    return None

  execute_results = iter([_Result((run, strategy)), _Result(None)])
  db = SimpleNamespace(
    get=get,
    execute=AsyncMock(side_effect=lambda _statement: next(execute_results)),
  )
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="权威状态快照缺失"):
    await service._exact_auto_entry_device(
      account_id="account-1",
      instrument_code="605499.SH",
      side="BUY",
      limit_price=Decimal("100"),
      volume=100,
      strategy_run_id="run-1",
      intent_id="intent-1",
      bucket="swing",
      policy_version=3,
      request_metadata={
        "entry_plan_id": "run-1",
        "auto_entry_authorization_grant_id": "grant-1",
        "auto_entry_plan_fingerprint": scope.plan_fingerprint,
        "auto_entry_rule_fingerprint": scope.rule_fingerprint,
      },
    )


@pytest.mark.asyncio
async def test_managed_manual_entry_cannot_exceed_approved_intent_volume() -> None:
  parameters = _managed_entry_parameters()
  parameters["managed_entry_plan"]["execution_policy"][
    "authorization_mode"
  ] = "MANUAL_CONFIRM"
  run = SimpleNamespace(
    mode="live",
    status="running",
    instruments=["605499.SH"],
    parameters=parameters,
  )
  strategy = SimpleNamespace(class_name="AshareManagedEntryPlanStrategy")
  intent = SimpleNamespace(
    id="intent-1",
    strategy_run_id="run-1",
    instrument_code="605499.SH",
    direction="BUY",
    status="APPROVED",
    target_volume=100,
    target_amount=None,
    intent_metadata={
      "execution_mode": "MANUAL_CONFIRM",
      "entry_plan_id": "run-1",
      "entry_config_version": 3,
    },
  )
  plan_state = SimpleNamespace(
    custom_state={
      "managed_entry_plan": {
        "phase": "AWAITING_APPROVAL",
        "pending_intent_id": "intent-1",
      }
    }
  )
  execute_results = iter([_Result((run, strategy)), _Result(plan_state)])
  db = SimpleNamespace(
    get=AsyncMock(return_value=intent),
    execute=AsyncMock(side_effect=lambda _statement: next(execute_results)),
  )
  service = TradeCommandService(db)
  service._require_manual_live_authorization = AsyncMock()
  service._device_for_account = AsyncMock()

  with pytest.raises(AgentUnavailableError, match="已确认意图数量"):
    await service._managed_manual_entry_device(
      account_id="account-1",
      instrument_code="605499.SH",
      limit_price=Decimal("50"),
      volume=200,
      strategy_run_id="run-1",
      intent_id="intent-1",
      bucket="swing",
      policy_version=3,
      intent=intent,
    )

  service._device_for_account.assert_not_awaited()


def test_incremental_target_is_a_hard_cap_below_max_total_budget() -> None:
  parameters = _managed_entry_parameters()
  target_policy = parameters["managed_entry_plan"]["target_policy"]
  target_policy["incremental_amount_cny"] = 10_000
  target_policy["max_total_amount_cny"] = 50_000
  config = ManagedEntryPlanConfig.from_dict(parameters["managed_entry_plan"])

  with pytest.raises(AgentUnavailableError, match="当前剩余目标或总预算"):
    TradeCommandService._require_managed_entry_capacity(
      plan_id="run-1",
      intent_id="intent-1",
      config=config,
      managed_state={"filled_volume": 0, "filled_amount_cny": 0},
      account=SimpleNamespace(total_asset=100_000),
      position=SimpleNamespace(
        volume=100,
        market_value=10_000,
      ),
      active_pending=[],
      executed_volumes={},
      requested_price=Decimal("100"),
      requested_volume=200,
    )


def test_external_position_increase_consumes_incremental_target_gap() -> None:
  parameters = _managed_entry_parameters()
  target_policy = parameters["managed_entry_plan"]["target_policy"]
  target_policy["incremental_amount_cny"] = 10_000
  target_policy["max_total_amount_cny"] = 50_000
  config = ManagedEntryPlanConfig.from_dict(parameters["managed_entry_plan"])

  with pytest.raises(AgentUnavailableError, match="当前剩余目标或总预算"):
    TradeCommandService._require_managed_entry_capacity(
      plan_id="run-1",
      intent_id="intent-1",
      config=config,
      managed_state={"filled_volume": 0, "filled_amount_cny": 0},
      account=SimpleNamespace(total_asset=100_000),
      # Price decline must not hide the externally added 100 shares.
      position=SimpleNamespace(volume=200, market_value=10_000),
      active_pending=[],
      executed_volumes={},
      requested_price=Decimal("100"),
      requested_volume=100,
    )


def test_working_buy_cash_reserve_covers_all_account_instruments() -> None:
  orders = [
    SimpleNamespace(
      intent_id="other-symbol-buy",
      instrument_code="600000.SH",
      side="BUY",
      limit_price=100,
      volume=100,
    ),
    SimpleNamespace(
      intent_id="same-symbol-buy",
      instrument_code="605499.SH",
      side="BUY",
      limit_price=20,
      volume=50,
    ),
    SimpleNamespace(
      intent_id="other-symbol-sell",
      instrument_code="600001.SH",
      side="SELL",
      limit_price=1_000,
      volume=100,
    ),
    SimpleNamespace(
      intent_id="intent-1",
      instrument_code="605499.SH",
      side="BUY",
      limit_price=1_000,
      volume=100,
    ),
  ]

  reserve = TradeCommandService._working_buy_cash_reserve(
    orders,
    intent_id="intent-1",
    executed_volumes={"other-symbol-buy": 20},
  )

  assert reserve == Decimal("9000")


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("side", "status"),
  [("SELL", "SUBMITTED"), ("BUY", "RECONCILE_REQUIRED")],
)
async def test_same_instrument_sell_or_reconcile_order_blocks_entry(
  side,
  status,
) -> None:
  db = SimpleNamespace(scalar=AsyncMock())
  service = TradeCommandService(db)
  working_order = SimpleNamespace(
    instrument_code="605499.SH",
    side=side,
    status=status,
  )

  with pytest.raises(AgentUnavailableError, match="卖单或待对账委托"):
    await service._require_no_conflicting_entry_exit(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[working_order],
    )

  db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_instrument_non_plan_working_buy_blocks_entry() -> None:
  db = SimpleNamespace(scalar=AsyncMock())
  service = TradeCommandService(db)
  working_order = SimpleNamespace(
    instrument_code="605499.SH",
    side="BUY",
    status="ACCEPTED",
    strategy_run_id="other-run",
    request_metadata={},
  )

  with pytest.raises(AgentUnavailableError, match="外部或其他策略工作买单"):
    await service._require_no_conflicting_entry_exit(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[working_order],
    )

  db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_non_plan_pending_buy_invalidates_exact_grant(
  monkeypatch,
) -> None:
  db = SimpleNamespace(scalar=AsyncMock())
  invalidate = AsyncMock(return_value=True)

  class AuthorizationService:
    def __init__(self, _db):
      pass

    async def invalidate(self, **kwargs):
      return await invalidate(**kwargs)

  monkeypatch.setattr(
    command_module,
    "EntryPlanAuthorizationService",
    AuthorizationService,
  )
  service = TradeCommandService(db)
  working_order = SimpleNamespace(
    instrument_code="605499.SH",
    side="BUY",
    status="ACCEPTED",
    strategy_run_id="other-run",
    request_metadata={},
  )

  with pytest.raises(AgentUnavailableError, match="自动授权已失效"):
    await service._require_no_conflicting_entry_exit(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[working_order],
      invalidate_auto_external_buy=True,
    )

  invalidate.assert_awaited_once_with(
    plan_id="run-1",
    reason="ENTRY_EXTERNAL_WORKING_BUY",
    commit=True,
  )


def _authoritative_order(
  *,
  order_id: int = 9001,
  order_type: OrderType | int = OrderType.BUY,
  status: OrderStatus = OrderStatus.REPORTED,
  stock_code: str = "605499.SH",
  order_time=None,
) -> SimpleNamespace:
  return SimpleNamespace(
    id=order_id,
    type=order_type,
    status=status,
    stock_code=stock_code,
    time=order_time if order_time is not None else command_module.time_utils.now(),
  )


@pytest.mark.asyncio
async def test_auto_external_authoritative_buy_invalidates_exact_grant(
  monkeypatch,
) -> None:
  order = _authoritative_order()
  db = SimpleNamespace(
    execute=AsyncMock(side_effect=[_Result([order]), _Result([])]),
  )
  invalidate = AsyncMock(return_value=True)

  class AuthorizationService:
    def __init__(self, _db):
      pass

    async def invalidate(self, **kwargs):
      return await invalidate(**kwargs)

  monkeypatch.setattr(
    command_module,
    "EntryPlanAuthorizationService",
    AuthorizationService,
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="自动授权已失效"):
    await service._require_no_authoritative_entry_order_conflict(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[],
      invalidate_auto_external_buy=True,
    )

  invalidate.assert_awaited_once_with(
    plan_id="run-1",
    reason="ENTRY_EXTERNAL_WORKING_BUY",
    commit=True,
  )
  assert db.execute.await_args_list[0].args[0]._for_update_arg is not None


@pytest.mark.asyncio
async def test_manual_external_authoritative_buy_requires_reconfirmation(
  monkeypatch,
) -> None:
  order = _authoritative_order()
  db = SimpleNamespace(
    execute=AsyncMock(side_effect=[_Result([order]), _Result([])]),
  )
  invalidate = AsyncMock()

  class AuthorizationService:
    def __init__(self, _db):
      pass

    async def invalidate(self, **kwargs):
      return await invalidate(**kwargs)

  monkeypatch.setattr(
    command_module,
    "EntryPlanAuthorizationService",
    AuthorizationService,
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="处理后重新确认"):
    await service._require_no_authoritative_entry_order_conflict(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[],
      invalidate_auto_external_buy=False,
    )

  invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_symbol_external_working_buy_invalidates_before_stale_cash_spend(
  monkeypatch,
) -> None:
  order = _authoritative_order(stock_code="600000.SH")
  db = SimpleNamespace(
    execute=AsyncMock(side_effect=[_Result([order]), _Result([])]),
  )
  invalidate = AsyncMock(return_value=True)

  class AuthorizationService:
    def __init__(self, _db):
      pass

    async def invalidate(self, **kwargs):
      return await invalidate(**kwargs)

  monkeypatch.setattr(
    command_module,
    "EntryPlanAuthorizationService",
    AuthorizationService,
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="自动授权已失效"):
    await service._require_no_authoritative_entry_order_conflict(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[],
      invalidate_auto_external_buy=True,
    )

  invalidate.assert_awaited_once_with(
    plan_id="run-1",
    reason="ENTRY_EXTERNAL_WORKING_BUY",
    commit=True,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("order_type", "status"),
  [
    (OrderType.SELL, OrderStatus.REPORTED),
    (OrderType.BUY, OrderStatus.UNKNOWN),
    (999, OrderStatus.REPORTED),
  ],
)
async def test_external_authoritative_sell_or_unknown_order_blocks_entry(
  order_type,
  status,
) -> None:
  order = _authoritative_order(order_type=order_type, status=status)
  db = SimpleNamespace(
    execute=AsyncMock(side_effect=[_Result([order]), _Result([])]),
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="外部、其他策略或方向不明"):
    await service._require_no_authoritative_entry_order_conflict(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[],
      invalidate_auto_external_buy=False,
    )


@pytest.mark.asyncio
async def test_authoritative_order_already_in_pending_is_not_double_checked() -> None:
  order = _authoritative_order()
  pending = SimpleNamespace(
    broker_order_id="9001",
    strategy_run_id="run-1",
    request_metadata={"entry_plan_id": "run-1"},
  )
  db = SimpleNamespace(execute=AsyncMock(return_value=_Result([order])))
  service = TradeCommandService(db)

  await service._require_no_authoritative_entry_order_conflict(
    plan_id="run-1",
    account_id="account-1",
    instrument_code="605499.SH",
    working_orders=[pending],
    invalidate_auto_external_buy=False,
  )

  assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_terminal_authoritative_order_does_not_block_entry() -> None:
  # Return the terminal row from the fake even though the SQL status filter
  # would exclude it, proving the defensive terminal check remains fail-open.
  order = _authoritative_order(status=OrderStatus.SUCCEEDED)
  db = SimpleNamespace(execute=AsyncMock(return_value=_Result([order])))
  service = TradeCommandService(db)

  await service._require_no_authoritative_entry_order_conflict(
    plan_id="run-1",
    account_id="account-1",
    instrument_code="605499.SH",
    working_orders=[],
    invalidate_auto_external_buy=False,
  )

  assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_previous_trading_day_working_status_does_not_block_entry() -> None:
  order = _authoritative_order(
    order_time=command_module.time_utils.now() - timedelta(days=1)
  )
  db = SimpleNamespace(execute=AsyncMock(return_value=_Result([order])))
  service = TradeCommandService(db)

  await service._require_no_authoritative_entry_order_conflict(
    plan_id="run-1",
    account_id="account-1",
    instrument_code="605499.SH",
    working_orders=[],
    invalidate_auto_external_buy=False,
  )

  assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_plan_correlated_broker_only_order_fails_closed_for_reconcile() -> None:
  order = _authoritative_order()
  correlation = SimpleNamespace(
    broker_order_id="9001",
    strategy_run_id="run-1",
    request_metadata={"entry_plan_id": "run-1"},
  )
  db = SimpleNamespace(
    execute=AsyncMock(
      side_effect=[_Result([order]), _Result([correlation])]
    ),
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="仅见于 Broker"):
    await service._require_no_authoritative_entry_order_conflict(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[],
      invalidate_auto_external_buy=False,
    )


@pytest.mark.asyncio
async def test_old_reconcile_required_intent_blocks_new_entry() -> None:
  db = SimpleNamespace(scalar=AsyncMock(return_value="intent-old"))
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="未收敛成交意图"):
    await service._require_no_conflicting_entry_exit(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[],
    )

  db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_active_exit_plan_blocks_managed_entry() -> None:
  db = SimpleNamespace(
    scalar=AsyncMock(side_effect=[None, None, "exit-plan-1"])
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="正在持续清仓"):
    await service._require_no_conflicting_entry_exit(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      working_orders=[],
    )

  assert db.scalar.await_count == 3


@pytest.mark.asyncio
async def test_unattributed_durable_buy_invalidates_exact_authorization(
  monkeypatch,
) -> None:
  execute_results = iter(
    [
      _Result([SimpleNamespace(order_id="broker-order-1")]),
      _Result([]),
    ]
  )
  db = SimpleNamespace(
    execute=AsyncMock(side_effect=lambda _statement: next(execute_results))
  )
  invalidate = AsyncMock(return_value=True)

  class AuthorizationService:
    def __init__(self, _db):
      pass

    async def invalidate(self, **kwargs):
      return await invalidate(**kwargs)

  monkeypatch.setattr(
    command_module,
    "EntryPlanAuthorizationService",
    AuthorizationService,
  )
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="未归因真实买入"):
    await service._require_no_unattributed_buy_trades(
      plan_id="run-1",
      account_id="account-1",
      instrument_code="605499.SH",
      grant=SimpleNamespace(authorized_at=utcnow() - timedelta(seconds=30)),
    )

  invalidate.assert_awaited_once_with(
    plan_id="run-1",
    reason="ENTRY_UNATTRIBUTED_REAL_BUY",
    commit=True,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize("position_volume", [100, 200])
async def test_second_gate_rechecks_authoritative_plan_snapshot_and_position(
  monkeypatch,
  position_volume,
) -> None:
  parameters = _managed_entry_parameters()
  scope = scope_from_managed_entry_config(plan_id="run-1", config=parameters)
  rollout = SimpleNamespace(
    authorization_state="ENABLED",
    reconcile_status="READY",
    last_snapshot_id="snapshot-7",
    last_snapshot_hash="a" * 64,
    last_snapshot_at=utcnow(),
    # Exact automatic authorization deliberately does not borrow a manual
    # controlled window.
    controlled_window_active=False,
  )
  run = SimpleNamespace(
    mode="live",
    status="running",
    instruments=["605499.SH"],
    parameters=parameters,
  )
  strategy = SimpleNamespace(class_name="AshareManagedEntryPlanStrategy")
  intent = SimpleNamespace(
    strategy_run_id="run-1",
    instrument_code="605499.SH",
    direction="BUY",
    bucket="swing",
    status="PENDING",
    target_volume=100,
    target_amount=None,
    intent_metadata={
      "execution_mode": "AUTO",
      "entry_plan_id": "run-1",
      "entry_config_version": 3,
      "protected_limit_price": 100,
      "exact_auto_entry_authorized": True,
      "auto_entry_authorization_grant_id": "grant-1",
      "auto_entry_plan_fingerprint": scope.plan_fingerprint,
      "auto_entry_rule_fingerprint": scope.rule_fingerprint,
    },
  )
  plan_state = SimpleNamespace(
    custom_state={
      "managed_entry_plan": {
        "phase": "ENTRY_PENDING",
        "pending_intent_id": "intent-1",
        "filled_volume": 0,
        "filled_amount_cny": 0,
      }
    }
  )
  account = SimpleNamespace(total_asset=100_000, cash=50_000)
  position = SimpleNamespace(
    market_value=position_volume * 100,
    last_price=100,
    volume=position_volume,
  )
  grant = SimpleNamespace(
    subject_user_id="authorized-user",
    authorized_at=utcnow() - timedelta(seconds=30),
  )
  heartbeat = SimpleNamespace(
    status="READY",
    details={"capabilities": ["live"], "protocolVersion": "1.1"},
  )

  class Result:
    def __init__(self, value):
      self.value = value

    def one_or_none(self):
      return self.value

    def scalar_one_or_none(self):
      return self.value

    def scalars(self):
      return SimpleNamespace(all=lambda: self.value)

  execute_results = iter(
    [
      Result((run, strategy)),
      Result(plan_state),
      Result(account),
      Result([]),
      Result([]),
    ]
  )

  async def get(model, _key, **_kwargs):
    if model is AccountExecutionControl:
      return rollout
    if model is TradeIntentRecord:
      return intent
    if model is EntryPlanAuthorizationGrant:
      return grant
    if model is RuntimeComponentHeartbeat:
      return heartbeat
    return None

  db = SimpleNamespace(
    get=get,
    execute=AsyncMock(side_effect=lambda _statement: next(execute_results)),
    scalar=AsyncMock(return_value=position),
  )
  validation_args: dict = {}
  invalidation_args: dict = {}

  class AuthorizationService:
    def __init__(self, _db):
      pass

    async def validate_or_invalidate(self, **kwargs):
      validation_args.update(kwargs)
      return SimpleNamespace(
        valid=True,
        code="AUTHORIZED",
        balance=SimpleNamespace(grant_id="grant-1"),
      )

    async def invalidate(self, **kwargs):
      invalidation_args.update(kwargs)
      return True

  monkeypatch.setattr(
    command_module,
    "EntryPlanAuthorizationService",
    AuthorizationService,
  )
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(command_module.settings, "t_trade_live_enabled", True)
  monkeypatch.setattr(
    command_module.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )
  service = TradeCommandService(db)
  device = SimpleNamespace(id="device-1", user_id="authorized-user")
  service._device_for = AsyncMock(return_value=device)
  service._require_no_conflicting_entry_exit = AsyncMock()
  service._require_no_authoritative_entry_order_conflict = AsyncMock()

  call = service._exact_auto_entry_device(
    account_id="account-1",
    instrument_code="605499.SH",
    side="BUY",
    limit_price=Decimal("100"),
    volume=100,
    strategy_run_id="run-1",
    intent_id="intent-1",
    bucket="swing",
    policy_version=3,
    request_metadata={
      "entry_plan_id": "run-1",
      "auto_entry_authorization_grant_id": "grant-1",
      "auto_entry_plan_fingerprint": scope.plan_fingerprint,
      "auto_entry_rule_fingerprint": scope.rule_fingerprint,
    },
  )
  if position_volume > 100:
    with pytest.raises(RuntimeError, match="外部增仓"):
      await call
    assert invalidation_args["reason"] == "ENTRY_UNEXPLAINED_POSITION_INCREASE"
    assert invalidation_args["commit"] is True
    service._device_for.assert_not_awaited()
    return
  selected = await call

  assert selected is device
  assert validation_args["commit"] is False
  assert validation_args["proposed_amount_cny"] == Decimal("10000")
  assert validation_args["resulting_position_pct"] == Decimal("0.2")
  service._device_for.assert_awaited_once_with(
    user_id="authorized-user",
    account_id="account-1",
    execution_mode="live",
  )


class _ReportDatabase:
  def __init__(self, pending, intent):
    self.pending = pending
    self.intent = intent

  async def __aenter__(self):
    return self

  async def __aexit__(self, *_args):
    return False

  async def get(self, model, _key):
    if model is PendingTradeOrder:
      return self.pending
    if model is TradeIntentRecord:
      return self.intent
    return None


@pytest.mark.asyncio
async def test_only_live_buy_trade_consumes_exact_entry_grant(monkeypatch) -> None:
  pending = SimpleNamespace(
    execution_mode="live",
    side="BUY",
    account_id="account-1",
    strategy_run_id="run-1",
    intent_id="intent-1",
    request_metadata={
      "entry_plan_id": "run-1",
      "exact_auto_entry_authorized": True,
      "auto_entry_authorization_grant_id": "grant-1",
    },
  )
  intent = SimpleNamespace(
    strategy_run_id="run-1",
    direction="BUY",
    intent_metadata={
      "execution_mode": "AUTO",
      "auto_entry_authorization_grant_id": "grant-1",
    },
  )
  database = _ReportDatabase(pending, intent)
  consume = AsyncMock()

  class AuthorizationService:
    def __init__(self, _db):
      pass

    async def consume_real_fill(self, **kwargs):
      return await consume(**kwargs)

  monkeypatch.setattr(report_processor, "AsyncSessionLocal", lambda: database)
  monkeypatch.setattr(
    report_processor,
    "EntryPlanAuthorizationService",
    AuthorizationService,
  )

  await report_processor._consume_exact_auto_entry_fill(
    {"client_order_id": "client-1"},
    {
      "account_id": "account-1",
      "execution_id": "trade-1",
      "traded_price": 100,
      "traded_volume": 100,
      "traded_time": 1_787_190_060,
    },
  )
  assert consume.await_args.kwargs["grant_id"] == "grant-1"
  assert consume.await_args.kwargs["filled_amount_cny"] == 10_000
  assert consume.await_args.kwargs["trade_business_key"].endswith(":trade-1")

  pending.execution_mode = "paper"
  await report_processor._consume_exact_auto_entry_fill(
    {"client_order_id": "client-1"},
    {
      "execution_id": "paper-trade",
      "traded_price": 100,
      "traded_volume": 100,
    },
  )
  pending.execution_mode = "live"
  pending.side = "SELL"
  await report_processor._consume_exact_auto_entry_fill(
    {"client_order_id": "client-1"},
    {
      "execution_id": "sell-trade",
      "traded_price": 100,
      "traded_volume": 100,
    },
  )
  assert consume.await_count == 1
