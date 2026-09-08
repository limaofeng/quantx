"""Explicit market-only reference fields; no account or device models."""

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, text

from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.models.divid_factor import DividFactor, DividFactorTable
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.models.holidays import Holiday
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.repositories.divid_factor_repository import (
  DividFactorRepository,
)
from quantx_infrastructure.services.divid_factor_evidence import (
  DividFactorEvidence,
  current_rows_match_evidence,
  parse_divid_factor_evidence,
)

INSTRUMENT_FIELDS = (
  "id",
  "market",
  "instrument_id",
  "name",
  "type",
  "open_date",
  "expire_date",
  "price_tick",
  "volume_multiple",
  "min_market_order_volume",
  "max_market_order_volume",
  "min_limit_order_volume",
  "max_limit_order_volume",
)
FACTOR_FIELDS = (
  "stock_code",
  "time",
  "ex_date",
  "interest",
  "stock_bonus",
  "stock_gift",
  "allot_num",
  "allot_price",
  "gugai",
  "dr",
)


def encode(value):
  if isinstance(value, (date, datetime)):
    return value.isoformat()
  if isinstance(value, Decimal):
    return str(value)
  return getattr(value, "value", value)


async def export_reference(code: str, day: date) -> dict:
  async with AsyncSessionLocal() as db:
    instrument = await db.get(Instrument, code)
    holidays = (
      (
        await db.execute(
          select(Holiday).where(Holiday.market == "SH", Holiday.year == day.year)
        )
      )
      .scalars()
      .all()
    )
    if instrument is None or not holidays:
      raise ValueError("REFERENCE_DATA_MISSING")
    factors = (
      (
        await db.execute(
          select(DividFactorTable)
          .where(
            DividFactorTable.stock_code == code,
          )
          .order_by(DividFactorTable.time)
          .limit(10001)
        )
      )
      .scalars()
      .all()
    )
    if len(factors) > 10000:
      raise ValueError("REFERENCE_DATA_BUDGET_EXCEEDED")
    proof = (
      (
        await db.execute(
          text("""
      SELECT request_id, request_payload, status, expected_chunks, received_chunks,
             completed_at, ingestion_result
      FROM market_data_request WHERE status='COMPLETED'
        AND request_payload->>'operation'='divid_factors'
        AND (request_payload->'stock_list')::jsonb @> CAST(:codes AS jsonb)
        AND ingestion_result->'replacement_audit' IS NOT NULL
        AND request_payload->>'end_time' >= :day
      ORDER BY completed_at DESC LIMIT 1
    """),
          {"codes": json.dumps([code]), "day": day.strftime("%Y%m%d")},
        )
      )
      .mappings()
      .one_or_none()
    )
    evidence = None
    if proof:
      parsed = parse_divid_factor_evidence(**dict(proof)) or ()
      values = [
        tuple(getattr(item, field) for field in FACTOR_FIELDS) for item in factors
      ]
      evidence = next(
        (
          item
          for item in parsed
          if item.stock_code == code and current_rows_match_evidence(item, values)
        ),
        None,
      )
    return {
      "as_of": day.isoformat(),
      "instrument": {
        key: encode(getattr(instrument, key)) for key in INSTRUMENT_FIELDS
      },
      "holidays": [
        {
          "market": item.market,
          "year": item.year,
          "date": item.date.isoformat(),
          "description": item.description,
        }
        for item in holidays
      ],
      "factors": [
        {key: encode(getattr(item, key)) for key in FACTOR_FIELDS} for item in factors
      ],
      "factor_coverage": {
        "status": "VERIFIED",
        "start_date": evidence.start_date.strftime("%Y%m%d"),
        "end_date": evidence.end_date.strftime("%Y%m%d"),
        "evidence": {key: encode(value) for key, value in asdict(evidence).items()},
        "expected_chunks": proof["expected_chunks"],
        "received_chunks": proof["received_chunks"],
      }
      if evidence
      else {"status": "UNVERIFIED"},
    }


async def import_reference(reference: dict, *, code: str) -> None:
  instrument = dict(reference["instrument"])
  if set(instrument) != set(INSTRUMENT_FIELDS) or instrument["id"] != code:
    raise ValueError("Invalid reference instrument")
  for key in ("open_date", "expire_date"):
    instrument[key] = date.fromisoformat(instrument[key]) if instrument[key] else None
  if instrument["type"] is not None:
    instrument["type"] = InstrumentType(instrument["type"])
  proof = reference.get("factor_coverage", {})
  if proof.get("status") == "VERIFIED":
    raw = proof["evidence"]
    evidence = DividFactorEvidence(
      **{
        **raw,
        "start_date": date.fromisoformat(raw["start_date"]),
        "end_date": date.fromisoformat(raw["end_date"]),
        "completed_at": datetime.fromisoformat(raw["completed_at"]),
      }
    )
    if (
      type(evidence.record_count) is not int
      or evidence.record_count < 0
      or type(proof["expected_chunks"]) is not int
      or proof["expected_chunks"] <= 0
      or proof["received_chunks"] != proof["expected_chunks"]
      or evidence.start_date.strftime("%Y%m%d") != proof["start_date"]
      or evidence.end_date.strftime("%Y%m%d") != proof["end_date"]
    ):
      raise ValueError("Invalid reference coverage metadata")
    values = [
      tuple(
        datetime.fromisoformat(item[field])
        if field == "time"
        else Decimal(item[field])
        if field in FACTOR_FIELDS[3:]
        else item[field]
        for field in FACTOR_FIELDS
      )
      for item in reference["factors"]
    ]
    if evidence.stock_code != code or not current_rows_match_evidence(evidence, values):
      raise ValueError("Imported reference evidence does not match factor rows")
  async with AsyncSessionLocal() as db:
    await db.merge(Instrument(**instrument))
    for holiday in reference["holidays"]:
      if (
        set(holiday) != {"market", "year", "date", "description"}
        or holiday["market"] != "SH"
      ):
        raise ValueError("Invalid calendar record")
      day = date.fromisoformat(holiday["date"])
      found = await db.scalar(
        select(Holiday).where(Holiday.market == "SH", Holiday.date == day)
      )
      if found is None:
        db.add(Holiday(**{**holiday, "date": day}))
    factors = []
    for raw in reference["factors"]:
      if set(raw) != set(FACTOR_FIELDS) or raw["stock_code"] != code:
        raise ValueError("Invalid reference factor")
      factors.append(
        DividFactor(
          **{
            **raw,
            "time": datetime.fromisoformat(raw["time"]),
            **{field: Decimal(raw[field]) for field in FACTOR_FIELDS[3:]},
          }
        )
      )
    # Available rows are not proof of an empty or authoritative replacement range.
    proof = reference.get("factor_coverage", {})
    if proof.get("status") == "VERIFIED":
      await DividFactorRepository(db).replace_range(
        [
          factor
          for factor in factors
          if proof["start_date"] <= factor.ex_date <= proof["end_date"]
        ],
        stock_codes=[code],
        start_ex_date=proof["start_date"],
        end_ex_date=proof["end_date"],
      )
    elif factors:
      await DividFactorRepository(db).replace_range(
        factors,
        stock_codes=[code],
        start_ex_date=min(factor.ex_date for factor in factors),
        end_ex_date=max(factor.ex_date for factor in factors),
      )
    await db.commit()
  if proof.get("status") == "VERIFIED":
    encoded = json.dumps(reference, sort_keys=True)
    version = hashlib.sha256(encoded.encode()).hexdigest()
    identity = hashlib.sha256(
      f"reference:{code}:{proof['evidence']['request_id']}".encode()
    ).hexdigest()
    async with AsyncSessionLocal() as db:
      await db.execute(
        text("""
        INSERT INTO development_data_export(id,request,state,manifest,updated_at)
        VALUES (:id,CAST(:request AS JSON),'REFERENCE_VERIFIED',CAST(:manifest AS JSON),CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET manifest=EXCLUDED.manifest,updated_at=CURRENT_TIMESTAMP
      """),
        {
          "id": identity,
          "request": json.dumps(
            {
              "operation": "reference",
              "instrument": code,
              "trading_date": reference["as_of"],
            }
          ),
          "manifest": json.dumps({"reference": reference, "data_version": version}),
        },
      )
      await db.commit()


async def imported_factor_evidence(session, codes: set[str]) -> list[tuple]:
  manifests = (
    (
      await session.execute(
        text("""
    SELECT manifest FROM development_data_export WHERE state='REFERENCE_VERIFIED'
      AND request->>'instrument' = ANY(:codes) ORDER BY updated_at DESC LIMIT 1001
  """),
        {"codes": sorted(codes)},
      )
    )
    .scalars()
    .all()
  )
  if len(manifests) > 1000:
    raise ValueError("Imported factor evidence exceeds scan budget")
  result = []
  for manifest in manifests:
    proof = manifest["reference"]["factor_coverage"]
    raw = proof["evidence"]
    item = DividFactorEvidence(
      **{
        **raw,
        "start_date": date.fromisoformat(raw["start_date"]),
        "end_date": date.fromisoformat(raw["end_date"]),
        "completed_at": datetime.fromisoformat(raw["completed_at"]),
      }
    )
    if (
      item.stock_code not in codes
      or proof["expected_chunks"] <= 0
      or proof["expected_chunks"] != proof["received_chunks"]
    ):
      raise ValueError("Invalid imported factor evidence")
    result.append((item, proof["expected_chunks"], proof["received_chunks"]))
  return result
