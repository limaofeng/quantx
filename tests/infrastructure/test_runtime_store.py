from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from quantx_infrastructure import runtime_store


class _ScalarResult:
  def scalar_one_or_none(self) -> str:
    return "request-1"


class _Connection:
  def __init__(self) -> None:
    self.parameters: dict[str, object] = {}
    self.statement = ""

  async def execute(self, statement, parameters):
    self.statement = str(statement)
    self.parameters = parameters
    return _ScalarResult()


class _Result:
  def __init__(self, value: str | None) -> None:
    self.value = value

  def scalar_one_or_none(self) -> str | None:
    return self.value


class _SequenceConnection:
  def __init__(self, values: list[str | None]) -> None:
    self.values = values
    self.calls: list[tuple[str, dict[str, object]]] = []

  async def execute(self, statement, parameters):
    self.calls.append((str(statement), parameters))
    return _Result(self.values.pop(0))


class _MappingResult:
  def __init__(self, value: dict[str, object] | None) -> None:
    self.value = value

  def mappings(self):
    return self

  def one_or_none(self) -> dict[str, object] | None:
    return self.value


class _MappingConnection:
  def __init__(self, value: dict[str, object] | None) -> None:
    self.value = value
    self.calls: list[tuple[str, dict[str, object]]] = []

  async def execute(self, statement, parameters):
    self.calls.append((str(statement), parameters))
    return _MappingResult(self.value)


class _Engine:
  def __init__(self, connection) -> None:
    self.connection = connection

  @asynccontextmanager
  async def begin(self):
    yield self.connection


class _BoundDeviceResult:
  def __init__(self, value=None, mapping=None) -> None:
    self.value = value
    self.mapping = mapping

  def scalar_one_or_none(self):
    return self.value

  def mappings(self):
    return self

  def one_or_none(self):
    return self.mapping


class _BoundDeviceConnection:
  def __init__(self, capabilities=None) -> None:
    self.calls: list[tuple[str, dict[str, object] | None]] = []
    self.capabilities = capabilities or ["market-data", "data-only"]

  async def execute(self, statement, parameters=None):
    sql = str(statement)
    self.calls.append((sql, parameters))
    if "WHERE idempotency_key" in sql:
      return _BoundDeviceResult()
    if "FROM agent_devices" in sql:
      return _BoundDeviceResult(
        mapping={
          "id": "device-data-only",
          "capabilities": self.capabilities,
        }
      )
    return _BoundDeviceResult()


@pytest.mark.asyncio
async def test_market_data_request_binds_an_explicit_capable_device() -> None:
  connection = _BoundDeviceConnection()
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  request_id = await store.create_market_data_request(
    {"operation": "bars"},
    device_id="device-data-only",
  )

  assert request_id
  device_lookup = next(
    call for call in connection.calls if "FROM agent_devices" in call[0]
  )
  assert device_lookup[1] == {"device_id": "device-data-only"}
  insert = next(
    call for call in connection.calls if "INSERT INTO market_data_request" in call[0]
  )
  assert insert[1]["device_id"] == "device-data-only"


@pytest.mark.asyncio
async def test_market_data_request_requires_financial_protocol_capability() -> None:
  connection = _BoundDeviceConnection(["market-data", "data-only"])
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  with pytest.raises(RuntimeError, match="financial-data-v1"):
    await store.create_market_data_request(
      {"operation": "financial_data"},
      device_id="device-data-only",
      required_capabilities=["financial-data-v1"],
    )


@pytest.mark.asyncio
async def test_claim_market_data_request_uses_precomputed_stale_cutoff(
  monkeypatch,
) -> None:
  now = datetime(2026, 7, 29, 8, 5, 11)
  monkeypatch.setattr(runtime_store, "_utcnow", lambda: now)
  connection = _Connection()
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  claimed = await store.claim_market_data_request("request-1")

  assert claimed is True
  assert "updated_at < :stale_before" in connection.statement
  assert "INTERVAL" not in connection.statement
  assert connection.parameters == {
    "request_id": "request-1",
    "updated_at": now,
    "stale_before": now - timedelta(minutes=5),
  }


@pytest.mark.asyncio
async def test_finish_market_data_request_writes_unambiguous_terminal_state(
  monkeypatch,
) -> None:
  now = datetime(2026, 7, 29, 8, 7, 17)
  monkeypatch.setattr(runtime_store, "_utcnow", lambda: now)
  connection = _Connection()
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  await store.finish_market_data_request(
    "request-1",
    status="FAILED",
    error="transfer failed",
  )

  assert "CASE" not in connection.statement
  assert "completed_at = :completed_at" in connection.statement
  assert "status NOT IN ('COMPLETED', 'FAILED')" in connection.statement
  assert "RETURNING status" in connection.statement
  assert connection.parameters == {
    "request_id": "request-1",
    "status": "FAILED",
    "error": "transfer failed",
    "completed_at": now,
  }


@pytest.mark.asyncio
async def test_finish_market_data_request_reports_terminal_state_conflict() -> None:
  connection = _SequenceConnection([None, "FAILED"])
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  with pytest.raises(
    RuntimeError,
    match="existing=FAILED requested=COMPLETED",
  ):
    await store.finish_market_data_request(
      "request-1",
      status="COMPLETED",
    )

  assert len(connection.calls) == 2
  assert "status NOT IN ('COMPLETED', 'FAILED')" in connection.calls[0][0]
  assert "SELECT status" in connection.calls[1][0]


@pytest.mark.asyncio
async def test_reopen_failed_market_data_request_returns_atomic_evidence(
  monkeypatch,
) -> None:
  now = datetime(2026, 7, 30, 9, 31, 7)
  monkeypatch.setattr(runtime_store, "_utcnow", lambda: now)
  connection = _MappingConnection(
    {
      "request_id": "request-1",
      "status": "UPLOADED",
      "old_processing_error": "worker stopped after upload",
      "expected_chunks": 3,
      "received_chunks": 3,
      "manifest_count": 3,
      "manifest_records": 825,
    }
  )
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  evidence = await store.reopen_failed_market_data_request("request-1")

  assert evidence == {
    "request_id": "request-1",
    "status": "UPLOADED",
    "old_processing_error": "worker stopped after upload",
    "expected_chunks": 3,
    "received_chunks": 3,
    "manifest_count": 3,
    "manifest_records": 825,
  }
  assert len(connection.calls) == 1
  statement, parameters = connection.calls[0]
  assert "WITH candidate AS MATERIALIZED" in statement
  assert "FOR UPDATE OF market_request" in statement
  assert "UPDATE market_data_request AS market_request" in statement
  assert "SET status = 'UPLOADED'" in statement
  assert "processing_error = NULL" in statement
  assert "completed_at = NULL" in statement
  assert parameters == {
    "request_id": "request-1",
    "reopened_at": now,
  }


@pytest.mark.asyncio
async def test_reopen_failed_market_data_request_fails_closed() -> None:
  connection = _MappingConnection(None)
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  with pytest.raises(RuntimeError, match="not safely reopenable"):
    await store.reopen_failed_market_data_request("request-1")

  assert len(connection.calls) == 1
  statement, _ = connection.calls[0]
  assert "market_request.status = 'FAILED'" in statement
  assert "market_request.expected_chunks IS NOT NULL" in statement
  assert "market_request.expected_chunks > 0" in statement
  assert (
    "market_request.expected_chunks =\n"
    "                    market_request.received_chunks"
  ) in statement
  assert (
    "candidate.manifest_count =\n"
    "                    candidate.expected_chunks"
  ) in statement
  assert "candidate.manifest_records > 0" in statement
