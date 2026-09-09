"""LIVE candidate evidence through the shared allocator; valuation cut is isolated."""

from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationConflict,
)
from quantx_infrastructure.services.live_allocation_coordinator import (
  LiveAllocationCoordinator,
)
from quantx_infrastructure.services.live_portfolio_snapshot import (
  LivePortfolioSnapshotReader,
)

from tests.infrastructure.test_paper_allocation_coordinator import (
  allocation_sessions,
  base_sessions,
  committed_evaluation,
  frozen_config,
  ledger_sessions,
  sessions,
)
from tests.infrastructure.test_paper_portfolio_snapshot import enrich_intent
from tests.infrastructure.test_t_allocation_repository import NOW, _seed

_FIXTURES = committed_evaluation, frozen_config, sessions, allocation_sessions, base_sessions, ledger_sessions


async def seed_live(sessions):
  def enrich(intent):
    enrich_intent(intent)
    intent.metadata["tick_ordinal"] = 1
    intent.max_price_deviation_bps = 20
  return await _seed(sessions, environment=ExecutionEnvironment.LIVE, enrich_intent=enrich)


async def test_live_allocation_keeps_manual_approval_and_original_ttl(sessions, frozen_config, committed_evaluation, monkeypatch):
  snapshot, _ = await seed_live(sessions)
  reader = AsyncMock(return_value=snapshot)
  monkeypatch.setattr(LivePortfolioSnapshotReader, "read", reader)
  scope = snapshot.cut.execution_ref.owner_id
  mark_reader = object()
  async with sessions() as db, db.begin():
    original = await db.get(TradeIntentRecord, "intent-0")
    ttl = original.intent_metadata["approval_ttl_ms"]
    created_at = original.created_at
    batch = await LiveAllocationCoordinator(db, market_mark_reader=mark_reader).allocate_cycle(
      execution_id=scope, cycle_id="intake-cycle", processing_owner="live-entry", now=NOW
    )
    assert batch.environment == "LIVE" and batch.status == "COMMITTED"
    assert original.status == "AWAITING_APPROVAL"
    assert original.intent_metadata["approval_ttl_ms"] == ttl
    assert original.created_at == created_at
  assert reader.await_args.kwargs["account_max_age_seconds"] == 90
  assert reader.await_args.kwargs["market_mark_reader"] is mark_reader


async def test_degraded_source_cannot_allocate_or_read_portfolio(sessions, frozen_config, committed_evaluation, monkeypatch):
  snapshot, _ = await seed_live(sessions)
  reader = AsyncMock(return_value=snapshot)
  monkeypatch.setattr(LivePortfolioSnapshotReader, "read", reader)
  scope = snapshot.cut.execution_ref.owner_id
  async with sessions() as db, db.begin():
    row = await db.get(TAssistantExecutionRecord, scope)
    row.entry_readiness = "DEGRADED"
    row.entry_readiness_reasons = ["LIVE_READY_RECOVERY_REQUIRED"]
  async with sessions() as db, db.begin():
    with pytest.raises(TAllocationConflict, match="NOT_ENTRY_READY"):
      await LiveAllocationCoordinator(db, market_mark_reader=object()).allocate_cycle(
        execution_id=scope, cycle_id="intake-cycle", processing_owner="live-entry", now=NOW
      )
  reader.assert_not_awaited()
