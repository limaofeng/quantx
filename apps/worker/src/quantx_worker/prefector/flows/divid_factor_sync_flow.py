"""Data-only QMT corporate-action factor synchronization."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from prefect import flow

from quantx_worker.prefector.flows.durable_agent_flows import (
  _persisted_instrument_codes,
  _request_and_wait,
)


def _compact_date(value: str) -> str:
  compact = str(value or "").strip().replace("-", "")
  if len(compact) != 8 or not compact.isdigit():
    raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
  datetime.strptime(compact, "%Y%m%d")
  return compact


@flow(name="QMT Agent 复权因子同步", retries=1, retry_delay_seconds=30)
async def divid_factor_sync_flow(
  stock_list: Optional[list[str]] = None,
  start_time: str = "",
  end_time: str = "",
  agent_device_id: str = "",
  timeout_seconds: int = 900,
  request_key: str = "",
) -> dict[str, object]:
  """Request sparse ``get_divid_factors`` data through the outbound Agent."""
  codes = sorted(
    {
      str(code).strip().upper()
      for code in (stock_list or [])
      if str(code).strip()
    }
  )
  if not codes:
    codes = await _persisted_instrument_codes()
  if not codes:
    return {"status": "skipped", "reason": "persisted universe is empty"}
  if len(codes) > 500:
    raise ValueError("one divid-factor request accepts at most 500 instruments")
  start = _compact_date(start_time)
  end = _compact_date(end_time)
  if end < start:
    raise ValueError("end_time precedes start_time")
  if timeout_seconds <= 0:
    raise ValueError("timeout_seconds must be positive")

  payload: dict[str, object] = {
    "operation": "divid_factors",
    "source": "qmt-get-divid-factors-v1",
    "stock_list": codes,
    "start_time": start,
    "end_time": end,
  }
  if request_key:
    payload["request_key"] = str(request_key)
  return await _request_and_wait(
    payload,
    timeout_seconds=int(timeout_seconds),
    agent_device_id=str(agent_device_id or ""),
  )
