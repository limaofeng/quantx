from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
from quantx_domain.clock import utcnow
from quantx_domain.trading.exit_plan import (
  ExitExecutionPolicy,
  ExitPlanBook,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleSpec,
  ExitRuleType,
  ExitT1Policy,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  TTradeBatch,
)
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.services import (
  auto_exit_plan_service as auto_exit_module,
)
from quantx_infrastructure.services import (
  exit_plan_authorization_service as authorization_module,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.exit_plan_authorization_service import (
  T_TRADE_ENTRY_APPROVAL_ACTION,
  T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY,
  authorization_expiry_for_challenge,
  bind_t_trade_exit_authorization_to_challenge_payload,
  build_exit_plan_authorization_snapshot,
  derive_exact_auto_exit_authorization_from_t_trade_entry,
  trade_confirmation_payload_fingerprint,
  validate_exact_auto_exit_authorization,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.infrastructure.test_t_assistant_runtime_repository import _version

ACCOUNT_ID = "ACCOUNT-1"
RUN_ID = "11111111-1111-1111-1111-111111111111"
INTENT_ID = "22222222-2222-2222-2222-222222222222"
BATCH_ID = "33333333-3333-3333-3333-333333333333"
PLAN_ID = f"t-exit-{BATCH_ID}"
CHALLENGE_ID = "44444444-4444-4444-4444-444444444444"
INSTRUMENT = "600000.SH"


def _exit_template(*, config_version: int = 3) -> dict:
  return ExitPlanTemplate(
    plan_id=PLAN_ID,
    source_type="T_TRADE_BATCH",
    source_id=BATCH_ID,
    account_id=ACCOUNT_ID,
    instrument_code=INSTRUMENT,
    bucket="swing",
    rules=[
      ExitRuleSpec(
        rule_id=f"{PLAN_ID}:target",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 10.5},
      )
    ],
    strategy_id="t-trade-strategy",
    run_id=RUN_ID,
    config_version=config_version,
    t1_policy=ExitT1Policy.ALLOW_SAME_INSTRUMENT_SUBSTITUTION,
    execution=ExitExecutionPolicy(
      price_reference=ExitPriceReference.BID,
      price_type="FIX_PRICE",
      protected_limit=True,
      max_slippage_bps=30,
      urgency="PROTECTIVE_EXIT",
      execution_mode="AUTO",
    ),
    metadata={
      "t_trade_role": "exit",
      "account_id": ACCOUNT_ID,
      "strategy_run_id": RUN_ID,
      "instrument_code": INSTRUMENT,
      "t_batch_id": BATCH_ID,
      "exit_plan_config_version": config_version,
      "exit_policy_version": "TExitOrderPolicy.v1",
      "t_exit_order_policy_version": "TExitOrderPolicy.v1",
      "t_exit_order_ttl_seconds": 30,
      "t_exit_total_ttl_seconds": 90,
      "t_exit_max_replace_count": 2,
      "t_exit_max_slippage_bps": 30,
    },
    auto_exit_authorized=False,
  ).to_dict()


def _entry_intent(*, filled_volume: int = 100) -> TradeIntentRecord:
  return TradeIntentRecord(
    id=INTENT_ID,
    strategy_run_id=RUN_ID,
    owner_type="STRATEGY_RUN",
    owner_id=RUN_ID,
    environment="LIVE",
    idempotency_key=f"t-entry:{INTENT_ID}",
    account_id=ACCOUNT_ID,
    strategy_id="t-trade-strategy",
    instrument_code=INSTRUMENT,
    direction="BUY",
    bucket="swing",
    reason="T_TRADE_PULLBACK_REBOUND_ENTRY",
    priority="NORMAL",
    target_amount=10_000,
    target_volume=None,
    limit_price_hint=10,
    status="PARTIAL_FILLED",
    executed_price=9.99,
    executed_volume=filled_volume,
    intent_metadata={
      "t_trade_role": "entry",
      "account_id": ACCOUNT_ID,
      "strategy_run_id": RUN_ID,
      "instrument_code": INSTRUMENT,
      "config_version": 3,
      "policy_version": "t-trade-v3",
      "max_price_deviation_bps": 30,
      "requested_entry_amount": 10_000,
      "target_trade_amount": 10_000,
      "t_batch_id": BATCH_ID,
      "exit_plan_id": PLAN_ID,
      "exit_plan_template": _exit_template(),
      "t_trade_entry_approval_challenge_id": CHALLENGE_ID,
    },
  )


def _exit_plan_record(*, protected_volume: int = 100) -> AutoExitPlanRecord:
  plan = ExitPlanBook().register_entry_fill(
    _exit_template(),
    volume=protected_volume,
    price=9.99,
    trade_time=time_utils.now(),
  )
  return AutoExitPlanRecord(
    plan_id=PLAN_ID,
    account_id=ACCOUNT_ID,
    instrument_code=INSTRUMENT,
    bucket="swing",
    source_type="T_TRADE_BATCH",
    source_id=BATCH_ID,
    strategy_run_id=RUN_ID,
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id=RUN_ID,
    source_execution_environment="LIVE",
    enabled=True,
    status="ACTIVE",
    environment="LIVE",
    auto_exit_authorized=False,
    config_version=3,
    state_version=1,
    protected_volume=protected_volume,
    exited_volume=0,
    remaining_volume=protected_volume,
    entry_avg_price=9.99,
    plan_state=plan.to_dict(),
  )


@pytest.fixture
async def authorization_database(monkeypatch: pytest.MonkeyPatch, request):
  monkeypatch.setattr(
    authorization_module.settings,
    "secret_key",
    "test-t-trade-exit-authorization-key-32-bytes",
  )
  monkeypatch.setattr(authorization_module.settings, "algorithm", "HS256")
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AuthUser.__table__,
          AuthUserAccountAccess.__table__,
          AuthDeviceSession.__table__,
          TradeConfirmationChallenge.__table__,
          AccountExecutionControl.__table__,
          Position.__table__,
          AutoExitPlanRecord.__table__,
          AutoExitPlanEvent.__table__,
          PendingTradeOrder.__table__,
          TradeIntentRecord.__table__,
          TTradeBatch.__table__,
          TAssistantExecutionRecord.__table__,
          TAssistantConfigVersionRecord.__table__,
          TTradeGlobalConfig.__table__,
        ],
      )
    )
  factory = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(auto_exit_module, "AsyncSessionLocal", factory)
  now = time_utils.now()
  intent = _entry_intent()
  plan = _exit_plan_record()
  owner_type = getattr(request, "param", "STRATEGY_RUN")
  if owner_type == "T_ASSISTANT_EXECUTION":
    intent.owner_type = owner_type
    intent.strategy_run_id = None
    metadata = dict(intent.intent_metadata)
    metadata.pop("strategy_run_id")
    binding = {
      "source_execution_ref": {"owner_type": owner_type, "owner_id": RUN_ID},
      "candidate_id": "candidate-1", "candidate_fingerprint": "a" * 64,
      "policy_version": "t-trade-v3", "feature_schema_version": 1,
    }
    metadata.update(binding)
    template = metadata["exit_plan_template"]
    template["run_id"] = ""
    template["metadata"].pop("strategy_run_id")
    template["metadata"].pop("account_id")
    template["metadata"].update(binding)
    intent.intent_metadata = metadata
    plan.strategy_run_id = None
    plan.source_execution_owner_type = owner_type
    plan.plan_state = {**plan.plan_state, "template": template}
  challenge_payload = bind_t_trade_exit_authorization_to_challenge_payload(
    {
      "action": T_TRADE_ENTRY_APPROVAL_ACTION,
      "user_id": "user-1",
      "device_session_id": "session-1",
      "account_id": ACCOUNT_ID,
      "business_owner_id": RUN_ID,
      "owner_type": owner_type,
      "owner_id": RUN_ID,
      "environment": "LIVE",
      "intent_id": INTENT_ID,
      "intent_fingerprint": "entry-intent-fingerprint",
    },
    intent,
  )
  async with factory() as db:
    if owner_type == "T_ASSISTANT_EXECUTION":
      db.add(TTradeGlobalConfig(id="config-1", account_id=ACCOUNT_ID, mode="live"))
      version = _version("config-1")
      await TAssistantConfigRepository(db).append_version(version)
      db.add(TAssistantExecutionRecord(
        execution_id=RUN_ID, config_id="config-1", config_version_id=version.config_version_id,
        config_snapshot_hash=version.config_snapshot_hash, frozen_config_version=version.version,
        account_id=ACCOUNT_ID, environment="LIVE", entry_authorization="MANUAL_CONFIRM",
        rollout_stage="CANARY", status="STOPPED", completed_at=now,
        entry_readiness="BLOCKED", entry_readiness_reasons=["SOURCE_STOPPED"],
        entry_readiness_as_of=now, policy_version=version.policy_version,
        feature_schema_version=version.feature_schema_version, scorer_mode="RULE_ONLY",
      ))
    db.add_all(
      [
        AccountExecutionControl(account_id=ACCOUNT_ID),
        AuthUser(
          id="user-1",
          username="operator",
          display_name="Operator",
          password_hash="hash",
          is_active=True,
          permissions=["liquidation:control", "trade:approve"],
        ),
        AuthDeviceSession(
          id="session-1",
          user_id="user-1",
          refresh_token_hash="r" * 64,
          expires_at=utcnow() + timedelta(hours=1),
          revoked_at=None,
          last_used_at=utcnow(),
          device_name="iPhone",
          granted_permissions=["liquidation:control", "trade:approve"],
        ),
        AuthUserAccountAccess(
          user_id="user-1",
          account_id=ACCOUNT_ID,
          is_default=True,
        ),
        Position(
          id="position-1",
          account_id=ACCOUNT_ID,
          account_type="STOCK",
          stock_code=INSTRUMENT,
          instrument_name="浦发银行",
          volume=600,
          can_use_volume=500,
          frozen_volume=0,
          yesterday_volume=500,
          avg_price=10,
          market_value=6000,
          created_at=now,
          updated_at=now,
        ),
        intent,
        plan,
        TTradeBatch(
          batch_id=BATCH_ID,
          account_id=ACCOUNT_ID,
          instrument_code=INSTRUMENT,
          strategy_run_id=RUN_ID if owner_type == "STRATEGY_RUN" else None,
          status="HOLDING",
          entry_intent_id=INTENT_ID,
          target_volume=100,
          entry_filled_volume=100,
          entry_avg_price=9.99,
          source_execution_owner_type=owner_type,
          source_execution_owner_id=RUN_ID,
          source_execution_environment="LIVE",
          environment="LIVE",
          policy_version=3,
        ),
        TradeConfirmationChallenge(
          id=CHALLENGE_ID,
          action=T_TRADE_ENTRY_APPROVAL_ACTION,
          user_id="user-1",
          device_session_id="session-1",
          account_id=ACCOUNT_ID,
          owner_type=owner_type,
          owner_id=RUN_ID,
          environment="LIVE",
          idempotency_key="t-entry-confirmation",
          payload=challenge_payload,
          payload_fingerprint=trade_confirmation_payload_fingerprint(
            challenge_payload
          ),
          token_digest="t" * 64,
          expires_at=now + timedelta(seconds=30),
          consumed_at=now,
          result_reference={"challenge_status": "CONSUMED"},
        ),
      ]
    )
    await db.commit()
  yield factory
  await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("lock_mutable_rows", [False, True])
async def test_live_authorization_snapshot_excludes_paper_protections_and_sells(
  authorization_database,
  lock_mutable_rows: bool,
) -> None:
  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    baseline = await build_exit_plan_authorization_snapshot(
      db, plan, lock_mutable_rows=lock_mutable_rows
    )
    paper_plan = _exit_plan_record(protected_volume=10_000)
    paper_plan.plan_id = "paper-protection"
    paper_plan.source_id = "paper-batch"
    paper_plan.source_execution_environment = "PAPER"
    paper_plan.environment = "PAPER"
    paper_sell = PendingTradeOrder(
      client_order_id="paper-sell",
      user_id="user-1",
      account_id=ACCOUNT_ID,
      owner_type="STRATEGY_RUN",
      owner_id=RUN_ID,
      environment="PAPER",
      strategy_run_id=RUN_ID,
      strategy_order_id="paper-order",
      intent_id=INTENT_ID,
      instrument_code=INSTRUMENT,
      side="SELL",
      order_type="FIX_PRICE",
      limit_price="10",
      volume=10_000,
      status="QUEUED",
    )
    db.add_all([paper_plan, paper_sell])
    await db.flush()
    with_paper = await build_exit_plan_authorization_snapshot(
      db, plan, lock_mutable_rows=lock_mutable_rows
    )
    assert with_paper.fingerprint == baseline.fingerprint
    assert with_paper.subject == baseline.subject
    assert not with_paper.has_pending_sell

    live_plan = _exit_plan_record(protected_volume=100)
    live_plan.plan_id = "other-live-protection"
    live_plan.source_id = "other-live-batch"
    live_sell = PendingTradeOrder(
      client_order_id="live-sell",
      user_id="user-1",
      account_id=ACCOUNT_ID,
      owner_type="STRATEGY_RUN",
      owner_id=RUN_ID,
      environment="LIVE",
      strategy_run_id=RUN_ID,
      strategy_order_id="live-order",
      intent_id=INTENT_ID,
      instrument_code=INSTRUMENT,
      side="SELL",
      order_type="FIX_PRICE",
      limit_price="10",
      volume=100,
      status="QUEUED",
    )
    db.add_all([live_plan, live_sell])
    await db.flush()
    with_live = await build_exit_plan_authorization_snapshot(
      db, plan, lock_mutable_rows=lock_mutable_rows
    )
    assert with_live.fingerprint != baseline.fingerprint
    assert with_live.has_pending_sell
    assert [item["plan_id"] for item in with_live.subject["other_protections"]] == [
      "other-live-protection"
    ]
    assert [item["client_order_id"] for item in with_live.subject["pending_sells"]] == [
      "live-sell"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization_database", ["STRATEGY_RUN", "T_ASSISTANT_EXECUTION"], indirect=True)
async def test_t_entry_confirmation_derives_exact_exit_authorization(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    if plan.source_execution_owner_type == "T_ASSISTANT_EXECUTION":
      challenge = await db.get(TradeConfirmationChallenge, CHALLENGE_ID)
      subject = challenge.payload[T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY]["subject"]
      assert subject["schema_version"] == 2
      assert subject["source_execution_ref"] == {"owner_type": "T_ASSISTANT_EXECUTION", "owner_id": RUN_ID}
      assert "strategy_run_id" not in subject
    result = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db,
      plan,
      entry_intent_id=INTENT_ID,
      challenge_id=CHALLENGE_ID,
      cumulative_filled_volume=100,
    )
    assert result.valid
    assert result.code == "T_TRADE_EXIT_AUTHORIZATION_DERIVED"
    assert result.authorization_user_id == "user-1"
    assert result.config_version == 3
    assert result.challenge_id == CHALLENGE_ID
    assert len(result.fingerprint or "") == 64
    assert plan.auto_exit_authorized
    assert plan.auto_exit_authorization_challenge_id == CHALLENGE_ID
    assert plan.auto_exit_authorization_user_id == "user-1"
    assert plan.auto_exit_authorization_device_session_id == "session-1"
    assert plan.auto_exit_authorization_expires_at == (
      authorization_expiry_for_challenge(
        (await db.get(TradeConfirmationChallenge, CHALLENGE_ID)).expires_at
      )
    )
    await db.commit()

  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    validation = await validate_exact_auto_exit_authorization(db, plan)
    assert validation.valid
    assert validation.code == "AUTHORIZED"
    events = list(
      (
        await db.execute(
          select(AutoExitPlanEvent).where(
            AutoExitPlanEvent.event_type
            == "AUTO_EXIT_AUTHORIZATION_DERIVED_FROM_T_ENTRY"
          )
        )
      ).scalars()
    )
    assert len(events) == 1
    assert events[0].payload["cumulative_filled_volume"] == 100


@pytest.mark.asyncio
async def test_partial_fill_refresh_keeps_original_authorization_expiry(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    first = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db,
      plan,
      entry_intent_id=INTENT_ID,
      challenge_id=CHALLENGE_ID,
      cumulative_filled_volume=100,
    )
    original_expiry = first.authorization_expires_at
    await db.commit()

  async with authorization_database() as db:
    intent = await db.get(TradeIntentRecord, INTENT_ID)
    intent.executed_volume = 200
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    plan.protected_volume = 200
    plan.remaining_volume = 200
    state = ExitPlanBook().register_entry_fill(
      _exit_template(),
      volume=200,
      price=9.99,
      trade_time=time_utils.now(),
    )
    plan.plan_state = state.to_dict()
    refreshed = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db,
      plan,
      entry_intent_id=INTENT_ID,
      challenge_id=CHALLENGE_ID,
      cumulative_filled_volume=200,
      now=time_utils.now() + timedelta(hours=2),
    )
    assert refreshed.valid
    assert refreshed.authorization_expires_at == original_expiry
    assert plan.auto_exit_authorized_at == (
      await db.get(TradeConfirmationChallenge, CHALLENGE_ID)
    ).consumed_at
    await db.commit()

  async with authorization_database() as db:
    assert (
      await db.scalar(select(func.count()).select_from(AutoExitPlanEvent))
      == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["template", "volume", "challenge"])
async def test_t_entry_authorization_fails_closed_on_scope_drift(
  authorization_database,
  drift: str,
) -> None:
  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    if drift == "template":
      state = dict(plan.plan_state)
      template = dict(state["template"])
      template["config_version"] = 4
      state["template"] = template
      plan.plan_state = state
      plan.config_version = 4
    elif drift == "volume":
      intent = await db.get(TradeIntentRecord, INTENT_ID)
      intent.executed_volume = 1100
      plan.protected_volume = 1100
      plan.remaining_volume = 1100
      state = ExitPlanBook().register_entry_fill(
        _exit_template(),
        volume=1100,
        price=9.99,
        trade_time=time_utils.now(),
      )
      plan.plan_state = state.to_dict()
    else:
      challenge = await db.get(TradeConfirmationChallenge, CHALLENGE_ID)
      payload = dict(challenge.payload)
      binding = dict(payload[T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY])
      subject = dict(binding["subject"])
      subject["t_batch_id"] = "tampered-batch"
      binding["subject"] = subject
      payload[T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY] = binding
      challenge.payload = payload

    result = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db,
      plan,
      entry_intent_id=INTENT_ID,
      challenge_id=CHALLENGE_ID,
      cumulative_filled_volume=1100 if drift == "volume" else 100,
    )
    assert not result.valid
    assert not plan.auto_exit_authorized
    assert plan.auto_exit_authorization_fingerprint is None
    assert plan.plan_state["template"]["auto_exit_authorized"] is False


@pytest.mark.asyncio
async def test_unbound_or_external_entry_cannot_mint_exit_authority(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, CHALLENGE_ID)
    payload = dict(challenge.payload)
    payload.pop(T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY)
    challenge.payload = payload
    challenge.payload_fingerprint = trade_confirmation_payload_fingerprint(payload)
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    result = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db,
      plan,
      entry_intent_id=INTENT_ID,
      challenge_id=CHALLENGE_ID,
      cumulative_filled_volume=100,
    )
    assert not result.valid
    assert result.code == "T_TRADE_EXIT_AUTHORIZATION_BINDING_MISSING"
    assert not plan.auto_exit_authorized


@pytest.mark.asyncio
async def test_position_update_rederives_t_exit_authorization_idempotently(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    first = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db,
      plan,
      entry_intent_id=INTENT_ID,
      challenge_id=CHALLENGE_ID,
      cumulative_filled_volume=100,
    )
    assert first.valid
    original_expiry = first.authorization_expires_at
    await db.commit()

  async with authorization_database() as db:
    position = await db.get(Position, "position-1")
    position.volume = 700
    position.market_value = 7000
    await db.commit()

  service = AutoExitPlanService()
  refreshed = await service.rederive_t_trade_exit_authorizations_after_position_update(
    account_id=ACCOUNT_ID,
    instrument_codes=[INSTRUMENT],
  )
  assert refreshed == [
    {
      "plan_id": PLAN_ID,
      "valid": True,
      "code": "T_TRADE_EXIT_AUTHORIZATION_DERIVED",
      "outcome": "DERIVED",
    }
  ]

  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    assert plan.auto_exit_authorized
    assert plan.auto_exit_authorization_expires_at == original_expiry
    event_count = await db.scalar(
      select(func.count())
      .select_from(AutoExitPlanEvent)
      .where(
        AutoExitPlanEvent.event_type
        == "AUTO_EXIT_AUTHORIZATION_DERIVED_FROM_T_ENTRY"
      )
    )
    assert event_count == 2

  replayed = await service.rederive_t_trade_exit_authorizations_after_position_update(
    account_id=ACCOUNT_ID,
    instrument_codes=[INSTRUMENT],
  )
  assert replayed[0]["outcome"] == "DERIVED"
  async with authorization_database() as db:
    assert (
      await db.scalar(
        select(func.count())
        .select_from(AutoExitPlanEvent)
        .where(
          AutoExitPlanEvent.event_type
          == "AUTO_EXIT_AUTHORIZATION_DERIVED_FROM_T_ENTRY"
        )
      )
      == 2
    )


@pytest.mark.asyncio
async def test_position_gap_is_deferred_then_rederived(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    position = await db.get(Position, "position-1")
    await db.delete(position)
    await db.commit()

  service = AutoExitPlanService()
  deferred = await service.rederive_t_trade_exit_authorizations_after_position_update(
    account_id=ACCOUNT_ID,
    instrument_codes=[INSTRUMENT],
  )
  assert deferred[0]["valid"] is False
  assert deferred[0]["outcome"] == "DEFERRED"
  assert deferred[0]["code"] == "T_TRADE_EXIT_SAFETY_SNAPSHOT_UNAVAILABLE"

  async with authorization_database() as db:
    event = await db.scalar(
      select(AutoExitPlanEvent).where(
        AutoExitPlanEvent.event_type
        == "AUTO_EXIT_AUTHORIZATION_DERIVATION_DEFERRED"
      )
    )
    assert event is not None
    assert event.payload["retryable"] is True
    db.add(
      Position(
        id="position-1",
        account_id=ACCOUNT_ID,
        account_type="STOCK",
        stock_code=INSTRUMENT,
        instrument_name="浦发银行",
        volume=600,
        can_use_volume=500,
        frozen_volume=0,
        yesterday_volume=500,
        avg_price=10,
        market_value=6000,
      )
    )
    await db.commit()

  derived = await service.rederive_t_trade_exit_authorizations_after_position_update(
    account_id=ACCOUNT_ID,
    instrument_codes=[INSTRUMENT],
  )
  assert derived[0]["valid"] is True
  assert derived[0]["outcome"] == "DERIVED"
  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    challenge = await db.get(TradeConfirmationChallenge, CHALLENGE_ID)
    assert plan.auto_exit_authorization_expires_at == (
      authorization_expiry_for_challenge(challenge.expires_at)
    )


@pytest.mark.asyncio
async def test_tampered_challenge_rederivation_is_permanently_rejected(
  authorization_database,
) -> None:
  async with authorization_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, CHALLENGE_ID)
    challenge.payload = {**dict(challenge.payload or {}), "intent_id": "tampered"}
    await db.commit()

  rejected = (
    await AutoExitPlanService().rederive_t_trade_exit_authorizations_after_position_update(
      account_id=ACCOUNT_ID,
      instrument_codes=[INSTRUMENT],
    )
  )
  assert rejected[0]["valid"] is False
  assert rejected[0]["outcome"] == "REJECTED"
  assert rejected[0]["code"] == "T_TRADE_ENTRY_CHALLENGE_TAMPERED"
  async with authorization_database() as db:
    event = await db.scalar(
      select(AutoExitPlanEvent).where(
        AutoExitPlanEvent.event_type
        == "AUTO_EXIT_AUTHORIZATION_DERIVATION_REJECTED"
      )
    )
    assert event is not None
    assert event.payload["retryable"] is False


@pytest.mark.parametrize("authorization_database", ["T_ASSISTANT_EXECUTION"], indirect=True)
@pytest.mark.parametrize("drift", ["candidate", "source", "schema"])
async def test_independent_t_confirmation_rejects_bound_material_drift(authorization_database, drift):
  async with authorization_database() as db:
    intent = await db.get(TradeIntentRecord, INTENT_ID)
    challenge = await db.get(TradeConfirmationChallenge, CHALLENGE_ID)
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    if drift == "candidate":
      metadata = deepcopy(intent.intent_metadata)
      metadata["candidate_fingerprint"] = "b" * 64
      intent.intent_metadata = metadata
    else:
      payload = deepcopy(challenge.payload)
      envelope = payload[T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY]
      if drift == "source":
        envelope["subject"]["source_execution_ref"]["owner_id"] = "another-execution"
      else:
        envelope["subject"]["schema_version"] = 1
      envelope["fingerprint"] = authorization_module._sha256_fingerprint(envelope["subject"])
      challenge.payload = payload
      challenge.payload_fingerprint = trade_confirmation_payload_fingerprint(payload)
    result = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db, plan, entry_intent_id=INTENT_ID, challenge_id=CHALLENGE_ID,
      cumulative_filled_volume=100,
    )
    assert not result.valid
    assert not plan.auto_exit_authorized
    assert result.code in {"T_TRADE_ENTRY_INTENT_SCOPE_CHANGED", "T_TRADE_EXIT_AUTHORIZATION_BINDING_INVALID"}


@pytest.mark.parametrize("authorization_database", ["T_ASSISTANT_EXECUTION"], indirect=True)
@pytest.mark.parametrize("damage", [None, "owner", "environment"])
async def test_live_t_public_template_projection_preserves_signed_exit_scope(authorization_database, damage):
  from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef

  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, PLAN_ID)
    template = AutoExitPlanService._execution_plan_template(
      ExitPlanTemplate.from_dict(plan.plan_state["template"]),
      ExecutionOwnerRef("T_ASSISTANT_EXECUTION", RUN_ID), ExecutionEnvironment.LIVE,
    ).to_dict()
    if damage:
      template["metadata"]["source_execution_owner_id" if damage == "owner" else "source_execution_environment"] = "wrong"
    plan.plan_state = {**plan.plan_state, "template": template}
    result = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db, plan, entry_intent_id=INTENT_ID, challenge_id=CHALLENGE_ID, cumulative_filled_volume=100,
    )
    assert result.valid == (damage is None), result
    assert result.code == ("T_TRADE_EXIT_AUTHORIZATION_DERIVED" if damage is None else "T_TRADE_EXIT_PLAN_SCOPE_CHANGED")
