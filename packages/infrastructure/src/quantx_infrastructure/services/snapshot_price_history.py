"""Read-only, proven corporate-action history for daily indicator snapshots."""

from __future__ import annotations

from contextlib import aclosing
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import String, bindparam, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from quantx_infrastructure.models.agent_runtime import MarketDataRequest
from quantx_infrastructure.models.divid_factor import DividFactorTable
from quantx_infrastructure.repositories.divid_factor_repository import (
  DIVID_FACTOR_WRITE_LOCK_KEY,
)
from quantx_infrastructure.services.divid_factor_evidence import (
  DividFactorEvidence,
  current_rows_match_evidence,
  parse_divid_factor_evidence,
)

MAX_FACTOR_EVIDENCE_REQUESTS = 4_096


def covered_codes(
  evidence: list[DividFactorEvidence],
  code_bounds: dict[str, tuple[date, date]],
) -> set[str]:
  """Merge inclusive authoritative completed-request windows per instrument."""
  intervals: dict[str, list[tuple[date, date]]] = {code: [] for code in code_bounds}
  for item in evidence:
    if item.stock_code in intervals:
      intervals[item.stock_code].append((item.start_date, item.end_date))
  result = set()
  for code, windows in intervals.items():
    start, end = code_bounds[code]
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
  code_bounds = {
    code: (
      pd.to_datetime(frame.time, utc=True).min().tz_convert("Asia/Shanghai").date(),
      pd.to_datetime(frame.time, utc=True).max().tz_convert("Asia/Shanghai").date(),
    )
    for code, frame in frames.items()
  }
  start = min(bounds[0] for bounds in code_bounds.values())
  end = max(bounds[1] for bounds in code_bounds.values())
  evidence_codes_parameter = bindparam(
    "factor_evidence_codes",
    value=sorted(code_bounds),
    type_=ARRAY(String()),
  )
  async with aclosing(db_factory()) as sessions:
    db = await anext(sessions, None)
    if db is None:
      raise RuntimeError("无法打开日级指标数据会话")
    # Keep request evidence and the exact factor rows in one stable read
    # interval. Every factor-table writer takes the matching exclusive xact
    # lock, so replacement cannot slip between the two SELECTs.
    await db.execute(
      select(func.pg_advisory_xact_lock_shared(DIVID_FACTOR_WRITE_LOCK_KEY))
    )
    requests = (
      await db.execute(
        select(
          MarketDataRequest.request_id,
          MarketDataRequest.request_payload,
          MarketDataRequest.status,
          MarketDataRequest.expected_chunks,
          MarketDataRequest.received_chunks,
          MarketDataRequest.completed_at,
          MarketDataRequest.ingestion_result,
        )
        .where(
          MarketDataRequest.status == "COMPLETED",
          MarketDataRequest.request_payload["operation"].as_string() == "divid_factors",
          MarketDataRequest.request_payload["source"].as_string()
          == "qmt-get-divid-factors-v1",
          MarketDataRequest.request_payload["end_time"].as_string()
          >= start.strftime("%Y%m%d"),
          MarketDataRequest.request_payload["start_time"].as_string()
          <= end.strftime("%Y%m%d"),
          cast(
            MarketDataRequest.request_payload["stock_list"],
            JSONB,
          ).op("?|")(evidence_codes_parameter),
        )
        .order_by(
          MarketDataRequest.completed_at.desc(),
          MarketDataRequest.request_id.desc(),
        )
        .limit(MAX_FACTOR_EVIDENCE_REQUESTS + 1)
      )
    ).all()
    if len(requests) > MAX_FACTOR_EVIDENCE_REQUESTS:
      raise RuntimeError("复权因子覆盖请求超过安全扫描预算")
    candidates = []
    for row in requests:
      items = parse_divid_factor_evidence(
        request_id=row[0],
        request_payload=row[1],
        status=row[2],
        expected_chunks=row[3],
        received_chunks=row[4],
        completed_at=row[5],
        ingestion_result=row[6],
      )
      if items is not None:
        candidates.extend(
          item
          for item in items
          if item.stock_code in code_bounds
          and item.end_date >= code_bounds[item.stock_code][0]
          and item.start_date <= code_bounds[item.stock_code][1]
        )
    if not candidates:
      raise ValueError(
        f"日级指标计算有{len(frames)}只标的缺少 schema-v2 复权因子窗口证明；请先补齐历史数据"
      )
    evidence_codes = sorted({item.stock_code for item in candidates})
    evidence_start = min(item.start_date for item in candidates)
    evidence_end = max(item.end_date for item in candidates)
    rows = (
      await db.execute(
        select(
          DividFactorTable.stock_code,
          DividFactorTable.time,
          DividFactorTable.ex_date,
          DividFactorTable.interest,
          DividFactorTable.stock_bonus,
          DividFactorTable.stock_gift,
          DividFactorTable.allot_num,
          DividFactorTable.allot_price,
          DividFactorTable.gugai,
          DividFactorTable.dr,
        )
        .where(
          DividFactorTable.stock_code.in_(evidence_codes),
          DividFactorTable.ex_date >= evidence_start.strftime("%Y%m%d"),
          DividFactorTable.ex_date <= evidence_end.strftime("%Y%m%d"),
        )
        .order_by(DividFactorTable.time)
      )
    ).all()
    verified = [item for item in candidates if current_rows_match_evidence(item, rows)]
    covered = covered_codes(verified, code_bounds)
    if covered != set(frames):
      raise ValueError(
        f"日级指标计算有{len(set(frames) - covered)}只标的缺少与当前数据库一致的复权因子窗口证明；请先补齐历史数据"
      )
    by_code: dict[str, list] = {code: [] for code in covered}
    for row in rows:
      code, when, ex_date, *_, ratio = row
      factor_date = datetime.strptime(str(ex_date), "%Y%m%d").date()
      if (
        code in covered and code_bounds[code][0] <= factor_date <= code_bounds[code][1]
      ):
        by_code[code].append((when, ratio))
    return {code: adjust_price_frame(frames[code], by_code[code]) for code in covered}
