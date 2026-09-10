"""Legacy receipt candidates bind original bytes to native source provenance."""

# ruff: noqa: F811
import copy
import hashlib
import json
from datetime import datetime

import pytest
from quantx_infrastructure.services.data_exchange import content_path
from quantx_infrastructure.services.development_version_ingestion import (
  ingest_development_storage_version,
)
from quantx_infrastructure.services.legacy_delivery_proof import upgrade_legacy_delivery

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_immutable_bar_storage import VersionStorage
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


def inputs(case):
  proof = case.manifest["source_proof"]
  legacy = copy.deepcopy(case.manifest)
  legacy.pop("source_proof")
  legacy["version"] = 1
  legacy["data_version"] = hashlib.sha256(
    json.dumps(
      {"chunks": legacy["chunks"], "reference": legacy["reference"]}, sort_keys=True
    ).encode()
  ).hexdigest()
  legacy["local_verification"] = {"records_verified": 1, "old_receipt": "preserve"}
  source = {
    "request_id": legacy["source_request_id"],
    "status": "COMPLETED",
    "created_at": datetime.fromisoformat(proof["source_created_at"]),
    "request_payload": proof["source_payload"],
    "ingestion_result": {
      "native_storage_version": proof["native_storage_version"],
      "records_verified": proof["source_records_verified"],
      "persistence_verification": {
        "status": "verified",
        "records_verified": proof["source_records_verified"],
      },
      "content_verification": {
        "schema_version": 1,
        "records_verified": proof["source_records_verified"],
        "fields_verified": proof["source_fields_verified"],
        "source_sha256": proof["source_content_sha256"],
        "persisted_sha256": proof["source_content_sha256"],
        "storage_version": proof["native_storage_version"],
      },
      "day_coverage": [
        {
          "instrument_code": case.request.instrument,
          "period": case.request.period,
          "trading_date": case.request.trading_date.isoformat(),
          "point_count": proof["partition_records"],
          "content_sha256": proof["partition_sha256"],
        }
      ],
    },
  }
  return legacy, source


@pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)
async def test_upgrade_preserves_bytes_and_old_receipt_before_real_new_proof(prepared):
  case = prepared
  legacy, source = inputs(case)
  before = copy.deepcopy(legacy)
  files = {
    c["checksum_sha256"]: content_path(c["checksum_sha256"]).read_bytes()
    for c in legacy["chunks"]
  }
  candidate = await upgrade_legacy_delivery(
    legacy, case.request, source, delivery_id="delivery"
  )
  assert legacy == before == candidate["previous_receipt"]
  assert candidate["manifest"] == case.manifest
  assert "local_verification" not in candidate["manifest"]
  assert candidate["storage_version"] == case.version.storage_version
  storage = VersionStorage()
  receipt = await ingest_development_storage_version(
    case.request, candidate["manifest"], case.progress, connection=storage
  )
  assert receipt["local_verification"]["immutable_storage"]["records_verified"] == 1
  assert len(storage.lines) == 1
  assert files == {key: content_path(key).read_bytes() for key in files}


@pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)
@pytest.mark.parametrize(
  "change", ["source_id", "status", "old_version", "scope", "partition_hash", "file"]
)
async def test_upgrade_refuses_changed_source_scope_or_file(prepared, change):
  case = prepared
  legacy, source = inputs(case)
  if change == "source_id":
    source["request_id"] = "replacement-source"
  elif change == "status":
    source["status"] = "UPLOADED"
  elif change == "old_version":
    legacy["data_version"] = "f" * 64
  elif change == "scope":
    legacy["payload"]["stock_list"] = ["000001.SZ"]
  elif change == "partition_hash":
    source["ingestion_result"]["day_coverage"][0]["content_sha256"] = "f" * 64
  else:
    content_path(legacy["chunks"][0]["checksum_sha256"]).write_bytes(b"broken")
  before = copy.deepcopy(legacy)
  from quantx_infrastructure.services.market_data_transfer_ingestion import (
    MarketDataValidationError,
  )

  with pytest.raises(MarketDataValidationError if change == "file" else ValueError):
    await upgrade_legacy_delivery(legacy, case.request, source, delivery_id="delivery")
  assert legacy == before
