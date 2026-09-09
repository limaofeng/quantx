from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

import pytest
from quantx_application.t_trade_v3.daily_t_valuation import TValuationMark
from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.services.live_portfolio_snapshot import (
  LivePortfolioSnapshotReader,
)

from tests.infrastructure.test_live_position_attribution import (
  CODE,
  NOW,
  SEED_AT,
  approve,
)
from tests.infrastructure.test_live_position_attribution import (
  capacity_db as _capacity_db,
)
from tests.infrastructure.test_live_position_attribution import db as _base_db
from tests.infrastructure.test_paper_portfolio_snapshot import reference_payload
from tests.infrastructure.test_t_assistant_runtime_repository import _version

capacity_db = _capacity_db
base_db = _base_db


@pytest.fixture
async def db(base_db):
  for model in (
    TAllocationBatchRecord,
    TAllocationDecisionRecord,
    TAssistantDecisionCycleRecord,
  ):
    await base_db.run_sync(lambda session: model.__table__.create(session.connection()))
  await approve(base_db)
  old = await base_db.get(TAssistantExecutionRecord, "source")
  old.status = "STOPPED"
  old.entry_readiness = "BLOCKED"
  old.completed_at = NOW
  old.updated_at = NOW
  await base_db.flush()
  data = asdict(_version("config-1"))
  data.pop("config_snapshot_hash")
  data.update(config_version_id="new-config", version=2)
  reference = reference_payload()
  reference["portfolio_policy"]["trading_calendar"].update(
    valid_through="2026-09-10", trading_dates=["2026-09-08", "2026-09-09", "2026-09-10"]
  )
  data["canonical_payload"].update(reference)
  version = TAssistantConfigVersion.create(**data)
  base_db.add(TAssistantConfigVersionRecord(**asdict(version), created_at=SEED_AT))
  base_db.add(
    TAssistantExecutionRecord(
      execution_id="new-source",
      config_id="config-1",
      config_version_id=version.config_version_id,
      frozen_config_version=2,
      config_snapshot_hash=version.config_snapshot_hash,
      account_id="account",
      environment="LIVE",
      entry_authorization="MANUAL_CONFIRM",
      rollout_stage="CANARY",
      status="RUNNING",
      entry_readiness="READY",
      entry_readiness_reasons=[],
      entry_readiness_as_of=NOW,
      policy_version=version.policy_version,
      feature_schema_version=1,
      scorer_mode="RULE_ONLY",
      created_at=NOW,
      updated_at=NOW,
    )
  )
  head = await base_db.get(TTradeGlobalConfig, "config-1")
  head.active_config_version_id = "new-config"
  head.config_version = 2
  head.desired_environment = "LIVE"
  head.enabled = True
  head.created_at = SEED_AT
  head.updated_at = NOW
  control = await base_db.get(AccountExecutionControl, "account")
  control.authorization_state = "ENABLED"
  control.reconcile_status = "READY"
  control.updated_at = NOW
  base_db.add(
    TAssistantDecisionCycleRecord(
      cycle_id="cycle",
      execution_id="new-source",
      cycle_sequence=1,
      decision_key="a" * 64,
      attempt=1,
      snapshot_hash="b" * 64,
      fence_from=0,
      fence_to=1,
      market_delta_manifest_hash="c" * 64,
      reducer_cursor_manifest_hash="d" * 64,
      status="PREPARED",
      input_manifest_hash="e" * 64,
      input_manifest={},
      prepared_at=NOW,
      created_at=NOW,
    )
  )
  await base_db.commit()
  return base_db


async def read(db, **changes):
  args = dict(
    execution_id="new-source",
    cycle_id="cycle",
    instrument_codes=[CODE],
    as_of=NOW,
    current_marks={CODE: TValuationMark(CODE, Decimal(10), NOW, "quote")},
    opening_marks={},
    account_max_age_seconds=90,
  )
  args.update(changes)
  current, opening = args.pop("current_marks"), args.pop("opening_marks")

  class Marks:
    async def read(self, **kwargs):
      from types import SimpleNamespace

      return SimpleNamespace(as_of=kwargs["as_of"], current=current, opening=opening)

  args.setdefault("market_mark_reader", Marks())
  async with db.begin():
    return await LivePortfolioSnapshotReader(db).read(**args)


async def test_complete_live_cut_reuses_old_seed_and_preserves_floor(db):
  result = await read(db)
  assert result.cut.execution_ref.owner_id == "new-source"
  assert result.realized_t_pnl == result.unrealized_t_pnl == 0
  assert result.entry_blockers == ()
  envelope = result.envelopes[0]
  assert envelope.observed_position_projection.locked_core == 200
  assert envelope.protected_old_position_floor == 200
  assert envelope.planning_replaceable_old_volume_ceiling == 800
  assert result.planning_amount_cap > 0
  assert (
    await read(db)
  ).portfolio_input_fingerprint == result.portfolio_input_fingerprint


@pytest.mark.parametrize(
  "damage,reason",
  [
    ("stale", "SNAPSHOT_STALE_OR_FUTURE"),
    ("no_mark", "CURRENT_MARK_REQUIRED"),
    ("future_mark", "FUTURE_MARK"),
    ("wrong_cycle", "CYCLE_SCOPE_INVALID"),
  ],
)
async def test_incomplete_cuts_do_not_produce_planning_capacity(db, damage, reason):
  changes = {}
  if damage == "stale":
    changes["as_of"] = NOW + timedelta(minutes=2)
  elif damage == "no_mark":
    changes["current_marks"] = {}
  elif damage == "future_mark":
    changes["current_marks"] = {
      CODE: TValuationMark(CODE, Decimal(10), NOW + timedelta(seconds=1), "future")
    }
  else:
    changes["cycle_id"] = "missing"
  with pytest.raises(ValueError, match=reason):
    await read(db, **changes)


async def test_account_disable_blocks_entry_without_erasing_evidence(db):
  async with db.begin():
    control = await db.get(AccountExecutionControl, "account")
    control.authorization_state = "DISABLED"
    control.updated_at = NOW
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(control, "updated_at")
  result = await read(db)
  assert result.planning_amount_cap == 0
  assert "T_ACCOUNT_ENTRY_DISABLED" in result.entry_blockers
  assert result.envelopes[0].observed_position_projection.core == 600


async def test_late_fill_of_stopped_source_enters_account_exposure_and_pnl(db):
  from quantx_infrastructure.models.agent_runtime import TTradeBatch

  from tests.infrastructure.test_live_position_attribution import add_buy

  async with db.begin():
    await add_buy(db)
    db.add(
      TTradeBatch(
        batch_id="batch",
        account_id="account",
        instrument_code=CODE,
        source_execution_owner_type="T_ASSISTANT_EXECUTION",
        source_execution_owner_id="source",
        source_execution_environment="LIVE",
        environment="LIVE",
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
  result = await read(db)
  assert result.current_t_exposure == Decimal("1005.01")
  assert result.unrealized_t_pnl == Decimal("-5.01")
  assert result.active_batch_count == 1
  assert not result.envelopes[0].positive_t_eligible
  assert result.envelopes[0].observed_position_projection.swing == 300


async def test_accepted_entry_cash_is_reserved_once_and_symbol_stays_blocked(db):
  from quantx_infrastructure.models.agent_runtime import PendingTradeOrder, TTradeBatch
  from quantx_infrastructure.services.account_capacity_service import (
    AccountCapacityService,
  )

  async with db.begin():
    db.add(
      TTradeBatch(
        batch_id="pending-batch",
        account_id="account",
        instrument_code=CODE,
        source_execution_owner_type="T_ASSISTANT_EXECUTION",
        source_execution_owner_id="new-source",
        source_execution_environment="LIVE",
        environment="LIVE",
        entry_filled_volume=0,
        exit_filled_volume=0,
        created_at=NOW,
        updated_at=NOW,
      )
    )
    db.add(
      PendingTradeOrder(
        client_order_id="pending",
        user_id="fixture",
        account_id="account",
        owner_type="T_ASSISTANT_EXECUTION",
        owner_id="new-source",
        environment="LIVE",
        instrument_code=CODE,
        side="BUY",
        order_type="LIMIT",
        limit_price="10",
        volume=100,
        status="QUEUED",
        batch_id="pending-batch",
        bucket="swing",
        t_trade_role="ENTRY",
        intent_id="pending-intent",
        created_at=NOW,
        updated_at=NOW,
      )
    )
  result = await read(db)
  async with db.begin():
    control = await db.get(AccountExecutionControl, "account")
    capacity = await AccountCapacityService(db).read(control, instrument_code=CODE)
    assert (
      result.available_cash - result.uncovered_buy_amount == capacity.available_cash
    )
    assert result.available_cash == 10000
  assert result.active_batch_count == 1
  assert not result.envelopes[0].positive_t_eligible


async def test_future_control_state_is_not_used_for_an_earlier_cut(db):
  async with db.begin():
    control = await db.get(AccountExecutionControl, "account")
    control.updated_at = NOW + timedelta(seconds=1)
  with pytest.raises(ValueError, match="FUTURE_CONTROL"):
    await read(db)


async def test_live_market_reader_supplies_the_real_portfolio_cut(db):
  from quantx_infrastructure.services.live_t_market_marks import LiveTMarketMarkReader

  from tests.infrastructure.test_live_t_market_marks import History, hub_at

  history = History()
  reader = LiveTMarketMarkReader(hub_at(NOW), tick_repository=history)
  result = await read(db, market_mark_reader=reader)
  assert result.planning_amount_cap > 0
  assert result.cut.execution_ref.owner_id == "new-source"
  assert result.entry_blockers == ()
  assert history.calls == []  # No filled T batch means no invented overnight mark.
