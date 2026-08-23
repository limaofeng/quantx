from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)


def _existing_v3_intent(**overrides):
  values = {
    "id": "intent-v3",
    "strategy_run_id": "run-1",
    "account_id": "account-1",
    "strategy_id": "1",
    "instrument_code": "600000.SH",
    "direction": "BUY",
    "bucket": "swing",
    "reason": "T_TRADE_PULLBACK_REBOUND_ENTRY",
    "status": "AWAITING_APPROVAL",
    "intent_metadata": {
      "opportunity_schema_version": 3,
      "t_trade_role": "entry",
      "execution_mode": "MANUAL_CONFIRM",
      "account_id": "account-1",
      "candidate_id": "candidate-1",
      "candidate_fingerprint": "fingerprint-1",
      "candidate_state_version": 7,
      "config_version": 3,
      "policy_version": "policy-1",
    },
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def _incoming_v3_intent(**overrides):
  values = {
    "id": "intent-v3",
    "strategy_run_id": "run-1",
    "account_id": "account-1",
    "strategy_id": "1",
    "instrument_code": "600000.SH",
    "direction": "BUY",
    "bucket": "swing",
    "reason": "T_TRADE_PULLBACK_REBOUND_ENTRY",
    "status": "AWAITING_APPROVAL",
    "intent_metadata": dict(_existing_v3_intent().intent_metadata),
  }
  values.update(overrides)
  return values


def test_idempotent_intent_create_accepts_only_exact_initial_retry() -> None:
  TradeIntentRepository._validate_idempotent_create(
    _existing_v3_intent(),
    _incoming_v3_intent(),
  )


@pytest.mark.parametrize(
  "existing,incoming,field",
  [
    (
      _existing_v3_intent(),
      _incoming_v3_intent(strategy_run_id="run-2"),
      "strategy_run_id",
    ),
    (
      _existing_v3_intent(),
      _incoming_v3_intent(account_id="account-2"),
      "account_id",
    ),
    (
      _existing_v3_intent(status="FILLED"),
      _incoming_v3_intent(),
      "status",
    ),
    (
      _existing_v3_intent(),
      _incoming_v3_intent(
        intent_metadata={
          **_incoming_v3_intent()["intent_metadata"],
          "candidate_fingerprint": "different",
        }
      ),
      "metadata.candidate_fingerprint",
    ),
  ],
)
def test_idempotent_intent_create_rejects_identity_or_lifecycle_overwrite(
  existing,
  incoming,
  field: str,
) -> None:
  with pytest.raises(ValueError, match="TRADE_INTENT_IDEMPOTENCY_CONFLICT") as raised:
    TradeIntentRepository._validate_idempotent_create(existing, incoming)

  assert field in str(raised.value)


@pytest.mark.asyncio
async def test_v3_recovery_query_filters_protocol_and_is_exactly_run_scoped() -> None:
  valid = _existing_v3_intent()
  invalid_role = _existing_v3_intent(
    id="intent-wrong-role",
    intent_metadata={
      **dict(valid.intent_metadata),
      "t_trade_role": "exit",
    },
  )
  invalid_mode = _existing_v3_intent(
    id="intent-auto",
    intent_metadata={
      **dict(valid.intent_metadata),
      "execution_mode": "AUTO",
    },
  )
  result = SimpleNamespace(
    scalars=lambda: SimpleNamespace(all=lambda: [valid, invalid_role, invalid_mode])
  )
  db = SimpleNamespace(execute=AsyncMock(return_value=result))

  rows = await TradeIntentRepository(
    db
  ).find_v3_manual_candidate_recovery_intents(
    "run-1",
    linked_intent_ids=["linked-1"],
  )

  assert rows == [valid]
  statement = db.execute.await_args.args[0]
  sql = str(statement)
  assert "strategy_trade_intents.strategy_run_id" in sql
  assert "strategy_trade_intents.direction" in sql
  assert "strategy_trade_intents.status" in sql
  assert "strategy_trade_intents.id" in sql
  assert "strategy_trade_intents.notes IN" not in sql
  assert "LIMIT" in sql
  assert "run-1" in statement.compile().params.values()


@pytest.mark.asyncio
async def test_v3_recovery_query_rejects_more_than_its_bounded_row_limit() -> None:
  first = _existing_v3_intent(id="intent-1")
  second = _existing_v3_intent(id="intent-2")
  result = SimpleNamespace(
    scalars=lambda: SimpleNamespace(all=lambda: [first, second])
  )
  db = SimpleNamespace(execute=AsyncMock(return_value=result))

  with pytest.raises(RuntimeError, match="有界上限"):
    await TradeIntentRepository(
      db
    ).find_v3_manual_candidate_recovery_intents(
      "run-1",
      max_rows=1,
    )
