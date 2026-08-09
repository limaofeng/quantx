"""Small SQLAlchemy-Core adapter for cross-process runtime coordination."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


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

  async def create_market_data_request(
    self,
    payload: dict[str, Any],
    *,
    device_id: Optional[str] = None,
  ) -> str:
    encoded = json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    )
    idempotency_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
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
      selected_device_id = ""
      for row in rows:
        capabilities = row["capabilities"]
        if isinstance(capabilities, str):
          capabilities = json.loads(capabilities)
        if "market-data" in list(capabilities or []):
          selected_device_id = str(row["id"])
          break
      if not selected_device_id:
        if device_id:
          raise RuntimeError(
            "Requested QMT Agent is unavailable or lacks market-data capability"
          )
        raise RuntimeError("No registered market-data QMT Agent is available")
      request_id = str(uuid.uuid4())
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
      return request_id

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
                   received_chunks, processing_error
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
                   compressed, storage_reference
            FROM market_data_transfer
            WHERE request_id = :request_id
            ORDER BY chunk_index
            """
          ),
          {"request_id": request_id},
        )
      ).mappings()
      return [dict(value) for value in values]

  async def finish_market_data_request(
    self,
    request_id: str,
    *,
    status: str,
    error: str = "",
  ) -> None:
    if status not in {"COMPLETED", "FAILED"}:
      raise ValueError("market-data terminal status must be COMPLETED or FAILED")
    async with self.engine.begin() as connection:
      updated_status = (
        await connection.execute(
          text(
            """
            UPDATE market_data_request
             SET status = :status,
                 processing_error = :error,
                 completed_at = :completed_at,
                 updated_at = :completed_at
             WHERE request_id = :request_id
               AND status NOT IN ('COMPLETED', 'FAILED')
             RETURNING status
             """
          ),
          {
            "request_id": request_id,
            "status": status,
            "error": error[:2000] or None,
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
            SELECT status
            FROM market_data_request
            WHERE request_id = :request_id
            """
          ),
          {"request_id": request_id},
        )
      ).scalar_one_or_none()
      if existing_status is None or existing_status == status:
        return
      raise RuntimeError(
        "market-data terminal state conflict: "
        f"existing={existing_status} requested={status}"
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

  async def claim_market_data_request(self, request_id: str) -> bool:
    """Claim an uploaded request, recovering a stale interrupted claim."""
    updated_at = _utcnow()
    stale_before = updated_at - timedelta(minutes=5)
    async with self.engine.begin() as connection:
      value = (
        await connection.execute(
          text(
            """
            UPDATE market_data_request
            SET status = 'PROCESSING',
                processing_error = NULL,
                updated_at = :updated_at
            WHERE request_id = :request_id
              AND (
                status = 'UPLOADED'
                OR (
                  status = 'PROCESSING'
                  AND updated_at < :stale_before
                )
              )
            RETURNING request_id
            """
          ),
          {
            "request_id": request_id,
            "updated_at": updated_at,
            "stale_before": stale_before,
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

  async def instrument_codes(self, limit: int = 10000) -> list[str]:
    async with self.engine.connect() as connection:
      values = (
        await connection.execute(
          text(
            """
            SELECT code
            FROM instruments
            WHERE code IS NOT NULL
            ORDER BY code
            LIMIT :limit
            """
          ),
          {"limit": max(1, min(int(limit), 100000))},
        )
      ).scalars()
      return [str(value) for value in values if value]
