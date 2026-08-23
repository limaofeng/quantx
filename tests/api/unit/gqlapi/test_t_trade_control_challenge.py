import asyncio
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi import t_trade_control, trade_approval
from quantx_api.gqlapi.t_trade_control import (
  TradeApprovalChallengeError,
  TTradeControlChallengeService,
  normalize_t_trade_control_request,
)
from quantx_api.gqlapi.types.t_trade_types import (
  TTradeControlAction,
  TTradeRolloutTarget,
)
from quantx_domain.clock import utcnow
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRollout,
  AccountTradingRolloutEvent,
)
from quantx_infrastructure.services.t_trade_rollout_evidence_service import (
  V3_ROLLOUT_GATE_CODES,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_PREPARATION_CODES = {
  "SERVER_REAL_TRADING_ENABLED",
  "T_TRADE_LIVE_ENABLED",
  "ACCOUNT_ALLOWLISTED",
  "ENGINE_READY",
  "LIVE_AGENT_READY",
  "AGENT_MODE_LIVE",
  "PROTOCOL_1_1",
  "ROLLOUT_CONFIGURED",
  "SNAPSHOT_RECONCILED",
  "SNAPSHOT_FRESH",
  "SNAPSHOT_ACTIVITY_CLASSIFIED",
  "RECENT_BACKUP",
  "NO_CRITICAL_ALERTS",
  "NO_DEAD_LETTERS",
  "KILL_SWITCH_CLEAR",
}
_ACTIVATION_CODES = _PREPARATION_CODES | {
  "CONTROLLED_WINDOW_ACTIVE",
  "NO_EXTERNAL_BROKER_ACTIVITY",
} | V3_ROLLOUT_GATE_CODES


def _readiness(
  *,
  controlled_window_active: bool = False,
  failed_codes: set[str] | None = None,
) -> dict:
  failed = failed_codes or set()
  checked_at = time_utils.now()
  return {
    "account_id": "ACCOUNT-1",
    "ready": not failed,
    "status": "READY" if not failed else "BLOCKED",
    "preparation_ready": not failed,
    "automation_ready": controlled_window_active and not failed,
    "stage": "SHADOW",
    "engine_status": "READY",
    "agent_status": "READY",
    "agent_device_id": "agent-1",
    "ready_live_agent_count": 1,
    "agent_mode": "live",
    "protocol_version": "1.1",
    "reconcile_status": "READY",
    "kill_switch": False,
    "policy_version": 3,
    "snapshot_id": "snapshot-1",
    "snapshot_hash": "a" * 64,
    "snapshot_at": checked_at,
    "controlled_window_active": controlled_window_active,
    "controlled_window_snapshot_id": ("snapshot-1" if controlled_window_active else ""),
    "new_external_order_count": 0,
    "new_external_trade_count": 0,
    "working_external_order_count": 0,
    "queued_command_count": 0,
    "dead_letter_count": 0,
    "unresolved_critical_alert_count": 0,
    "v3_rollout_evidence": {
      "schema_version": 1,
      "fingerprint": "v3-evidence-fingerprint-1",
    },
    "checks": [
      {
        "code": code,
        "passed": code not in failed
        and (code != "CONTROLLED_WINDOW_ACTIVE" or controlled_window_active),
        "message": f"{code} blocked" if code in failed else "ok",
        "scope": "AUTOMATION",
      }
      for code in sorted(_ACTIVATION_CODES)
    ],
  }


class FakeOperations:
  def __init__(self, *readiness_results: dict):
    self.readiness_results = [
      deepcopy(item) for item in (readiness_results or (_readiness(),))
    ]
    self.readiness_calls = 0
    self.begin_calls: list[dict] = []
    self.activate_calls: list[dict] = []
    self.kill_calls: list[dict] = []
    self.begin_started: asyncio.Event | None = None
    self.begin_release: asyncio.Event | None = None
    self.forbid_readiness = False

  async def readiness(self, account_id: str) -> dict:
    assert account_id == "ACCOUNT-1"
    if self.forbid_readiness:
      raise AssertionError("kill switch must bypass ordinary readiness")
    index = min(self.readiness_calls, len(self.readiness_results) - 1)
    self.readiness_calls += 1
    return deepcopy(self.readiness_results[index])

  async def begin_controlled_window(self, account_id: str, **kwargs) -> dict:
    self.begin_calls.append({"account_id": account_id, **kwargs})
    if self.begin_started is not None:
      self.begin_started.set()
    if self.begin_release is not None:
      await self.begin_release.wait()
    return _readiness(controlled_window_active=True)

  async def activate_rollout(self, account_id: str, **kwargs) -> dict:
    self.activate_calls.append({"account_id": account_id, **kwargs})
    return _readiness(controlled_window_active=True)

  async def kill(self, account_id: str, reason: str, **kwargs) -> dict:
    self.kill_calls.append({"account_id": account_id, "reason": reason, **kwargs})
    return {
      **_readiness(),
      "status": "HARD_KILL",
      "kill_switch": True,
      "stage": "KILL_SWITCHED",
    }


def _principal(
  *,
  device_session_id: str = "device-session-1",
  permissions: frozenset[str] | None = None,
  account_id: str = "ACCOUNT-1",
  native_session: bool = True,
) -> Principal:
  return Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id=device_session_id,
    access_token_expires_at=utcnow() + timedelta(minutes=5),
    permissions=(
      permissions
      if permissions is not None
      else frozenset({"t-trade:control", "trade:approve"})
    ),
    authorized_account_ids=(account_id,),
    is_native_session=native_session,
  )


def _request(
  *,
  action: TTradeControlAction = TTradeControlAction.BEGIN_CONTROLLED_WINDOW,
  key: str = "ios-t-control-1",
  policy_version: int = 3,
  reason: str = "",
):
  target = {
    TTradeControlAction.ACTIVATE_CANARY: TTradeRolloutTarget.CANARY,
    TTradeControlAction.ACTIVATE_LIVE: TTradeRolloutTarget.LIVE,
  }.get(action)
  return normalize_t_trade_control_request(
    account_id="ACCOUNT-1",
    action=action,
    policy_version=policy_version,
    snapshot_id=("" if action == TTradeControlAction.KILL_SWITCH else "snapshot-1"),
    target_stage=target,
    reason=reason,
    idempotency_key=key,
  )


@pytest.fixture
async def control_database(monkeypatch, tmp_path):
  engine = create_async_engine(
    f"sqlite+aiosqlite:///{tmp_path / 't-trade-control.sqlite3'}"
  )
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AuthUser.__table__,
          AuthUserAccountAccess.__table__,
          AuthDeviceSession.__table__,
          AccountTradingRollout.__table__,
          AccountTradingRolloutEvent.__table__,
          TradeConfirmationChallenge.__table__,
        ],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="operator",
        display_name="Operator",
        password_hash="hash",
        is_active=True,
        permissions=[
          "mutation:write",
          "t-trade:control",
          "trade:approve",
        ],
      )
    )
    db.add_all(
      [
        AuthUserAccountAccess(
          user_id="user-1",
          account_id="ACCOUNT-1",
          is_default=True,
        ),
      ]
    )
    for session_id in ("device-session-1", "device-session-2"):
      db.add(
        AuthDeviceSession(
          id=session_id,
          user_id="user-1",
          refresh_token_hash=("1" if session_id.endswith("1") else "2") * 64,
          expires_at=utcnow() + timedelta(hours=1),
          revoked_at=None,
          last_used_at=utcnow(),
          device_name="iPhone",
          granted_permissions=["t-trade:control", "trade:approve"],
        )
      )
    db.add(
      AuthDeviceSession(
        id="web-session",
        user_id="user-1",
        refresh_token_hash="w" * 64,
        expires_at=utcnow() + timedelta(hours=1),
        revoked_at=None,
        last_used_at=utcnow(),
        device_name="Web",
        granted_permissions=None,
      )
    )
    db.add(
      AccountTradingRollout(
        account_id="ACCOUNT-1",
        stage="SHADOW",
        enabled=False,
        kill_switch=False,
        reconcile_status="READY",
        policy_version=3,
        acknowledged_policy_version=0,
        last_snapshot_id="snapshot-1",
        last_snapshot_hash="a" * 64,
        last_snapshot_at=utcnow(),
        last_backup_at=utcnow(),
        controlled_window_active=False,
        controlled_window_snapshot_id=None,
        controlled_window_snapshot_hash=None,
      )
    )
    await db.commit()
  monkeypatch.setattr(t_trade_control, "AsyncSessionLocal", session_factory)
  monkeypatch.setattr(
    trade_approval,
    "settings",
    SimpleNamespace(
      secret_key="test-t-trade-control-signing-key-at-least-32-bytes",
      algorithm="HS256",
    ),
  )
  yield session_factory
  await engine.dispose()


def _use_operations(monkeypatch, *readiness_results: dict) -> FakeOperations:
  operations = FakeOperations(*readiness_results)
  monkeypatch.setattr(
    TTradeControlChallengeService,
    "operations_service",
    operations,
  )
  return operations


async def _expire_dispatch_lease(session_factory, challenge_id: str) -> None:
  async with session_factory() as db:
    challenge = await db.get(TradeConfirmationChallenge, challenge_id)
    challenge.consumed_at = time_utils.now() - timedelta(seconds=40)
    challenge.result_reference = {
      "challenge_status": "CONSUMED",
      "operation_status": "DISPATCHING",
      "operation_code": "T_TRADE_CONTROL_DISPATCHING",
      "message": "dispatch interrupted",
      "dispatch_lease_id": "old-lease",
      "dispatch_attempt": 1,
      "dispatch_started_at": (time_utils.now() - timedelta(seconds=40)).isoformat(),
      "dispatch_lease_expires_at": (
        time_utils.now() - timedelta(seconds=10)
      ).isoformat(),
    }
    await db.commit()


async def _mark_controlled_window(session_factory) -> None:
  async with session_factory() as db:
    rollout = await db.get(AccountTradingRollout, "ACCOUNT-1")
    assert rollout is not None
    rollout.controlled_window_active = True
    rollout.controlled_window_snapshot_id = "snapshot-1"
    rollout.controlled_window_snapshot_hash = "a" * 64
    await db.commit()


def test_graphql_contract_uses_strong_control_action_and_public_names() -> None:
  schema_source = (
    Path(__file__).resolve().parents[4]
    / "apps/api/src/quantx_api/gqlapi/schemas/t_trade_schema.py"
  ).read_text(encoding="utf-8")
  assert 'name="previewTTradeControl"' in schema_source
  assert 'name="confirmTTradeControl"' in schema_source
  assert {item.value for item in TTradeControlAction} == {
    "BEGIN_CONTROLLED_WINDOW",
    "ACTIVATE_CANARY",
    "ACTIVATE_LIVE",
    "KILL_SWITCH",
  }


def test_action_and_target_stage_cannot_be_mixed() -> None:
  with pytest.raises(TradeApprovalChallengeError) as rejected:
    normalize_t_trade_control_request(
      account_id="ACCOUNT-1",
      action=TTradeControlAction.ACTIVATE_CANARY,
      policy_version=3,
      snapshot_id="snapshot-1",
      target_stage=TTradeRolloutTarget.LIVE,
      reason="activate",
      idempotency_key="target-tamper-1",
    )
  assert rejected.value.code == "CONTROL_ACTION_TARGET_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "permissions",
  [
    frozenset({"t-trade:control"}),
    frozenset({"trade:approve"}),
    frozenset({"mutation:write"}),
  ],
)
async def test_native_control_requires_both_specific_scopes(
  control_database,
  monkeypatch,
  permissions,
) -> None:
  operations = _use_operations(monkeypatch)
  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await TTradeControlChallengeService.issue(
      principal=_principal(permissions=permissions),
      request=_request(),
    )
  assert rejected.value.code == "FORBIDDEN"
  assert operations.readiness_calls == 0


@pytest.mark.asyncio
async def test_control_requires_native_unique_primary_account(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  with pytest.raises(TradeApprovalChallengeError) as web_rejected:
    await TTradeControlChallengeService.issue(
      principal=_principal(
        device_session_id="web-session",
        native_session=False,
      ),
      request=_request(),
    )
  assert web_rejected.value.code == "NATIVE_SESSION_ACCOUNT_REQUIRED"

  with pytest.raises(TradeApprovalChallengeError) as account_rejected:
    await TTradeControlChallengeService.issue(
      principal=_principal(account_id="ACCOUNT-2"),
      request=_request(),
    )
  assert account_rejected.value.code == "FORBIDDEN"
  assert operations.readiness_calls == 0


@pytest.mark.asyncio
async def test_preview_token_is_returned_once_and_never_persisted(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  first = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(),
  )
  second = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(),
  )

  assert first.token_issued is True
  assert first.confirmation_token
  assert second.challenge_id == first.challenge_id
  assert second.token_issued is False
  assert second.confirmation_token is None
  assert first.confirmation_token not in repr(first)
  assert operations.readiness_calls == 1
  async with control_database() as db:
    stored = await db.get(TradeConfirmationChallenge, first.challenge_id)
    assert first.confirmation_token not in str(stored.payload)
    assert first.confirmation_token not in stored.token_digest
    assert (
      await db.scalar(select(func.count()).select_from(TradeConfirmationChallenge)) == 1
    )

  conflicting = _request(
    action=TTradeControlAction.KILL_SWITCH,
    reason="emergency",
  )
  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await TTradeControlChallengeService.issue(
      principal=_principal(),
      request=conflicting,
    )
  assert rejected.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_integrity_recovery_rejects_an_expired_pending_preview(
  control_database,
  monkeypatch,
) -> None:
  _use_operations(monkeypatch)
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="expired-idempotency-1"),
  )
  async with control_database() as db:
    stored = await db.get(TradeConfirmationChallenge, preview.challenge_id)
    stored.expires_at = time_utils.now() - timedelta(seconds=1)
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await TTradeControlChallengeService._recover_idempotent_preview(
      principal=_principal(),
      request=_request(key="expired-idempotency-1"),
    )
  assert rejected.value.code == "IDEMPOTENCY_KEY_ALREADY_USED"


@pytest.mark.asyncio
async def test_confirmation_rejects_expiry_tamper_and_other_session(
  control_database,
  monkeypatch,
) -> None:
  _use_operations(monkeypatch)
  session_preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="session-bound-1"),
  )
  with pytest.raises(TradeApprovalChallengeError) as wrong_session:
    await TTradeControlChallengeService.confirm(
      principal=_principal(device_session_id="device-session-2"),
      challenge_id=session_preview.challenge_id,
      confirmation_token=session_preview.confirmation_token or "",
    )
  assert wrong_session.value.code == "CONFIRMATION_CONTEXT_MISMATCH"

  tampered = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="payload-tamper-1"),
  )
  async with control_database() as db:
    stored = await db.get(TradeConfirmationChallenge, tampered.challenge_id)
    stored.payload = {
      **stored.payload,
      "request_binding": {
        **stored.payload["request_binding"],
        "reason": "tampered",
      },
    }
    await db.commit()
  with pytest.raises(TradeApprovalChallengeError) as payload_changed:
    await TTradeControlChallengeService.confirm(
      principal=_principal(),
      challenge_id=tampered.challenge_id,
      confirmation_token=tampered.confirmation_token or "",
    )
  assert payload_changed.value.code == "TRADE_PAYLOAD_CHANGED"

  expired = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="expired-confirm-1"),
  )
  async with control_database() as db:
    stored = await db.get(TradeConfirmationChallenge, expired.challenge_id)
    stored.expires_at = time_utils.now() - timedelta(seconds=1)
    await db.commit()
  with pytest.raises(TradeApprovalChallengeError) as challenge_expired:
    await TTradeControlChallengeService.confirm(
      principal=_principal(),
      challenge_id=expired.challenge_id,
      confirmation_token=expired.confirmation_token or "",
    )
  assert challenge_expired.value.code == "CONFIRMATION_EXPIRED"


@pytest.mark.asyncio
async def test_confirmation_revalidates_persisted_session_scopes(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="scope-recheck-1"),
  )
  async with control_database() as db:
    session = await db.get(AuthDeviceSession, "device-session-1")
    session.granted_permissions = ["trade:approve"]
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await TTradeControlChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token or "",
    )
  assert rejected.value.code == "FORBIDDEN"
  assert operations.begin_calls == []


@pytest.mark.asyncio
async def test_confirmation_is_one_time_and_idempotently_recovers_result(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="one-time-1"),
  )
  first = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )
  replay = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )

  assert first.operation_status == "APPLIED"
  assert first.challenge_consumed is True
  assert replay == first
  assert len(operations.begin_calls) == 1
  assert operations.begin_calls[0]["operation_id"] == preview.challenge_id


@pytest.mark.asyncio
async def test_gate_change_consumes_challenge_but_rejects_operation(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(
    monkeypatch,
    _readiness(),
    _readiness(failed_codes={"SNAPSHOT_FRESH"}),
  )
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="gate-change-1"),
  )
  result = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )

  assert result.challenge_consumed is True
  assert result.operation_status == "REJECTED"
  assert result.operation_code == "T_TRADE_CONTROL_NOT_READY"
  assert operations.begin_calls == []


@pytest.mark.asyncio
async def test_canary_confirmation_can_atomically_record_pending_v3_operator_review(
  control_database,
  monkeypatch,
) -> None:
  await _mark_controlled_window(control_database)
  pending_review = _readiness(
    controlled_window_active=True,
    failed_codes={"V3_OPERATOR_REVIEW_CONFIRMED"},
  )
  operations = _use_operations(
    monkeypatch,
    pending_review,
    pending_review,
  )
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(
      action=TTradeControlAction.ACTIVATE_CANARY,
      key="canary-v3-review-required-1",
    ),
  )

  result = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )

  assert result.challenge_consumed is True
  assert result.operation_status == "APPLIED", (result.operation_code, result.message)
  assert result.operation_code == "CANARY_ACTIVATION_APPLIED"
  assert operations.activate_calls[0]["operation_id"] == preview.challenge_id


@pytest.mark.asyncio
async def test_canary_review_path_cannot_bypass_another_v3_gate(
  control_database,
  monkeypatch,
) -> None:
  await _mark_controlled_window(control_database)
  operations = _use_operations(
    monkeypatch,
    _readiness(
      controlled_window_active=True,
      failed_codes={"V3_REPLAY_20_TRADING_DAYS"},
    ),
  )

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await TTradeControlChallengeService.issue(
      principal=_principal(),
      request=_request(
        action=TTradeControlAction.ACTIVATE_CANARY,
        key="canary-v3-replay-required-1",
      ),
    )

  assert rejected.value.code == "T_TRADE_CONTROL_NOT_READY"
  assert operations.activate_calls == []


@pytest.mark.asyncio
async def test_canary_confirmation_rejects_changed_v3_evidence_binding(
  control_database,
  monkeypatch,
) -> None:
  await _mark_controlled_window(control_database)
  changed = _readiness(controlled_window_active=True)
  changed["v3_rollout_evidence"]["fingerprint"] = "v3-evidence-fingerprint-2"
  operations = _use_operations(
    monkeypatch,
    _readiness(controlled_window_active=True),
    changed,
  )
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(
      action=TTradeControlAction.ACTIVATE_CANARY,
      key="canary-v3-evidence-binding-1",
    ),
  )

  result = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )

  assert result.challenge_consumed is True
  assert result.operation_status == "REJECTED"
  assert result.operation_code == "READINESS_CHANGED"
  assert operations.activate_calls == []


@pytest.mark.asyncio
async def test_real_switch_changes_fail_closed_with_testing_switches_off(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(
    monkeypatch,
    _readiness(),
    _readiness(
      failed_codes={
        "SERVER_REAL_TRADING_ENABLED",
        "T_TRADE_LIVE_ENABLED",
        "ACCOUNT_ALLOWLISTED",
      }
    ),
  )
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="real-switch-off-1"),
  )
  result = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )

  assert result.operation_status == "REJECTED"
  assert result.challenge_consumed is True
  assert operations.begin_calls == []


@pytest.mark.asyncio
async def test_kill_bypasses_readiness_and_policy_changes_but_reconfirms_each_time(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  operations.forbid_readiness = True

  first = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(
      action=TTradeControlAction.KILL_SWITCH,
      key="kill-1",
      policy_version=1,
      reason="broker state uncertain",
    ),
  )
  async with control_database() as db:
    rollout = await db.get(AccountTradingRollout, "ACCOUNT-1")
    rollout.policy_version = 99
    rollout.last_snapshot_id = "changed-after-preview"
    rollout.reconcile_status = "RECONCILE_REQUIRED"
    await db.commit()
  first_result = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=first.challenge_id,
    confirmation_token=first.confirmation_token or "",
  )

  second = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(
      action=TTradeControlAction.KILL_SWITCH,
      key="kill-2",
      policy_version=2,
      reason="repeat risk reduction",
    ),
  )
  second_result = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=second.challenge_id,
    confirmation_token=second.confirmation_token or "",
  )

  assert first_result.operation_status == "APPLIED"
  assert second_result.operation_status == "APPLIED"
  assert operations.readiness_calls == 0
  assert [item["reason"] for item in operations.kill_calls] == [
    "broker state uncertain",
    "repeat risk reduction",
  ]


@pytest.mark.asyncio
async def test_stale_dispatch_recovers_crash_before_apply(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="crash-before-apply-1"),
  )
  await _expire_dispatch_lease(control_database, preview.challenge_id)

  result = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )

  assert result.operation_status == "APPLIED"
  assert len(operations.begin_calls) == 1
  assert operations.begin_calls[0]["operation_id"] == preview.challenge_id


@pytest.mark.asyncio
async def test_event_marker_recovers_crash_after_apply_without_repeating_operation(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="crash-after-apply-1"),
  )
  await _expire_dispatch_lease(control_database, preview.challenge_id)
  async with control_database() as db:
    db.add(
      AccountTradingRolloutEvent(
        event_id=preview.challenge_id,
        account_id="ACCOUNT-1",
        event_type="CONTROLLED_WINDOW_STARTED",
        actor_user_id="user-1",
        previous_stage="SHADOW",
        next_stage="SHADOW",
        snapshot_id="snapshot-1",
        details={"operationId": preview.challenge_id, "policyVersion": 3},
        created_at=utcnow(),
      )
    )
    await db.commit()

  result = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )

  assert result.operation_status == "APPLIED"
  assert result.operation_code == "CONTROLLED_WINDOW_APPLIED"
  assert operations.begin_calls == []


@pytest.mark.asyncio
async def test_event_marker_with_wrong_operation_binding_is_rejected(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="marker-conflict-1"),
  )
  await _expire_dispatch_lease(control_database, preview.challenge_id)
  async with control_database() as db:
    db.add(
      AccountTradingRolloutEvent(
        event_id=preview.challenge_id,
        account_id="ACCOUNT-1",
        event_type="CONTROLLED_WINDOW_STARTED",
        actor_user_id="user-1",
        previous_stage="SHADOW",
        next_stage="SHADOW",
        snapshot_id="snapshot-1",
        details={"operationId": "different-challenge"},
        created_at=utcnow(),
      )
    )
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as conflict:
    await TTradeControlChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token or "",
    )
  assert conflict.value.code == "CONTROL_OPERATION_MARKER_CONFLICT"
  assert operations.begin_calls == []


@pytest.mark.asyncio
async def test_begin_marker_with_same_snapshot_but_different_policy_is_rejected(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="marker-policy-conflict-1"),
  )
  await _expire_dispatch_lease(control_database, preview.challenge_id)
  async with control_database() as db:
    db.add(
      AccountTradingRolloutEvent(
        event_id=preview.challenge_id,
        account_id="ACCOUNT-1",
        event_type="CONTROLLED_WINDOW_STARTED",
        actor_user_id="user-1",
        previous_stage="SHADOW",
        next_stage="SHADOW",
        snapshot_id="snapshot-1",
        details={
          "operationId": preview.challenge_id,
          "policyVersion": 4,
        },
        created_at=utcnow(),
      )
    )
    await db.commit()

  with pytest.raises(TradeApprovalChallengeError) as conflict:
    await TTradeControlChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token or "",
    )
  assert conflict.value.code == "CONTROL_OPERATION_MARKER_CONFLICT"
  assert operations.begin_calls == []


@pytest.mark.asyncio
async def test_concurrent_confirmation_uses_fresh_lease_and_applies_once(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  operations.begin_started = asyncio.Event()
  operations.begin_release = asyncio.Event()
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="concurrent-confirm-1"),
  )

  first_task = asyncio.create_task(
    TTradeControlChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token or "",
    )
  )
  await asyncio.wait_for(operations.begin_started.wait(), timeout=2)
  concurrent = await TTradeControlChallengeService.confirm(
    principal=_principal(),
    challenge_id=preview.challenge_id,
    confirmation_token=preview.confirmation_token or "",
  )
  operations.begin_release.set()
  first = await asyncio.wait_for(first_task, timeout=2)

  assert concurrent.challenge_consumed is True
  assert concurrent.operation_status == "DISPATCHING"
  assert first.operation_status == "APPLIED"
  assert len(operations.begin_calls) == 1


@pytest.mark.asyncio
async def test_preview_and_confirmation_share_session_then_challenge_lock_order(
  control_database,
  monkeypatch,
) -> None:
  operations = _use_operations(monkeypatch)
  preview = await TTradeControlChallengeService.issue(
    principal=_principal(),
    request=_request(key="preview-confirm-lock-order-1"),
  )

  original_lock = TTradeControlChallengeService._lock_current_principal
  barrier = asyncio.Barrier(2)

  async def synchronized_lock(cls, db, principal, account_id):
    current = await original_lock(db, principal, account_id)
    await barrier.wait()
    return current

  monkeypatch.setattr(
    TTradeControlChallengeService,
    "_lock_current_principal",
    classmethod(synchronized_lock),
  )

  confirmation_task = asyncio.create_task(
    TTradeControlChallengeService.confirm(
      principal=_principal(),
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token or "",
    )
  )
  preview_task = asyncio.create_task(
    TTradeControlChallengeService.issue(
      principal=_principal(),
      request=_request(key="preview-confirm-lock-order-1"),
    )
  )
  confirmation, replayed_preview = await asyncio.wait_for(
    asyncio.gather(confirmation_task, preview_task),
    timeout=2,
  )

  assert confirmation.challenge_consumed is True
  assert replayed_preview.challenge_id == preview.challenge_id
  assert replayed_preview.confirmation_token is None
  assert len(operations.begin_calls) == 1
