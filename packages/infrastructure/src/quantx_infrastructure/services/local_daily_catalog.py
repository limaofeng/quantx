"""Resolve the latest published benchmark date without a wall-clock fallback."""

from datetime import datetime

from sqlalchemy import text

from .development_bar_publication import _VISIBLE_VERSION_QUERY


async def latest_daily_date(db, instrument, *, development):
  if development:
    return await db.scalar(
      text(
        "SELECT max(trading_date) FROM ("
        + _VISIBLE_VERSION_QUERY
        + " AND v.stock_code=:code AND v.period='1d') available"
      ),
      {"code": instrument},
    )
  day = await db.scalar(
    text("""
    SELECT max(replace(c->>'trading_date','-','')) FROM market_data_request r
    CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(r.ingestion_result::jsonb->'day_coverage')='array'
      THEN r.ingestion_result::jsonb->'day_coverage' ELSE '[]'::jsonb END) c
    WHERE r.status='COMPLETED' AND r.request_payload->>'operation'='bars'
      AND r.ingestion_result->>'native_storage_version' IS NOT NULL
      AND c->>'instrument_code'=:code AND c->>'period'='1d'
      AND c->>'point_count' ~ '^[1-9][0-9]*$'
      AND (r.request_payload->'stock_list')::jsonb @> jsonb_build_array(CAST(:code AS text))
      AND (r.request_payload->'periods')::jsonb @> '["1d"]'::jsonb
      AND r.request_payload->>'start_time' ~ '^[0-9]{8}$'
      AND r.request_payload->>'end_time' ~ '^[0-9]{8}$'
      AND replace(c->>'trading_date','-','') BETWEEN r.request_payload->>'start_time' AND r.request_payload->>'end_time'
  """),
    {"code": instrument},
  )
  return datetime.strptime(day, "%Y%m%d").date() if day else None
