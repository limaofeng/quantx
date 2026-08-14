from datetime import timedelta
from types import SimpleNamespace

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi import liquidation_approval, trade_approval
from quantx_api.gqlapi.liquidation_approval import (
  LiquidationChallengeService,
  normalize_liquidation_request,
)
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.trade_approval import TradeApprovalChallengeError
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  EngineCommandOutbox,
  PendingTradeOrder,
)
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services.engine_command_service import EngineCommandReceipt
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _principal(
  *,
  permissions=frozenset({"liquidation:control", "trade:approve"}),
  device_session_id: str = "liquidation-session-1",
) -> Principal:
  return Principal(
    user_id="liquidation-user-1",
    username="operator",
    display_name="Operator",
    device_session_id=device_session_id,
    access_token_expires_at=utcnow() + timedelta(minutes=5),
    permissions=frozenset(permissions),
    authorized_account_ids=("ACCOUNT-1",),
    active_account_id="ACCOUNT-1",
  )


def _request(
  *,
  key: str = "liquidation-preview-1",
  scope: str = "SELECTED",
  codes=("600000.SH",),
  completion: str = "AVAILABLE_NOW",
  conflict: str = "UNALLOCATED_ONLY",
  mode: str = "PAPER",
):
  return normalize_liquidation_request(
    account_id="ACCOUNT-1",
    scope=scope,
    instrument_codes=codes,
    completion_strategy=completion,
    conflict_strategy=conflict,
    execution_mode=mode,
    idempotency_key=key,
  )


@pytest.fixture
async def liquidation_database(monkeypatch):
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
          EngineCommandOutbox.__table__,
          Account.__table__,
          Position.__table__,
          AutoExitPlanRecord.__table__,
          PendingTradeOrder.__table__,
        ],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AuthUser(
        id="liquidation-user-1",
        username="operator",
        display_name="Operator",
        password_hash="hash",
        is_active=True,
        permissions=["liquidation:control", "trade:approve"],
      )
    )
    db.add(
      AuthDeviceSession(
        id="liquidation-session-1",
        user_id="liquidation-user-1",
        refresh_token_hash="r" * 64,
        expires_at=utcnow() + timedelta(hours=1),
        revoked_at=None,
        last_used_at=utcnow(),
        device_name="iPhone",
        granted_permissions=["liquidation:control", "trade:approve"],
        active_account_id="ACCOUNT-1",
      )
    )
    db.add(
      AuthUserAccountAccess(
        user_id="liquidation-user-1",
        account_id="ACCOUNT-1",
        is_default=True,
      )
    )
    db.add(
      Account(
        id="account-row-1",
        account_id="ACCOUNT-1",
        account_type=AccountType.STOCK,
        total_asset=100000,
        cash=50000,
        market_value=50000,
        frozen_cash=0,
      )
    )
    db.add(
      Position(
        id="position-row-1",
        account_id="ACCOUNT-1",
        account_type=AccountType.STOCK,
        stock_code="600000.SH",
        instrument_name="浦发银行",
        volume=500,
        can_use_volume=300,
        frozen_volume=100,
        yesterday_volume=400,
        avg_price=10,
        market_value=5000,
      )
    )
    await db.commit()
  monkeypatch.setattr(liquidation_approval, "AsyncSessionLocal", session_factory)
  monkeypatch.setattr(
    trade_approval,
    "settings",
    SimpleNamespace(
      secret_key="test-liquidation-signing-key-at-least-32-bytes",
      algorithm="HS256",
    ),
  )
  yield session_factory
  await engine.dispose()


@pytest.mark.asyncio
async def test_paper_challenge_binds_snapshot_and_queues_engine_command_once(
  liquidation_database,
  monkeypatch,
):
  preview = await LiquidationChallengeService.issue(
    principal=_principal(),
    request=_request(),
  )
  item = preview.snapshot.items[0]
  assert preview.request.execution_mode == "PAPER"
  assert item.total_volume == 500
  assert item.available_volume == 300
  assert item.t1_unavailable_volume == 100
  assert item.max_protected_volume == 300
  assert item.included

  async with liquidation_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert challenge.payload["execution_mode"] == "PAPER"
    assert challenge.payload["snapshot"]["items"][0]["max_protected_volume"] == 300
    assert preview.confirmation_token not in str(challenge.payload)
    assert preview.confirmation_token not in challenge.token_digest

  confirmed = await LiquidationChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert confirmed.group_id == preview.group_id
  assert confirmed.status == "PENDING"

  async with liquidation_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    command = await db.get(EngineCommandOutbox, confirmed.command_id)
    assert challenge.consumed_at is not None
    assert challenge.result_reference == {
      "group_id": preview.group_id,
      "command_id": confirmed.command_id,
      "status": "PENDING",
    }
    assert command.command_type == "EXIT_PLAN_LIQUIDATE_POSITIONS"
    assert command.payload["execution_mode"] == "paper"
    assert command.payload["scope"] == "SELECTED"
    assert command.payload["requested_scope"] == "SELECTED"
    assert command.payload["instrument_codes"] == ["600000.SH"]
    assert command.payload["expected_items"][0]["max_protected_volume"] == 300
    assert command.payload["auto_exit_authorized"] is True

  async def receipt(command_id):
    assert command_id == confirmed.command_id
    return EngineCommandReceipt(
      message_id=command_id,
      command_type="EXIT_PLAN_LIQUIDATE_POSITIONS",
      aggregate_id=f"ACCOUNT-1:{preview.group_id}",
      status="PENDING",
    )

  monkeypatch.setattr(liquidation_approval.engine_command_service, "get", receipt)
  replay = await LiquidationChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token,
  )
  assert replay == confirmed
  async with liquidation_database() as db:
    assert await db.scalar(select(func.count()).select_from(EngineCommandOutbox)) == 1


@pytest.mark.asyncio
async def test_graphql_preview_exposes_strong_paper_snapshot_contract(
  liquidation_database,
):
  result = await schema.execute(
    """
    mutation Preview($input: LiquidationPreviewInput!) {
      previewLiquidation(input: $input) {
        success
        code
        preview {
          accountId
          scope
          executionMode
          snapshotVersion
          includedCount
          skippedCount
          items {
            instrumentCode
            totalVolume
            availableVolume
            t1UnavailableVolume
            maxProtectedVolume
            included
            reasonCode
          }
        }
      }
    }
    """,
    variable_values={
      "input": {
        "accountId": "ACCOUNT-1",
        "scope": "SINGLE",
        "instrumentCodes": ["600000.SH"],
        "completionStrategy": "AVAILABLE_NOW",
        "conflictStrategy": "UNALLOCATED_ONLY",
        "idempotencyKey": "graphql-liquidation-preview-1",
      }
    },
    context_value={
      # mutation:write keeps this source-level test independent from the
      # separately integrated root permission map; the service itself still
      # requires liquidation:control.
      "principal": _principal(
        permissions={"liquidation:control", "mutation:write"}
      ),
      "request_id": "graphql-liquidation-preview",
    },
  )

  assert result.errors is None
  payload = result.data["previewLiquidation"]
  assert payload["success"]
  assert payload["code"] == "PREVIEW_READY"
  assert payload["preview"]["scope"] == "SINGLE"
  assert payload["preview"]["executionMode"] == "PAPER"
  assert len(payload["preview"]["snapshotVersion"]) == 64
  assert payload["preview"]["includedCount"] == 1
  assert payload["preview"]["skippedCount"] == 0
  assert payload["preview"]["items"] == [
    {
      "instrumentCode": "600000.SH",
      "totalVolume": 500,
      "availableVolume": 300,
      "t1UnavailableVolume": 100,
      "maxProtectedVolume": 300,
      "included": True,
      "reasonCode": "INCLUDED",
    }
  ]


@pytest.mark.asyncio
async def test_liquidation_snapshot_change_rejects_without_consuming_challenge(
  liquidation_database,
):
  preview = await LiquidationChallengeService.issue(
    principal=_principal(),
    request=_request(key="liquidation-drift-1"),
  )
  async with liquidation_database() as db:
    position = await db.get(Position, "position-row-1")
    position.can_use_volume = 200
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await LiquidationChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert rejected.value.code == "LIQUIDATION_SNAPSHOT_CHANGED"
  async with liquidation_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert challenge.consumed_at is None
    assert await db.scalar(select(func.count()).select_from(EngineCommandOutbox)) == 0


@pytest.mark.asyncio
async def test_confirmation_rechecks_trade_approve_permission(
  liquidation_database,
):
  preview = await LiquidationChallengeService.issue(
    principal=_principal(),
    request=_request(key="liquidation-revoke-1"),
  )
  async with liquidation_database() as db:
    user = await db.get(AuthUser, "liquidation-user-1")
    user.permissions = ["liquidation:control"]
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await LiquidationChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert rejected.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_confirmation_rechecks_and_locks_account_membership(
  liquidation_database,
):
  preview = await LiquidationChallengeService.issue(
    principal=_principal(),
    request=_request(key="liquidation-account-revoke-1"),
  )
  async with liquidation_database() as db:
    await db.execute(
      delete(AuthUserAccountAccess).where(
        AuthUserAccountAccess.user_id == "liquidation-user-1",
        AuthUserAccountAccess.account_id == "ACCOUNT-1",
      )
    )
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await LiquidationChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert rejected.value.code == "UNAUTHENTICATED"
  async with liquidation_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    assert challenge.consumed_at is None
    assert await db.scalar(select(func.count()).select_from(EngineCommandOutbox)) == 0


@pytest.mark.asyncio
async def test_challenge_rejects_cross_device_and_signed_set_tampering(
  liquidation_database,
):
  preview = await LiquidationChallengeService.issue(
    principal=_principal(),
    request=_request(key="liquidation-tamper-1"),
  )
  with pytest.raises(TradeApprovalChallengeError) as device_rejected:
    await LiquidationChallengeService.confirm(
      principal=_principal(device_session_id="different-session"),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert device_rejected.value.code == "CONFIRMATION_CONTEXT_MISMATCH"

  async with liquidation_database() as db:
    challenge = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    payload = dict(challenge.payload)
    payload["snapshot"] = {
      **dict(payload["snapshot"]),
      "items": [
        {
          **dict(payload["snapshot"]["items"][0]),
          "max_protected_volume": 500,
        }
      ],
    }
    challenge.payload = payload
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as tamper_rejected:
    await LiquidationChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
    )
  assert tamper_rejected.value.code == "TRADE_PAYLOAD_CHANGED"


@pytest.mark.asyncio
async def test_live_preview_is_fail_closed_while_paper_remains_available(
  liquidation_database,
  monkeypatch,
):
  from quantx_infrastructure.services import trade_command_service

  monkeypatch.setattr(trade_command_service.settings, "enable_real_trading", False)
  monkeypatch.setattr(trade_command_service.settings, "t_trade_live_enabled", False)
  paper = await LiquidationChallengeService.issue(
    principal=_principal(),
    request=_request(key="liquidation-paper-off-1", mode="PAPER"),
  )
  assert paper.request.execution_mode == "PAPER"

  with pytest.raises(TradeApprovalChallengeError) as live_rejected:
    await LiquidationChallengeService.issue(
      principal=_principal(),
      request=_request(key="liquidation-live-off-1", mode="LIVE"),
    )
  assert live_rejected.value.code == "LIVE_AUTHORIZATION_REJECTED"


@pytest.mark.asyncio
async def test_selected_preview_lists_pending_sell_as_partial_skip(
  liquidation_database,
):
  async with liquidation_database() as db:
    db.add(
      Position(
        id="position-row-2",
        account_id="ACCOUNT-1",
        account_type=AccountType.STOCK,
        stock_code="000001.SZ",
        instrument_name="平安银行",
        volume=200,
        can_use_volume=200,
        frozen_volume=0,
        yesterday_volume=200,
        avg_price=12,
        market_value=2400,
      )
    )
    db.add(
      PendingTradeOrder(
        client_order_id="pending-sell-1",
        user_id="liquidation-user-1",
        account_id="ACCOUNT-1",
        instrument_code="600000.SH",
        side="SELL",
        order_type="FIX_PRICE",
        limit_price="10",
        volume=100,
        status="QUEUED",
        execution_mode="paper",
        bucket="manual",
        request_metadata={},
      )
    )
    await db.commit()

  preview = await LiquidationChallengeService.issue(
    principal=_principal(),
    request=_request(
      key="liquidation-partial-1",
      codes=("600000.SH", "000001.SZ"),
      completion="UNTIL_SNAPSHOT_CLEARED",
    ),
  )
  items = {item.instrument_code: item for item in preview.snapshot.items}
  assert items["000001.SZ"].included
  assert items["000001.SZ"].max_protected_volume == 200
  assert not items["600000.SH"].included
  assert items["600000.SH"].pending_sell_volume == 100
  assert items["600000.SH"].reason_code == "PENDING_SELL_CONFLICT"


@pytest.mark.parametrize(
  ("scope", "codes", "code"),
  [
    ("SINGLE", (), "SINGLE_INSTRUMENT_REQUIRED"),
    ("SINGLE", ("600000.SH", "000001.SZ"), "SINGLE_INSTRUMENT_REQUIRED"),
    ("SELECTED", (), "SELECTED_INSTRUMENTS_REQUIRED"),
    ("ALL", ("600000.SH",), "ALL_SCOPE_FORBIDS_INSTRUMENTS"),
  ],
)
def test_scope_contract_is_strongly_validated(scope, codes, code):
  with pytest.raises(TradeApprovalChallengeError) as rejected:
    _request(scope=scope, codes=codes)
  assert rejected.value.code == code
