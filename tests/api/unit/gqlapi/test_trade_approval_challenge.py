from datetime import timedelta
from types import SimpleNamespace

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi import trade_approval
from quantx_api.gqlapi.trade_approval import (
  EXIT_PLAN_SELL_APPROVAL,
  T_TRADE_ENTRY_APPROVAL,
  TradeApprovalChallengeError,
  TradeApprovalChallengeService,
)
from quantx_domain.trading.exit_plan import (
  ExitExecutionPolicy,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleSpec,
  ExitRuleType,
  ExitT1Policy,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.exit_plan_authorization_service import (
  T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY,
)

ACCOUNT_ID = "ACCOUNT-1"
RUN_ID = "run-1"
INTENT_ID = "intent-1"
T_BATCH_ID = "t-batch-1"
EXIT_PLAN_ID = "t-exit-t-batch-1"
INSTRUMENT_CODE = "600000.SH"
SELL_PLAN_ID = "exit-plan-1"
SELL_INTENT_ID = "exit-intent-1"


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


def _exit_plan_template() -> dict:
  return ExitPlanTemplate(
    plan_id=EXIT_PLAN_ID,
    source_type="T_TRADE_BATCH",
    source_id=T_BATCH_ID,
    account_id=ACCOUNT_ID,
    instrument_code=INSTRUMENT_CODE,
    bucket="swing",
    rules=[
      ExitRuleSpec(
        rule_id=f"{EXIT_PLAN_ID}:target",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 11.2},
      )
    ],
    strategy_id="strategy-1",
    run_id=RUN_ID,
    config_version=3,
    t1_policy=ExitT1Policy.ALLOW_SAME_INSTRUMENT_SUBSTITUTION,
    execution=ExitExecutionPolicy(
      price_reference=ExitPriceReference.BID,
      price_type="MARKET",
      protected_limit=False,
      max_slippage_bps=30,
      urgency="PROTECTIVE_EXIT",
      execution_mode="AUTO",
    ),
    metadata={
      "t_trade_role": "exit",
      "account_id": ACCOUNT_ID,
      "strategy_run_id": RUN_ID,
      "instrument_code": INSTRUMENT_CODE,
      "t_batch_id": T_BATCH_ID,
      "exit_policy_version": 3,
    },
    auto_exit_authorized=False,
  ).to_dict()


def _record(*, ttl_ms: int = 60_000) -> TradeIntentRecord:
  now = time_utils.now()
  return TradeIntentRecord(
    id=INTENT_ID,
    strategy_run_id=RUN_ID,
    owner_type="STRATEGY_RUN",
    owner_id=RUN_ID,
    account_id=ACCOUNT_ID,
    strategy_id="strategy-1",
    instrument_code=INSTRUMENT_CODE,
    direction="BUY",
    bucket="swing",
    reason="T_TRADE_PULLBACK_REBOUND_ENTRY",
    priority="NORMAL",
    confidence=0.9,
    target_amount=1050.0,
    target_position_pct=None,
    target_volume=100,
    limit_price_hint=10.5,
    status="AWAITING_APPROVAL",
    intent_metadata={
      "approval_ttl_ms": ttl_ms,
      "intent_created_at": now.isoformat(),
      "signal": {"signal_price": 10.5},
      "t_trade_role": "entry",
      "account_id": ACCOUNT_ID,
      "strategy_run_id": RUN_ID,
      "instrument_code": INSTRUMENT_CODE,
      "opportunity_schema_version": 3,
      "execution_mode": "MANUAL_CONFIRM",
      "candidate_id": "candidate-1",
      "candidate_fingerprint": "candidate-fingerprint-1",
      "candidate_state_version": 7,
      "config_version": 3,
      "policy_version": "t-trade-v3",
      "max_price_deviation_bps": 30,
      "requested_entry_amount": 1050.0,
      "target_trade_amount": 1050.0,
      "t_batch_id": T_BATCH_ID,
      "exit_plan_id": EXIT_PLAN_ID,
      "exit_plan_template": _exit_plan_template(),
    },
    created_at=now,
    updated_at=now,
  )


def _exit_plan_record(*, ttl_ms: int = 60_000) -> TradeIntentRecord:
  now = time_utils.now()
  return TradeIntentRecord(
    id=SELL_INTENT_ID,
    strategy_run_id=RUN_ID,
    owner_type="EXIT_PLAN",
    owner_id=SELL_PLAN_ID,
    account_id=ACCOUNT_ID,
    strategy_id="managed-exit-plan",
    instrument_code=INSTRUMENT_CODE,
    direction="SELL",
    bucket="swing",
    reason="EXIT_PLAN_TARGET_PRICE",
    priority="HIGH",
    confidence=1.0,
    target_amount=None,
    target_position_pct=None,
    target_volume=100,
    limit_price_hint=11.2,
    status="AWAITING_APPROVAL",
    intent_metadata={
      "approval_ttl_ms": ttl_ms,
      "intent_created_at": now.isoformat(),
      "signal": {"signal_price": 11.2},
      "account_id": ACCOUNT_ID,
      "exit_plan_id": SELL_PLAN_ID,
      "rule_id": f"{SELL_PLAN_ID}:target",
    },
    created_at=now,
    updated_at=now,
  )


def _principal(
  *,
  device_session_id: str = "device-session-1",
  authorized_account_ids: tuple[str, ...] = (ACCOUNT_ID,),
) -> Principal:
  return Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id=device_session_id,
    access_token_expires_at=time_utils.now() + timedelta(minutes=5),
    permissions=frozenset({"trade:approve"}),
    authorized_account_ids=authorized_account_ids,
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


def _exit_plan_command_kwargs() -> dict:
  return {
    "command_type": "EXIT_PLAN_CONFIRM_INTENT",
    "command_aggregate_id": f"{ACCOUNT_ID}:{SELL_PLAN_ID}",
    "command_idempotency_key_factory": lambda challenge_id: (
      f"exit-plan-confirm:{challenge_id}"
    ),
    "command_payload": {
      "plan_id": SELL_PLAN_ID,
      "intent_id": SELL_INTENT_ID,
      "account_id": ACCOUNT_ID,
      "approval_audit": {
        "actor_id": "user-1",
        "device_session_id": "device-session-1",
        "channel": "EXIT_PLAN_DEVICE_CHALLENGE",
      },
    },
    "return_command_reference": True,
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


@pytest.fixture
def configured_exit_plan_challenge_service(monkeypatch):
  record = _exit_plan_record()
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
async def test_exit_plan_consume_atomically_binds_one_stable_engine_command(
  configured_exit_plan_challenge_service,
):
  record, database = configured_exit_plan_challenge_service
  preview = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=EXIT_PLAN_SELL_APPROVAL,
    account_id=ACCOUNT_ID,
    business_owner_id=SELL_PLAN_ID,
    intent_id=SELL_INTENT_ID,
  )

  assert len(database.challenges) == 1
  challenge = database.challenges[0]
  assert challenge.id == preview.challenge_id
  assert challenge.payload["business_owner_id"] == SELL_PLAN_ID
  assert challenge.payload["intent_id"] == SELL_INTENT_ID

  database.fail_commit_once = True
  with pytest.raises(RuntimeError, match="injected commit failure"):
    await TradeApprovalChallengeService.consume(
      principal=_principal(),
      action=EXIT_PLAN_SELL_APPROVAL,
      account_id=ACCOUNT_ID,
      business_owner_id=SELL_PLAN_ID,
      intent_id=SELL_INTENT_ID,
      confirmation_token=preview.confirmation_token,
      **_exit_plan_command_kwargs(),
    )

  assert database.commands == []
  assert challenge.consumed_at is None

  first = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=EXIT_PLAN_SELL_APPROVAL,
    account_id=ACCOUNT_ID,
    business_owner_id=SELL_PLAN_ID,
    intent_id=SELL_INTENT_ID,
    confirmation_token=preview.confirmation_token,
    **_exit_plan_command_kwargs(),
  )
  record.status = "APPROVED"
  replay = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=EXIT_PLAN_SELL_APPROVAL,
    account_id=ACCOUNT_ID,
    business_owner_id=SELL_PLAN_ID,
    intent_id=SELL_INTENT_ID,
    confirmation_token=preview.confirmation_token,
    **_exit_plan_command_kwargs(),
  )

  assert replay == first
  assert first.challenge_id == preview.challenge_id
  assert first.message_id == database.commands[0].message_id
  assert first.idempotency_key == f"exit-plan-confirm:{preview.challenge_id}"
  assert len(database.commands) == 1
  command = database.commands[0]
  assert command.command_type == "EXIT_PLAN_CONFIRM_INTENT"
  assert command.aggregate_id == f"{ACCOUNT_ID}:{SELL_PLAN_ID}"
  assert command.payload == {
    "plan_id": SELL_PLAN_ID,
    "intent_id": SELL_INTENT_ID,
    "account_id": ACCOUNT_ID,
    "approval_audit": {
      "actor_id": "user-1",
      "device_session_id": "device-session-1",
      "channel": "EXIT_PLAN_DEVICE_CHALLENGE",
      "challenge_id": preview.challenge_id,
    },
  }
  assert challenge.consumed_at is not None
  assert challenge.result_reference["engine_command"]["message_id"] == (
    first.message_id
  )
  assert database.commits == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("mismatch", "expected_code"),
  [
    ("plan", "INTENT_NOT_FOUND"),
    ("intent", "INTENT_NOT_FOUND"),
    ("account", "CONFIRMATION_CONTEXT_MISMATCH"),
    ("device", "CONFIRMATION_CONTEXT_MISMATCH"),
  ],
)
async def test_exit_plan_challenge_rejects_context_mismatch_without_outbox(
  configured_exit_plan_challenge_service,
  mismatch,
  expected_code,
):
  _record_value, database = configured_exit_plan_challenge_service
  preview = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=EXIT_PLAN_SELL_APPROVAL,
    account_id=ACCOUNT_ID,
    business_owner_id=SELL_PLAN_ID,
    intent_id=SELL_INTENT_ID,
  )
  principal = _principal()
  account_id = ACCOUNT_ID
  run_id = SELL_PLAN_ID
  intent_id = SELL_INTENT_ID
  if mismatch == "plan":
    run_id = "exit-plan-other"
  elif mismatch == "intent":
    intent_id = "exit-intent-other"
  elif mismatch == "account":
    account_id = "ACCOUNT-2"
    principal = _principal(
      authorized_account_ids=(ACCOUNT_ID, "ACCOUNT-2"),
    )
  else:
    principal = _principal(device_session_id="device-session-2")

  with pytest.raises(TradeApprovalChallengeError) as rejected:
    await TradeApprovalChallengeService.consume(
      principal=principal,
      action=EXIT_PLAN_SELL_APPROVAL,
      account_id=account_id,
      business_owner_id=run_id,
      intent_id=intent_id,
      confirmation_token=preview.confirmation_token,
      **_exit_plan_command_kwargs(),
    )

  assert rejected.value.code == expected_code
  assert database.commands == []
  assert database.challenges[0].consumed_at is None


@pytest.mark.asyncio
async def test_challenge_replay_returns_stable_operation_identity(
  configured_challenge_service,
):
  record, database = configured_challenge_service
  preview = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
    intent_id="intent-1",
  )

  challenge = database.challenges[0]
  assert preview.confirmation_token not in str(challenge)
  assert challenge.device_session_id == "device-session-1"
  assert preview.target_volume == 100
  assert preview.estimated_amount == 1050.0
  authorization = preview.t_trade_auto_exit_authorization
  assert authorization is not None
  assert authorization.plan_id == EXIT_PLAN_ID
  assert authorization.config_version == 3
  assert authorization.max_protected_volume == 100
  assert authorization.rules == _exit_plan_template()["rules"]
  assert authorization.t1_policy == "ALLOW_SAME_INSTRUMENT_SUBSTITUTION"
  assert authorization.execution_policy == _exit_plan_template()["execution"]
  assert authorization.execution_semantics == (
    "MiniQMT STOCK_SELL；沪深五档即时成交剩余撤销；委托价 0"
  )
  assert authorization.authorization_expires_at > preview.challenge_expires_at
  assert any("自动卖出" in warning for warning in preview.warnings)

  expected_template = _exit_plan_template()
  expected_template.pop("auto_exit_authorized")
  envelope = challenge.payload[T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY]
  assert envelope["subject"] == {
    "schema_version": 1,
    "account_id": ACCOUNT_ID,
    "strategy_run_id": RUN_ID,
    "entry_intent_id": INTENT_ID,
    "instrument_code": INSTRUMENT_CODE,
    "bucket": "swing",
    "t_batch_id": T_BATCH_ID,
    "exit_plan_id": EXIT_PLAN_ID,
    "exit_config_version": 3,
    "max_protected_volume": 100,
    "entry_target_amount": 1050.0,
    "entry_reference_price": 10.5,
    "entry_max_price_deviation_bps": 30.0,
    "exit_plan_template": expected_template,
  }
  assert len(envelope["fingerprint"]) == 64

  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
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
    business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )
  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    **_approval_command_kwargs(),
  )
  record.status = "APPROVED"

  replay = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )
  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    **_approval_command_kwargs(),
  )

  with pytest.raises(TradeApprovalChallengeError) as pending:
    await TradeApprovalChallengeService.issue(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )
  second = await TradeApprovalChallengeService.issue(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
    intent_id="intent-1",
  )

  assert first.challenge_id != second.challenge_id
  with pytest.raises(TradeApprovalChallengeError) as replaced:
    await TradeApprovalChallengeService.consume(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      business_owner_id="run-1",
      intent_id="intent-1",
      confirmation_token=first.confirmation_token,
      **_approval_command_kwargs(command_key="replaced-token-command"),
    )
  assert replaced.value.code == "CONFIRMATION_SUPERSEDED"

  await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )
  payload = {
    "run_id": "run-1",
    "intent_id": "intent-1",
    "expected_candidate_id": "candidate-1",
    "approval_audit": {
      "candidate_id": "candidate-1",
      "candidate_fingerprint": "candidate-fingerprint-1",
    },
  }
  command_key = "t-trade:approve-entry:stable-command-key"
  challenge_id = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
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
  assert command.payload["approval_audit"] == {
    "candidate_id": "candidate-1",
    "candidate_fingerprint": "candidate-fingerprint-1",
    "challenge_id": challenge_id,
  }
  assert database.challenges[0].consumed_at is not None

  # Engine status updates can replace the cached intent metadata wholesale;
  # the independent challenge row must remain the durable operation identity.
  record.intent_metadata = {"engine_status_update": "APPROVED"}

  replay = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
    intent_id="intent-1",
    confirmation_token=preview.confirmation_token,
    command_type="T_TRADE_APPROVE_ENTRY",
    command_aggregate_id="run-1",
    command_idempotency_key=command_key,
    command_payload=payload,
  )
  assert replay == challenge_id
  assert len(database.commands) == 1
  assert database.commands[0].payload["approval_audit"]["challenge_id"] == (
    challenge_id
  )
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )
  payload = {"intent_id": "intent-1", "expected_candidate_id": "candidate-1"}
  database.fail_commit_once = True

  with pytest.raises(RuntimeError, match="injected commit failure"):
    await TradeApprovalChallengeService.consume(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      business_owner_id="run-1",
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
    business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )
  payload = {"intent_id": "intent-1", "expected_candidate_id": "candidate-1"}

  first = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
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
    business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )
  await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )

  assert second.challenge_id != first.challenge_id
  old_replay = await TradeApprovalChallengeService.consume(
    principal=_principal(),
    action=T_TRADE_ENTRY_APPROVAL,
    account_id="ACCOUNT-1",
    business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )

  with pytest.raises(TradeApprovalChallengeError) as mismatch:
    await TradeApprovalChallengeService.consume(
      principal=_principal(device_session_id="device-session-2"),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      business_owner_id="run-1",
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
    business_owner_id="run-1",
    intent_id="intent-1",
  )
  record.target_volume = 200

  with pytest.raises(TradeApprovalChallengeError) as changed:
    await TradeApprovalChallengeService.consume(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id="ACCOUNT-1",
      business_owner_id="run-1",
      intent_id="intent-1",
      confirmation_token=preview.confirmation_token,
      **_approval_command_kwargs(),
    )
  assert changed.value.code == "INTENT_CHANGED"


@pytest.mark.asyncio
async def test_challenge_fails_closed_when_exit_template_is_missing(
  configured_challenge_service,
):
  record, database = configured_challenge_service
  record.intent_metadata.pop("exit_plan_template")

  with pytest.raises(
    ValueError,
    match="T_TRADE_EXIT_PLAN_TEMPLATE_MISSING",
  ):
    await TradeApprovalChallengeService.issue(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id=ACCOUNT_ID,
      business_owner_id=RUN_ID,
      intent_id=INTENT_ID,
    )

  assert database.challenges == []
  assert database.commits == 0


@pytest.mark.asyncio
async def test_challenge_fails_closed_when_exit_template_is_invalid(
  configured_challenge_service,
):
  record, database = configured_challenge_service
  record.intent_metadata["exit_plan_template"]["rules"] = []

  with pytest.raises(
    ValueError,
    match="T_TRADE_EXIT_PLAN_TEMPLATE_INVALID",
  ):
    await TradeApprovalChallengeService.issue(
      principal=_principal(),
      action=T_TRADE_ENTRY_APPROVAL,
      account_id=ACCOUNT_ID,
      business_owner_id=RUN_ID,
      intent_id=INTENT_ID,
    )

  assert database.challenges == []
  assert database.commits == 0
