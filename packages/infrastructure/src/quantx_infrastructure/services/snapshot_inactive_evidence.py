"""Strict, batched empty-market-data evidence for daily snapshots."""

from __future__ import annotations

from contextlib import aclosing
from datetime import date, datetime
from typing import Iterable

from sqlalchemy import Date, String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY


async def load_snapshot_inactive_empty_proofs(
  candidates: Iterable[tuple[str, date]],
  db_factory,
) -> set[tuple[str, date]]:
  """Return candidates with strict exact-day ``1m`` and ``1d`` empty proofs.

  One set-based query serves the whole snapshot batch. An accepted period must
  have a completed, persistence-verified, exact-single-day request whose day
  coverage and code summary both say canonical zero rows and
  ``XT_DATA_NO_ROWS``. Any completed, verified nonzero/malformed coverage for
  the same code/date/period rejects the proof.
  """

  normalized = sorted(
    {
      (str(code or "").strip().upper(), target)
      for code, target in candidates
      if str(code or "").strip() and isinstance(target, date)
    },
    key=lambda item: (item[1], item[0]),
  )
  if not normalized:
    return set()

  statement = text(
    """
    WITH requested(instrument_code, trading_date) AS (
      SELECT *
      FROM unnest(:candidate_codes, :candidate_dates)
    ),
    period_evidence AS (
      SELECT DISTINCT
        requested.instrument_code,
        requested.trading_date,
        LOWER(coverage.value ->> 'period') AS period
      FROM requested
      JOIN market_data_request AS evidence_request
        ON evidence_request.status = 'COMPLETED'
       AND evidence_request.ingestion_result
             -> 'persistence_verification' ->> 'status' = 'verified'
       AND evidence_request.request_payload ->> 'start_time' =
             TO_CHAR(requested.trading_date, 'YYYYMMDD')
       AND evidence_request.request_payload ->> 'end_time' =
             TO_CHAR(requested.trading_date, 'YYYYMMDD')
      CROSS JOIN LATERAL json_array_elements(
        COALESCE(
          evidence_request.ingestion_result -> 'day_coverage',
          '[]'::json
        )
      ) AS coverage(value)
      WHERE coverage.value ->> 'instrument_code' = requested.instrument_code
        AND LOWER(COALESCE(coverage.value ->> 'period', '')) IN ('1m', '1d')
        AND coverage.value ->> 'trading_date' =
              TO_CHAR(requested.trading_date, 'YYYY-MM-DD')
        AND coverage.value ->> 'point_count' = '0'
        AND EXISTS (
          SELECT 1
          FROM json_array_elements(
            COALESCE(
              evidence_request.ingestion_result -> 'code_summaries',
              '[]'::json
            )
          ) AS summary(value)
          WHERE summary.value ->> 'code' = requested.instrument_code
            AND LOWER(COALESCE(summary.value ->> 'period', '')) =
                  LOWER(coverage.value ->> 'period')
            AND summary.value ->> 'row_count' = '0'
            AND summary.value ->> 'no_data_reason' = 'XT_DATA_NO_ROWS'
        )
        AND NOT EXISTS (
          SELECT 1
          FROM market_data_request AS contradictory_request
          WHERE contradictory_request.status = 'COMPLETED'
            AND contradictory_request.ingestion_result
                  -> 'persistence_verification' ->> 'status' = 'verified'
            AND (
              EXISTS (
                SELECT 1
                FROM json_array_elements(
                  COALESCE(
                    contradictory_request.ingestion_result -> 'day_coverage',
                    '[]'::json
                  )
                ) AS contradictory_coverage(value)
                WHERE contradictory_coverage.value ->> 'instrument_code' =
                      requested.instrument_code
                  AND LOWER(
                    COALESCE(contradictory_coverage.value ->> 'period', '')
                  ) = LOWER(coverage.value ->> 'period')
                  AND contradictory_coverage.value ->> 'trading_date' =
                        TO_CHAR(requested.trading_date, 'YYYY-MM-DD')
                  AND COALESCE(
                    contradictory_coverage.value ->> 'point_count',
                    ''
                  ) <> '0'
              )
              OR (
                contradictory_request.request_payload ->> 'start_time' =
                  TO_CHAR(requested.trading_date, 'YYYYMMDD')
                AND contradictory_request.request_payload ->> 'end_time' =
                  TO_CHAR(requested.trading_date, 'YYYYMMDD')
                AND EXISTS (
                  SELECT 1
                  FROM json_array_elements(
                    COALESCE(
                      contradictory_request.ingestion_result -> 'code_summaries',
                      '[]'::json
                    )
                  ) AS contradictory_summary(value)
                  WHERE contradictory_summary.value ->> 'code' =
                        requested.instrument_code
                    AND LOWER(
                      COALESCE(contradictory_summary.value ->> 'period', '')
                    ) = LOWER(coverage.value ->> 'period')
                    AND COALESCE(
                      contradictory_summary.value ->> 'row_count',
                      ''
                    ) <> '0'
                )
              )
            )
        )
    )
    SELECT instrument_code, trading_date
    FROM period_evidence
    GROUP BY instrument_code, trading_date
    HAVING COUNT(DISTINCT period) = 2
    """
  ).bindparams(
    bindparam("candidate_codes", type_=ARRAY(String())),
    bindparam("candidate_dates", type_=ARRAY(Date())),
  )
  parameters = {
    "candidate_codes": [code for code, _ in normalized],
    "candidate_dates": [target for _, target in normalized],
  }

  async with aclosing(db_factory()) as sessions:
    db = await anext(sessions, None)
    if db is None:
      raise RuntimeError("日级快照停牌证据查询未取得数据库连接")
    rows = (await db.execute(statement, parameters)).mappings().all()

  requested = set(normalized)
  proven: set[tuple[str, date]] = set()
  for row in rows:
    code = str(row.get("instrument_code") or "").strip().upper()
    raw_target = row.get("trading_date")
    if isinstance(raw_target, datetime):
      target = raw_target.date()
    elif isinstance(raw_target, date):
      target = raw_target
    elif isinstance(raw_target, str):
      try:
        target = date.fromisoformat(raw_target)
      except ValueError:
        continue
    else:
      continue
    candidate = (code, target)
    if candidate in requested:
      proven.add(candidate)
  return proven
