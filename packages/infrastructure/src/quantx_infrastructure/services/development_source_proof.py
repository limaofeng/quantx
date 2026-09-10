"""Bind remote partition provenance to its native immutable source receipt."""

import hashlib
import json
import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .market_data_ingestion_progress import evidence_hash
from .native_bar_publication import validate_native_bar_receipt


class SourceProofInvalid(ValueError):
  pass


def delivery_version(manifest):
  return hashlib.sha256(
    json.dumps(
      {
        k: v
        for k, v in manifest.items()
        if k not in {"data_version", "local_verification"}
      },
      sort_keys=True,
      allow_nan=False,
    ).encode()
  ).hexdigest()


def source_proof(source, request):
  payload, audit = source.get("request_payload"), source.get("ingestion_result")
  version = validate_native_bar_receipt(payload, audit)
  created = source.get("created_at")
  if not isinstance(created, datetime):
    raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID")
  # market_data_request.created_at is stored as UTC without a timezone.
  created = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
  coverage = audit.get("day_coverage")
  if not isinstance(coverage, list):
    raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID")
  partitions = [
    item
    for item in coverage
    if isinstance(item, dict)
    and item.get("instrument_code") == request.instrument
    and item.get("period") == request.period
    and item.get("trading_date") == request.trading_date.isoformat()
  ]
  if len(partitions) != 1:
    raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID")
  partition = partitions[0]
  proof = {
    "source_created_at": created.astimezone(timezone.utc).isoformat(),
    "source_payload": payload,
    "native_storage_version": version,
    "source_content_sha256": audit["content_verification"]["source_sha256"],
    "source_records_verified": audit["content_verification"]["records_verified"],
    "source_fields_verified": audit["content_verification"]["fields_verified"],
    "partition_records": partition.get("point_count"),
    "partition_sha256": partition.get("content_sha256"),
  }
  validate_source_proof(proof, request)
  return proof


def validate_source_proof(proof, request):
  if not isinstance(proof, dict) or set(proof) != {
    "source_created_at",
    "source_payload",
    "native_storage_version",
    "source_content_sha256",
    "source_records_verified",
    "source_fields_verified",
    "partition_records",
    "partition_sha256",
  }:
    raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID")
  for key in ("native_storage_version", "source_content_sha256", "partition_sha256"):
    if (
      not isinstance(proof[key], str)
      or re.fullmatch(r"[0-9a-f]{64}", proof[key]) is None
    ):
      raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID")
  for key in ("source_records_verified", "source_fields_verified", "partition_records"):
    if type(proof[key]) is not int or proof[key] <= 0:
      raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID")
  if proof["partition_records"] > proof["source_records_verified"]:
    raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID")
  payload = proof["source_payload"]
  day = request.trading_date.strftime("%Y%m%d")
  if (
    not isinstance(payload, dict)
    or payload.get("operation") != "bars"
    or type(payload.get("download")) is not bool
    or not isinstance(payload.get("stock_list"), list)
    or request.instrument not in payload["stock_list"]
    or not isinstance(payload.get("periods"), list)
    or request.period not in payload["periods"]
    or any(
      not isinstance(payload.get(k), str)
      or re.fullmatch(r"[0-9]{8}", payload[k]) is None
      for k in ("start_time", "end_time")
    )
    or not payload["start_time"] <= day <= payload["end_time"]
    or proof["native_storage_version"]
    != evidence_hash(
      {
        "storage_format": "native-bars-v1",
        "payload": payload,
        "content_sha256": proof["source_content_sha256"],
      }
    )
  ):
    raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID")
  try:
    created = datetime.fromisoformat(proof["source_created_at"])
    if created.tzinfo is None:
      raise ValueError()
  except (TypeError, ValueError):
    raise SourceProofInvalid("SOURCE_PROVENANCE_INVALID") from None
  cutoff = datetime.combine(
    request.trading_date, time(15, 1), ZoneInfo("Asia/Shanghai")
  )
  return payload["download"] is True and created >= cutoff
