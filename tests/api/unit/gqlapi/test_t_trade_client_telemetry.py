from datetime import datetime, timedelta, timezone

import pytest
from graphql import is_non_null_type
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.schema import schema
from quantx_api.monitoring.metrics import (
  T_TRADE_CLIENT_EVENTS,
  record_t_trade_client_event,
)

MUTATION = """
mutation RecordTTradeClientTelemetry($input: TTradeClientTelemetryInput!) {
  recordTTradeClientTelemetry(input: $input) {
    accepted
  }
}
"""


def _context(
  account_id: str,
  *,
  permissions: frozenset[str] = frozenset({"strategy:read"}),
) -> dict:
  return {
    "principal": Principal(
      user_id="telemetry-user",
      username="telemetry-user",
      display_name="Telemetry User",
      device_session_id="telemetry-session",
      access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
      + timedelta(minutes=5),
      permissions=permissions,
      authorized_account_ids=(account_id,),
    ),
    "request_id": "telemetry-request",
  }


def _input(**overrides: str) -> dict[str, str]:
  value = {
    "accountId": "ACCOUNT-1",
    "platform": "WEB",
    "event": "REFRESH_SUCCESS",
    "surface": "T_TRADE_SIGNAL_V3",
  }
  value.update(overrides)
  return value


def _count(*, platform: str = "WEB", event: str = "REFRESH_SUCCESS") -> float:
  return T_TRADE_CLIENT_EVENTS.labels(
    surface="T_TRADE_SIGNAL_V3",
    platform=platform,
    event=event,
  )._value.get()


@pytest.mark.asyncio
async def test_client_telemetry_accepts_fixed_contract_and_increments_counter() -> None:
  before = _count()

  result = await schema.execute(
    MUTATION,
    variable_values={"input": _input()},
    context_value=_context("ACCOUNT-1"),
  )

  assert result.errors is None
  assert result.data == {"recordTTradeClientTelemetry": {"accepted": True}}
  assert _count() == before + 1
  assert T_TRADE_CLIENT_EVENTS._labelnames == ("surface", "platform", "event")


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("field", "value"),
  [
    ("platform", "ANDROID"),
    ("event", "ERROR_WITH_FREE_TEXT"),
    ("surface", "OTHER_SURFACE"),
  ],
)
async def test_client_telemetry_rejects_unknown_enum_before_increment(
  field: str,
  value: str,
) -> None:
  before = _count()

  result = await schema.execute(
    MUTATION,
    variable_values={"input": _input(**{field: value})},
    context_value=_context("ACCOUNT-1"),
  )

  assert result.errors
  assert result.data is None
  assert _count() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_field", ["errorText", "stockCode", "userLabel"])
async def test_client_telemetry_rejects_high_cardinality_input_fields(
  extra_field: str,
) -> None:
  before = _count()
  payload = _input()
  payload[extra_field] = "must-not-be-accepted"

  result = await schema.execute(
    MUTATION,
    variable_values={"input": payload},
    context_value=_context("ACCOUNT-1"),
  )

  assert result.errors
  assert result.data is None
  assert _count() == before


@pytest.mark.asyncio
async def test_client_telemetry_requires_account_scope_and_read_permission() -> None:
  before = _count()
  unauthorized_account = await schema.execute(
    MUTATION,
    variable_values={"input": _input(accountId="OTHER-ACCOUNT")},
    context_value=_context("ACCOUNT-1"),
  )
  missing_permission = await schema.execute(
    MUTATION,
    variable_values={"input": _input()},
    context_value=_context(
      "ACCOUNT-1",
      permissions=frozenset({"portfolio:read"}),
    ),
  )

  assert unauthorized_account.errors
  assert unauthorized_account.errors[0].extensions["code"] == "FORBIDDEN"
  assert missing_permission.errors
  assert missing_permission.errors[0].extensions["code"] == "FORBIDDEN"
  assert _count() == before


def test_client_telemetry_contract_is_exact_required_and_non_trading() -> None:
  input_type = schema._schema.get_type("TTradeClientTelemetryInput")
  assert input_type is not None
  assert set(input_type.fields) == {"accountId", "platform", "event", "surface"}
  assert all(is_non_null_type(field.type) for field in input_type.fields.values())

  policy = operation_policy("Mutation", "recordTTradeClientTelemetry")
  assert policy.required_permissions == ("strategy:read",)
  assert policy.audiences == ("web", "native")
  assert policy.risk == "NON_TRADING_WRITE"


def test_metric_recorder_rejects_unbounded_internal_labels() -> None:
  before = _count()
  with pytest.raises(ValueError, match="platform"):
    record_t_trade_client_event(
      surface="T_TRADE_SIGNAL_V3",
      platform="ANDROID",
      event="REFRESH_SUCCESS",
    )
  assert _count() == before
