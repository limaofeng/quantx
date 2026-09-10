"""Bound and pin remote export evidence before downloading any content."""

import json

from quantx_contracts.data_exchange import HistoryPartitionRequest
from sqlalchemy import text

from .development_source_proof import delivery_version, validate_source_proof
from .market_data_transfer_ingestion import (
  MAX_TRANSFER_CHUNK_COMPRESSED_BYTES,
  MAX_TRANSFER_CHUNK_RECORDS,
  MAX_TRANSFER_REQUEST_CHUNKS,
  MAX_TRANSFER_REQUEST_COMPRESSED_BYTES,
  MAX_TRANSFER_REQUEST_RECORDS,
)

MAX_DELIVERY_METADATA_BYTES = 2 * 1024 * 1024


async def read_delivery_metadata(client, method, path, **kwargs):
  async with client.stream(method, path, **kwargs) as response:
    response.raise_for_status()
    body = bytearray()
    async for block in response.aiter_bytes(chunk_size=65536):
      if len(body) + len(block) > MAX_DELIVERY_METADATA_BYTES:
        raise ValueError("DELIVERY_METADATA_BUDGET_EXCEEDED")
      body.extend(block)
  result = json.loads(body)
  if not isinstance(result, dict):
    raise ValueError("DELIVERY_METADATA_INVALID")
  return result


def validate_delivery_manifest(manifest, request: HistoryPartitionRequest):
  if isinstance(manifest, dict) and manifest.get("version") == 1:
    raise ValueError("SOURCE_PROVENANCE_MIGRATION_REQUIRED")
  if not isinstance(manifest, dict) or set(manifest) != {
    "version",
    "payload",
    "chunks",
    "reference",
    "source_request_id",
    "source_proof",
    "data_version",
    "coverage",
    "rows",
  }:
    raise ValueError("DELIVERY_MANIFEST_INVALID")
  if (
    type(manifest["version"]) is not int
    or manifest["version"] != 2
    or manifest["payload"] != request.agent_payload()
    or manifest["coverage"] != "SOURCE_VERIFIED"
    or not isinstance(manifest["reference"], dict)
    or not isinstance(manifest["source_request_id"], str)
    or not manifest["source_request_id"]
    or type(manifest["rows"]) is not int
    or not 0 < manifest["rows"] < MAX_TRANSFER_REQUEST_RECORDS
  ):
    raise ValueError("DELIVERY_MANIFEST_INVALID")
  chunks = manifest["chunks"]
  if (
    not isinstance(chunks, list) or not 1 <= len(chunks) <= MAX_TRANSFER_REQUEST_CHUNKS
  ):
    raise ValueError("DELIVERY_CHUNKS_INVALID")
  total_bytes = total_records = 0
  for index, item in enumerate(chunks):
    if not isinstance(item, dict) or set(item) != {
      "chunk_index",
      "checksum_sha256",
      "record_count",
      "compressed",
      "compressed_bytes",
    }:
      raise ValueError("DELIVERY_CHUNK_INVALID")
    digest = item["checksum_sha256"]
    if (
      type(item["chunk_index"]) is not int
      or item["chunk_index"] != index
      or item["compressed"] is not True
      or not isinstance(digest, str)
      or len(digest) != 64
      or any(c not in "0123456789abcdef" for c in digest)
      or type(item["compressed_bytes"]) is not int
      or not 0 < item["compressed_bytes"] <= MAX_TRANSFER_CHUNK_COMPRESSED_BYTES
      or type(item["record_count"]) is not int
      or not 0 < item["record_count"] <= MAX_TRANSFER_CHUNK_RECORDS
    ):
      raise ValueError("DELIVERY_CHUNK_INVALID")
    total_bytes += item["compressed_bytes"]
    total_records += item["record_count"]
  if (
    total_bytes > MAX_TRANSFER_REQUEST_COMPRESSED_BYTES
    or total_records > MAX_TRANSFER_REQUEST_RECORDS
    or total_records != manifest["rows"] + 1  # one partition summary record
  ):
    raise ValueError("DELIVERY_MANIFEST_BUDGET_INVALID")
  encoded = json.dumps(manifest, allow_nan=False)
  if len(encoded.encode()) > MAX_DELIVERY_METADATA_BYTES:
    raise ValueError("DELIVERY_METADATA_BUDGET_EXCEEDED")
  validate_source_proof(manifest["source_proof"], request)
  if manifest["source_proof"]["partition_records"] != manifest["rows"]:
    raise ValueError("DELIVERY_SOURCE_COUNT_INVALID")
  expected = delivery_version(manifest)
  if manifest["data_version"] != expected:
    raise ValueError("DELIVERY_VERSION_INVALID")


async def pin_delivery_manifest(connection, identity, request, manifest):
  """Caller owns the transaction and, for Worker use, its lease fence."""
  validate_delivery_manifest(manifest, request)
  row = (
    (
      await connection.execute(
        text("""
    SELECT request,manifest FROM development_data_export WHERE id=:id FOR UPDATE
  """),
        {"id": identity},
      )
    )
    .mappings()
    .one()
  )
  if row["request"] != request.model_dump(mode="json"):
    raise ValueError("DELIVERY_SCOPE_CONFLICT")
  if row["manifest"] is not None:
    pinned = {k: v for k, v in row["manifest"].items() if k != "local_verification"}
    if pinned != manifest:
      raise ValueError("DELIVERY_VERSION_CONFLICT")
    return
  await connection.execute(
    text("""
    UPDATE development_data_export SET manifest=CAST(:manifest AS JSON),
      updated_at=CURRENT_TIMESTAMP WHERE id=:id
  """),
    {"id": identity, "manifest": json.dumps(manifest, allow_nan=False)},
  )
