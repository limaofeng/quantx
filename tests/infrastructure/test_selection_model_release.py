import hashlib
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import lightgbm as lgb
import numpy as np
import pytest
from quantx_api.stock_selection_model_service import StockSelectionModelService
from quantx_domain.selection_factors import selection_feature_columns
from quantx_infrastructure.selection_model_release import export_release, verify_release
from quantx_infrastructure.services.stock_selection_artifacts import file_sha256, load_selection_artifact
from quantx_infrastructure.training_bundle_store import BundleTransferError

from tests.infrastructure.test_stock_selection_artifacts import _valid_artifact, _write_json


@pytest.fixture
def artifact(tmp_path):
  directory = _valid_artifact(tmp_path / "final-run")
  columns = list(selection_feature_columns())
  data = np.arange(16 * len(columns), dtype=float).reshape(16, len(columns))
  model = lgb.train(
    {"objective": "binary", "device_type": "cpu", "num_threads": 1,
     "num_leaves": 2, "min_data_in_leaf": 1, "verbosity": -1},
    lgb.Dataset(data, label=[0, 1] * 8, feature_name=columns), num_boost_round=1,
  )
  model.save_model(str(directory / "lightgbm.txt"))
  (directory / "resolved-config.yaml").write_text("requested_backend: CPU\nresolved_backend: CPU\nrandom_seed: 42\n")
  _write_json(directory / "source-evidence.json", {
    "schema_version": 1, "source": "trainer-package", "commit": "a" * 40,
    "dirty": False, "code_manifest_sha256": "b" * 64,
  })
  manifest = json.loads((directory / "manifest.json").read_text())
  manifest["parent_run_id"] = "development-run"
  manifest["environment"]["dependencies"]["pandas"] = manifest["environment"]["pandas"]
  manifest["telemetry"]["environment"] = manifest["environment"]
  manifest["artifacts"] = [
    {"path": path.name, "bytes": path.stat().st_size, "sha256": file_sha256(path)}
    for path in sorted(directory.iterdir()) if path.name != "manifest.json"
  ]
  _write_json(directory / "manifest.json", manifest)
  return load_selection_artifact(directory)


def export(artifact, tmp_path):
  key = hashlib.sha256(f"next-day-selection\0v1\0{artifact.manifest['run_id']}".encode()).hexdigest()
  return export_release(artifact, run_key=key, reviewed_by="isolated-reviewer",
                        output=tmp_path / "release", reserve_bytes=0)


def test_release_reloads_real_cpu_models_and_pins_every_file(artifact, tmp_path):
  assert artifact.manifest_sha256 == file_sha256(artifact.directory / "manifest.json")
  release = export(artifact, tmp_path)
  assert release.cpu_runtime["status"] == "PASSED"
  assert release.cpu_runtime["num_threads"] == 1
  assert release.cpu_runtime["feature_count"] == len(selection_feature_columns())
  with pytest.raises(ValueError, match="INVENTORY_IDENTITY"):
    verify_release(tmp_path / "release", expected_bundle_id="0" * 64)
  (release.artifact.directory / "lightgbm.txt").write_text("tree\n")
  with pytest.raises(BundleTransferError, match="INTEGRITY"):
    verify_release(tmp_path / "release", expected_bundle_id=release.inventory.bundle_id)


def test_offline_cli_verifies_pinned_package(artifact, tmp_path):
  release = export(artifact, tmp_path)
  script = Path(__file__).resolve().parents[2] / "ops/trainer/release_model.py"
  result = subprocess.run([sys.executable, "-I", str(script), "verify", "--package",
    str(tmp_path / "release"), "--bundle-id", release.inventory.bundle_id],
    capture_output=True, text=True, timeout=30)
  assert result.returncode == 0, result.stderr
  assert json.loads(result.stdout)["status"] == "VERIFIED"


@pytest.mark.parametrize("defect", ["dirty", "missing-source", "fake-model", "review", "seed", "dependency"])
def test_invalid_release_never_publishes_output(artifact, tmp_path, defect):
  root = artifact.directory
  if defect == "dirty":
    source = json.loads((root / "source-evidence.json").read_text())
    source["dirty"] = True
    _write_json(root / "source-evidence.json", source)
  elif defect == "missing-source":
    (root / "source-evidence.json").unlink()
  elif defect == "fake-model":
    (root / "lightgbm.txt").write_text("tree\n")
  elif defect == "seed":
    (root / "resolved-config.yaml").write_text("random_seed: true\n")
  manifest = json.loads((root / "manifest.json").read_text())
  if defect == "dependency":
    manifest["environment"]["dependencies"].pop("pandas")
    manifest["telemetry"]["environment"] = manifest["environment"]
  manifest["artifacts"] = [
    {"path": path.name, "bytes": path.stat().st_size, "sha256": file_sha256(path)}
    for path in root.iterdir() if path.name != "manifest.json"
  ]
  _write_json(root / "manifest.json", manifest)
  fresh = load_selection_artifact(root)
  key = hashlib.sha256(f"next-day-selection\0v1\0{root.name}".encode()).hexdigest()
  with pytest.raises(ValueError):
    export_release(fresh, run_key=key, reviewed_by="" if defect == "review" else "reviewer",
                   output=tmp_path / "rejected", reserve_bytes=0)
  assert not (tmp_path / "rejected").exists()


@pytest.mark.asyncio
async def test_database_failure_keeps_verified_files_for_retry(artifact, tmp_path):
  release = export(artifact, tmp_path)
  repo = SimpleNamespace(register_model=AsyncMock(side_effect=RuntimeError("database unavailable")))
  service = StockSelectionModelService(repo, runs_root=tmp_path / "unused")
  kwargs = dict(expected_bundle_id=release.inventory.bundle_id,
                import_root=tmp_path / "target", reserve_bytes=0)
  with pytest.raises(RuntimeError, match="database unavailable"):
    await service.import_release(tmp_path / "release", **kwargs)
  installed = tmp_path / "target" / release.inventory.bundle_id
  assert verify_release(installed, expected_bundle_id=release.inventory.bundle_id)
  original = (installed / "bundle.json").stat().st_mtime_ns
  repo.register_model.side_effect = None
  await service.import_release(tmp_path / "release", **kwargs)
  assert (installed / "bundle.json").stat().st_mtime_ns == original
  assert repo.register_model.await_count == 2


@pytest.mark.asyncio
async def test_target_registers_without_source_training_tables_or_active_state(artifact, tmp_path):
  from sqlalchemy import func, select
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
  from quantx_infrastructure.database.relational_base import Base
  from quantx_infrastructure.models.stock_selection import StockSelectionModelVersion
  from quantx_infrastructure.repositories.stock_selection_repository import StockSelectionRepository

  release = export(artifact, tmp_path)
  target = create_async_engine("sqlite+aiosqlite:///:memory:")
  try:
    async with target.begin() as connection:
      await connection.run_sync(lambda db: Base.metadata.create_all(db, tables=[StockSelectionModelVersion.__table__]))
    async with async_sessionmaker(target, expire_on_commit=False)() as db:
      repo = StockSelectionRepository(db)
      service = StockSelectionModelService(repo, runs_root=tmp_path / "unused")
      kwargs = dict(expected_bundle_id=release.inventory.bundle_id,
                    import_root=tmp_path / "target-models", reserve_bytes=0)
      row = await service.import_release(tmp_path / "release", **kwargs)
      assert row.stage == "CANDIDATE"
      assert row.approved_by == ""
      assert row.evidence["release"]["review"]["reviewed_by"] == "isolated-reviewer"
      assert row.evidence["release"]["cpu_runtime"]["status"] == "PASSED"
      assert await repo.runtime_models() == []
      row = await repo.set_model_stage(row.model_version, "SHADOW", expected_version=row.state_version,
                                      approved_by="target-reviewer", approved_at=datetime.now(timezone.utc))
      repeated = await service.import_release(tmp_path / "release", **kwargs)
      assert repeated.stage == "SHADOW"
      assert repeated.approved_by == "target-reviewer"
      assert await db.scalar(select(func.count()).select_from(StockSelectionModelVersion)) == 1
  finally:
    await target.dispose()


@pytest.mark.asyncio
async def test_export_requires_successful_source_lineage_without_registering(artifact, tmp_path):
  key = hashlib.sha256(b"next-day-selection\0v1\0final-run").hexdigest()
  row = SimpleNamespace(run_id="final-run", run_key=key, run_kind="FINAL_EVALUATION",
                        status="FAILED", spec_id="final-spec", parent_run_id="development-run",
                        artifact_manifest_sha256=artifact.manifest_sha256)
  target = SimpleNamespace(register_model=AsyncMock())
  training = SimpleNamespace(get_run_by_run_key=AsyncMock(return_value=row))
  service = StockSelectionModelService(target, training, runs_root=tmp_path)
  with pytest.raises(ValueError, match="成功"):
    await service.export_release(key, source_environment="development", reviewed_by="reviewer",
                                 output=tmp_path / "release", reserve_bytes=0)
  target.register_model.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_source_exports_reviewed_package_without_registering(artifact, tmp_path):
  manifest = artifact.manifest
  key = hashlib.sha256(b"next-day-selection\0v1\0final-run").hexdigest()
  row = SimpleNamespace(run_id="final-run", run_key=key, run_kind="FINAL_EVALUATION",
                        status="SUCCEEDED", spec_id="final-spec", parent_run_id="development-run",
                        artifact_manifest_sha256=artifact.manifest_sha256)
  parent = SimpleNamespace(run_kind="DEVELOPMENT", status="SUCCEEDED", spec_id="development-spec")
  final_spec = SimpleNamespace(run_kind="FINAL_EVALUATION", **{
    name: manifest[name] for name in ("spec_hash", "coordinate_hash", "requested_backend", "resolved_backend")
  })
  training = SimpleNamespace(
    get_run_by_run_key=AsyncMock(return_value=row), get_run=AsyncMock(return_value=parent),
    get_spec=AsyncMock(side_effect=lambda value: final_spec if value == "final-spec"
                      else SimpleNamespace(coordinate_hash=manifest["coordinate_hash"])),
  )
  target = SimpleNamespace(register_model=AsyncMock())
  service = StockSelectionModelService(target, training, runs_root=tmp_path)
  release = await service.export_release(key, source_environment="development", reviewed_by="reviewer",
                                        output=tmp_path / "release", reserve_bytes=0)
  assert release.review.run_key == key
  assert release.review.manifest_sha256 == artifact.manifest_sha256
  target.register_model.assert_not_awaited()
