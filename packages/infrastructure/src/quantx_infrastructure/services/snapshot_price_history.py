"""Read-only, proven corporate-action history for daily factor snapshots."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import MarketDataRequest
from quantx_infrastructure.models.divid_factor import DividFactorTable


def covered_codes(requests: list, codes: list[str], start: date, end: date) -> set[str]:
  """Merge inclusive authoritative completed-request windows per instrument."""
  intervals: dict[str, list[tuple[date, date]]] = {code: [] for code in codes}
  for payload, expected, received, completed in requests:
    if isinstance(payload, str):
      try:
        payload = json.loads(payload)
      except ValueError:
        continue
    if (
      not isinstance(payload, dict)
      or not completed
      or not expected
      or expected != received
    ):
      continue
    if (
      payload.get("operation") != "divid_factors"
      or payload.get("source") != "qmt-get-divid-factors-v1"
    ):
      continue
    try:
      first = datetime.strptime(
        str(payload["start_time"]).replace("-", ""), "%Y%m%d"
      ).date()
      last = datetime.strptime(
        str(payload["end_time"]).replace("-", ""), "%Y%m%d"
      ).date()
    except (KeyError, ValueError):
      continue
    if last < first:
      continue
    for code in set(payload.get("stock_list") or []).intersection(intervals):
      intervals[code].append((first, last))
  result = set()
  for code, windows in intervals.items():
    cursor = start
    for first, last in sorted(windows):
      if first > cursor:
        break
      cursor = max(cursor, last + timedelta(days=1))
      if cursor > end:
        result.add(code)
        break
  return result


def adjust_price_frame(frame: pd.DataFrame, factors: list[tuple]) -> pd.DataFrame:
  """Backward cumulative dr only through each row; preserve raw price columns."""
  result = frame.copy()
  times = (
    pd.to_datetime(result["time"], utc=True).dt.tz_convert("Asia/Shanghai").dt.date
  )
  events: dict[date, float] = {}
  for when, ratio in factors:
    value = float(ratio)
    if not np.isfinite(value) or value <= 0:
      raise ValueError("复权因子包含非法dr")
    effective = pd.Timestamp(when).date()
    events[effective] = events.get(effective, 1.0) * value
  ordered = sorted(events.items())
  cursor = 0
  product = 1.0
  multipliers = []
  for when in times:
    while cursor < len(ordered) and ordered[cursor][0] <= when:
      product *= ordered[cursor][1]
      cursor += 1
    multipliers.append(product)
  for column in ("open", "high", "low", "close"):
    result[f"raw_{column}"] = result[column]
    result[column] = pd.to_numeric(result[column], errors="coerce") * np.asarray(
      multipliers
    )
  return result


async def load_snapshot_price_history(
  frames: dict[str, pd.DataFrame], db_factory
) -> dict[str, pd.DataFrame]:
  if not frames:
    return {}
  frames = {
    code: frame.sort_values("time").drop_duplicates("time", keep="last")
    for code, frame in frames.items()
    if not frame.empty
  }
  if not frames:
    return {}
  start = min(
    pd.to_datetime(frame.time, utc=True).min().tz_convert("Asia/Shanghai").date()
    for frame in frames.values()
  )
  end = max(
    pd.to_datetime(frame.time, utc=True).max().tz_convert("Asia/Shanghai").date()
    for frame in frames.values()
  )
  async for db in db_factory():
    requests = (
      await db.execute(
        select(
          MarketDataRequest.request_payload,
          MarketDataRequest.expected_chunks,
          MarketDataRequest.received_chunks,
          MarketDataRequest.completed_at,
        ).where(
          MarketDataRequest.status == "COMPLETED",
          MarketDataRequest.request_payload["operation"].as_string() == "divid_factors",
        )
      )
    ).all()
    covered = covered_codes(requests, list(frames), start, end)
    if covered != set(frames):
      raise ValueError(
        f"日级因子计算有{len(set(frames) - covered)}只标的缺少已完成的复权因子窗口证明；请先补齐历史数据"
      )
    rows = (
      await db.execute(
        select(
          DividFactorTable.stock_code,
          DividFactorTable.time,
          DividFactorTable.dr,
        )
        .where(
          DividFactorTable.stock_code.in_(covered),
          DividFactorTable.time >= datetime.combine(start, datetime.min.time()),
          DividFactorTable.time
          < datetime.combine(end + timedelta(days=1), datetime.min.time()),
        )
        .order_by(DividFactorTable.time)
      )
    ).all()
    by_code: dict[str, list] = {code: [] for code in covered}
    for code, when, ratio in rows:
      by_code[code].append((when, ratio))
    return {code: adjust_price_frame(frames[code], by_code[code]) for code in covered}
  raise RuntimeError("无法打开日级因子数据会话")
