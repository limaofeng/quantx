"""Independent T zero-fill proof must preserve confirmed entry material."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest
from quantx_engine import report_processor
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import select

from tests.engine.test_entry_plan_broker_zero_fill_reconciliation import (
  _database,
  _seed_managed_order,
  _snapshot_report,
  _terminal_report,
)


@pytest.mark.parametrize(
  "fault",
  [
    None,
    "batch_owner",
    "batch_intent",
    "batch_fill",
    "intent_batch",
    "order_side",
    "order_volume",
    "incomplete",
    "trade",
  ],
)
@pytest.mark.asyncio
async def test_independent_entry_snapshot_proof(monkeypatch, fault):
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(terminal_status="CANCELLED", snapshot_id="t-zero")
  await _seed_managed_order(
    sessions, terminal_status="CANCELLED", snapshot=snapshot, independent=True
  )
  try:
    async with sessions() as db, db.begin():
      intent = await db.get(TradeIntentRecord, "intent-1")
      batch = await db.get(TTradeBatch, "batch-1")
      if fault == "batch_owner":
        # Deliberately corrupt the fixture using SQL: immutable owner fields
        # must never be changed through the production ORM path.
        await db.execute(
          TTradeBatch.__table__.update().values(source_execution_owner_id="other")
        )
      elif fault == "batch_intent":
        batch.entry_intent_id = "other"
      elif fault == "batch_fill":
        batch.entry_filled_volume = 1
      elif fault == "intent_batch":
        intent.intent_metadata = {**intent.intent_metadata, "t_batch_id": "other"}
      original_metadata = deepcopy(intent.intent_metadata)
    await report_processor._stage_runtime_events(_terminal_report("CANCELLED"))
    async with sessions() as db:
      assert (await db.get(PendingTradeOrder, "client-1")).status == "CANCELLED"
      assert (await db.get(TradeIntentRecord, "intent-1")).status == "EXECUTION_PENDING"
    if fault == "order_side":
      snapshot.payload["orders"][0]["order_type"] = 24
    elif fault == "order_volume":
      snapshot.payload["orders"][0]["order_volume"] = 200
    elif fault == "incomplete":
      snapshot.payload["section_completeness_by_account"]["account-1"]["trades"] = False
    elif fault == "trade":
      snapshot.payload["trades"] = [
        {
          "account_id": "account-1",
          "stock_code": "605499.SH",
          "order_id": 9001,
          "traded_volume": 1,
        }
      ]
    if fault in {"order_side", "order_volume", "incomplete", "trade"}:
      # Valid checkpoint/hash even for contradictory data; rejection must
      # arise from the scope/no-fill checks, not a stale hash.
      snapshot.payload["snapshot_hash"] = sha256(
        json.dumps(
          {k: v for k, v in snapshot.payload.items() if k != "snapshot_hash"},
          sort_keys=True,
          separators=(",", ":"),
          default=str,
        ).encode()
      ).hexdigest()
      async with sessions() as db, db.begin():
        (
          await db.get(AccountExecutionControl, "account-1")
        ).last_snapshot_hash = snapshot.payload["snapshot_hash"]
    await report_processor._stage_runtime_events(snapshot)
    await report_processor._stage_runtime_events(snapshot)
    async with sessions() as db:
      events = list(await db.scalars(select(StrategyRuntimeEvent)))
      proofs = [
        e
        for e in events
        if e.payload["report"].get("effective_order_status") == "RECONCILED_ZERO_FILL"
      ]
      assert len(proofs) == (0 if fault else 1)
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.intent_metadata == original_metadata
      assert intent.status == "EXECUTION_PENDING"
      assert (await db.get(PendingTradeOrder, "client-1")).status == (
        "CANCELLED" if fault else "RECONCILED_ZERO_FILL"
      )
      if not fault:
        assert (
          proofs[0].payload["metadata"]["qmt_zero_fill_reconciliation"]["snapshot_id"]
          == "t-zero"
        )
  finally:
    await engine.dispose()


@pytest.mark.parametrize("by_broker", [False, True])
@pytest.mark.parametrize("filled", [0, 1, None])
@pytest.mark.asyncio
async def test_zero_fill_replay_requires_exact_terminal_counter(
  monkeypatch, by_broker, filled
):
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(terminal_status="CANCELLED", snapshot_id="zero-replay")
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
    independent=True,
  )
  try:
    await report_processor._stage_runtime_events(snapshot)
    kwargs = dict(
      status="CANCELLED",
      reason="replayed broker evidence",
      source_sequence=11,
      cumulative_filled_volume=filled,
    )
    result = (
      await report_processor._update_pending_by_broker("9001", **kwargs)
      if by_broker
      else await report_processor._update_pending(
        "client-1", broker_order_id="9001", **kwargs
      )
    )
    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, "client-1")
      assert pending.last_source_sequence == 11
      assert pending.status == (
        "RECONCILED_ZERO_FILL"
        if filled == 0
        else "CANCELLED"
      )
      assert result.accepted is (filled != 0)
  finally:
    await engine.dispose()
