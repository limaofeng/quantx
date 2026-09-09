"""Actual LIVE allocation persistence; isolated account valuation and no broker."""

from unittest.mock import AsyncMock

import pytest
from quantx_engine.t_assistant_live_allocation_runtime import (
  TAssistantLiveAllocationRuntime,
)
from quantx_infrastructure.models.t_allocation import TAllocationBatchRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.live_portfolio_snapshot import (
  LivePortfolioSnapshotReader,
)
from sqlalchemy import func, select

from tests.infrastructure.test_live_allocation_coordinator import (
  NOW,
  allocation_sessions,
  base_sessions,
  committed_evaluation,
  frozen_config,
  ledger_sessions,
  seed_live,
  sessions,
)

_FIXTURES = allocation_sessions, base_sessions, committed_evaluation, frozen_config, ledger_sessions, sessions


@pytest.mark.parametrize("market_lost", [False, True])
async def test_dispatch_recovers_pending_cycles_and_rolls_back_on_market_loss(
  sessions, frozen_config, committed_evaluation, monkeypatch, market_lost
):
  snapshot, _ = await seed_live(sessions)
  monkeypatch.setattr(LivePortfolioSnapshotReader, "read", AsyncMock(return_value=snapshot))
  runtime = TAssistantLiveAllocationRuntime(session_factory=sessions, clock=lambda: NOW)
  checks = 0
  def validate():
    nonlocal checks
    checks += 1
    if market_lost and checks == 2:
      raise ValueError("LIVE_ALLOCATION_MARKET_CHANGED")
  kwargs = dict(execution_id=snapshot.cut.execution_ref.owner_id,
    market_mark_reader=object(), validate_market=validate)
  if market_lost:
    with pytest.raises(ValueError, match="MARKET_CHANGED"):
      await runtime.dispatch(**kwargs)
  else:
    result = await runtime.dispatch(**kwargs)
    assert result.status == "PROCESSED" and len(result.allocation_ids) == 1
    assert (await runtime.dispatch(**kwargs)).status == "IDLE"
  async with sessions() as db:
    intent = await db.get(TradeIntentRecord, "intent-0")
    assert intent.status == ("ALLOCATION_PENDING" if market_lost else "AWAITING_APPROVAL")
    assert await db.scalar(select(func.count()).select_from(TAllocationBatchRecord)) == (0 if market_lost else 1)
