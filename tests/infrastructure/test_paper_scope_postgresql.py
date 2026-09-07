"""Real PAPER schema, public admission and ledger-chain isolation checks."""

from dataclasses import replace
from datetime import timedelta

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.services.account_risk_increase_admission import (
  AccountRiskIncreaseAdmissionSequencer,
)
from quantx_infrastructure.services.paper_broker_matching import (
  PAPER_MATCHING_POLICY_VERSION,
  PaperBrokerMatching,
)
from quantx_infrastructure.services.paper_receipt_convergence import _stored_amount
from quantx_infrastructure.services.t_allocation_serialization import (
  allocation_evidence,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from tests.infrastructure.test_p4_allocation_postgresql import (
  NOW,
  _claim,
  _prepare,
  _sessions,
)
from tests.infrastructure.test_p4_allocation_postgresql import (
  pytestmark as migration_gate_marker,
)
from tests.infrastructure.test_paper_broker_matching import quote
from tests.infrastructure.test_paper_execution_ledger import (
  test_partial_fill_restart_history_idempotency_and_bucket_conservation as verify_ledger_restart,
)
from tests.infrastructure.test_paper_execution_ledger import (
  test_sink_failure_rolls_back_orders_event_account_and_public_state as verify_sink_rollback,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  test_public_plan_failure_rolls_back_fill_and_all_projections as verify_public_rollback,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  test_public_plan_sell_receipt_closes_batch_without_pending_mismatch as verify_public_close,
)
from tests.infrastructure.test_t_allocation_repository import _seed

pytestmark = migration_gate_marker


@pytest.mark.asyncio
async def test_actual_paper_public_projection_failure_is_atomic():
  async with _sessions(head="20260907_0053") as sessions:
    await verify_public_rollback(sessions)


@pytest.mark.asyncio
async def test_actual_paper_public_exit_plan_closure():
  async with _sessions(head="20260907_0053") as sessions:
    await verify_public_close(sessions)
    async with sessions() as db:
      for amount in ("10.000078125", "0.000000005"):
        assert _stored_amount(amount) == await db.scalar(
          text("SELECT CAST(:amount AS NUMERIC(24,8))"), {"amount": amount}
        )
      for table in (
        "orders",
        "trades",
        "pending_trade_orders",
        "trade_command_outbox",
        "account_execution_controls",
      ):
        assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.asyncio
async def test_actual_paper_receipt_failure_rolls_back_every_fact():
  async def receipt(db, execution_id, result):
    raise AssertionError("setup must not emit receipts")

  async with _sessions(head="20260907_0053") as sessions:
    await verify_sink_rollback(sessions, receipt)
    async with sessions() as db:
      for table in (
        "paper_execution_accounts",
        "paper_execution_events",
        "paper_execution_orders",
        "paper_execution_fills",
      ):
        assert await db.scalar(
          text("SELECT obj_description(to_regclass(:table), 'pg_class')"),
          {"table": table},
        )
      with pytest.raises(DBAPIError, match="PAPER_BUY_AUTHORIZATION_TIME_INVALID"):
        async with db.begin_nested():
          await db.execute(
            text("""
            INSERT INTO paper_execution_orders
              (order_id,execution_id,environment,owner_type,owner_id,intent_id,
               allocation_decision_id,admission_batch_id,instrument_code,
               order_attempt,side,volume,limit_price,filled_volume,status,
               request_payload,response_payload,sizing_evidence,risk_evidence,
               last_event_id,submitted_at,expires_at)
            SELECT 'late-order',i.owner_id,'PAPER',i.owner_type,i.owner_id,i.id,
              i.allocation_decision_id,i.admission_batch_id,i.instrument_code,
              0,'BUY',100,9.9,0,'SUBMITTED','{}','{}','{}','{}','absent-event',
              d.expires_at,d.expires_at + interval '1 minute'
            FROM trade_intents i JOIN t_allocation_decisions d
              ON d.decision_id=i.allocation_decision_id
            WHERE i.status='EXECUTION_READY' LIMIT 1
          """)
          )


@pytest.mark.asyncio
async def test_actual_paper_ledger_partial_fills_restart_and_isolation():
  # Exercise the same production adapter against the migrated constraints,
  # including separate committed transactions for place and both fills.
  # This recording sink verifies ledger delivery only, not public ExitPlan closure.
  async def receipt(db, execution_id, result):
    receipt.seen.append((execution_id, len(result.orders), len(result.trades)))

  receipt.seen = []
  async with _sessions(head="20260907_0053") as sessions:
    await verify_ledger_restart(sessions, receipt)
    async with sessions() as db:
      for table in (
        "orders",
        "trades",
        "pending_trade_orders",
        "trade_command_outbox",
        "account_execution_controls",
      ):
        assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.asyncio
async def test_actual_paper_scope_admission_and_atomic_ledger_chain():
  async with _sessions(head="20260907_0053") as sessions:
    snapshot, candidates = await _seed(sessions, authorization="AUTO")
    execution_id = snapshot.cut.execution_ref.owner_id
    matcher = PaperBrokerMatching(
      scope_execution_id=execution_id,
      cash=10000,
      non_trading_asset_value=0,
      positions={},
      now=NOW,
    )
    checkpoint = matcher.export_checkpoint()
    initial_hash = stable_manifest_hash(checkpoint)
    async with sessions() as db, db.begin():
      execution = await db.get(TAssistantExecutionRecord, execution_id)
      account_id = execution.account_id
      db.add(
        PaperExecutionAccountRecord(
          execution_id=execution_id,
          account_id=account_id,
          environment="PAPER",
          seed_snapshot_id="isolated-seed",
          seed_snapshot_hash=initial_hash,
          seed_as_of=NOW,
          seed_payload=checkpoint,
          matching_policy_version=PAPER_MATCHING_POLICY_VERSION,
          broker_checkpoint=checkpoint,
          bucket_checkpoint={},
          revision=0,
          snapshot_hash=initial_hash,
          initial_snapshot_hash=initial_hash,
          snapshot_as_of=NOW,
        )
      )
    allocation = await _prepare(sessions, snapshot, candidates)
    claim = await _claim(sessions, allocation, snapshot, candidates, owner="allocator")
    async with sessions() as db, db.begin():
      await TAllocationRepository(db).commit(
        claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
      )
    binding = dict(
      environment=ExecutionEnvironment.PAPER, paper_execution_id=execution_id
    )
    snapshot_id = f"paper:{execution_id}:0"
    watermark = "b" * 64
    async with sessions() as db, db.begin():
      sequencer = AccountRiskIncreaseAdmissionSequencer(db, **binding)
      batch = await sequencer.prepare_batch(
        account_id=account_id,
        account_snapshot_id=snapshot_id,
        account_snapshot_hash=initial_hash,
        obligation_watermark=watermark,
        intent_ids=[candidates[0].intent_id],
        now=NOW,
        commit=False,
      )
      batch_id = batch.admission_batch_id
    async with sessions() as db, db.begin():
      sequencer = AccountRiskIncreaseAdmissionSequencer(db, **binding)
      claim = await sequencer.claim_batch(
        admission_batch_id=batch_id,
        processing_owner="paper-dispatch",
        now=NOW,
        commit=False,
      )
    async with sessions() as db, db.begin():
      with pytest.raises(ValueError, match="NOT_FOUND|SCOPE|MISSING"):
        await AccountRiskIncreaseAdmissionSequencer(
          db, environment=ExecutionEnvironment.PAPER, paper_execution_id="wrong-scope"
        ).claim_batch(
          admission_batch_id=batch_id, processing_owner="wrong", now=NOW, commit=False
        )
    async with sessions() as db, db.begin():
      batch = await AccountRiskIncreaseAdmissionSequencer(db, **binding).commit_batch(
        admission_batch_id=batch_id,
        fence_token=claim.fence_token,
        account_snapshot_id=snapshot_id,
        account_snapshot_hash=initial_hash,
        obligation_watermark=watermark,
        now=NOW,
        commit=False,
      )
      assert batch.status == "COMMITTED"
    async with sessions() as db, db.begin():
      with pytest.raises(DBAPIError, match="PAPER_LEDGER_HALF_COMMIT"):
        async with db.begin_nested():
          await db.execute(
            text("""
            UPDATE paper_execution_accounts SET revision=1,snapshot_hash=:hash
              WHERE execution_id=:id
          """),
            {"hash": "c" * 64, "id": execution_id},
          )
          await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    later = NOW + timedelta(seconds=1)
    market = replace(quote(1), timestamp=later)
    await matcher.process_quote(event_id="paper-quote-1", quote=market)
    next_checkpoint = matcher.export_checkpoint()
    next_hash = stable_manifest_hash(next_checkpoint)
    async with sessions() as db, db.begin():
      account = await db.get(
        PaperExecutionAccountRecord, execution_id, with_for_update=True
      )
      db.add(
        PaperExecutionEventRecord(
          event_id="paper-event-1",
          execution_id=execution_id,
          environment="PAPER",
          event_key="quote-1",
          event_type="QUOTE",
          revision=1,
          input_hash=stable_manifest_hash(allocation_evidence(market)),
          input_payload=allocation_evidence(market),
          result_payload={"order_ids": [], "fill_ids": []},
          resulting_snapshot_hash=next_hash,
          previous_snapshot_hash=initial_hash,
          occurred_at=later,
        )
      )
      account.broker_checkpoint = next_checkpoint
      account.revision, account.snapshot_hash, account.snapshot_as_of = (
        1,
        next_hash,
        later,
      )
    async with sessions() as db, db.begin():
      with pytest.raises(
        DBAPIError, match="PAPER_RECEIPT_FACTS_MISSING_OR_CONFLICTING"
      ):
        async with db.begin_nested():
          account = await db.get(PaperExecutionAccountRecord, execution_id)
          db.add(
            PaperExecutionEventRecord(
              event_id="missing-fill-event",
              execution_id=execution_id,
              environment="PAPER",
              event_key="missing-fill",
              event_type="QUOTE",
              revision=2,
              input_hash="c" * 64,
              input_payload={},
              result_payload={"order_ids": [], "fill_ids": ["missing"]},
              previous_snapshot_hash=next_hash,
              resulting_snapshot_hash="d" * 64,
              occurred_at=later + timedelta(seconds=1),
            )
          )
          account.revision, account.snapshot_hash, account.snapshot_as_of = (
            2,
            "d" * 64,
            later + timedelta(seconds=1),
          )
          await db.flush()
          await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
      with pytest.raises(DBAPIError, match="PAPER_BUY_CURRENT_AUTHORIZATION_REQUIRED"):
        async with db.begin_nested():
          intent = await db.get(TradeIntentRecord, candidates[0].intent_id)
          intent.status = "REJECTED"
          await db.flush()
          db.add(
            PaperExecutionOrderRecord(
              order_id="stale-authorization",
              execution_id=execution_id,
              environment="PAPER",
              owner_type="T_ASSISTANT_EXECUTION",
              owner_id=execution_id,
              intent_id=intent.id,
              allocation_decision_id=intent.allocation_decision_id,
              admission_batch_id=batch_id,
              instrument_code=intent.instrument_code,
              order_attempt=0,
              side="BUY",
              volume=100,
              limit_price=9,
              filled_volume=0,
              status="SUBMITTED",
              request_payload={},
              response_payload={},
              sizing_evidence={},
              risk_evidence={},
              last_event_id="paper-event-1",
              submitted_at=NOW,
              expires_at=NOW + timedelta(seconds=15),
            )
          )
          await db.flush()
      for statement, reason in (
        (
          "UPDATE paper_execution_accounts SET environment='LIVE'",
          "PAPER_ACCOUNT_SEED_IMMUTABLE",
        ),
        (
          "UPDATE paper_execution_events SET input_hash=repeat('0',64)",
          "PAPER_FACT_IMMUTABLE",
        ),
        ("DELETE FROM paper_execution_events", "PAPER_FACT_IMMUTABLE"),
      ):
        with pytest.raises(DBAPIError, match=reason):
          async with db.begin_nested():
            await db.execute(text(statement))
      assert (
        await db.scalar(select(func.count()).select_from(PaperExecutionAccountRecord))
        == 1
      )
      assert (
        await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord))
        == 1
      )
      for table in (
        "orders",
        "trades",
        "pending_trade_orders",
        "trade_command_outbox",
        "account_execution_controls",
      ):
        assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0
