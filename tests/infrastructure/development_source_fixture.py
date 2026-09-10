"""Explicit source provenance for isolated delivery fixtures."""

from quantx_infrastructure.services.market_data_ingestion_progress import evidence_hash


def provenance(request, digest="a" * 64, records=1):
  payload = request.agent_payload()
  return {
    "source_created_at": request.trading_date.isoformat() + "T08:00:00+00:00",
    "source_payload": payload,
    "native_storage_version": evidence_hash(
      {
        "storage_format": "native-bars-v1",
        "payload": payload,
        "content_sha256": digest,
      }
    ),
    "source_content_sha256": digest,
    "source_records_verified": records,
    "source_fields_verified": records * 8,
    "partition_records": records,
    "partition_sha256": digest,
  }
