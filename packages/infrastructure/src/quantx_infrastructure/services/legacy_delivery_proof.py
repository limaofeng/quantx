"""Build a v2 migration candidate from unchanged v1 files and the original source."""

import hashlib
import json
from copy import deepcopy

from .development_delivery_manifest import (
  MAX_DELIVERY_METADATA_BYTES,
  validate_delivery_manifest,
)
from .development_history_import import ImportedTransfer
from .development_source_proof import delivery_version, source_proof
from .immutable_bar_storage import prepare_immutable_bar_version
from .market_data_ingestion_progress import evidence_hash


async def upgrade_legacy_delivery(receipt, request, source, *, delivery_id):
  """No writes: caller must freeze both catalogs and retain the returned old receipt."""
  if (
    not isinstance(receipt, dict)
    or len(json.dumps(receipt, allow_nan=False).encode()) > MAX_DELIVERY_METADATA_BYTES
  ):
    raise ValueError("LEGACY_DELIVERY_METADATA_INVALID")
  original = {k: v for k, v in receipt.items() if k != "local_verification"}
  if (
    type(original.get("version")) is not int
    or original["version"] != 1
    or set(original)
    != {
      "version",
      "payload",
      "chunks",
      "reference",
      "source_request_id",
      "coverage",
      "rows",
      "data_version",
    }
  ):
    raise ValueError("LEGACY_DELIVERY_MANIFEST_INVALID")
  old_version = hashlib.sha256(
    json.dumps(
      {"chunks": original["chunks"], "reference": original["reference"]},
      sort_keys=True,
      allow_nan=False,
    ).encode()
  ).hexdigest()
  if original["data_version"] != old_version:
    raise ValueError("LEGACY_DELIVERY_VERSION_INVALID")
  if (
    source.get("status") != "COMPLETED"
    or source.get("request_id") != original["source_request_id"]
  ):
    raise ValueError("LEGACY_DELIVERY_SOURCE_IDENTITY_INVALID")
  candidate = deepcopy(original)
  candidate.update(version=2, source_proof=source_proof(source, request))
  candidate["data_version"] = delivery_version(candidate)
  validate_delivery_manifest(candidate, request)
  # Real normalized content, not only a chunk checksum or a caller-supplied digest.
  version = await prepare_immutable_bar_version(
    ImportedTransfer(candidate), delivery_id
  )
  if (
    (version.code, version.period, version.trading_date)
    != (request.instrument, request.period, request.trading_date.isoformat())
    or version.records != candidate["rows"]
    or version.content_sha256 != candidate["source_proof"]["partition_sha256"]
  ):
    raise ValueError("LEGACY_DELIVERY_SOURCE_CONTENT_MISMATCH")
  return {
    "previous_receipt": deepcopy(receipt),
    "previous_receipt_sha256": evidence_hash(receipt),
    "manifest": candidate,
    "storage_version": version.storage_version,
    "progress_manifest_sha256": evidence_hash(
      {
        "storage_version": version.storage_version,
        "manifest": json.loads(version.manifest_json),
      }
    ),
  }
