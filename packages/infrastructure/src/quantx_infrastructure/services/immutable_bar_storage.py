"""Content-addressed historical storage, before publication by a fenced catalog.

These tables are separate from the current published historical tables. Creating
a version does not publish it or authorize any reader to choose the latest tag.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass

from quantx_infrastructure.database.timeseries_operations import (
  TimeSeriesOperations,
  single_write_attempt,
)

from .market_data_content_verification import (
  _canonical,
  _compare,
  verify_persisted_bar_content,
)
from .market_data_ingestion_progress import evidence_hash
from .market_data_transfer_ingestion import (
  _parse_bars_request,
  _settle_task,
  _uploaded_content_batches,
  _validate_bar_manifest,
  load_uploaded_request_manifest,
)


@dataclass(frozen=True)
class ImmutableBarVersion:
  manifest_json: str
  code: str
  period: str
  trading_date: str
  records: int
  content_sha256: str
  storage_version: str


async def _prepare(manifest_json):
  value = json.loads(manifest_json)
  payload, manifest = value["payload"], value["chunks"]
  scope = _parse_bars_request(payload)
  if (
    len(scope.codes) != 1
    or len(scope.periods) != 1
    or scope.start_date != scope.end_date
  ):
    raise ValueError("immutable storage requires one daily partition")
  audit = await asyncio.to_thread(_validate_bar_manifest, manifest, payload)
  digest, count = hashlib.sha256(), 0
  async for frame in _uploaded_content_batches(manifest):
    for row in frame.to_dict("records"):
      normalized, _ = _compare(row, row)
      digest.update(_canonical(normalized) + b"\n")
      count += 1
  if count != audit["records_received"]:
    raise ValueError("immutable storage source count changed")
  content = digest.hexdigest()
  identity = {
    "storage_format": 1,
    "code": scope.codes[0],
    "period": scope.periods[0],
    "trading_date": scope.start_date.isoformat(),
    "content_sha256": content,
  }
  return ImmutableBarVersion(
    manifest_json,
    identity["code"],
    identity["period"],
    identity["trading_date"],
    count,
    content,
    evidence_hash(identity),
  )


async def prepare_immutable_bar_version(store, request_id):
  _, payload, manifest = await load_uploaded_request_manifest(store, request_id)
  return await _prepare(
    json.dumps(
      {"payload": payload, "chunks": manifest}, sort_keys=True, allow_nan=False
    )
  )


async def _revalidate(version):
  if (
    not isinstance(version, ImmutableBarVersion)
    or await _prepare(version.manifest_json) != version
  ):
    raise ValueError("immutable storage source or version changed")
  return json.loads(version.manifest_json)["chunks"]


def _write_frame(connection, version, frame):
  # The owned frame and tag are never handed to a concurrent caller. Source
  # changes produce a different content digest and therefore a different key.
  records = frame.copy(deep=True)
  records["storage_version"] = version.storage_version
  measurement = {
    "tick": "ticks_versions",
    "1m": "kline_1m_versions",
    "1d": "kline_1d_versions",
  }[version.period]
  with single_write_attempt():
    TimeSeriesOperations(connection).write_dataframe(
      records, measurement, ["stock_code", "period", "storage_version"], batch_size=1000
    )


async def write_immutable_bar_version(version, *, connection):
  manifest = await _revalidate(version)
  count = 0
  async for frame in _uploaded_content_batches(manifest):
    task = asyncio.create_task(
      asyncio.to_thread(_write_frame, connection, version, frame)
    )
    try:
      await asyncio.shield(task)
    except asyncio.CancelledError:
      await _settle_task(task)
      raise
    count += len(frame)
  if count != version.records:
    raise ValueError("immutable storage write count changed")
  return count


async def verify_immutable_bar_version(version, *, connection):
  manifest = await _revalidate(version)
  proof = await verify_persisted_bar_content(
    _uploaded_content_batches(manifest),
    connection=connection,
    storage_version=version.storage_version,
  )
  if (
    proof["records_verified"] != version.records
    or proof["source_sha256"] != version.content_sha256
    or proof["persisted_sha256"] != version.content_sha256
  ):
    raise ValueError("immutable storage content proof changed")
  return {
    **proof,
    "storage_version": version.storage_version,
    "code": version.code,
    "period": version.period,
    "trading_date": version.trading_date,
  }
