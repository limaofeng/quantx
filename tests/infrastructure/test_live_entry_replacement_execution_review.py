"""Real portfolio, attribution, sizing and risk; approval preflight is a test seam."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_application.t_trade_v3.daily_t_valuation import TValuationMark
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.t_allocation import TAllocationDecisionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import (
  live_entry_replacement_execution_review as review_module,
)
from quantx_infrastructure.services.live_entry_replacement_review import (
  LiveEntryReplacementPreflight,
)
from sqlalchemy.orm.attributes import flag_modified

from tests.infrastructure import test_live_portfolio_snapshot as portfolio_tests
from tests.infrastructure.test_live_position_attribution import CODE, NOW, add_buy

capacity_db = portfolio_tests.capacity_db
base_db = portfolio_tests.base_db
db = portfolio_tests.db


@pytest.mark.parametrize(
  "fault,reason",
  [
    (None, None),
    ("staged", None),
    ("expiry", None),
    ("claimed", "T_NO_REPLACEABLE_OLD_POSITION"),
    ("disabled", "T_ACCOUNT_ENTRY_DISABLED"),
    ("budget", "LIVE_REVIEW_ZERO_SIZE"),
    ("pending", "T_SAME_SYMBOL_ENTRY_OBLIGATION"),
    ("unobserved", "T_SAME_SYMBOL_ENTRY_OBLIGATION"),
    ("stale", "T_ORDER_MARKET_EVIDENCE_INVALID"),
    ("crossed", "T_ORDER_MARKET_EVIDENCE_INVALID"),
    ("depth", "T_ORDER_MARKET_EVIDENCE_INVALID"),
    ("mark", "LIVE_REPLACEMENT_MARKET_CUT_CONFLICT"),
    ("approval", "APPROVAL_REVOKED"),
  ],
)
async def test_replacement_keeps_existing_exposure_and_reads_current_risk(
  db, monkeypatch, fault, reason
):
  proof = LiveEntryReplacementPreflight(
    "client",
    "intent",
    "new-source",
    "fixture",
    100,
    100,
    Decimal("10.03"),
    NOW - timedelta(seconds=31),
    NOW + timedelta(seconds=29),
    2,
  )
  preflight = AsyncMock(return_value=proof)
  authority = AsyncMock(return_value="fixture")
  if fault == "approval":
    authority.side_effect = ValueError("APPROVAL_REVOKED")
  monkeypatch.setattr(review_module, "review_live_entry_replacement", preflight)
  monkeypatch.setattr(review_module, "_authorize_live_entry", authority)
  async with db.begin():
    await add_buy(
      db,
      owner_id="new-source",
      requested_volume=200,
      status="CANCELLED",
      orders=[]
      if fault == "unobserved"
      else [dict(account_id="account", order_id=101)],
    )
    parent = await db.get(PendingTradeOrder, "client")
    parent.t_order_original_created_at = proof.original_created_at.replace(tzinfo=None)
    parent.created_at = proof.original_created_at.replace(tzinfo=None)
    parent.order_type = "FIX_PRICE"
    parent.updated_at = NOW
    flag_modified(parent, "updated_at")
    db.add(
      TTradeBatch(
        batch_id="batch",
        account_id="account",
        instrument_code=CODE,
        source_execution_owner_type="T_ASSISTANT_EXECUTION",
        source_execution_owner_id="new-source",
        source_execution_environment="LIVE",
        environment="LIVE",
        entry_intent_id="intent",
        entry_filled_volume=100,
        exit_filled_volume=0,
        commission_rate=0.0003,
        minimum_commission=5,
        stamp_tax_rate=0.0005,
        transfer_fee_rate=0.00001,
        policy_version=1,
        created_at=NOW,
        updated_at=NOW,
      )
    )
    intent = TradeIntentRecord(
      id="intent",
      owner_type="T_ASSISTANT_EXECUTION",
      owner_id="new-source",
      environment="LIVE",
      account_id="account",
      instrument_code=CODE,
      direction="BUY",
      reason="T_ENTRY_REPLACEMENT",
      bucket="swing",
      status="PARTIAL_FILLED",
      executed_volume=100,
      idempotency_key="intent",
      allocation_cycle_id="cycle",
      allocation_decision_id="decision",
      allocation_version=2,
      intent_metadata=dict(
        t_trade_role="ENTRY",
        t_batch_id="batch",
        intent_created_at=proof.original_created_at.isoformat(),
      ),
      created_at=NOW,
      updated_at=NOW,
    )
    db.add(intent)
    db.add(
      TAllocationDecisionRecord(
        decision_id="decision",
        allocation_batch_id="allocation",
        intent_id="intent",
        intent_version=1,
        candidate_id="candidate",
        instrument_code=CODE,
        rank=1,
        action="ALLOW",
        requested_amount_ceiling=1100 if fault == "budget" else 3000,
        allocated_amount_cap=1100 if fault == "budget" else 3000,
        evidence={},
        created_at=proof.original_created_at,
        expires_at=NOW + timedelta(seconds=10)
        if fault == "expiry"
        else proof.expires_at,
      )
    )
    if fault == "claimed":
      db.add(
        AutoExitPlanRecord(
          plan_id="other-exit",
          source_type="MANUAL_POSITION",
          source_id="other-exit",
          account_id="account",
          instrument_code=CODE,
          source_execution_owner_type="MANUAL_COMMAND",
          source_execution_owner_id="other-exit",
          source_execution_environment="LIVE",
          environment="LIVE",
          status="ACTIVE",
          protected_volume=700,
          remaining_volume=700,
          entry_avg_price=10,
          created_at=NOW,
          updated_at=NOW,
        )
      )
    if fault == "disabled":
      control = await db.get(AccountExecutionControl, "account")
      control.authorization_state = "DISABLED"
      control.updated_at = NOW
      flag_modified(control, "updated_at")
    if fault == "pending":
      db.add(
        PendingTradeOrder(
          client_order_id="other",
          user_id="fixture",
          account_id="account",
          owner_type="T_ASSISTANT_EXECUTION",
          owner_id="new-source",
          environment="LIVE",
          instrument_code=CODE,
          side="BUY",
          order_type="FIX_PRICE",
          limit_price="10",
          volume=100,
          status="QUEUED",
          batch_id="other-batch",
          bucket="swing",
          t_trade_role="ENTRY",
          intent_id="other-intent",
          created_at=NOW,
          updated_at=NOW,
        )
      )
    await db.flush()
  market = MarketDataSnapshot(
    CODE,
    timestamp=NOW,
    price=10,
    price_tick=0.01,
    limit_up=11,
    limit_down=9,
    bid_price=[9.99, 9.98, 9.97, 9.96, 9.95],
    ask_price=[10, 10.01, 10.02, 10.03, 10.04],
    bid_vol=[1000] * 5,
    ask_vol=[1000] * 5,
  )
  if fault == "stale":
    market.timestamp -= timedelta(seconds=3)
  elif fault == "crossed":
    market.bid_price[0] = 10.01
  elif fault == "depth":
    market.ask_vol = [1000]

  class Marks:
    async def read(self, **kwargs):
      return SimpleNamespace(
        as_of=kwargs["as_of"],
        opening={},
        current={
          CODE: TValuationMark(
            CODE, Decimal("10.01") if fault == "mark" else Decimal(10), NOW, "quote"
          )
        },
      )

  async with db.begin():
    call = review_module.LiveEntryReplacementExecutionReview(db).review(
      client_order_id="client",
      market_data=market,
      market_mark_reader=Marks(),
      now=NOW,
    )
    if fault in {"mark", "approval"}:
      with pytest.raises(ValueError, match=reason):
        await call
    else:
      result = await call
      if reason:
        assert result.outcome != "REVIEWED"
        assert reason in result.reason_codes
        authority.assert_not_awaited()
      else:
        assert result.outcome == "REVIEWED", result.reason_codes
        assert result.request.volume == 100
        assert result.request.price == 10.03
        assert result.request.metadata["order_expire_at_ms"] == int(
          (
            NOW + timedelta(seconds=10) if fault == "expiry" else proof.expires_at
          ).timestamp()
          * 1000
        )
        envelope = result.request.metadata["t_trading_envelope"]
        assert envelope["protected_old_position_floor"] == 200
        assert envelope["observed_position_projection"]["swing"]["total_volume"] == 300
        assert result.portfolio_input_fingerprint
        authority.assert_awaited_once()
        assert authority.call_args.kwargs["volume"] == 100
    if fault == "staged":
      from quantx_infrastructure.services.live_entry_dispatch_review import (
        revalidate_live_entry_dispatch,
      )
      from quantx_infrastructure.services.live_entry_replacement_staging import (
        stage_live_entry_replacement,
      )

      staged = await stage_live_entry_replacement(
        db,
        client_order_id="client",
        market_data=market,
        market_mark_reader=Marks(),
        now=NOW,
        validate_market=lambda: None,
      )
      assert staged.status == "STAGED"

      async def fresh_review(**kwargs):
        return await review_module.LiveEntryReplacementExecutionReview(db).review(
          client_order_id="client",
          market_data=market,
          market_mark_reader=Marks(),
          now=kwargs["now"],
        )

      again = await revalidate_live_entry_dispatch(
        db,
        intent=intent,
        volume=100,
        limit_price=Decimal("10.03"),
        now=NOW,
        fresh_review=fresh_review,
      )
      assert again.outcome == "REVIEWED" and again.request.volume == 100
    assert intent.status == (
      "EXECUTION_READY" if fault == "staged" else "PARTIAL_FILLED"
    )
    assert intent.executed_volume == 100
