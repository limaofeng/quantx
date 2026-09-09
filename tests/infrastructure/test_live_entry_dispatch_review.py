"""Persisted review plus a fresh Engine review are both mandatory at dispatch."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.trading.risk_checker import OrderRiskDecision
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import live_entry_request_staging as staging
from quantx_infrastructure.services.live_entry_dispatch_review import (
  revalidate_live_entry_dispatch,
)
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryReviewResult,
)

from tests.application.test_t_entry_execution_gate import gate_input as _gate_input
from tests.infrastructure.test_live_portfolio_snapshot import CODE, NOW, add_ready
from tests.infrastructure.test_live_portfolio_snapshot import base_db as _base_db
from tests.infrastructure.test_live_portfolio_snapshot import (
  capacity_db as _capacity_db,
)
from tests.infrastructure.test_live_portfolio_snapshot import db as _db
from tests.infrastructure.test_paper_execution_ledger import quote

base_db = _base_db
capacity_db = _capacity_db
db = _db
gate_input = _gate_input


def at_now(gate):
  shift = int(NOW.timestamp() * 1000) - gate.evaluated_at_ms
  candidate = replace(
    gate.candidate,
    latched_at_ms=gate.candidate.latched_at_ms + shift,
    source_time_ms=gate.candidate.source_time_ms + shift,
    expires_at_ms=gate.candidate.expires_at_ms + shift,
  )
  tick = replace(
    gate.latest_tick,
    received_at_ms=gate.latest_tick.received_at_ms + shift,
    sample=replace(
      gate.latest_tick.sample,
      source_time_ms=gate.latest_tick.sample.source_time_ms + shift,
    ),
  )
  return replace(
    gate,
    candidate=candidate,
    latest_tick=tick,
    execution_environment=ExecutionEnvironment.LIVE,
    intent_created_at_ms=gate.intent_created_at_ms + shift,
    intent_expires_at_ms=gate.intent_expires_at_ms + shift,
    evaluated_at_ms=gate.evaluated_at_ms + shift,
  )


@pytest.mark.parametrize(
  "fault",
  [
    None,
    "no_hook",
    "expired",
    "future",
    "request",
    "allocation",
    "rejected",
    "smaller",
    "actor",
  ],
)
async def test_dispatch_requires_exact_fresh_review(db, gate_input, monkeypatch, fault):
  gate = at_now(gate_input)
  async with db.begin():
    await add_ready(db, "own")
  request = OrderRequest(
    instrument_code=CODE,
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=9.9,
    execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "new-source"),
    environment=ExecutionEnvironment.LIVE,
    metadata={"portfolio_input_fingerprint": "a" * 64},
  )
  reviewed = LiveEntryReviewResult(
    "REVIEWED", (), "user", request, None, OrderRiskDecision.allow(request)
  )
  monkeypatch.setattr(
    staging.LiveEntryExecutionReview, "review", AsyncMock(return_value=reviewed)
  )
  async with db.begin():
    await staging.stage_live_entry_request(
      db,
      execution_id="new-source",
      intent_id="own",
      gate_input=gate,
      market_data=replace(quote(0), timestamp=NOW),
      market_mark_reader=None,
      now=NOW,
      validate_market=lambda: None,
    )
  async with db.begin():
    intent = await db.get(TradeIntentRecord, "own")
    current = NOW
    if fault == "expired":
      current += timedelta(seconds=4)
    elif fault == "future":
      current -= timedelta(seconds=1)
    elif fault == "request":
      from copy import deepcopy

      metadata = deepcopy(intent.intent_metadata)
      metadata["risk_increase_order_request"]["risk_decision_id"] = "changed"
      intent.intent_metadata = metadata
    elif fault == "allocation":
      intent.allocation_version += 1
    elif fault == "rejected":
      reviewed = LiveEntryReviewResult("REJECT", ("T_MARKET_NOT_READY",))
    elif fault == "smaller":
      reviewed = replace(reviewed, request=replace(request, volume=50))
    elif fault == "actor":
      reviewed = replace(reviewed, user_id="different-user")
    fresh = AsyncMock(return_value=reviewed)
    call = revalidate_live_entry_dispatch(
      db,
      intent=intent,
      volume=100,
      limit_price=Decimal("9.9"),
      now=current,
      fresh_review=None if fault == "no_hook" else fresh,
    )
    if fault:
      with pytest.raises(ValueError, match="LIVE_ENTRY_"):
        await call
    else:
      assert await call is reviewed
      fresh.assert_awaited_once_with(
        execution_id="new-source", intent_id="own", now=NOW
      )
      from types import SimpleNamespace

      from quantx_infrastructure.services import (
        t_live_entry_authorization,
        trade_command_service,
      )

      monkeypatch.setattr(
        trade_command_service, "datetime", SimpleNamespace(now=lambda tz: NOW)
      )
      confirmation = AsyncMock(return_value="user")
      monkeypatch.setattr(
        t_live_entry_authorization, "authorize_live_entry", confirmation
      )
      service = trade_command_service.TradeCommandService(db, live_entry_review=fresh)
      device = SimpleNamespace(user_id="user")
      pick_device = AsyncMock(return_value=device)
      monkeypatch.setattr(service, "_device_for", pick_device)
      args = dict(
        intent=intent,
        account_id="account",
        instrument_code=CODE,
        volume=100,
        limit_price=Decimal("9.9"),
      )
      assert await service._t_entry_device(**args) is device
      confirmation.assert_awaited_once()
      service.live_entry_review = None
      with pytest.raises(
        trade_command_service.AgentUnavailableError,
        match="LIVE_ENTRY_FRESH_REVIEW_REQUIRED",
      ):
        await service._t_entry_device(**args)
      pick_device.assert_awaited_once()
    if fault in {"no_hook", "expired", "future", "request", "allocation"}:
      fresh.assert_not_awaited()
