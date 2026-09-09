"""Bounded, lossless source snapshots for instrument detail requests."""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

MAX_INSTRUMENT_DETAIL_CODES = 10000
MAX_INSTRUMENT_DETAIL_BYTES = 65536
MAX_INSTRUMENT_DETAIL_RESULT_BYTES = 32 * 1024 * 1024
INSTRUMENT_CODE_PATTERN = r"^[A-Z0-9]{1,16}\.(SH|SZ|BJ)$"


def instrument_detail_codes(payload: dict[str, Any]) -> list[str]:
  codes = payload.get("stock_list")
  if (
    payload.get("operation") != "instrument_details"
    or not isinstance(codes, list)
    or not 1 <= len(codes) <= MAX_INSTRUMENT_DETAIL_CODES
    or any(
      not isinstance(code, str) or not re.fullmatch(INSTRUMENT_CODE_PATTERN, code)
      for code in codes
    )
    or len(set(codes)) != len(codes)
  ):
    raise ValueError("invalid instrument_details request scope")
  return sorted(codes)


def instrument_detail_json(record: dict[str, Any]) -> str:
  if not isinstance(record, dict) or not record.keys() - {"code"}:
    raise ValueError("instrument_details record has no source fields")
  try:
    value = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
  except (TypeError, ValueError, RecursionError) as exc:
    raise ValueError("instrument_details record is not canonical JSON") from exc
  if len(value.encode()) > MAX_INSTRUMENT_DETAIL_BYTES:
    raise ValueError("instrument_details record exceeds byte limit")
  return value


class InstrumentDetailSnapshot(BaseModel):
  model_config = ConfigDict(extra="forbid")
  request_id: str = Field(min_length=1, max_length=36)
  code: str = Field(pattern=INSTRUMENT_CODE_PATTERN)
  schema_version: Literal[1] = 1
  manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  record: dict[str, JsonValue]
