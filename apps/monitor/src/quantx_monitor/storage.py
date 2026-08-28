"""SQLite persistence for samples, effective state, incidents, and rollups."""

from __future__ import annotations

import asyncio
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from .models import MonitorStatus, ProbeResult, percentile

SCHEMA_VERSION = 1
HOUR_SECONDS = 3600


class MonitorStorage:
  def __init__(self, path: Path) -> None:
    self.path = path
    self._db: aiosqlite.Connection | None = None
    self._write_lock = asyncio.Lock()

  @property
  def is_open(self) -> bool:
    return self._db is not None

  async def open(self, target_ids: Iterable[str]) -> None:
    self.path.parent.mkdir(parents=True, exist_ok=True)
    self._db = await aiosqlite.connect(self.path)
    self._db.row_factory = aiosqlite.Row
    await self._db.execute("PRAGMA journal_mode=WAL")
    await self._db.execute("PRAGMA synchronous=NORMAL")
    await self._db.execute("PRAGMA busy_timeout=5000")
    await self._db.execute("PRAGMA foreign_keys=ON")
    current_version = int(
      (await (await self._db.execute("PRAGMA user_version")).fetchone())[0]
    )
    if current_version > SCHEMA_VERSION:
      raise RuntimeError(
        f"monitor database schema {current_version} is newer than {SCHEMA_VERSION}"
      )
    if current_version == 0:
      await self._create_schema()
    for target_id in target_ids:
      await self._db.execute(
        """
        INSERT OR IGNORE INTO target_states (
          target_id, effective_status, consecutive_successes,
          consecutive_failures
        ) VALUES (?, 'unknown', 0, 0)
        """,
        (target_id,),
      )
    await self._db.commit()

  async def _create_schema(self) -> None:
    assert self._db is not None
    await self._db.executescript(
      """
      PRAGMA auto_vacuum=INCREMENTAL;

      CREATE TABLE check_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id TEXT NOT NULL,
        checked_at REAL NOT NULL,
        observed_status TEXT NOT NULL,
        effective_status TEXT NOT NULL,
        latency_ms REAL,
        status_code INTEGER,
        reason_code TEXT
      );
      CREATE INDEX ix_check_samples_target_time
        ON check_samples (target_id, checked_at);
      CREATE INDEX ix_check_samples_time ON check_samples (checked_at);

      CREATE TABLE incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id TEXT NOT NULL,
        opened_at REAL NOT NULL,
        resolved_at REAL,
        opened_reason_code TEXT,
        last_reason_code TEXT
      );
      CREATE INDEX ix_incidents_target_opened
        ON incidents (target_id, opened_at);

      CREATE TABLE target_states (
        target_id TEXT PRIMARY KEY,
        effective_status TEXT NOT NULL,
        checked_at REAL,
        last_success_at REAL,
        latency_ms REAL,
        reason_code TEXT,
        consecutive_successes INTEGER NOT NULL DEFAULT 0,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        active_incident_id INTEGER REFERENCES incidents(id)
      );

      CREATE TABLE hourly_rollups (
        target_id TEXT NOT NULL,
        hour_start REAL NOT NULL,
        sample_count INTEGER NOT NULL,
        healthy_count INTEGER NOT NULL,
        degraded_count INTEGER NOT NULL,
        unavailable_count INTEGER NOT NULL,
        unknown_count INTEGER NOT NULL,
        disabled_count INTEGER NOT NULL,
        latency_count INTEGER NOT NULL,
        latency_min_ms REAL,
        latency_max_ms REAL,
        latency_p50_ms REAL,
        latency_p95_ms REAL,
        PRIMARY KEY (target_id, hour_start)
      );
      CREATE INDEX ix_hourly_rollups_time ON hourly_rollups (hour_start);

      PRAGMA user_version=1;
      """
    )
    await self._db.commit()

  async def close(self) -> None:
    if self._db is not None:
      await self._db.close()
      self._db = None

  async def record_results(self, results: Iterable[ProbeResult]) -> None:
    assert self._db is not None
    async with self._write_lock:
      await self._db.execute("BEGIN IMMEDIATE")
      try:
        for result in results:
          await self._record_result(result)
        await self._db.commit()
      except Exception:
        await self._db.rollback()
        raise

  async def _record_result(self, result: ProbeResult) -> None:
    assert self._db is not None
    row = await (
      await self._db.execute(
        "SELECT * FROM target_states WHERE target_id = ?",
        (result.target_id,),
      )
    ).fetchone()
    if row is None:
      raise KeyError(f"unknown monitor target: {result.target_id}")

    successes = int(row["consecutive_successes"] or 0)
    failures = int(row["consecutive_failures"] or 0)
    active_incident_id = row["active_incident_id"]
    observed = result.observed_status
    effective = observed
    last_success_at = row["last_success_at"]

    if observed == MonitorStatus.HEALTHY:
      successes += 1
      failures = 0
      last_success_at = result.checked_at_epoch
      if active_incident_id is not None and successes < 2:
        effective = MonitorStatus.DEGRADED
      else:
        effective = MonitorStatus.HEALTHY
        if active_incident_id is not None:
          await self._db.execute(
            "UPDATE incidents SET resolved_at = ? WHERE id = ?",
            (result.checked_at_epoch, active_incident_id),
          )
          active_incident_id = None
    elif observed == MonitorStatus.DEGRADED:
      successes = 0
      failures = 0
      effective = MonitorStatus.DEGRADED
    elif observed == MonitorStatus.UNAVAILABLE:
      failures += 1
      successes = 0
      if failures < 2:
        effective = MonitorStatus.DEGRADED
      else:
        effective = MonitorStatus.UNAVAILABLE
        if active_incident_id is None:
          cursor = await self._db.execute(
            """
            INSERT INTO incidents (
              target_id, opened_at, opened_reason_code, last_reason_code
            ) VALUES (?, ?, ?, ?)
            """,
            (
              result.target_id,
              result.checked_at_epoch,
              result.reason_code,
              result.reason_code,
            ),
          )
          active_incident_id = cursor.lastrowid
        else:
          await self._db.execute(
            "UPDATE incidents SET last_reason_code = ? WHERE id = ?",
            (result.reason_code, active_incident_id),
          )
    elif observed == MonitorStatus.DISABLED:
      successes = 0
      failures = 0
      effective = MonitorStatus.DISABLED
      if active_incident_id is not None:
        await self._db.execute(
          "UPDATE incidents SET resolved_at = ? WHERE id = ?",
          (result.checked_at_epoch, active_incident_id),
        )
        active_incident_id = None
    else:
      successes = 0
      failures = 0
      effective = MonitorStatus.UNKNOWN

    await self._db.execute(
      """
      INSERT INTO check_samples (
        target_id, checked_at, observed_status, effective_status,
        latency_ms, status_code, reason_code
      ) VALUES (?, ?, ?, ?, ?, ?, ?)
      """,
      (
        result.target_id,
        result.checked_at_epoch,
        observed.value,
        effective.value,
        result.latency_ms,
        result.status_code,
        result.reason_code,
      ),
    )
    await self._db.execute(
      """
      UPDATE target_states
      SET effective_status = ?, checked_at = ?, last_success_at = ?,
          latency_ms = ?, reason_code = ?, consecutive_successes = ?,
          consecutive_failures = ?, active_incident_id = ?
      WHERE target_id = ?
      """,
      (
        effective.value,
        result.checked_at_epoch,
        last_success_at,
        result.latency_ms,
        result.reason_code,
        successes,
        failures,
        active_incident_id,
        result.target_id,
      ),
    )

  async def target_states(self) -> dict[str, dict[str, Any]]:
    assert self._db is not None
    rows = await (await self._db.execute("SELECT * FROM target_states")).fetchall()
    return {str(row["target_id"]): dict(row) for row in rows}

  async def window_metrics(
    self,
    *,
    since: float,
    now: float,
    interval_seconds: float,
  ) -> dict[str, dict[str, Any]]:
    assert self._db is not None
    rows = await (
      await self._db.execute(
        """
        SELECT target_id,
               COUNT(*) AS sample_count,
               SUM(observed_status = 'healthy') AS healthy_count,
               SUM(observed_status = 'degraded') AS degraded_count,
               SUM(observed_status = 'unavailable') AS unavailable_count,
               SUM(observed_status = 'unknown') AS unknown_count,
               SUM(observed_status = 'disabled') AS disabled_count,
               MIN(checked_at) AS first_checked_at
        FROM check_samples
        WHERE checked_at >= ?
        GROUP BY target_id
        """,
        (since,),
      )
    ).fetchall()
    latency_rows = await (
      await self._db.execute(
        """
        SELECT target_id, latency_ms
        FROM check_samples
        WHERE checked_at >= ? AND latency_ms IS NOT NULL
          AND (
            observed_status IN ('healthy', 'degraded')
            OR target_id = 'qmt-agent'
          )
        ORDER BY target_id, latency_ms
        """,
        (since,),
      )
    ).fetchall()
    latencies: dict[str, list[float]] = defaultdict(list)
    for row in latency_rows:
      latencies[str(row["target_id"])].append(float(row["latency_ms"]))

    metrics: dict[str, dict[str, Any]] = {}
    for row in rows:
      target_id = str(row["target_id"])
      total = int(row["sample_count"] or 0)
      disabled = int(row["disabled_count"] or 0)
      denominator = max(0, total - disabled)
      healthy = int(row["healthy_count"] or 0)
      degraded = int(row["degraded_count"] or 0)
      available = healthy + degraded
      first_checked = max(since, float(row["first_checked_at"] or since))
      expected = max(1, math.floor((now - first_checked) / interval_seconds) + 1)
      values = latencies.get(target_id, [])
      metrics[target_id] = {
        "sampleCount": total,
        "availabilityPct": ((available / denominator) * 100 if denominator else None),
        "healthyPct": ((healthy / denominator) * 100 if denominator else None),
        "coveragePct": min(100.0, (total / expected) * 100),
        "latencyP50Ms": percentile(values, 0.50),
        "latencyP95Ms": percentile(values, 0.95),
      }
    return metrics

  async def history(
    self,
    target_id: str,
    *,
    since: float,
    now: float,
    bucket_seconds: int,
    use_rollups: bool,
  ) -> list[dict[str, Any]]:
    assert self._db is not None
    buckets: dict[int, dict[str, Any]] = {}
    if use_rollups:
      rows = await (
        await self._db.execute(
          """
          SELECT * FROM hourly_rollups
          WHERE target_id = ? AND hour_start >= ? AND hour_start <= ?
          ORDER BY hour_start
          """,
          (target_id, since, now),
        )
      ).fetchall()
      for row in rows:
        bucket = int(float(row["hour_start"]) // bucket_seconds * bucket_seconds)
        item = buckets.setdefault(bucket, self._empty_bucket(bucket))
        for key, column in (
          ("sampleCount", "sample_count"),
          ("healthyCount", "healthy_count"),
          ("degradedCount", "degraded_count"),
          ("unavailableCount", "unavailable_count"),
          ("unknownCount", "unknown_count"),
          ("disabledCount", "disabled_count"),
          ("latencyCount", "latency_count"),
        ):
          item[key] += int(row[column] or 0)
        if row["latency_p50_ms"] is not None:
          item["latencyP50Values"].append(float(row["latency_p50_ms"]))
        if row["latency_p95_ms"] is not None:
          item["latencyP95Values"].append(float(row["latency_p95_ms"]))
        if row["latency_max_ms"] is not None:
          item["latencyMaxMs"] = max(
            item["latencyMaxMs"] or 0.0,
            float(row["latency_max_ms"]),
          )
    else:
      rows = await (
        await self._db.execute(
          """
          SELECT checked_at, observed_status, latency_ms
          FROM check_samples
          WHERE target_id = ? AND checked_at >= ? AND checked_at <= ?
          ORDER BY checked_at
          """,
          (target_id, since, now),
        )
      ).fetchall()
      for row in rows:
        bucket = int(float(row["checked_at"]) // bucket_seconds * bucket_seconds)
        item = buckets.setdefault(bucket, self._empty_bucket(bucket))
        item["sampleCount"] += 1
        status_key = {
          "healthy": "healthyCount",
          "degraded": "degradedCount",
          "unavailable": "unavailableCount",
          "unknown": "unknownCount",
          "disabled": "disabledCount",
        }[str(row["observed_status"])]
        item[status_key] += 1
        if row["latency_ms"] is not None:
          value = float(row["latency_ms"])
          item["latencyCount"] += 1
          item["latencyP50Values"].append(value)
          item["latencyP95Values"].append(value)
          item["latencyMaxMs"] = max(item["latencyMaxMs"] or 0.0, value)
    return [self._finalize_bucket(buckets[key]) for key in sorted(buckets)]

  @staticmethod
  def _empty_bucket(start: int) -> dict[str, Any]:
    return {
      "start": start,
      "sampleCount": 0,
      "healthyCount": 0,
      "degradedCount": 0,
      "unavailableCount": 0,
      "unknownCount": 0,
      "disabledCount": 0,
      "latencyCount": 0,
      "latencyP50Values": [],
      "latencyP95Values": [],
      "latencyMaxMs": None,
    }

  @staticmethod
  def _finalize_bucket(item: dict[str, Any]) -> dict[str, Any]:
    if item["unavailableCount"]:
      status = MonitorStatus.UNAVAILABLE
    elif item["degradedCount"]:
      status = MonitorStatus.DEGRADED
    elif item["healthyCount"]:
      status = MonitorStatus.HEALTHY
    elif item["disabledCount"]:
      status = MonitorStatus.DISABLED
    else:
      status = MonitorStatus.UNKNOWN
    p50_values = list(item.pop("latencyP50Values"))
    p95_values = list(item.pop("latencyP95Values"))
    item["status"] = status.value
    item["latencyP50Ms"] = percentile(p50_values, 0.50)
    item["latencyP95Ms"] = percentile(p95_values, 0.95)
    return item

  async def incidents(
    self,
    *,
    since: float,
    target_id: str | None = None,
    limit: int = 200,
  ) -> list[dict[str, Any]]:
    assert self._db is not None
    query = """
      SELECT id, target_id, opened_at, resolved_at,
             opened_reason_code, last_reason_code
      FROM incidents
      WHERE opened_at >= ?
    """
    params: list[Any] = [since]
    if target_id is not None:
      query += " AND target_id = ?"
      params.append(target_id)
    query += " ORDER BY opened_at DESC LIMIT ?"
    params.append(limit)
    rows = await (await self._db.execute(query, params)).fetchall()
    return [dict(row) for row in rows]

  async def rollup_and_retain(
    self,
    *,
    now: float,
    raw_retention_seconds: int,
    rollup_retention_seconds: int,
  ) -> None:
    assert self._db is not None
    current_hour = int(now // HOUR_SECONDS * HOUR_SECONDS)
    async with self._write_lock:
      missing_hours = await (
        await self._db.execute(
          """
          SELECT DISTINCT target_id,
                 CAST(checked_at / 3600 AS INTEGER) * 3600 AS hour_start
          FROM check_samples AS sample
          WHERE checked_at < ?
            AND NOT EXISTS (
              SELECT 1 FROM hourly_rollups AS rollup
              WHERE rollup.target_id = sample.target_id
                AND rollup.hour_start =
                    CAST(sample.checked_at / 3600 AS INTEGER) * 3600
            )
          ORDER BY hour_start, target_id
          """,
          (current_hour,),
        )
      ).fetchall()
      await self._db.execute("BEGIN IMMEDIATE")
      try:
        for missing in missing_hours:
          await self._rollup_hour(
            str(missing["target_id"]),
            float(missing["hour_start"]),
          )
        await self._db.execute(
          "DELETE FROM check_samples WHERE checked_at < ?",
          (now - raw_retention_seconds,),
        )
        await self._db.execute(
          "DELETE FROM hourly_rollups WHERE hour_start < ?",
          (now - rollup_retention_seconds,),
        )
        await self._db.execute(
          """
          DELETE FROM incidents
          WHERE resolved_at IS NOT NULL AND resolved_at < ?
          """,
          (now - rollup_retention_seconds,),
        )
        await self._db.commit()
      except Exception:
        await self._db.rollback()
        raise
      await self._db.execute("PRAGMA incremental_vacuum(256)")

  async def _rollup_hour(self, target_id: str, hour_start: float) -> None:
    assert self._db is not None
    rows = await (
      await self._db.execute(
        """
        SELECT observed_status, latency_ms
        FROM check_samples
        WHERE target_id = ? AND checked_at >= ? AND checked_at < ?
        """,
        (target_id, hour_start, hour_start + HOUR_SECONDS),
      )
    ).fetchall()
    counts = {status.value: 0 for status in MonitorStatus}
    latencies: list[float] = []
    for row in rows:
      counts[str(row["observed_status"])] += 1
      if row["latency_ms"] is not None:
        latencies.append(float(row["latency_ms"]))
    await self._db.execute(
      """
      INSERT OR REPLACE INTO hourly_rollups (
        target_id, hour_start, sample_count, healthy_count, degraded_count,
        unavailable_count, unknown_count, disabled_count, latency_count,
        latency_min_ms, latency_max_ms, latency_p50_ms, latency_p95_ms
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
      (
        target_id,
        hour_start,
        len(rows),
        counts["healthy"],
        counts["degraded"],
        counts["unavailable"],
        counts["unknown"],
        counts["disabled"],
        len(latencies),
        min(latencies) if latencies else None,
        max(latencies) if latencies else None,
        percentile(latencies, 0.50),
        percentile(latencies, 0.95),
      ),
    )

  async def backup_to(self, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    def backup() -> None:
      source = sqlite3.connect(self.path)
      target = sqlite3.connect(destination)
      try:
        source.backup(target)
      finally:
        target.close()
        source.close()

    await asyncio.to_thread(backup)
