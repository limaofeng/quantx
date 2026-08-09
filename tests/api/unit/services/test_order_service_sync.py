from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.services.order_service import OrderService


@pytest.mark.asyncio
async def test_sync_today_orders_reads_persisted_agent_reports() -> None:
  persisted_orders = [object(), object()]
  service = OrderService(account_id="account-1")
  service.get_today_orders = AsyncMock(return_value=persisted_orders)

  result = await service.sync_today_orders("account-1")

  service.get_today_orders.assert_awaited_once_with("account-1")
  assert result.saved_entities == persisted_orders
  assert result.saved_count == 2
  assert result.inserted_count == 0
  assert result.updated_count == 0


def test_order_service_has_no_qmt_manager() -> None:
  service = OrderService(account_id="account-1")

  assert not hasattr(service, "trading_manager")
