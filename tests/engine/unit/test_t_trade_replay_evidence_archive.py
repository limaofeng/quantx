import json

import pytest
from quantx_infrastructure.core.backtest_result_storage import BacktestResultStorage
from quantx_infrastructure.core.t_trade_replay_evidence import (
  ReplayEvidenceUnavailable,
  iter_jsonl,
  read_manifest,
  sealed_opportunity_path,
)


def record(index=1):
  return {
    "id": str(index),
    "event_key": f"event-{index}",
    "account_id": "test-account",
    "strategy_run_id": "run",
    "instrument_code": "600000.SH",
    "evaluated_at": "2026-08-28T02:00:00",
    "record_kind": "MATERIAL",
    "event_type": "CANDIDATE_SUPPRESSED",
    "payload": {"signal_snapshot": {}},
  }


async def records(count=2):
  for index in range(count):
    yield record(index)


@pytest.mark.asyncio
async def test_archive_seals_exact_version_with_empty_or_real_signals(tmp_path):
  for version, count in ((1, 0), (2, 1001)):
    storage = BacktestResultStorage(
      f"bt-{version}",
      str(tmp_path),
      "run",
      version,
    )
    await storage.archive_opportunity_evaluations(
      records(count), account_id="test-account"
    )
    path = await storage.flush()
    manifest = read_manifest(
      path, run_id="run", backtest_id=f"bt-{version}", version=version
    )
    assert manifest["schema_version"] == 4
    assert manifest["sealed"] is True
    assert manifest["opportunity_evaluations"]["count"] == count
    archive = sealed_opportunity_path(path, manifest, account_id="test-account")
    assert sum(1 for _ in iter_jsonl(archive)) == count
    with pytest.raises(ReplayEvidenceUnavailable, match="IDENTITY_MISMATCH"):
      read_manifest(
        path, run_id="another-run", backtest_id=f"bt-{version}", version=version
      )
    with pytest.raises(ReplayEvidenceUnavailable, match="IDENTITY_MISMATCH"):
      sealed_opportunity_path(path, manifest, account_id="another-account")


@pytest.mark.asyncio
async def test_failed_export_does_not_publish_partial_archive_or_manifest(tmp_path):
  async def broken():
    yield record()
    raise RuntimeError("export interrupted")

  storage = BacktestResultStorage("bt", str(tmp_path), "run", 1)
  with pytest.raises(RuntimeError, match="export interrupted"):
    await storage.archive_opportunity_evaluations(broken(), account_id="test-account")
  assert not (tmp_path / "run/v1/opportunity_evaluations.jsonl").exists()
  assert not (tmp_path / "run/v1/manifest.json").exists()
  assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_archive_corruption_is_not_an_empty_signal_page(tmp_path):
  storage = BacktestResultStorage("bt", str(tmp_path), "run", 1)
  await storage.archive_opportunity_evaluations(records(), account_id="test-account")
  path = await storage.flush()
  manifest = json.loads((tmp_path / "run/v1/manifest.json").read_text())
  (tmp_path / "run/v1/opportunity_evaluations.jsonl").write_text("{}\n")
  with pytest.raises(ReplayEvidenceUnavailable, match="INTEGRITY_FAILED"):
    sealed_opportunity_path(path, manifest, account_id="test-account")


def test_legacy_archive_is_explicitly_unavailable():
  with pytest.raises(ReplayEvidenceUnavailable, match="SIGNAL_ARCHIVE_NOT_RECORDED"):
    sealed_opportunity_path(
      "manifest.json", {"schema_version": 3}, account_id="test-account"
    )


@pytest.mark.asyncio
async def test_sealed_version_cannot_be_overwritten(tmp_path):
  storage = BacktestResultStorage("bt", str(tmp_path), "run", 1)
  await storage.archive_opportunity_evaluations(records(), account_id="test-account")
  path = await storage.flush()
  with pytest.raises(ValueError, match="ALREADY_SEALED"):
    await storage.flush()
  with pytest.raises(ValueError, match="ALREADY_SEALED"):
    await storage.archive_opportunity_evaluations(records(0), account_id="test-account")
  manifest = read_manifest(path, run_id="run", backtest_id="bt", version=1)
  assert manifest["opportunity_evaluations"]["count"] == 2


def test_compaction_preserves_exact_evaluation_links():
  references = [
    {
      "record_kind": "MATERIAL",
      "event_type": "CANDIDATE_SUPPRESSED",
      "evaluation_event_key": "exact-event-key",
    }
  ]
  assert (
    BacktestResultStorage._compact_output_summary(
      {
        "format": "T_TRADE_MATERIAL_V1",
        "record_kind": "MATERIAL",
        "evaluation_references": references,
      }
    )["evaluation_references"]
    == references
  )
