"""Targeted dispatcher recovery audit reproductions over real SQLite facts."""

from datetime import timedelta

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_engine.t_assistant_paper_entry_runtime import TAssistantPaperEntryRuntime
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_risk_increase_admission import (
  AccountRiskIncreaseAdmissionSequencer,
)
from quantx_infrastructure.services.paper_allocation_coordinator import (
  PaperAllocationCoordinator,
)
from quantx_infrastructure.services.paper_portfolio_snapshot import (
  PaperPortfolioSnapshotReader,
)
from sqlalchemy import select

from tests.engine.unit import test_t_assistant_paper_entry_runtime as dispatcher_tests

allocation_sessions = dispatcher_tests.allocation_sessions
base_sessions = dispatcher_tests.base_sessions
ledger_sessions = dispatcher_tests.ledger_sessions
sessions = dispatcher_tests.sessions
frozen_config = dispatcher_tests.frozen_config


@pytest.mark.parametrize("initialize", [False, True])
async def test_expired_unallocated_candidates_terminalize_without_fresh_quotes(
  sessions, frozen_config, initialize
):
  source, _ = await dispatcher_tests.seeded(sessions, initialize=initialize)

  async def no_market(_code):
    pytest.fail("expired candidates must not require a new market witness")

  result = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now + timedelta(minutes=5)
  ).dispatch(execution_id=source.execution_id, market_witness_provider=no_market)
  assert len(result.reviews) == 2
  if not initialize:
    assert result.status == "SEED_REQUIRED"
  async with sessions() as db:
    assert all(
      row.status == "EXPIRED"
      for row in (await db.scalars(select(TradeIntentRecord))).all()
    )


async def test_prepared_admission_recovery_does_not_supersede_its_own_bookkeeping(
  sessions, frozen_config
):
  source, witnesses = await dispatcher_tests.seeded(sessions)
  async with sessions() as db, db.begin():
    await PaperAllocationCoordinator(db).allocate_cycle(
      execution_id=source.execution_id,
      cycle_id=source.cycle_id,
      processing_owner="allocation",
      now=source.now,
    )
    snapshot = await PaperPortfolioSnapshotReader(db).read(
      execution_id=source.execution_id,
      cycle_id=source.cycle_id,
      instrument_codes=dispatcher_tests.CODES,
      as_of=source.now,
    )
    sequencer = AccountRiskIncreaseAdmissionSequencer(
      db, environment=ExecutionEnvironment.PAPER, paper_execution_id=source.execution_id
    )
    admission = await sequencer.prepare_batch(
      account_id="account-1",
      account_snapshot_id=snapshot.cut.account_snapshot_id,
      account_snapshot_hash=snapshot.cut.account_snapshot_hash,
      obligation_watermark=snapshot.cut.local_obligation_watermark,
      now=source.now,
      commit=False,
    )
    batch_id = admission.admission_batch_id
    await sequencer.claim_batch(
      admission_batch_id=batch_id,
      processing_owner="original-worker",
      now=source.now,
      commit=False,
    )

  async def provider(code):
    return witnesses[code]

  with pytest.raises(ValueError, match="RISK_ADMISSION_LEASE_HELD"):
    await TAssistantPaperEntryRuntime(
      session_factory=sessions, clock=lambda: source.now + timedelta(milliseconds=1)
    ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  async with sessions() as db:
    assert (
      await db.get(AccountRiskIncreaseAdmissionBatch, batch_id)
    ).status == "PREPARED"


@pytest.mark.parametrize("change", [None, "target_amount", "status", "batch_expired"])
async def test_prepared_admission_restarts_complete_batch_with_economic_watermark(
  sessions, frozen_config, change, monkeypatch
):
  source, witnesses = await dispatcher_tests.seeded(sessions)
  async with sessions() as db, db.begin():
    await PaperAllocationCoordinator(db).allocate_cycle(
      execution_id=source.execution_id,
      cycle_id=source.cycle_id,
      processing_owner="allocation",
      now=source.now,
    )
    reader = PaperPortfolioSnapshotReader(db)
    kwargs = dict(
      execution_id=source.execution_id,
      cycle_id=source.cycle_id,
      instrument_codes=dispatcher_tests.CODES,
      as_of=source.now,
    )
    before = await reader.read(**kwargs)
    rows = list((await db.scalars(select(TradeIntentRecord))).all())
    previous = {
      row.id: {
        key: getattr(row, key)
        for key in (
          "admission_batch_id",
          "admission_rank",
          "admission_policy_version",
          "admission_input_fingerprint",
        )
      }
      for row in rows
    }
    sequencer = AccountRiskIncreaseAdmissionSequencer(
      db, environment=ExecutionEnvironment.PAPER, paper_execution_id=source.execution_id
    )
    if change == "batch_expired":
      monkeypatch.setattr(
        "quantx_infrastructure.services.account_risk_increase_admission.ADMISSION_TTL_SECONDS",
        0.0005,
      )
    admission = await sequencer.prepare_batch(
      account_id="account-1",
      account_snapshot_id=before.cut.account_snapshot_id,
      account_snapshot_hash=before.cut.account_snapshot_hash,
      obligation_watermark=before.cut.local_obligation_watermark,
      now=source.now,
      commit=False,
    )
    batch_id = admission.admission_batch_id
    after = await reader.read(**kwargs)
    # Exactly these four fields are execution credentials, not cash/stock claims.
    for row in rows:
      assert all(getattr(row, key) != value for key, value in previous[row.id].items())
    assert before == after
    if change in {"status", "target_amount"}:
      if change == "status":
        rows[0].status = "CANCELLED"
      else:
        rows[0].target_amount += 1
      await db.flush()
      changed = await reader.read(**kwargs)
      assert (
        changed.cut.local_obligation_watermark != before.cut.local_obligation_watermark
      )

  async def provider(code):
    return witnesses[code]

  runtime = TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now + timedelta(milliseconds=1)
  )
  if change == "status":
    result = await runtime.dispatch(
      execution_id=source.execution_id, market_witness_provider=provider
    )
    assert result.reason_codes == ("PAPER_ENTRY_ADMISSION_COMPLETE_BATCH_REQUIRED",)
    again = await runtime.dispatch(
      execution_id=source.execution_id, market_witness_provider=provider
    )
    assert again.reason_codes == result.reason_codes
  elif change == "target_amount":
    with pytest.raises(
      ValueError,
      match="PAPER_ENTRY_ADMISSION_COMPLETE_BATCH_REQUIRED|RISK_ADMISSION_INPUT_CHANGED",
    ):
      await runtime.dispatch(
        execution_id=source.execution_id, market_witness_provider=provider
      )
  else:
    result = await runtime.dispatch(
      execution_id=source.execution_id, market_witness_provider=provider
    )
    assert len(result.order_ids) == 2
  async with sessions() as db:
    batches = list((await db.scalars(select(AccountRiskIncreaseAdmissionBatch))).all())
    if change == "batch_expired":
      assert len(batches) == 2
      assert (
        await db.get(AccountRiskIncreaseAdmissionBatch, batch_id)
      ).status == "EXPIRED"
      assert sum(batch.status == "COMMITTED" for batch in batches) == 1
    else:
      assert len(batches) == 1
      assert batches[0].admission_batch_id == batch_id
      assert batches[0].status == ("PREPARED" if change else "COMMITTED")
    if change == "status":
      events = list(
        (
          await db.scalars(
            select(TAssistantExecutionEventRecord).where(
              TAssistantExecutionEventRecord.event_type
              == "PAPER_ENTRY_DISPATCH_BLOCKED"
            )
          )
        ).all()
      )
      assert len(events) == 1
      assert events[0].payload["reason_codes"] == [
        "PAPER_ENTRY_ADMISSION_COMPLETE_BATCH_REQUIRED"
      ]


async def test_ttl_maintenance_survives_later_stale_frame_without_partial_allocation(
  sessions, frozen_config, monkeypatch
):
  from quantx_infrastructure.models.paper_execution import PaperExecutionOrderRecord
  from quantx_infrastructure.models.t_allocation import TAllocationBatchRecord
  from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
    TAssistantDecisionCycleRepository,
  )

  from tests.infrastructure import test_paper_portfolio_snapshot as reader_tests

  original_reference = reader_tests.reference_payload

  def short_freshness():
    payload = original_reference()
    payload["portfolio_policy"]["mark_max_age_seconds"] = 2
    return payload

  monkeypatch.setattr(reader_tests, "reference_payload", short_freshness)
  original_commit = TAssistantDecisionCycleRepository.commit_material_cycle

  async def commit_with_short_original_ttl(self, **kwargs):
    # Vary only the original producer intent TTL before its immutable intake.
    # Candidate market/evaluation still comes from the real StrategyBase.step.
    intents = kwargs.get("trade_intents", ())
    if intents:
      intents[0].approval_ttl_ms = 1
    return await original_commit(self, **kwargs)

  monkeypatch.setattr(
    TAssistantDecisionCycleRepository,
    "commit_material_cycle",
    commit_with_short_original_ttl,
  )
  source, _ = await dispatcher_tests.seeded(sessions)

  async def no_market(_):
    pytest.fail("stale authoritative portfolio must fail before final witness")

  with pytest.raises(ValueError, match="T_VALUATION_MARK_STALE"):
    await TAssistantPaperEntryRuntime(
      session_factory=sessions, clock=lambda: source.now + timedelta(seconds=5)
    ).dispatch(execution_id=source.execution_id, market_witness_provider=no_market)
  async with sessions() as db:
    rows = list((await db.scalars(select(TradeIntentRecord))).all())
    assert sorted(row.status for row in rows) == ["ALLOCATION_PENDING", "EXPIRED"]
    assert all(
      row.allocation_version == 0 and row.allocation_decision_id is None for row in rows
    )
    assert not list((await db.scalars(select(TAllocationBatchRecord))).all())
    assert not list((await db.scalars(select(PaperExecutionOrderRecord))).all())
    events = list(
      (
        await db.scalars(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.event_key.like("intent-expired:%")
          )
        )
      ).all()
    )
    assert len(events) == 1
