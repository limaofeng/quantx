"""Bounded GraphQL phase, field-resolver, and SQL timing instrumentation."""

from __future__ import annotations

import contextvars
import hashlib
import inspect
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from graphql import get_operation_ast
from quantx_infrastructure.database.relational_connection import engine
from sqlalchemy import event
from starlette.requests import Request
from strawberry.extensions import SchemaExtension

from quantx_api.monitoring.metrics import (
  GRAPHQL_FIELD_RESOLVER_INVOCATIONS,
  GRAPHQL_FIELD_RESOLVER_REQUEST_DURATION,
  GRAPHQL_PHASE_DURATION,
  GRAPHQL_SQL_REQUEST_DURATION,
  GRAPHQL_SQL_STATEMENTS,
)

logger = logging.getLogger(__name__)

_SLOW_OPERATION_SECONDS = 1.0
_MAX_FIELD_AGGREGATES = 512
_MAX_EXPORTED_FIELDS = 100
_MAX_SQL_AGGREGATES = 128
_MAX_EXPORTED_SQL = 20
_MAX_REPRESENTATIVE_PATHS = 3
_SERVER_TIMING_FIELD_LIMIT = 6
_SQL_KIND_PATTERN = re.compile(
  r"\b(SELECT|INSERT|UPDATE|DELETE|WITH|CALL|MERGE|CREATE|ALTER|DROP)\b",
  re.IGNORECASE,
)
_PATH_SEGMENT_PATTERN = re.compile(r"[^A-Za-z0-9_]", re.ASCII)


@dataclass
class _FieldAggregate:
  parent_type: str
  field_name: str
  count: int = 0
  total_seconds: float = 0.0
  max_seconds: float = 0.0
  errors: int = 0
  paths: list[str] = field(default_factory=list)

  def record(self, duration: float, *, failed: bool, path: str) -> None:
    self.count += 1
    self.total_seconds += duration
    self.max_seconds = max(self.max_seconds, duration)
    if failed:
      self.errors += 1
    if path and path not in self.paths and len(self.paths) < _MAX_REPRESENTATIVE_PATHS:
      self.paths.append(path)


@dataclass
class _SqlAggregate:
  kind: str
  fingerprint: str
  count: int = 0
  total_seconds: float = 0.0
  max_seconds: float = 0.0
  errors: int = 0

  def record(self, duration: float, *, failed: bool) -> None:
    self.count += 1
    self.total_seconds += duration
    self.max_seconds = max(self.max_seconds, duration)
    if failed:
      self.errors += 1


@dataclass
class GraphQLRequestTrace:
  """One operation trace with bounded, schema-safe aggregate dimensions."""

  request_id: str
  operation_name: str
  started_at: float = field(default_factory=time.perf_counter)
  operation_type: str = "Unknown"
  total_seconds: float = 0.0
  phases: dict[str, float] = field(default_factory=dict)
  fields: dict[tuple[str, str], _FieldAggregate] = field(default_factory=dict)
  sql: dict[tuple[str, str], _SqlAggregate] = field(default_factory=dict)
  field_invocations: int = 0
  field_total_seconds: float = 0.0
  sql_count: int = 0
  sql_total_seconds: float = 0.0
  sql_max_seconds: float = 0.0
  sql_errors: int = 0
  dropped_field_aggregates: int = 0
  dropped_sql_aggregates: int = 0
  active: bool = True

  def add_phase(self, name: str, duration: float) -> None:
    self.phases[name] = self.phases.get(name, 0.0) + max(0.0, duration)

  def record_field(
    self,
    *,
    parent_type: str,
    field_name: str,
    path: str,
    duration: float,
    failed: bool,
  ) -> None:
    if not self.active:
      return
    duration = max(0.0, duration)
    self.field_invocations += 1
    self.field_total_seconds += duration
    key = (parent_type, field_name)
    aggregate = self.fields.get(key)
    if aggregate is None:
      if len(self.fields) >= _MAX_FIELD_AGGREGATES:
        self.dropped_field_aggregates += 1
        return
      aggregate = _FieldAggregate(parent_type=parent_type, field_name=field_name)
      self.fields[key] = aggregate
    aggregate.record(duration, failed=failed, path=path)

  def record_sql(self, statement: str, duration: float, *, failed: bool) -> None:
    if not self.active:
      return
    duration = max(0.0, duration)
    kind = _sql_kind(statement)
    fingerprint = _sql_fingerprint(statement)
    self.sql_count += 1
    self.sql_total_seconds += duration
    self.sql_max_seconds = max(self.sql_max_seconds, duration)
    if failed:
      self.sql_errors += 1
    key = (kind, fingerprint)
    aggregate = self.sql.get(key)
    if aggregate is None:
      if len(self.sql) >= _MAX_SQL_AGGREGATES:
        self.dropped_sql_aggregates += 1
        return
      aggregate = _SqlAggregate(kind=kind, fingerprint=fingerprint)
      self.sql[key] = aggregate
    aggregate.record(duration, failed=failed)

  def finish(self) -> None:
    if not self.active:
      return
    self.total_seconds = max(0.0, time.perf_counter() - self.started_at)
    self.active = False
    GRAPHQL_PHASE_DURATION.labels(phase="operation").observe(self.total_seconds)
    for phase, duration in self.phases.items():
      GRAPHQL_PHASE_DURATION.labels(phase=phase).observe(duration)
    for aggregate in self.fields.values():
      labels = {
        "parent_type": aggregate.parent_type,
        "field": aggregate.field_name,
      }
      successes = max(0, aggregate.count - aggregate.errors)
      if successes:
        GRAPHQL_FIELD_RESOLVER_INVOCATIONS.labels(**labels, outcome="success").inc(
          successes
        )
      if aggregate.errors:
        GRAPHQL_FIELD_RESOLVER_INVOCATIONS.labels(**labels, outcome="error").inc(
          aggregate.errors
        )
      GRAPHQL_FIELD_RESOLVER_REQUEST_DURATION.labels(
        **labels, statistic="total"
      ).observe(aggregate.total_seconds)
      GRAPHQL_FIELD_RESOLVER_REQUEST_DURATION.labels(**labels, statistic="max").observe(
        aggregate.max_seconds
      )
    sql_by_kind: dict[str, _SqlAggregate] = {}
    for aggregate in self.sql.values():
      by_kind = sql_by_kind.setdefault(
        aggregate.kind,
        _SqlAggregate(kind=aggregate.kind, fingerprint=""),
      )
      by_kind.count += aggregate.count
      by_kind.total_seconds += aggregate.total_seconds
      by_kind.max_seconds = max(by_kind.max_seconds, aggregate.max_seconds)
      by_kind.errors += aggregate.errors
    for aggregate in sql_by_kind.values():
      successes = max(0, aggregate.count - aggregate.errors)
      if successes:
        GRAPHQL_SQL_STATEMENTS.labels(kind=aggregate.kind, outcome="success").inc(
          successes
        )
      if aggregate.errors:
        GRAPHQL_SQL_STATEMENTS.labels(kind=aggregate.kind, outcome="error").inc(
          aggregate.errors
        )
      GRAPHQL_SQL_REQUEST_DURATION.labels(
        kind=aggregate.kind, statistic="total"
      ).observe(aggregate.total_seconds)
      GRAPHQL_SQL_REQUEST_DURATION.labels(kind=aggregate.kind, statistic="max").observe(
        aggregate.max_seconds
      )

  def payload(self) -> dict[str, Any]:
    sorted_fields = sorted(
      self.fields.values(),
      key=lambda item: (item.total_seconds, item.max_seconds),
      reverse=True,
    )
    sorted_sql = sorted(
      self.sql.values(),
      key=lambda item: (item.total_seconds, item.max_seconds),
      reverse=True,
    )
    return {
      "requestId": self.request_id,
      "operationName": self.operation_name,
      "operationType": self.operation_type,
      "totalMs": _milliseconds(self.total_seconds),
      "phases": {
        f"{name}Ms": _milliseconds(duration)
        for name, duration in sorted(self.phases.items())
      },
      "fieldInvocations": self.field_invocations,
      "fieldResolverMs": _milliseconds(self.field_total_seconds),
      "fieldCount": len(self.fields),
      "fieldsTruncated": bool(
        self.dropped_field_aggregates or len(self.fields) > _MAX_EXPORTED_FIELDS
      ),
      "fields": [
        {
          "parentType": item.parent_type,
          "field": item.field_name,
          "paths": item.paths,
          "count": item.count,
          "totalMs": _milliseconds(item.total_seconds),
          "maxMs": _milliseconds(item.max_seconds),
          "errors": item.errors,
        }
        for item in sorted_fields[:_MAX_EXPORTED_FIELDS]
      ],
      "sql": {
        "count": self.sql_count,
        "totalMs": _milliseconds(self.sql_total_seconds),
        "maxMs": _milliseconds(self.sql_max_seconds),
        "errors": self.sql_errors,
        "statementCount": len(self.sql),
        "statementsTruncated": bool(
          self.dropped_sql_aggregates or len(self.sql) > _MAX_EXPORTED_SQL
        ),
        "statements": [
          {
            "kind": item.kind,
            "fingerprint": item.fingerprint,
            "count": item.count,
            "totalMs": _milliseconds(item.total_seconds),
            "maxMs": _milliseconds(item.max_seconds),
            "errors": item.errors,
          }
          for item in sorted_sql[:_MAX_EXPORTED_SQL]
        ],
      },
    }


_current_trace: contextvars.ContextVar[GraphQLRequestTrace | None] = (
  contextvars.ContextVar("quantx_graphql_request_trace", default=None)
)
_current_http_request: contextvars.ContextVar[Request | None] = contextvars.ContextVar(
  "quantx_graphql_http_request", default=None
)


class GraphQLPerformanceExtension(SchemaExtension):
  """Measure GraphQL lifecycle phases and every concrete field resolver."""

  trace: GraphQLRequestTrace | None = None

  async def on_operation(self):
    context = self.execution_context.context
    context_dict = context if isinstance(context, dict) else {}
    request_id = str(context_dict.get("request_id") or "unknown")[:64]
    operation_name = str(self.execution_context.operation_name or "Anonymous")[:80]
    self.trace = GraphQLRequestTrace(
      request_id=request_id,
      operation_name=operation_name,
    )
    token = _current_trace.set(self.trace)
    try:
      yield
    finally:
      operation = _operation_ast(self.execution_context)
      self.trace.operation_type = str(
        getattr(getattr(operation, "operation", None), "value", None) or "Unknown"
      ).capitalize()
      self.trace.operation_name = str(
        getattr(getattr(operation, "name", None), "value", None)
        or self.trace.operation_name
      )[:80]
      self.trace.finish()
      _current_trace.reset(token)
      request = context_dict.get("request")
      if isinstance(request, Request):
        traces = getattr(request.state, "graphql_timing_traces", None)
        if not isinstance(traces, list):
          traces = []
          request.state.graphql_timing_traces = traces
        traces.append(self.trace)
      if self.trace.total_seconds >= _SLOW_OPERATION_SECONDS:
        logger.warning(
          "GraphQL slow operation: operation=%s type=%s total=%.3fs "
          "parse=%.3fs validate=%.3fs execute=%.3fs sql=%.3fs "
          "sql_count=%d fields=%s request_id=%s",
          self.trace.operation_name,
          self.trace.operation_type,
          self.trace.total_seconds,
          self.trace.phases.get("parse", 0.0),
          self.trace.phases.get("validate", 0.0),
          self.trace.phases.get("execute", 0.0),
          self.trace.sql_total_seconds,
          self.trace.sql_count,
          _slow_field_summary(self.trace),
          self.trace.request_id,
        )

  async def on_parse(self):
    async with _phase_timer(self.trace, "parse"):
      yield

  async def on_validate(self):
    async with _phase_timer(self.trace, "validate"):
      yield

  async def on_execute(self):
    async with _phase_timer(self.trace, "execute"):
      yield

  async def resolve(self, _next, root, info, *args, **kwargs):
    trace = _current_trace.get()
    field_name = str(getattr(info, "field_name", "Unknown"))[:128]
    parent_type = str(getattr(getattr(info, "parent_type", None), "name", "Unknown"))[
      :128
    ]
    if trace is None or field_name.startswith("__") or parent_type.startswith("__"):
      result = _next(root, info, *args, **kwargs)
      return await result if inspect.isawaitable(result) else result

    started_at = time.perf_counter()
    failed = False
    try:
      result = _next(root, info, *args, **kwargs)
      return await result if inspect.isawaitable(result) else result
    except BaseException:
      failed = True
      raise
    finally:
      trace.record_field(
        parent_type=parent_type,
        field_name=field_name,
        path=_normalized_path(info, field_name),
        duration=time.perf_counter() - started_at,
        failed=failed,
      )

  def get_results(self) -> dict[str, Any]:
    if self.trace is None:
      return {}
    context = self.execution_context.context
    context_dict = context if isinstance(context, dict) else {}
    request = context_dict.get("request")
    if (
      not isinstance(request, Request)
      or request.headers.get("x-quantx-debug-timing") != "1"
      or context_dict.get("principal") is None
    ):
      return {}
    return {"quantxTiming": self.trace.payload()}


class _phase_timer:
  def __init__(self, trace: GraphQLRequestTrace | None, name: str) -> None:
    self.trace = trace
    self.name = name
    self.started_at = 0.0

  async def __aenter__(self) -> None:
    self.started_at = time.perf_counter()

  async def __aexit__(self, exc_type, exc, traceback) -> None:
    if self.trace is not None:
      self.trace.add_phase(self.name, time.perf_counter() - self.started_at)


def bind_graphql_http_request(request: Request) -> contextvars.Token:
  return _current_http_request.set(request)


def reset_graphql_http_request(token: contextvars.Token) -> None:
  _current_http_request.reset(token)


def current_graphql_http_request() -> Request | None:
  return _current_http_request.get()


def record_graphql_transport_phase(
  request: Request,
  phase: str,
  duration_seconds: float,
) -> None:
  duration_seconds = max(0.0, duration_seconds)
  state_name = f"graphql_{phase}_seconds"
  previous = getattr(request.state, state_name, 0.0)
  setattr(request.state, state_name, previous + duration_seconds)
  GRAPHQL_PHASE_DURATION.labels(phase=phase).observe(duration_seconds)


def graphql_server_timing_header(
  request: Request,
  *,
  admission_wait_seconds: float | None,
  transport_seconds: float,
) -> str:
  traces = getattr(request.state, "graphql_timing_traces", [])
  valid_traces = [trace for trace in traces if isinstance(trace, GraphQLRequestTrace)]
  entries: list[str] = []

  def add(name: str, duration: float, description: str | None = None) -> None:
    value = f"{name};dur={_milliseconds(duration):.3f}"
    if description:
      safe_description = description.replace("\\", "").replace('"', "")[:128]
      value += f';desc="{safe_description}"'
    entries.append(value)

  if admission_wait_seconds is not None:
    add("gql-admission", admission_wait_seconds)
  for phase in ("context", "format", "serialize"):
    duration = getattr(request.state, f"graphql_{phase}_seconds", None)
    if isinstance(duration, (int, float)):
      add(f"gql-{phase}", float(duration))
  for phase in ("parse", "validate", "execute"):
    duration = sum(trace.phases.get(phase, 0.0) for trace in valid_traces)
    if valid_traces:
      add(f"gql-{phase}", duration)
  if valid_traces:
    add("gql-sql", sum(trace.sql_total_seconds for trace in valid_traces))
    add("gql-total", sum(trace.total_seconds for trace in valid_traces))
    fields: dict[tuple[str, str], _FieldAggregate] = {}
    for trace in valid_traces:
      for key, aggregate in trace.fields.items():
        merged = fields.setdefault(
          key,
          _FieldAggregate(
            parent_type=aggregate.parent_type,
            field_name=aggregate.field_name,
          ),
        )
        merged.count += aggregate.count
        merged.total_seconds += aggregate.total_seconds
        merged.max_seconds = max(merged.max_seconds, aggregate.max_seconds)
        merged.errors += aggregate.errors
    top_fields = sorted(
      fields.values(), key=lambda item: item.total_seconds, reverse=True
    )[:_SERVER_TIMING_FIELD_LIMIT]
    for index, aggregate in enumerate(top_fields):
      add(
        f"gql-field-{index}",
        aggregate.total_seconds,
        f"{aggregate.parent_type}.{aggregate.field_name} ({aggregate.count}x)",
      )
  add("graphql-http", max(0.0, transport_seconds))
  return ", ".join(entries)


def _operation_ast(execution_context: Any) -> Any:
  try:
    return get_operation_ast(
      execution_context.graphql_document,
      execution_context.operation_name,
    )
  except Exception:
    return None


def _normalized_path(info: Any, field_name: str) -> str:
  path = getattr(info, "path", None)
  try:
    raw_segments = path.as_list()
  except Exception:
    return field_name
  segments: list[str] = []
  for index, segment in enumerate(raw_segments):
    if isinstance(segment, int):
      if not segments or segments[-1] != "[]":
        segments.append("[]")
      continue
    value = field_name if index == len(raw_segments) - 1 else str(segment)
    value = _PATH_SEGMENT_PATTERN.sub("_", value)[:64]
    segments.append(value or "field")
  return ".".join(segments)[:256]


def _sql_kind(statement: str) -> str:
  match = _SQL_KIND_PATTERN.search(str(statement)[:512])
  return match.group(1).upper() if match else "OTHER"


def _sql_fingerprint(statement: str) -> str:
  normalized = " ".join(str(statement).split())
  return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


def _milliseconds(seconds: float) -> float:
  return round(max(0.0, seconds) * 1000.0, 3)


def _slow_field_summary(trace: GraphQLRequestTrace) -> str:
  fields = sorted(
    trace.fields.values(), key=lambda item: item.total_seconds, reverse=True
  )[:8]
  return (
    ",".join(
      f"{item.parent_type}.{item.field_name}:{item.total_seconds:.3f}s/{item.count}x"
      for item in fields
    )
    or "none"
  )


def _before_cursor_execute(
  conn: Any,
  cursor: Any,
  statement: str,
  parameters: Any,
  context: Any,
  executemany: bool,
) -> None:
  trace = _current_trace.get()
  if trace is not None and trace.active:
    context._quantx_graphql_sql_started_at = time.perf_counter()


def _pop_sql_started_at(context: Any) -> float | None:
  started_at = getattr(context, "_quantx_graphql_sql_started_at", None)
  if started_at is not None:
    try:
      delattr(context, "_quantx_graphql_sql_started_at")
    except AttributeError:
      pass
  return started_at if isinstance(started_at, (int, float)) else None


def _record_sql(context: Any, statement: str, *, failed: bool) -> None:
  started_at = _pop_sql_started_at(context)
  trace = _current_trace.get()
  if started_at is None or trace is None or not trace.active:
    return
  trace.record_sql(
    statement,
    time.perf_counter() - started_at,
    failed=failed,
  )


def _after_cursor_execute(
  conn: Any,
  cursor: Any,
  statement: str,
  parameters: Any,
  context: Any,
  executemany: bool,
) -> None:
  _record_sql(context, statement, failed=False)


def _handle_sql_error(exception_context: Any) -> None:
  context = getattr(exception_context, "execution_context", None)
  if context is None:
    return
  statement = str(getattr(context, "statement", "") or "")
  _record_sql(context, statement, failed=True)


def _install_sql_timing_listeners() -> None:
  sync_engine = engine.sync_engine
  marker = "_quantx_graphql_timing_listeners_installed"
  if getattr(sync_engine, marker, False):
    return
  event.listen(sync_engine, "before_cursor_execute", _before_cursor_execute)
  event.listen(sync_engine, "after_cursor_execute", _after_cursor_execute)
  event.listen(sync_engine, "handle_error", _handle_sql_error)
  setattr(sync_engine, marker, True)


_install_sql_timing_listeners()
