"""Native source manifests write immutable versions before publishing a receipt."""

from quantx_application.market_data.ingestion import IngestionEvidenceConflict

from quantx_infrastructure.database.timeseries import get_timeseries_connection

from .immutable_bar_storage import (
  prepare_native_bar_bundle,
  verify_immutable_bar_version,
  write_immutable_bar_version,
)
from .market_data_ingestion_progress import evidence_hash


async def ingest_native_bar_bundle(
  payload, manifest, audit, *, progress=None, read_only=False
):
  version = await prepare_native_bar_bundle(payload, manifest)
  persisted = {
    **audit,
    "records_saved": version.records,
    "rows_accepted": version.records,
    "native_storage_version": version.storage_version,
    "day_coverage": [
      {
        "instrument_code": code,
        "period": period,
        "trading_date": day,
        "point_count": count,
      }
      for code, period, day, count in version.coverage
    ],
  }
  if progress is not None:
    original_manifest = {
      "payload": payload,
      "chunks": [
        {k: v for k, v in item.items() if k != "storage_reference"} for item in manifest
      ],
    }
    if progress.state.get("manifest_hash") == evidence_hash(original_manifest):
      raise IngestionEvidenceConflict("NATIVE_STORAGE_VERSION_MIGRATION_REQUIRED")
    await progress.apply(
      "manifest",
      sha256=evidence_hash(
        {
          "storage_format": "native-bars-v1",
          "storage_version": version.storage_version,
          "payload": payload,
          "chunks": [
            {k: v for k, v in item.items() if k != "storage_reference"}
            for item in manifest
          ],
        }
      ),
    )
    if progress.state["phase"] == "VALIDATE":
      await progress.apply("advance", phase="WRITE")
    if (
      progress.state["phase"] == "READBACK"
      and progress.state["write_result"] != persisted
    ):
      raise IngestionEvidenceConflict(
        "native immutable write checkpoint differs from source"
      )
  connection = get_timeseries_connection()
  if not read_only and (progress is None or progress.state["phase"] != "READBACK"):
    await write_immutable_bar_version(version, connection=connection, progress=progress)
    if progress is not None:
      await progress.apply("advance", phase="READBACK", write_result=persisted)
  content = await verify_immutable_bar_version(version, connection=connection)
  # The content verifier compares every source key and owned field. Its fixed
  # version cannot be mutated by another source manifest or a late old request.
  fields = ("code", "period", "row_count", "min_time", "max_time", "key_sha256")
  return {
    **persisted,
    "records_verified": version.records,
    "content_verification": content,
    "persistence_verification": {
      "status": "verified",
      "records_verified": version.records,
      "groups_verified": len(audit["code_summaries"]),
      "code_summaries": [
        {key: item.get(key) for key in fields} for item in audit["code_summaries"]
      ],
    },
  }
