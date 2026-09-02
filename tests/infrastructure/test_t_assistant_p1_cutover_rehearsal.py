"""Focused tests for the read-only P1 cutover rehearsal."""

from __future__ import annotations

import json
from typing import Any

import pytest
from quantx_contracts.agent import PROTOCOL_VERSION as CONTRACT_PROTOCOL_VERSION
from quantx_infrastructure.services import t_assistant_p1_cutover_rehearsal as rehearsal


class _FakeResult:
  def __init__(self, value: Any = None) -> None:
    self.value = value

  def scalar_one_or_none(self) -> Any:
    return self.value


class _FakeTransaction:
  def __init__(self, *, rollback_error: Exception | None = None) -> None:
    self.rollback_error = rollback_error
    self.rollback_calls = 0

  async def rollback(self) -> None:
    self.rollback_calls += 1
    if self.rollback_error is not None:
      raise self.rollback_error


class _FakeConnection:
  def __init__(
    self,
    *,
    unsettled_count: Any = 0,
    rollback_error: Exception | None = None,
    set_error: Exception | None = None,
  ) -> None:
    self.statements: list[str] = []
    self.unsettled_count = unsettled_count
    self.set_error = set_error
    self.transaction = _FakeTransaction(rollback_error=rollback_error)

  async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _FakeResult:
    del args, kwargs
    sql = str(statement)
    self.statements.append(sql)
    if sql.lstrip().upper().startswith("SET TRANSACTION READ ONLY"):
      if self.set_error is not None:
        raise self.set_error
      return _FakeResult()
    if "agent_report_inbox" in sql:
      return _FakeResult(self.unsettled_count)
    return _FakeResult()

  async def begin(self) -> _FakeTransaction:
    return self.transaction


class _FakeConnectionContext:
  def __init__(self, connection: _FakeConnection) -> None:
    self.connection = connection

  async def __aenter__(self) -> _FakeConnection:
    return self.connection

  async def __aexit__(self, *args: Any) -> None:
    del args


class _FakeEngine:
  def __init__(self, connection: _FakeConnection) -> None:
    self.connection = connection

  def connect(self) -> _FakeConnectionContext:
    return _FakeConnectionContext(self.connection)

  async def dispose(self) -> None:
    return None


def _audit_report(
  *,
  ready: Any = True,
  missing_tables: int = 0,
  current_protocol: str = CONTRACT_PROTOCOL_VERSION,
  target_protocol: str = rehearsal.TARGET_PROTOCOL_VERSION,
  legacy_config_count: int | None = 1,
  queued_count: int | None = 0,
  unknown_count: int | None = 0,
) -> dict[str, Any]:
  checks = [
    {
      "code": rehearsal.P0_REQUIRED_TABLES_MISSING,
      "count": missing_tables,
      "severity": "BLOCKER",
    },
    {
      "code": "P0_ENABLED_LEGACY_CONFIG_COUNT",
      "count": legacy_config_count,
      "severity": "FACT",
    },
    {
      "code": "P0_QUEUED_PROTOCOL_11_T_COMMAND",
      "count": queued_count,
      "severity": "BLOCKER",
    },
    {
      "code": "P0_UNKNOWN_RESULT_PROTOCOL_11_T_COMMAND",
      "count": unknown_count,
      "severity": "BLOCKER",
    },
  ]
  return {
    "readyForP1": ready,
    "currentProtocol": current_protocol,
    "targetProtocol": target_protocol,
    "checks": checks,
  }


@pytest.mark.asyncio
async def test_ready_gate_requires_zero_obligations_and_legacy_config(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  connection = _FakeConnection(unsettled_count=0)

  async def fake_audit(_connection: Any) -> dict[str, Any]:
    return _audit_report()

  monkeypatch.setattr(
    rehearsal.p0_audit,
    "audit_connection",
    fake_audit,
  )

  report = await rehearsal.inspect_connection(connection)

  assert report["readyForCutover"] is True
  assert report["reasonCodes"] == []
  assert report["observed"]["legacyConfigCount"] == 1
  config_check = next(
    check
    for check in report["checks"]
    if check["code"] == "P0_ENABLED_LEGACY_CONFIG_COUNT"
  )
  assert config_check["status"] == "PASSED"
  assert config_check["status"] != "BLOCKED"
  assert report["observed"]["queuedProtocol11OutboxCount"] == 0
  assert report["observed"]["protocol11UnknownResultCount"] == 0
  assert report["observed"]["unsettledAgentInboxCount"] == 0


@pytest.mark.asyncio
async def test_missing_p0_and_unsettled_obligations_fail_closed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  connection = _FakeConnection(unsettled_count=2)

  async def fake_audit(_connection: Any) -> dict[str, Any]:
    return _audit_report(
      ready=False,
      missing_tables=1,
      legacy_config_count=0,
      queued_count=1,
      unknown_count=1,
    )

  monkeypatch.setattr(
    rehearsal.p0_audit,
    "audit_connection",
    fake_audit,
  )

  report = await rehearsal.inspect_connection(connection)

  assert report["readyForCutover"] is False
  assert set(report["reasonCodes"]) >= {
    rehearsal.P0_REQUIRED_TABLES_MISSING,
    rehearsal.CONFIGURATION_MISSING,
    rehearsal.PROTOCOL_11_OUTBOX_NOT_DRAINED,
    rehearsal.PROTOCOL_11_RESULT_UNKNOWN,
  }
  assert rehearsal.UNSETTLED_AGENT_INBOX not in report["reasonCodes"]
  assert report["observed"]["unsettledAgentInboxCount"] is None


@pytest.mark.asyncio
async def test_protocol_conflicts_and_unknown_facts_are_blockers(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  connection = _FakeConnection(unsettled_count=None)

  async def fake_audit(_connection: Any) -> dict[str, Any]:
    return _audit_report(
      current_protocol="1.2",
      target_protocol="1.1",
    )

  monkeypatch.setattr(
    rehearsal.p0_audit,
    "audit_connection",
    fake_audit,
  )

  report = await rehearsal.inspect_connection(connection)

  assert rehearsal.PROTOCOL_VERSION_UNSUPPORTED in report["reasonCodes"]
  assert rehearsal.INBOX_STATUS_UNKNOWN in report["reasonCodes"]
  assert report["readyForCutover"] is False
  assert report["observed"]["configuredProtocol"] == "1.2"
  assert report["observed"]["targetContractVersion"] is None


@pytest.mark.asyncio
async def test_audit_target_protocol_is_required(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  connection = _FakeConnection(unsettled_count=0)

  async def fake_audit(_connection: Any) -> dict[str, Any]:
    return _audit_report(target_protocol="1.1")

  monkeypatch.setattr(
    rehearsal.p0_audit,
    "audit_connection",
    fake_audit,
  )

  report = await rehearsal.inspect_connection(connection)

  assert report["readyForCutover"] is False
  assert rehearsal.PROTOCOL_VERSION_UNSUPPORTED in report["reasonCodes"]
  assert report["observed"]["targetContractVersion"] is None


@pytest.mark.asyncio
async def test_read_only_transaction_and_unconditional_rollback(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  connection = _FakeConnection(unsettled_count=0)

  async def fake_audit(_connection: Any) -> dict[str, Any]:
    return _audit_report()

  monkeypatch.setattr(
    rehearsal.p0_audit,
    "audit_connection",
    fake_audit,
  )

  report = await rehearsal.run_rehearsal(_FakeEngine(connection))

  assert connection.statements[0].upper().startswith("SET TRANSACTION READ ONLY")
  assert all(
    statement.lstrip().upper().startswith(("SET", "SELECT"))
    for statement in connection.statements
  )
  assert connection.transaction.rollback_calls == 1
  assert report["transaction"] == {"readOnly": True, "rolledBack": True}


@pytest.mark.asyncio
async def test_rollback_failure_is_reported_and_not_ready(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  connection = _FakeConnection(
    unsettled_count=0,
    rollback_error=RuntimeError("rollback failed"),
  )

  async def fake_audit(_connection: Any) -> dict[str, Any]:
    return _audit_report()

  monkeypatch.setattr(
    rehearsal.p0_audit,
    "audit_connection",
    fake_audit,
  )

  report = await rehearsal.run_rehearsal(_FakeEngine(connection))

  assert report["readyForCutover"] is False
  assert rehearsal.READ_ONLY_ROLLBACK_FAILED in report["reasonCodes"]
  assert report["transaction"] == {"readOnly": True, "rolledBack": False}


@pytest.mark.asyncio
async def test_read_only_failure_is_reported_without_claiming_read_only(
) -> None:
  connection = _FakeConnection(set_error=RuntimeError("account-SECRET"))

  report = await rehearsal.run_rehearsal(_FakeEngine(connection))

  assert report["readyForCutover"] is False
  assert rehearsal.P0_AUDIT_UNAVAILABLE in report["reasonCodes"]
  assert report["transaction"] == {"readOnly": False, "rolledBack": True}
  assert connection.transaction.rollback_calls == 1
  assert "account-SECRET" not in json.dumps(report, ensure_ascii=False)


@pytest.mark.asyncio
async def test_audit_failure_is_fail_closed_without_raw_error(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  connection = _FakeConnection(unsettled_count=0)

  async def fail_audit(_connection: Any) -> dict[str, Any]:
    raise RuntimeError("account-SECRET run-SECRET")

  monkeypatch.setattr(rehearsal.p0_audit, "audit_connection", fail_audit)
  report = await rehearsal.inspect_connection(connection)

  assert report["readyForCutover"] is False
  assert rehearsal.P0_AUDIT_UNAVAILABLE in report["reasonCodes"]
  rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
  assert "account-SECRET" not in rendered
  assert "run-SECRET" not in rendered


def test_report_is_aggregate_deidentified_and_simulation_is_explicit() -> None:
  report = rehearsal._build_report(
    _audit_report(),
    unsettled_inbox_count=0,
    target_contract_source={"protocol_version": "1.2"},
  )
  rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
  for value in ("account-SECRET", "run-SECRET", "plan-SECRET", "order-SECRET"):
    assert value not in rendered

  assert report["rehearsal"]["simulated"] is True
  assert report["rehearsal"]["actualComponentStop"] is False
  assert report["rehearsal"]["actualBackup"] is False
  assert report["rehearsal"]["actualDeploy"] is False
  assert report["rehearsal"]["actualPostDeployReconcile"] is False
  assert all(step["performed"] is False for step in report["rehearsal"]["steps"])
  assert report["rehearsal"]["stateSequence"] == list(
    rehearsal.REHEARSAL_STATE_SEQUENCE
  )


def test_markdown_and_require_ready_cli_gate() -> None:
  report = rehearsal._build_report(
    _audit_report(ready=False, queued_count=1),
    unsettled_inbox_count=0,
    target_contract_source={"protocol_version": "1.2"},
  )
  markdown = rehearsal.render_markdown(report)

  assert "read-only rehearsal" in markdown
  assert "Ready for cutover: false" in markdown
  assert rehearsal.exit_code_for_report(report, require_ready=True) == 2
  assert rehearsal.exit_code_for_report(report, require_ready=False) == 0


def test_cli_renders_json_and_markdown_without_real_database(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  report = _build_cli_report()
  connection = _FakeConnection()
  engine = _FakeEngine(connection)

  async def fake_run(_engine: Any) -> dict[str, Any]:
    return report

  monkeypatch.setattr(rehearsal, "run_rehearsal", fake_run)

  class _Factory:
    def __call__(self, _url: str, **_kwargs: Any) -> _FakeEngine:
      return engine

  monkeypatch.setattr(
    "sqlalchemy.ext.asyncio.create_async_engine",
    _Factory(),
  )

  assert rehearsal.main(["--database-url", "postgresql+asyncpg://fake", "--require-ready"]) == 2
  json_output = capsys.readouterr().out
  assert json.loads(json_output)["readyForCutover"] is False

  monkeypatch.setattr(rehearsal, "run_rehearsal", fake_run)
  assert rehearsal.main(
    ["--database-url", "postgresql+asyncpg://fake", "--format", "markdown"]
  ) == 0
  markdown_output = capsys.readouterr().out
  assert "Protocol 1.2 cutover rehearsal" in markdown_output


def _build_cli_report() -> dict[str, Any]:
  return rehearsal._build_report(
    _audit_report(ready=False, queued_count=1),
    unsettled_inbox_count=0,
    target_contract_source={"protocol_version": "1.2"},
  )
