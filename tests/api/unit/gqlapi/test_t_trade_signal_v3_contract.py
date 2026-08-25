import asyncio
import copy
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_api.gqlapi.resolvers.t_trade as resolver_module
from graphql import GraphQLError
from quantx_api.gqlapi.resolvers.t_trade import (
  EngineCommandPendingError,
  TTradeResolver,
  stable_command_payload_digest,
)
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.types.t_trade_types import (
  TTradeCandidateApprovalExpectationInput,
  TTradeExternalEntryInput,
  TTradeGlobalSettingsInput,
  TTradeReplayPortfolioInput,
  TTradeReplayPortfolioSource,
  TTradeReplayStartInput,
  TTradeSignalDataHealth,
  TTradeSignalEvaluationKind,
  TTradeSignalPath,
  TTradeSignalPolicyInput,
  TTradeSignalPolicyPreviewInput,
)
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy


@pytest.fixture(autouse=True)
def reset_cold_global_get_single_flights():
  resolver_module._cold_global_get_keys.clear()
  yield
  resolver_module._cold_global_get_keys.clear()


def _policy_input(**overrides) -> TTradeSignalPolicyInput:
  payload = OpportunityPolicy().to_dict()
  payload.pop("policy_version")
  payload.pop("feature_schema_version")
  payload.update(overrides)
  return TTradeSignalPolicyInput(**payload)


def _replay_portfolio_input() -> TTradeReplayPortfolioInput:
  return TTradeReplayPortfolioInput(
    source=TTradeReplayPortfolioSource.SNAPSHOT,
    as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    snapshot_id="snapshot-d1",
  )


def _camel_case(value: str) -> str:
  head, *tail = value.split("_")
  return head + "".join(item[:1].upper() + item[1:] for item in tail)


def _signal_snapshot() -> dict:
  return {
    "instrument_code": "600000.SH",
    "trade_date": "2026-08-23",
    "evaluated_at_ms": 1_766_265_000_250,
    "source_time_ms": 1_766_265_000_000,
    "tick_ordinal": 9_007_199_254_740_993,
    "continuity_generation": 9_007_199_254_740_995,
    "data_health": "READY",
    "data_health_reasons": ["SPARSE_SAMPLE_COVERAGE"],
    "features": {
      "sample_count": 12,
      "coverage_seconds": 31.6,
      "price": 12.34,
      "pullback_pct": None,
    },
    "pullback": {
      "path": "PULLBACK_REBOUND",
      "phase": "REBOUND_CONFIRMING",
      "score": 76.25,
      "preview": True,
      "candidate_ready": True,
      "components": [
        {
          "name": "PULLBACK_DEPTH",
          "raw_value": 1.1,
          "contribution": 22.0,
          "weight": 25.0,
          "detail": "causal window",
        }
      ],
      "hard_gates": [{"code": "DATA_READY", "passed": True, "detail": ""}],
      "blockers": [],
    },
    "momentum": {
      "path": "MOMENTUM_ACCELERATION",
      "phase": "BASELINING",
      "score": None,
      "preview": False,
      "candidate_ready": False,
      "components": [],
      "hard_gates": [],
      "blockers": ["MOMENTUM_PATTERN_NOT_CONFIRMED"],
    },
    "selected_path": "PULLBACK_REBOUND",
    "opportunity_score": 76.25,
    "hard_gates": [{"code": "DATA_READY", "passed": True, "detail": ""}],
    "blockers": [],
    "candidate_status": "AWAITING_APPROVAL",
    "candidate_id": "candidate-1",
    "candidate_fingerprint": "fingerprint-1",
    "episode_id": "episode-1",
    "candidate_created_at_ms": 1_766_265_000_000,
    "candidate_expires_at_ms": 1_766_265_030_000,
    "preview_threshold": 55.0,
    "candidate_threshold": 72.0,
    "revalidate_threshold": 60.0,
    "rearm_threshold": 45.0,
    "signal_version": 7,
    "candidate_state_version": 7,
    "state_schema_version": "3",
    "feature_schema_version": "1",
    "policy_version": "t_trade_opportunity_v3.0.0",
    "config_version": 9,
    "profile_version": "2026-08-22.v1",
    "profile_fingerprint": None,
    "pending_entry_intent_id": "intent-1",
  }


def test_snapshot_maps_enums_nulls_versions_and_decimal_source_identity() -> None:
  snapshot = TTradeResolver._signal_snapshot_type(_signal_snapshot())

  assert snapshot is not None
  assert snapshot.data_health is TTradeSignalDataHealth.READY
  assert snapshot.selected_path is TTradeSignalPath.PULLBACK_REBOUND
  assert snapshot.momentum_score is None
  assert snapshot.features.pullback_pct is None
  assert snapshot.source_time_ms == "1766265000000"
  assert snapshot.tick_ordinal == "9007199254740993"
  assert snapshot.continuity_generation == "9007199254740995"
  assert snapshot.window_coverage_seconds == 32
  assert snapshot.signal_version == 7
  assert snapshot.candidate_state_version == 7
  assert snapshot.pending_entry_intent_id == "intent-1"
  assert snapshot.score_contributions[0].points == 22.0
  assert snapshot.data_health_reasons[0].code == "SPARSE_SAMPLE_COVERAGE"
  assert snapshot.pullback.preview is True
  assert snapshot.momentum.preview is False
  assert snapshot.momentum.candidate_ready is False
  assert snapshot.hard_gates[0].passed is True


@pytest.mark.parametrize(
  ("field", "value"),
  [
    ("source_time_ms", "1766265000251"),
    ("opportunity_score", 100.01),
    ("features", {"sample_count": 12, "coverage_seconds": -0.01}),
    ("revalidate_threshold", 54.0),
  ],
)
def test_snapshot_rejects_non_causal_or_out_of_contract_numeric_values(
  field: str,
  value,
) -> None:
  raw = _signal_snapshot()
  if field == "features":
    raw[field].update(value)
  else:
    raw[field] = value

  assert TTradeResolver._signal_snapshot_type(raw) is None


@pytest.mark.parametrize(
  ("field", "value"),
  [
    ("continuity_generation", None),
    ("preview_threshold", None),
    ("state_schema_version", ""),
    ("data_health", "UNKNOWN_HEALTH"),
  ],
)
def test_snapshot_fails_closed_when_core_contract_is_missing_or_unknown(
  field: str,
  value,
) -> None:
  raw = _signal_snapshot()
  raw[field] = value

  assert TTradeResolver._signal_snapshot_type(raw) is None


@pytest.mark.parametrize(
  ("section", "field", "value"),
  [
    ("pullback", "preview", "false"),
    ("momentum", "candidate_ready", None),
    ("hard_gates", "passed", "false"),
  ],
)
def test_snapshot_rejects_non_boolean_or_missing_required_boolean(
  section: str,
  field: str,
  value,
) -> None:
  raw = _signal_snapshot()
  if section == "hard_gates":
    if value is None:
      raw[section][0].pop(field)
    else:
      raw[section][0][field] = value
  elif value is None:
    raw[section].pop(field)
  else:
    raw[section][field] = value

  assert TTradeResolver._signal_snapshot_type(raw) is None


@pytest.mark.parametrize(
  ("section", "field"),
  [
    (None, "candidate_status"),
    (None, "selected_path"),
    ("pullback", "phase"),
    ("momentum", "phase"),
  ],
)
def test_snapshot_rejects_unknown_signal_enum(
  section: str | None,
  field: str,
) -> None:
  raw = _signal_snapshot()
  if section is None:
    raw[field] = "UNKNOWN_ENUM"
  else:
    raw[section][field] = "UNKNOWN_ENUM"

  assert TTradeResolver._signal_snapshot_type(raw) is None


@pytest.mark.asyncio
async def test_signal_policy_preview_uses_validation_only_engine_command(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  changed = _policy_input(candidate_score=75.0)
  engine_request = AsyncMock(
    return_value={
      "errors": [],
      "warnings": [],
      "normalized_policy": TTradeResolver._policy_from_input(changed).to_dict(),
      "changed_fields": ["candidate_score"],
      "requires_rewarm": True,
      "config_version": 4,
    }
  )
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)

  result = await TTradeResolver.preview_signal_policy(
    TTradeSignalPolicyPreviewInput(
      account_id="account-1",
      expected_config_version=4,
      signal_policy=changed,
    )
  )

  assert result.valid is True
  assert result.errors == []
  assert result.changed_fields == ["candidate_score"]
  assert result.requires_rewarm is True
  assert result.normalized_policy is not None
  assert result.normalized_policy.candidate_score == 75.0
  assert engine_request.await_args.args[0] == "T_TRADE_SIGNAL_POLICY_PREVIEW"
  assert engine_request.await_args.args[1]["input"]["expected_config_version"] == 4
  preview_payload = engine_request.await_args.args[1]["input"]["signal_policy"]
  assert set(preview_payload) == {
    "policy_version",
    "feature_schema_version",
    *OpportunityPolicy.configurable_field_names(),
  }


def test_signal_policy_graphql_contract_exposes_every_typed_field_as_required():
  sdl = schema.as_str()
  input_start = sdl.index("input TTradeSignalPolicyInput {")
  input_block = sdl[input_start : sdl.index("}", input_start)]
  output_start = sdl.index("type TTradeSignalPolicy {")
  output_block = sdl[output_start : sdl.index("}", output_start)]

  for field_name in OpportunityPolicy.configurable_field_names():
    graphql_name = _camel_case(field_name)
    assert f"  {graphql_name}:" in input_block
    assert next(
      line for line in input_block.splitlines() if line.startswith(f"  {graphql_name}:")
    ).endswith("!")
    assert f"  {graphql_name}:" in output_block

  parameters = inspect.signature(TTradeSignalPolicyInput).parameters
  assert set(parameters) == set(OpportunityPolicy.configurable_field_names())
  assert all(item.default is inspect.Parameter.empty for item in parameters.values())


def test_signal_policy_graphql_input_rejects_each_missing_field_and_round_trips_all():
  payload = OpportunityPolicy().to_dict()
  payload.pop("policy_version")
  payload.pop("feature_schema_version")
  for field_name in OpportunityPolicy.configurable_field_names():
    incomplete = dict(payload)
    incomplete.pop(field_name)
    with pytest.raises(TypeError):
      TTradeSignalPolicyInput(**incomplete)

  input_policy = TTradeSignalPolicyInput(**payload)
  domain_policy = TTradeResolver._policy_from_input(input_policy)
  output_policy = TTradeResolver._signal_policy_from_domain(domain_policy)
  assert domain_policy.to_dict() == OpportunityPolicy().to_dict()
  assert set(output_policy.__dict__) == {
    "policy_version",
    "feature_schema_version",
    *OpportunityPolicy.configurable_field_names(),
  }

  with pytest.raises(ValueError, match="signal policy has unknown fields"):
    TTradeResolver._signal_policy_type(
      {**OpportunityPolicy().to_dict(), "hidden_magic": 1}
    )


def test_configurable_gate_and_component_codes_have_server_owned_labels():
  for code in (
    "TRADING_SESSION",
    "PULLBACK_REQUIRED_CUMULATIVE_AMOUNT",
    "MOMENTUM_REQUIRED_BID_PRICE",
    "REQUIRED_FIELD_ASK_PRICE_UNAVAILABLE",
    "PULLBACK_DEPTH",
    "MOMENTUM_OVEREXTENSION_PENALTY",
  ):
    assert not TTradeResolver._signal_label(code).startswith("未注册状态")


@pytest.mark.asyncio
async def test_signal_policy_preview_rejects_invalid_threshold_order(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine_request = AsyncMock()
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  result = await TTradeResolver.preview_signal_policy(
    TTradeSignalPolicyPreviewInput(
      account_id="account-1",
      expected_config_version=4,
      signal_policy=_policy_input(
        candidate_score=50.0,
        revalidate_score=60.0,
      ),
    )
  )

  assert result.valid is False
  assert result.normalized_policy is None
  assert result.errors[0].code == "INVALID_POLICY"
  engine_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_global_monitor_sends_nested_policy_and_expected_version(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine_request = AsyncMock(return_value={"projection": "new"})
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  monkeypatch.setattr(
    TTradeResolver,
    "_global_monitor_type",
    classmethod(lambda cls, raw: raw),
  )
  settings = TTradeGlobalSettingsInput(
    account_id="account-1",
    expected_config_version=4,
    signal_policy=_policy_input(candidate_score=75.0),
  )

  result = await TTradeResolver.save_global_monitor(settings)

  assert result.success is True
  command_payload = engine_request.await_args.args[1]["input"]
  assert command_payload["expected_config_version"] == 4
  assert command_payload["signal_policy"]["candidate_score"] == 75.0
  assert command_payload["signal_policy"]["policy_version"] == (
    "t_trade_opportunity_v3.0.0"
  )
  assert set(command_payload["signal_policy"]) == {
    "policy_version",
    "feature_schema_version",
    *OpportunityPolicy.configurable_field_names(),
  }


@pytest.mark.asyncio
async def test_save_global_monitor_keeps_draft_when_engine_receipt_is_pending(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  receipt = SimpleNamespace(status="PENDING", message_id="command-1")
  monkeypatch.setattr(
    TTradeResolver,
    "_engine_request",
    AsyncMock(side_effect=EngineCommandPendingError(receipt)),
  )

  result = await TTradeResolver.save_global_monitor(
    TTradeGlobalSettingsInput(
      account_id="account-1",
      expected_config_version=4,
      signal_policy=_policy_input(candidate_score=75.0),
    )
  )

  assert result.success is False
  assert result.code == "CONFIG_SAVE_COMMAND_PENDING"
  assert "请求仍在处理" in result.message
  assert "尚不知是否已提交" in result.message
  assert "command-1" in result.message
  assert result.monitor is None


def test_global_save_digest_is_order_independent_and_rejects_non_finite_values():
  first = {
    "account_id": "account-1",
    "expected_config_version": 4,
    "settings": {"max_trade_amount": 12_000.0, "enabled": True},
  }
  second = {
    "settings": {"enabled": True, "max_trade_amount": 12_000.0},
    "expected_config_version": 4,
    "account_id": "account-1",
  }

  assert stable_command_payload_digest(first) == stable_command_payload_digest(second)
  with pytest.raises(ValueError, match="finite JSON values"):
    stable_command_payload_digest({"value": float("nan")})


@pytest.mark.asyncio
async def test_global_save_retries_share_payload_digest_but_changes_do_not(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine_request = AsyncMock(
    return_value={"config_version": 5, "last_error": None, "apply_status": "APPLIED"}
  )
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  monkeypatch.setattr(
    TTradeResolver,
    "_global_monitor_type",
    classmethod(lambda cls, raw: raw),
  )
  base = TTradeGlobalSettingsInput(
    account_id="account-1",
    expected_config_version=4,
    signal_policy=_policy_input(),
  )
  changed = TTradeGlobalSettingsInput(
    account_id="account-1",
    expected_config_version=4,
    signal_policy=_policy_input(candidate_score=75.0),
  )

  await TTradeResolver.save_global_monitor(base)
  await TTradeResolver.save_global_monitor(base)
  await TTradeResolver.save_global_monitor(changed)

  keys = [call.kwargs["idempotency_key"] for call in engine_request.await_args_list]
  assert keys[0] == keys[1]
  assert keys[0] != keys[2]


@pytest.mark.asyncio
async def test_global_save_changed_payload_reaches_engine_and_maps_stale_cas_conflict(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine_request = AsyncMock(
    side_effect=[
      {"config_version": 5, "last_error": None, "apply_status": "APPLIED"},
      ValueError("CONFIG_VERSION_CONFLICT: expected=4 actual=5"),
    ]
  )
  latest = AsyncMock(return_value={"config_version": 5})
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  monkeypatch.setattr(
    resolver_module.t_trade_monitor_projection_service,
    "get",
    latest,
  )
  monkeypatch.setattr(
    TTradeResolver,
    "_global_monitor_type",
    classmethod(lambda cls, raw: raw),
  )

  first = await TTradeResolver.save_global_monitor(
    TTradeGlobalSettingsInput(
      account_id="account-1",
      expected_config_version=4,
      signal_policy=_policy_input(),
    )
  )
  second = await TTradeResolver.save_global_monitor(
    TTradeGlobalSettingsInput(
      account_id="account-1",
      expected_config_version=4,
      signal_policy=_policy_input(candidate_score=75.0),
    )
  )

  assert first.success is True
  assert second.success is False
  assert second.code == "CONFIG_VERSION_CONFLICT"
  assert (
    engine_request.await_args_list[0].kwargs["idempotency_key"]
    != engine_request.await_args_list[1].kwargs["idempotency_key"]
  )


@pytest.mark.asyncio
async def test_global_save_pending_is_non_success_and_recovered_retry_can_apply(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  pending = {
    "config_version": 5,
    "last_error": "rewarm unavailable",
    "apply_status": "PENDING",
    "apply_code": "CONFIG_APPLY_PENDING",
  }
  engine_request = AsyncMock(return_value=pending)
  projection = AsyncMock(return_value={"config_version": 5, "last_error": None})
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  monkeypatch.setattr(
    resolver_module.t_trade_monitor_projection_service,
    "get",
    projection,
  )
  monkeypatch.setattr(
    TTradeResolver,
    "_global_monitor_type",
    classmethod(lambda cls, raw: raw),
  )

  result = await TTradeResolver.save_global_monitor(
    TTradeGlobalSettingsInput(
      account_id="account-1",
      expected_config_version=4,
      signal_policy=_policy_input(),
    )
  )

  assert result.success is True
  assert result.code == "CONFIG_APPLIED"
  assert result.monitor == {"config_version": 5, "last_error": None}


@pytest.mark.asyncio
async def test_global_save_pending_does_not_accept_lower_recovered_projection(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    TTradeResolver,
    "_engine_request",
    AsyncMock(
      return_value={
        "config_version": 5,
        "last_error": "rewarm unavailable",
        "apply_status": "PENDING",
        "apply_code": "CONFIG_APPLY_PENDING",
      }
    ),
  )
  monkeypatch.setattr(
    resolver_module.t_trade_monitor_projection_service,
    "get",
    AsyncMock(return_value={"config_version": 4, "last_error": None}),
  )
  monkeypatch.setattr(
    TTradeResolver,
    "_global_monitor_type",
    classmethod(lambda cls, raw: raw),
  )

  result = await TTradeResolver.save_global_monitor(
    TTradeGlobalSettingsInput(
      account_id="account-1",
      expected_config_version=4,
      signal_policy=_policy_input(),
    )
  )

  assert result.success is False
  assert result.code == "CONFIG_APPLY_PENDING"
  assert result.monitor["config_version"] == 5
  assert result.monitor["last_error"] == "rewarm unavailable"


@pytest.mark.asyncio
async def test_save_global_monitor_maps_version_conflict_to_latest_projection(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    TTradeResolver,
    "_engine_request",
    AsyncMock(side_effect=ValueError("CONFIG_VERSION_CONFLICT: expected=4 actual=5")),
  )
  monkeypatch.setattr(
    resolver_module.t_trade_monitor_projection_service,
    "get",
    AsyncMock(return_value={"config_version": 5}),
  )
  monkeypatch.setattr(
    TTradeResolver,
    "_global_monitor_type",
    classmethod(lambda cls, raw: {"latest": raw["config_version"]}),
  )

  result = await TTradeResolver.save_global_monitor(
    TTradeGlobalSettingsInput(
      account_id="account-1",
      expected_config_version=4,
      signal_policy=_policy_input(),
    )
  )

  assert result.success is False
  assert result.code == "CONFIG_VERSION_CONFLICT"
  assert result.monitor == {"latest": 5}


@pytest.mark.asyncio
async def test_approval_forwards_all_candidate_cas_fields(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = SimpleNamespace(
    get_session=AsyncMock(return_value={"mode": "paper", "account_id": "account-1"})
  )
  engine_request = AsyncMock(
    return_value={"success": True, "code": "APPROVED", "message": "ok"}
  )
  monkeypatch.setattr(TTradeResolver, "service", service)
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  expectation = TTradeCandidateApprovalExpectationInput(
    signal_version=7,
    candidate_id="candidate-1",
    candidate_fingerprint="fingerprint-1",
    candidate_state_version=7,
    config_version=9,
    policy_version="t_trade_opportunity_v3.0.0",
  )

  result = await TTradeResolver.approve_entry(
    "run-1",
    "intent-1",
    expectation=expectation,
    idempotency_key="approval-operation-1",
    actor_id="user-1",
  )

  assert result.success is True
  payload = engine_request.await_args.args[1]
  assert payload["expected_signal_version"] == 7
  assert payload["expected_candidate_id"] == "candidate-1"
  assert payload["expected_candidate_fingerprint"] == "fingerprint-1"
  assert payload["expected_candidate_state_version"] == 7
  assert payload["expected_config_version"] == 9
  assert payload["expected_policy_version"] == "t_trade_opportunity_v3.0.0"


@pytest.mark.asyncio
async def test_approval_retry_reaches_stable_engine_operation_after_readiness_changes(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = SimpleNamespace(
    get_session=AsyncMock(return_value={"mode": "live", "account_id": "account-1"})
  )
  readiness = AsyncMock(
    return_value={"can_approve": False, "blocked_reasons": ["agent changed"]}
  )
  engine_request = AsyncMock(
    side_effect=[
      {"success": True, "code": "APPROVED", "message": "ok"},
      {"success": True, "code": "APPROVED", "message": "ok"},
    ]
  )
  monkeypatch.setattr(TTradeResolver, "service", service)
  monkeypatch.setattr(
    TTradeResolver,
    "operations_service",
    SimpleNamespace(readiness=readiness),
  )
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  expectation = TTradeCandidateApprovalExpectationInput(
    signal_version=7,
    candidate_id="candidate-1",
    candidate_fingerprint="fingerprint-1",
    candidate_state_version=7,
    config_version=9,
    policy_version="t_trade_opportunity_v3.0.0",
  )

  first = await TTradeResolver.approve_entry(
    "run-1",
    "intent-1",
    expectation=expectation,
    idempotency_key="approval-operation-1",
  )
  second = await TTradeResolver.approve_entry(
    "run-1",
    "intent-1",
    expectation=expectation,
    idempotency_key="approval-operation-1",
  )

  assert first.success is True
  assert second.success is True
  assert readiness.await_count == 0
  keys = [call.kwargs["idempotency_key"] for call in engine_request.await_args_list]
  assert keys[0] == keys[1]


def _pending_engine_request(*, message_id: str = "command-pending") -> AsyncMock:
  receipt = SimpleNamespace(status="PROCESSING", message_id=message_id)
  return AsyncMock(
    side_effect=EngineCommandPendingError(
      receipt,
      "T_TRADE_TEST_COMMAND",
    )
  )


async def test_engine_command_pending_is_not_validation_error() -> None:
  pending = EngineCommandPendingError(
    SimpleNamespace(status="PENDING", message_id="command-pending")
  )

  assert not isinstance(pending, ValueError)
  assert "尚不知是否已提交" in str(pending)


@pytest.mark.asyncio
async def test_cold_global_monitor_get_exposes_typed_pending_graphql_error(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    resolver_module.t_trade_monitor_projection_service,
    "get",
    AsyncMock(return_value=None),
  )
  engine_request = _pending_engine_request(message_id="get-command-1")
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)

  with pytest.raises(GraphQLError) as raised:
    await TTradeResolver.get_global_monitor("account-1")

  assert raised.value.extensions["code"] == "T_TRADE_GLOBAL_GET_COMMAND_PENDING"
  assert raised.value.extensions["retryable"] is True
  assert raised.value.extensions["commandId"] == "get-command-1"
  assert "尚不知是否已提交" in str(raised.value)
  with pytest.raises(GraphQLError):
    await TTradeResolver.get_global_monitor("account-1")
  assert engine_request.await_count == 2
  keys = [call.kwargs["idempotency_key"] for call in engine_request.await_args_list]
  assert keys[0] == keys[1]


@pytest.mark.asyncio
async def test_cold_global_monitor_get_single_flight_coalesces_concurrent_wakeups(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    resolver_module.t_trade_monitor_projection_service,
    "get",
    AsyncMock(return_value=None),
  )
  engine_request = _pending_engine_request(message_id="get-command-concurrent")
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)

  results = await asyncio.gather(
    TTradeResolver.get_global_monitor("account-concurrent"),
    TTradeResolver.get_global_monitor("account-concurrent"),
    return_exceptions=True,
  )

  assert all(isinstance(result, GraphQLError) for result in results)
  assert engine_request.await_count == 2
  keys = [call.kwargs["idempotency_key"] for call in engine_request.await_args_list]
  assert keys[0] == keys[1]


@pytest.mark.asyncio
async def test_cold_global_monitor_get_releases_terminal_key_before_projection_loss_retry(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    resolver_module.t_trade_monitor_projection_service,
    "get",
    AsyncMock(return_value=None),
  )
  engine_request = AsyncMock(
    side_effect=[{"config_version": 1}, {"config_version": 2}]
  )
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  monkeypatch.setattr(
    TTradeResolver,
    "_global_monitor_type",
    classmethod(lambda cls, raw: raw),
  )

  first = await TTradeResolver.get_global_monitor("account-loss")
  second = await TTradeResolver.get_global_monitor("account-loss")

  assert first == {"config_version": 1}
  assert second == {"config_version": 2}
  keys = [call.kwargs["idempotency_key"] for call in engine_request.await_args_list]
  assert keys[0] != keys[1]


@pytest.mark.asyncio
async def test_cold_global_monitor_get_fails_closed_at_single_flight_capacity(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    resolver_module.t_trade_monitor_projection_service,
    "get",
    AsyncMock(return_value=None),
  )
  resolver_module._cold_global_get_keys.update(
    {
      f"account-{index}": f"key-{index}"
      for index in range(resolver_module._T_TRADE_GLOBAL_GET_MAX_SINGLE_FLIGHTS)
    }
  )

  with pytest.raises(GraphQLError) as raised:
    await TTradeResolver.get_global_monitor("account-capacity")

  assert raised.value.extensions["code"] == (
    "T_TRADE_GLOBAL_GET_SINGLE_FLIGHT_CAPACITY"
  )
  assert raised.value.extensions["retryable"] is True


@pytest.mark.asyncio
async def test_global_reconcile_pending_is_explicit_and_not_validation_failure(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine_request = _pending_engine_request(message_id="reconcile-command-1")
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)

  result = await TTradeResolver.reconcile_global_monitor(
    "account-1",
    "reconcile-operation-1",
  )

  assert result.success is False
  assert result.code == "T_TRADE_GLOBAL_RECONCILE_COMMAND_PENDING"
  assert "尚不知是否已提交" in result.message


@pytest.mark.asyncio
async def test_signal_policy_preview_pending_is_explicit_and_retry_is_one_shot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine_request = _pending_engine_request(message_id="preview-command-1")
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)
  input_value = TTradeSignalPolicyPreviewInput(
    account_id="account-1",
    expected_config_version=4,
    signal_policy=_policy_input(candidate_score=75.0),
  )

  first = await TTradeResolver.preview_signal_policy(input_value)
  second = await TTradeResolver.preview_signal_policy(input_value)

  assert first.valid is False
  assert second.valid is False
  assert first.errors[0].code == "T_TRADE_SIGNAL_POLICY_PREVIEW_COMMAND_PENDING"
  assert "尚不知是否已提交" in first.errors[0].message
  keys = [call.kwargs["idempotency_key"] for call in engine_request.await_args_list]
  assert keys[0] != keys[1]


@pytest.mark.asyncio
async def test_reconcile_client_operation_key_is_stable_only_for_same_operation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine_request = _pending_engine_request(message_id="reconcile-command-1")
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)

  await TTradeResolver.reconcile_global_monitor("account-1", "operation-1")
  await TTradeResolver.reconcile_global_monitor("account-1", "operation-1")
  await TTradeResolver.reconcile_global_monitor("account-1", "operation-2")

  keys = [call.kwargs["idempotency_key"] for call in engine_request.await_args_list]
  assert keys[0] == keys[1]
  assert keys[0] != keys[2]
  assert "operation-1" not in keys[0]
  assert keys[0].startswith("t-trade:global-reconcile:")


def test_t_trade_operation_inputs_require_client_idempotency_keys() -> None:
  sdl = schema.as_str()
  assert (
    "reconcileTTradeGlobalMonitor(accountId: String!, idempotencyKey: String!)"
    in sdl
  )
  assert "startTTradeSession" not in sdl
  assert "TTradeStartInput" not in sdl


@pytest.mark.asyncio
async def test_approve_entry_pending_is_explicit(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    TTradeResolver,
    "service",
    SimpleNamespace(
      get_session=AsyncMock(
        return_value={"mode": "paper", "account_id": "account-1"}
      )
    ),
  )
  engine_request = _pending_engine_request(message_id="approve-command-1")
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)

  result = await TTradeResolver.approve_entry(
    "run-1",
    "intent-1",
    expectation=TTradeCandidateApprovalExpectationInput(
      signal_version=7,
      candidate_id="candidate-1",
      candidate_fingerprint="fingerprint-1",
      candidate_state_version=7,
      config_version=9,
      policy_version="t_trade_opportunity_v3.0.0",
    ),
    idempotency_key="approval-pending-1",
  )

  assert result.success is False
  assert result.code == "T_TRADE_APPROVE_ENTRY_COMMAND_PENDING"
  assert "尚不知是否已提交" in result.message


@pytest.mark.asyncio
async def test_stop_session_pending_is_explicit_and_retry_key_is_stable(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine_request = _pending_engine_request(message_id="stop-command-1")
  monkeypatch.setattr(TTradeResolver, "_engine_request", engine_request)

  first = await TTradeResolver.stop_session("run-1")
  second = await TTradeResolver.stop_session("run-1")

  assert first.success is False
  assert second.success is False
  assert first.code == "T_TRADE_STOP_SESSION_COMMAND_PENDING"
  assert "尚不知是否已提交" in first.message
  keys = [call.kwargs["idempotency_key"] for call in engine_request.await_args_list]
  assert keys[0] == keys[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("operation", "expected_code"),
  [
    ("reject", "T_TRADE_REJECT_ENTRY_COMMAND_PENDING"),
    ("import", "T_TRADE_IMPORT_EXTERNAL_ENTRY_COMMAND_PENDING"),
    ("replay_cancel", "T_TRADE_REPLAY_CANCEL_COMMAND_PENDING"),
  ],
)
async def test_one_shot_pending_results_are_explicit_and_never_success(
  monkeypatch: pytest.MonkeyPatch,
  operation: str,
  expected_code: str,
) -> None:
  monkeypatch.setattr(
    TTradeResolver,
    "_engine_request",
    _pending_engine_request(message_id=f"{operation}-command-1"),
  )

  if operation == "reject":
    result = await TTradeResolver.reject_entry("run-1", "intent-1")
    assert result.session is None
  elif operation == "import":
    result = await TTradeResolver.import_external_entry(
      TTradeExternalEntryInput(
        run_id="run-1",
        account_id="account-1",
        order_id="order-1",
      )
    )
    assert result.session is None
  else:
    result = await TTradeResolver.cancel_replay("run-1")
    assert result.replay is None

  assert result.success is False
  assert result.code == expected_code
  assert "尚不知是否已提交" in result.message


@pytest.mark.asyncio
async def test_replay_start_raw_key_is_not_derived_from_payload_digest(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = AsyncMock(
    return_value=SimpleNamespace(
      status="PROCESSING",
      message_id="replay-command-1",
      command_type="T_TRADE_REPLAY_START",
    )
  )
  monkeypatch.setattr(resolver_module.engine_command_service, "request", request)
  base = dict(
    account_id="account-1",
    idempotency_key="client-operation-1",
    start_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
    end_time=datetime(2026, 8, 2, tzinfo=timezone.utc),
    signal_policy=_policy_input(),
    portfolio=_replay_portfolio_input(),
  )

  first = await TTradeResolver.start_replay(TTradeReplayStartInput(**base))
  second = await TTradeResolver.start_replay(
    TTradeReplayStartInput(
      **{**base, "end_time": datetime(2026, 8, 3, tzinfo=timezone.utc)}
    )
  )

  assert first.success is False
  assert second.success is False
  keys = [call.kwargs["idempotency_key"] for call in request.await_args_list]
  assert keys[0] == keys[1]
  assert keys[0].startswith("t-trade:replay-start:")


@pytest.mark.asyncio
async def test_replay_start_submission_unknown_is_retryable_and_not_terminal(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = AsyncMock(side_effect=RuntimeError("connection lost after commit"))
  monkeypatch.setattr(resolver_module.engine_command_service, "request", request)
  input_value = TTradeReplayStartInput(
    account_id="account-1",
    idempotency_key="client-operation-unknown",
    start_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
    end_time=datetime(2026, 8, 2, tzinfo=timezone.utc),
    signal_policy=_policy_input(),
    portfolio=_replay_portfolio_input(),
  )

  result = await TTradeResolver.start_replay(input_value)

  assert result.success is False
  assert result.replay is None
  assert result.code == "T_TRADE_REPLAY_START_OUTCOME_UNKNOWN"
  assert "尚不知是否已提交" in result.message


class _DbContext:
  async def __aenter__(self):
    return object()

  async def __aexit__(self, exc_type, exc, tb):
    return False


@pytest.mark.asyncio
async def test_evaluation_history_uses_stable_keyset_and_material_default(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  evaluated_at = datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc)
  rows = [
    SimpleNamespace(
      id="b",
      account_id="account-1",
      strategy_run_id="run-1",
      instrument_code="600000.SH",
      evaluated_at=evaluated_at,
      record_kind="MATERIAL",
      event_type="CANDIDATE_LATCHED",
      window_started_at=None,
      window_ended_at=None,
      coalesced_count=1,
      policy_version="t_trade_opportunity_v3.0.0",
      schema_version="3",
      content_fingerprint="fingerprint-b",
      payload={"signal_snapshot": _signal_snapshot()},
    ),
    SimpleNamespace(
      id="a",
      account_id="account-1",
      strategy_run_id="run-1",
      instrument_code="600000.SH",
      evaluated_at=evaluated_at,
      record_kind="MATERIAL",
      event_type="PREVIEW_CROSSED",
      window_started_at=None,
      window_ended_at=None,
      coalesced_count=1,
      policy_version="t_trade_opportunity_v3.0.0",
      schema_version="3",
      content_fingerprint="fingerprint-a",
      payload={},
    ),
  ]
  repository = SimpleNamespace(list_evaluations=AsyncMock(return_value=rows))
  monkeypatch.setattr(resolver_module, "AsyncSessionLocal", _DbContext)
  monkeypatch.setattr(
    resolver_module,
    "TTradeOpportunityEvaluationRepository",
    lambda db: repository,
  )

  page = await TTradeResolver.list_signal_evaluations(
    "account-1",
    stock_code="600000.SH",
    event_kinds=None,
    start_time=evaluated_at,
    end_time=evaluated_at,
    first=1,
    after=None,
  )

  assert page.page_info.has_next_page is True
  assert page.items[0].id == "b"
  assert page.items[0].event_kind is TTradeSignalEvaluationKind.MATERIAL
  assert page.items[0].signal_snapshot is not None
  call = repository.list_evaluations.await_args.kwargs
  assert call["record_kind"] == "MATERIAL"
  assert call["instrument_code"] == "600000.SH"
  assert page.page_info.end_cursor is not None

  repository.list_evaluations.reset_mock()
  repository.list_evaluations.return_value = [rows[1]]
  next_page = await TTradeResolver.list_signal_evaluations(
    "account-1",
    stock_code="600000.SH",
    event_kinds=[TTradeSignalEvaluationKind.MATERIAL],
    start_time=evaluated_at,
    end_time=evaluated_at,
    first=1,
    after=page.page_info.end_cursor,
  )

  next_call = repository.list_evaluations.await_args.kwargs
  assert next_call["cursor_evaluated_at"] == evaluated_at
  assert next_call["cursor_id"] == "b"
  assert next_page.items[0].id == "a"
  assert next_page.items[0].signal_snapshot is None


@pytest.mark.asyncio
async def test_diagnostics_is_explicitly_unavailable_without_truth_projection(
  monkeypatch,
) -> None:
  monkeypatch.setattr(TTradeResolver, "service", object())
  result = await TTradeResolver.signal_diagnostics(
    "account-1",
    stock_code=None,
    start_time=datetime(2026, 8, 22, tzinfo=timezone.utc),
    end_time=datetime(2026, 8, 23, tzinfo=timezone.utc),
  )

  assert result.available is False
  assert result.reason_code == "DIAGNOSTICS_PROJECTION_UNAVAILABLE"
  assert result.merged_versions is False
  assert result.partitions == []


@pytest.mark.asyncio
async def test_diagnostics_explicit_merge_flag_reaches_truth_projection(
  monkeypatch,
) -> None:
  provider = AsyncMock(
    return_value={
      "available": True,
      "merged_versions": True,
      "warnings": [],
      "partitions": [],
      "version_groups": [],
    }
  )
  monkeypatch.setattr(
    TTradeResolver,
    "service",
    SimpleNamespace(signal_diagnostics=provider),
  )
  start = datetime(2026, 8, 22, tzinfo=timezone.utc)
  end = datetime(2026, 8, 23, tzinfo=timezone.utc)

  result = await TTradeResolver.signal_diagnostics(
    "account-1",
    stock_code="600000.SH",
    start_time=start,
    end_time=end,
    merge_versions=True,
  )

  assert result.merged_versions is True
  provider.assert_awaited_once_with(
    "account-1",
    stock_code="600000.SH",
    start_time=start,
    end_time=end,
    merge_versions=True,
  )


def test_diagnostics_mapper_requires_ready_instrument_time_denominator() -> None:
  bounds = {
    "account_id": "account-1",
    "stock_code": None,
    "start_time": datetime(2026, 8, 22, tzinfo=timezone.utc),
    "end_time": datetime(2026, 8, 23, tzinfo=timezone.utc),
  }
  with pytest.raises(ValueError, match="READY_INSTRUMENT_SECONDS"):
    TTradeResolver._signal_diagnostics_type(
      {
        "available": True,
        "merged_versions": False,
        "warnings": [],
        "partitions": [
          {
            "policy_version": "v3",
            "feature_schema_version": "1",
            "profile_version": None,
            "denominator": {
              "code": "RAW_TICK_COUNT",
              "ready_instrument_seconds": 99,
            },
          }
        ],
      },
      **bounds,
    )

  mapped = TTradeResolver._signal_diagnostics_type(
    {
      "available": True,
      "merged_versions": False,
      "warnings": [],
      "partitions": [{
        "policy_version": "v3",
        "feature_schema_version": "1",
        "profile_version": None,
        "denominator": {
          "code": "READY_INSTRUMENT_SECONDS",
          "label": "READY 标的时长",
          "ready_instrument_seconds": 3600.0,
        },
        "funnel": [
          {
            "code": "ELIGIBLE",
            "label": "合格持仓评估",
            "unit_code": "MATERIAL_EVENTS",
            "denominator_code": None,
            "count": 4,
            "conversion_rate": None,
          },
          {
            "code": "DATA_READY",
            "label": "数据可决策",
            "unit_code": "MATERIAL_EVENTS",
            "denominator_code": "ELIGIBLE",
            "count": 4,
            "conversion_rate": 1.0,
          },
          {
            "code": "PATTERN",
            "label": "形态片段",
            "unit_code": "RUN_SCOPED_EPISODES",
            "denominator_code": "DATA_READY",
            "count": 3,
            "conversion_rate": 0.75,
          },
          {
            "code": "PREVIEW",
            "label": "越过预览阈值",
            "unit_code": "RUN_SCOPED_EPISODES",
            "denominator_code": "PATTERN",
            "count": 3,
            "conversion_rate": 1.0,
          },
          {
            "code": "CANDIDATE",
            "label": "候选",
            "unit_code": "RUN_SCOPED_CANDIDATES",
            "denominator_code": "PREVIEW",
            "count": 2,
            "conversion_rate": 0.666667,
          },
          {
            "code": "TRADE_INTENT",
            "label": "待确认意图",
            "unit_code": "TRADE_INTENTS",
            "denominator_code": "CANDIDATE",
            "count": 2,
            "conversion_rate": 1.0,
          },
          {
            "code": "APPROVED",
            "label": "人工确认",
            "unit_code": "APPROVED_INTENTS",
            "denominator_code": "TRADE_INTENT",
            "count": 1,
            "conversion_rate": 0.5,
          },
          {
            "code": "ORDERED",
            "label": "已下单",
            "unit_code": "ORDERS",
            "denominator_code": "APPROVED",
            "count": 1,
            "conversion_rate": 1.0,
          },
          {
            "code": "FILLED",
            "label": "已成交",
            "unit_code": "FILLS",
            "denominator_code": "ORDERED",
            "count": 1,
            "conversion_rate": 1.0,
          },
        ],
        "blockers": [
          {
            "blocker": {"code": "QUOTE_STALE", "label": "行情陈旧"},
            "count": 2,
            "rate": 0.5,
            "denominator_code": "MATERIAL_EVENTS",
            "denominator_value": 4,
          }
        ],
        "score_distribution": [
          {
            "policy_version": "v3",
            "feature_schema_version": "1",
            "profile_version": None,
            "path": "PULLBACK_REBOUND",
            "lower_bound": 55,
            "upper_bound": 72,
            "count": 2,
          }
        ],
        "fsm_dwell": [
          {
            "branch": "PULLBACK",
            "phase": "OBSERVING",
            "duration_seconds": 8,
            "transition_count": 1,
          }
        ],
        "fsm_transitions": [
          {
            "branch": "PULLBACK",
            "from_phase": "OBSERVING",
            "to_phase": "PULLBACK_FORMING",
            "count": 1,
          }
        ],
        "candidate_outcomes": [
          {"code": "EXPIRED", "label": "已过期", "count": 1}
        ],
        "post_candidate_performance": {
          "available": False,
          "reason_code": "POST_FILL_CAUSAL_PATH_AND_COST_LEDGER_UNAVAILABLE",
          "reason": "缺少权威数据链",
          "sample_count": 0,
          "net_mfe_pct": None,
          "net_mae_pct": None,
          "fixed_window_returns": [],
          "required_data_codes": ["AUTHORITATIVE_EXECUTION_FEE_LEDGER"],
        },
      }],
      "version_groups": [
        {
          "policy_version": "v3",
          "feature_schema_version": "1",
          "profile_version": None,
          "count": 2,
        }
      ],
    },
    **bounds,
  )

  assert mapped.available is True
  assert mapped.partitions[0].denominator.ready_instrument_seconds == 3600.0
  assert mapped.partitions[0].funnel[4].count == 2
  assert mapped.partitions[0].funnel[4].unit_code == "RUN_SCOPED_CANDIDATES"
  assert mapped.partitions[0].blockers[0].denominator_code == "MATERIAL_EVENTS"
  assert mapped.partitions[0].score_distribution[0].policy_version == "v3"
  assert mapped.partitions[0].fsm_transitions[0].from_phase == "OBSERVING"
  assert mapped.partitions[0].post_candidate_performance.available is False


def _diagnostics_payload_with_performance(performance: dict) -> dict:
  stages = (
    ("ELIGIBLE", "MATERIAL_EVENTS"),
    ("DATA_READY", "MATERIAL_EVENTS"),
    ("PATTERN", "RUN_SCOPED_EPISODES"),
    ("PREVIEW", "RUN_SCOPED_EPISODES"),
    ("CANDIDATE", "RUN_SCOPED_CANDIDATES"),
    ("TRADE_INTENT", "TRADE_INTENTS"),
    ("APPROVED", "APPROVED_INTENTS"),
    ("ORDERED", "ORDERS"),
    ("FILLED", "FILLS"),
  )
  funnel = []
  previous_code = None
  for code, unit_code in stages:
    funnel.append(
      {
        "code": code,
        "label": code,
        "unit_code": unit_code,
        "denominator_code": previous_code,
        "count": 0,
        "conversion_rate": None,
      }
    )
    previous_code = code
  return {
    "available": True,
    "merged_versions": False,
    "warnings": [],
    "partitions": [
      {
        "policy_version": "v3",
        "feature_schema_version": "1",
        "profile_version": None,
        "denominator": {
          "code": "READY_INSTRUMENT_SECONDS",
          "label": "READY 标的时长",
          "ready_instrument_seconds": 0,
        },
        "funnel": funnel,
        "blockers": [],
        "score_distribution": [],
        "fsm_dwell": [],
        "fsm_transitions": [],
        "candidate_outcomes": [],
        "post_candidate_performance": performance,
      }
    ],
    "version_groups": [
      {
        "policy_version": "v3",
        "feature_schema_version": "1",
        "profile_version": None,
        "count": 0,
      }
    ],
  }


@pytest.mark.parametrize(
  "override",
  [
    {"sample_count": 1},
    {"net_mfe_pct": 0.1},
    {
      "fixed_window_returns": [
        {
          "window_seconds": 60,
          "sample_count": 1,
          "average_net_return_pct": 0.1,
        }
      ]
    },
  ],
)
def test_diagnostics_mapper_rejects_unavailable_performance_with_results(
  override: dict,
) -> None:
  performance = {
    "available": False,
    "reason_code": "AUTHORITATIVE_DATA_UNAVAILABLE",
    "reason": "缺少权威数据",
    "sample_count": 0,
    "net_mfe_pct": None,
    "net_mae_pct": None,
    "fixed_window_returns": [],
    "required_data_codes": ["AUTHORITATIVE_EXECUTION_FEE_LEDGER"],
  }
  performance.update(override)

  with pytest.raises(ValueError, match="不得携带样本、收益或固定窗口数据"):
    TTradeResolver._signal_diagnostics_type(
      _diagnostics_payload_with_performance(performance),
      account_id="account-1",
      stock_code=None,
      start_time=datetime(2026, 8, 22, tzinfo=timezone.utc),
      end_time=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
  "performance, error",
  [
    (
      {
        "available": True,
        "reason_code": None,
        "reason": None,
        "sample_count": 0,
        "net_mfe_pct": None,
        "net_mae_pct": None,
        "fixed_window_returns": [],
        "required_data_codes": [],
      },
      "必须包含样本",
    ),
    (
      {
        "available": True,
        "reason_code": "CONTRADICTORY_REASON",
        "reason": "不应存在",
        "sample_count": 1,
        "net_mfe_pct": 0.1,
        "net_mae_pct": -0.1,
        "fixed_window_returns": [],
        "required_data_codes": [],
      },
      "不得携带失败原因",
    ),
    (
      {
        "available": True,
        "reason_code": None,
        "reason": None,
        "sample_count": 1,
        "net_mfe_pct": 0.1,
        "net_mae_pct": -0.1,
        "fixed_window_returns": [],
        "required_data_codes": ["AUTHORITATIVE_EXECUTION_FEE_LEDGER"],
      },
      "不能再声明缺失权威数据",
    ),
  ],
)
def test_diagnostics_mapper_rejects_contradictory_available_performance(
  performance: dict,
  error: str,
) -> None:
  with pytest.raises(ValueError, match=error):
    TTradeResolver._signal_diagnostics_type(
      _diagnostics_payload_with_performance(performance),
      account_id="account-1",
      stock_code=None,
      start_time=datetime(2026, 8, 22, tzinfo=timezone.utc),
      end_time=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )


def test_diagnostics_mapper_rejects_score_bucket_from_another_partition() -> None:
  unavailable_performance = {
    "available": False,
    "reason_code": "AUTHORITATIVE_DATA_UNAVAILABLE",
    "reason": "缺少权威数据",
    "sample_count": 0,
    "net_mfe_pct": None,
    "net_mae_pct": None,
    "fixed_window_returns": [],
    "required_data_codes": ["AUTHORITATIVE_EXECUTION_FEE_LEDGER"],
  }
  payload = _diagnostics_payload_with_performance(unavailable_performance)
  second_partition = copy.deepcopy(payload["partitions"][0])
  second_partition["policy_version"] = "v4"
  payload["partitions"].append(second_partition)
  payload["version_groups"].append(
    {
      "policy_version": "v4",
      "feature_schema_version": "1",
      "profile_version": None,
      "count": 0,
    }
  )
  payload["partitions"][0]["score_distribution"] = [
    {
      "policy_version": "v4",
      "feature_schema_version": "1",
      "profile_version": None,
      "path": "PULLBACK_REBOUND",
      "lower_bound": 55,
      "upper_bound": 72,
      "count": 1,
    }
  ]

  with pytest.raises(ValueError, match="与所在诊断分区一致"):
    TTradeResolver._signal_diagnostics_type(
      payload,
      account_id="account-1",
      stock_code=None,
      start_time=datetime(2026, 8, 22, tzinfo=timezone.utc),
      end_time=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )


def test_schema_atomically_exposes_v3_signal_contract() -> None:
  sdl = schema.as_str()

  assert "signalSnapshot: TTradeSignalSnapshot" in sdl
  assert "currentSignal:" not in sdl
  assert "latestEvaluation:" not in sdl
  assert "tTradeSignalEvaluations(" in sdl
  assert "tTradeSignalDiagnostics(" in sdl
  assert "mergeVersions: Boolean! = false" in sdl
  assert "partitions: [TTradeSignalDiagnosticPartition!]!" in sdl
  assert "readyInstrumentSeconds: Float!" in sdl
  assert "denominatorCode: String!" in sdl
  assert "denominatorValue: Float!" in sdl
  assert "unitCode: String!" in sdl
  assert "fsmTransitions: [TTradeSignalFsmTransition!]!" in sdl
  assert "fromPhase: String!" in sdl
  assert "postCandidatePerformance: TTradePostCandidatePerformance!" in sdl
  assert "requiredDataCodes: [String!]!" in sdl
  assert "tTradeSignalHistory(" not in sdl
  assert "tTradeSignalHistoryPage(" not in sdl
  assert "previewTTradeSignalPolicy(" in sdl
  assert "expectedConfigVersion: Int!" in sdl
  assert "signalPolicy: TTradeSignalPolicyInput!" in sdl
  assert "candidateTtlSeconds: Int!" in sdl
  assert "approvalTtlSeconds" not in sdl
  assert "expectation: TTradeCandidateApprovalExpectationInput!" in sdl
