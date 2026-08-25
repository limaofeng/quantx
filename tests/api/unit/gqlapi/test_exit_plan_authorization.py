from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.auth.errors import AuthError
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi import exit_plan_authorization, trade_approval
from quantx_api.gqlapi.exit_plan_authorization import (
  ExitPlanAuthorizationChallengeService,
  normalize_exit_plan_authorization_request,
)
from quantx_api.gqlapi.resolvers.liquidation import LiquidationResolver
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.trade_approval import TradeApprovalChallengeError
from quantx_api.gqlapi.types.liquidation_types import (
  ConditionalLiquidationOrderInput,
  CreateManualExitPlanInput,
  ExitPlanCostBasisInput,
  UpdateManualExitPlanInput,
)
from quantx_domain.clock import utcnow
from quantx_domain.trading.exit_plan import (
  ExitDecision,
  ExitEvaluationContext,
  ExitPlanBook,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRollout,
  AgentDevice,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  TradeCommandOutbox,
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
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import (
  exit_plan_authorization_service as authorization_guard_module,
)
from quantx_infrastructure.services import (
  trade_command_service as trade_command_module,
)
from quantx_infrastructure.services import (
  trade_intent_processor as trade_intent_processor_module,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.engine_command_service import EngineCommandReceipt
from quantx_infrastructure.services.exit_plan_authorization_service import (
  AutoExitAuthorizationGuard,
  validate_exact_auto_exit_authorization,
)
from quantx_infrastructure.services.liquidation_service import (
  LiquidationError,
  LiquidationService,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from quantx_infrastructure.services.trade_intent_processor import (
  TradeIntentProcessor,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _principal(
  *,
  permissions=frozenset({"liquidation:control", "trade:approve"}),
  user_id: str = "user-1",
  session_id: str = "session-1",
  account_id: str = "ACCOUNT-1",
  account_ids: tuple[str, ...] | None = None,
  native_session: bool = True,
) -> Principal:
  return Principal(
    user_id=user_id,
    username="operator",
    display_name="Operator",
    device_session_id=session_id,
    access_token_expires_at=utcnow() + timedelta(minutes=5),
    permissions=frozenset(permissions),
    authorized_account_ids=(account_id,) if account_ids is None else account_ids,
    is_native_session=native_session,
  )


def _request(*, key: str = "exit-plan-auth-1", version: int = 1):
  return normalize_exit_plan_authorization_request(
    account_id="ACCOUNT-1",
    plan_id="plan-1",
    expected_config_version=version,
    idempotency_key=key,
  )


def _plan_state(*, auto_exit_authorized: bool = False) -> dict:
  template = ExitPlanTemplate(
    plan_id="plan-1",
    source_type="MANUAL_POSITION",
    source_id="condition-1",
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    bucket="manual",
    rules=[
      ExitRuleSpec(
        rule_id="plan-1:target",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 12.0},
      )
    ],
    config_version=1,
    auto_exit_authorized=auto_exit_authorized,
  )
  return ExitPlanBook().register_entry_fill(
    template,
    volume=300,
    price=10.0,
  ).to_dict()


@pytest.fixture
async def authorization_database(monkeypatch):
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
          Account.__table__,
          Position.__table__,
          AutoExitPlanRecord.__table__,
          AutoExitPlanEvent.__table__,
          PendingTradeOrder.__table__,
          TradeCommandOutbox.__table__,
          TradeIntentRecord.__table__,
          AccountTradingRollout.__table__,
          AgentDevice.__table__,
          RuntimeComponentHeartbeat.__table__,
        ],
      )
    )
  factory = async_sessionmaker(engine, expire_on_commit=False)
  shanghai_now = time_utils.now()
  async with factory() as db:
    db.add_all(
      [
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
          account_id="ACCOUNT-1",
          is_default=True,
        ),
        Account(
          id="account-row-1",
          account_id="ACCOUNT-1",
          account_type=AccountType.STOCK,
          total_asset=100000,
          cash=50000,
          market_value=50000,
          frozen_cash=0,
          created_at=shanghai_now,
          updated_at=shanghai_now,
        ),
        Position(
          id="position-row-1",
          account_id="ACCOUNT-1",
          account_type=AccountType.STOCK,
          stock_code="600000.SH",
          instrument_name="浦发银行",
          volume=500,
          can_use_volume=400,
          frozen_volume=0,
          yesterday_volume=400,
          avg_price=10,
          market_value=5000,
          created_at=shanghai_now,
          updated_at=shanghai_now,
        ),
        AutoExitPlanRecord(
          plan_id="plan-1",
          account_id="ACCOUNT-1",
          instrument_code="600000.SH",
          bucket="manual",
          source_type="MANUAL_POSITION",
          source_id="condition-1",
          enabled=True,
          status="ACTIVE",
          execution_mode="live",
          auto_exit_authorized=False,
          config_version=1,
          protected_volume=300,
          exited_volume=0,
          remaining_volume=300,
          entry_avg_price=10,
          plan_state=_plan_state(),
          created_at=shanghai_now,
          updated_at=shanghai_now,
        ),
        AccountTradingRollout(
          account_id="ACCOUNT-1",
          stage="CANARY",
          enabled=True,
          kill_switch=False,
          reconcile_status="READY",
          policy_version=2,
          acknowledged_policy_version=2,
          last_snapshot_id="snapshot-1",
          last_snapshot_hash="snapshot-hash-1",
          last_snapshot_at=utcnow(),
          created_at=shanghai_now,
          updated_at=shanghai_now,
        ),
        AgentDevice(
          id="agent-1",
          user_id="user-1",
          name="QMT",
          secret_hash="s" * 64,
          authorized_account_ids=["ACCOUNT-1"],
          capabilities=["live"],
          last_seen_at=utcnow(),
          revoked_at=None,
          created_at=shanghai_now,
          updated_at=shanghai_now,
        ),
        RuntimeComponentHeartbeat(
          component="qmt-agent:agent-1",
          instance_id="qmt-agent",
          status="READY",
          details={"capabilities": ["live"], "protocolVersion": "1.1"},
          updated_at=utcnow(),
        ),
      ]
    )
    await db.commit()

  monkeypatch.setattr(exit_plan_authorization, "AsyncSessionLocal", factory)
  monkeypatch.setattr(authorization_guard_module, "AsyncSessionLocal", factory)
  monkeypatch.setattr(trade_intent_processor_module, "AsyncSessionLocal", factory)
  monkeypatch.setattr(
    trade_approval,
    "settings",
    SimpleNamespace(
      secret_key="test-exit-plan-authorization-key-32-bytes",
      algorithm="HS256",
    ),
  )
  monkeypatch.setattr(trade_command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(trade_command_module.settings, "t_trade_live_enabled", True)
  monkeypatch.setattr(
    trade_command_module.settings,
    "real_trading_account_allowlist",
    ["ACCOUNT-1"],
  )
  yield factory
  await engine.dispose()


async def _mark_exact_intent_pending(
  factory,
  *,
  intent_id: str,
  volume: int = 100,
) -> dict:
  async with factory() as db:
    plan = await db.get(AutoExitPlanRecord, "plan-1")
    state = dict(plan.plan_state or {})
    state.update(
      {
        "status": "EXIT_PENDING",
        "pending_intent_id": intent_id,
        "pending_rule_id": "plan-1:target",
        "pending_requested_volume": volume,
      }
    )
    plan.status = "EXIT_PENDING"
    plan.plan_state = state
    metadata = {
      "account_id": "ACCOUNT-1",
      "exit_plan_id": "plan-1",
      "exit_rule_id": "plan-1:target",
      "exit_policy_version": 1,
      "exact_auto_exit_authorized": True,
      "auto_exit_authorization_code": "AUTHORIZED",
      "auto_exit_authorization_user_id": "user-1",
      "auto_exit_authorization_fingerprint": str(
        plan.auto_exit_authorization_fingerprint
      ),
    }
    db.add(
      TradeIntentRecord(
        id=intent_id,
        strategy_run_id=None,
        owner_type="EXIT_PLAN",
        owner_id="plan-1",
        account_id="ACCOUNT-1",
        strategy_id="exit-plan",
        instrument_code="600000.SH",
        direction="SELL",
        bucket="manual",
        reason="target_reached",
        priority="HIGH",
        target_volume=volume,
        status="PENDING",
        intent_metadata=metadata,
      )
    )
    await db.commit()
    return metadata


@pytest.mark.asyncio
async def test_legacy_boolean_cannot_mint_automatic_exit_authority() -> None:
  create_input = CreateManualExitPlanInput(
    instrument_code="600000.SH",
    protected_volume=100,
    rules=[],
    idempotency_key="legacy-create-1",
    cost_basis=ExitPlanCostBasisInput(
      mode="MANUAL_UNIT_COST", unit_cost_cny=10.0
    ),
    account_id="ACCOUNT-1",
    execution_mode="live",
    auto_exit_authorized=True,
  )
  with pytest.raises(AuthError) as create_error:
    await LiquidationResolver.create_manual_exit_plan(
      create_input,
      "ACCOUNT-1",
    )
  assert create_error.value.code == "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE"

  update_input = UpdateManualExitPlanInput(
    plan_id="plan-1",
    config_version=1,
    rules=[],
    account_id="ACCOUNT-1",
    auto_exit_authorized=True,
  )
  with pytest.raises(AuthError) as update_error:
    await LiquidationResolver.update_manual_exit_plan(
      update_input,
      "ACCOUNT-1",
    )
  assert update_error.value.code == "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE"

  conditional_input = ConditionalLiquidationOrderInput(
    stock_code="600000.SH",
    account_id="ACCOUNT-1",
    target_price=12,
    execution_mode="live",
    auto_exit_authorized=True,
  )
  with pytest.raises(AuthError) as conditional_error:
    await LiquidationResolver.upsert_conditional_liquidation_order(
      conditional_input,
      "ACCOUNT-1",
    )
  assert (
    conditional_error.value.code
    == "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE"
  )

  with pytest.raises(ValueError, match="REQUIRES_CHALLENGE"):
    await AutoExitPlanService().create_manual_exit_plan(
      {"auto_exit_authorized": True}
    )
  with pytest.raises(LiquidationError, match="REQUIRES_CHALLENGE"):
    await LiquidationService(account_id="ACCOUNT-1").upsert_conditional_liquidation_order(
      stock_code="600000.SH",
      target_price=12,
      execution_mode="live",
      auto_exit_authorized=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("field_name", "input_value"),
  [
    (
      "previewExitPlanAuthorization",
      {
        "accountId": "ACCOUNT-1",
        "planId": "plan-1",
        "expectedConfigVersion": 1,
        "idempotencyKey": "preview-error-result",
      },
    ),
    (
      "confirmExitPlanAuthorization",
      {
        "accountId": "ACCOUNT-1",
        "planId": "plan-1",
        "expectedConfigVersion": 1,
        "idempotencyKey": "confirm-error-result",
        "challengeId": "challenge-1",
        "confirmationToken": "token-1",
      },
    ),
  ],
)
async def test_exit_plan_authorization_errors_return_typed_results(
  monkeypatch: pytest.MonkeyPatch,
  field_name: str,
  input_value: dict,
) -> None:
  method_name = "issue" if field_name.startswith("preview") else "confirm"
  monkeypatch.setattr(
    ExitPlanAuthorizationChallengeService,
    method_name,
    AsyncMock(
      side_effect=TradeApprovalChallengeError(
        "AUTHORIZATION_NOT_READY",
        "退出计划授权条件尚未满足",
      )
    ),
  )
  input_type = (
    "ExitPlanAuthorizationPreviewInput!"
    if method_name == "issue"
    else "ExitPlanAuthorizationConfirmationInput!"
  )

  result = await schema.execute(
    f"""
    mutation Authorization($input: {input_type}) {{
      {field_name}(input: $input) {{ success code message }}
    }}
    """,
    variable_values={"input": input_value},
    context_value={"principal": _principal(), "request_id": "authorization-error"},
  )

  assert result.errors is None
  assert result.data == {
    field_name: {
      "success": False,
      "code": "AUTHORIZATION_NOT_READY",
      "message": "退出计划授权条件尚未满足",
    }
  }


@pytest.mark.asyncio
async def test_manual_plan_create_uses_caller_idempotency_key(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = AsyncMock(
    return_value=EngineCommandReceipt(
      message_id="command-1",
      command_type="EXIT_PLAN_CREATE_MANUAL",
      aggregate_id="ACCOUNT-1:600000.SH",
      status="SUCCEEDED",
      result={"plan_id": "plan-1", "config_version": 1},
    )
  )
  monkeypatch.setattr(
    "quantx_api.gqlapi.resolvers.liquidation.engine_command_service.request",
    request,
  )
  monkeypatch.setattr(
    LiquidationResolver,
    "_load_exit_plan",
    AsyncMock(return_value=SimpleNamespace()),
  )
  monkeypatch.setattr(
    "quantx_api.gqlapi.resolvers.liquidation.ExitPlanView.from_model",
    lambda _record: "plan-view",
  )
  create_input = CreateManualExitPlanInput(
    instrument_code="600000.SH",
    protected_volume=100,
    rules=[],
    idempotency_key="ios-create-1",
    cost_basis=ExitPlanCostBasisInput(
      mode="MANUAL_UNIT_COST", unit_cost_cny=10.0
    ),
    account_id="ACCOUNT-1",
  )

  result = await LiquidationResolver.create_manual_exit_plan(
    create_input,
    "ACCOUNT-1",
  )

  assert result == "plan-view"
  command_key = request.await_args.kwargs["idempotency_key"]
  assert command_key.startswith("exit-plan-create:")
  assert len(command_key) == len("exit-plan-create:") + 64


@pytest.mark.asyncio
async def test_manual_plan_create_rejects_blank_idempotency_key() -> None:
  create_input = CreateManualExitPlanInput(
    instrument_code="600000.SH",
    protected_volume=100,
    rules=[],
    idempotency_key="   ",
    cost_basis=ExitPlanCostBasisInput(
      mode="MANUAL_UNIT_COST", unit_cost_cny=10.0
    ),
    account_id="ACCOUNT-1",
  )

  with pytest.raises(ValueError, match="幂等键不能为空"):
    await LiquidationResolver.create_manual_exit_plan(
      create_input,
      "ACCOUNT-1",
    )


@pytest.mark.asyncio
async def test_exact_authorization_is_device_bound_audited_and_idempotent(
  authorization_database,
):
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(),
  )
  assert preview.plan_binding["execution_mode"] == "live"
  assert preview.plan_binding["protected_volume"] == 300
  assert preview.plan_binding["remaining_volume"] == 300
  assert preview.safety_subject["position"]["t1_unavailable_volume"] == 100
  assert preview.readiness["protocol_version"] == "1.1"
  assert preview.authorization_expires_at > preview.challenge_expires_at

  async with authorization_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert preview.confirmation_token not in str(challenge.payload)
    assert preview.confirmation_token not in challenge.token_digest

  confirmed = await ExitPlanAuthorizationChallengeService.confirm(
    principal=_principal(),
    request=_request(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert confirmed.plan_id == "plan-1"
  assert confirmed.config_version == 1

  async with authorization_database() as db:
    record = await db.get(AutoExitPlanRecord, "plan-1")
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    events = list((await db.execute(select(AutoExitPlanEvent))).scalars().all())
    assert record.auto_exit_authorized
    assert record.auto_exit_authorization_config_version == 1
    assert record.auto_exit_authorization_challenge_id == preview.challenge_id
    assert record.auto_exit_authorization_device_session_id == "session-1"
    assert len(record.auto_exit_authorization_fingerprint) == 64
    assert record.plan_state["template"]["auto_exit_authorized"] is True
    assert challenge.consumed_at is not None
    assert len(events) == 1
    assert events[0].event_type == "AUTO_EXIT_AUTHORIZED"
    assert events[0].payload["actor_user_id"] == "user-1"
    assert events[0].payload["device_session_id"] == "session-1"
    assert preview.confirmation_token not in str(events[0].payload)
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 0

  replay = await ExitPlanAuthorizationChallengeService.confirm(
    principal=_principal(),
    request=_request(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert replay == confirmed
  async with authorization_database() as db:
    assert await db.scalar(select(func.count()).select_from(AutoExitPlanEvent)) == 1


@pytest.mark.asyncio
async def test_browser_session_can_grant_exact_authorization(
  authorization_database,
):
  async with authorization_database() as db:
    session = await db.get(AuthDeviceSession, "session-1")
    session.device_name = "QuantX Web"
    session.granted_permissions = None
    await db.commit()

  principal = _principal(native_session=False)
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=principal,
    request=_request(key="browser-session"),
  )
  confirmed = await ExitPlanAuthorizationChallengeService.confirm(
    principal=principal,
    request=_request(key="browser-session"),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )

  assert confirmed.plan_id == "plan-1"
  async with authorization_database() as db:
    record = await db.get(AutoExitPlanRecord, "plan-1")
    assert record.auto_exit_authorized
    assert record.auto_exit_authorization_device_session_id == "session-1"
    validation = await validate_exact_auto_exit_authorization(db, record)
    assert validation.valid
    assert validation.code == "AUTHORIZED"


@pytest.mark.asyncio
async def test_authorization_rejects_a_session_with_multiple_accounts(
  authorization_database,
):
  with pytest.raises(TradeApprovalChallengeError) as error:
    await ExitPlanAuthorizationChallengeService.issue(
      principal=_principal(
        account_ids=("ACCOUNT-1", "ACCOUNT-2"),
        native_session=False,
      ),
      request=_request(key="multiple-accounts"),
    )

  assert error.value.code == "SINGLE_ACCOUNT_SESSION_REQUIRED"


@pytest.mark.asyncio
async def test_confirmation_rejects_cross_session_tampering_and_expiry(
  authorization_database,
):
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key="cross-session"),
  )
  with pytest.raises(TradeApprovalChallengeError) as cross_session:
    await ExitPlanAuthorizationChallengeService.confirm(
      principal=_principal(session_id="other-session"),
      request=_request(key="cross-session"),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert cross_session.value.code == "CONFIRMATION_CONTEXT_MISMATCH"

  with pytest.raises(TradeApprovalChallengeError) as cross_account:
    await ExitPlanAuthorizationChallengeService.confirm(
      principal=_principal(account_id="ACCOUNT-2"),
      request=_request(key="cross-session"),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert cross_account.value.code == "SINGLE_ACCOUNT_SESSION_REQUIRED"

  with pytest.raises(TradeApprovalChallengeError) as altered_request:
    await ExitPlanAuthorizationChallengeService.confirm(
      principal=_principal(),
      request=_request(key="different-key"),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert altered_request.value.code == "CONFIRMATION_CONTEXT_MISMATCH"

  async with authorization_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    payload = dict(challenge.payload)
    payload["authorization_fingerprint"] = "0" * 64
    challenge.payload = payload
    await db.commit()
  with pytest.raises(TradeApprovalChallengeError) as tampered:
    await ExitPlanAuthorizationChallengeService.confirm(
      principal=_principal(),
      request=_request(key="cross-session"),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert tampered.value.code == "TRADE_PAYLOAD_CHANGED"

  expired = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key="expired-challenge"),
  )
  async with authorization_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, expired.challenge_id)
    challenge.expires_at = time_utils.now() - timedelta(seconds=1)
    await db.commit()
  with pytest.raises(TradeApprovalChallengeError) as expired_error:
    await ExitPlanAuthorizationChallengeService.confirm(
      principal=_principal(),
      request=_request(key="expired-challenge"),
      challenge_id=expired.challenge_id,
      confirmation_token=expired.confirmation_token,
    )
  assert expired_error.value.code == "CONFIRMATION_EXPIRED"


@pytest.mark.asyncio
async def test_confirmation_rechecks_permissions_account_plan_and_readiness(
  authorization_database,
):
  permission_preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key="permission-revoked"),
  )
  async with authorization_database() as db:
    user = await db.get(AuthUser, "user-1")
    user.permissions = ["liquidation:control"]
    await db.commit()
  with pytest.raises(TradeApprovalChallengeError) as permission_error:
    await ExitPlanAuthorizationChallengeService.confirm(
      principal=_principal(),
      request=_request(key="permission-revoked"),
      challenge_id=permission_preview.challenge_id,
      confirmation_token=permission_preview.confirmation_token,
    )
  assert permission_error.value.code == "FORBIDDEN"

  async with authorization_database() as db:
    user = await db.get(AuthUser, "user-1")
    user.permissions = ["liquidation:control", "trade:approve"]
    await db.commit()
  account_preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key="account-revoked"),
  )
  async with authorization_database() as db:
    await db.execute(
      delete(AuthUserAccountAccess).where(
        AuthUserAccountAccess.user_id == "user-1",
        AuthUserAccountAccess.account_id == "ACCOUNT-1",
      )
    )
    await db.commit()
  with pytest.raises(TradeApprovalChallengeError) as account_error:
    await ExitPlanAuthorizationChallengeService.confirm(
      principal=_principal(),
      request=_request(key="account-revoked"),
      challenge_id=account_preview.challenge_id,
      confirmation_token=account_preview.confirmation_token,
    )
  assert account_error.value.code in {"FORBIDDEN", "UNAUTHENTICATED"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("change", "code"),
  [
    ("plan", "CONFIG_VERSION_CONFLICT"),
    ("position", "EXIT_PLAN_AUTHORIZATION_SCOPE_CHANGED"),
    ("readiness", "READINESS_CHANGED"),
  ],
)
async def test_confirmation_fails_closed_when_bound_safety_facts_change(
  authorization_database,
  change,
  code,
):
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key=f"change-{change}"),
  )
  async with authorization_database() as db:
    if change == "plan":
      plan = await db.get(AutoExitPlanRecord, "plan-1")
      plan.config_version = 2
    elif change == "position":
      position = await db.get(Position, "position-row-1")
      position.can_use_volume = 300
    else:
      rollout = await db.get(AccountTradingRollout, "ACCOUNT-1")
      rollout.last_snapshot_hash = "snapshot-hash-2"
    await db.commit()
  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await ExitPlanAuthorizationChallengeService.confirm(
      principal=_principal(),
      request=_request(key=f"change-{change}"),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert rejected.value.code == code
  async with authorization_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert challenge.consumed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("switch_name", "error_pattern"),
  [
    ("enable_real_trading", "真实交易总开关|真实交易或做 T 实盘开关"),
    ("t_trade_live_enabled", "真实交易或做 T 实盘开关"),
  ],
)
async def test_live_preview_and_atomic_route_fail_when_real_switch_is_off(
  authorization_database,
  monkeypatch,
  switch_name,
  error_pattern,
):
  monkeypatch.setattr(trade_command_module.settings, switch_name, False)
  with pytest.raises(TradeApprovalChallengeError) as preview_error:
    await ExitPlanAuthorizationChallengeService.issue(
      principal=_principal(),
      request=_request(key=f"preview-{switch_name}-off"),
    )
  assert preview_error.value.code == "LIVE_AUTHORIZATION_REJECTED"

  monkeypatch.setattr(trade_command_module.settings, switch_name, True)
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key=f"atomic-{switch_name}-off"),
  )
  await ExitPlanAuthorizationChallengeService.confirm(
    principal=_principal(),
    request=_request(key=f"atomic-{switch_name}-off"),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  metadata = await _mark_exact_intent_pending(
    authorization_database,
    intent_id=f"intent-{switch_name}-off",
  )
  monkeypatch.setattr(trade_command_module.settings, switch_name, False)
  async with authorization_database() as db:
    service = TradeCommandService(db)
    with pytest.raises(AgentUnavailableError, match=error_pattern):
      await service.enqueue_order_for_account(
        account_id="ACCOUNT-1",
        instrument_code="600000.SH",
        side="SELL",
        order_type="FIX_PRICE",
        limit_price=10,
        volume=100,
        execution_mode="live",
        intent_id=f"intent-{switch_name}-off",
        policy_version=1,
        request_metadata=metadata,
        require_risk_reducing_live_authorization=True,
        authorization_user_id="user-1",
      )


@pytest.mark.asyncio
async def test_atomic_route_revalidates_exact_plan_and_queues_once(
  authorization_database,
):
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key="atomic-success"),
  )
  await ExitPlanAuthorizationChallengeService.confirm(
    principal=_principal(),
    request=_request(key="atomic-success"),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  metadata = await _mark_exact_intent_pending(
    authorization_database,
    intent_id="intent-atomic-success",
  )

  async with authorization_database() as db:
    queued = await TradeCommandService(db).enqueue_order_for_account(
      account_id="ACCOUNT-1",
      instrument_code="600000.SH",
      side="SELL",
      order_type="FIX_PRICE",
      limit_price=10,
      volume=100,
      intent_id="intent-atomic-success",
      idempotency_key="exact-plan-order-1",
      execution_mode="live",
      policy_version=1,
      request_metadata=metadata,
      require_risk_reducing_live_authorization=True,
      authorization_user_id="user-1",
    )
  assert queued.status == "QUEUED"
  async with authorization_database() as db:
    pending = list((await db.execute(select(PendingTradeOrder))).scalars())
    outbox = list((await db.execute(select(TradeCommandOutbox))).scalars())
    assert len(pending) == len(outbox) == 1
    assert pending[0].intent_id == "intent-atomic-success"
    assert outbox[0].payload["request_metadata"]["exit_plan_id"] == "plan-1"


@pytest.mark.asyncio
async def test_atomic_route_rejects_scope_drift_after_engine_guard(
  authorization_database,
):
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key="atomic-scope-drift"),
  )
  await ExitPlanAuthorizationChallengeService.confirm(
    principal=_principal(),
    request=_request(key="atomic-scope-drift"),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  metadata = await _mark_exact_intent_pending(
    authorization_database,
    intent_id="intent-atomic-drift",
  )
  async with authorization_database() as db:
    position = await db.get(Position, "position-row-1")
    position.can_use_volume = 300
    await db.commit()

  async with authorization_database() as db:
    with pytest.raises(AgentUnavailableError, match="授权已失效"):
      await TradeCommandService(db).enqueue_order_for_account(
        account_id="ACCOUNT-1",
        instrument_code="600000.SH",
        side="SELL",
        order_type="FIX_PRICE",
        limit_price=10,
        volume=100,
        intent_id="intent-atomic-drift",
        idempotency_key="exact-plan-order-drift",
        execution_mode="live",
        policy_version=1,
        request_metadata=metadata,
        require_risk_reducing_live_authorization=True,
        authorization_user_id="user-1",
      )
  async with authorization_database() as db:
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 0
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0


@pytest.mark.asyncio
async def test_engine_invalidates_changed_scope_and_returns_to_manual_approval(
  authorization_database,
):
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key="engine-scope-change"),
  )
  await ExitPlanAuthorizationChallengeService.confirm(
    principal=_principal(),
    request=_request(key="engine-scope-change"),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, "plan-1")
    detached_plan = plan
    position = await db.get(Position, "position-row-1")
    position.volume = 400
    position.can_use_volume = 300
    await db.commit()

  validation = await AutoExitAuthorizationGuard.validate_or_invalidate("plan-1")
  assert not validation.valid
  assert validation.code == "AUTO_EXIT_AUTHORIZATION_SCOPE_CHANGED"
  async with authorization_database() as db:
    stored = await db.get(AutoExitPlanRecord, "plan-1")
    assert stored.enabled
    assert stored.status == "ACTIVE"
    assert not stored.auto_exit_authorized
    assert stored.plan_state["template"]["auto_exit_authorized"] is False

  # A stale Engine projection that still carries the old boolean is also
  # forced through the guard and produces an approval task, never an order.
  detached_plan.auto_exit_authorized = True
  result = await TradeIntentProcessor().process_exit_decision(
    plan=detached_plan,
    decision=ExitDecision(
      plan_id="plan-1",
      rule_id="plan-1:target",
      rule_type="TARGET_PRICE",
      reason="target_reached",
      volume=100,
      priority=500,
    ),
    intent_id="intent-after-scope-change",
    context=ExitEvaluationContext(
      timestamp=time_utils.now(),
      current_price=12.0,
      bid_price=11.99,
      price_tick=0.01,
      limit_up=13.2,
      limit_down=10.8,
    ),
    position=None,
    limit_price=11.99,
  )
  assert result["awaiting_approval"] is True
  async with authorization_database() as db:
    intent = await db.get(TradeIntentRecord, "intent-after-scope-change")
    assert intent.status == "AWAITING_APPROVAL"
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("revocation", "expected_code"),
  [
    ("expiry", "AUTO_EXIT_AUTHORIZATION_EXPIRED"),
    ("session", "AUTO_EXIT_AUTHORIZATION_REVOKED"),
    ("permission", "AUTO_EXIT_AUTHORIZATION_REVOKED"),
  ],
)
async def test_engine_revokes_expired_or_withdrawn_authorization_without_disabling_plan(
  authorization_database,
  revocation,
  expected_code,
):
  preview = await ExitPlanAuthorizationChallengeService.issue(
    principal=_principal(),
    request=_request(key=f"engine-revocation-{revocation}"),
  )
  await ExitPlanAuthorizationChallengeService.confirm(
    principal=_principal(),
    request=_request(key=f"engine-revocation-{revocation}"),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  async with authorization_database() as db:
    if revocation == "expiry":
      plan = await db.get(AutoExitPlanRecord, "plan-1")
      plan.auto_exit_authorization_expires_at = time_utils.now() - timedelta(
        seconds=1
      )
    elif revocation == "session":
      session = await db.get(AuthDeviceSession, "session-1")
      session.revoked_at = utcnow()
    else:
      user = await db.get(AuthUser, "user-1")
      user.permissions = ["liquidation:control"]
    await db.commit()

  validation = await AutoExitAuthorizationGuard.validate_or_invalidate("plan-1")
  assert validation.code == expected_code
  assert not validation.valid
  async with authorization_database() as db:
    plan = await db.get(AutoExitPlanRecord, "plan-1")
    assert plan.enabled
    assert plan.status == "ACTIVE"
    assert not plan.auto_exit_authorized
    assert plan.auto_exit_authorization_fingerprint is None
    invalidations = list(
      (
        await db.execute(
          select(AutoExitPlanEvent).where(
            AutoExitPlanEvent.event_type
            == "AUTO_EXIT_AUTHORIZATION_INVALIDATED"
          )
        )
      ).scalars()
    )
    assert len(invalidations) == 1
    assert invalidations[0].payload["reason_code"] == expected_code
