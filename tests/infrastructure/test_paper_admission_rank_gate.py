"""SQLite final rank barrier with real P3/allocation/admission/ledger calls.

The fixture's sink projects only intent status. This is not a PostgreSQL or
public ExitPlan closure test.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_domain.strategies.base import TradeIntent
from quantx_domain.trading.t_assistant_market_state import (
  SymbolMarketDeltaRing,
  TAssistantSymbolState,
)
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.account_risk_increase_admission import (
  AccountRiskIncreaseAdmissionSequencer,
)
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from sqlalchemy import func, select

from tests.infrastructure.test_paper_execution_ledger import (
  NOW,
  quote,
  seed_values,
)
from tests.infrastructure.test_paper_execution_ledger import (
  allocation_sessions as _allocation_sessions,
)
from tests.infrastructure.test_paper_execution_ledger import (
  base_sessions as _base_sessions,
)
from tests.infrastructure.test_paper_execution_ledger import (
  sessions as _sessions,
)
from tests.infrastructure.test_t_allocation_repository import _claim, _prepared, _seed
from tests.infrastructure.test_t_assistant_runtime_repository import _snapshot
from tests.infrastructure.test_t_intent_atomic_intake import (
  _intent,
  candidate_evidence_row,
)

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
sessions = _sessions
SUBMIT_AT = NOW + timedelta(milliseconds=2)


async def sink(db, execution_id, result):
  for response in result.orders:
    order = await db.get(PaperExecutionOrderRecord, response.order_id)
    assert order.execution_id == execution_id
    row = await db.get(TradeIntentRecord, order.intent_id)
    row.status = (
      "ROUTED" if response.status.value == "SUBMITTED" else response.status.value
    )


async def seed_ranked(sessions):
  first_snapshot, first_candidates = await _seed(sessions, authorization="AUTO")
  scope = first_snapshot.cut.execution_ref.owner_id
  async with sessions() as db, db.begin():
    execution = await TAssistantExecutionRepository(db).get_domain(scope)
    original = _snapshot(execution)
    code = "000001.SZ"
    sample = replace(
      original.symbols[0].delta_slice.ticks[0].sample, instrument_code=code
    )
    tick = replace(original.symbols[0].delta_slice.ticks[0], sample=sample)
    ring = SymbolMarketDeltaRing(code)
    ring.accept(tick, capture_time_ms=tick.received_at_ms)
    state = TAssistantSymbolState.initial(
      execution_id=scope,
      instrument_code=code,
      policy_version=execution.policy_version,
      feature_schema_version=execution.feature_schema_version,
      trade_date=original.trade_date,
    )
    snapshot = replace(
      original,
      symbols=(
        replace(
          original.symbols[0],
          instrument_code=code,
          state=state,
          delta_slice=ring.slice_after(None, decision_time_ms=tick.received_at_ms),
        ),
      ),
    )
    cycles = TAssistantDecisionCycleRepository(db)
    cycle = await cycles.prepare_material_cycle(
      snapshot=snapshot, cycle_id="second-cycle", now=NOW
    )
    claim = await cycles.claim(
      cycle_id=cycle.cycle_id,
      processing_owner="fixture",
      expected_input_manifest_hash=cycle.input_manifest_hash,
      now=NOW,
    )
    second = _intent(execution, intent_id="higher-intent", cycle_id=cycle.cycle_id)
    second.instrument_code = code
    second.approval_ttl_ms = 60000
    second.metadata.update(
      source_time_ms=int(NOW.timestamp() * 1000), opportunity_score=89
    )
    await cycles.commit_material_cycle(
      claim=claim,
      expected_input_manifest_hash=cycle.input_manifest_hash,
      symbol_patches=(),
      execution_events=(),
      trade_intents=(second,),
      now=NOW,
      opportunity_evidence=(
        candidate_evidence_row(execution, second),
      ),
    )
    second_snapshot = replace(
      first_snapshot,
      cycle_id=cycle.cycle_id,
      envelopes=(
        replace(
          first_snapshot.envelopes[0],
          observed_position_projection=replace(
            first_snapshot.envelopes[0].observed_position_projection,
            instrument_code=code,
          ),
        ),
      ),
    )
    second_candidates = (
      replace(
        first_candidates[0],
        intent_id=second.intent_id,
        candidate_id="candidate",
        candidate_fingerprint="fingerprint",
        instrument_code=code,
        rank_score=Decimal("0.89"),
        rule_score=Decimal(89),
      ),
    )
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    initial = seed_values()
    initial["positions"][code] = replace(
      initial["positions"]["600000.SH"], instrument_code=code
    )
    initial["bucket_checkpoint"]["instruments"][code] = deepcopy(
      initial["bucket_checkpoint"]["instruments"]["600000.SH"]
    )
    await ledger.initialize(execution_id=scope, account_id="account-1", **initial)
    repository = TAllocationRepository(db)
    for index, (portfolio, candidates) in enumerate(
      (
        (first_snapshot, first_candidates),
        (second_snapshot, second_candidates),
      )
    ):
      at = NOW + timedelta(milliseconds=index)
      batch = await _prepared(repository, portfolio, candidates, now=at)
      claimed = await _claim(repository, batch, portfolio, candidates, now=at)
      await repository.commit(
        claim=claimed, snapshot=portfolio, candidates=candidates, now=at
      )
    state = await ledger.get_snapshot(execution_id=scope)
    service = AccountRiskIncreaseAdmissionSequencer(
      db,
      environment=ExecutionEnvironment.PAPER,
      paper_execution_id=scope,
    )
    evidence = dict(
      account_snapshot_id=state["snapshot_id"],
      account_snapshot_hash=state["snapshot_hash"],
      obligation_watermark="b" * 64,
      now=SUBMIT_AT,
      commit=False,
    )
    batch = await service.prepare_batch(account_id="account-1", **evidence)
    claimed = await service.claim_batch(
      admission_batch_id=batch.admission_batch_id,
      processing_owner="fixture",
      now=SUBMIT_AT,
      commit=False,
    )
    await service.commit_batch(
      admission_batch_id=batch.admission_batch_id,
      fence_token=claimed.fence_token,
      **evidence,
    )
    items = await service.repository.items(batch.admission_batch_id)
    assert [item.intent_id for item in items] == [
      first_candidates[0].intent_id,
      second.intent_id,
    ]
    return scope, first_candidates[0].intent_id, second.intent_id


async def arguments(db, scope, intent_id):
  from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
  from quantx_domain.strategies.base import TAssistantExecutionIntentOrigin
  from quantx_domain.trading.order_sizer import OrderSizer
  from quantx_domain.trading.risk_checker import TradingRiskChecker

  raw = await db.get(TradeIntentRecord, intent_id)
  execution = await TAssistantExecutionRepository(db).get_domain(scope)
  value = TradeIntent(
    intent_id=raw.id,
    strategy_id=raw.strategy_id,
    instrument_code=raw.instrument_code,
    direction="BUY",
    bucket=raw.bucket,
    reason=raw.reason,
    target_amount=raw.target_amount,
    execution_ref=execution.execution_ref,
    origin=TAssistantExecutionIntentOrigin(
      scope,
      "fixture",
      raw.intent_metadata["candidate_id"],
      raw.intent_metadata["candidate_id"],
      raw.allocation_cycle_id,
    ),
    metadata=dict(raw.intent_metadata),
  )
  draft = OrderSizer().draft_intent(
    value,
    OrderType.BUY,
    9.9,
    {"cash": 100000},
    {"available_volume": 1000},
    allocated_amount_cap=1000,
  )
  request = OrderRequest(
    instrument_code=raw.instrument_code,
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=draft.sized_volume,
    price=9.9,
    execution_ref=execution.execution_ref,
    environment=ExecutionEnvironment.PAPER,
    metadata={
      "bucket": raw.bucket,
      "intent_id": raw.id,
      "order_expire_at_ms": int((NOW + timedelta(seconds=60)).timestamp() * 1000),
    },
  )
  risk = await TradingRiskChecker(
    strict_market_data=True, strict_limit_data=True
  ).evaluate_order(
    request,
    account={"cash": 100000},
    position={"available_volume": 1000},
    market_data=replace(quote(0), instrument_code=raw.instrument_code),
    current_time=SUBMIT_AT,
  )
  assert risk.allowed
  state = await PaperExecutionLedger(db, receipt_sink=sink).get_snapshot(
    execution_id=scope
  )
  return dict(
    execution_id=scope,
    event_key="place:" + raw.id,
    order_id="order:" + raw.id,
    intent_id=raw.id,
    order_attempt=0,
    request=request,
    sizing_evidence=draft,
    risk_evidence=risk,
    expected_revision=state["revision"],
    expected_snapshot_hash=state["snapshot_hash"],
    now=SUBMIT_AT,
  )


@pytest.mark.parametrize(
  "status", ["EXECUTION_READY", "ROUTED", "FILLED", "DELAYED", "AWAITING_APPROVAL"]
)
async def test_higher_first_and_status_without_order_never_count_as_acceptance(
  sessions, status
):
  scope, lower, higher = await seed_ranked(sessions)
  async with sessions() as db, db.begin():
    row = await db.get(TradeIntentRecord, lower)
    row.status = status
    await db.flush()
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    before = await ledger.get_snapshot(execution_id=scope)
    with pytest.raises(ValueError, match="PAPER_ADMISSION_PREDECESSOR_PENDING"):
      await ledger.place_order(**await arguments(db, scope, higher))
    assert await ledger.get_snapshot(execution_id=scope) == before
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionOrderRecord)) == 0
    )
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord)) == 0
    )


@pytest.mark.parametrize("status", ["REJECTED", "EXPIRED", "CANCELLED"])
async def test_explicit_terminal_predecessor_allows_progress_without_order(
  sessions, status
):
  scope, lower, higher = await seed_ranked(sessions)
  async with sessions() as db, db.begin():
    (await db.get(TradeIntentRecord, lower)).status = status
    await db.flush()
    await PaperExecutionLedger(db, receipt_sink=sink).place_order(
      **await arguments(db, scope, higher)
    )
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 1


async def test_accepted_lower_requires_new_snapshot_then_higher_and_old_retry_succeed(
  sessions,
):
  scope, lower, higher = await seed_ranked(sessions)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    low_args, stale_high = (
      await arguments(db, scope, lower),
      await arguments(db, scope, higher),
    )
    low_receipt = await ledger.place_order(**low_args)
    with pytest.raises(ValueError, match="PAPER_ACCOUNT_REVISION_CONFLICT"):
      await ledger.place_order(**stale_high)
    fresh_high = {
      **stale_high,
      "expected_revision": 1,
      "expected_snapshot_hash": (await ledger.get_snapshot(execution_id=scope))[
        "snapshot_hash"
      ],
    }
    await ledger.place_order(**fresh_high)
    lower_row = await db.get(TradeIntentRecord, lower)
    lower_row.admission_input_fingerprint = "c" * 64
    await db.flush()
    retried = await ledger.place_order(**low_args)
    assert retried.duplicate and retried.event_id == low_receipt.event_id
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 2


@pytest.mark.parametrize(
  "change", ["owner", "rank", "binding", "policy", "fingerprint", "missing_item"]
)
async def test_predecessor_scope_and_current_binding_fail_closed(sessions, change):
  scope, lower, higher = await seed_ranked(sessions)
  async with sessions() as db, db.begin():
    row = await db.get(TradeIntentRecord, lower)
    row.status = "REJECTED"
    item = await db.scalar(
      select(AccountRiskIncreaseAdmissionItem).where(
        AccountRiskIncreaseAdmissionItem.intent_id == lower,
      )
    )
    if change == "owner":
      item.owner_id = "different-execution"
    elif change == "rank":
      row.admission_rank = 9
    elif change == "binding":
      row.admission_batch_id = None
      row.admission_rank = None
      row.admission_policy_version = None
      row.admission_input_fingerprint = None
    elif change == "policy":
      row.admission_policy_version = "changed"
    elif change == "fingerprint":
      row.admission_input_fingerprint = "f" * 64
    else:
      await db.delete(item)
    await db.flush()
    with pytest.raises(ValueError, match="PAPER_ADMISSION_PREDECESSOR_SCOPE_CONFLICT"):
      await PaperExecutionLedger(db, receipt_sink=sink).place_order(
        **await arguments(db, scope, higher)
      )


@pytest.mark.parametrize("change", ["owner", "instrument", "admission"])
async def test_real_predecessor_order_must_match_admission_and_owner(sessions, change):
  scope, lower, higher = await seed_ranked(sessions)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(**await arguments(db, scope, lower))
    order = await db.scalar(
      select(PaperExecutionOrderRecord).where(
        PaperExecutionOrderRecord.intent_id == lower,
      )
    )
    # SQLite corruption fixture: PostgreSQL also protects these immutable facts.
    if change == "owner":
      order.owner_id = "different-execution"
    elif change == "instrument":
      order.instrument_code = "600001.SH"
    else:
      order.admission_batch_id = None
    await db.flush()
    before = await ledger.get_snapshot(execution_id=scope)
    with pytest.raises(ValueError, match="PAPER_ADMISSION_PREDECESSOR_SCOPE_CONFLICT"):
      await ledger.place_order(**await arguments(db, scope, higher))
    assert await ledger.get_snapshot(execution_id=scope) == before
