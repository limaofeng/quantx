"""Engine-owned recovery dispatcher for durable LIVE BUY admission."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from quantx_contracts import ExecutionEnvironment
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_risk_increase_admission import (
  ADMISSION_RENEW_INTERVAL_SECONDS,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from sqlalchemy import select

logger = logging.getLogger(__name__)


class RiskIncreaseAdmissionRuntime:
  """Recover READY/PREPARED admission work independently of producers."""

  def __init__(self, *, interval_seconds: float = ADMISSION_RENEW_INTERVAL_SECONDS, live_entry_review_factory=None):
    self.live_entry_review_factory = live_entry_review_factory
    self.interval_seconds = float(interval_seconds)
    self.instance_id = str(uuid.uuid4())
    self._task: Optional[asyncio.Task] = None
    self._stopping = asyncio.Event()

  @property
  def is_running(self) -> bool:
    return bool(self._task and not self._task.done())

  async def start(self) -> None:
    if self.is_running:
      return
    self._stopping = asyncio.Event()
    # Complete one bounded recovery scan before Engine advertises trading
    # readiness; failures remain durable and are retried by the loop.
    await self.recover_once()
    self._task = asyncio.create_task(
      self._run(),
      name="RiskIncreaseAdmissionRuntime",
    )
    logger.info("公共 LIVE BUY admission dispatcher 已启动")

  async def stop(self) -> None:
    self._stopping.set()
    if self._task is None:
      return
    self._task.cancel()
    try:
      await self._task
    except asyncio.CancelledError:
      pass
    finally:
      self._task = None
    logger.info("公共 LIVE BUY admission dispatcher 已停止")

  async def _run(self) -> None:
    while not self._stopping.is_set():
      try:
        await self.recover_once()
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception("公共 LIVE BUY admission 恢复扫描失败")
      try:
        await asyncio.wait_for(
          self._stopping.wait(),
          timeout=self.interval_seconds,
        )
      except asyncio.TimeoutError:
        pass

  async def recover_once(self) -> dict[str, int]:
    """Scan committed READY/PREPARED work and attempt account dispatch."""

    async with AsyncSessionLocal() as db:
      ready_accounts = set(
        str(value)
        for value in (
          await db.scalars(
            select(TradeIntentRecord.account_id)
            .where(
              TradeIntentRecord.environment == ExecutionEnvironment.LIVE.value,
              TradeIntentRecord.direction == "BUY",
              TradeIntentRecord.status == "EXECUTION_READY",
            )
            .distinct()
          )
        ).all()
        if value
      )
      prepared_accounts = set(
        str(value)
        for value in (
          await db.scalars(
            select(AccountRiskIncreaseAdmissionBatch.account_id)
            .where(
              AccountRiskIncreaseAdmissionBatch.environment
              == ExecutionEnvironment.LIVE.value,
              AccountRiskIncreaseAdmissionBatch.status == "PREPARED",
            )
            .distinct()
          )
        ).all()
        if value
      )
      account_ids = sorted(ready_accounts | prepared_accounts)
      dispatched = 0
      for account_id in account_ids:
        try:
          kwargs = (
            {"live_entry_review": self.live_entry_review_factory(db)}
            if self.live_entry_review_factory is not None else {}
          )
          result = await TradeCommandService(db, **kwargs).dispatch_ready_risk_increase_orders(
            account_id=account_id,
            processing_owner=f"engine-admission:{self.instance_id}",
          )
          dispatched += len(result)
        except asyncio.CancelledError:
          raise
        except Exception as exc:
          await db.rollback()
          logger.warning(
            "LIVE BUY admission 将重试: error_type=%s",
            type(exc).__name__,
          )
      return {"accounts": len(account_ids), "dispatched": dispatched}


def _live_entry_review(db):
  # Resolve the Engine-owned monitor only when its recovery dispatcher runs.
  from .t_trade_runtime import t_assistant_live_supervisor

  return t_assistant_live_supervisor.entry_review_adapter(db)


risk_increase_admission_runtime = RiskIncreaseAdmissionRuntime(live_entry_review_factory=_live_entry_review)

__all__ = [
  "RiskIncreaseAdmissionRuntime",
  "risk_increase_admission_runtime",
]
