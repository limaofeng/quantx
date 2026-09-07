"""Authoritative portfolio cuts over actual PAPER receipts and public plans."""

import os
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_portfolio_snapshot import (
  PaperPortfolioSnapshotReader,
)
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import event

from tests.infrastructure import test_t_allocation_repository as allocation_tests
from tests.infrastructure.test_paper_receipt_convergence import (
  allocation_sessions as _allocation_sessions,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  base_sessions as _base_sessions,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  enrich_intent,
  quote,
  setup,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  ledger_sessions as _ledger_sessions,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  sessions as _sessions,
)
from tests.infrastructure.test_t_assistant_runtime_repository import NOW

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
ledger_sessions = _ledger_sessions
sessions = _sessions


def storage_time(model, field, value):
  value = value.astimezone(UTC)
  return value if model.__table__.c[field].type.timezone else value.replace(tzinfo=None)


@pytest.mark.skipif(
  os.environ.get("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="isolated PostgreSQL migration gate",
)
@pytest.mark.parametrize(
  "scenario", ["obligations", "allocation_recovery", "delayed_close"]
)
async def test_postgresql_portfolio_cut_uses_actual_paper_obligations(
  frozen_config, scenario, monkeypatch
):
  from tests.infrastructure.test_p4_allocation_postgresql import (
    _sessions as migrated_sessions,
  )

  async with migrated_sessions(head="20260907_0055") as sessions:
    if scenario == "obligations":
      await test_actual_pending_partial_daily_pnl_and_watermark(sessions, frozen_config)
    elif scenario == "allocation_recovery":
      await test_unallocated_reader_prepare_restart_claim_commit_keeps_source_cut(
        sessions, frozen_config
      )
    else:
      await test_next_day_t_pnl_requires_actual_close_window(
        sessions, frozen_config, monkeypatch, 60
      )
    async with sessions() as db:
      from sqlalchemy import text

      definition = await db.scalar(
        text(
          "SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() "
          "AND indexname='ix_paper_event_scope_quote_source'"
        )
      )
      assert "(execution_id, event_type, quote_source_at)" in definition


def reference_payload():
  return {
    "portfolio_policy": {
      "version": "portfolio-v1",
      "max_total_t_amount": "20000",
      "max_total_asset_fraction": "0.5",
      "cash_buffer": "100",
      "max_industry_t_amount": "10000",
      "max_concurrent_batches": 10,
      "max_daily_loss": "1000",
      "mark_max_age_seconds": 60,
      "industry_classification": {
        "version": "industry-v1",
        "as_of": (NOW - timedelta(days=1)).isoformat(),
        "effective_from": (NOW - timedelta(days=1)).isoformat(),
        "mappings": {"600000.SH": "bank", "000001.SZ": "bank"},
      },
      "trading_calendar": {
        "version": "calendar-v1",
        "as_of": (NOW - timedelta(days=2)).isoformat(),
        "valid_from": "2026-09-01",
        "valid_through": "2026-09-04",
        "trading_dates": ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"],
        "complete": True,
      },
    },
    "t_trading_envelope_policy": {
      "version": "envelope-v1",
      "protected_core_volume": 0,
      "max_symbol_t_amount": "10000",
      "max_entry_volume": 1000,
    },
  }


@pytest.fixture
def frozen_config(monkeypatch, request):
  from quantx_infrastructure.models.agent_runtime import TTradeBatch
  from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
  from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
  from quantx_infrastructure.repositories.auto_exit_plan_repository import (
    AutoExitPlanRepository,
  )
  from quantx_infrastructure.repositories.t_assistant_config_repository import (
    TAssistantConfigRepository,
  )
  from quantx_infrastructure.repositories.t_assistant_execution_repository import (
    TAssistantExecutionRepository,
  )
  from sqlalchemy import inspect
  from sqlalchemy.orm.attributes import flag_modified

  original = allocation_tests._version
  original_ensure = TAssistantExecutionRepository.ensure_paper_shadow
  head_clock = {
    "head_time": NOW - timedelta(days=2),
    "obligation_time": NOW - timedelta(days=2),
  }
  original_cas = AutoExitPlanRepository.compare_and_swap_state

  async def historical_cas(repository, **kwargs):
    result = await original_cas(repository, **kwargs)
    record = await repository.db.get(
      AutoExitPlanRecord, kwargs["plan_id"], populate_existing=True
    )
    record.updated_at = storage_time(
      AutoExitPlanRecord, "updated_at", head_clock["obligation_time"]
    )
    await repository.db.flush()
    return result

  async def activate_then_ensure(repository, *, account_id, version, now):
    head = await repository.db.get(TTradeGlobalConfig, version.config_id)
    await TAssistantConfigRepository(repository.db).activate_version(
      config_id=version.config_id,
      config_version_id=version.config_version_id,
      desired_environment="PAPER",
      expected_state_version=head.state_version,
    )
    # activate_version uses a bulk UPDATE, which bypasses mapper before_update.
    head.updated_at = storage_time(
      TTradeGlobalConfig, "updated_at", head_clock["head_time"]
    )
    await repository.db.flush()
    return await original_ensure(
      repository, account_id=account_id, version=version, now=now
    )

  def version(config_id):
    values = asdict(original(config_id))
    values.pop("config_snapshot_hash")
    values["canonical_payload"].update(reference_payload())
    if getattr(request, "param", None) == "missing-industry":
      values["canonical_payload"]["portfolio_policy"]["industry_classification"][
        "mappings"
      ].pop("600000.SH")
    if getattr(request, "param", None) == "retired-A":
      policy = values["canonical_payload"]["portfolio_policy"]
      policy["industry_classification"]["mappings"].pop("600000.SH")
      policy["trading_calendar"]["valid_through"] = "2026-09-07"
      policy["trading_calendar"]["trading_dates"].append("2026-09-07")
    return TAssistantConfigVersion.create(**values)

  def historical_creation(_mapper, _connection, target):
    target.created_at = storage_time(
      type(target), "created_at", NOW - timedelta(days=2)
    )
    if isinstance(target, TTradeGlobalConfig):
      target.updated_at = storage_time(
        type(target), "updated_at", head_clock["head_time"]
      )

  def historical_control_update(_mapper, _connection, target):
    target.updated_at = storage_time(
      type(target), "updated_at", head_clock["head_time"]
    )
    flag_modified(target, "updated_at")

  def historical_obligation_creation(_mapper, _connection, target):
    target.created_at = storage_time(
      type(target), "created_at", head_clock["obligation_time"]
    )
    if target.updated_at is None:
      target.updated_at = storage_time(
        type(target), "updated_at", head_clock["obligation_time"]
      )

  def historical_obligation_update(_mapper, _connection, target):
    # Explicit availability supplied by a negative test must survive the clock.
    if not inspect(target).attrs.updated_at.history.has_changes():
      target.updated_at = storage_time(
        type(target), "updated_at", head_clock["obligation_time"]
      )
      flag_modified(target, "updated_at")

  monkeypatch.setattr(allocation_tests, "_version", version)
  monkeypatch.setattr(
    TAssistantExecutionRepository, "ensure_paper_shadow", activate_then_ensure
  )
  for model in (
    TAssistantConfigVersionRecord,
    TAssistantExecutionRecord,
    TTradeGlobalConfig,
  ):
    event.listen(model, "before_insert", historical_creation)
  event.listen(TTradeGlobalConfig, "before_update", historical_control_update)
  obligation_models = (TradeIntentRecord, TTradeBatch, AutoExitPlanRecord)
  for model in obligation_models:
    event.listen(model, "before_insert", historical_obligation_creation)
    event.listen(model, "before_update", historical_obligation_update)
  # The real CAS is still executed; only fixture persistence availability is
  # assigned explicitly after its bulk SQL update (mapper events do not fire).
  monkeypatch.setattr(AutoExitPlanRepository, "compare_and_swap_state", historical_cas)
  yield head_clock
  for model in obligation_models:
    event.remove(model, "before_insert", historical_obligation_creation)
    event.remove(model, "before_update", historical_obligation_update)
  event.remove(TTradeGlobalConfig, "before_update", historical_control_update)
  for model in (
    TAssistantConfigVersionRecord,
    TAssistantExecutionRecord,
    TTradeGlobalConfig,
  ):
    event.remove(model, "before_insert", historical_creation)


async def read(db, execution_id, *, at=NOW, codes=("600000.SH",)):
  return await PaperPortfolioSnapshotReader(db).read(
    execution_id=execution_id, cycle_id="intake-cycle", instrument_codes=codes, as_of=at
  )


async def test_actual_pending_partial_daily_pnl_and_watermark(sessions, frozen_config):
  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    initial = await read(db, scope)
    assert initial.total_assets == Decimal("110050")
    assert initial.uncovered_buy_amount == Decimal("1000")
    assert (
      initial.current_t_exposure
      == initial.realized_t_pnl
      == initial.unrealized_t_pnl
      == 0
    )
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    pending = await read(db, scope)
    assert pending.uncovered_buy_amount == Decimal("995.00990000")
    assert pending.active_batch_count == 1
    await ledger.process_quote(
      execution_id=scope,
      event_key="partial",
      accepted_at=(quote(1)).timestamp,
      quote=quote(1),
    )
    partial = await read(db, scope, at=quote(1).timestamp)
    assert partial.uncovered_buy_amount == Decimal("495.00495000")
    assert partial.current_t_exposure == Decimal("500.00495000")
    assert partial.realized_t_pnl == 0
    assert partial.unrealized_t_pnl == Decimal("-5.00495000")
    assert (
      partial.envelopes[0].observed_position_projection.uncovered_protected_volume
      == 100
    )
    assert partial.portfolio_input_fingerprint != pending.portfolio_input_fingerprint
    repeated = await read(db, scope, at=quote(1).timestamp)
    assert repeated == partial


@pytest.mark.parametrize("corruption", ["missing-batch", "wrong-template-source"])
async def test_ready_intent_requires_frozen_public_batch_identity(
  sessions, frozen_config, corruption
):
  def malformed(intent):
    enrich_intent(intent)
    if corruption == "missing-batch":
      intent.metadata.pop("t_batch_id")
    else:
      intent.metadata["exit_plan_template"]["source_id"] = "different-batch"

  scope, _ = await setup(sessions, PaperReceiptConvergence(), enrich_intent=malformed)
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match="READY_SOURCE"):
      await read(db, scope)


async def test_head_reads_never_reverse_execution_lock_and_detect_publication(
  sessions, frozen_config, monkeypatch
):
  from sqlalchemy import update

  scope, _ = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    original = db.scalars
    head_reads = 0

    async def scalars(statement, *args, **kwargs):
      nonlocal head_reads
      entity = statement.column_descriptions[0].get("entity")
      if entity in {TTradeGlobalConfig, TAssistantConfigVersionRecord}:
        assert statement._for_update_arg is None
      if entity is TTradeGlobalConfig:
        head_reads += 1
        if head_reads == 2:
          # Inject an observed publication between the two reads. This verifies
          # validation, not a SQLite proof of PostgreSQL lock concurrency.
          await db.execute(
            update(TTradeGlobalConfig)
            .where(TTradeGlobalConfig.id == "config-1")
            .values(
              enabled=False,
              state_version=TTradeGlobalConfig.state_version + 1,
              updated_at=storage_time(
                TTradeGlobalConfig, "updated_at", frozen_config["head_time"]
              ),
            )
          )
      return await original(statement, *args, **kwargs)

    monkeypatch.setattr(db, "scalars", scalars)
    with pytest.raises(ValueError, match="HEAD_CHANGED"):
      await read(db, scope)
    assert head_reads == 2


async def test_old_running_execution_rejected_after_new_head_is_published(
  sessions, frozen_config
):
  from quantx_infrastructure.repositories.t_assistant_config_repository import (
    TAssistantConfigRepository,
  )

  scope, _ = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    current = await db.get(TAssistantConfigVersionRecord, "config-version-1")
    values = {
      field: getattr(current, field)
      for field in (
        "config_id",
        "config_schema_version",
        "canonical_payload",
        "entry_authorization",
        "rollout_stage",
        "policy_version",
        "feature_schema_version",
        "scorer_mode",
        "model_runtime_binding",
      )
    }
    values["canonical_payload"] = {
      **current.canonical_payload,
      "portfolio_policy": {
        **current.canonical_payload["portfolio_policy"],
        "cash_buffer": "200",
      },
    }
    new = TAssistantConfigVersion.create(
      **values, config_version_id="config-version-2", version=2
    )
    repository = TAssistantConfigRepository(db)
    await repository.append_version(new)
    head = await db.get(TTradeGlobalConfig, "config-1")
    # User config revision precedes immutable snapshot activation.
    head.config_version = 2
    await db.flush()
    await repository.activate_version(
      config_id=head.id,
      config_version_id=new.config_version_id,
      desired_environment="PAPER",
      expected_state_version=head.state_version,
    )
    head.updated_at = storage_time(
      TTradeGlobalConfig, "updated_at", frozen_config["head_time"]
    )
    await db.flush()
    with pytest.raises(ValueError, match="ACTIVE_CONFIG_CONFLICT"):
      await read(db, scope)


async def test_later_head_publication_cannot_enable_an_older_cut(
  sessions, frozen_config
):
  scope, _ = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    assert (await read(db, scope)).entry_enabled
    head = await db.get(TTradeGlobalConfig, "config-1")
    head.enabled = False
    head.state_version += 1
    await db.flush()
    assert (await read(db, scope)).kill_switch
    frozen_config["head_time"] = NOW + timedelta(seconds=1)
    head.enabled = True
    head.state_version += 1
    await db.flush()
    with pytest.raises(ValueError, match="FUTURE_EVIDENCE"):
      await read(db, scope)


async def test_kill_state_and_stale_or_future_cut(sessions, frozen_config):
  sink = PaperReceiptConvergence()
  scope, _ = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    before = await read(db, scope)
    global_config = await db.get(TTradeGlobalConfig, "config-1")
    global_config.enabled = False
    global_config.state_version += 1
    await db.flush()
    killed = await read(db, scope)
    assert killed.kill_switch and not killed.entry_enabled
    assert (
      killed.cut.local_obligation_watermark != before.cut.local_obligation_watermark
    )
    with pytest.raises(ValueError, match="STALE"):
      await read(db, scope, at=NOW + timedelta(seconds=61))
    with pytest.raises(ValueError, match="FUTURE"):
      await read(db, scope, at=NOW - timedelta(seconds=1))


async def test_non_candidate_t_exposure_still_counts_in_account_and_industry(
  sessions, frozen_config
):
  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    await ledger.process_quote(
      execution_id=scope,
      event_key="partial",
      accepted_at=(quote(1)).timestamp,
      quote=quote(1),
    )
    await ledger.process_quote(
      execution_id=scope,
      event_key="candidate-B",
      accepted_at=(replace(quote(1), instrument_code="000001.SZ")).timestamp,
      quote=replace(quote(1), instrument_code="000001.SZ"),
    )
    value = await read(db, scope, at=quote(1).timestamp, codes=("000001.SZ",))
    assert (
      value.envelopes[0].observed_position_projection.instrument_code == "000001.SZ"
    )
    assert value.envelopes[0].observed_position_projection.current_t_exposure == 0
    assert value.current_t_exposure == Decimal("500.00495000")
    assert value.uncovered_buy_amount == Decimal("495.00495000")
    assert value.industry_exposures[0].current_t_exposure == value.current_t_exposure
    assert value.active_batch_count == 1
    assert value.total_assets == Decimal("109944.99505")


@pytest.mark.parametrize("closing_second", [60, 66, None])
async def test_next_day_t_pnl_requires_actual_close_window(
  sessions, frozen_config, monkeypatch, closing_second
):
  from tests.infrastructure import test_paper_execution_ledger as ledger_tests
  from tests.infrastructure import test_t_assistant_runtime_repository as runtime_tests
  from tests.infrastructure import test_t_intent_atomic_intake as intake_tests

  start = datetime(2026, 9, 3, 6, 59, tzinfo=UTC)
  for module in (allocation_tests, ledger_tests, runtime_tests, intake_tests):
    monkeypatch.setattr(module, "NOW", start)
  sink = PaperReceiptConvergence()
  scope, args = await setup(
    sessions, sink, enrich_intent=enrich_intent, allocation_at=start, admission_at=start
  )
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    await ledger.process_quote(
      execution_id=scope,
      event_key="buy",
      accepted_at=(quote(1, depth=400)).timestamp,
      quote=quote(1, depth=400),
    )
    if closing_second is not None:
      await ledger.process_quote(
        execution_id=scope,
        event_key="close",
        accepted_at=(quote(closing_second + 10)).timestamp,
        quote=quote(closing_second),
      )
    next_day = datetime(2026, 9, 4, 1, 30, tzinfo=UTC)
    await ledger.process_quote(
      execution_id=scope,
      event_key="next-day",
      accepted_at=(quote(int((next_day - start).total_seconds()))).timestamp,
      quote=quote(int((next_day - start).total_seconds())),
    )
    if closing_second == 60:
      value = await read(db, scope, at=next_day)
      assert value.realized_t_pnl == value.unrealized_t_pnl == 0
      assert value.current_t_exposure == Decimal("995.00990000")
    else:
      with pytest.raises(ValueError, match="OPENING_MARK"):
        await read(db, scope, at=next_day)


async def test_future_config_creation_is_not_inferred_from_its_payload(
  sessions, frozen_config
):
  def future_creation(_mapper, _connection, target):
    target.created_at = NOW + timedelta(seconds=1)

  event.listen(TAssistantConfigVersionRecord, "before_insert", future_creation)
  try:
    scope, _ = await setup(
      sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
    )
  finally:
    event.remove(TAssistantConfigVersionRecord, "before_insert", future_creation)
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match="FUTURE"):
      await read(db, scope)


@pytest.mark.parametrize("frozen_config", ["missing-industry"], indirect=True)
async def test_non_candidate_held_industry_must_be_explicit(sessions, frozen_config):
  scope, _ = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match="INDUSTRY_MAPPING_INCOMPLETE"):
      await read(db, scope, codes=("000001.SZ",))


async def test_actual_sell_pending_envelope_uses_already_unfrozen_capacity(
  sessions, frozen_config, monkeypatch
):
  from quantx_domain.brokers.base import OrderType

  from tests.infrastructure.test_paper_receipt_convergence import (
    test_public_plan_sell_receipt_closes_batch_without_pending_mismatch,
  )

  original = PaperExecutionLedger.place_order
  seen = []

  async def place(ledger, **kwargs):
    result = await original(ledger, **kwargs)
    if kwargs["request"].order_type is OrderType.SELL:
      snapshot = await read(ledger.db, kwargs["execution_id"], at=kwargs["now"])
      envelope = snapshot.envelopes[0]
      assert envelope.observed_position_projection.old_sellable_volume == 900
      assert envelope.observed_position_projection.uncovered_protected_volume == 0
      assert envelope.planning_replaceable_old_volume_ceiling == 900
      seen.append(snapshot)
    return result

  monkeypatch.setattr(PaperExecutionLedger, "place_order", place)
  await test_public_plan_sell_receipt_closes_batch_without_pending_mismatch(sessions)
  assert len(seen) == 1


async def test_error_plan_blocks_entry_and_no_live_query(sessions, frozen_config):
  import re

  from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
  from quantx_domain.trading.exit_plan import ExitPlan, ExitPlanStatus
  from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
  from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService

  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    await ledger.process_quote(
      execution_id=scope,
      event_key="partial",
      accepted_at=(quote(1)).timestamp,
      quote=quote(1),
    )
    row = await db.get(AutoExitPlanRecord, "paper-plan")
    plan = ExitPlan.from_dict(row.plan_state)
    plan.status = ExitPlanStatus.ERROR
    await AutoExitPlanService().persist_execution_plan_state(
      execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", scope),
      environment=ExecutionEnvironment.PAPER,
      plan_state=plan.to_dict(),
      expected_state_version=row.state_version,
      evaluated_at=quote(1).timestamp,
      event_business_key="test-plan-error",
      db=db,
      commit=False,
    )

    def guard(_connection, _cursor, statement, _parameters, _context, _many):
      assert not re.search(
        r"\b(?:positions|account_execution_controls|pending_trade_orders|trade_command_outbox|agent_report_inbox)\b",
        statement.lower(),
      )

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", guard)
    try:
      snapshot = await read(db, scope, at=quote(1).timestamp)
      assert snapshot.reconcile_required and snapshot.planning_amount_cap == 0
    finally:
      event.remove(engine, "before_cursor_execute", guard)


async def test_future_intent_cancellation_cannot_release_old_cut_cap(
  sessions, frozen_config
):
  from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    assert (await read(db, scope)).uncovered_buy_amount == Decimal("1000")
    row = await db.get(TradeIntentRecord, args["intent_id"])
    row.status = "CANCELLED"
    row.updated_at = storage_time(type(row), "updated_at", NOW + timedelta(seconds=1))
    await db.flush()
    with pytest.raises(ValueError, match="FUTURE_EVIDENCE"):
      await read(db, scope)


async def test_future_plan_error_clear_cannot_unblock_old_cut(sessions, frozen_config):
  from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord

  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    await ledger.process_quote(
      execution_id=scope,
      event_key="partial",
      accepted_at=(quote(1)).timestamp,
      quote=quote(1),
    )
    row = await db.get(AutoExitPlanRecord, "paper-plan")
    row.status = "ERROR"
    await db.flush()
    assert (await read(db, scope, at=quote(1).timestamp)).reconcile_required
    row.status = "ACTIVE"
    row.updated_at = storage_time(type(row), "updated_at", quote(2).timestamp)
    await db.flush()
    with pytest.raises(ValueError, match="FUTURE_EVIDENCE"):
      await read(db, scope, at=quote(1).timestamp)


@pytest.mark.parametrize("frozen_config", ["retired-A"], indirect=True)
async def test_closed_zero_balance_A_does_not_need_mark_or_industry_when_B_is_healthy(
  sessions, frozen_config, monkeypatch
):
  from quantx_infrastructure.models.paper_execution import (
    PaperExecutionAccountRecord,
    PaperExecutionOrderRecord,
  )
  from sqlalchemy import select

  from tests.infrastructure import test_paper_receipt_convergence as receipt_tests
  from tests.infrastructure.test_paper_execution_ledger import seed_values

  original_setup, original_quote = receipt_tests.setup, receipt_tests.quote
  scope_ids = []

  async def empty_seed_setup(*args, **kwargs):
    seed = seed_values()
    seed["positions"] = {}
    seed["bucket_checkpoint"] = {"instruments": {}}
    result = await original_setup(*args, **kwargs, initial_seed=seed)
    scope_ids.append(result[0])
    return result

  def next_day_exit_quote(seconds, **kwargs):
    market = original_quote(seconds, **kwargs)
    return (
      replace(market, timestamp=market.timestamp + timedelta(days=1))
      if seconds >= 2
      else market
    )

  monkeypatch.setattr(receipt_tests, "setup", empty_seed_setup)
  monkeypatch.setattr(receipt_tests, "quote", next_day_exit_quote)
  # This is the real ledger/sink T+1 round trip: buy A, roll to next day, sell
  # its full acquired balance. No historical order/fill is deleted afterward.
  await (
    receipt_tests.test_public_plan_sell_receipt_closes_batch_without_pending_mismatch(
      sessions
    )
  )
  scope = scope_ids[0]
  cutoff = datetime(2026, 9, 7, 1, 30, tzinfo=UTC)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
    await ledger.process_quote(
      execution_id=scope,
      event_key="healthy-B",
      accepted_at=(
        replace(original_quote(3), instrument_code="000001.SZ", timestamp=cutoff)
      ).timestamp,
      quote=replace(original_quote(3), instrument_code="000001.SZ", timestamp=cutoff),
    )
    account = await db.get(PaperExecutionAccountRecord, scope)
    assert "600000.SH" not in account.broker_checkpoint["material"]["positions"]
    assert len(list((await db.scalars(select(PaperExecutionOrderRecord))).all())) == 2
    snapshot = await read(db, scope, at=cutoff, codes=("000001.SZ",))
    assert snapshot.current_t_exposure == snapshot.uncovered_buy_amount == 0
    assert snapshot.realized_t_pnl == snapshot.unrealized_t_pnl == 0
    assert snapshot.active_batch_count == 0
    assert len(snapshot.industry_exposures) == 1


async def test_unallocated_reader_prepare_restart_claim_commit_keeps_source_cut(
  sessions, frozen_config
):
  from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
  from quantx_infrastructure.repositories.t_allocation_repository import (
    TAllocationRepository,
  )

  from tests.infrastructure.test_paper_execution_ledger import seed_values

  source, candidates = await allocation_tests._seed(
    sessions, authorization="AUTO", enrich_intent=enrich_intent
  )
  scope = source.cut.execution_ref.owner_id
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence()).initialize(
      execution_id=scope, account_id="account-1", **seed_values()
    )
    initial = await read(db, scope)
    assert initial.uncovered_buy_amount == 0
    repository = TAllocationRepository(db)
    prepared = await repository.prepare(
      snapshot=initial,
      candidates=candidates,
      now=NOW,
      expires_at=NOW + timedelta(seconds=30),
    )
    batch_id = prepared.allocation_batch_id
    fresh = await read(db, scope, at=NOW + timedelta(milliseconds=1))
    assert fresh == initial
    assert fresh.cut.as_of == NOW
  # New session/repository simulates recovery of the same durable PREPARED batch.
  async with sessions() as db, db.begin():
    repository = TAllocationRepository(db)
    recovered = await read(db, scope, at=NOW + timedelta(milliseconds=2))
    assert recovered.portfolio_input_fingerprint == initial.portfolio_input_fingerprint
    claim = await repository.claim(
      allocation_batch_id=batch_id,
      processing_owner="restarted-worker",
      snapshot=recovered,
      candidates=candidates,
      now=NOW + timedelta(milliseconds=2),
      lease_seconds=10,
    )
    claimed_cut = await read(db, scope, at=NOW + timedelta(milliseconds=3))
    assert claimed_cut == initial
    committed = await repository.commit(
      claim=claim,
      snapshot=claimed_cut,
      candidates=candidates,
      now=NOW + timedelta(milliseconds=3),
    )
    from quantx_infrastructure.models.t_allocation import TAllocationDecisionRecord
    from sqlalchemy import select

    decisions = list(
      (
        await db.scalars(
          select(TAllocationDecisionRecord).where(
            TAllocationDecisionRecord.allocation_batch_id == batch_id
          )
        )
      ).all()
    )
    assert committed.status == "COMMITTED"
    assert len(decisions) == 1 and decisions[0].action == "ALLOW"
    assert (
      await db.get(TradeIntentRecord, candidates[0].intent_id)
    ).status == "EXECUTION_READY"


async def test_quote_history_growth_does_not_expand_portfolio_event_read(
  sessions, frozen_config, monkeypatch
):
  from types import SimpleNamespace

  from quantx_infrastructure.models.paper_execution import PaperExecutionEventRecord
  from sqlalchemy import func, select

  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    selected_counts = []
    original = db.scalars

    async def observed_scalars(statement, *args, **kwargs):
      result = await original(statement, *args, **kwargs)
      if (
        statement.column_descriptions[0].get("entity") is not PaperExecutionEventRecord
      ):
        return result
      sql = str(statement).lower()
      assert "event_id in" in sql and "revision =" in sql
      assert "quote_source_at >=" in sql and "quote_source_at <=" in sql
      assert statement._for_update_arg is None

      def observed_all():
        values = result.all()
        selected_counts.append(len(values))
        return values

      return SimpleNamespace(all=observed_all)

    monkeypatch.setattr(db, "scalars", observed_scalars)
    for second in range(1, 26):
      await ledger.process_quote(
        execution_id=scope,
        event_key=f"tick-{second}",
        accepted_at=(quote(second)).timestamp,
        quote=quote(second),
      )
      if second in (5, 25):
        snapshot = await read(db, scope, at=quote(second).timestamp)
        assert snapshot.current_t_exposure == Decimal("995.00990000")
    # Two fill-bearing quotes plus the latest mark/current-revision quote.
    # Twenty more unrelated ticks do not enter the selected evidence set.
    assert selected_counts == [3, 3]
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord)) == 26
    )
