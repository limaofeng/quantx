"""Read-only recovery must reread coverage and validate the immutable files."""

import pytest
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion

from tests.infrastructure.test_market_data_transfer_ingestion import (
  ManifestStore,
  _payload,
  _summary,
  _tick_row,
  _write_chunk,
)


async def test_recheck_reads_again_without_writes_or_old_readback_checkpoint(
  tmp_path, monkeypatch
):
  row = _tick_row()
  store = ManifestStore(
    payload=_payload(), manifest=[_write_chunk(tmp_path, [row, _summary([row])])]
  )
  calls = 0
  from tests.infrastructure.test_market_data_content_verification import (
    stub_content_verifier,
  )

  monkeypatch.setattr(ingestion, "verify_persisted_bar_content", stub_content_verifier)

  def write(**kwargs):
    pytest.fail("readonly recovery wrote market data")

  monkeypatch.setattr(ingestion, "_save_market_data_period_sync", write)

  async def verify(**kwargs):
    nonlocal calls
    calls += 1
    assert kwargs["max_attempts"] == 1
    assert kwargs["concurrency"] == 1
    assert kwargs["progress"] is None
    assert len([b async for b in kwargs["expected_key_batches"]]) == 1
    summaries = [
      {
        k: item[k]
        for k in ("code", "period", "row_count", "min_time", "max_time", "key_sha256")
      }
      for item in kwargs["code_summaries"]
    ]
    return {
      "status": "verified",
      "records_verified": 1 if calls == 1 else 0,
      "groups_verified": 1,
      "code_summaries": summaries,
    }

  result = await ingestion.verify_uploaded_bar_request(
    store, "request-1", verify_persistence=verify
  )
  assert result["records_verified"] == 1
  with pytest.raises(ingestion.MarketDataPersistenceVerificationError):
    await ingestion.verify_uploaded_bar_request(
      store, "request-1", verify_persistence=verify
    )
  assert calls == 2


async def test_changed_archive_is_rejected_before_storage_read(tmp_path):
  row = _tick_row()
  item = _write_chunk(tmp_path, [row, _summary([row])])
  store = ManifestStore(payload=_payload(), manifest=[item])
  from pathlib import Path

  Path(item["storage_reference"]).write_bytes(b"changed")

  async def verify(**kwargs):
    pytest.fail("corrupt source must be rejected before storage query")

  with pytest.raises(ingestion.MarketDataValidationError):
    await ingestion.verify_uploaded_bar_request(
      store, "request-1", verify_persistence=verify
    )
