"""Isolated SQLite BACKTEST versions, append-only frames and result manifests."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash


class TAssistantBacktestStore:
  VERSION = "t-assistant-backtest.v2"

  def __init__(self, directory):
    self.directory = Path(directory)
    if not (self.directory / "facts.sqlite3").is_file():
      raise ValueError("BACKTEST_FACT_DATABASE_REQUIRED")
    with self._connection() as db:
      rows = db.execute("SELECT manifest FROM t_assistant_backtest_versions").fetchall()
      if len(rows) != 1:
        raise ValueError("BACKTEST_SINGLE_VERSION_REQUIRED")
      self.manifest = json.loads(rows[0][0])
      material = self.manifest["material"]
      scopes = db.execute(
        "SELECT execution_id, environment FROM t_assistant_executions"
      ).fetchall()
      if scopes != [(material["execution_id"], "BACKTEST")]:
        raise ValueError("BACKTEST_EXECUTION_SCOPE_INVALID")
    if (
      self.manifest["hash"] != stable_manifest_hash(material)
      or material["schema_version"] != self.VERSION
      or material["environment"] != "BACKTEST"
      or material["scorer_mode"] != "RULE_ONLY"
    ):
      raise ValueError("BACKTEST_VERSION_INVALID")

  @contextmanager
  def _connection(self):
    db = sqlite3.connect(self.directory / "facts.sqlite3", timeout=30)
    try:
      db.execute("PRAGMA foreign_keys = ON")
      with db:
        yield db
    finally:
      db.close()

  @classmethod
  def create(cls, root, *, frozen):
    required = {"config", "data", "code", "broker", "timeline", "initial_account"}
    if not required <= frozen.keys() or any(not frozen[k] for k in required):
      raise ValueError("BACKTEST_FROZEN_INPUT_REQUIRED")
    execution_id = str(uuid4())
    directory = Path(root) / execution_id
    directory.mkdir(parents=True, exist_ok=False)
    material = {
      "schema_version": cls.VERSION,
      "execution_id": execution_id,
      "environment": "BACKTEST",
      "scorer_mode": "RULE_ONLY",
      "frozen": frozen,
    }
    manifest = {"material": material, "hash": stable_manifest_hash(material)}
    db = sqlite3.connect(directory / "facts.sqlite3")
    try:
      with db:
        db.executescript("""
          PRAGMA foreign_keys=ON;
          CREATE TABLE t_assistant_executions (
            execution_id TEXT PRIMARY KEY,
            environment TEXT NOT NULL CHECK(environment='BACKTEST'),
            status TEXT NOT NULL CHECK(status IN ('RUNNING','STOPPED','FAILED')));
          CREATE TABLE t_assistant_backtest_versions (
            execution_id TEXT PRIMARY KEY REFERENCES t_assistant_executions, manifest TEXT NOT NULL);
          CREATE TABLE backtest_frames (
            frame_index INTEGER PRIMARY KEY CHECK(frame_index>=0),
            previous TEXT NOT NULL, hash TEXT NOT NULL UNIQUE, facts TEXT NOT NULL);
          CREATE TABLE backtest_results (
            execution_id TEXT PRIMARY KEY REFERENCES t_assistant_executions, manifest TEXT NOT NULL);
          CREATE TABLE backtest_failures (hash TEXT PRIMARY KEY, manifest TEXT NOT NULL);
        """)
        db.execute(
          "INSERT INTO t_assistant_executions VALUES (?, 'BACKTEST', 'RUNNING')",
          (execution_id,),
        )
        db.execute(
          "INSERT INTO t_assistant_backtest_versions VALUES (?, ?)",
          (execution_id, cls._encode(manifest)),
        )
    finally:
      db.close()
    return cls(directory)

  @staticmethod
  def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)

  def record_failure(self, error):
    detail = str(error)
    safe = (
      detail
      if detail.startswith(("BACKTEST_", "T_"))
      and all(c.isupper() or c.isdigit() or c == "_" for c in detail)
      else type(error).__name__
    )
    with self._connection() as db:
      last = db.execute(
        "SELECT hash FROM backtest_frames ORDER BY frame_index DESC LIMIT 1"
      ).fetchone()
      material = {
        "version_hash": self.manifest["hash"],
        "last_frame_hash": last[0] if last else None,
        "error_type": type(error).__name__,
        "reason": safe,
      }
      db.execute(
        "INSERT OR IGNORE INTO backtest_failures VALUES (?, ?)",
        (stable_manifest_hash(material), self._encode(material)),
      )
      if not db.execute("SELECT 1 FROM backtest_results").fetchone():
        db.execute("UPDATE t_assistant_executions SET status='FAILED'")

  @staticmethod
  def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))

  @staticmethod
  def _create(path, value):
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
      with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(TAssistantBacktestStore._encode(value))
        stream.flush()
        os.fsync(stream.fileno())
      os.link(temporary, path)
    finally:
      temporary.unlink(missing_ok=True)

  def frames(self):
    previous = self.manifest["hash"]
    with self._connection() as db:
      rows = db.execute(
        "SELECT frame_index, previous, hash, facts FROM backtest_frames ORDER BY frame_index"
      )
      for index, row in enumerate(rows):
        material = {"index": index, "previous": previous, "facts": json.loads(row[3])}
        if row[:3] != (index, previous, stable_manifest_hash(material)):
          raise ValueError("BACKTEST_FRAME_HASH_MISMATCH")
        previous = row[2]
        yield {**material, "hash": previous}

  def commit_frame(self, *, index, previous, facts):
    if type(index) is not int or index < 0:
      raise ValueError("BACKTEST_FRAME_INDEX_INVALID")
    digest = stable_manifest_hash(
      {"index": index, "previous": previous, "facts": facts}
    )
    with self._connection() as db:
      db.execute("BEGIN IMMEDIATE")
      preceding = db.execute(
        "SELECT hash FROM backtest_frames WHERE frame_index=?", (index - 1,)
      ).fetchone()
      expected = (
        self.manifest["hash"] if index == 0 else preceding[0] if preceding else None
      )
      if previous != expected:
        raise ValueError("BACKTEST_FRAME_PREFIX_MISMATCH")
      existing = db.execute(
        "SELECT previous, hash, facts FROM backtest_frames WHERE frame_index=?",
        (index,),
      ).fetchone()
      if existing:
        if existing != (previous, digest, self._encode(facts)):
          raise ValueError("BACKTEST_REPLAY_DIVERGED")
      else:
        if db.execute("SELECT 1 FROM backtest_results").fetchone():
          raise ValueError("BACKTEST_RESULT_ALREADY_FROZEN")
        db.execute(
          "INSERT INTO backtest_frames VALUES (?, ?, ?, ?)",
          (index, previous, digest, self._encode(facts)),
        )
    return digest

  def finish(self, result):
    count, last = 0, self.manifest["hash"]
    for frame in self.frames():
      count, last = frame["index"] + 1, frame["hash"]
    material = {
      "version_hash": self.manifest["hash"],
      "execution_id": self.manifest["material"]["execution_id"],
      "frame_count": count,
      "last_frame_hash": last,
      "result": result,
    }
    value = {"material": material, "hash": stable_manifest_hash(material)}
    with self._connection() as db:
      db.execute("BEGIN IMMEDIATE")
      if db.execute(
        "SELECT COUNT(*), MAX(frame_index) FROM backtest_frames"
      ).fetchone() != (count, count - 1 if count else None):
        raise ValueError("BACKTEST_FINISH_PREFIX_CHANGED")
      existing = db.execute("SELECT manifest FROM backtest_results").fetchone()
      if existing:
        if existing[0] != self._encode(value):
          raise ValueError("BACKTEST_RESULT_DIVERGED")
      else:
        db.execute(
          "INSERT INTO backtest_results VALUES (?, ?)",
          (material["execution_id"], self._encode(value)),
        )
        db.execute("UPDATE t_assistant_executions SET status='STOPPED'")
    path = self.directory / "result.json"
    if path.exists():
      if self._read(path) != value:
        raise ValueError("BACKTEST_RESULT_EXPORT_DIVERGED")
    else:
      self._create(path, value)
    return value
