"""Real consumed approval/allocation plus synthetic durable broker evidence."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.enums import OrderPriceType, OrderStatus, OrderType
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.live_entry_replacement_review import (
  review_live_entry_replacement,
)
from quantx_infrastructure.services.t_live_entry_authorization import (
  authorize_live_entry,
)
from sqlalchemy import func, select

from tests.infrastructure import test_t_allocation_repository as allocation
from tests.infrastructure import test_t_assistant_runtime_repository as runtime
from tests.infrastructure import test_t_entry_confirmation as confirmation
from tests.infrastructure import test_t_intent_atomic_intake as intake

allocation_sessions = confirmation.allocation_sessions
base_sessions = confirmation.base_sessions
sessions = confirmation.sessions
signing_key = confirmation.signing_key


@pytest.mark.parametrize(
  "fault,reason",
  [
    (None, None),
    ("zero_fill", None),
    ("unknown", "ORDER_RESULT_UNKNOWN"),
    ("missing_trade", "ORDER_CANCEL_UNCONFIRMED"),
    ("wrong_actor", "T_ENTRY_REPLACEMENT_ACTOR_CONFLICT"),
    ("wrong_batch", "T_ENTRY_REPLACEMENT_BATCH_CONFLICT"),
    ("projection", "T_ENTRY_REPLACEMENT_FILL_PROJECTION_CONFLICT"),
    ("expired", "T_ENTRY_ORDER_EXPIRED"),
    ("future", "T_ENTRY_REPLACEMENT_CLOCK_INVALID"),
    ("tampered", "T_ENTRY_CHALLENGE_TAMPERED"),
    ("disabled", "T_ENTRY_SOURCE_NOT_READY"),
    ("released", "T_ENTRY_AUTHORIZATION_SCOPE_INVALID"),
    ("wrong_side", "ORDER_CANCEL_UNCONFIRMED"),
    ("wrong_bucket", "ORDER_CANCEL_UNCONFIRMED"),
    ("second_attempt", "T_ENTRY_REPLACEMENT_SCOPE_INVALID"),
    ("extra_correlation", "T_ENTRY_REPLACEMENT_CHAIN_CONFLICT"),
    ("active", "T_ENTRY_ORDER_ACTIVE"),
    ("db_clock_offset", None),
    ("wrong_order_type", "T_ENTRY_REPLACEMENT_SCOPE_INVALID"),
  ],
)
async def test_replacement_preflight_preserves_original_authority(
  sessions,
  monkeypatch,
  fault,
  reason,
):
  now = allocation.NOW.replace(hour=1)
  monkeypatch.setattr(allocation, "NOW", now)
  monkeypatch.setattr(runtime, "NOW", now)
  monkeypatch.setattr(intake, "NOW", now)
  monkeypatch.setattr(confirmation, "NOW", now)
  confirmed = now + timedelta(seconds=1)
  monkeypatch.setattr(confirmation, "CONFIRMED", confirmed)
  async with sessions.kw["bind"].begin() as conn:
    await conn.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          Order.__table__,
          Trade.__table__,
          TTradeBatch.__table__,
          StrategyRuntimeEvent.__table__,
        ],
      )
    )
  snapshot, candidates = await confirmation.seed_confirmable(sessions)
  async with sessions() as db, db.begin():
    await confirmation.confirm(db)
    repo = allocation.TAllocationRepository(db)
    newer = confirmation.fresh(snapshot)
    candidates = tuple(replace(c, intent_version=1) for c in candidates)
    allocation_batch = await allocation._prepared(
      repo, newer, candidates, now=confirmed
    )
    claim = await allocation._claim(
      repo, allocation_batch, newer, candidates, now=confirmed
    )
    await repo.commit(claim=claim, snapshot=newer, candidates=candidates, now=confirmed)
    intent = await db.get(TradeIntentRecord, "intent-0")
    filled = 0 if fault == "zero_fill" else 4
    intent.status = "EXECUTION_PENDING" if not filled else "PARTIAL_FILLED"
    intent.executed_volume = filled
    original = confirmed.replace(tzinfo=None)
    scope = dict(
      account_id=intent.account_id,
      owner_type=intent.owner_type,
      owner_id=intent.owner_id,
      environment="LIVE",
      bucket=intent.bucket,
      intent_id=intent.id,
      batch_id=intent.intent_metadata["t_batch_id"],
      t_trade_role="ENTRY",
    )
    pending = PendingTradeOrder(
      client_order_id="parent",
      user_id="user-1",
      **scope,
      instrument_code=intent.instrument_code,
      side="BUY",
      order_type="FIX_PRICE",
      limit_price="9.9",
      volume=10,
      broker_order_id="42",
      status="RECONCILED_ZERO_FILL" if not filled else "CANCELLED",
      created_at=original,
      t_order_original_created_at=original,
      t_order_attempt=0,
      request_metadata={"t_entry_order_policy_version": "TEntryOrderPolicy.v1"},
    )
    batch = TTradeBatch(
      batch_id=scope["batch_id"],
      account_id=intent.account_id,
      instrument_code=intent.instrument_code,
      environment="LIVE",
      source_execution_owner_type=intent.owner_type,
      source_execution_owner_id=intent.owner_id,
      source_execution_environment="LIVE",
      entry_intent_id=intent.id,
      entry_filled_volume=filled,
    )
    db.add_all(
      [
        pending,
        batch,
        OrderCorrelation(
          id="correlation",
          client_order_id="parent",
          broker_order_id="42",
          trace_id="trace",
          **scope,
        ),
        Order(
          id=42,
          account_id=intent.account_id,
          stock_code=intent.instrument_code,
          sysid="broker-42",
          time=original,
          type=OrderType.BUY,
          volume=10,
          price_type=OrderPriceType.LIMIT,
          price=9.9,
          traded_volume=filled,
          traded_price=9.9,
          status=OrderStatus.CANCELED,
        ),
      ]
    )
    if filled and fault != "missing_trade":
      db.add(
        Trade(
          id="fill-42",
          time=original,
          price=9.9,
          volume=filled,
          amount=filled * 9.9,
          account_id=intent.account_id,
          stock_code=intent.instrument_code,
          order_id=42,
          order_sysid="broker-42",
          order_type=24 if fault == "wrong_side" else 23,
        )
      )
    reviewed_at = confirmed + timedelta(seconds=31)
    if fault == "unknown":
      pending.status = "UNKNOWN"
    elif fault == "wrong_actor":
      pending.user_id = "other"
    elif fault == "wrong_batch":
      batch.source_execution_owner_id = "other"
    elif fault == "projection":
      intent.executed_volume += 1
    elif fault == "expired":
      reviewed_at = confirmed + timedelta(seconds=60)
    elif fault == "future":
      pending.t_order_original_created_at = original + timedelta(seconds=40)
      pending.created_at = pending.t_order_original_created_at
    elif fault == "tampered":
      challenge = await db.get(TradeConfirmationChallenge, "challenge-1")
      challenge.payload_fingerprint = "0" * 64
    elif fault == "disabled":
      head = await db.get(TTradeGlobalConfig, "config-1")
      head.enabled = False
    elif fault == "released":
      intent.status = "FILLED"
    elif fault == "wrong_bucket":
      correlation = await db.get(OrderCorrelation, "correlation")
      correlation.bucket = "core"
    elif fault == "second_attempt":
      pending.t_order_attempt = 1
    elif fault == "extra_correlation":
      db.add(
        OrderCorrelation(
          id="orphan",
          client_order_id="orphan",
          broker_order_id="43",
          trace_id="orphan",
          **scope,
        )
      )
    elif fault == "active":
      reviewed_at = confirmed + timedelta(seconds=29)
    elif fault == "wrong_order_type":
      pending.order_type = "MARKET"
    elif fault == "db_clock_offset":
      pending.created_at = original + timedelta(milliseconds=10)
    await db.flush()
    before = (intent.status, intent.allocation_version, dict(intent.intent_metadata))
    call = review_live_entry_replacement(
      db,
      client_order_id="parent",
      now=reviewed_at,
      reference_price=Decimal("9.88"),
      price_tick=Decimal("0.01"),
      limit_up=Decimal("11"),
      limit_down=Decimal("9"),
    )
    if reason:
      with pytest.raises(ValueError, match=reason):
        await call
    else:
      result = await call
      assert result.remaining_volume == 10 - filled
      assert result.filled_volume == filled
      assert result.user_id == "user-1"
      assert result.limit_price == Decimal("9.90")
      assert result.expires_at == confirmed + timedelta(seconds=60)
      assert result.original_created_at == confirmed
      # Preflight never makes an existing intent eligible for initial dispatch.
      with pytest.raises(ValueError, match="T_ENTRY_AUTHORIZATION_SCOPE_INVALID"):
        await authorize_live_entry(
          db,
          intent=intent,
          account_id=intent.account_id,
          instrument_code=intent.instrument_code,
          volume=result.remaining_volume,
          limit_price=result.limit_price,
          now=reviewed_at,
        )
    assert (intent.status, intent.allocation_version, intent.intent_metadata) == before
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 1
