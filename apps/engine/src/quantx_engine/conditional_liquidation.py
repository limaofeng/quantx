"""Engine-owned conditional liquidation monitor."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.repositories.conditional_liquidation_order_repository import (
  ConditionalLiquidationOrderRepository,
)
from quantx_infrastructure.services.liquidation_service import LiquidationService

logger = logging.getLogger(__name__)


class ConditionalLiquidationMonitor:
  def __init__(self, interval_seconds: float = 5.0):
    self.interval_seconds = max(1.0, float(interval_seconds or 5.0))
    self._task: Optional[asyncio.Task] = None
    self._stopping = asyncio.Event()

  async def start(self) -> None:
    if self._task and not self._task.done():
      return
    self._stopping = asyncio.Event()
    self._task = asyncio.create_task(self._run(), name="ConditionalLiquidationMonitor")
    logger.info("条件清仓单监控器已启动")

  async def stop(self) -> None:
    self._stopping.set()
    if not self._task:
      return
    self._task.cancel()
    try:
      await self._task
    except asyncio.CancelledError:
      pass
    logger.info("条件清仓单监控器已停止")

  async def _run(self) -> None:
    while not self._stopping.is_set():
      try:
        await self.evaluate_all_active_orders()
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        logger.warning("条件清仓单监控扫描失败: %s", exc)
      try:
        await asyncio.wait_for(
          self._stopping.wait(),
          timeout=self.interval_seconds,
        )
      except asyncio.TimeoutError:
        continue

  async def evaluate_all_active_orders(
    self,
    *,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
  ) -> list:
    async for db in get_async_db():
      repo = ConditionalLiquidationOrderRepository(db)
      orders = await repo.find_active(
        account_id=account_id,
        stock_code=stock_code,
      )
      break
    else:
      orders = []

    results = []
    for order in orders:
      account_type = getattr(order, "account_type", None) or AccountType.STOCK
      if isinstance(account_type, str):
        account_type = AccountType.__members__.get(account_type, AccountType.STOCK)
      result = await LiquidationService(
        account_id=order.account_id,
        account_type=account_type,
      ).evaluate_conditional_liquidation_order(order)
      results.append(result)
    return results


conditional_liquidation_monitor = ConditionalLiquidationMonitor()
