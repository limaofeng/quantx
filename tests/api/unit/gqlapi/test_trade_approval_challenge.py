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
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord


class _Result:
  def __init__(self, record):
    self.record = record

  def scalar_one_or_none(self):
    return self.record


class _RowsResult:
  def __init__(self, rows):
    self.rows = rows

  def scalars(self):
    return self

  def all(self):
    return list(self.rows)


class _Database:
  def __init__(self, record):
    self.record = record
    self.commits = 0
    self.commands = []
    self.challenges = []
    self.fail_commit_once = False
    self._committed_challenges = {}
    self._committed_command_count = 0

  async def execute(self, statement):
    entity = statement.column_descriptions[0].get("entity")
    if entity is TradeConfirmationChallenge:
      return _RowsResult(self.challenges)
    return _Result(self.record)

  async def commit(self):
    if self.fail_commit_once:
      self.fail_commit_once = False
      self.commands = self.commands[: self._committed_command_count]
      committed_ids = set(self._committed_challenges)
      self.challenges[:] = [
        challenge
        for challenge in self.challenges
        if challenge.id in committed_ids
      ]
      for challenge in self.challenges:
        consumed_at, result_reference = self._committed_challenges[challenge.id]
        challenge.consumed_at = consumed_at
        challenge.result_reference = result_reference
      raise RuntimeError("injected commit failure")
    self.commits += 1
    self._committed_command_count = len(self.commands)
    self._committed_challenges = {
      challenge.id: (challenge.consumed_at, challenge.result_reference)
      for challenge in self.challenges
    }

  def add(self, value):
    if isinstance(value, TradeConfirmationChallenge):
      self.challenges.append(value)
    else:
      self.commands.append(value)

  async def flush(self):
    return None

  async def scalar(self, statement):
    entity = statement.column_descriptions[0].get("entity")
    if entity is EngineCommandOutbox:
      key = statement.whereclause.right.value
      field = statement.whereclause.left.name
      return next(
        (item for item in self.commands if getattr(item, field) == key),
        None,
      )
    return None


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


def _approval_command_kwargs(
  *,
  command_key: str = "command-test-approval",
  payload: dict | None = None,
) -> dict:
  return {
    "command_type": "T_TRADE_APPROVE_ENTRY",
    "command_aggregate_id": "run-1",
    "command_idempotency_key": command_key,
    "command_payload": payload or {"intent_id": "intent-1"},
  }


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
async def test_challenge_replay_returns_stable_operation_identity(
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

  challenge = database.challenges[0]
  assert preview.confirmation_token not in str(challenge)
  assert challenge.device_session_id == "device-session-1"
  assert preview.target_volume == 100
  assert preview.estimated_amount == 1050.0

  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    **_approval_command_kwargs(),
  )
  assert challenge_id == preview.challenge_id
  assert database.commits == 2

  replay = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    **_approval_command_kwargs(),
  )
  assert replay == challenge_id
  assert database.commits == 2


@pytest.mark.asyncio
async def test_consumed_challenge_replays_after_intent_terminal_state(
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
  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    **_approval_command_kwargs(),
  )
  record.status = "APPROVED"

  replay = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    **_approval_command_kwargs(),
  )

  assert replay == challenge_id


@pytest.mark.asyncio
async def test_issue_does_not_replace_consumed_challenge_while_result_is_unknown(
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
  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    **_approval_command_kwargs(),
  )

  with pytest.raises(TradeApprovalChallengeError) as pending:
    await TradeApprovalChallengeService.issue(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      run_id="run-1",
      intent_id="intent-1",
    )

  assert pending.value.code == "APPROVAL_RESULT_PENDING"
  assert "继续重试原确认请求" in str(pending.value)
  assert database.challenges[0].id == challenge_id
  assert database.commits == 2


@pytest.mark.asyncio
async def test_new_preview_invalidates_older_unconsumed_token_and_has_one_outbox(
  configured_challenge_service,
):
  _record_value, database = configured_challenge_service
  first = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )
  second = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )

  assert first.challenge_id != second.challenge_id
  with pytest.raises(TradeApprovalChallengeError) as replaced:
    await TradeApprovalChallengeService.consume(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      run_id="run-1",
      intent_id="intent-1",
      confirmation_token=first.confirmation_token,
      **_approval_command_kwargs(command_key="replaced-token-command"),
    )
  assert replaced.value.code == "CONFIRMATION_SUPERSEDED"

  await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=second.confirmation_token,
    **_approval_command_kwargs(command_key="active-token-command"),
  )
  assert len(database.commands) == 1


@pytest.mark.asyncio
async def test_consume_binds_approval_outbox_before_marking_challenge_consumed(
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
  payload = {
    "run_id": "run-1",
    "intent_id": "intent-1",
    "expected_candidate_id": "candidate-1",
  }
  command_key = "t-trade:approve-entry:stable-command-key"
  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    command_type="T_TRADE_APPROVE_ENTRY",
    command_aggregate_id="run-1",
    command_idempotency_key=command_key,
    command_payload=payload,
  )

  assert challenge_id == preview.challenge_id
  assert len(database.commands) == 1
  command = database.commands[0]
  assert command.command_type == "T_TRADE_APPROVE_ENTRY"
  assert command.aggregate_id == "run-1"
  assert command.idempotency_key == command_key
  assert database.challenges[0].consumed_at is not None

  # Engine status updates can replace the cached intent metadata wholesale;
  # the independent challenge row must remain the durable operation identity.
  record.intent_metadata = {"engine_status_update": "APPROVED"}

  replay = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    command_type="T_TRADE_APPROVE_ENTRY",
    command_aggregate_id="run-1",
    command_idempotency_key=command_key,
    command_payload=payload,
  )
  assert replay == challenge_id
  assert len(database.commands) == 1
  assert database.commits == 2


@pytest.mark.asyncio
async def test_consume_commit_failure_rolls_back_challenge_and_outbox_together(
  configured_challenge_service,
):
  _record_value, database = configured_challenge_service
  preview = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )
  payload = {"intent_id": "intent-1", "expected_candidate_id": "candidate-1"}
  database.fail_commit_once = True

  with pytest.raises(RuntimeError, match="injected commit failure"):
    await TradeApprovalChallengeService.consume(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      run_id="run-1",
      intent_id="intent-1",
      confirmation_token=preview.confirmation_token,
      command_type="T_TRADE_APPROVE_ENTRY",
      command_aggregate_id="run-1",
      command_idempotency_key="command-atomic-retry",
      command_payload=payload,
    )

  assert database.commands == []
  assert database.challenges[0].consumed_at is None
  retry = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    command_type="T_TRADE_APPROVE_ENTRY",
    command_aggregate_id="run-1",
    command_idempotency_key="command-atomic-retry",
    command_payload=payload,
  )
  assert retry == preview.challenge_id
  assert len(database.commands) == 1
  assert database.challenges[0].consumed_at is not None


@pytest.mark.asyncio
async def test_factory_binds_final_challenge_id_to_one_stable_outbox(
  configured_challenge_service,
):
  _record_value, database = configured_challenge_service
  preview = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )
  payload = {"intent_id": "intent-1", "expected_candidate_id": "candidate-1"}

  first = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    command_type="T_TRADE_APPROVE_ENTRY",
    command_aggregate_id="run-1",
    command_idempotency_key_factory=lambda challenge_id: (
      f"t-trade:approve:{challenge_id}"
    ),
    command_payload=payload,
    return_command_reference=True,
  )
  second = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    command_type="T_TRADE_APPROVE_ENTRY",
    command_aggregate_id="run-1",
    command_idempotency_key_factory=lambda challenge_id: (
      f"t-trade:approve:{challenge_id}"
    ),
    command_payload=payload,
    return_command_reference=True,
  )

  assert first.challenge_id == preview.challenge_id
  assert second == first
  assert first.message_id == database.commands[0].message_id
  assert len(database.commands) == 1


@pytest.mark.asyncio
async def test_terminal_rejection_allows_new_challenge_and_preserves_old_token(
  configured_challenge_service,
):
  record, database = configured_challenge_service
  first = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )
  await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=first.confirmation_token,
    command_type="T_TRADE_APPROVE_ENTRY",
    command_aggregate_id="run-1",
    command_idempotency_key="command-rejected-1",
    command_payload={"intent_id": "intent-1"},
  )
  database.commands[0].processing_status = "SUCCEEDED"
  database.commands[0].result = {
    "success": False,
    "code": "INTENT_NOT_APPROVABLE",
  }

  second = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
  )

  assert second.challenge_id != first.challenge_id
  old_replay = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    run_id="run-1",
    intent_id="intent-1",
    confirmation_token=first.confirmation_token,
    command_type="T_TRADE_APPROVE_ENTRY",
    command_aggregate_id="run-1",
    command_idempotency_key="command-rejected-1",
    command_payload={"intent_id": "intent-1"},
  )
  assert old_replay == first.challenge_id
  assert len(database.challenges) == 2
  assert database.challenges[0].id == first.challenge_id


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
      **_approval_command_kwargs(),
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
      **_approval_command_kwargs(),
    )
  assert changed.value.code == "INTENT_CHANGED"
