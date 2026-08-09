"""Crash-safe local command idempotency and report outbox."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


def payload_hash(payload: dict[str, Any]) -> str:
  encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class LocalJournal:
  def __init__(self, path: Path) -> None:
    self.path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    self.connection = sqlite3.connect(path, check_same_thread=False)
    self.connection.row_factory = sqlite3.Row
    self.lock = threading.RLock()
    with self.connection:
      self.connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS commands (
          message_id TEXT PRIMARY KEY,
          payload_hash TEXT NOT NULL,
          payload_json TEXT,
          status TEXT NOT NULL,
          result_json TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reports (
          message_id TEXT PRIMARY KEY,
          envelope_json TEXT NOT NULL,
          acked INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
      )
      command_columns = {
        str(row["name"])
        for row in self.connection.execute("PRAGMA table_info(commands)")
      }
      if "payload_json" not in command_columns:
        self.connection.execute(
          "ALTER TABLE commands ADD COLUMN payload_json TEXT"
        )

  def begin_command(
    self,
    message_id: str,
    payload: dict[str, Any],
  ) -> tuple[str, Optional[dict[str, Any]]]:
    digest = payload_hash(payload)
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
        INSERT INTO commands(message_id, payload_hash, payload_json, status)
        VALUES (?, ?, ?, 'PROCESSING')
        """,
        (
          message_id,
          digest,
          json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
      )
    return "NEW", None

  def complete_command(
    self,
    message_id: str,
    result: dict[str, Any],
  ) -> None:
    with self.lock, self.connection:
      self.connection.execute(
        """
        UPDATE commands
        SET status = 'COMPLETED', result_json = ?, completed_at = CURRENT_TIMESTAMP
        WHERE message_id = ?
        """,
        (json.dumps(result, separators=(",", ":")), message_id),
      )

  def add_report(self, message_id: str, envelope_json: str) -> None:
    with self.lock, self.connection:
      self.connection.execute(
        """
        INSERT INTO reports(message_id, envelope_json, acked)
        VALUES (?, ?, 0)
        ON CONFLICT(message_id) DO NOTHING
        """,
        (message_id, envelope_json),
      )

  def retire_pending_full_snapshots(self) -> int:
    """Retire complete snapshots superseded by a newly captured snapshot."""
    with self.lock, self.connection:
      rows = self.connection.execute(
        """
        SELECT message_id, envelope_json
        FROM reports
        WHERE acked = 0
        """
      ).fetchall()
      message_ids = []
      for row in rows:
        try:
          envelope = json.loads(str(row["envelope_json"]))
          payload = envelope.get("payload")
          if (
            envelope.get("message_type") == "delta_report"
            and isinstance(payload, dict)
            and payload.get("is_complete") is True
          ):
            message_ids.append(str(row["message_id"]))
        except (TypeError, ValueError, json.JSONDecodeError):
          continue
      if message_ids:
        self.connection.executemany(
          "UPDATE reports SET acked = 1 WHERE message_id = ?",
          [(message_id,) for message_id in message_ids],
        )
      return len(message_ids)

  def pending_reports(self) -> list[str]:
    with self.lock:
      rows = self.connection.execute(
        "SELECT envelope_json FROM reports WHERE acked = 0 ORDER BY created_at"
      ).fetchall()
    return [str(row["envelope_json"]) for row in rows]

  def acknowledge_report(self, message_id: str) -> None:
    with self.lock, self.connection:
      self.connection.execute(
        "UPDATE reports SET acked = 1 WHERE message_id = ?",
        (message_id,),
      )

  def integrity_check(self) -> str:
    with self.lock:
      row = self.connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0] if row else "unknown")

  def backup_to(self, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with self.lock:
      backup = sqlite3.connect(destination)
      try:
        self.connection.backup(backup)
      finally:
        backup.close()

  def stats(self) -> dict[str, int | str]:
    with self.lock:
      pending_reports = int(
        self.connection.execute(
          "SELECT COUNT(*) FROM reports WHERE acked = 0"
        ).fetchone()[0]
      )
      reports = int(
        self.connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
      )
      processing_commands = int(
        self.connection.execute(
          "SELECT COUNT(*) FROM commands WHERE status = 'PROCESSING'"
        ).fetchone()[0]
      )
      commands = int(
        self.connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
      )
    return {
      "integrity": self.integrity_check(),
      "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
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
      # VACUUM compacts the journal only after the retention transaction has
      # committed; pending/unacknowledged rows are never deleted.
      self.connection.execute("VACUUM")
    return {
      "reports_deleted": max(0, int(reports or 0)),
      "commands_deleted": max(0, int(commands or 0)),
    }

  def broker_order_client_ids(self) -> dict[str, str]:
    """Return durable broker-order correlation learned from accepted commands."""
    with self.lock:
      rows = self.connection.execute(
        """
        SELECT payload_json, result_json
        FROM commands
        WHERE status = 'COMPLETED'
          AND payload_json IS NOT NULL
          AND result_json IS NOT NULL
        """
      ).fetchall()
    mappings: dict[str, str] = {}
    for row in rows:
      try:
        payload = json.loads(row["payload_json"])
        result = json.loads(row["result_json"])
        broker_order_id = result.get("broker_order_id")
        client_order_id = payload.get("client_order_id")
        if broker_order_id is not None and client_order_id:
          mappings[str(broker_order_id)] = str(client_order_id)
      except (TypeError, ValueError, json.JSONDecodeError):
        continue
    return mappings

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
      rows = self.connection.execute(
        """
        SELECT message_id, payload_json
        FROM commands
        WHERE status = 'PROCESSING'
          AND payload_json IS NOT NULL
        """
      ).fetchall()
      for row in rows:
        try:
          payload = json.loads(row["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
          continue
        if str(payload.get("client_order_id") or "") != client_order_id:
          continue
        result = {
          "accepted": True,
          "reason": "reconciled_from_broker_snapshot",
          "broker_order_id": broker_order_id,
          "reports": [],
        }
        self.connection.execute(
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
        return True
    return False

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
      mapped = self.broker_order_client_ids().get(str(broker_order_id))
      if mapped:
        return mapped

    prefix = str(order_remark or "")
    if prefix.startswith("qx:"):
      prefix = prefix[3:]
    if not prefix:
      return None

    with self.lock:
      rows = self.connection.execute(
        """
        SELECT payload_json
        FROM commands
        WHERE payload_json IS NOT NULL
        """
      ).fetchall()
    matches: set[str] = set()
    for row in rows:
      try:
        payload = json.loads(row["payload_json"])
      except (TypeError, ValueError, json.JSONDecodeError):
        continue
      client_order_id = str(payload.get("client_order_id") or "")
      if client_order_id.startswith(prefix):
        matches.add(client_order_id)
    return next(iter(matches)) if len(matches) == 1 else None
