"""PAPER source timestamps remain visible through delayed, durable receipts."""

import os
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionFillRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.services.paper_execution_ledger import (
  PaperExecutionLedger,
  _stored_time,
)
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import func, select

from tests.infrastructure import test_paper_receipt_convergence as receipt_tests

allocation_sessions = receipt_tests.allocation_sessions
base_sessions = receipt_tests.base_sessions
ledger_sessions = receipt_tests.ledger_sessions
sessions = receipt_tests.sessions
setup, quote, enrich_intent = (
  receipt_tests.setup,
  receipt_tests.quote,
  receipt_tests.enrich_intent,
)


async def test_same_source_instant_with_different_offset_is_one_durable_event(sessions):
  sink = PaperReceiptConvergence()
  scope, _ = await setup(sessions, sink, enrich_intent=enrich_intent)
  original = quote(1)
  local = replace(
    original, timestamp=original.timestamp.astimezone(ZoneInfo("Asia/Shanghai"))
  )
  async with sessions() as db, db.begin():
    first = await PaperExecutionLedger(db, receipt_sink=sink).process_quote(
      execution_id=scope,
      event_key="same-source",
      quote=local,
      accepted_at=quote(3).timestamp,
    )
  async with sessions() as db, db.begin():
    repeated = await PaperExecutionLedger(db, receipt_sink=sink).process_quote(
      execution_id=scope,
      event_key="same-source",
      quote=original,
      accepted_at=quote(3).timestamp.astimezone(ZoneInfo("Asia/Shanghai")),
    )
    assert repeated.duplicate and repeated.event_id == first.event_id
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord)) == 1
    )
    stored = await db.get(PaperExecutionEventRecord, first.event_id)
    assert _stored_time(stored.quote_source_at) == original.timestamp
    assert stored.input_payload["quote"]["timestamp"] == original.timestamp.isoformat()
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 1
  assert local.timestamp.tzinfo == ZoneInfo("Asia/Shanghai")


async def test_actual_delayed_source_is_not_liquidity_for_a_newer_order_and_restarts(
  sessions, *, after_late=None
):
  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.process_quote(
      execution_id=scope,
      event_key="other-symbol",
      quote=replace(quote(2), instrument_code="000001.SZ"),
      accepted_at=quote(2).timestamp,
    )
    snapshot = await ledger.get_snapshot(execution_id=scope)
    args.update(
      now=quote(2).timestamp,
      expected_revision=snapshot["revision"],
      expected_snapshot_hash=snapshot["snapshot_hash"],
    )
    await ledger.place_order(execution_id=scope, **args)
    await ledger.process_quote(
      execution_id=scope,
      event_key="late-source",
      quote=quote(1),
      accepted_at=quote(3).timestamp,
    )
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionFillRecord)) == 0
    )
    source_event = await db.scalar(
      select(PaperExecutionEventRecord).where(
        PaperExecutionEventRecord.event_key == "late-source"
      )
    )
    assert _stored_time(source_event.quote_source_at) == quote(1).timestamp
    assert _stored_time(source_event.occurred_at) == quote(3).timestamp
    if after_late is not None:
      await after_late(db, scope, args)
    same = await ledger.process_quote(
      execution_id=scope,
      event_key="same-source-new-book",
      quote=replace(quote(1), bid_vol=[800] * 5),
      accepted_at=quote(4).timestamp,
    )
    assert same.result_payload["fill_ids"] == []
    assert same.result_payload["reason_codes"] == [
      "PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY"
    ]
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    before = await ledger.get_snapshot(execution_id=scope)
    assert (
      await ledger.process_quote(
        execution_id=scope,
        event_key="late-source",
        quote=quote(1),
        accepted_at=quote(3).timestamp,
      )
    ).duplicate
    assert await ledger.get_snapshot(execution_id=scope) == before
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
      await ledger.process_quote(
        execution_id=scope,
        event_key="late-source",
        quote=quote(1),
        accepted_at=quote(4).timestamp,
      )
    await ledger.process_quote(
      execution_id=scope,
      event_key="fresh-source",
      quote=quote(3, depth=400),
      accepted_at=quote(5).timestamp,
    )
    fills = list((await db.scalars(select(PaperExecutionFillRecord))).all())
    assert len(fills) == 1 and fills[0].volume == 100
    assert _stored_time(fills[0].occurred_at) == quote(5).timestamp
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).protected_volume == 100
    assert (
      _stored_time((await db.get(PaperExecutionAccountRecord, scope)).snapshot_as_of)
      == quote(5).timestamp
    )


async def test_actual_acceptance_ttl_precedes_matching_even_when_source_is_earlier(
  sessions,
):
  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    await ledger.process_quote(
      execution_id=scope,
      event_key="arrived-at-expiry",
      quote=quote(59),
      accepted_at=quote(60).timestamp,
    )
    assert (
      await db.get(PaperExecutionOrderRecord, args["order_id"])
    ).status == "EXPIRED"
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionFillRecord)) == 0
    )


@pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true", reason="isolated PostgreSQL gate"
)
async def test_postgresql_dual_clock_guards_and_real_receipt_rollback():
  from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
  from quantx_infrastructure.services.paper_broker_matching import _json
  from sqlalchemy import text
  from sqlalchemy.exc import DBAPIError

  from tests.infrastructure.test_p4_allocation_postgresql import _sessions

  async def negative_clocks(db, scope, args):
    snapshot = await PaperExecutionLedger(
      db, receipt_sink=PaperReceiptConvergence()
    ).get_snapshot(execution_id=scope)

    def forged_event(kind):
      source = quote(
        1 if kind == "same-source-fill" else 2 if kind != "future" else 5
      ).timestamp
      payload = {
        "quote": _json(replace(quote(2), timestamp=source)),
        "accepted_at": quote(4).timestamp.isoformat(),
      }
      return PaperExecutionEventRecord(
        event_id="forged-" + kind,
        execution_id=scope,
        environment="PAPER",
        event_key="forged-" + kind,
        event_type="QUOTE",
        revision=snapshot["revision"] + 1,
        input_hash=stable_manifest_hash({"event_type": "QUOTE", "input": payload}),
        input_payload=payload,
        result_payload={"order_ids": [], "fill_ids": ["forged-fill"]},
        previous_snapshot_hash=snapshot["snapshot_hash"],
        resulting_snapshot_hash="f" * 64,
        occurred_at=quote(4).timestamp,
        quote_source_at=quote(3).timestamp if kind == "mismatch" else source,
      )

    for kind, reason in (
      ("future", "ck_paper_event_quote_clock"),
      ("mismatch", "PAPER_QUOTE_EVENT_CLOCK_CONFLICT"),
      ("same-source-fill", "PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY"),
    ):
      with pytest.raises(DBAPIError, match=reason):
        async with db.begin_nested():
          db.add(forged_event(kind))
          await db.flush()
    # All bindings agree except source == submit: acceptance occurs later but
    # must not make that pre-existing quote usable by the newly submitted order.
    with pytest.raises(DBAPIError, match="PAPER_FILL_SCOPE_OR_CAUSALITY_CONFLICT"):
      async with db.begin_nested():
        event = forged_event("invisible")
        db.add(event)
        await db.flush()
        await db.execute(text("SET CONSTRAINTS trg_paper_fill_binding IMMEDIATE"))
        db.add(
          PaperExecutionFillRecord(
            fill_id="forged-fill",
            execution_id=scope,
            environment="PAPER",
            event_id=event.event_id,
            order_id=args["order_id"],
            volume=100,
            price=9.9,
            fee=5,
            occurred_at=quote(4).timestamp,
            trade_payload={},
          )
        )
        await db.flush()
    await db.execute(text("SET CONSTRAINTS ALL DEFERRED"))

  async with _sessions(head="20260907_0055") as pg:
    await test_actual_delayed_source_is_not_liquidity_for_a_newer_order_and_restarts(
      pg, after_late=negative_clocks
    )
  async with _sessions(head="20260907_0055") as pg:
    await test_actual_delayed_receipt_failure_rolls_back_both_clocks(pg)


@pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true", reason="isolated PostgreSQL gate"
)
async def test_postgresql_clock_upgrade_refuses_existing_accounts_without_rewriting_them():
  from pathlib import Path

  from alembic.migration import MigrationContext
  from alembic.operations import Operations
  from alembic.script import ScriptDirectory
  from sqlalchemy import text
  from sqlalchemy.exc import DBAPIError

  from tests.infrastructure.test_p4_allocation_postgresql import _sessions
  from tests.infrastructure.test_paper_execution_ledger import seed_values
  from tests.infrastructure.test_t_allocation_repository import _seed

  async with _sessions(head="20260907_0054") as pg:
    source, _ = await _seed(pg, authorization="AUTO", enrich_intent=enrich_intent)
    scope = source.cut.execution_ref.owner_id
    async with pg() as db, db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
      await ledger.initialize(
        execution_id=scope, account_id="account-1", **seed_values()
      )
      before = await ledger.get_snapshot(execution_id=scope)

    def upgrade(connection):
      revision = ScriptDirectory(
        str(Path(__file__).parents[2] / "packages/infrastructure/alembic")
      ).get_revision("20260907_0055")
      with Operations.context(MigrationContext.configure(connection)):
        revision.module.upgrade()

    with pytest.raises(DBAPIError, match="PAPER_V2_REQUIRES_EMPTY_ACCOUNT_STORE"):
      async with pg.kw["bind"].begin() as connection:
        await connection.run_sync(upgrade)
    async with pg() as db:
      assert (
        await PaperExecutionLedger(
          db, receipt_sink=PaperReceiptConvergence()
        ).get_snapshot(execution_id=scope)
        == before
      )
      assert (
        await db.scalar(
          text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='paper_execution_events' AND column_name='quote_source_at'"
          )
        )
        == 0
      )


@pytest.mark.parametrize("expire", [False, True])
async def test_actual_same_source_book_updates_have_one_liquidity_budget_and_audit(
  sessions, expire
):
  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    await ledger.process_quote(
      execution_id=scope,
      event_key="first",
      quote=quote(1),
      accepted_at=quote(1).timestamp,
    )
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).protected_volume == 50
  changed = replace(
    quote(1, depth=800), price=9.89, ask_price=[9.89, 9.90, 9.91, 9.92, 9.93]
  )
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    result = await ledger.process_quote(
      execution_id=scope,
      event_key="same-source",
      quote=changed,
      accepted_at=quote(2).timestamp,
    )
    assert result.result_payload["reason_codes"] == [
      "PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY"
    ]
    assert result.result_payload["fill_ids"] == []
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).protected_volume == 50
    assert (
      await db.get(PaperExecutionOrderRecord, args["order_id"])
    ).filled_volume == 50
    account = await db.get(PaperExecutionAccountRecord, scope)
    assert (
      account.broker_checkpoint["material"]["market_snapshots"]["600000.SH"][
        "ask_price"
      ]
      == changed.ask_price
    )
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    retry = await ledger.process_quote(
      execution_id=scope,
      event_key="same-source",
      quote=changed,
      accepted_at=quote(2).timestamp,
    )
    assert retry.duplicate and retry.result_payload == result.result_payload
    if expire:
      expired = await ledger.process_quote(
        execution_id=scope,
        event_key="same-at-ttl",
        quote=changed,
        accepted_at=quote(60).timestamp,
      )
      assert expired.result_payload["reason_codes"] == [
        "PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY"
      ]
      assert (
        await db.get(PaperExecutionOrderRecord, args["order_id"])
      ).status == "EXPIRED"
    else:
      await ledger.process_quote(
        execution_id=scope,
        event_key="fresh",
        quote=quote(2),
        accepted_at=quote(3).timestamp,
      )
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).protected_volume == (
      50 if expire else 100
    )
    assert await db.scalar(
      select(func.count()).select_from(PaperExecutionFillRecord)
    ) == (1 if expire else 2)


async def test_actual_delayed_receipt_failure_rolls_back_both_clocks(sessions):
  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)

  async def failure(db, execution_id, result):
    await sink(db, execution_id, result)
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).protected_volume == 100
    raise RuntimeError("after real receipt")

  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=failure)
    before = await ledger.get_snapshot(execution_id=scope)
    with pytest.raises(RuntimeError, match="after real receipt"):
      await ledger.process_quote(
        execution_id=scope,
        event_key="delayed-fill",
        quote=quote(1, depth=400),
        accepted_at=quote(5).timestamp,
      )
    assert await ledger.get_snapshot(execution_id=scope) == before
    assert await db.get(AutoExitPlanRecord, "paper-plan") is None
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionFillRecord)) == 0
    )
