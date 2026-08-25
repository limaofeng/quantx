"""Bound UI GraphQL query concurrency without partitioning the DB pool."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from graphql import OperationType, get_operation_ast, parse

from quantx_api.monitoring.metrics import (
  GRAPHQL_QUERY_ADMISSION_ACTIVE,
  GRAPHQL_QUERY_ADMISSION_REJECTIONS,
  GRAPHQL_QUERY_ADMISSION_WAIT,
)

logger = logging.getLogger(__name__)
GRAPHQL_QUERY_CONCURRENCY = 6
GRAPHQL_QUERY_ADMISSION_TIMEOUT_SECONDS = 3.0
GRAPHQL_SLOW_REQUEST_SECONDS = 1.0


@dataclass(frozen=True)
class GraphQLRequestIdentity:
  is_query: bool
  operation_name: str


def graphql_request_identity(body: bytes) -> GraphQLRequestIdentity:
  try:
    payload = json.loads(body)
    query = str(payload.get("query") or "")
    requested_name = str(payload.get("operationName") or "") or None
    operation = get_operation_ast(parse(query), requested_name)
  except Exception:
    # Invalid requests still pass to Strawberry for its canonical error shape.
    return GraphQLRequestIdentity(is_query=False, operation_name="Unknown")
  if operation is None:
    return GraphQLRequestIdentity(is_query=False, operation_name="Unknown")
  operation_name = (
    operation.name.value if operation.name is not None else "Anonymous"
  )[:80]
  return GraphQLRequestIdentity(
    is_query=operation.operation is OperationType.QUERY,
    operation_name=operation_name,
  )


class GraphQLQueryAdmission:
  def __init__(self, capacity: int = GRAPHQL_QUERY_CONCURRENCY) -> None:
    self._semaphore = asyncio.Semaphore(capacity)

  async def acquire(self) -> float | None:
    started = time.monotonic()
    try:
      await asyncio.wait_for(
        self._semaphore.acquire(),
        timeout=GRAPHQL_QUERY_ADMISSION_TIMEOUT_SECONDS,
      )
    except asyncio.TimeoutError:
      GRAPHQL_QUERY_ADMISSION_REJECTIONS.labels(reason="queue_timeout").inc()
      return None
    waited = max(0.0, time.monotonic() - started)
    GRAPHQL_QUERY_ADMISSION_WAIT.observe(waited)
    GRAPHQL_QUERY_ADMISSION_ACTIVE.inc()
    return waited

  def release(self) -> None:
    GRAPHQL_QUERY_ADMISSION_ACTIVE.dec()
    self._semaphore.release()


graphql_query_admission = GraphQLQueryAdmission()
