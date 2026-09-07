from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_infrastructure.models.enums import InstrumentType, OrderType, PriceType
from quantx_infrastructure.services.trading_service import (
  InvalidOrderError,
  TradingService,
)


def make_stock_info(**overrides):
  data = {
    "id": "000001.SZ",
    "type": InstrumentType.STOCK,
    "min_market_order_volume": 100,
    "max_market_order_volume": 1000000,
    "up_stop_price": Decimal("11.00"),
    "down_stop_price": Decimal("9.00"),
  }
  data.update(overrides)
  return SimpleNamespace(**data)


def test_close_position_sell_allows_odd_lot_volume():
  service = TradingService.__new__(TradingService)

  assert service._validate_order_volume(
    150,
    make_stock_info(),
    order_type=OrderType.SELL,
    close_position=True,
  )


def test_ordinary_sell_still_rejects_odd_lot_volume():
  service = TradingService.__new__(TradingService)

  assert not service._validate_order_volume(
    150,
    make_stock_info(),
    order_type=OrderType.SELL,
    close_position=False,
  )


def test_calculate_commission_uses_default_fee_constants():
  service = TradingService.__new__(TradingService)

  assert service._calculate_commission(Decimal("6041"), OrderType.BUY) == Decimal(
    "5.06041"
  )


@pytest.mark.asyncio
async def test_market_order_returns_queued_client_order_without_broker_id():
  service = TradingService(account_id="account-1")
  queued = SimpleNamespace(
    client_order_id="client-1",
    status="QUEUED",
  )

  class SessionContext:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, *_):
      return None

  with patch(
    "quantx_infrastructure.services.trading_service.AsyncSessionLocal",
    return_value=SessionContext(),
  ), patch(
    "quantx_infrastructure.services.trading_service.TradeCommandService"
  ) as command_service:
    command_service.return_value.enqueue_order_for_account = AsyncMock(
      return_value=queued
    )
    result = await service.place_order(
      stock_code="000001.SZ",
      order_type=OrderType.BUY,
      order_volume=100,
      price_type=PriceType.FIX_PRICE,
      price=10,
      idempotency_key="trading-service-paper",
      execution_ref=ExecutionOwnerRef.manual_command("trading-service-test"),
      environment=ExecutionEnvironment.PAPER,
    )

  assert result == {
    "success": True,
    "order_id": None,
    "client_order_id": "client-1",
    "status": "QUEUED",
    "message": "交易命令已排队",
  }
  command_service.return_value.enqueue_order_for_account.assert_awaited_once()


@pytest.mark.asyncio
async def test_t_exit_policy_version_is_audit_evidence_not_numeric_config() -> None:
  service = TradingService(account_id="account-1")
  queued = SimpleNamespace(client_order_id="client-exit", status="QUEUED")

  class SessionContext:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, *_):
      return None

  with patch(
    "quantx_infrastructure.services.trading_service.AsyncSessionLocal",
    return_value=SessionContext(),
  ), patch(
    "quantx_infrastructure.services.trading_service.TradeCommandService"
  ) as command_service:
    command_service.return_value.enqueue_order_for_account = AsyncMock(
      return_value=queued
    )
    await service.place_order(
      stock_code="600000.SH",
      order_type=OrderType.SELL,
      order_volume=100,
      price_type=PriceType.FIX_PRICE,
      price=9.97,
      idempotency_key="public-t-exit",
      execution_ref=ExecutionOwnerRef("EXIT_PLAN", "exit-plan-1"),
      environment=ExecutionEnvironment.PAPER,
      execution_context={
        "intent_id": "intent-exit",
        "t_batch_id": "batch-1",
        "t_trade_role": "exit",
        "config_version": 7,
        "exit_policy_version": "TExitOrderPolicy.v1",
        "t_exit_order_policy_version": "TExitOrderPolicy.v1",
      },
    )

  request = command_service.return_value.enqueue_order_for_account.await_args.kwargs
  assert request["batch_id"] == "batch-1"
  assert request["t_trade_role"] == "exit"
  assert request["policy_version"] == 7
  assert request["request_metadata"]["exit_policy_version"] == (
    "TExitOrderPolicy.v1"
  )
  assert request["request_metadata"]["t_exit_order_policy_version"] == (
    "TExitOrderPolicy.v1"
  )


@pytest.mark.asyncio
async def test_invalid_volume_is_rejected_before_command_queue_access():
  service = TradingService(account_id="account-1")

  with pytest.raises(InvalidOrderError, match="订单数量必须大于 0"):
    await service.place_order(
      stock_code="000001.SZ",
      order_type=OrderType.BUY,
      order_volume=0,
      price_type=PriceType.FIX_PRICE,
      price=10,
      idempotency_key="trading-service-invalid",
      execution_ref=ExecutionOwnerRef.manual_command("trading-service-invalid"),
      environment=ExecutionEnvironment.PAPER,
    )


@pytest.mark.asyncio
async def test_place_order_rejects_missing_idempotency_key_before_queue_access():
  service = TradingService(account_id="account-1")
  with pytest.raises(InvalidOrderError, match="IDEMPOTENCY_KEY_REQUIRED"):
    await service.place_order(
      stock_code="000001.SZ",
      order_type=OrderType.BUY,
      order_volume=100,
      price_type=PriceType.FIX_PRICE,
      price=10,
      idempotency_key="",
      execution_ref=ExecutionOwnerRef.manual_command("trading-service-missing"),
      environment=ExecutionEnvironment.PAPER,
    )


@pytest.mark.asyncio
async def test_execute_strategy_orders_rejects_missing_key_without_partial_enqueue():
  service = TradingService(account_id="account-1")
  place_order = AsyncMock()
  with patch.object(service, "place_order", place_order):
    with pytest.raises(InvalidOrderError, match="每个订单提供稳定"):
      await service.execute_strategy_orders(
        "strategy-1",
        [
          {
            "stock_code": "000001.SZ",
            "order_type": OrderType.BUY,
            "quantity": 100,
            "price": 10,
            "idempotency_key": "batch-1-order-1",
          },
          {
            "stock_code": "000002.SZ",
            "order_type": OrderType.BUY,
            "quantity": 100,
            "price": 10,
          },
        ],
        execution_ref=ExecutionOwnerRef.strategy_run("run-1"),
        environment=ExecutionEnvironment.PAPER,
      )
  place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_strategy_orders_passes_distinct_explicit_batch_keys():
  service = TradingService(account_id="account-1")
  place_order = AsyncMock(
    side_effect=[
      {"success": True, "status": "QUEUED"},
      {"success": True, "status": "QUEUED"},
    ]
  )
  with patch.object(service, "place_order", place_order):
    result = await service.execute_strategy_orders(
      "strategy-1",
      [
        {
          "stock_code": "000001.SZ",
          "order_type": OrderType.BUY,
          "quantity": 100,
          "price": 10,
          "idempotency_key": "batch-1-order-1",
        },
        {
          "stock_code": "000001.SZ",
          "order_type": OrderType.BUY,
          "quantity": 100,
          "price": 10,
          "idempotency_key": "batch-2-order-1",
        },
      ],
      execution_ref=ExecutionOwnerRef.strategy_run("run-1"),
      environment=ExecutionEnvironment.PAPER,
    )

  assert result["success_count"] == 2
  assert [call.kwargs["idempotency_key"] for call in place_order.await_args_list] == [
    "batch-1-order-1",
    "batch-2-order-1",
  ]
