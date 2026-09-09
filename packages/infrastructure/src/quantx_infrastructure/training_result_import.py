"""Materialize a completed, database-bound result into the API artifact root."""

import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

from quantx_contracts.training_bundle import TrainingBundle

from quantx_infrastructure.training_bundle_store import (
  materialize_bundle,
  publication_lock,
  reject_links,
  verify_bundle,
)


def import_training_result(
  row,
  source,
  *,
  runs_root: Path,
  cache_root: Path,
  reserve_bytes: int,
  cancel: threading.Event | None = None,
) -> Path:
  """Copy verified files, never links; only a new complete run becomes visible."""
  if type(reserve_bytes) is not int or reserve_bytes < 0:
    raise ValueError("RESULT_IMPORT_RESERVE_INVALID")
  bundle = TrainingBundle.model_validate(row.artifact_bundle)
  if (
    row.status != "SUCCEEDED"
    or bundle.kind != "RESULT"
    or bundle.source_id != row.run_id
  ):
    raise ValueError("RESULT_IMPORT_IDENTITY_INVALID")
  manifest_entry = next(
    (item for item in bundle.files if item.path == "manifest.json"), None
  )
  if manifest_entry is None or manifest_entry.sha256 != row.artifact_manifest_sha256:
    raise ValueError("RESULT_IMPORT_MANIFEST_MISMATCH")
  expected_key = hashlib.sha256(
    f"next-day-selection\0v1\0{row.run_id}".encode()
  ).hexdigest()
  if row.run_key != expected_key or row.run_kind not in {
    "DEVELOPMENT",
    "FINAL_EVALUATION",
  }:
    raise ValueError("RESULT_IMPORT_RUN_KEY_INVALID")
  runs_root, cache_root = runs_root.absolute(), cache_root.absolute()
  if (
    runs_root == cache_root
    or runs_root in cache_root.parents
    or cache_root in runs_root.parents
  ):
    raise ValueError("RESULT_IMPORT_ROOTS_OVERLAP")
  for root in (runs_root, cache_root):
    reject_links(root)
    root.mkdir(parents=True, exist_ok=True)
  target = runs_root / bundle.source_id
  # Serialize retries and different purported bundles for the same run.
  lock_root = cache_root / "locks" / bundle.source_id
  reject_links(lock_root)
  lock_root.mkdir(parents=True, exist_ok=True)
  with publication_lock(lock_root):
    reject_links(target)
    existing = target.exists()
    cached = (
      verify_bundle(target, bundle, cancel=cancel)
      if existing
      else materialize_bundle(
        source,
        bundle,
        cache_root / "bundles",
        reserve_bytes=reserve_bytes,
        cancel=cancel,
      )
    )
    with (cached / "manifest.json").open("rb") as stream:
      raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
      raise ValueError("RESULT_IMPORT_MANIFEST_TOO_LARGE")
    manifest = json.loads(raw)
    if (
      not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
    ):
      raise ValueError("RESULT_IMPORT_CONTENT_IDENTITY_INVALID")
    if any(
      manifest.get(key) != value
      for key, value in {
        "schema_version": 2,
        "study_id": "next-day-selection",
        "version": "v1",
        "run_id": row.run_id,
        "run_kind": row.run_kind,
        "status": "SUCCEEDED",
      }.items()
    ):
      raise ValueError("RESULT_IMPORT_CONTENT_IDENTITY_INVALID")
    if existing:
      return target
    required = sum(item.size for item in bundle.files) + reserve_bytes
    if shutil.disk_usage(runs_root).free < required:
      raise ValueError("RESULT_IMPORT_DISK_RESERVE")
    with tempfile.TemporaryDirectory(prefix=".result-import-", dir=runs_root) as tmp:
      staging = Path(tmp) / "run"
      staging.mkdir()
      for entry in bundle.files:
        if cancel is not None and cancel.is_set():
          raise ValueError("RESULT_IMPORT_CANCELLED")
        destination = staging / entry.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (
          (cached / entry.path).open("rb") as content,
          destination.open("xb") as output,
        ):
          copied = 0
          while chunk := content.read(1024 * 1024):
            copied += len(chunk)
            if copied > entry.size:
              raise ValueError("RESULT_IMPORT_SOURCE_CHANGED")
            if cancel is not None and cancel.is_set():
              raise ValueError("RESULT_IMPORT_CANCELLED")
            output.write(chunk)
          output.flush()
          os.fsync(output.fileno())
      verify_bundle(staging, bundle, cancel=cancel)
      if target.exists() or target.is_symlink():
        raise ValueError("RESULT_IMPORT_TARGET_EXISTS")
      os.rename(staging, target)
    return target
