from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_api.gqlapi.resolvers.t_trade as resolver_module
from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.resolvers.t_trade import TTradeResolver
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.types.t_trade_types import (
  TTradeCandidateTraceIntegrityStatus,
)
from quantx_infrastructure.services.t_trade_candidate_trace_service import (
  CandidateTrace,
  CandidateTraceEvent,
  CandidateTraceLinks,
  CandidateTraceMissingReason,
  CandidateTraceSourceIdentity,
)


class _DbContext:
  def __init__(self, db: object) -> None:
    self.db = db

  async def __aenter__(self) -> object:
    return self.db

  async def __aexit__(self, exc_type, exc, tb) -> bool:
    return False


class _DbFactory:
  def __init__(self, db: object) -> None:
    self.db = db

  def __call__(self) -> _DbContext:
    return _DbContext(self.db)


def _sdl_block(marker: str) -> str:
  rendered = schema.as_str()
  start = rendered.index(marker)
  return rendered[start : rendered.index("}", start) + 1]


def _trace() -> CandidateTrace:
  occurred_at = datetime(2026, 8, 23, 2, 3, 4, tzinfo=timezone.utc)
  return CandidateTrace(
    account_id="account-1",
    candidate_id="candidate-1",
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    source_evaluation_id="evaluation-1",
    source_identity=CandidateTraceSourceIdentity(
      source_time_ms=9_007_199_254_740_993,
      tick_ordinal=9_223_372_036_854_775_807,
      continuity_generation="generation-7",
      trade_date="2026-08-23",
      candidate_fingerprint="fingerprint-1",
      policy_version="t_trade_opportunity_v3.0.0",
      feature_schema_version="1",
      profile_version="2026-08-22.v1",
    ),
    integrity_status="IN_PROGRESS",
    missing_reasons=(
      CandidateTraceMissingReason(
        code="EXIT_PLAN_PENDING",
        stage="AUTO_EXIT_PLAN",
        expected=True,
        detail="入场成交后退出计划仍在创建中",
      ),
    ),
    links=CandidateTraceLinks(
      evaluation_ids=("evaluation-1",),
      intent_ids=("intent-1",),
      client_order_ids=("client-order-1",),
      correlation_ids=("correlation-1",),
      broker_order_ids=("broker-order-1",),
      order_ids=("order-1",),
      trade_ids=("trade-1",),
      batch_ids=("batch-1",),
      exit_plan_ids=("exit-plan-1",),
      exit_plan_event_ids=("exit-event-1",),
    ),
    events=(
      CandidateTraceEvent(
        stage="EVALUATION",
        event_type="CANDIDATE_LATCHED",
        entity_id="evaluation-1",
        occurred_at=occurred_at,
        status="MATERIAL",
        related_ids={"intent_ids": ("intent-1",)},
        details={"score": 78.5, "candidate_ready": True},
      ),
      CandidateTraceEvent(
        stage="BROKER_TRADE",
        event_type="TRADE_REPORTED",
        entity_id="trade-1",
        occurred_at=occurred_at,
        status=None,
        related_ids={"order_ids": ("order-1",)},
        details={"volume": 100},
      ),
    ),
  )


def test_candidate_trace_schema_exposes_complete_typed_contract() -> None:
  rendered = schema.as_str()
  trace = _sdl_block("type TTradeCandidateTrace {")
  source = _sdl_block("type TTradeCandidateTraceSourceIdentity {")
  missing = _sdl_block("type TTradeCandidateTraceMissingReason {")
  links = _sdl_block("type TTradeCandidateTraceLinks {")
  event = _sdl_block("type TTradeCandidateTraceEvent {")
  integrity = _sdl_block("enum TTradeCandidateTraceIntegrityStatus {")

  assert (
    "tTradeCandidateTrace(accountId: String!, strategyRunId: String!, candidateId: String!): "
    "TTradeCandidateTrace"
  ) in rendered
  for field in (
    "accountId: String!",
    "candidateId: String!",
    "strategyRunId: String!",
    "instrumentCode: String!",
    "sourceEvaluationId: String!",
    "sourceIdentity: TTradeCandidateTraceSourceIdentity!",
    "integrityStatus: TTradeCandidateTraceIntegrityStatus!",
    "missingReasons: [TTradeCandidateTraceMissingReason!]!",
    "links: TTradeCandidateTraceLinks!",
    "events: [TTradeCandidateTraceEvent!]!",
  ):
    assert field in trace
  for field in (
    "sourceTimeMs: String",
    "tickOrdinal: String",
    "continuityGeneration: String",
    "tradeDate: String",
    "candidateFingerprint: String",
    "policyVersion: String",
    "featureSchemaVersion: String",
    "profileVersion: String",
  ):
    assert field in source
  for field in (
    "code: String!",
    "stage: String!",
    "expected: Boolean!",
    "detail: String!",
  ):
    assert field in missing
  for field in (
    "evaluationIds: [String!]!",
    "intentIds: [String!]!",
    "clientOrderIds: [String!]!",
    "correlationIds: [String!]!",
    "brokerOrderIds: [String!]!",
    "orderIds: [String!]!",
    "tradeIds: [String!]!",
    "batchIds: [String!]!",
    "exitPlanIds: [String!]!",
    "exitPlanEventIds: [String!]!",
  ):
    assert field in links
  for field in (
    "stage: String!",
    "eventType: String!",
    "entityId: String!",
    "occurredAt: DateTime!",
    "status: String",
    "relatedIds: JSON!",
    "details: JSON!",
  ):
    assert field in event
  assert {"COMPLETE", "IN_PROGRESS", "BROKEN"} <= {
    line.strip() for line in integrity.splitlines()
  }


def test_candidate_trace_operation_requires_strategy_read() -> None:
  policy = operation_policy("Query", "tTradeCandidateTrace")

  assert policy.required_permissions == ("strategy:read",)
  assert policy.risk == "READ"


@pytest.mark.asyncio
async def test_candidate_trace_resolver_maps_complete_trace_and_large_identity(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  db = object()
  trace_service = SimpleNamespace(get_trace=AsyncMock(return_value=_trace()))

  def service_factory(actual_db: object):
    assert actual_db is db
    return trace_service

  monkeypatch.setattr(resolver_module, "AsyncSessionLocal", _DbFactory(db))
  monkeypatch.setattr(
    resolver_module,
    "TTradeCandidateTraceService",
    service_factory,
  )

  result = await TTradeResolver.candidate_trace(
    "account-1",
    "  run-1  ",
    "  candidate-1  ",
  )

  assert result is not None
  assert result.account_id == "account-1"
  assert result.candidate_id == "candidate-1"
  assert result.strategy_run_id == "run-1"
  assert result.instrument_code == "600000.SH"
  assert result.source_evaluation_id == "evaluation-1"
  assert result.source_identity.source_time_ms == "9007199254740993"
  assert result.source_identity.tick_ordinal == "9223372036854775807"
  assert result.source_identity.continuity_generation == "generation-7"
  assert result.integrity_status is TTradeCandidateTraceIntegrityStatus.IN_PROGRESS
  assert result.missing_reasons[0].expected is True
  assert result.links.exit_plan_event_ids == ["exit-event-1"]
  assert result.events[0].related_ids == {"intent_ids": ["intent-1"]}
  assert result.events[0].details == {"score": 78.5, "candidate_ready": True}
  assert result.events[1].status is None
  trace_service.get_trace.assert_awaited_once_with(
    account_id="account-1",
    strategy_run_id="run-1",
    candidate_id="candidate-1",
  )


@pytest.mark.asyncio
async def test_candidate_trace_resolver_returns_none_when_candidate_is_absent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  db = object()
  trace_service = SimpleNamespace(get_trace=AsyncMock(return_value=None))
  monkeypatch.setattr(resolver_module, "AsyncSessionLocal", _DbFactory(db))
  monkeypatch.setattr(
    resolver_module,
    "TTradeCandidateTraceService",
    lambda actual_db: trace_service,
  )

  result = await TTradeResolver.candidate_trace(
    "account-1",
    "run-1",
    "candidate-missing",
  )

  assert result is None
  trace_service.get_trace.assert_awaited_once_with(
    account_id="account-1",
    strategy_run_id="run-1",
    candidate_id="candidate-missing",
  )


@pytest.mark.asyncio
async def test_candidate_trace_resolver_rejects_blank_candidate_before_db_access(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  def unexpected_session():
    raise AssertionError("blank candidate must not access the database")

  monkeypatch.setattr(resolver_module, "AsyncSessionLocal", unexpected_session)

  with pytest.raises(ValueError, match="candidate_id is required"):
    await TTradeResolver.candidate_trace("account-1", "run-1", "   ")


@pytest.mark.asyncio
async def test_candidate_trace_resolver_rejects_blank_run_before_db_access(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  def unexpected_session():
    raise AssertionError("blank strategy run must not access the database")

  monkeypatch.setattr(resolver_module, "AsyncSessionLocal", unexpected_session)

  with pytest.raises(ValueError, match="strategy_run_id is required"):
    await TTradeResolver.candidate_trace("account-1", "   ", "candidate-1")
