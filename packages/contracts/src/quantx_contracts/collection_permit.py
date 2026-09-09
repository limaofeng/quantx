"""Canonical native work-unit planning and expiring collection permission identities.

Callers validate the complete request before planning; splitting cannot reset its
record budget. This module contains no broker, database, or network dependency.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

COLLECTION_PLAN_VERSION = "xtdata-units-v1"

HISTORICAL_WORK_UNIT_INSTRUMENTS = 20
HISTORICAL_TICK_WORK_UNIT_INSTRUMENTS = 10
HISTORICAL_WORK_UNIT_WINDOW_DAYS = {
  "tick": 1,
  "1m": 1,
  "1d": 31,
}


def _chunks(values: list[str], size: int) -> Iterator[list[str]]:
  if not values:
    return
  group_count = (len(values) + size - 1) // size
  group_size, larger_groups = divmod(len(values), group_count)
  offset = 0
  for index in range(group_count):
    width = group_size + (1 if index < larger_groups else 0)
    yield values[offset : offset + width]
    offset += width


def _date_windows(
  start_text: str,
  end_text: str,
  *,
  max_days: int,
) -> Iterator[tuple[str, str]]:
  start = datetime.strptime(start_text, "%Y%m%d").date()
  end = datetime.strptime(end_text, "%Y%m%d").date()
  cursor = start
  while cursor <= end:
    window_end = min(end, cursor + timedelta(days=max_days - 1))
    yield cursor.strftime("%Y%m%d"), window_end.strftime("%Y%m%d")
    cursor = window_end + timedelta(days=1)


def plan_historical_work_units(
  payload: dict[str, Any],
  *,
  instrument_batch_size: int | None = None,
) -> tuple[dict[str, Any], ...]:
  """Plan bounded, preemptible native calls for one validated request."""

  if instrument_batch_size is not None and not 10 <= instrument_batch_size <= 30:
    raise ValueError("historical work-unit size must be between 10 and 30")
  operation = str(payload.get("operation") or "bars")
  raw_codes = payload.get("stock_list")
  codes = list(raw_codes) if isinstance(raw_codes, list) else []
  if not codes:
    return (dict(payload),)

  units: list[dict[str, Any]] = []
  if operation == "bars":
    raw_periods = payload.get("periods") or ["1d"]
    periods = list(raw_periods) if isinstance(raw_periods, list) else []
    if not periods:
      return (dict(payload),)
    # Broker output is period-major and instrument-sorted. Matching that order
    # keeps the complete request manifest deterministic across retries.
    ordered_codes = sorted(codes)
    for period in periods:
      batch_size = instrument_batch_size or (
        HISTORICAL_TICK_WORK_UNIT_INSTRUMENTS
        if period == "tick"
        else HISTORICAL_WORK_UNIT_INSTRUMENTS
      )
      for code_batch in _chunks(ordered_codes, batch_size):
        for start_text, end_text in _date_windows(
          str(payload["start_time"]),
          str(payload["end_time"]),
          max_days=HISTORICAL_WORK_UNIT_WINDOW_DAYS[period],
        ):
          units.append(
            {
              **payload,
              "stock_list": code_batch,
              "periods": [period],
              "start_time": start_text,
              "end_time": end_text,
            }
          )
    return tuple(units)

  batch_size = instrument_batch_size or HISTORICAL_WORK_UNIT_INSTRUMENTS
  for code_batch in _chunks(sorted(codes), batch_size):
    units.append({**payload, "stock_list": code_batch})
  return tuple(units)


def native_payload_sha256(payload: dict[str, Any]) -> str:
  """Bind every native parameter; exclude only the envelope's transport metadata."""
  native = {
    key: value
    for key, value in payload.items()
    if key not in {"request_id", "upload_path", "collection_permit"}
  }
  return hashlib.sha256(
    json.dumps(native, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
  ).hexdigest()


class CollectionUnit(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  plan_version: Literal["xtdata-units-v1"] = COLLECTION_PLAN_VERSION
  request_id: UUID
  unit_index: int = Field(ge=0, strict=True)
  payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

  @property
  def unit_id(self) -> str:
    return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

  @classmethod
  def from_payload(cls, request_id: str, unit_index: int, payload: dict[str, Any]):
    return cls(
      request_id=UUID(request_id),
      unit_index=unit_index,
      payload_sha256=native_payload_sha256(payload),
    )


class CollectionPermit(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  permit_id: UUID
  device_id: UUID
  owner_epoch: int = Field(gt=0, strict=True)
  unit: CollectionUnit
  issued_at: AwareDatetime
  expires_at: AwareDatetime

  @model_validator(mode="after")
  def bounded_validity(self):
    if not timedelta(0) < self.expires_at - self.issued_at <= timedelta(seconds=30):
      raise ValueError(
        "collection permit validity must be positive and at most 30 seconds"
      )
    return self

  def validate_start(
    self,
    *,
    device_id: str,
    unit: CollectionUnit,
    now: datetime | None = None,
    minimum_epoch: int = 0,
  ) -> None:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
      raise ValueError("collection permit requires an aware clock")
    if self.device_id != UUID(device_id) or self.unit != unit:
      raise ValueError("collection permit scope mismatch")
    if self.owner_epoch < minimum_epoch:
      raise ValueError("collection permit owner is stale")
    if not self.issued_at <= current < self.expires_at:
      raise ValueError("collection permit is expired or not active")
