"""Portable, reviewed model packages; no source database rows cross this boundary."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from quantx_contracts.training_bundle import BundleFile, TrainingBundle

from quantx_infrastructure.services.stock_selection_artifacts import (
  SelectionArtifactBundle,
  file_sha256,
  load_selection_artifact,
)
from quantx_infrastructure.training_bundle_store import publication_lock, reject_links, verify_bundle


class ReleaseReview(BaseModel):
  model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
  schema_version: Literal[1]
  source_environment: Literal["development"]
  decision: Literal["APPROVED_FOR_IMPORT"]
  reviewed_by: str = Field(min_length=1, max_length=64)
  reviewed_at: str
  run_key: str = Field(pattern=r"^[a-f0-9]{64}$")
  manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

  @field_validator("schema_version", mode="before")
  @classmethod
  def exact_version(cls, value):
    if type(value) is not int or value != 1:
      raise ValueError("RELEASE_REVIEW_VERSION_INVALID")
    return value

  @field_validator("reviewed_by")
  @classmethod
  def bounded_identity(cls, value):
    if value != value.strip() or any(ord(c) < 32 or c in "/\\" for c in value):
      raise ValueError("RELEASE_REVIEW_IDENTITY_INVALID")
    return value

  @field_validator("reviewed_at")
  @classmethod
  def utc_timestamp(cls, value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
      raise ValueError("RELEASE_REVIEW_TIME_INVALID")
    return value


@dataclass(frozen=True)
class VerifiedRelease:
  inventory: TrainingBundle
  review: ReleaseReview
  artifact: SelectionArtifactBundle
  cpu_runtime: dict


def _read(path: Path, limit: int):
  reject_links(path)
  before = path.stat()
  if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
    raise ValueError("RELEASE_JSON_FILE_INVALID")
  with path.open("rb") as stream:
    opened = os.fstat(stream.fileno())
    if (opened.st_dev, opened.st_ino, opened.st_nlink) != (before.st_dev, before.st_ino, 1):
      raise ValueError("RELEASE_JSON_FILE_CHANGED")
    data = stream.read(limit + 1)
  if len(data) > limit:
    raise ValueError("RELEASE_JSON_TOO_LARGE")
  return data


def _json(path: Path, limit: int):
  return json.loads(_read(path, limit))


def _eligible(bundle: SelectionArtifactBundle, run_key: str) -> None:
  manifest = bundle.manifest
  expected = hashlib.sha256(
    f"next-day-selection\0v1\0{manifest['run_id']}".encode()
  ).hexdigest()
  if (
    run_key != expected
    or manifest.get("registerable") is not True
    or bundle.metrics.get("registerable") is not True
    or manifest.get("conclusion") not in {"SHADOW_ELIGIBLE", "ACTIVE_ELIGIBLE"}
    or manifest.get("parent_run_id") != bundle.metrics["parent_development"]["run_id"]
  ):
    raise ValueError("RELEASE_FINAL_EVALUATION_NOT_ELIGIBLE")
  # Publishing requires actual immutable source identity, beyond a model header.
  indexed = {entry["path"] for entry in manifest["artifacts"]}
  if "source-evidence.json" not in indexed:
    raise ValueError("RELEASE_SOURCE_EVIDENCE_MISSING")
  source = _json(bundle.directory / "source-evidence.json", 8192)
  if (
    not isinstance(source, dict)
    or type(source.get("schema_version")) is not int
    or source.get("schema_version") != 1
    or source.get("source") not in {"git", "trainer-package"}
    or source.get("dirty") is not False
    or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", str(source.get("commit")))
    or (
      source["source"] == "trainer-package"
      and not re.fullmatch(r"[a-f0-9]{64}", str(source.get("code_manifest_sha256")))
    )
  ):
    raise ValueError("RELEASE_SOURCE_EVIDENCE_INVALID")
  import yaml

  config = yaml.safe_load(_read(bundle.directory / "resolved-config.yaml", 512 * 1024))
  environment = manifest.get("environment")
  dependencies = environment.get("dependencies") if isinstance(environment, dict) else None
  if (
    not isinstance(config, dict) or type(config.get("random_seed")) is not int
    or not isinstance(dependencies, dict)
    or any(not isinstance(dependencies.get(name), str) or not dependencies[name]
           for name in ("lightgbm", "numpy", "pandas"))
  ):
    raise ValueError("RELEASE_REPRODUCIBILITY_EVIDENCE_MISSING")


def verify_cpu_runtime(bundle: SelectionArtifactBundle) -> dict:
  """Load both real model families and calibrators using one CPU thread."""
  import lightgbm as lgb
  import numpy as np
  from quantx_domain.selection_model import apply_calibrator, predict_logistic

  columns = bundle.preprocessing["families"]["LIGHTGBM"]["feature_columns"]
  matrix = np.zeros((1, len(columns)), dtype=float)
  try:
    # Loading a text model does not construct a training learner; prediction
    # uses the native CPU predictor, with an explicit one-thread budget.
    booster = lgb.Booster(model_file=str(bundle.lightgbm_path))
    if booster.num_feature() != len(columns):
      raise ValueError("MODEL_FEATURE_COUNT_MISMATCH")
    tree_probability = np.asarray(booster.predict(matrix, num_threads=1), dtype=float)
    clipped = np.clip(tree_probability, 1e-8, 1 - 1e-8)
    tree_raw = np.log(clipped / (1 - clipped))
    linear_raw, linear_probability = predict_logistic(matrix, bundle.logistic)
    probabilities = {}
    for family, raw, probability in (
      ("LOGISTIC", linear_raw, linear_probability),
      ("LIGHTGBM", tree_raw, tree_probability),
    ):
      calibrated = apply_calibrator(raw, probability, bundle.calibrators["families"][family])
      if probability.shape != (1,) or calibrated.shape != (1,):
        raise ValueError("MODEL_OUTPUT_SHAPE_INVALID")
      if not all(math.isfinite(float(v)) and 0 <= v <= 1 for v in (*probability, *calibrated)):
        raise ValueError("MODEL_OUTPUT_INVALID")
      probabilities[family] = float(calibrated[0])
  except Exception:
    raise ValueError("RELEASE_CPU_RUNTIME_INCOMPATIBLE") from None
  return {
    "status": "PASSED",
    "device_type": "cpu",
    "num_threads": 1,
    "feature_count": len(columns),
    "python_version": sys.version.split()[0],
    "lightgbm_version": lgb.__version__,
    "prediction_sha256": hashlib.sha256(
      json.dumps(probabilities, sort_keys=True).encode()
    ).hexdigest(),
  }


def verify_release(directory: Path, *, expected_bundle_id: str) -> VerifiedRelease:
  """Verify the pinned inventory, manual review, model contracts and local CPU runtime."""
  reject_links(directory)
  inventory = TrainingBundle.model_validate(_json(directory / "bundle.json", 4 * 1024 * 1024))
  if inventory.kind != "RELEASE" or inventory.bundle_id != expected_bundle_id:
    raise ValueError("RELEASE_INVENTORY_IDENTITY_MISMATCH")
  payload = verify_bundle(directory / "payload", inventory)
  review = ReleaseReview.model_validate(_json(payload / "release.json", 8192))
  artifact = load_selection_artifact(
    payload / "runs" / inventory.source_id,
    expected_manifest_sha256=review.manifest_sha256,
  )
  expected_files = {"release.json", f"runs/{inventory.source_id}/manifest.json"}
  expected_files.update(f"runs/{inventory.source_id}/{item['path']}" for item in artifact.manifest["artifacts"])
  if expected_files != {entry.path for entry in inventory.files}:
    raise ValueError("RELEASE_UNEXPECTED_CONTENT")
  _eligible(artifact, review.run_key)
  return VerifiedRelease(inventory, review, artifact, verify_cpu_runtime(artifact))


def export_release(
  artifact: SelectionArtifactBundle,
  *,
  run_key: str,
  reviewed_by: str,
  output: Path,
  reserve_bytes: int,
) -> VerifiedRelease:
  """Export only after the caller verifies the successful source database lineage."""
  if type(reserve_bytes) is not int or reserve_bytes < 0:
    raise ValueError("RELEASE_DISK_RESERVE_INVALID")
  source = artifact.directory.absolute()
  output = output.absolute()
  if source == output or source in output.parents or output in source.parents:
    raise ValueError("RELEASE_PATHS_OVERLAP")
  reject_links(output)
  output.parent.mkdir(parents=True, exist_ok=True)
  with publication_lock(output.parent):
    if output.exists():
      raise FileExistsError("RELEASE_OUTPUT_EXISTS")
    fresh = load_selection_artifact(source, expected_manifest_sha256=artifact.manifest_sha256)
    _eligible(fresh, run_key)
    review = ReleaseReview(
      schema_version=1, source_environment="development", decision="APPROVED_FOR_IMPORT",
      reviewed_by=reviewed_by, reviewed_at=datetime.now(timezone.utc).isoformat(),
      run_key=run_key, manifest_sha256=fresh.manifest_sha256,
    )
    names = {"manifest.json", *(item["path"] for item in fresh.manifest["artifacts"])}
    entries = []
    for name in sorted(names):
      path = source / name
      reject_links(path)
      entries.append(BundleFile(path=f"runs/{fresh.manifest['run_id']}/{name}", size=path.stat().st_size, sha256=file_sha256(path)))
    review_bytes = review.model_dump_json().encode()
    entries.append(BundleFile(path="release.json", size=len(review_bytes), sha256=hashlib.sha256(review_bytes).hexdigest()))
    inventory = TrainingBundle(schema_version=1, kind="RELEASE", source_id=fresh.manifest["run_id"], files=tuple(entries))
    if shutil.disk_usage(output.parent).free < inventory.total_bytes + reserve_bytes:
      raise ValueError("RELEASE_DISK_RESERVE")
    with tempfile.TemporaryDirectory(prefix=".release-", dir=output.parent) as temporary:
      staging = Path(temporary) / "package"
      payload = staging / "payload"
      model = payload / "runs" / inventory.source_id
      model.mkdir(parents=True)
      for name in names:
        destination = model / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
      (payload / "release.json").write_bytes(review_bytes)
      (staging / "bundle.json").write_bytes(inventory.canonical_bytes())
      verify_release(staging, expected_bundle_id=inventory.bundle_id)
      staging.rename(output)
    return verify_release(output, expected_bundle_id=inventory.bundle_id)


def install_release(
  package: Path, *, expected_bundle_id: str, import_root: Path, reserve_bytes: int
) -> VerifiedRelease:
  """Install immutable verified files before the caller registers a candidate."""
  if type(reserve_bytes) is not int or reserve_bytes < 0:
    raise ValueError("RELEASE_DISK_RESERVE_INVALID")
  source, destination = package.absolute(), import_root.absolute()
  if source == destination or source in destination.parents or destination in source.parents:
    raise ValueError("RELEASE_PATHS_OVERLAP")
  verified = verify_release(source, expected_bundle_id=expected_bundle_id)
  reject_links(destination)
  destination.mkdir(parents=True, exist_ok=True)
  target = destination / verified.inventory.bundle_id
  lock = destination / ".locks" / verified.inventory.bundle_id
  reject_links(lock)
  lock.mkdir(parents=True, exist_ok=True)
  with publication_lock(lock):
    reject_links(target)
    if target.exists():
      return verify_release(target, expected_bundle_id=expected_bundle_id)
    if shutil.disk_usage(destination).free < verified.inventory.total_bytes + reserve_bytes:
      raise ValueError("RELEASE_DISK_RESERVE")
    with tempfile.TemporaryDirectory(prefix=".release-import-", dir=destination) as temporary:
      staging = Path(temporary) / "package"
      staging.mkdir()
      for entry in verified.inventory.files:
        origin = source / "payload" / entry.path
        reject_links(origin)
        copied = staging / "payload" / entry.path
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, copied)
      (staging / "bundle.json").write_bytes(verified.inventory.canonical_bytes())
      verify_release(staging, expected_bundle_id=expected_bundle_id)
      staging.rename(target)
    return verify_release(target, expected_bundle_id=expected_bundle_id)
