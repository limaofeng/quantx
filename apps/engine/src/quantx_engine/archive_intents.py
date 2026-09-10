"""Local durable admission intent, committed before starting a Tick subscription."""

import sqlite3
import time

from quantx_contracts.realtime_archive import ArchiveRecoveryScope
from quantx_infrastructure.services.market_data_staging import market_data_staging_root

MAX_PENDING_SCOPES = 10_000


class ArchiveIntentJournal:
  def __init__(self, path=None):
    self.path = (
      path or market_data_staging_root().parent / "engine-archive-intents.sqlite"
    )

  def _connect(self):
    self.path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(self.path, timeout=3)
    try:
      db.row_factory = sqlite3.Row
      db.execute("PRAGMA synchronous=FULL")
      db.execute("PRAGMA page_size=4096")
      if (
        db.execute("PRAGMA page_size").fetchone()[0] != 4096
        or db.execute("PRAGMA max_page_count=4096").fetchone()[0] > 4096
      ):
        raise RuntimeError("ARCHIVE_INTENT_STORAGE_CAPACITY")
      db.execute("""CREATE TABLE IF NOT EXISTS scope_intent(
        generation INTEGER NOT NULL, instrument TEXT NOT NULL, scope TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 4),
        next_retry REAL NOT NULL DEFAULT 0, reason TEXT,
        PRIMARY KEY(generation,instrument))""")
    except BaseException:
      db.close()
      raise
    return db

  def pending(self):
    db = self._connect()
    try:
      rows = db.execute(
        "SELECT scope FROM scope_intent ORDER BY generation,instrument LIMIT ?",
        (MAX_PENDING_SCOPES + 1,),
      ).fetchall()
      if len(rows) > MAX_PENDING_SCOPES:
        raise RuntimeError("ARCHIVE_INTENT_CAPACITY")
      return [ArchiveRecoveryScope.model_validate_json(row["scope"]) for row in rows]
    finally:
      db.close()

  def put(self, scope):
    db = self._connect()
    try:
      with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
          "SELECT scope FROM scope_intent WHERE generation=? AND instrument=?",
          (scope.generation, scope.instrument),
        ).fetchone()
        if row:
          return ArchiveRecoveryScope.model_validate_json(row["scope"])
        if (
          db.execute("SELECT count(*) FROM scope_intent").fetchone()[0]
          >= MAX_PENDING_SCOPES
        ):
          raise RuntimeError("ARCHIVE_INTENT_CAPACITY")
        db.execute(
          "INSERT INTO scope_intent(generation,instrument,scope) VALUES (?,?,?)",
          (scope.generation, scope.instrument, scope.model_dump_json()),
        )
      return scope
    finally:
      db.close()

  def reserve(self):
    db = self._connect()
    try:
      with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
          """SELECT * FROM scope_intent WHERE attempts<4 AND next_retry<=?
          ORDER BY next_retry,generation,instrument LIMIT 1""",
          (time.time(),),
        ).fetchone()
        if row is None:
          return None
        # Reserve before HTTP; process loss cannot refund an ambiguous attempt.
        db.execute(
          "UPDATE scope_intent SET attempts=attempts+1,next_retry=?,reason='REGISTRATION_UNCONFIRMED' WHERE generation=? AND instrument=?",
          (time.time() + 30, row["generation"], row["instrument"]),
        )
      return ArchiveRecoveryScope.model_validate_json(row["scope"])
    finally:
      db.close()

  def acknowledge(self, scope):
    db = self._connect()
    try:
      with db:
        db.execute(
          "DELETE FROM scope_intent WHERE generation=? AND instrument=? AND scope=?",
          (scope.generation, scope.instrument, scope.model_dump_json()),
        )
    finally:
      db.close()
