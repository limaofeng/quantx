import hashlib
import json
import threading
from types import SimpleNamespace

import pytest
from quantx_contracts.training_bundle import TrainingBundle
from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  DirectoryBundleReader,
)
from quantx_infrastructure.training_result_import import import_training_result


@pytest.fixture
def result(tmp_path):
  manifest = json.dumps(
    {
      "schema_version": 2,
      "study_id": "next-day-selection",
      "version": "v1",
      "run_id": "run-1",
      "run_kind": "DEVELOPMENT",
      "status": "SUCCEEDED",
    }
  ).encode()
  files = {"manifest.json": manifest, "metrics.json": b'{"value": 1}'}
  bundle = TrainingBundle(
    schema_version=1,
    kind="RESULT",
    source_id="run-1",
    files=tuple(
      {
        "path": name,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
      }
      for name, content in files.items()
    ),
  )
  source = tmp_path / "remote" / bundle.bundle_id
  source.mkdir(parents=True)
  for name, content in files.items():
    (source / name).write_bytes(content)
  row = SimpleNamespace(
    run_id="run-1",
    run_kind="DEVELOPMENT",
    status="SUCCEEDED",
    run_key=hashlib.sha256(b"next-day-selection\0v1\0run-1").hexdigest(),
    artifact_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
    artifact_bundle=bundle.model_dump(mode="json"),
  )
  return row, DirectoryBundleReader(source.parent), source


def run_import(tmp_path, result, **kwargs):
  row, reader, _ = result
  return import_training_result(
    row,
    reader,
    runs_root=tmp_path / "api-runs",
    cache_root=tmp_path / "cache",
    reserve_bytes=0,
    **kwargs,
  )


def test_real_bundle_is_exposed_under_run_id_and_retry_preserves_files(
  tmp_path, result
):
  path = run_import(tmp_path, result)
  assert path == tmp_path / "api-runs/run-1"
  assert json.loads((path / "manifest.json").read_text())["run_id"] == "run-1"
  original = (path / "metrics.json").stat().st_mtime_ns
  assert run_import(tmp_path, result) == path
  assert (path / "metrics.json").stat().st_mtime_ns == original
  (path / "metrics.json").write_text("changed")
  with pytest.raises(BundleTransferError):
    run_import(tmp_path, result)
  assert (path / "metrics.json").read_text() == "changed"


@pytest.mark.parametrize(
  "field,value",
  [
    ("status", "RUNNING"),
    ("run_key", "a" * 64),
    ("artifact_manifest_sha256", "b" * 64),
  ],
)
def test_database_identity_mismatch_cannot_create_api_run(
  tmp_path, result, field, value
):
  setattr(result[0], field, value)
  with pytest.raises(ValueError):
    run_import(tmp_path, result)
  assert not (tmp_path / "api-runs/run-1").exists()


def test_corrupt_remote_result_is_not_exposed(tmp_path, result):
  (result[2] / "metrics.json").write_text("bad")
  with pytest.raises(BundleTransferError):
    run_import(tmp_path, result)
  assert not (tmp_path / "api-runs/run-1").exists()


def test_cancelled_import_leaves_no_visible_run(tmp_path, result):
  cancel = threading.Event()
  cancel.set()
  with pytest.raises((ValueError, BundleTransferError)):
    run_import(tmp_path, result, cancel=cancel)
  assert not (tmp_path / "api-runs/run-1").exists()


def test_hash_consistent_bundle_with_wrong_manifest_identity_is_rejected(
  tmp_path, result
):
  row, _, source = result
  payload = json.loads((source / "manifest.json").read_text())
  payload["run_kind"] = "FINAL_EVALUATION"
  content = json.dumps(payload).encode()
  (source / "manifest.json").write_bytes(content)
  bundle = dict(row.artifact_bundle)
  bundle["files"] = [
    {**item, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    if item["path"] == "manifest.json"
    else item
    for item in bundle["files"]
  ]
  validated = TrainingBundle.model_validate(bundle)
  source.rename(source.parent / validated.bundle_id)
  row.artifact_bundle = validated.model_dump(mode="json")
  row.artifact_manifest_sha256 = hashlib.sha256(content).hexdigest()
  with pytest.raises(ValueError, match="CONTENT_IDENTITY"):
    run_import(tmp_path, result)
  assert not (tmp_path / "api-runs/run-1").exists()
