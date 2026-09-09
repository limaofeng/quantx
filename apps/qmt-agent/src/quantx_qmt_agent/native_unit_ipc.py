"""Pull-based single-unit IPC between the Agent executor and XTData child.

The parent owns authorization, journal and artifact publication. The child checks
that native parameters match the complete request plan before touching XTData.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any

from quantx_contracts.collection_permit import (
  CollectionPermit,
  CollectionUnit,
  plan_historical_work_units,
)

from .broker import MAX_MARKET_DATA_RECORDS, validate_market_data_request

MAX_UNIT_BATCH_BYTES = 1024 * 1024
MAX_UNIT_BATCH_RECORDS = 128


class NativeUnitIPCStuck(RuntimeError):
  """The native transport cannot be proved stopped; do not restart a child."""


def _call_before_deadline(function, deadline, abort):
  if time.monotonic() >= deadline:
    raise TimeoutError("native unit IPC deadline exceeded")
  done, errors, results = threading.Event(), [], []

  def send():
    try:
      results.append(function())
    except BaseException as exc:
      errors.append(exc)
    finally:
      done.set()

  sender = threading.Thread(target=send, name="history-unit-io", daemon=True)
  sender.start()
  if not done.wait(max(0, deadline - time.monotonic())):
    abort()
    sender.join(5)
    if sender.is_alive():
      raise NativeUnitIPCStuck("native unit transport did not stop")
    raise TimeoutError("native unit IPC deadline exceeded")
  sender.join()
  if errors:
    raise errors[0]
  return results[0]


def _encoded(record: dict[str, Any]) -> bytes:
  if not isinstance(record, dict):
    raise ValueError("native unit record is not an object")
  return json.dumps(
    record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
  ).encode()


def serve_native_unit(connection, broker, message) -> None:
  permit = CollectionPermit.model_validate(message["permit"])
  request_id = str(permit.unit.request_id)
  if message["request_id"] != request_id:
    raise ValueError("native unit IPC request identity mismatch")
  payload = message["payload"]
  validate_market_data_request(payload)
  units = plan_historical_work_units(payload)
  index = permit.unit.unit_index
  if (
    index >= len(units)
    or CollectionUnit.from_payload(request_id, index, units[index]) != permit.unit
  ):
    raise ValueError("native unit IPC plan mismatch")
  identity = {"request_id": request_id, "permit_id": str(permit.permit_id)}
  batch, size, count, sequence = [], 0, 0, 0

  def flush():
    nonlocal batch, size, sequence
    connection.send(
      {"type": "unit_records", **identity, "sequence": sequence, "records": batch}
    )
    reply = connection.recv()
    if reply != {"type": "unit_continue", **identity, "sequence": sequence}:
      raise ValueError("native unit IPC continuation mismatch")
    sequence += 1
    batch, size = [], 0

  if not permit.issued_at <= datetime.now(timezone.utc) < permit.expires_at:
    raise ValueError("native unit authorization expired before child entry")
  records = iter(broker.iter_market_data(units[index]))
  try:
    for record in records:
      raw = _encoded(record)
      if len(raw) > MAX_UNIT_BATCH_BYTES:
        raise ValueError("native unit record exceeds IPC byte limit")
      if batch and (
        size + len(raw) > MAX_UNIT_BATCH_BYTES or len(batch) >= MAX_UNIT_BATCH_RECORDS
      ):
        flush()
      count += 1
      if count > MAX_MARKET_DATA_RECORDS:
        raise ValueError("native unit exceeds record limit")
      batch.append(json.loads(raw))
      size += len(raw)
    if batch:
      flush()
    connection.send(
      {"type": "unit_complete", **identity, "sequence": sequence, "record_count": count}
    )
  finally:
    close = getattr(records, "close", None)
    if callable(close):
      close()


def iter_native_unit(
  connection,
  permit: CollectionPermit,
  payload: dict[str, Any],
  *,
  timeout: float,
  abort: Callable[[], None],
) -> Iterator[dict[str, Any]]:
  """Called under the executor's native lock; caller terminates on incomplete exit."""
  if timeout <= 0:
    raise ValueError("native unit timeout must be positive")
  identity = {
    "request_id": str(permit.unit.request_id),
    "permit_id": str(permit.permit_id),
  }
  deadline = time.monotonic() + timeout
  _call_before_deadline(
    lambda: connection.send(
      {
        "type": "collect_unit",
        "request_id": identity["request_id"],
        "permit": permit.model_dump(mode="json"),
        "payload": payload,
      }
    ),
    deadline,
    abort,
  )
  sequence, count = 0, 0
  while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not connection.poll(remaining):
      raise TimeoutError("native unit IPC deadline exceeded")
    # poll alone only proves some bytes are available, not that a complete
    # frame arrived. Bound recv as well as both directions of send.
    message = _call_before_deadline(connection.recv, deadline, abort)
    if not isinstance(message, dict) or any(
      message.get(key) != value for key, value in identity.items()
    ):
      raise ValueError("native unit IPC response identity mismatch")
    if message.get("sequence") != sequence:
      raise ValueError("native unit IPC sequence mismatch")
    if message.get("type") == "unit_complete":
      if (
        type(message.get("record_count")) is not int or message["record_count"] != count
      ):
        raise ValueError("native unit IPC completion count mismatch")
      return
    records = message.get("records")
    if (
      message.get("type") != "unit_records"
      or not isinstance(records, list)
      or not 1 <= len(records) <= MAX_UNIT_BATCH_RECORDS
    ):
      raise ValueError("native unit IPC invalid batch")
    if sum(len(_encoded(record)) for record in records) > MAX_UNIT_BATCH_BYTES:
      raise ValueError("native unit IPC batch exceeds byte limit")
    count += len(records)
    if count > MAX_MARKET_DATA_RECORDS:
      raise ValueError("native unit exceeds record limit")
    yield from records
    if time.monotonic() >= deadline:
      raise TimeoutError("native unit IPC deadline exceeded")
    _call_before_deadline(
      lambda: connection.send(
        {"type": "unit_continue", **identity, "sequence": sequence}
      ),
      deadline,
      abort,
    )
    sequence += 1
