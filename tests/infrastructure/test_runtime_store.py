import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from quantx_infrastructure import runtime_store


class _ScalarResult:
  def __init__(self, value: str = "request-1") -> None:
    self.value = value

  def scalar_one_or_none(self) -> str:
    return self.value


class _Connection:
  def __init__(self) -> None:
    self.parameters: dict[str, object] = {}
    self.statement = ""

  async def execute(self, statement, parameters):
    self.statement = str(statement)
    self.parameters = parameters
    return _ScalarResult(str(parameters.get("claim_token") or "request-1"))


class _Result:
  def __init__(self, value: str | None) -> None:
    self.value = value

  def scalar_one_or_none(self) -> str | None:
    return self.value

  def mappings(self):
    return self

  def one_or_none(self) -> dict[str, object] | None:
    if self.value is None:
      return None
    return {"status": self.value, "ingestion_result": None}


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


class _UnexpectedConnectEngine:
  def connect(self):
    raise AssertionError("blocked launch state must not query stale heartbeats")


class _AvailabilityResult:
  def __init__(self, rows) -> None:
    self.rows = rows

  def mappings(self):
    return self.rows


class _AvailabilityConnection:
  def __init__(self, rows) -> None:
    self.rows = rows
    self.calls = []

  async def execute(self, statement, parameters):
    self.calls.append((str(statement), parameters))
    return _AvailabilityResult(self.rows)


class _ConnectEngine:
  def __init__(self, connection) -> None:
    self.connection = connection

  @asynccontextmanager
  async def connect(self):
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
async def test_blocked_agent_launch_state_overrides_stale_heartbeat(monkeypatch) -> None:
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _UnexpectedConnectEngine()
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")

  assert await store.available_market_data_device() is None


@pytest.mark.asyncio
async def test_available_market_data_device_accepts_fresh_data_only_agent(
  monkeypatch,
) -> None:
  monkeypatch.delenv("QMT_AGENT_LAUNCH_STATE", raising=False)
  connection = _AvailabilityConnection(
    [
      {
        "id": "device-data-only",
        "capabilities": ["market-data", "data-only"],
        "heartbeat_updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
      }
    ]
  )
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _ConnectEngine(connection)

  assert await store.available_market_data_device() == "device-data-only"
  assert len(connection.calls) == 1
  assert "runtime_component_heartbeats" in connection.calls[0][0]
  assert "XTDATA_UNAVAILABLE" not in connection.calls[0][1]["connected_statuses"]


@pytest.mark.asyncio
async def test_available_market_data_device_requires_current_launch_heartbeat(
  monkeypatch,
) -> None:
  launch_started_at = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv(
    "QMT_AGENT_LAUNCH_STARTED_AT",
    launch_started_at.isoformat(),
  )
  old_row = {
    "id": "device-old",
    "capabilities": ["market-data", "live"],
    "heartbeat_updated_at": (
      launch_started_at - timedelta(seconds=1)
    ).replace(tzinfo=None),
  }
  current_row = {
    "id": "device-current",
    "capabilities": ["market-data", "live"],
    "heartbeat_updated_at": (
      launch_started_at + timedelta(seconds=1)
    ).replace(tzinfo=None),
  }
  connection = _AvailabilityConnection([old_row, current_row])
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _ConnectEngine(connection)

  assert await store.available_market_data_device() == "device-current"

  old_only = _AvailabilityConnection([old_row])
  store.engine = _ConnectEngine(old_only)
  assert await store.available_market_data_device() is None


@pytest.mark.asyncio
async def test_market_data_request_scopes_repair_attempt_without_changing_payload() -> None:
  connection = _BoundDeviceConnection()
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)
  payload = {"operation": "bars", "stock_list": ["000001.SH"]}
  scope = "core-index-intraday-repair:v1:2026-08-17:attempt-1"

  await store.create_market_data_request(
    payload,
    device_id="device-data-only",
    idempotency_scope=scope,
  )

  encoded = json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
  )
  expected_key = hashlib.sha256(f"{scope}\0{encoded}".encode()).hexdigest()
  lookup = next(
    call for call in connection.calls if "WHERE idempotency_key" in call[0]
  )
  insert = next(
    call for call in connection.calls if "INSERT INTO market_data_request" in call[0]
  )
  assert lookup[1] == {"idempotency_key": expected_key}
  assert insert[1]["request_payload"] == encoded


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
  monkeypatch.setattr(runtime_store.uuid, "uuid4", lambda: "claim-token-1")
  connection = _Connection()
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  claimed = await store.claim_market_data_request("request-1")

  assert claimed == "claim-token-1"
  assert "processing_claim_token = :claim_token" in connection.statement
  assert "RETURNING processing_claim_token" in connection.statement
  assert "updated_at < :stale_before" in connection.statement
  assert "INTERVAL" not in connection.statement
  assert connection.parameters == {
    "request_id": "request-1",
    "claim_token": "claim-token-1",
    "updated_at": now,
    "stale_before": now - timedelta(minutes=5),
  }


@pytest.mark.asyncio
async def test_stale_market_data_takeover_rotates_claim_token(monkeypatch) -> None:
  tokens = iter(("claim-token-1", "claim-token-2"))
  monkeypatch.setattr(runtime_store.uuid, "uuid4", lambda: next(tokens))
  connection = _Connection()
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  first = await store.claim_market_data_request("request-1")
  second = await store.claim_market_data_request("request-1")

  assert first == "claim-token-1"
  assert second == "claim-token-2"
  assert first != second
  assert connection.parameters["claim_token"] == "claim-token-2"


@pytest.mark.asyncio
async def test_market_data_processing_claim_can_be_renewed_and_released(
  monkeypatch,
) -> None:
  now = datetime(2026, 8, 21, 8, 6, 11)
  monkeypatch.setattr(runtime_store, "_utcnow", lambda: now)
  connection = _Connection()
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  assert (
    await store.renew_market_data_request_claim(
      "request-1",
      claim_token="claim-token-1",
    )
    is True
  )
  assert "status = 'PROCESSING'" in connection.statement
  assert "processing_claim_token = :claim_token" in connection.statement
  assert connection.parameters == {
    "request_id": "request-1",
    "claim_token": "claim-token-1",
    "updated_at": now,
  }

  assert await store.release_market_data_request_claim(
    "request-1",
    claim_token="claim-token-1",
    error="Influx unavailable",
  ) is True
  assert "SET status = 'UPLOADED'" in connection.statement
  assert "processing_claim_token = NULL" in connection.statement
  assert connection.parameters == {
    "request_id": "request-1",
    "claim_token": "claim-token-1",
    "error": "Influx unavailable",
    "updated_at": now,
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
    "ingestion_result": None,
    "claim_token": None,
    "completed_at": now,
  }


@pytest.mark.asyncio
async def test_completed_market_data_request_persists_its_ingestion_audit(
  monkeypatch,
) -> None:
  now = datetime(2026, 8, 21, 8, 7, 17)
  monkeypatch.setattr(runtime_store, "_utcnow", lambda: now)
  connection = _Connection()
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)
  audit = {
    "records_received": 2,
    "records_saved": 2,
    "code_summaries": [{"code": "600000.SH", "period": "tick"}],
  }

  await store.finish_market_data_request(
    "request-1",
    status="COMPLETED",
    ingestion_result=audit,
    claim_token="claim-token-1",
  )

  assert "ingestion_result = CAST(:ingestion_result AS JSON)" in connection.statement
  assert "processing_claim_token = CAST(:claim_token AS TEXT)" in connection.statement
  assert connection.parameters["claim_token"] == "claim-token-1"
  assert json.loads(str(connection.parameters["ingestion_result"])) == audit


@pytest.mark.asyncio
async def test_stale_market_data_claim_cannot_renew_release_or_finish() -> None:
  renew_connection = _SequenceConnection([None])
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(renew_connection)

  assert (
    await store.renew_market_data_request_claim(
      "request-1",
      claim_token="stale-token",
    )
    is False
  )
  assert "processing_claim_token = :claim_token" in renew_connection.calls[0][0]

  release_connection = _SequenceConnection([None])
  store.engine = _Engine(release_connection)
  assert (
    await store.release_market_data_request_claim(
      "request-1",
      claim_token="stale-token",
      error="stale owner",
    )
    is False
  )
  assert "processing_claim_token = :claim_token" in release_connection.calls[0][0]

  finish_connection = _SequenceConnection([None, "PROCESSING"])
  store.engine = _Engine(finish_connection)
  with pytest.raises(RuntimeError, match="processing claim was lost"):
    await store.finish_market_data_request(
      "request-1",
      status="COMPLETED",
      ingestion_result={"records_received": 1, "records_saved": 1},
      claim_token="stale-token",
    )
  assert "processing_claim_token = CAST(:claim_token AS TEXT)" in (
    finish_connection.calls[0][0]
  )


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
      ingestion_result={"records_received": 1, "records_saved": 1},
    )

  assert len(connection.calls) == 2
  assert "status NOT IN ('COMPLETED', 'FAILED')" in connection.calls[0][0]
  assert "SELECT status" in connection.calls[1][0]


@pytest.mark.asyncio
async def test_finish_market_data_request_rejects_missing_request() -> None:
  connection = _SequenceConnection([None, None])
  store = runtime_store.DurableRuntimeStore.__new__(
    runtime_store.DurableRuntimeStore
  )
  store.engine = _Engine(connection)

  with pytest.raises(RuntimeError, match="disappeared before terminal"):
    await store.finish_market_data_request(
      "request-1",
      status="FAILED",
      error="invalid transfer",
    )


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
