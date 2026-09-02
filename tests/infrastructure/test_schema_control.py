import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from quantx_infrastructure.database import schema_control as module

HEAD = "20260902_0045"


def _status(
  *,
  current_heads: list[str] | None = None,
  expected_heads: list[str] | None = None,
  revision_relation: str = "current",
  missing_tables: list[str] | None = None,
  missing_columns: dict[str, list[str]] | None = None,
) -> dict[str, object]:
  current = [HEAD] if current_heads is None else current_heads
  expected = [HEAD] if expected_heads is None else expected_heads
  return {
    "ok": not missing_tables and not missing_columns and current == expected,
    "missing_tables": missing_tables or [],
    "missing_columns": missing_columns or {},
    "current_heads": current,
    "expected_heads": expected,
    "revision_relation": revision_relation,
    "table_count": len(module.REQUIRED_BASELINE),
  }


def _main_result(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
  command: str,
  status: dict[str, object],
) -> tuple[int, dict[str, object]]:
  async def fake_schema_status() -> dict[str, object]:
    return status

  monkeypatch.setattr(module, "schema_status", fake_schema_status)
  monkeypatch.setattr(sys, "argv", ["schema_control", command])
  result = asyncio.run(module._main())
  payload = json.loads(capsys.readouterr().out)
  return result, payload


def test_required_baseline_keeps_account_safety_columns_in_their_authority() -> None:
  assert module.REQUIRED_BASELINE["account_trading_rollouts"] == {
    "account_id",
    "stage",
    "enabled",
  }
  assert module.REQUIRED_BASELINE["account_execution_controls"] == {
    "account_id",
    "authorization_state",
    "reconcile_status",
  }


def test_inspect_schema_reports_current_complete_structure(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  columns = {
    table_name: set(required)
    for table_name, required in module.REQUIRED_BASELINE.items()
  }

  class Inspector:
    def get_table_names(self) -> list[str]:
      return list(columns)

    def get_columns(self, table_name: str) -> list[dict[str, str]]:
      return [{"name": name} for name in columns[table_name]]

  class MigrationContextStub:
    @staticmethod
    def configure(_connection: object) -> SimpleNamespace:
      return SimpleNamespace(get_current_heads=lambda: [HEAD])

  monkeypatch.setattr(module, "inspect", lambda _connection: Inspector())
  monkeypatch.setattr(module, "MigrationContext", MigrationContextStub)
  monkeypatch.setattr(module, "expected_heads", lambda: (HEAD,))

  status = module._inspect_schema(object())

  assert status["ok"] is True
  assert status["missing_tables"] == []
  assert status["missing_columns"] == {}
  assert status["revision_relation"] == "current"


def test_current_schema_with_missing_column_is_unhealthy_but_status_exits_zero(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  result, payload = _main_result(
    monkeypatch,
    capsys,
    "status",
    _status(missing_columns={"account_trading_rollouts": ["stage"]}),
  )

  assert result == 0
  assert payload["ok"] is False
  assert payload["revision_relation"] == "current"
  assert payload["missing_columns"] == {"account_trading_rollouts": ["stage"]}


def test_check_requires_current_revision_and_complete_structure(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  complete_current, current_payload = _main_result(
    monkeypatch,
    capsys,
    "check",
    _status(),
  )
  complete_behind, behind_payload = _main_result(
    monkeypatch,
    capsys,
    "check",
    _status(
      current_heads=["20260825_0033"],
      revision_relation="behind",
    ),
  )

  assert complete_current == 0
  assert current_payload["ok"] is True
  assert complete_behind == 2
  assert behind_payload["ok"] is False
  assert behind_payload["revision_relation"] == "behind"


def test_doctor_accepts_complete_unversioned_schema(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  result, payload = _main_result(
    monkeypatch,
    capsys,
    "doctor",
    _status(
      current_heads=[],
      revision_relation="unversioned",
    ),
  )

  assert result == 0
  assert payload["ok"] is True
  assert payload["revision_relation"] == "unversioned"


@pytest.mark.parametrize(
  ("status", "expected_messages"),
  (
    (
      _status(missing_columns={"account_execution_controls": ["authorization_state"]}),
      ("结构不完整", "authorization_state"),
    ),
    (
      _status(
        current_heads=["20260825_0033"],
        revision_relation="behind",
      ),
      ("revision", "behind"),
    ),
  ),
)
def test_assert_schema_current_fails_closed_on_structure_or_revision(
  monkeypatch: pytest.MonkeyPatch,
  status: dict[str, object],
  expected_messages: tuple[str, str],
) -> None:
  async def fake_schema_status() -> dict[str, object]:
    return status

  monkeypatch.setattr(module, "schema_status", fake_schema_status)

  with pytest.raises(RuntimeError) as error:
    asyncio.run(module.assert_schema_current())

  message = str(error.value)
  for expected in expected_messages:
    assert expected in message


def test_assert_schema_current_accepts_current_complete_schema(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async def fake_schema_status() -> dict[str, object]:
    return _status()

  monkeypatch.setattr(module, "schema_status", fake_schema_status)

  asyncio.run(module.assert_schema_current())
