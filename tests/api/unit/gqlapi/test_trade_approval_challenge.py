from datetime import timedelta
from types import SimpleNamespace

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi import trade_approval
from quantx_api.gqlapi.trade_approval import (
  T_TRADE_ENTRY_APPROVAL,
  TradeApprovalChallengeError,
  TradeApprovalChallengeService,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord


class _Result:
  def __init__(self, record):
    self.record = record

  def scalar_one_or_none(self):
    return self.record


class _Database:
  def __init__(self, record):
    self.record = record
    self.commits = 0

  async def execute(self, _statement):
    return _Result(self.record)

  async def commit(self):
    self.commits += 1


def _record(*, ttl_ms: int = 60_000) -> TradeIntentRecord:
  now = time_utils.now()
  return TradeIntentRecord(
    id="intent-1",
    strategy_run_id="run-1",
    strategy_id="strategy-1",
    instrument_code="600000.SH",
    direction="BUY",
    bucket="swing",
    reason="T_TRADE_PULLBACK_REBOUND_ENTRY",
    priority="NORMAL",
    confidence=0.9,
    target_amount=None,
    target_position_pct=None,
    target_volume=100,
    limit_price_hint=10.5,
    status="AWAITING_APPROVAL",
    intent_metadata={
      "approval_ttl_ms": ttl_ms,
      "intent_created_at": now.isoformat(),
      "signal": {"signal_price": 10.5},
    },
    created_at=now,
    updated_at=now,
  )


def _principal(*, device_session_id: str = "device-session-1") -> Principal:
  return Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id=device_session_id,
    access_token_expires_at=time_utils.now() + timedelta(minutes=5),
    permissions=frozenset({"trade:approve"}),
    authorized_account_ids=("ACCOUNT-1",),
  )


@pytest.fixture
def configured_challenge_service(monkeypatch):
  record = _record()
  database = _Database(record)

  async def database_factory():
    yield database

  monkeypatch.setattr(trade_approval, "get_async_db", database_factory)
  monkeypatch.setattr(
    trade_approval,
    "settings",
    SimpleNamespace(
      secret_key="test-trade-approval-signing-key-at-least-32-bytes",
      algorithm="HS256",
    ),
  )
  return record, database


@pytest.mark.asyncio
async def test_challenge_is_device_bound_and_consumed_once(
  configured_challenge_service,
):
  record, database = configured_challenge_service
  preview = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )

  challenge = record.intent_metadata["mobile_trade_approval_challenge_v1"]
  assert preview.confirmation_token not in str(record.intent_metadata)
  assert challenge["device_session_id"] == "device-session-1"
  assert preview.target_volume == 100
  assert preview.estimated_amount == 1050.0

  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
  )
  assert challenge_id == preview.challenge_id
  assert database.commits == 2

  with pytest.raises(TradeApprovalChallengeError, match="已使用") as replay:
    await TradeApprovalChallengeService.consume(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      run_id="run-1",
      intent_id="intent-1",
      confirmation_token=preview.confirmation_token,
    )
  assert replay.value.code == "CONFIRMATION_ALREADY_USED"


@pytest.mark.asyncio
async def test_challenge_rejects_another_authenticated_device(
  configured_challenge_service,
):
  preview = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )

  with pytest.raises(TradeApprovalChallengeError) as mismatch:
    await TradeApprovalChallengeService.consume(
      principal=_principal(device_session_id="device-session-2"),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      run_id="run-1",
      intent_id="intent-1",
      confirmation_token=preview.confirmation_token,
    )
  assert mismatch.value.code == "CONFIRMATION_CONTEXT_MISMATCH"


@pytest.mark.asyncio
async def test_challenge_fails_closed_when_intent_changes(
  configured_challenge_service,
):
  record, _database = configured_challenge_service
  preview = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )
  record.target_volume = 200

  with pytest.raises(TradeApprovalChallengeError) as changed:
    await TradeApprovalChallengeService.consume(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      run_id="run-1",
      intent_id="intent-1",
      confirmation_token=preview.confirmation_token,
    )
  assert changed.value.code == "INTENT_CHANGED"
