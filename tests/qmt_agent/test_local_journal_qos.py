from __future__ import annotations

import json
import sqlite3

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent.journal import LocalJournal, payload_hash


def _report(message_id: str, sequence: int) -> str:
  return AgentEnvelope(
    message_id=message_id,
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"sequence": sequence, "is_complete": False},
  ).model_dump_json()


def test_legacy_schema_is_backfilled_and_indexed(tmp_path) -> None:
  path = tmp_path / "legacy.sqlite3"
  payload = {"client_order_id": "client-1"}
  result = {"broker_order_id": 9001, "accepted": True}
  envelope = AgentEnvelope(
    message_id="report-1",
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"sequence": 1, "is_complete": True},
  ).model_dump_json()
  connection = sqlite3.connect(path)
  with connection:
    connection.executescript(
      """
      CREATE TABLE commands (
        message_id TEXT PRIMARY KEY,
        payload_hash TEXT NOT NULL,
        payload_json TEXT,
        status TEXT NOT NULL,
        result_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT
      );
      CREATE TABLE reports (
        message_id TEXT PRIMARY KEY,
        envelope_json TEXT NOT NULL,
        acked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      """
    )
    connection.execute(
      """
      INSERT INTO commands(
        message_id, payload_hash, payload_json, status, result_json
      ) VALUES (?, ?, ?, 'COMPLETED', ?)
      """,
      (
        "command-1",
        payload_hash(payload),
        json.dumps(payload),
        json.dumps(result),
      ),
    )
    connection.execute(
      "INSERT INTO reports(message_id, envelope_json) VALUES (?, ?)",
      ("report-1", envelope),
    )
  connection.close()

  journal = LocalJournal(path)

  assert journal.broker_order_client_ids() == {"9001": "client-1"}
  assert journal.retire_pending_full_snapshots() == 1
  index_names = {
    str(row["name"])
    for row in journal.connection.execute("PRAGMA index_list(reports)")
  }
  assert "ix_reports_pending_sequence" in index_names
  assert "ix_reports_pending_snapshot" in index_names
  assert "ix_reports_sequence" in index_names
  command_index_names = {
    str(row["name"])
    for row in journal.connection.execute("PRAGMA index_list(commands)")
  }
  assert "ix_commands_status_kind_client" in command_index_names


def test_structured_backfill_runs_only_once_per_schema_version(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  path = tmp_path / "backfill-version.sqlite3"
  first = LocalJournal(path)
  first.connection.close()

  def unexpected_backfill(_journal) -> None:
    raise AssertionError("completed structured backfill ran again")

  monkeypatch.setattr(
    LocalJournal,
    "_backfill_structured_columns",
    unexpected_backfill,
  )

  reopened = LocalJournal(path)
  assert reopened.stats()["commands"] == 0
  assert reopened.stats()["reports"] == 0


def test_pending_reports_are_stable_and_bounded(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  journal.add_report("z-last-by-id", _report("z-last-by-id", 1))
  journal.add_report("a-first-by-id", _report("a-first-by-id", 2))
  journal.add_report("middle", _report("middle", 3))

  pending = [
    AgentEnvelope.model_validate_json(value).message_id
    for value in journal.pending_reports(limit=2)
  ]

  assert pending == ["z-last-by-id", "a-first-by-id"]
  assert journal.stats()["pending_reports"] == 3
  journal.acknowledge_report("z-last-by-id")
  assert journal.stats()["pending_reports"] == 2


def test_stats_uses_cached_integrity_and_counts(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  assert journal.integrity_check() == "ok"
  journal.integrity_check = lambda: (_ for _ in ()).throw(
    AssertionError("hot-path stats ran a full integrity check")
  )
  journal.begin_command("command-1", {"client_order_id": "client-1"})
  journal.add_report("report-1", _report("report-1", 1))

  stats = journal.stats()

  assert stats["integrity"] == "ok"
  assert stats["commands"] == 1
  assert stats["processing_commands"] == 1
  assert stats["reports"] == 1
  assert stats["pending_reports"] == 1


def test_structured_correlation_cache_updates_without_json_rescan(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  client_order_id = "client-order-1234567"
  journal.begin_command("command-1", {"client_order_id": client_order_id})
  with journal.connection:
    journal.connection.execute(
      "UPDATE commands SET payload_json = '{broken' WHERE message_id = ?",
      ("command-1",),
    )
  journal.complete_command(
    "command-1",
    {"accepted": True, "broker_order_id": 12345, "reports": []},
  )

  assert journal.client_order_id_for_report(broker_order_id=12345) == (
    client_order_id
  )
  assert journal.client_order_id_for_report(
    order_remark=f"qx:{client_order_id[:20]}"
  ) == (
    client_order_id
  )


def test_snapshot_recovery_never_completes_processing_cancel(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  cancel_payload = {
    "command_kind": "CANCEL_ORDER",
    "client_order_id": "client-order-1",
    "broker_order_id": "broker-order-1",
  }
  assert journal.begin_command("cancel-1", cancel_payload)[0] == "NEW"

  assert (
    journal.reconcile_processing_order(
      client_order_id="client-order-1",
      broker_order_id="broker-order-1",
    )
    is False
  )
  assert journal.begin_command("cancel-1", cancel_payload)[0] == "INDETERMINATE"
  row = journal.connection.execute(
    "SELECT command_kind, status FROM commands WHERE message_id = ?",
    ("cancel-1",),
  ).fetchone()
  assert tuple(row) == ("CANCEL_ORDER", "PROCESSING")

  assert journal.reconcile_processing_cancel(
    broker_order_id="broker-order-1",
    order_status="SUBMITTED",
  ) is False
  assert journal.reconcile_processing_cancel(
    broker_order_id="broker-order-1",
    order_status="CANCELLED",
  )
  assert journal.begin_command("cancel-1", cancel_payload) == (
    "DUPLICATE",
    {
      "accepted": True,
      "reason": "reconciled_terminal_cancelled",
      "reports": [],
    },
  )
