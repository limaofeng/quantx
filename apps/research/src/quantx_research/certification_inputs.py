"""Portable, version-bound inputs for certification without control-plane reads."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import yaml

from quantx_research.data.frozen_source import (
  FrozenResearchDataSource,
  _digest,
  _json,
  _no_links,
  export_frozen_source,
)
from quantx_research.indicator_runner import indicator_source_start
from quantx_research.next_day_selection_config import (
  NextDaySelectionConfig,
  load_next_day_selection_config,
)
from quantx_research.next_day_selection_dataset import (
  certify_next_day_selection_dataset,
  safe_dataset_version,
)
from quantx_research.next_day_selection_training import certification_study_config

_HISTORY = (
  "historical_st_membership_path",
  "historical_industry_membership_path",
  "historical_delisting_status_path",
)


async def export_certification_inputs(
  config_path, source, calendar, directory, *, dataset_version
):
  """Freeze bytes and input scope; caller owns the read-only source transaction."""
  dataset_version = safe_dataset_version(dataset_version)
  config = load_next_day_selection_config(config_path)
  if (
    config.data.market_data_archive is not None
    or config.data.verified_panel_path is not None
  ):
    raise ValueError("Certification export requires an explicit source")
  directory = Path(directory).absolute()
  _no_links(directory)
  if directory.exists():
    raise FileExistsError(directory)
  directory.parent.mkdir(parents=True, exist_ok=True)
  staging = Path(tempfile.mkdtemp(prefix=".certification-", dir=directory.parent))
  try:
    payload = config.model_dump(mode="json")
    for key in _HISTORY:
      original = getattr(config.data, key)
      if original is None:
        raise ValueError("Certification export requires all historical evidence")
      original = Path(original)
      _no_links(original)
      if original.suffix.lower() not in {".parquet", ".csv", ".tsv"}:
        raise ValueError("Unsupported historical evidence format")
      frozen = staging / f"{key}{original.suffix.lower()}"
      before = _digest(original)
      shutil.copyfile(original, frozen)
      if _digest(frozen) != before or _digest(original) != before:
        raise ValueError("Historical evidence changed during export")
      payload["data"][key] = frozen.name
    _json(staging / "config.json", payload)
    end = await calendar.get_next_trading_date("SH", config.data.date_range[1])
    if end <= config.data.date_range[1]:
      raise ValueError("Certification requires a strictly later label session")
    study = certification_study_config(config, end)
    start = indicator_source_start(study, config.data.date_range[0])
    await export_frozen_source(
      source,
      calendar,
      staging / "source",
      start=start,
      end=end,
      batch_size=config.runtime.batch_size,
    )
    files = {
      path.relative_to(staging).as_posix(): {
        "size": path.stat().st_size,
        "sha256": _digest(path),
      }
      for path in sorted(staging.rglob("*"))
      if path.is_file()
    }
    _json(
      staging / "manifest.json",
      {
        "schema_version": 1,
        "kind": "certification-inputs",
        "dataset_version": dataset_version,
        "files": files,
      },
    )
    digest = _digest(staging / "manifest.json")
    load_certification_inputs(
      staging, dataset_version=dataset_version, manifest_sha256=digest
    )
    if directory.exists():
      raise FileExistsError(directory)
    staging.rename(directory)
    return digest
  finally:
    if staging.exists():
      shutil.rmtree(staging)


def load_certification_inputs(directory, *, dataset_version, manifest_sha256):
  """Validate the expected identity and return a relocated config plus file source."""
  directory = Path(directory).absolute()
  _no_links(directory)
  manifest_path = directory / "manifest.json"
  _no_links(manifest_path)
  raw = manifest_path.read_bytes()
  if hashlib.sha256(raw).hexdigest() != manifest_sha256:
    raise ValueError("Certification input manifest hash mismatch")
  manifest = json.loads(raw)
  if (
    manifest.get("schema_version") != 1
    or manifest.get("kind") != "certification-inputs"
    or manifest.get("dataset_version") != safe_dataset_version(dataset_version)
  ):
    raise ValueError("Certification input identity mismatch")
  expected = manifest["files"]
  for name, evidence in expected.items():
    relative = Path(name)
    if (
      relative.is_absolute()
      or ".." in relative.parts
      or "\\" in name
      or relative.as_posix() != name
    ):
      raise ValueError("Invalid certification input path")
    path = directory / relative
    _no_links(path)
    if (
      not path.is_file()
      or path.stat().st_size != evidence["size"]
      or _digest(path) != evidence["sha256"]
    ):
      raise ValueError("Certification input file integrity mismatch")
  actual = set()
  for path in directory.rglob("*"):
    _no_links(path)
    if path.is_file():
      actual.add(path.relative_to(directory).as_posix())
  if actual != set(expected) | {"manifest.json"}:
    raise ValueError("Unexpected certification input files")
  payload = json.loads((directory / "config.json").read_text(encoding="utf-8"))
  history_names = set()
  for key in _HISTORY:
    name = payload["data"].get(key)
    if (
      name not in {f"{key}.csv", f"{key}.parquet", f"{key}.tsv"} or name not in expected
    ):
      raise ValueError("Historical evidence must be bound to frozen input files")
    history_names.add(name)
    payload["data"][key] = str(directory / name)
  config = NextDaySelectionConfig.model_validate(payload)
  if (
    config.data.market_data_archive is not None
    or config.data.verified_panel_path is not None
  ):
    raise ValueError("Frozen certification cannot use external paths")
  frozen = FrozenResearchDataSource(directory / "source")
  allowed = {"config.json", *history_names, "source/manifest.json"}
  allowed.update(f"source/{name}" for name in frozen.manifest["files"])
  if set(expected) != allowed:
    raise ValueError("Unexpected certification input inventory")
  return config, frozen, manifest


async def certify_frozen_inputs(
  directory, *, dataset_version, manifest_sha256, work_directory, output_root
):
  config, source, manifest = load_certification_inputs(
    directory, dataset_version=dataset_version, manifest_sha256=manifest_sha256
  )
  work_directory = Path(work_directory).absolute()
  _no_links(work_directory)
  work_directory.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(
    prefix="certification-", dir=work_directory
  ) as attempt:
    payload = config.model_dump(mode="json")
    for key in _HISTORY:
      original = getattr(config.data, key)
      copied = Path(attempt) / original.name
      _no_links(original)
      shutil.copyfile(original, copied)
      if _digest(copied) != manifest["files"][original.name]["sha256"]:
        raise ValueError("Historical evidence changed before certification")
      payload["data"][key] = str(copied)
    config_path = Path(attempt) / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return await certify_next_day_selection_dataset(
      config_path,
      dataset_version=dataset_version,
      output_root=output_root,
      source=source,
      calendar=source,
    )
