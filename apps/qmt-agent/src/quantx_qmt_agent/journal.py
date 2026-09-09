"""Crash-safe local command idempotency and report outbox."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from quantx_contracts.collection_permit import CollectionPermit, CollectionUnit
from quantx_contracts.collection_receipt import CollectionAbort
from quantx_contracts.history_upload import HistoryUploadSnapshot


def payload_hash(payload: dict[str, Any]) -> str:
  encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class LocalJournal:
  _STRUCTURED_BACKFILL_VERSION = "2"

  def __init__(self, path: Path) -> None:
    self.path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    self.connection = sqlite3.connect(path, check_same_thread=False)
    self.connection.row_factory = sqlite3.Row
    self.lock = threading.RLock()
    self._integrity_status = "unknown"
    self._broker_to_client: dict[str, str] = {}
    self._client_order_ids: frozenset[str] = frozenset()
    self._remark_prefix_to_client: dict[str, str | None] = {}
    with self.connection:
      self.connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS commands (
          message_id TEXT PRIMARY KEY,
          payload_hash TEXT NOT NULL,
          payload_json TEXT,
          status TEXT NOT NULL,
          result_json TEXT,
          command_kind TEXT,
          client_order_id TEXT,
          broker_order_id TEXT,
          target_broker_order_id TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reports (
          message_id TEXT PRIMARY KEY,
          envelope_json TEXT NOT NULL,
          acked INTEGER NOT NULL DEFAULT 0,
          report_type TEXT,
          is_complete_snapshot INTEGER NOT NULL DEFAULT 0,
          sequence_id INTEGER,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS history_collection_receipts (
          device_id TEXT NOT NULL,
          request_id TEXT NOT NULL,
          unit_index INTEGER NOT NULL,
          unit_id TEXT NOT NULL,
          permit_id TEXT PRIMARY KEY,
          permit_sha256 TEXT NOT NULL,
          owner_epoch INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_history_collection_unit
          ON history_collection_receipts(device_id, request_id, unit_index);
        CREATE TABLE IF NOT EXISTS history_collection_artifacts (
          unit_id TEXT PRIMARY KEY,
          permit_id TEXT NOT NULL,
          artifact_sha256 TEXT NOT NULL,
          byte_count INTEGER NOT NULL CHECK(byte_count > 0),
          record_count INTEGER NOT NULL CHECK(record_count >= 0)
        );
        CREATE TABLE IF NOT EXISTS history_collection_executions (
          unit_id TEXT NOT NULL,
          permit_id TEXT PRIMARY KEY,
          attempt_index INTEGER NOT NULL CHECK(attempt_index >= 0),
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS history_upload_retirements (
          device_id TEXT NOT NULL,
          request_id TEXT NOT NULL,
          request_sha256 TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          retired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(device_id,request_id)
        );
        CREATE TABLE IF NOT EXISTS history_collection_aborts (
          permit_id TEXT PRIMARY KEY,
          unit_id TEXT NOT NULL,
          permit_json TEXT NOT NULL,
          failure_json TEXT NOT NULL,
          accepted_at TEXT,
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS journal_metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
      )
      self._migrate_collection_attempts()
      command_columns = {
        str(row["name"])
        for row in self.connection.execute("PRAGMA table_info(commands)")
      }
      if "payload_json" not in command_columns:
        self.connection.execute(
          "ALTER TABLE commands ADD COLUMN payload_json TEXT"
        )
      if "command_kind" not in command_columns:
        self.connection.execute(
          "ALTER TABLE commands ADD COLUMN command_kind TEXT"
        )
      if "client_order_id" not in command_columns:
        self.connection.execute(
          "ALTER TABLE commands ADD COLUMN client_order_id TEXT"
        )
      if "broker_order_id" not in command_columns:
        self.connection.execute(
          "ALTER TABLE commands ADD COLUMN broker_order_id TEXT"
        )
      if "target_broker_order_id" not in command_columns:
        self.connection.execute(
          "ALTER TABLE commands ADD COLUMN target_broker_order_id TEXT"
        )
      report_columns = {
        str(row["name"])
        for row in self.connection.execute("PRAGMA table_info(reports)")
      }
      if "report_type" not in report_columns:
        self.connection.execute(
          "ALTER TABLE reports ADD COLUMN report_type TEXT"
        )
      if "is_complete_snapshot" not in report_columns:
        self.connection.execute(
          """
          ALTER TABLE reports
          ADD COLUMN is_complete_snapshot INTEGER NOT NULL DEFAULT 0
          """
        )
      if "sequence_id" not in report_columns:
        self.connection.execute(
          "ALTER TABLE reports ADD COLUMN sequence_id INTEGER"
        )
      backfill_version = self.connection.execute(
        "SELECT value FROM journal_metadata WHERE key = ?",
        ("structured_backfill_version",),
      ).fetchone()
      if (
        backfill_version is None
        or str(backfill_version["value"]) != self._STRUCTURED_BACKFILL_VERSION
      ):
        self._backfill_structured_columns()
        self.connection.execute(
          """
          INSERT INTO journal_metadata(key, value)
          VALUES (?, ?)
          ON CONFLICT(key) DO UPDATE SET value = excluded.value
          """,
          ("structured_backfill_version", self._STRUCTURED_BACKFILL_VERSION),
        )
      self.connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS ix_commands_status_client
          ON commands(status, client_order_id);
        CREATE INDEX IF NOT EXISTS ix_commands_status_kind_client
          ON commands(status, command_kind, client_order_id);
        CREATE INDEX IF NOT EXISTS ix_commands_broker_order
          ON commands(broker_order_id);
        CREATE INDEX IF NOT EXISTS ix_commands_processing_cancel_target
          ON commands(status, command_kind, target_broker_order_id);
        CREATE INDEX IF NOT EXISTS ix_commands_client_order
          ON commands(client_order_id);
        CREATE INDEX IF NOT EXISTS ix_commands_status_completed
          ON commands(status, completed_at);
        CREATE INDEX IF NOT EXISTS ix_reports_pending_sequence
          ON reports(acked, sequence_id);
        CREATE INDEX IF NOT EXISTS ix_reports_pending_snapshot
          ON reports(acked, is_complete_snapshot);
        CREATE INDEX IF NOT EXISTS ix_reports_pending_created
          ON reports(acked, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS ix_reports_sequence
          ON reports(sequence_id);
        """
      )
    self._load_correlation_cache()
    self._load_count_cache()
    self._size_bytes = self._read_size_bytes()

  def history_upload_retired(self, device_id: str, request_id: str) -> bool:
    with self.lock:
      return self.connection.execute(
        "SELECT 1 FROM history_upload_retirements WHERE device_id=? AND request_id=?",
        (device_id, request_id),
      ).fetchone() is not None

  def retire_history_upload(self, *, device_id: str, request_sha256: str, snapshot, now=None):
    snapshot = HistoryUploadSnapshot.model_validate(snapshot.model_dump(mode="json"))
    current = now or datetime.now(timezone.utc)
    if snapshot.verified_at is None or current - snapshot.verified_at < timedelta(hours=24):
      raise ValueError("history retirement requires verified ingestion and retention")
    encoded = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    with self.lock, self.connection:
      self.connection.execute("BEGIN IMMEDIATE")
      existing = self.connection.execute(
        "SELECT request_sha256,evidence_json FROM history_upload_retirements WHERE device_id=? AND request_id=?",
        (device_id, str(snapshot.request_id)),
      ).fetchone()
      if existing is not None:
        if existing["request_sha256"] != request_sha256 or existing["evidence_json"] != encoded:
          raise ValueError("history retirement evidence conflict")
        return
      self.connection.execute(
        "INSERT INTO history_upload_retirements(device_id,request_id,request_sha256,evidence_json) VALUES (?,?,?,?)",
        (device_id, str(snapshot.request_id), request_sha256, encoded),
      )
      scope = (device_id, str(snapshot.request_id))
      if self.connection.execute(
        "SELECT 1 FROM history_collection_aborts a JOIN history_collection_receipts r "
        "ON r.permit_id=a.permit_id WHERE r.device_id=? AND r.request_id=? "
        "AND a.accepted_at IS NULL LIMIT 1", scope,
      ).fetchone() is not None:
        raise ValueError("history retirement has an unconfirmed collection failure")
      # Keep one compact retirement proof instead of an unbounded set of native
      # receipts in the trading journal. The tombstone still forbids recollection.
      self.connection.execute(
        "DELETE FROM history_collection_aborts WHERE permit_id IN "
        "(SELECT permit_id FROM history_collection_receipts WHERE device_id=? AND request_id=?)", scope,
      )
      self.connection.execute(
        "DELETE FROM history_collection_executions WHERE permit_id IN "
        "(SELECT permit_id FROM history_collection_receipts WHERE device_id=? AND request_id=?)", scope,
      )
      self.connection.execute(
        "DELETE FROM history_collection_artifacts WHERE permit_id IN "
        "(SELECT permit_id FROM history_collection_receipts WHERE device_id=? AND request_id=?)", scope,
      )
      self.connection.execute("DELETE FROM history_collection_receipts WHERE device_id=? AND request_id=?", scope)
    self._refresh_size_cache()

  def accept_collection_permit(
    self,
    permit: CollectionPermit,
    *,
    device_id: str,
    unit: CollectionUnit,
    now: datetime | None = None,
  ) -> bool:
    """Persist a start authorization before native work; False means a replay.

    Receipt is not completion evidence. A replay must join/recover the existing
    request/spool state, never infer that its native work or upload is complete.
    """
    # Validate caller scope and time before touching even the epoch watermark.
    permit.validate_start(device_id=device_id, unit=unit, now=now)
    device = str(permit.device_id)
    epoch_key = "history_owner_epoch:" + device
    permit_digest = payload_hash(permit.model_dump(mode="json"))
    with self.lock, self.connection:
      # Serialize read/check/write across journal connections as well as threads.
      self.connection.execute("BEGIN IMMEDIATE")
      if self.history_upload_retired(device, str(unit.request_id)):
        raise ValueError("history request has been retired")
      epoch_row = self.connection.execute(
        "SELECT value FROM journal_metadata WHERE key=?", (epoch_key,)
      ).fetchone()
      minimum_epoch = int(epoch_row["value"]) if epoch_row is not None else 0
      permit.validate_start(
        device_id=device, unit=unit, now=now, minimum_epoch=minimum_epoch
      )
      receipt = self.connection.execute(
        "SELECT permit_sha256 FROM history_collection_receipts WHERE permit_id=?",
        (str(permit.permit_id),),
      ).fetchone()
      if receipt is not None and receipt["permit_sha256"] != permit_digest:
        raise ValueError("collection permit ID conflicts with recorded authorization")
      existing = self.connection.execute(
        "SELECT unit_id, permit_id FROM history_collection_receipts "
        "WHERE device_id=? AND request_id=? AND unit_index=? LIMIT 1",
        (device, str(unit.request_id), unit.unit_index),
      ).fetchone()
      if existing is not None and existing["unit_id"] != unit.unit_id:
        raise ValueError("collection permit conflicts with the recorded unit")
      self.connection.execute(
        "INSERT INTO journal_metadata(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (epoch_key, str(permit.owner_epoch)),
      )
      self.connection.execute(
        "INSERT INTO history_collection_receipts "
        "(device_id,request_id,unit_index,unit_id,permit_id,permit_sha256,owner_epoch) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(permit_id) DO NOTHING",
        (
          device,
          str(unit.request_id),
          unit.unit_index,
          unit.unit_id,
          str(permit.permit_id),
          permit_digest,
          permit.owner_epoch,
        ),
      )
    with self.lock:
      self._refresh_size_cache()
    return existing is None

  def collection_permit_received(self, permit: CollectionPermit) -> bool:
    """Require original authorization evidence when the server reports STARTED."""
    with self.lock:
      row = self.connection.execute(
        "SELECT permit_sha256 FROM history_collection_receipts WHERE permit_id=?",
        (str(permit.permit_id),),
      ).fetchone()
    if row is None:
      return False
    if row["permit_sha256"] != payload_hash(permit.model_dump(mode="json")):
      raise ValueError("collection receipt conflicts with original authorization")
    return True

  def _migrate_collection_attempts(self) -> None:
    """Retain every original execution when changing from one slot to attempts."""
    self.connection.execute("BEGIN IMMEDIATE")
    columns = self.connection.execute("PRAGMA table_info(history_collection_executions)").fetchall()
    if any(row["name"] == "unit_id" and row["pk"] for row in columns):
      for statement in (
        "CREATE TABLE history_collection_executions_v2 (unit_id TEXT NOT NULL,permit_id TEXT PRIMARY KEY,attempt_index INTEGER NOT NULL CHECK(attempt_index>=0),started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "INSERT INTO history_collection_executions_v2 SELECT unit_id,permit_id,0,started_at FROM history_collection_executions",
        "CREATE TABLE history_collection_aborts_v2 (permit_id TEXT PRIMARY KEY,unit_id TEXT NOT NULL,permit_json TEXT NOT NULL,failure_json TEXT NOT NULL,accepted_at TEXT,recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "INSERT INTO history_collection_aborts_v2 SELECT permit_id,unit_id,permit_json,failure_json,accepted_at,recorded_at FROM history_collection_aborts",
        "DROP TABLE history_collection_executions",
        "ALTER TABLE history_collection_executions_v2 RENAME TO history_collection_executions",
        "DROP TABLE history_collection_aborts",
        "ALTER TABLE history_collection_aborts_v2 RENAME TO history_collection_aborts",
      ):
        self.connection.execute(statement)
    self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_history_execution_attempt ON history_collection_executions(unit_id,attempt_index)")

  def collection_execution_started(self, permit: CollectionPermit) -> bool:
    """Read original execution identity without renewing its start authorization."""
    with self.lock:
      own = self.connection.execute(
        "SELECT e.permit_id,r.permit_sha256 FROM history_collection_executions e "
        "JOIN history_collection_receipts r ON r.permit_id=e.permit_id "
        "WHERE e.unit_id=? AND e.permit_id=?",
        (permit.unit.unit_id, str(permit.permit_id)),
      ).fetchone()
      if own is not None:
        if own["permit_sha256"] != payload_hash(permit.model_dump(mode="json")):
          raise ValueError("collection execution belongs to another authorization")
        return True
      unresolved = self.connection.execute(
        "SELECT 1 FROM history_collection_executions e WHERE e.unit_id=? "
        "AND NOT EXISTS (SELECT 1 FROM history_collection_aborts a "
        "WHERE a.permit_id=e.permit_id AND a.accepted_at IS NOT NULL) LIMIT 1",
        (permit.unit.unit_id,),
      ).fetchone()
      if unresolved is not None:
        raise ValueError("collection execution belongs to another authorization")
      return False

  def begin_collection_execution(
    self, permit: CollectionPermit, *, device_id: str, now: datetime | None = None
  ) -> None:
    """Persist uncertainty before native entry, after the server confirms START.

    The caller must never invoke the native function after a duplicate marker.
    Neither an expired grant nor a new owner clears an existing execution fact.
    """
    with self.lock, self.connection:
      self.connection.execute("BEGIN IMMEDIATE")
      if self.history_upload_retired(device_id, str(permit.unit.request_id)):
        raise ValueError("history request has been retired")
      row = self.connection.execute(
        "SELECT permit_sha256 FROM history_collection_receipts WHERE permit_id=?",
        (str(permit.permit_id),),
      ).fetchone()
      if row is None or row["permit_sha256"] != payload_hash(
        permit.model_dump(mode="json")
      ):
        raise ValueError("collection execution requires its original permit receipt")
      epoch = self.connection.execute(
        "SELECT value FROM journal_metadata WHERE key=?",
        ("history_owner_epoch:" + str(permit.device_id),),
      ).fetchone()
      permit.validate_start(
        device_id=device_id,
        unit=permit.unit,
        now=now,
        minimum_epoch=int(epoch["value"]) if epoch is not None else 0,
      )
      if self.collection_execution_started(permit):
        raise ValueError("collection execution has already entered native work")
      self.connection.execute(
        "INSERT INTO history_collection_executions(unit_id,permit_id,attempt_index) "
        "SELECT ?,?,COALESCE(MAX(attempt_index)+1,0) FROM history_collection_executions WHERE unit_id=?",
        (permit.unit.unit_id, str(permit.permit_id), permit.unit.unit_id),
      )
    with self.lock:
      self._refresh_size_cache()

  def record_collection_abort(self, permit: CollectionPermit, failure: CollectionAbort) -> None:
    """Persist confirmed exit supplied by the current execution's stop boundary."""
    failure = CollectionAbort.model_validate(failure.model_dump(mode="json"))
    if failure.unit != permit.unit:
      raise ValueError("collection failure scope mismatch")
    with self.lock, self.connection:
      self.connection.execute("BEGIN IMMEDIATE")
      if not self.collection_permit_received(permit) or not self.collection_execution_started(permit):
        raise ValueError("collection failure requires original execution evidence")
      if self.connection.execute(
        "SELECT 1 FROM history_collection_artifacts WHERE unit_id=?", (permit.unit.unit_id,)
      ).fetchone() is not None:
        raise ValueError("collection failure cannot replace a completed artifact")
      saved = self.load_collection_abort(permit)
      if saved is not None and saved != failure:
        raise ValueError("collection failure conflicts with recorded exit")
      self.connection.execute(
        "INSERT INTO history_collection_aborts(permit_id,unit_id,permit_json,failure_json) VALUES (?,?,?,?) ON CONFLICT(permit_id) DO NOTHING",
        (str(permit.permit_id), permit.unit.unit_id, permit.model_dump_json(), failure.model_dump_json()),
      )
    with self.lock:
      self._refresh_size_cache()

  def load_collection_abort(self, permit: CollectionPermit) -> CollectionAbort | None:
    with self.lock:
      row = self.connection.execute(
        "SELECT permit_json,failure_json FROM history_collection_aborts WHERE permit_id=?",
        (str(permit.permit_id),),
      ).fetchone()
      if row is None:
        return None
      if CollectionPermit.model_validate_json(row["permit_json"]) != permit or not self.collection_permit_received(permit):
        raise ValueError("collection failure conflicts with original authorization")
      failure = CollectionAbort.model_validate_json(row["failure_json"])
      if failure.unit != permit.unit:
        raise ValueError("collection failure scope mismatch")
      return failure

  def confirm_collection_abort(self, permit: CollectionPermit) -> None:
    with self.lock, self.connection:
      self.connection.execute("BEGIN IMMEDIATE")
      if self.load_collection_abort(permit) is None:
        raise ValueError("collection abort confirmation lacks local evidence")
      self.connection.execute(
        "UPDATE history_collection_aborts SET accepted_at=COALESCE(accepted_at,CURRENT_TIMESTAMP) WHERE permit_id=?",
        (str(permit.permit_id),),
      )

  def request_collection_aborts(self, device_id: str, request_id: str):
    """Bounded original failures, including Worker acceptance for offline recovery."""
    with self.lock:
      rows = self.connection.execute(
        "SELECT a.permit_json,a.accepted_at FROM history_collection_aborts a "
        "JOIN history_collection_receipts r ON r.permit_id=a.permit_id "
        "JOIN history_collection_executions e ON e.permit_id=a.permit_id "
        "WHERE r.device_id=? AND r.request_id=? "
        "AND NOT EXISTS (SELECT 1 FROM history_collection_executions newer "
        "WHERE newer.unit_id=a.unit_id AND newer.attempt_index>e.attempt_index) "
        "ORDER BY r.unit_index LIMIT 2049",
        (device_id, request_id),
      ).fetchall()
      if len(rows) > 2048:
        raise ValueError("collection abort recovery exceeds request unit budget")
      results = []
      for row in rows:
        permit = CollectionPermit.model_validate_json(row["permit_json"])
        if str(permit.device_id) != device_id or str(permit.unit.request_id) != request_id:
          raise ValueError("collection abort recovery scope mismatch")
        results.append((permit, self.load_collection_abort(permit), row["accepted_at"] is not None))
      return results

  def record_collection_artifact(self, *, permit_id: str, artifacts, artifact) -> bool:
    """Bind verified bytes to a received permit, without claiming upload success."""
    verified = artifacts.inspect(artifact.unit, expected_sha256=artifact.sha256)
    if verified != artifact:
      raise ValueError("collection artifact metadata mismatch")
    with self.lock, self.connection:
      self.connection.execute("BEGIN IMMEDIATE")
      if self.connection.execute(
        "SELECT 1 FROM history_collection_aborts WHERE permit_id=?", (permit_id,)
      ).fetchone() is not None:
        raise ValueError("collection artifact cannot replace a recorded failure")
      receipt = self.connection.execute(
        "SELECT unit_id FROM history_collection_receipts WHERE permit_id=?",
        (permit_id,),
      ).fetchone()
      if receipt is None or receipt["unit_id"] != artifact.unit.unit_id:
        raise ValueError("collection artifact has no matching permit receipt")
      existing = self.connection.execute(
        "SELECT artifact_sha256,byte_count,record_count FROM history_collection_artifacts WHERE unit_id=?",
        (artifact.unit.unit_id,),
      ).fetchone()
      if existing is not None and (
        existing["artifact_sha256"] != artifact.sha256
        or existing["byte_count"] != artifact.byte_count
        or existing["record_count"] != artifact.record_count
      ):
        raise ValueError("collection artifact conflicts with recorded completion")
      self.connection.execute(
        "INSERT INTO history_collection_artifacts(unit_id,permit_id,artifact_sha256,byte_count,record_count) "
        "VALUES (?,?,?,?,?) ON CONFLICT(unit_id) DO NOTHING",
        (
          artifact.unit.unit_id,
          permit_id,
          artifact.sha256,
          artifact.byte_count,
          artifact.record_count,
        ),
      )
    with self.lock:
      self._refresh_size_cache()
    return existing is None

  def collection_request_record_count(self, device_id: str, request_id: str) -> int:
    """Count completed unit records against the original request's one budget."""
    with self.lock:
      row = self.connection.execute(
        "SELECT COALESCE(SUM(a.record_count),0) FROM history_collection_artifacts a "
        "JOIN history_collection_receipts r ON r.permit_id=a.permit_id "
        "WHERE r.device_id=? AND r.request_id=?", (device_id, request_id),
      ).fetchone()
      return int(row[0])

  def load_collection_artifact(
    self, *, device_id: str, unit: CollectionUnit, artifacts
  ):
    """Return only reverified native bytes, never infer completion from a receipt."""
    with self.lock:
      record = self.connection.execute(
        "SELECT a.artifact_sha256,a.byte_count,a.record_count FROM history_collection_artifacts a "
        "JOIN history_collection_receipts r ON r.permit_id=a.permit_id "
        "WHERE a.unit_id=? AND r.device_id=?",
        (unit.unit_id, device_id),
      ).fetchone()
    if record is None:
      return None
    artifact = artifacts.inspect(unit, expected_sha256=record["artifact_sha256"])
    if (
      artifact.byte_count != record["byte_count"]
      or artifact.record_count != record["record_count"]
    ):
      raise ValueError("collection artifact metadata differs from journal")
    return artifact

  def _backfill_structured_columns(self) -> None:
    command_rows = self.connection.execute(
      """
      SELECT message_id, payload_json, result_json
      FROM commands
      WHERE command_kind IS NULL
         OR client_order_id IS NULL
         OR broker_order_id IS NULL
         OR target_broker_order_id IS NULL
      """
    ).fetchall()
    command_updates: list[
      tuple[str | None, str | None, str | None, str | None, str]
    ] = []
    for row in command_rows:
      command_kind: str | None = None
      client_order_id: str | None = None
      broker_order_id: str | None = None
      target_broker_order_id: str | None = None
      try:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        raw_command_kind = payload.get("command_kind")
        raw_client_order_id = payload.get("client_order_id")
        raw_broker_order_id = result.get("broker_order_id")
        raw_target_broker_order_id = payload.get("broker_order_id")
        if raw_command_kind:
          command_kind = str(raw_command_kind).upper()
        if raw_client_order_id:
          client_order_id = str(raw_client_order_id)
        if raw_broker_order_id is not None:
          broker_order_id = str(raw_broker_order_id)
        if raw_target_broker_order_id is not None:
          target_broker_order_id = str(raw_target_broker_order_id)
      except (TypeError, ValueError, json.JSONDecodeError):
        pass
      command_updates.append(
        (
          command_kind,
          client_order_id,
          broker_order_id,
          target_broker_order_id,
          str(row["message_id"]),
        )
      )
    if command_updates:
      self.connection.executemany(
        """
        UPDATE commands
        SET command_kind = COALESCE(command_kind, ?),
            client_order_id = COALESCE(client_order_id, ?),
            broker_order_id = COALESCE(broker_order_id, ?),
            target_broker_order_id = COALESCE(target_broker_order_id, ?)
        WHERE message_id = ?
        """,
        command_updates,
      )

    report_rows = self.connection.execute(
      """
      SELECT rowid, message_id, envelope_json, report_type, sequence_id
      FROM reports
      WHERE report_type IS NULL OR sequence_id IS NULL
      """
    ).fetchall()
    report_updates: list[tuple[str | None, int, int, str]] = []
    for row in report_rows:
      report_type: str | None = None
      is_complete_snapshot = 0
      try:
        envelope = json.loads(str(row["envelope_json"]))
        report_type = str(envelope.get("message_type") or "") or None
        payload = envelope.get("payload")
        is_complete_snapshot = int(
          report_type == "delta_report"
          and isinstance(payload, dict)
          and payload.get("is_complete") is True
        )
      except (TypeError, ValueError, json.JSONDecodeError):
        pass
      report_updates.append(
        (
          report_type,
          is_complete_snapshot,
          int(row["rowid"]),
          str(row["message_id"]),
        )
      )
    if report_updates:
      self.connection.executemany(
        """
        UPDATE reports
        SET report_type = COALESCE(report_type, ?),
            is_complete_snapshot = ?,
            sequence_id = COALESCE(sequence_id, ?)
        WHERE message_id = ?
        """,
        report_updates,
      )

  def _load_correlation_cache(self) -> None:
    with self.lock:
      rows = self.connection.execute(
        """
        SELECT client_order_id, broker_order_id
        FROM commands
        WHERE client_order_id IS NOT NULL
        """
      ).fetchall()
      client_order_ids = {
        str(row["client_order_id"])
        for row in rows
        if row["client_order_id"]
      }
      self._client_order_ids = frozenset(client_order_ids)
      self._broker_to_client = {
        str(row["broker_order_id"]): str(row["client_order_id"])
        for row in rows
        if row["broker_order_id"] is not None and row["client_order_id"]
      }
      prefixes: dict[str, str | None] = {}
      for client_order_id in client_order_ids:
        prefix = client_order_id[:20]
        existing = prefixes.get(prefix)
        prefixes[prefix] = (
          client_order_id
          if existing is None and prefix not in prefixes
          else client_order_id
          if existing == client_order_id
          else None
        )
      self._remark_prefix_to_client = prefixes

  def _publish_client_order_id(self, client_order_id: str) -> None:
    if client_order_id in self._client_order_ids:
      return
    self._client_order_ids = self._client_order_ids | {client_order_id}
    prefix = client_order_id[:20]
    prefixes = dict(self._remark_prefix_to_client)
    existing = prefixes.get(prefix)
    prefixes[prefix] = (
      client_order_id
      if existing is None and prefix not in prefixes
      else client_order_id
      if existing == client_order_id
      else None
    )
    self._remark_prefix_to_client = prefixes

  def _publish_broker_order_id(
    self,
    broker_order_id: str,
    client_order_id: str,
  ) -> None:
    mappings = dict(self._broker_to_client)
    mappings[broker_order_id] = client_order_id
    self._broker_to_client = mappings

  def _load_count_cache(self) -> None:
    with self.lock:
      self._report_count = int(
        self.connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
      )
      self._pending_report_count = int(
        self.connection.execute(
          "SELECT COUNT(*) FROM reports WHERE acked = 0"
        ).fetchone()[0]
      )
      self._command_count = int(
        self.connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
      )
      self._processing_command_count = int(
        self.connection.execute(
          "SELECT COUNT(*) FROM commands WHERE status = 'PROCESSING'"
        ).fetchone()[0]
      )
      self._next_report_sequence = int(
        self.connection.execute(
          "SELECT COALESCE(MAX(sequence_id), 0) + 1 FROM reports"
        ).fetchone()[0]
      )

  def _read_size_bytes(self) -> int:
    try:
      return self.path.stat().st_size
    except OSError:
      return 0

  def _refresh_size_cache(self) -> None:
    self._size_bytes = self._read_size_bytes()

  def begin_command(
    self,
    message_id: str,
    payload: dict[str, Any],
  ) -> tuple[str, Optional[dict[str, Any]]]:
    digest = payload_hash(payload)
    raw_client_order_id = payload.get("client_order_id")
    client_order_id = (
      str(raw_client_order_id) if raw_client_order_id else None
    )
    command_kind = str(payload.get("command_kind") or "").upper() or None
    raw_target_broker_order_id = payload.get("broker_order_id")
    target_broker_order_id = (
      str(raw_target_broker_order_id)
      if raw_target_broker_order_id is not None
      else None
    )
    with self.lock, self.connection:
      row = self.connection.execute(
        "SELECT payload_hash, status, result_json FROM commands WHERE message_id = ?",
        (message_id,),
      ).fetchone()
      if row is not None:
        if row["payload_hash"] != digest:
          return "MISMATCH", None
        if row["status"] == "COMPLETED":
          result = json.loads(row["result_json"]) if row["result_json"] else {}
          return "DUPLICATE", result
        return "INDETERMINATE", None
      self.connection.execute(
        """
        INSERT INTO commands(
          message_id,
          payload_hash,
          payload_json,
          status,
          command_kind,
          client_order_id,
          target_broker_order_id
        )
        VALUES (?, ?, ?, 'PROCESSING', ?, ?, ?)
        """,
        (
          message_id,
          digest,
          json.dumps(payload, sort_keys=True, separators=(",", ":")),
          command_kind,
          client_order_id,
          target_broker_order_id,
        ),
      )
      if client_order_id:
        self._publish_client_order_id(client_order_id)
      self._command_count += 1
      self._processing_command_count += 1
      self._refresh_size_cache()
    return "NEW", None

  def complete_command(
    self,
    message_id: str,
    result: dict[str, Any],
  ) -> None:
    raw_broker_order_id = result.get("broker_order_id")
    broker_order_id = (
      str(raw_broker_order_id)
      if raw_broker_order_id is not None
      else None
    )
    with self.lock, self.connection:
      updated = self.connection.execute(
        """
        UPDATE commands
        SET status = 'COMPLETED',
            result_json = ?,
            broker_order_id = ?,
            completed_at = CURRENT_TIMESTAMP
        WHERE message_id = ? AND status = 'PROCESSING'
        """,
        (
          json.dumps(result, separators=(",", ":")),
          broker_order_id,
          message_id,
        ),
      )
      if updated.rowcount:
        self._processing_command_count = max(
          0,
          self._processing_command_count - 1,
        )
      if broker_order_id is not None:
        row = self.connection.execute(
          "SELECT client_order_id FROM commands WHERE message_id = ?",
          (message_id,),
        ).fetchone()
        if row is not None and row["client_order_id"]:
          client_order_id = str(row["client_order_id"])
          self._publish_client_order_id(client_order_id)
          self._publish_broker_order_id(broker_order_id, client_order_id)
      self._refresh_size_cache()

  def add_report(self, message_id: str, envelope_json: str) -> None:
    report_type: str | None = None
    is_complete_snapshot = 0
    try:
      envelope = json.loads(envelope_json)
      report_type = str(envelope.get("message_type") or "") or None
      payload = envelope.get("payload")
      is_complete_snapshot = int(
        report_type == "delta_report"
        and isinstance(payload, dict)
        and payload.get("is_complete") is True
      )
    except (TypeError, ValueError, json.JSONDecodeError):
      pass
    with self.lock, self.connection:
      inserted = self.connection.execute(
        """
        INSERT INTO reports(
          message_id,
          envelope_json,
          acked,
          report_type,
          is_complete_snapshot,
          sequence_id
        )
        VALUES (
          ?,
          ?,
          0,
          ?,
          ?,
          ?
        )
        ON CONFLICT(message_id) DO NOTHING
        """,
        (
          message_id,
          envelope_json,
          report_type,
          is_complete_snapshot,
          self._next_report_sequence,
        ),
      )
      if inserted.rowcount:
        self._report_count += 1
        self._pending_report_count += 1
        self._next_report_sequence += 1
        self._refresh_size_cache()

  def retire_pending_full_snapshots(self) -> int:
    """Retire complete snapshots superseded by a newly captured snapshot."""
    with self.lock, self.connection:
      updated = self.connection.execute(
        """
        UPDATE reports
        SET acked = 1
        WHERE acked = 0 AND is_complete_snapshot = 1
        """
      ).rowcount
      self._pending_report_count = max(
        0,
        self._pending_report_count - max(0, int(updated or 0)),
      )
      if updated:
        self._refresh_size_cache()
      return max(0, int(updated or 0))

  def pending_reports(self, *, limit: int | None = None) -> list[str]:
    if limit is not None and limit <= 0:
      return []
    query = (
      "SELECT envelope_json FROM reports "
      "WHERE acked = 0 ORDER BY sequence_id"
    )
    parameters: tuple[int, ...] = ()
    if limit is not None:
      query += " LIMIT ?"
      parameters = (int(limit),)
    with self.lock:
      rows = self.connection.execute(query, parameters).fetchall()
    return [str(row["envelope_json"]) for row in rows]

  def acknowledge_report(self, message_id: str) -> None:
    self.acknowledge_reports((message_id,))

  def acknowledge_reports(self, message_ids: tuple[str, ...] | list[str]) -> int:
    """Acknowledge a bounded report batch in one durable transaction."""
    normalized = tuple(dict.fromkeys(str(value) for value in message_ids if value))
    if not normalized:
      return 0
    placeholders = ",".join("?" for _ in normalized)
    with self.lock, self.connection:
      updated = self.connection.execute(
        (
          "UPDATE reports SET acked = 1 "
          f"WHERE acked = 0 AND message_id IN ({placeholders})"
        ),
        normalized,
      )
      if updated.rowcount:
        self._pending_report_count = max(
          0,
          self._pending_report_count - int(updated.rowcount),
        )
        self._refresh_size_cache()
      return max(0, int(updated.rowcount or 0))

  def integrity_check(self) -> str:
    with self.lock:
      row = self.connection.execute("PRAGMA integrity_check").fetchone()
      self._integrity_status = str(row[0] if row else "unknown")
      return self._integrity_status

  def backup_to(self, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with self.lock:
      backup = sqlite3.connect(destination)
      try:
        self.connection.backup(backup)
      finally:
        backup.close()

  def stats(self) -> dict[str, int | str]:
    # These scalar projections are assigned atomically under CPython's GIL.
    # Heartbeats must never contend with an SQLite fsync or filesystem stat.
    pending_reports = self._pending_report_count
    reports = self._report_count
    processing_commands = self._processing_command_count
    commands = self._command_count
    return {
      "integrity": self._integrity_status,
      "size_bytes": self._size_bytes,
      "reports": reports,
      "pending_reports": pending_reports,
      "commands": commands,
      "processing_commands": processing_commands,
    }

  def prune(self, retention_days: int = 30) -> dict[str, int]:
    if retention_days < 7:
      raise ValueError("journal retention must be at least 7 days")
    cutoff = (
      datetime.now(timezone.utc) - timedelta(days=retention_days)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with self.lock:
      with self.connection:
        reports = self.connection.execute(
          "DELETE FROM reports WHERE acked = 1 AND created_at < ?",
          (cutoff,),
        ).rowcount
        commands = self.connection.execute(
          """
          DELETE FROM commands
          WHERE status = 'COMPLETED'
            AND completed_at IS NOT NULL
            AND completed_at < ?
          """,
          (cutoff,),
        ).rowcount
        self.connection.execute("PRAGMA optimize")
      self._report_count = max(0, self._report_count - max(0, int(reports or 0)))
      self._command_count = max(
        0,
        self._command_count - max(0, int(commands or 0)),
      )
      self._load_correlation_cache()
      self._refresh_size_cache()
    return {
      "reports_deleted": max(0, int(reports or 0)),
      "commands_deleted": max(0, int(commands or 0)),
    }

  def broker_order_client_ids(self) -> dict[str, str]:
    """Return durable broker-order correlation learned from accepted commands."""
    return dict(self._broker_to_client)

  def reconcile_processing_order(
    self,
    *,
    client_order_id: str,
    broker_order_id: Any,
  ) -> bool:
    """Complete an interrupted local command from an authoritative snapshot."""
    if not client_order_id or broker_order_id is None:
      return False
    with self.lock, self.connection:
      row = self.connection.execute(
        """
        SELECT message_id
        FROM commands
        WHERE status = 'PROCESSING'
          AND command_kind = 'PLACE_ORDER'
          AND client_order_id = ?
        ORDER BY created_at, message_id
        LIMIT 1
        """,
        (client_order_id,),
      ).fetchone()
      if row is not None:
        result = {
          "accepted": True,
          "reason": "reconciled_from_broker_snapshot",
          "broker_order_id": broker_order_id,
          "reports": [],
        }
        updated = self.connection.execute(
          """
          UPDATE commands
          SET status = 'COMPLETED',
              result_json = ?,
              completed_at = CURRENT_TIMESTAMP
          WHERE message_id = ?
            AND status = 'PROCESSING'
          """,
          (
            json.dumps(result, separators=(",", ":")),
            row["message_id"],
          ),
        )
        normalized_broker_order_id = str(broker_order_id)
        self.connection.execute(
          "UPDATE commands SET broker_order_id = ? WHERE message_id = ?",
          (normalized_broker_order_id, row["message_id"]),
        )
        self._publish_client_order_id(client_order_id)
        self._publish_broker_order_id(
          normalized_broker_order_id,
          client_order_id,
        )
        if updated.rowcount:
          self._processing_command_count = max(
            0,
            self._processing_command_count - 1,
          )
          self._refresh_size_cache()
        return True
    return False

  def reconcile_processing_cancel(
    self,
    *,
    broker_order_id: Any,
    order_status: str,
  ) -> bool:
    """Resolve an interrupted cancel only from an authoritative terminal state."""
    normalized_broker_order_id = str(broker_order_id or "").strip()
    normalized_status = str(order_status or "").strip().upper()
    if not normalized_broker_order_id or normalized_status not in {
      "CANCELLED",
      "FILLED",
      "REJECTED",
      "EXPIRED",
    }:
      return False
    accepted = normalized_status == "CANCELLED"
    result = {
      "accepted": accepted,
      "reason": (
        "reconciled_terminal_cancelled"
        if accepted
        else f"reconciled_terminal_order_{normalized_status.lower()}"
      ),
      "reports": [],
    }
    with self.lock, self.connection:
      updated = self.connection.execute(
        """
        UPDATE commands
        SET status = 'COMPLETED',
            result_json = ?,
            completed_at = CURRENT_TIMESTAMP
        WHERE status = 'PROCESSING'
          AND command_kind = 'CANCEL_ORDER'
          AND target_broker_order_id = ?
        """,
        (
          json.dumps(result, separators=(",", ":")),
          normalized_broker_order_id,
        ),
      )
      if updated.rowcount:
        self._processing_command_count = max(
          0,
          self._processing_command_count - int(updated.rowcount),
        )
        self._refresh_size_cache()
      return bool(updated.rowcount)

  def client_order_id_for_report(
    self,
    *,
    broker_order_id: Any = None,
    order_remark: str = "",
  ) -> Optional[str]:
    """Resolve a broker callback to the durable client order identity.

    miniQMT may deliver the callback before ``order_stock`` returns to the
    command handler. In that race the broker-order mapping is not available
    yet, so the local ``qx:<client-id-prefix>`` remark is used as a
    conservative fallback. Ambiguous prefixes are rejected.
    """
    if broker_order_id is not None:
      mapped = self._broker_to_client.get(str(broker_order_id))
      if mapped:
        return mapped

    remark = str(order_remark or "")
    if not remark.startswith("qx:"):
      return None
    prefix = remark[3:]
    if len(prefix) != 20:
      return None

    return self._remark_prefix_to_client.get(prefix)
