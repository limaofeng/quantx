"""Opaque cursor helpers shared by stable GraphQL connections."""

import base64
import json
from datetime import date, datetime
from typing import Tuple


def encode_cursor(value: date | datetime, row_id: str) -> str:
  payload = json.dumps(
    [value.isoformat(), str(row_id)],
    separators=(",", ":"),
  ).encode("utf-8")
  return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_date_cursor(cursor: str) -> Tuple[date, str]:
  value, row_id = _decode(cursor)
  return date.fromisoformat(value), row_id


def decode_datetime_cursor(cursor: str) -> Tuple[datetime, str]:
  value, row_id = _decode(cursor)
  return datetime.fromisoformat(value.replace("Z", "+00:00")), row_id


def _decode(cursor: str) -> Tuple[str, str]:
  if not cursor:
    raise ValueError("分页游标不能为空")
  try:
    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
  except Exception as exc:
    raise ValueError("分页游标无效") from exc
  if not isinstance(payload, list) or len(payload) != 2:
    raise ValueError("分页游标无效")
  return str(payload[0]), str(payload[1])
