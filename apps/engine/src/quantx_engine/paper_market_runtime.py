"""Feed accepted books to isolated PAPER facts, including stopped producers."""

import logging
import math
from datetime import UTC, datetime
from types import SimpleNamespace

from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.services.paper_broker_matching import PaperBrokerMatching
from quantx_infrastructure.services.paper_execution_ledger import (
  PaperExecutionLedger,
  _stored_time,
)
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)
_ACTIVE_ORDERS = ("PENDING", "SUBMITTED", "PARTIAL_FILLED")


def accepted_paper_market(instrument_code, raw, *, now):
  """Project the original hub book without inventing depth or price limits."""

  def required(*names):
    values = [raw[name] for name in names if name in raw and raw[name] is not None]
    if not values or any(value != values[0] for value in values):
      raise ValueError("PAPER_MARKET_FIELD_REQUIRED_OR_CONFLICTING")
    return values[0]

  def number(*names, positive=False):
    value = required(*names)
    if (
      type(value) not in (int, float)
      or not math.isfinite(value)
      or value < 0
      or (positive and value == 0)
    ):
      raise ValueError("PAPER_MARKET_NUMBER_INVALID")
    return value

  stream, generation = required("market_stream_id"), required("continuity_generation")
  source = required("source_time_ms")
  ordinal = required("tick_ordinal")
  fence = required("market_stream_sequence")
  if (
    not isinstance(stream, str)
    or not stream
    or any(
      type(value) is not int or value <= 0
      for value in (generation, source, ordinal, fence)
    )
  ):
    raise ValueError("PAPER_MARKET_LINEAGE_REQUIRED")
  timestamp = datetime.fromtimestamp(source / 1000, tz=UTC)
  if now.tzinfo is None or timestamp > now:
    raise ValueError("PAPER_MARKET_TIME_INVALID")
  tick = SimpleNamespace(
    code=instrument_code,
    time=timestamp,
    last_price=number("lastPrice", "last_price", positive=True),
    price_tick=number("priceTick", "PriceTick", "price_tick", positive=True),
    up_stop_price=number(
      "upperLimit", "upStopPrice", "UpStopPrice", "up_stop_price", positive=True
    ),
    down_stop_price=number(
      "lowerLimit", "downStopPrice", "DownStopPrice", "down_stop_price", positive=True
    ),
    stock_status=number("stockStatus", "stock_status"),
    volume=number("volume"),
    amount=number("amount"),
    bid_price=required("bidPrice", "bid_price"),
    ask_price=required("askPrice", "ask_price"),
    bid_vol=required("bidVol", "bid_vol"),
    ask_vol=required("askVol", "ask_vol"),
  )
  market = MarketDataSnapshot.from_tick(tick)
  market.source = "PAPER_ACCEPTED_WHOLE_QUOTE"
  PaperBrokerMatching._validate_quote(market)
  identity = {
    "stream_id": stream,
    "generation": generation,
    "source_time_ms": source,
    "tick_ordinal": ordinal,
    "instrument_code": instrument_code,
  }
  return "hub:" + stable_manifest_hash(identity), market


class PaperMarketRuntime:
  def __init__(self, *, session_factory, clock):
    self.session_factory = session_factory
    self.clock = clock

  async def on_quote_batch(self, data, *, active_instruments, now):
    if not data:
      return 0
    async with self.session_factory() as db:
      accounts = list((await db.scalars(select(PaperExecutionAccountRecord))).all())
      if not accounts:
        return 0
      scopes = {
        account.execution_id: set(active_instruments.get(account.execution_id, ()))
        for account in accounts
      }
      for account in accounts:
        if account.execution_id in active_instruments:
          scopes[account.execution_id].update(
            account.broker_checkpoint["material"]["positions"]
          )
      orders = list(
        (
          await db.scalars(
            select(PaperExecutionOrderRecord).where(
              PaperExecutionOrderRecord.execution_id.in_(scopes),
              PaperExecutionOrderRecord.environment == "PAPER",
              PaperExecutionOrderRecord.status.in_(_ACTIVE_ORDERS),
            )
          )
        ).all()
      )
      plans = list(
        (
          await db.scalars(
            select(AutoExitPlanRecord).where(
              AutoExitPlanRecord.source_execution_owner_type == "T_ASSISTANT_EXECUTION",
              AutoExitPlanRecord.source_execution_owner_id.in_(scopes),
              AutoExitPlanRecord.environment == "PAPER",
              AutoExitPlanRecord.remaining_volume > 0,
            )
          )
        ).all()
      )
      for order in orders:
        scopes[order.execution_id].add(order.instrument_code)
      for plan in plans:
        scopes[plan.source_execution_owner_id].add(plan.instrument_code)
    applied = 0
    for execution_id, codes in sorted(scopes.items()):
      for code in sorted(codes & data.keys()):
        try:
          key, market = accepted_paper_market(code, data[code], now=now)
        except ValueError as exc:
          # No book is fabricated. Existing orders remain durable; fresh complete
          # evidence can resume them, while the final gate rejects stale evidence.
          logger.warning(
            "PAPER market blocked: execution=%s symbol=%s reason=%s",
            execution_id,
            code,
            str(exc),
          )
          continue
        async with self.session_factory() as db, db.begin():
          ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
          await ledger._account(execution_id, lock=True)
          existing = await db.scalar(
            select(PaperExecutionEventRecord).where(
              PaperExecutionEventRecord.execution_id == execution_id,
              PaperExecutionEventRecord.event_key == key,
            )
          )
          receipt = await ledger.process_quote(
            execution_id=execution_id,
            event_key=key,
            quote=market,
            accepted_at=_stored_time(existing.occurred_at)
            if existing is not None
            else self.clock(),
          )
        applied += not receipt.duplicate
    return applied
