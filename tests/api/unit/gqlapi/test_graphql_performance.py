import asyncio
import time

import pytest
import strawberry
from fastapi import FastAPI
from fastapi.testclient import TestClient
from quantx_api.gqlapi.app import AuthenticatedGraphQLRouter
from quantx_api.gqlapi.performance import (
  GraphQLPerformanceExtension,
  GraphQLRequestTrace,
  graphql_server_timing_header,
  record_graphql_transport_phase,
)
from starlette.requests import HTTPConnection, Request


@strawberry.type
class TimingItem:
  value: str

  @strawberry.field
  def doubled(self) -> str:
    return self.value * 2


@strawberry.type
class TimingQuery:
  @strawberry.field
  async def items(self) -> list[TimingItem]:
    await asyncio.sleep(0)
    return [TimingItem(value="a"), TimingItem(value="b")]


TIMING_SCHEMA = strawberry.Schema(
  query=TimingQuery,
  extensions=[GraphQLPerformanceExtension],
)


async def _timing_context(request: HTTPConnection) -> dict[str, object]:
  return {
    "request": request,
    "request_id": "request-router",
    "principal": object(),
  }


def _request(*, debug_timing: bool = True) -> Request:
  headers = [(b"x-quantx-debug-timing", b"1")] if debug_timing else []
  return Request(
    {
      "type": "http",
      "method": "POST",
      "path": "/graphql",
      "headers": headers,
      "state": {},
    }
  )


@pytest.mark.asyncio
async def test_graphql_timing_aggregates_nested_field_resolvers() -> None:
  request = _request()
  result = await TIMING_SCHEMA.execute(
    """
    query TimingItems {
      primary: items { value doubled }
      secondary: items { value }
    }
    """,
    context_value={
      "request": request,
      "request_id": "request-timing",
      "principal": object(),
    },
  )

  assert result.errors is None
  timing = result.extensions["quantxTiming"]
  assert timing["requestId"] == "request-timing"
  assert timing["operationName"] == "TimingItems"
  assert timing["operationType"] == "Query"
  assert timing["phases"]["parseMs"] >= 0
  assert timing["phases"]["validateMs"] >= 0
  assert timing["phases"]["executeMs"] >= 0

  fields = {(item["parentType"], item["field"]): item for item in timing["fields"]}
  assert fields[("TimingQuery", "items")]["count"] == 2
  assert fields[("TimingItem", "value")]["count"] == 4
  assert fields[("TimingItem", "doubled")]["count"] == 2
  assert set(fields[("TimingItem", "value")]["paths"]) == {
    "primary.[].value",
    "secondary.[].value",
  }


@pytest.mark.asyncio
async def test_graphql_timing_details_require_authenticated_debug_request() -> None:
  result = await TIMING_SCHEMA.execute(
    "{ items { value } }",
    context_value={
      "request": _request(debug_timing=False),
      "request_id": "request-private",
      "principal": object(),
    },
  )

  assert result.errors is None
  assert not result.extensions


def test_graphql_timing_sql_payload_exposes_only_safe_fingerprint() -> None:
  trace = GraphQLRequestTrace(
    request_id="request-sql",
    operation_name="SqlTiming",
  )
  raw_statement = "SELECT secret_column FROM private_table WHERE account_id = $1"
  trace.record_sql(raw_statement, 0.125, failed=False)

  payload = trace.payload()
  statement = payload["sql"]["statements"][0]
  assert statement["kind"] == "SELECT"
  assert len(statement["fingerprint"]) == 16
  assert raw_statement not in str(payload)
  assert "private_table" not in str(payload)


def test_graphql_timing_marks_exported_aggregates_as_truncated() -> None:
  trace = GraphQLRequestTrace(
    request_id="request-bounded",
    operation_name="BoundedTiming",
  )
  for index in range(101):
    trace.record_field(
      parent_type="Query",
      field_name=f"field{index}",
      path=f"field{index}",
      duration=0.001,
      failed=False,
    )
  for index in range(21):
    trace.record_sql(f"SELECT {index}", 0.001, failed=False)

  payload = trace.payload()
  assert payload["fieldCount"] == 101
  assert len(payload["fields"]) == 100
  assert payload["fieldsTruncated"] is True
  assert payload["sql"]["statementCount"] == 21
  assert len(payload["sql"]["statements"]) == 20
  assert payload["sql"]["statementsTruncated"] is True


def test_server_timing_includes_phases_and_bounded_field_entries() -> None:
  request = _request()
  trace = GraphQLRequestTrace(
    request_id="request-header",
    operation_name="HeaderTiming",
  )
  trace.add_phase("parse", 0.002)
  trace.add_phase("validate", 0.003)
  trace.add_phase("execute", 0.030)
  trace.record_field(
    parent_type="Query",
    field_name="globalMonitor",
    path="globalMonitor",
    duration=0.025,
    failed=False,
  )
  trace.record_sql("SELECT 1", 0.010, failed=False)
  trace.finish()
  request.state.graphql_timing_traces = [trace]
  record_graphql_transport_phase(request, "context", 0.004)
  record_graphql_transport_phase(request, "format", 0.001)
  record_graphql_transport_phase(request, "serialize", 0.001)

  header = graphql_server_timing_header(
    request,
    admission_wait_seconds=0.005,
    transport_seconds=0.050,
  )

  assert "gql-admission;dur=5.000" in header
  assert "gql-context;dur=4.000" in header
  assert "gql-execute;dur=30.000" in header
  assert "gql-sql;dur=10.000" in header
  assert 'desc="Query.globalMonitor (1x)"' in header
  assert "graphql-http;dur=50.000" in header


def test_graphql_router_records_format_and_serialization_timing() -> None:
  app = FastAPI()
  router = AuthenticatedGraphQLRouter(
    TIMING_SCHEMA,
    allow_queries_via_get=False,
    context_getter=_timing_context,
  )
  app.include_router(router, prefix="/graphql")

  @app.middleware("http")
  async def add_server_timing(request: Request, call_next):
    started_at = time.monotonic()
    response = await call_next(request)
    response.headers["Server-Timing"] = graphql_server_timing_header(
      request,
      admission_wait_seconds=0.0,
      transport_seconds=time.monotonic() - started_at,
    )
    return response

  with TestClient(app) as client:
    response = client.post(
      "/graphql",
      headers={"X-QuantX-Debug-Timing": "1"},
      json={"query": "query RouterTiming { items { value } }"},
    )

  assert response.status_code == 200
  assert response.json()["extensions"]["quantxTiming"]["requestId"] == (
    "request-router"
  )
  assert "gql-format;dur=" in response.headers["Server-Timing"]
  assert "gql-serialize;dur=" in response.headers["Server-Timing"]
