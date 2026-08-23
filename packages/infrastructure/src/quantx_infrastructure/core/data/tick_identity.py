"""Lossless identity, ordering, and storage-time helpers for Tick snapshots."""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from quantx_contracts import HISTORICAL_TICK_ORDINALS_PER_MILLISECOND

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.tick import Tick

_IDENTITY_FIELDS = frozenset(
  {
    "stock_code",
    "period",
    "last_price",
    "open",
    "high",
    "low",
    "last_close",
    "amount",
    "volume",
    "open_int",
    "transaction_num",
    "ask_price",
    "bid_price",
    "ask_vol",
    "bid_vol",
  }
)
_IDENTITY_SCALAR_PRECISION = {
  "last_price": 3,
  "open": 3,
  "high": 3,
  "low": 3,
  "last_close": 3,
  "amount": 2,
  "volume": 2,
}
_IDENTITY_LIST_PRECISION = {
  "ask_price": 3,
  "bid_price": 3,
  "ask_vol": 2,
  "bid_vol": 2,
}
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _canonical_value(value: Any) -> Any:
  """Return a JSON-safe value with stable treatment of common scalar types."""

  if value is None or isinstance(value, (str, bool)):
    return value
  if isinstance(value, Enum):
    return _canonical_value(value.value)
  if isinstance(value, datetime):
    return value.isoformat()
  if isinstance(value, bytes):
    return {"$bytes": value.hex()}
  if isinstance(value, Mapping):
    return {
      str(key): _canonical_value(item)
      for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
  if isinstance(value, (list, tuple)):
    return [_canonical_value(item) for item in value]
  if isinstance(value, (set, frozenset)):
    canonical = [_canonical_value(item) for item in value]
    return sorted(canonical, key=_canonical_json)

  # Normalize numpy/pandas scalar values without importing either package here.
  scalar_item = getattr(value, "item", None)
  if callable(scalar_item):
    try:
      item = scalar_item()
    except (TypeError, ValueError):
      item = value
    if item is not value:
      return _canonical_value(item)

  if isinstance(value, int):
    return value
  if isinstance(value, float):
    if math.isnan(value):
      return {"$float": "nan"}
    if math.isinf(value):
      return {"$float": "inf" if value > 0 else "-inf"}
    # 1 and 1.0 are the same market-data value across wire/database codecs.
    return int(value) if value.is_integer() else value
  return str(value)


def _canonical_json(value: Any) -> str:
  return json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
  )


def _tick_fields(tick: Tick) -> dict[str, Any]:
  fields: dict[str, Any] = {}
  if dataclasses.is_dataclass(tick):
    for field in dataclasses.fields(tick):
      try:
        fields[field.name] = getattr(tick, field.name)
      except AttributeError:
        continue
  fields.update(vars(tick))
  return fields


def _rounded_market_value(value: Any, digits: int) -> Any:
  try:
    number = float(value)
  except (TypeError, ValueError, OverflowError):
    return _canonical_value(value)
  if not math.isfinite(number):
    return _canonical_value(value)
  rounded = round(number, digits)
  return int(rounded) if rounded.is_integer() else rounded


def _identity_field_value(name: str, value: Any) -> Any:
  precision = _IDENTITY_SCALAR_PRECISION.get(name)
  if precision is not None:
    return _rounded_market_value(value, precision)
  precision = _IDENTITY_LIST_PRECISION.get(name)
  if precision is not None and isinstance(value, (list, tuple)):
    return [_rounded_market_value(item, precision) for item in value]
  return _canonical_value(value)


def tick_source_time_ms(tick: Tick) -> int:
  """Return the raw source timestamp in Unix milliseconds for ``tick``.

  Persisted ticks carry ``source_time_ms``. Legacy and warm-cache ticks derive it
  by flooring their timestamp to the source's millisecond precision.
  """

  source_value = getattr(tick, "source_time_ms", None)
  missing_source = source_value is None or type(source_value).__name__ in {
    "NAType",
    "NaTType",
  }
  if isinstance(source_value, str) and source_value.strip() in {"", "0"}:
    missing_source = True
  if isinstance(source_value, bool):
    raise ValueError(f"invalid Tick source_time_ms: {source_value!r}")
  if not missing_source:
    try:
      source_number = float(source_value)
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError(f"invalid Tick source_time_ms: {source_value!r}") from exc
    if math.isnan(source_number) or source_number == 0:
      missing_source = True
    elif not math.isfinite(source_number) or not source_number.is_integer():
      raise ValueError(f"invalid Tick source_time_ms: {source_value!r}")
    elif source_number < 0:
      raise ValueError(f"invalid Tick source_time_ms: {source_value!r}")
    else:
      return int(source_number)

  tick_time = getattr(tick, "time", None)
  if hasattr(tick_time, "to_pydatetime"):
    tick_time = tick_time.to_pydatetime()
  if not isinstance(tick_time, datetime):
    raise ValueError("Tick must contain a datetime time or integer source_time_ms")

  utc_time = time_utils.to_utc(tick_time)
  delta = utc_time - _EPOCH_UTC
  return (
    delta.days * 86_400_000
    + delta.seconds * 1_000
    + delta.microseconds // 1_000
  )


def tick_snapshot_identity(tick: Tick) -> str:
  """Return the stable identity shared by historical and realtime Tick codecs."""

  fields = _tick_fields(tick)
  payload = {
    name: _identity_field_value(name, fields[name])
    for name in sorted(_IDENTITY_FIELDS)
    if name in fields
  }
  return _canonical_json(payload)


def _full_snapshot_payload(tick: Tick) -> str:
  payload = {
    name: _canonical_value(value)
    for name, value in _tick_fields(tick).items()
    if name not in {"time", "source_time_ms", "tick_ordinal"}
  }
  return _canonical_json(payload)


_PAGE_SOURCE_TIME_UNSET = object()


def tick_page_content_identity(
  tick: Tick,
  *,
  normalized_source_time_ms: int | None | object = _PAGE_SOURCE_TIME_UNSET,
) -> str:
  """Return a canonical identity for every field in a paged Tick snapshot.

  ``tick_snapshot_identity`` intentionally ignores storage/runtime fields and
  several unstable XTData values because it is the stable overlap identity
  shared by historical and realtime codecs.  Pagination needs a stricter
  identity: a backend that repeats a page must be detected even when only
  ``tickvol``, ``pvolume`` or another otherwise-unstable field differs.

  The source timestamp is represented by ``tick_source_time_ms`` so a legacy
  missing/zero value has the same page identity as its timestamp-derived form.
  Callers that already attempted source-time parsing may pass ``None`` as an
  override to defer an invalid value to the final normalization step.
  """

  if normalized_source_time_ms is _PAGE_SOURCE_TIME_UNSET:
    normalized_source_time_ms = tick_source_time_ms(tick)

  fields = _tick_fields(tick)
  payload = {
    name: _canonical_value(value)
    for name, value in sorted(fields.items(), key=lambda item: str(item[0]))
  }
  payload["source_time_ms"] = _canonical_value(normalized_source_time_ms)
  return _canonical_json(payload)


def _numeric_order_key(value: Any) -> tuple[int, Decimal | str]:
  try:
    number = Decimal(str(value))
  except (InvalidOperation, TypeError, ValueError):
    return (1, _canonical_json(_canonical_value(value)))
  if not number.is_finite():
    return (1, str(number))
  return (0, number)


def _snapshot_order_key(tick: Tick) -> tuple[Any, ...]:
  source_value = getattr(tick, "source_time_ms", None)
  ordinal_value = getattr(tick, "tick_ordinal", None)
  authoritative_ordinal: tuple[int, int]
  try:
    source_time_ms = int(source_value)
    ordinal = int(ordinal_value)
  except (TypeError, ValueError, OverflowError):
    authoritative_ordinal = (1, 0)
  else:
    if (
      not isinstance(source_value, bool)
      and not isinstance(ordinal_value, bool)
      and source_time_ms > 0
      and source_value == source_time_ms
      and ordinal_value == ordinal
      and 0 <= ordinal < HISTORICAL_TICK_ORDINALS_PER_MILLISECOND
    ):
      authoritative_ordinal = (0, ordinal)
    else:
      authoritative_ordinal = (1, 0)
  return (
    _numeric_order_key(getattr(tick, "transaction_num", None)),
    _numeric_order_key(getattr(tick, "volume", None)),
    _numeric_order_key(getattr(tick, "amount", None)),
    tick_snapshot_identity(tick),
    authoritative_ordinal,
    _full_snapshot_payload(tick),
  )


def _clone_tick(tick: Tick) -> Tick:
  return Tick(**dict(vars(tick)))


def tick_storage_time(source_time_ms: int, ordinal: int) -> datetime:
  """Return the canonical persisted Shanghai storage time for a Tick identity.

  The timestamp is reversible: the source millisecond is encoded in the base
  instant and the ordinal is encoded as microseconds.  Keep the range checks
  here so ingestion, historical cursors, and validation cannot drift apart.
  """

  if isinstance(source_time_ms, bool) or isinstance(ordinal, bool):
    raise ValueError("Tick storage identity must contain integer values")
  try:
    normalized_source_time_ms = int(source_time_ms)
    normalized_ordinal = int(ordinal)
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("Tick storage identity must contain integer values") from exc
  if (
    source_time_ms != normalized_source_time_ms
    or ordinal != normalized_ordinal
    or normalized_source_time_ms < 0
    or normalized_ordinal < 0
    or normalized_ordinal >= HISTORICAL_TICK_ORDINALS_PER_MILLISECOND
  ):
    raise ValueError("Tick storage identity is out of range")
  try:
    source_utc = _EPOCH_UTC + timedelta(
      milliseconds=normalized_source_time_ms,
      microseconds=normalized_ordinal,
    )
  except OverflowError as exc:
    raise ValueError("Tick storage identity cannot produce a datetime") from exc
  return time_utils.to_shanghai(source_utc)


def normalize_ticks_losslessly(ticks: Sequence[Tick]) -> list[Tick]:
  """Clone and deterministically encode every occurrence in one Tick sequence."""

  return merge_ticks_losslessly(ticks)


def merge_ticks_losslessly(*sequences: Sequence[Tick]) -> list[Tick]:
  """Merge Tick sequences without dropping distinct same-millisecond snapshots.

  Exact stable-snapshot duplicates keep the object from the earliest sequence.
  Returned objects are clones whose source millisecond, ordinal, and reversible
  microsecond storage timestamp have been normalized.
  """

  retained: list[tuple[tuple[str, str, int], str, Tick]] = []
  retained_counts: dict[tuple[str, str, int, str], int] = {}
  for sequence in sequences:
    sequence_counts: dict[tuple[str, str, int, str], int] = {}
    for tick in sequence:
      source_time_ms = tick_source_time_ms(tick)
      identity = tick_snapshot_identity(tick)
      group_key = (
        str(getattr(tick, "stock_code", "")),
        str(getattr(tick, "period", "tick")),
        source_time_ms,
      )
      identity_key = (*group_key, identity)
      occurrence = sequence_counts.get(identity_key, 0)
      sequence_counts[identity_key] = occurrence + 1
      # Stable identity is a cross-source overlap detector, not a set key.
      # A persisted source may legitimately contain multiple explicit ordinals
      # whose only differences are XTData's excluded, unstable fields.
      if occurrence >= retained_counts.get(identity_key, 0):
        retained.append((group_key, identity, tick))

    for identity_key, count in sequence_counts.items():
      retained_counts[identity_key] = max(
        retained_counts.get(identity_key, 0), count
      )

  groups: dict[tuple[str, str, int], list[Tick]] = {}
  for group_key, _identity, tick in retained:
    groups.setdefault(group_key, []).append(tick)

  normalized: list[Tick] = []
  for (stock_code, period, source_time_ms), group in sorted(
    groups.items(), key=lambda item: (item[0][2], item[0][0], item[0][1])
  ):
    ordered = sorted(group, key=_snapshot_order_key)
    if len(ordered) > HISTORICAL_TICK_ORDINALS_PER_MILLISECOND:
      raise ValueError(
        "too many Tick occurrences in one source millisecond: "
        f"{stock_code}/{period}/{source_time_ms} has {len(ordered)}, "
        f"maximum is {HISTORICAL_TICK_ORDINALS_PER_MILLISECOND}"
      )
    for ordinal, tick in enumerate(ordered):
      clone = _clone_tick(tick)
      clone.source_time_ms = source_time_ms
      clone.tick_ordinal = ordinal
      clone.time = tick_storage_time(source_time_ms, ordinal)
      normalized.append(clone)

  return normalized


def tick_query_end_time(end_time: datetime | None) -> datetime | None:
  """Include every synthetic ordinal belonging to ``end_time``'s source ms."""

  if end_time is None:
    return None
  if hasattr(end_time, "to_pydatetime"):
    end_time = end_time.to_pydatetime()
  if not isinstance(end_time, datetime):
    raise TypeError("tick query end_time must be a datetime or None")
  source_microsecond = (end_time.microsecond // 1_000) * 1_000
  return end_time.replace(microsecond=source_microsecond + 999)


__all__ = [
  "merge_ticks_losslessly",
  "normalize_ticks_losslessly",
  "tick_query_end_time",
  "tick_storage_time",
  "tick_page_content_identity",
  "tick_snapshot_identity",
  "tick_source_time_ms",
]
