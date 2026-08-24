"""Repository for durable strategy decision audit records."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.strategy_decision_trace_record import (
  StrategyDecisionTraceRecord,
)

_IMMUTABLE_TRACE_FIELDS = (
  "id",
  "trace_id",
  "strategy_run_id",
  "strategy_id",
  "instrument_code",
  "decided_at",
  "input_summary",
  "output_summary",
  "trade_intents",
  "state_patch",
  "decision_trace",
)
# Eleven explicit trace columns × 256 stays well below PostgreSQL's 32767 bind
# parameter ceiling while avoiding asyncpg executemany + RETURNING degradation.
_TRACE_APPEND_INSERT_CHUNK_SIZE = 256


def _normalize_decided_at(value: Any) -> Any:
  """Keep PostgreSQL ``TIMESTAMP WITHOUT TIME ZONE`` values UTC-naive.

  Decision traces are ordered by the instant at which the StrategyOutput was
  produced.  PostgreSQL's mapped column deliberately has no timezone, so an
  aware input must be converted to its UTC wall-clock representation before it
  reaches asyncpg.  Existing naive values are already the repository contract
  and remain unchanged.
  """

  if isinstance(value, datetime) and value.tzinfo is not None:
    return value.astimezone(timezone.utc).replace(tzinfo=None)
  return value


def _normalized_trace_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
  """Normalize the immutable database boundary without changing record IDs."""

  normalized = dict(payload)
  if "decided_at" in normalized:
    normalized["decided_at"] = _normalize_decided_at(normalized["decided_at"])
  return normalized


def _canonical_trace_value(value: Any) -> Any:
  if isinstance(value, datetime):
    value = _normalize_decided_at(value)
    return value.isoformat(timespec="microseconds")
  if isinstance(value, Mapping):
    return {
      str(key): _canonical_trace_value(item)
      for key, item in value.items()
    }
  if isinstance(value, (list, tuple)):
    return [_canonical_trace_value(item) for item in value]
  return value


def _immutable_trace_fingerprint(payload: Mapping[str, Any]) -> str:
  """Hash exactly the immutable content protected by a stable trace UUID."""

  canonical = {
    field: _canonical_trace_value(payload.get(field))
    for field in _IMMUTABLE_TRACE_FIELDS
  }
  encoded = json.dumps(
    canonical,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
  )
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class StrategyDecisionTraceRepository(BaseRepository[StrategyDecisionTraceRecord]):
  """Strategy decision trace repository."""

  model_class = StrategyDecisionTraceRecord

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def create_trace(self, trace_data: Dict[str, Any]) -> StrategyDecisionTraceRecord:
    payload = _normalized_trace_payload(trace_data or {})
    payload.setdefault("id", str(uuid.uuid4()))
    record = StrategyDecisionTraceRecord(**payload)
    self.db.add(record)
    await self.db.commit()
    await self.db.refresh(record)
    return record

  async def append_traces(
    self,
    trace_data: Iterable[Mapping[str, Any]],
    *,
    commit: bool = True,
    flush: bool = True,
  ) -> list[StrategyDecisionTraceRecord]:
    """Append a bounded batch without changing any trace identity.

    ``RuntimeStateManager`` supplies records with stable UUIDs and may place
    this append in the same transaction as its RuntimeState CAS.  Keeping the
    append caller-owned avoids a second remote commit per evaluated Tick while
    preserving append-only trace rows and all-or-nothing failure semantics.
    """

    payloads = [_normalized_trace_payload(payload) for payload in trace_data]
    if not payloads:
      return []
    trace_ids = [str(payload.get("id") or "") for payload in payloads]
    if any(not trace_id for trace_id in trace_ids):
      raise ValueError("decision trace append requires stable record ids")
    if len(trace_ids) != len(set(trace_ids)):
      raise ValueError("decision trace append batch contains duplicate record ids")
    payload_by_id = {
      trace_id: {**payload, "id": trace_id}
      for trace_id, payload in zip(trace_ids, payloads)
    }
    payloads = list(payload_by_id.values())

    # A commit response can be lost after PostgreSQL has durably committed the
    # RuntimeState CAS and this append.  The manager then retries the exact
    # stable UUID batch after reconciling the CAS winner.  PostgreSQL is the
    # authoritative relational store, so make that replay an idempotent no-op
    # rather than turning it into a permanent primary-key failure.
    inserted_ids: set[str] = set()
    for start in range(0, len(payloads), _TRACE_APPEND_INSERT_CHUNK_SIZE):
      chunk = payloads[start : start + _TRACE_APPEND_INSERT_CHUNK_SIZE]
      # ``values(chunk)`` produces one bounded multi-row INSERT.  Passing the
      # chunk as a second execute argument selects SQLAlchemy executemany,
      # which asyncpg handles poorly together with RETURNING at day scale.
      statement = (
        insert(StrategyDecisionTraceRecord)
        .values(chunk)
        .on_conflict_do_nothing(
          index_elements=[StrategyDecisionTraceRecord.id]
        )
        .returning(StrategyDecisionTraceRecord.id)
      )
      result = await self.db.execute(statement)
      inserted_ids.update(str(value) for value in result.scalars().all())
    replayed_ids = set(trace_ids) - inserted_ids
    if replayed_ids:
      existing_result = await self.db.execute(
        select(StrategyDecisionTraceRecord).where(
          StrategyDecisionTraceRecord.id.in_(sorted(replayed_ids))
        )
      )
      existing_by_id = {
        str(record.id): record
        for record in existing_result.scalars().all()
      }
      missing_ids = replayed_ids - set(existing_by_id)
      conflicting_ids = [
        trace_id
        for trace_id in replayed_ids
        if trace_id in existing_by_id
        and _immutable_trace_fingerprint(
          payload_by_id[trace_id]
        )
        != _immutable_trace_fingerprint(
          {
            field: getattr(existing_by_id[trace_id], field)
            for field in _IMMUTABLE_TRACE_FIELDS
          }
        )
      ]
      if missing_ids or conflicting_ids:
        raise ValueError(
          "decision trace idempotency conflict: "
          f"missing={sorted(missing_ids)} conflicting={sorted(conflicting_ids)}"
        )
    if commit:
      await self.db.commit()
    elif flush:
      await self.db.flush()
    return [StrategyDecisionTraceRecord(**payload) for payload in payloads]

  async def find_by_strategy_run(
    self,
    strategy_run_id: str,
    *,
    limit: int = 50,
    cursor: Optional[str] = None,
  ) -> List[StrategyDecisionTraceRecord]:
    stmt = (
      select(StrategyDecisionTraceRecord)
      .filter(StrategyDecisionTraceRecord.strategy_run_id == strategy_run_id)
      .order_by(desc(StrategyDecisionTraceRecord.decided_at), desc(StrategyDecisionTraceRecord.created_at))
      .limit(max(1, min(int(limit or 50), 200)))
    )
    if cursor:
      stmt = stmt.filter(StrategyDecisionTraceRecord.id < cursor)
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_by_trace_id(
    self,
    strategy_run_id: str,
    trace_id: str,
  ) -> List[StrategyDecisionTraceRecord]:
    stmt = (
      select(StrategyDecisionTraceRecord)
      .filter(StrategyDecisionTraceRecord.strategy_run_id == strategy_run_id)
      .filter(StrategyDecisionTraceRecord.trace_id == trace_id)
      .order_by(desc(StrategyDecisionTraceRecord.decided_at), desc(StrategyDecisionTraceRecord.created_at))
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())
