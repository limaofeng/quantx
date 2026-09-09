"""Bounded readback of mapped statement fields, including retained null values."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Numeric, select, tuple_

from .market_data_ingestion_progress import evidence_hash


def _content_hash(rows):
  def canonical(value):
    if isinstance(value, Decimal):
      return "0" if value.is_zero() else str(value.normalize())
    return str(value) if isinstance(value, date) else value

  return evidence_hash(
    [{name: canonical(value) for name, value in row.items()} for row in rows]
  )


async def read_statement_proof(db, model, rows, *, chunk_size):
  """Recompute the committed proof without rewriting or accepting a newer value."""
  digests = []
  count = 0
  for offset in range(0, len(rows), chunk_size):
    chunk = rows[offset : offset + chunk_size]
    fields = sorted(set().union(*(row.keys() for row in chunk)))
    keys = [(row["stock_code"], row["report_date"]) for row in chunk]
    query = (
      select(*(model.__table__.c[name] for name in fields))
      .where(tuple_(model.stock_code, model.report_date).in_(keys))
      .order_by(model.stock_code, model.report_date)
      .limit(len(chunk) + 1)
    )
    actual = [dict(row) for row in (await db.execute(query)).mappings()]
    digests.append(_content_hash(actual))
    count += len(actual)
  return {
    "schema_version": 1,
    "rows_verified": count,
    "mapped_content_sha256": evidence_hash(digests),
  }


async def verified_statement_upsert(db, model, rows, *, upsert, chunk_size):
  keys = [(row["stock_code"], row["report_date"]) for row in rows]
  if len(keys) != len(set(keys)):
    raise ValueError("duplicate financial statement source key")
  digests = []
  count = 0
  for offset in range(0, len(rows), chunk_size):
    chunk = [dict(row) for row in rows[offset : offset + chunk_size]]
    fields = sorted(set().union(*(row.keys() for row in chunk)))
    for row in chunk:
      if set(row) != set(fields):
        raise ValueError("financial statement field scope mismatch")
      for name, value in row.items():
        column_type = model.__table__.c[name].type
        if value is not None and isinstance(column_type, Numeric):
          row[name] = Decimal(str(value)).quantize(
            Decimal(1).scaleb(-column_type.scale),
            rounding=ROUND_HALF_UP,
          )
    selected_keys = [(row["stock_code"], row["report_date"]) for row in chunk]
    query = (
      select(*(model.__table__.c[name] for name in fields))
      .where(tuple_(model.stock_code, model.report_date).in_(selected_keys))
      .order_by(model.stock_code, model.report_date)
      .limit(len(chunk) + 1)
    )
    prior = {
      (row["stock_code"], row["report_date"]): dict(row)
      for row in (await db.execute(query.with_for_update())).mappings()
    }
    expected = []
    for row in chunk:
      old = prior.get((row["stock_code"], row["report_date"]), {})
      expected.append(
        {name: old.get(name) if value is None else value for name, value in row.items()}
      )
    expected.sort(key=lambda row: (row["stock_code"], row["report_date"]))
    written = await upsert(db, model, chunk)
    actual = [dict(row) for row in (await db.execute(query)).mappings()]
    if written != len(chunk) or actual != expected:
      raise RuntimeError("financial statement persistence content mismatch")

    digests.append(_content_hash(expected))
    count += len(actual)
  return {
    "schema_version": 1,
    "rows_verified": count,
    "mapped_content_sha256": evidence_hash(digests),
  }
