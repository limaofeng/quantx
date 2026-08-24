"""Small SQLAlchemy-Core adapter for cross-process runtime coordination."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quantx_infrastructure.services.qmt_launch_guard import (
  qmt_agent_launch_state,
  qmt_heartbeat_matches_current_launch,
)


def resolve_database_url() -> str:
  root_value = os.environ.get("QUANTX_ROOT", "").strip()
  root = (
    Path(root_value).expanduser().resolve()
    if root_value
    else Path(__file__).resolve().parents[4]
  )
  load_dotenv(root / "apps" / "api" / ".env", override=False)
  load_dotenv(root / "apps" / "api" / f".env.{os.getenv('ENV', 'development')}", override=False)
  value = os.environ.get("DATABASE_URL", "").strip()
  if not value or "asyncpg" not in value:
    raise RuntimeError("DATABASE_URL must be an async PostgreSQL URL")
  return value


def _utcnow() -> datetime:
  return datetime.now(timezone.utc).replace(tzinfo=None)


class DurableRuntimeStore:
  def __init__(self, database_url: Optional[str] = None) -> None:
    self.engine: AsyncEngine = create_async_engine(
      database_url or resolve_database_url(),
      pool_pre_ping=True,
    )

  async def close(self) -> None:
    await self.engine.dispose()

  async def heartbeat(
    self,
    *,
    component: str,
    instance_id: str,
    status: str,
    details: Optional[dict[str, Any]] = None,
  ) -> None:
    async with self.engine.begin() as connection:
      await connection.execute(
        text(
          """
          INSERT INTO runtime_component_heartbeats
            (component, instance_id, status, details, updated_at)
          VALUES
            (:component, :instance_id, :status, CAST(:details AS JSON), :updated_at)
          ON CONFLICT (component) DO UPDATE SET
            instance_id = EXCLUDED.instance_id,
            status = EXCLUDED.status,
            details = EXCLUDED.details,
            updated_at = EXCLUDED.updated_at
          """
        ),
        {
          "component": component,
          "instance_id": instance_id,
          "status": status[:32],
          "details": json.dumps(details or {}, default=str),
          "updated_at": _utcnow(),
        },
      )

  async def available_market_data_device(
    self,
    *,
    max_age_seconds: float = 90.0,
  ) -> Optional[str]:
    """Return one fresh connected Agent that can serve historical data.

    Registered devices are not sufficient here: a durable request assigned to
    an offline device would remain QUEUED until the caller times out.  Historical
    replay uses this read-only probe before it optionally queues a supplement.
    Trading readiness is deliberately not required; data-only and
    trading-unavailable Agents may still provide XTData history.
    """

    if qmt_agent_launch_state() in {"BLOCKED", "NOT_REQUESTED"}:
      return None
    cutoff = _utcnow() - timedelta(seconds=max(1.0, float(max_age_seconds)))
    connected_statuses = (
      "READY",
      "RECONCILING",
      "RECONCILE_REQUIRED",
      "TRADING_UNAVAILABLE",
      "EMERGENCY_STOP",
    )
    async with self.engine.connect() as connection:
      rows = (
        await connection.execute(
          text(
            """
            SELECT
              device.id,
              device.capabilities,
              heartbeat.updated_at AS heartbeat_updated_at
            FROM agent_devices AS device
            JOIN runtime_component_heartbeats AS heartbeat
              ON heartbeat.component = 'qmt-agent:' || device.id
            WHERE device.revoked_at IS NULL
              AND device.last_seen_at >= :cutoff
              AND heartbeat.updated_at >= :cutoff
              AND UPPER(heartbeat.status) IN :connected_statuses
            ORDER BY heartbeat.updated_at DESC, device.last_seen_at DESC
            """
          ).bindparams(bindparam("connected_statuses", expanding=True)),
          {
            "cutoff": cutoff,
            "connected_statuses": connected_statuses,
          },
        )
      ).mappings()
      for row in rows:
        if not qmt_heartbeat_matches_current_launch(
          row["heartbeat_updated_at"]
        ):
          continue
        capabilities = row["capabilities"]
        if isinstance(capabilities, str):
          capabilities = json.loads(capabilities)
        if "market-data" in {
          str(value).strip().lower() for value in list(capabilities or [])
        }:
          return str(row["id"])
    return None

  async def create_market_data_request(
    self,
    payload: dict[str, Any],
    *,
    device_id: Optional[str] = None,
    required_capabilities: Optional[list[str]] = None,
    idempotency_scope: str = "",
  ) -> str:
    encoded = json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    )
    normalized_scope = str(idempotency_scope or "").strip()
    if len(normalized_scope) > 200:
      raise ValueError("market-data idempotency_scope is too long")
    idempotency_material = (
      encoded if not normalized_scope else f"{normalized_scope}\0{encoded}"
    )
    idempotency_key = hashlib.sha256(
      idempotency_material.encode("utf-8")
    ).hexdigest()
    async with self.engine.begin() as connection:
      existing = (
        await connection.execute(
          text(
            """
            SELECT request_id
            FROM market_data_request
            WHERE idempotency_key = :idempotency_key
            """
          ),
          {"idempotency_key": idempotency_key},
        )
      ).scalar_one_or_none()
      if existing:
        return str(existing)
      if device_id:
        selected = (
          await connection.execute(
            text(
              """
              SELECT id, capabilities
              FROM agent_devices
              WHERE id = :device_id
                AND revoked_at IS NULL
              """
            ),
            {"device_id": device_id},
          )
        ).mappings().one_or_none()
        rows = [selected] if selected is not None else []
      else:
        rows = (
          await connection.execute(
            text(
              """
              SELECT id, capabilities
              FROM agent_devices
              WHERE revoked_at IS NULL
              ORDER BY last_seen_at DESC NULLS LAST
              """
            )
          )
        ).mappings()
      required = {"market-data"}
      required.update(
        str(item).strip()
        for item in required_capabilities or []
        if str(item).strip()
      )
      selected_device_id = ""
      for row in rows:
        capabilities = row["capabilities"]
        if isinstance(capabilities, str):
          capabilities = json.loads(capabilities)
        available = set(capabilities or [])
        if required.issubset(available):
          selected_device_id = str(row["id"])
          break
      if not selected_device_id:
        requirement = ", ".join(sorted(required))
        if device_id:
          raise RuntimeError(
            "Requested QMT Agent is unavailable or lacks required "
            f"capabilities: {requirement}"
          )
        raise RuntimeError(
          "No registered QMT Agent is available with capabilities: "
          f"{requirement}"
        )
      request_id = str(uuid.uuid4())
      inserted_request_id = (
        await connection.execute(
          text(
            """
            INSERT INTO market_data_request
              (
                request_id, device_id, idempotency_key, request_payload,
                status, expected_chunks, received_chunks, completed_at,
                created_at, updated_at
              )
            VALUES
              (
                :request_id, :device_id, :idempotency_key,
                CAST(:request_payload AS JSON), 'QUEUED', NULL, 0, NULL,
                :created_at, :updated_at
              )
            ON CONFLICT (idempotency_key)
            DO NOTHING
            RETURNING request_id
            """
          ),
          {
            "request_id": request_id,
            "device_id": selected_device_id,
            "idempotency_key": idempotency_key,
            "request_payload": encoded,
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
          },
        )
      ).scalar_one_or_none()
      if inserted_request_id is not None:
        return str(inserted_request_id)
      converged_request_id = (
        await connection.execute(
          text(
            """
            SELECT request_id
            FROM market_data_request
            WHERE idempotency_key = :idempotency_key
            """
          ),
          {"idempotency_key": idempotency_key},
        )
      ).scalar_one_or_none()
      if converged_request_id is None:  # pragma: no cover - conflict row is durable
        raise RuntimeError("market-data idempotent request did not converge")
      return str(converged_request_id)

  async def market_data_request_status(self, request_id: str) -> str:
    async with self.engine.connect() as connection:
      value = (
        await connection.execute(
          text(
            """
            SELECT status
            FROM market_data_request
            WHERE request_id = :request_id
            """
          ),
          {"request_id": request_id},
        )
      ).scalar_one_or_none()
    return str(value or "MISSING")

  async def market_data_request(
    self,
    request_id: str,
  ) -> Optional[dict[str, Any]]:
    async with self.engine.connect() as connection:
      value = (
        await connection.execute(
          text(
            """
            SELECT request_id, request_payload, status, expected_chunks,
                   received_chunks, processing_error, ingestion_result
            FROM market_data_request
            WHERE request_id = :request_id
            """
          ),
          {"request_id": request_id},
        )
      ).mappings().one_or_none()
    return dict(value) if value else None

  async def market_data_transfers(
    self,
    request_id: str,
  ) -> list[dict[str, Any]]:
    async with self.engine.connect() as connection:
      values = (
        await connection.execute(
          text(
            """
            SELECT chunk_index, checksum_sha256, record_count,
                   compressed, compressed_bytes, storage_reference
            FROM market_data_transfer
            WHERE request_id = :request_id
            ORDER BY chunk_index
            """
          ),
          {"request_id": request_id},
        )
      ).mappings()
      return [dict(value) for value in values]

  async def completed_tick_day_coverage(
    self,
    *,
    instrument_code: str,
    trading_dates: list[date],
  ) -> list[dict[str, Any]]:
    """Read strictly confirmed empty Tick coverage for bounded dates.

    ``ingestion_result`` is written atomically with the COMPLETED transition,
    so no manifest from a queued, uploading, or interrupted request can prove
    a suspended/empty Tick day. A missing Tick day is admissible only when
    *both* Tick and ``1d`` have separate completed, persistence-verified,
    exact-single-day ``XT_DATA_NO_ROWS`` proofs for the same code/date. A
    completed, persistence-verified request with nonzero or malformed ``1d``
    coverage for that code/date is contradictory and blocks the exception,
    including a multi-day request. Because summaries have no per-day key, a
    nonzero ``1d`` summary is contradictory only for an exact-day request.
    Multi-day requests never supply either positive proof.
    """

    normalized_code = str(instrument_code or "").strip().upper()
    normalized_dates = sorted(set(trading_dates))
    if not normalized_code or not normalized_dates:
      return []
    normalized_date_strings = [value.isoformat() for value in normalized_dates]
    bounded_limit = max(1, min(len(normalized_dates), 100))
    async with self.engine.connect() as connection:
      values = (
        await connection.execute(
          text(
            """
            SELECT DISTINCT ON (tick_coverage.value ->> 'trading_date')
              tick_coverage.value ->> 'trading_date' AS trading_date,
              tick_coverage.value ->> 'point_count' AS point_count
            FROM market_data_request AS tick_request
            CROSS JOIN LATERAL json_array_elements(
              COALESCE(
                tick_request.ingestion_result -> 'day_coverage',
                '[]'::json
              )
            ) AS tick_coverage(value)
            WHERE tick_request.status = 'COMPLETED'
              AND tick_request.ingestion_result
                    -> 'persistence_verification' ->> 'status' = 'verified'
              AND tick_coverage.value ->> 'instrument_code' = :instrument_code
              AND LOWER(COALESCE(tick_coverage.value ->> 'period', '')) = 'tick'
              AND tick_coverage.value ->> 'trading_date' IN :trading_dates
              AND tick_coverage.value ->> 'point_count' = '0'
              AND tick_request.request_payload ->> 'start_time' =
                  REPLACE(tick_coverage.value ->> 'trading_date', '-', '')
              AND tick_request.request_payload ->> 'end_time' =
                  REPLACE(tick_coverage.value ->> 'trading_date', '-', '')
              AND EXISTS (
                SELECT 1
                FROM json_array_elements(
                  COALESCE(
                    tick_request.ingestion_result -> 'code_summaries',
                    '[]'::json
                  )
                ) AS tick_summary(value)
                WHERE tick_summary.value ->> 'code' = :instrument_code
                  AND LOWER(COALESCE(tick_summary.value ->> 'period', '')) = 'tick'
                  AND tick_summary.value ->> 'row_count' = '0'
                  AND tick_summary.value ->> 'no_data_reason' = 'XT_DATA_NO_ROWS'
              )
              AND EXISTS (
                SELECT 1
                FROM market_data_request AS daily_request
                CROSS JOIN LATERAL json_array_elements(
                  COALESCE(
                    daily_request.ingestion_result -> 'day_coverage',
                    '[]'::json
                  )
                ) AS daily_coverage(value)
                WHERE daily_request.status = 'COMPLETED'
                  AND daily_request.ingestion_result
                        -> 'persistence_verification' ->> 'status' = 'verified'
                  AND daily_request.request_payload ->> 'start_time' =
                      REPLACE(tick_coverage.value ->> 'trading_date', '-', '')
                  AND daily_request.request_payload ->> 'end_time' =
                      REPLACE(tick_coverage.value ->> 'trading_date', '-', '')
                  AND daily_coverage.value ->> 'instrument_code' = :instrument_code
                  AND LOWER(COALESCE(daily_coverage.value ->> 'period', '')) = '1d'
                  AND daily_coverage.value ->> 'trading_date' =
                      tick_coverage.value ->> 'trading_date'
                  AND daily_coverage.value ->> 'point_count' = '0'
                  AND EXISTS (
                    SELECT 1
                    FROM json_array_elements(
                      COALESCE(
                        daily_request.ingestion_result -> 'code_summaries',
                        '[]'::json
                      )
                    ) AS daily_summary(value)
                    WHERE daily_summary.value ->> 'code' = :instrument_code
                      AND LOWER(
                        COALESCE(daily_summary.value ->> 'period', '')
                      ) = '1d'
                      AND daily_summary.value ->> 'row_count' = '0'
                      AND daily_summary.value ->> 'no_data_reason' =
                          'XT_DATA_NO_ROWS'
                  )
              )
              AND NOT EXISTS (
                SELECT 1
                FROM market_data_request AS daily_nonempty_request
                WHERE daily_nonempty_request.status = 'COMPLETED'
                  AND daily_nonempty_request.ingestion_result
                        -> 'persistence_verification' ->> 'status' = 'verified'
                  AND (
                    EXISTS (
                      SELECT 1
                      FROM json_array_elements(
                        COALESCE(
                          daily_nonempty_request.ingestion_result
                            -> 'day_coverage',
                          '[]'::json
                        )
                      ) AS daily_nonempty_coverage(value)
                      WHERE daily_nonempty_coverage.value ->> 'instrument_code' =
                            :instrument_code
                        AND LOWER(
                          COALESCE(
                            daily_nonempty_coverage.value ->> 'period',
                            ''
                          )
                        ) = '1d'
                        AND daily_nonempty_coverage.value ->> 'trading_date' =
                            tick_coverage.value ->> 'trading_date'
                        AND COALESCE(
                          daily_nonempty_coverage.value ->> 'point_count',
                          ''
                        ) <> '0'
                    )
                    OR (
                      daily_nonempty_request.request_payload ->> 'start_time' =
                        REPLACE(tick_coverage.value ->> 'trading_date', '-', '')
                      AND daily_nonempty_request.request_payload ->> 'end_time' =
                        REPLACE(tick_coverage.value ->> 'trading_date', '-', '')
                      AND EXISTS (
                        SELECT 1
                        FROM json_array_elements(
                          COALESCE(
                            daily_nonempty_request.ingestion_result
                              -> 'code_summaries',
                            '[]'::json
                          )
                        ) AS daily_nonempty_summary(value)
                        WHERE daily_nonempty_summary.value ->> 'code' =
                              :instrument_code
                          AND LOWER(
                            COALESCE(
                              daily_nonempty_summary.value ->> 'period',
                              ''
                            )
                          ) = '1d'
                          AND COALESCE(
                            daily_nonempty_summary.value ->> 'row_count',
                            ''
                          ) <> '0'
                      )
                    )
                  )
              )
            ORDER BY
              tick_coverage.value ->> 'trading_date',
              tick_request.completed_at DESC NULLS LAST,
              tick_request.updated_at DESC,
              tick_request.request_id DESC
            LIMIT :limit
            """
          ).bindparams(
            bindparam("trading_dates", expanding=True),
          ),
          {
            "instrument_code": normalized_code,
            "trading_dates": normalized_date_strings,
            "limit": bounded_limit,
          },
        )
      ).mappings()
      return [dict(value) for value in values]

  async def recoverable_market_data_request_ids(
    self,
    *,
    limit: int = 20,
  ) -> list[str]:
    """Return immutable uploads and expired ingestion leases for Worker recovery.

    Claiming remains a separate compare-and-set transition. This read is only a
    bounded discovery pass, so concurrent Workers can safely observe the same
    row while exactly one claim succeeds.
    """

    bounded_limit = max(1, min(int(limit), 100))
    stale_before = _utcnow() - timedelta(minutes=5)
    async with self.engine.connect() as connection:
      values = (
        await connection.execute(
          text(
            """
            SELECT request_id
            FROM market_data_request
            WHERE status = 'UPLOADED'
               OR (status = 'PROCESSING' AND updated_at < :stale_before)
            ORDER BY updated_at ASC, created_at ASC
            LIMIT :limit
            """
          ),
          {
            "stale_before": stale_before,
            "limit": bounded_limit,
          },
        )
      ).scalars()
      return [str(value) for value in values if value]

  async def requeue_expired_market_data_delivery_leases(
    self,
    *,
    limit: int = 20,
  ) -> list[str]:
    """Return abandoned Agent delivery leases to the durable request queue.

    A WebSocket reconnect can discard an Agent's in-memory receive queue before
    native preparation begins.  Reclaiming an expired ``DELIVERED`` or
    ``RECEIVING`` lease from a Worker makes that loss recoverable even when the
    Agent stays connected afterwards.  The update never touches immutable
    uploads or an active ingestion claim, and the Agent's request-id
    deduplication makes a stale active redelivery safe.
    """

    bounded_limit = max(1, min(int(limit), 100))
    requeued_at = _utcnow()
    stale_before = requeued_at - timedelta(minutes=5)
    async with self.engine.begin() as connection:
      values = (
        await connection.execute(
          text(
            """
            WITH expired AS (
              SELECT request_id
              FROM market_data_request
              WHERE status IN ('DELIVERED', 'RECEIVING')
                AND updated_at < :stale_before
              ORDER BY updated_at ASC, created_at ASC
              LIMIT :limit
              FOR UPDATE SKIP LOCKED
            )
            UPDATE market_data_request AS market_request
            SET status = 'QUEUED',
                updated_at = :requeued_at
            FROM expired
            WHERE market_request.request_id = expired.request_id
            RETURNING market_request.request_id
            """
          ),
          {
            "stale_before": stale_before,
            "requeued_at": requeued_at,
            "limit": bounded_limit,
          },
        )
      ).scalars()
      return sorted(str(value) for value in values if value)

  async def finish_market_data_request(
    self,
    request_id: str,
    *,
    status: str,
    error: str = "",
    ingestion_result: Optional[dict[str, Any]] = None,
    claim_token: Optional[str] = None,
  ) -> None:
    if status not in {"COMPLETED", "FAILED"}:
      raise ValueError("market-data terminal status must be COMPLETED or FAILED")
    if status == "COMPLETED" and not isinstance(ingestion_result, dict):
      raise ValueError("COMPLETED market-data request requires an ingestion_result")
    if status == "FAILED" and ingestion_result is not None:
      raise ValueError("FAILED market-data request cannot have an ingestion_result")
    normalized_claim_token = str(claim_token or "").strip() or None
    encoded_result = (
      json.dumps(
        ingestion_result,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
      )
      if ingestion_result is not None
      else None
    )
    async with self.engine.begin() as connection:
      updated_status = (
        await connection.execute(
          text(
            """
            UPDATE market_data_request
             SET status = :status,
                 processing_error = :error,
                 ingestion_result = CAST(:ingestion_result AS JSON),
                 processing_claim_token = NULL,
                 completed_at = :completed_at,
                 updated_at = :completed_at
             WHERE request_id = :request_id
               AND status NOT IN ('COMPLETED', 'FAILED')
               AND (
                 (
                   CAST(:claim_token AS TEXT) IS NULL
                   AND status <> 'PROCESSING'
                 )
                 OR (
                   status = 'PROCESSING'
                   AND processing_claim_token = CAST(:claim_token AS TEXT)
                 )
               )
             RETURNING status
             """
          ),
          {
            "request_id": request_id,
            "status": status,
            "error": error[:2000] or None,
            "ingestion_result": encoded_result,
            "claim_token": normalized_claim_token,
            "completed_at": _utcnow(),
          },
        )
      ).scalar_one_or_none()
      if updated_status is not None:
        return
      existing_status = (
        await connection.execute(
          text(
            """
            SELECT status, ingestion_result, processing_claim_token
            FROM market_data_request
            WHERE request_id = :request_id
            """
          ),
          {"request_id": request_id},
        )
      ).mappings().one_or_none()
      if existing_status is None:
        raise RuntimeError("market-data request disappeared before terminal transition")
      if str(existing_status["status"]) == "PROCESSING" or (
        normalized_claim_token is not None
        and str(existing_status["status"]) not in {"COMPLETED", "FAILED"}
      ):
        raise RuntimeError("market-data processing claim was lost")
      if str(existing_status["status"]) == status:
        existing_result = existing_status["ingestion_result"]
        if isinstance(existing_result, str):
          existing_result = json.loads(existing_result)
        if status == "FAILED" or existing_result == ingestion_result:
          return
        raise RuntimeError("market-data COMPLETED ingestion_result conflict")
      raise RuntimeError(
        "market-data terminal state conflict: "
        f"existing={existing_status['status']} requested={status}"
      )

  async def reopen_failed_market_data_request(
    self,
    request_id: str,
  ) -> dict[str, Any]:
    """Reopen a failed request only when its persisted transfer is complete."""
    reopened_at = _utcnow()
    async with self.engine.begin() as connection:
      evidence = (
        await connection.execute(
          text(
            """
            WITH candidate AS MATERIALIZED (
              SELECT
                market_request.request_id,
                market_request.processing_error AS old_processing_error,
                market_request.expected_chunks,
                market_request.received_chunks,
                manifest.manifest_count,
                manifest.manifest_records
              FROM market_data_request AS market_request
              CROSS JOIN LATERAL (
                SELECT
                  COUNT(*)::INTEGER AS manifest_count,
                  COALESCE(SUM(transfer.record_count), 0)::BIGINT
                    AS manifest_records
                FROM market_data_transfer AS transfer
                WHERE transfer.request_id = market_request.request_id
              ) AS manifest
              WHERE market_request.request_id = :request_id
                AND market_request.status = 'FAILED'
                AND market_request.expected_chunks IS NOT NULL
                AND market_request.expected_chunks > 0
                AND market_request.expected_chunks =
                    market_request.received_chunks
              FOR UPDATE OF market_request
            ),
            reopened AS (
              UPDATE market_data_request AS market_request
              SET status = 'UPLOADED',
                  processing_error = NULL,
                  ingestion_result = NULL,
                  processing_claim_token = NULL,
                  completed_at = NULL,
                  updated_at = :reopened_at
              FROM candidate
              WHERE market_request.request_id = candidate.request_id
                AND market_request.status = 'FAILED'
                AND market_request.expected_chunks =
                    candidate.expected_chunks
                AND market_request.received_chunks =
                    candidate.received_chunks
                AND candidate.manifest_count =
                    candidate.expected_chunks
                AND candidate.manifest_records > 0
              RETURNING market_request.request_id, market_request.status
            )
            SELECT
              reopened.request_id,
              reopened.status,
              candidate.old_processing_error,
              candidate.expected_chunks,
              candidate.received_chunks,
              candidate.manifest_count,
              candidate.manifest_records
            FROM reopened
            JOIN candidate ON candidate.request_id = reopened.request_id
            """
          ),
          {
            "request_id": request_id,
            "reopened_at": reopened_at,
          },
        )
      ).mappings().one_or_none()
      if evidence is None:
        raise RuntimeError(
          "market-data request is not safely reopenable: "
          "requires FAILED status, complete non-empty chunk manifest, "
          "and matching positive request chunk counts"
        )
      return {
        "request_id": str(evidence["request_id"]),
        "status": str(evidence["status"]),
        "old_processing_error": evidence["old_processing_error"],
        "expected_chunks": int(evidence["expected_chunks"]),
        "received_chunks": int(evidence["received_chunks"]),
        "manifest_count": int(evidence["manifest_count"]),
        "manifest_records": int(evidence["manifest_records"]),
      }

  async def claim_market_data_request(self, request_id: str) -> str | None:
    """Claim an uploaded request, recovering a stale interrupted claim."""
    updated_at = _utcnow()
    stale_before = updated_at - timedelta(minutes=5)
    claim_token = str(uuid.uuid4())
    async with self.engine.begin() as connection:
      value = (
        await connection.execute(
          text(
            """
            UPDATE market_data_request
            SET status = 'PROCESSING',
                processing_error = NULL,
                processing_claim_token = :claim_token,
                updated_at = :updated_at
            WHERE request_id = :request_id
              AND (
                status = 'UPLOADED'
                OR (
                  status = 'PROCESSING'
                  AND updated_at < :stale_before
                )
              )
            RETURNING processing_claim_token
            """
          ),
          {
            "request_id": request_id,
            "claim_token": claim_token,
            "updated_at": updated_at,
            "stale_before": stale_before,
          },
        )
      ).scalar_one_or_none()
    return str(value) if value is not None else None

  async def renew_market_data_request_claim(
    self,
    request_id: str,
    *,
    claim_token: str,
  ) -> bool:
    """Renew the lease of the sole live ingestion owner."""

    normalized_claim_token = str(claim_token or "").strip()
    if not normalized_claim_token:
      raise ValueError("market-data claim_token is required")
    async with self.engine.begin() as connection:
      value = (
        await connection.execute(
          text(
            """
            UPDATE market_data_request
            SET updated_at = :updated_at
            WHERE request_id = :request_id
              AND status = 'PROCESSING'
              AND processing_claim_token = :claim_token
            RETURNING request_id
            """
          ),
          {
            "request_id": request_id,
            "claim_token": normalized_claim_token,
            "updated_at": _utcnow(),
          },
        )
      ).scalar_one_or_none()
    return value is not None

  async def release_market_data_request_claim(
    self,
    request_id: str,
    *,
    claim_token: str,
    error: str,
  ) -> bool:
    """Return a retryable persistence failure to immutable UPLOADED state."""

    normalized_claim_token = str(claim_token or "").strip()
    if not normalized_claim_token:
      raise ValueError("market-data claim_token is required")
    async with self.engine.begin() as connection:
      value = (
        await connection.execute(
          text(
            """
            UPDATE market_data_request
            SET status = 'UPLOADED',
                processing_error = :error,
                processing_claim_token = NULL,
                updated_at = :updated_at
            WHERE request_id = :request_id
              AND status = 'PROCESSING'
              AND processing_claim_token = :claim_token
            RETURNING request_id
            """
          ),
          {
            "request_id": request_id,
            "claim_token": normalized_claim_token,
            "error": str(error or "")[:2000] or None,
            "updated_at": _utcnow(),
          },
        )
      ).scalar_one_or_none()
    return value is not None

  async def component_status(self, component_prefix: str) -> list[dict[str, Any]]:
    async with self.engine.connect() as connection:
      values = (
        await connection.execute(
          text(
            """
            SELECT component, instance_id, status, details, updated_at
            FROM runtime_component_heartbeats
            WHERE component LIKE :prefix
            ORDER BY updated_at DESC
            """
          ),
          {"prefix": f"{component_prefix}%"},
        )
      ).mappings()
      return [dict(value) for value in values]

  async def instrument_codes(
    self,
    limit: int = 10000,
    *,
    instrument_type: Optional[str] = None,
  ) -> list[str]:
    normalized_type = (
      str(instrument_type).strip().upper() if instrument_type else None
    )
    async with self.engine.connect() as connection:
      values = (
        await connection.execute(
          text(
            """
            SELECT code
            FROM instruments
            WHERE code IS NOT NULL
              AND (
                CAST(:instrument_type AS TEXT) IS NULL
                OR UPPER(instrument_type::text) = CAST(:instrument_type AS TEXT)
              )
            ORDER BY code
            LIMIT :limit
            """
          ),
          {
            "limit": max(1, min(int(limit), 100000)),
            "instrument_type": normalized_type,
          },
        )
      ).scalars()
      return [str(value) for value in values if value]
