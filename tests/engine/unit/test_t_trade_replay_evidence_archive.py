import asyncio
import json
from unittest.mock import MagicMock

import pytest
from quantx_infrastructure.core.backtest_result_storage import BacktestResultStorage
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.core.t_trade_replay_evidence import (
  ReplayEvidenceUnavailable,
  iter_jsonl,
  read_manifest,
  sealed_opportunity_path,
  validate_replay_archive_for_reset,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "artifact", ["opportunity_evaluations", "decision_events", "execution_summary"]
)
async def test_reset_requires_every_evidence_artifact_to_be_intact(tmp_path, artifact):
  storage = BacktestResultStorage("bt", str(tmp_path), "run", 1)
  await storage.archive_opportunity_evaluations(records(), account_id="test-account")
  path = await storage.flush()
  validate_replay_archive_for_reset(
    path, run_id="run", backtest_id="bt", version=1, account_id="test-account"
  )
  (tmp_path / "run/v1" / f"{artifact}.jsonl").write_text("{}\n")
  with pytest.raises(ReplayEvidenceUnavailable, match="INTEGRITY_FAILED"):
    validate_replay_archive_for_reset(
      path, run_id="run", backtest_id="bt", version=1, account_id="test-account"
    )


@pytest.mark.asyncio
async def test_reset_checks_archive_record_counts(tmp_path):
  storage = BacktestResultStorage("bt", str(tmp_path), "run", 1)
  await storage.archive_opportunity_evaluations(records(), account_id="test-account")
  path = await storage.flush()
  manifest_path = tmp_path / "run/v1/manifest.json"
  manifest = json.loads(manifest_path.read_text())
  manifest["opportunity_evaluations"]["count"] += 1
  manifest_path.write_text(json.dumps(manifest))
  with pytest.raises(ReplayEvidenceUnavailable, match="INTEGRITY_FAILED"):
    validate_replay_archive_for_reset(
      path, run_id="run", backtest_id="bt", version=1, account_id="test-account"
    )


@pytest.mark.asyncio
async def test_terminal_finalize_is_idempotent_without_overwriting_archive(
  tmp_path, monkeypatch
):
  from quantx_infrastructure.database import connection
  from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
    TTradeOpportunityEvaluationRepository,
  )

  async def database():
    yield object()

  export = MagicMock(side_effect=lambda **_kwargs: records())
  monkeypatch.setattr(connection, "get_async_db", database)
  monkeypatch.setattr(
    TTradeOpportunityEvaluationRepository, "iter_run_evaluations", export
  )
  manager = RuntimeStateManager(run_id="run", persist_enabled=False)
  manager._backtest_storage = BacktestResultStorage("bt", str(tmp_path), "run", 1)

  path = await manager.finalize_backtest(opportunity_account_id="test-account")
  original = (tmp_path / "run/v1/manifest.json").read_bytes()
  assert await manager.finalize_backtest(opportunity_account_id="test-account") == path
  export.assert_called_once_with(account_id="test-account", strategy_run_id="run")
  assert (tmp_path / "run/v1/manifest.json").read_bytes() == original
  validate_replay_archive_for_reset(
    path, run_id="run", backtest_id="bt", version=1, account_id="test-account"
  )

  # Idempotence must not hide new, unmaterialized evidence after sealing.
  monkeypatch.setattr(manager, "pending_t_trade_material_events", lambda: [{}])
  with pytest.raises(RuntimeError, match="MATERIALIZATION_PENDING"):
    await manager.finalize_backtest(opportunity_account_id="test-account")


@pytest.mark.asyncio
async def test_concurrent_terminal_callers_share_one_complete_archive(
  tmp_path, monkeypatch
):
  from quantx_infrastructure.database import connection
  from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
    TTradeOpportunityEvaluationRepository,
  )

  entered = asyncio.Event()
  release = asyncio.Event()

  async def database():
    yield object()

  async def paused_records(**_kwargs):
    yield record(1)
    entered.set()
    await release.wait()
    yield record(2)

  export = MagicMock(side_effect=paused_records)
  monkeypatch.setattr(connection, "get_async_db", database)
  monkeypatch.setattr(
    TTradeOpportunityEvaluationRepository, "iter_run_evaluations", export
  )
  manager = RuntimeStateManager(run_id="run", persist_enabled=False)
  manager._backtest_storage = BacktestResultStorage("bt", str(tmp_path), "run", 1)
  first = asyncio.create_task(
    manager.finalize_backtest(opportunity_account_id="test-account")
  )
  await asyncio.wait_for(entered.wait(), timeout=2)
  second = asyncio.create_task(
    manager.finalize_backtest(opportunity_account_id="test-account")
  )
  try:
    await asyncio.sleep(0)
    assert export.call_count == 1
  finally:
    release.set()
    paths = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
  assert paths[0] == paths[1]
  export.assert_called_once()
  validate_replay_archive_for_reset(
    paths[0], run_id="run", backtest_id="bt", version=1, account_id="test-account"
  )
