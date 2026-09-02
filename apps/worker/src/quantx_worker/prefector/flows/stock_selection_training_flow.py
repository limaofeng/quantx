"""Minute-level dispatcher for isolated next-day selection training jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from prefect import flow, get_run_logger
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

SHANGHAI = ZoneInfo("Asia/Shanghai")
CRITICAL_WINDOW_START = time(9, 15)
CRITICAL_WINDOW_END = time(16, 30)
RESEARCH_DATASETS_ENV = "QUANTX_RESEARCH_DATASETS_ROOT"
RESEARCH_RUNS_ENV = "QUANTX_RESEARCH_RUNS_ROOT"
CONTROL_DIRECTORY_NAME = "stock-selection-training-control"
_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
_processes: dict[str, Any] = {}


def _repo_root() -> Path:
  configured = os.environ.get("QUANTX_ROOT", "").strip()
  return Path(configured).expanduser().absolute() if configured else Path(__file__).resolve().parents[6]


def research_datasets_root() -> Path:
  configured = os.environ.get(RESEARCH_DATASETS_ENV, "").strip()
  root = Path(configured).expanduser() if configured else _repo_root() / ".runtime" / "research-datasets"
  return root.absolute()


def research_runs_root() -> Path:
  """Return the private Research artifact root used by isolated jobs."""

  configured = os.environ.get(RESEARCH_RUNS_ENV, "").strip()
  root = Path(configured).expanduser() if configured else _repo_root() / ".runtime" / "research-runs"
  return root.absolute()


def control_root() -> Path:
  return (_repo_root() / ".runtime" / CONTROL_DIRECTORY_NAME).absolute()


def _now() -> datetime:
  return datetime.now(timezone.utc)


def _aware_shanghai(value: datetime | None) -> datetime:
  current = value or datetime.now(timezone.utc)
  if current.tzinfo is None:
    current = current.replace(tzinfo=timezone.utc)
  return current.astimezone(SHANGHAI)


async def _maybe_await(value: Any) -> Any:
  return await value if hasattr(value, "__await__") else value


def _research_cli_command() -> list[str]:
  """Locate the installed Research protocol executable without importing it."""

  executable = shutil.which("quantx-research")
  if not executable:
    raise RuntimeError("Research protocol executable is unavailable")
  return [executable]


def _probe_capability() -> dict[str, Any]:
  """Read the Research GPU capability protocol through an isolated process."""

  unavailable = {
    "status": "GPU_UNAVAILABLE_RUNTIME",
    "qualification": {
      "status": "GPU_UNAVAILABLE_RUNTIME",
      "acceleration": None,
      "minimum_sample_count": None,
      "peak_memory_fraction": None,
      "gates_passed": False,
      "evidence_sha256": None,
    },
    "available_memory_mib": None,
  }
  try:
    completed = subprocess.run(
      [*_research_cli_command(), "probe-lightgbm-gpu", "--json"],
      check=False,
      capture_output=True,
      text=True,
      timeout=10,
      encoding="utf-8",
      errors="replace",
    )
  except (OSError, subprocess.SubprocessError):
    return unavailable
  if completed.returncode != 0:
    return unavailable
  try:
    value = json.loads(completed.stdout)
  except (TypeError, ValueError, json.JSONDecodeError):
    return unavailable
  return dict(value) if isinstance(value, Mapping) else unavailable


def _full_live_runtime() -> bool:
  profile = os.environ.get("RUNTIME_PROFILE", "")
  mode = os.environ.get("QMT_AGENT_MODE", "")
  return profile.strip().lower() == "full" and mode.strip().lower() == "live"


async def is_critical_trading_window(
  now: datetime | None = None,
  *,
  trading_dates: Any | None = None,
) -> bool:
  """Return true only for an actual Shanghai trading day in 09:15–16:30."""

  local = _aware_shanghai(now)
  if not (CRITICAL_WINDOW_START <= local.time() <= CRITICAL_WINDOW_END):
    return False
  helper = trading_dates or TradingDateHelper()
  return bool(await _maybe_await(helper.is_trading_date("SH", local.date())))


def _safe_relative_key(value: Any) -> str:
  text = str(value or "").strip().replace("\\", "/")
  parts = text.split("/")
  if (
    not text
    or not _SAFE_KEY.fullmatch(text)
    or text.startswith("/")
    or ":" in text
    or any(part in {"", ".", ".."} for part in parts)
  ):
    raise ValueError("dataset source_reference is not a safe relative key")
  return text


def _within(root: Path, candidate: Path) -> bool:
  try:
    candidate.resolve().relative_to(root.resolve())
    return True
  except ValueError:
    return False


def _reject_symlink_components(root: Path, candidate: Path) -> None:
  if _is_link_or_junction(root):
    raise ValueError("dataset source contains a symlink or junction")
  current = root
  try:
    relative = candidate.relative_to(root)
  except ValueError as exc:
    raise ValueError("dataset path escapes research root") from exc
  for part in relative.parts:
    current = current / part
    if _is_link_or_junction(current):
      raise ValueError("dataset source contains a symlink or junction")


def _is_link_or_junction(path: Path) -> bool:
  """Reject Windows reparse-point junctions as well as ordinary links."""

  if path.is_symlink() or os.path.islink(str(path)):
    return True
  is_junction = getattr(path, "is_junction", None)
  if callable(is_junction):
    try:
      return bool(is_junction())
    except OSError as exc:
      raise ValueError("path link type cannot be verified") from exc
  return False


def _sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def _json_read(path: Path) -> dict[str, Any]:
  try:
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
  except (OSError, UnicodeError, ValueError) as exc:
    raise ValueError(f"invalid dataset or training JSON: {path.name}") from exc
  if not isinstance(value, dict):
    raise ValueError(f"JSON object required: {path.name}")
  return value


def _reject_json_constant(value: str) -> None:
  raise ValueError(f"invalid JSON constant: {value}")


def _manifest_evidence_sha256(manifest: Mapping[str, Any]) -> str:
  evidence = dict(manifest)
  evidence.pop("manifest_sha256", None)
  payload = json.dumps(
    _to_json(evidence),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  )
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_dataset_directory(dataset: Any, *, root: Path | None = None) -> dict[str, Any]:
  """Resolve and verify a certified dataset without trusting DB paths."""

  research_root = (root or research_datasets_root()).absolute()
  dataset_status = _value(dataset, "status")
  if dataset_status is None or str(dataset_status).upper() != "CERTIFIED":
    raise ValueError("training dataset is not CERTIFIED")
  dataset_source_kind = _value(dataset, "source_kind")
  if dataset_source_kind is None or dataset_source_kind != "VERIFIED_PANEL":
    raise ValueError("training dataset source_kind evidence is invalid")
  reference = _safe_relative_key(
    dataset.get("source_reference") if isinstance(dataset, Mapping) else getattr(dataset, "source_reference", "")
  )
  candidate = research_root / Path(reference)
  if not _within(research_root, candidate):
    raise ValueError("dataset source_reference escapes research root")
  _reject_symlink_components(research_root, candidate)
  if not candidate.is_dir():
    raise ValueError("certified dataset directory is missing")
  manifest_path = candidate / "manifest.json"
  if not manifest_path.is_file():
    raise ValueError("certified dataset manifest is missing")
  _reject_symlink_components(research_root, manifest_path)
  manifest = _json_read(manifest_path)
  dataset_version = str(
    dataset.get("dataset_version") if isinstance(dataset, Mapping) else getattr(dataset, "dataset_version", "")
    or ""
  ).strip()
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", dataset_version):
    raise ValueError("certified dataset_version is not safe")
  dataset_reference = str(_value(dataset, "source_reference", "") or "").strip().replace("\\", "/")
  if dataset_reference != str(manifest.get("source_reference") or ""):
    raise ValueError("certified dataset source_reference does not match its manifest")
  expected = str(
    dataset.get("manifest_sha256") if isinstance(dataset, Mapping) else getattr(dataset, "manifest_sha256", "")
    or ""
  ).lower()
  declared = str(manifest.get("manifest_sha256") or "").lower()
  if not _HEX_RE.fullmatch(expected) or declared != expected:
    raise ValueError("certified dataset manifest SHA-256 mismatch")
  if _manifest_evidence_sha256(manifest) != expected:
    raise ValueError("certified dataset manifest evidence hash mismatch")
  if (
    manifest.get("status") != "CERTIFIED"
    or str(manifest.get("dataset_version") or "") != dataset_version
    or manifest.get("source_kind") != "VERIFIED_PANEL"
    or manifest.get("source_reference") != dataset_version
  ):
    raise ValueError("certified dataset manifest identity is invalid")
  panel_name = str(manifest.get("panel_path") or "")
  if panel_name != "training-panel.parquet":
    raise ValueError("certified dataset panel path is invalid")
  panel_path = candidate / panel_name
  if not _within(candidate, panel_path):
    raise ValueError("certified dataset panel escapes its directory")
  _reject_symlink_components(research_root, panel_path)
  if not panel_path.is_file():
    raise ValueError("certified dataset panel is missing")
  panel_hash = _sha256_file(panel_path)
  files = manifest.get("files")
  if not isinstance(files, Mapping):
    raise ValueError("certified dataset file evidence is missing")
  file_evidence = files.get(panel_name)
  if not isinstance(file_evidence, Mapping):
    raise ValueError("certified dataset panel evidence is missing")
  panel_bytes = panel_path.stat().st_size
  try:
    declared_panel_bytes = int(manifest.get("training_panel_bytes", -1))
    file_panel_bytes = int(file_evidence.get("bytes", -1))
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("certified dataset panel byte evidence is invalid") from exc
  declared_panel_hash = str(manifest.get("training_panel_sha256") or "").lower()
  file_panel_hash = str(file_evidence.get("sha256") or "").lower()
  if (
    not _HEX_RE.fullmatch(declared_panel_hash)
    or not _HEX_RE.fullmatch(file_panel_hash)
    or panel_hash != declared_panel_hash
    or panel_hash != file_panel_hash
    or panel_bytes != declared_panel_bytes
    or panel_bytes != file_panel_bytes
  ):
    raise ValueError("certified dataset panel evidence mismatch")
  quality_path = candidate / "data-quality.json"
  if not quality_path.is_file():
    raise ValueError("certified dataset quality evidence is missing")
  _reject_symlink_components(research_root, quality_path)
  quality = _json_read(quality_path)
  manifest_quality = manifest.get("quality")
  if not isinstance(manifest_quality, Mapping):
    raise ValueError("certified dataset quality evidence is missing")
  if quality.get("sample_count") != manifest_quality.get("sample_count"):
    raise ValueError("certified dataset quality evidence mismatch")
  quality_path_name = manifest.get("quality_path")
  if quality_path_name != quality_path.name:
    raise ValueError("certified dataset quality path is invalid")
  quality_file_evidence = files.get(quality_path.name)
  if not isinstance(quality_file_evidence, Mapping):
    raise ValueError("certified dataset quality evidence is missing")
  quality_bytes = quality_path.stat().st_size
  quality_hash = _sha256_file(quality_path)
  try:
    declared_quality_bytes = int(manifest.get("quality_bytes", -1))
    file_quality_bytes = int(quality_file_evidence.get("bytes", -1))
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("certified dataset quality byte evidence is invalid") from exc
  declared_quality_hash = str(manifest.get("quality_sha256") or "").lower()
  file_quality_hash = str(quality_file_evidence.get("sha256") or "").lower()
  if (
    not _HEX_RE.fullmatch(declared_quality_hash)
    or not _HEX_RE.fullmatch(file_quality_hash)
    or quality_hash != declared_quality_hash
    or quality_hash != file_quality_hash
    or quality_bytes != declared_quality_bytes
    or quality_bytes != file_quality_bytes
  ):
    raise ValueError("certified dataset quality evidence mismatch")
  if _to_json(quality) != _to_json(manifest_quality):
    raise ValueError("certified dataset quality evidence mismatch")
  # Every immutable DB projection must be present in the manifest.  Missing
  # evidence is a hard failure rather than an invitation to trust one side.
  for field, manifest_field in (
    ("date_start", "date_start"),
    ("date_end", "date_end"),
    ("universe_spec", "universe_spec"),
    ("indicator_version", "indicator_version"),
    ("factor_set_version", "factor_set_version"),
    ("factor_set_hash", "factor_set_hash"),
    ("label_version", "label_version"),
    ("sample_count", "quality"),
    ("stock_count", "quality"),
    ("trading_day_count", "quality"),
    ("quality_summary", "quality"),
  ):
    db_value = _value(dataset, field)
    if db_value is None:
      raise ValueError(f"certified dataset {field} evidence is missing")
    if manifest_field not in manifest:
      raise ValueError(f"certified dataset {manifest_field} evidence is missing")
    manifest_value = manifest[manifest_field]
    if manifest_value is None:
      raise ValueError(f"certified dataset {manifest_field} evidence is missing")
    if field in {"sample_count", "stock_count", "trading_day_count"}:
      if not isinstance(manifest_value, Mapping) or field not in manifest_value:
        raise ValueError(f"certified dataset {field} evidence is missing")
      manifest_value = manifest_value[field]
    if field in {"date_start", "date_end"}:
      db_value = str(db_value)[:10]
      manifest_value = str(manifest_value)[:10]
    elif field in {"universe_spec", "quality_summary"}:
      db_value = _to_json(db_value)
      manifest_value = _to_json(manifest_value)
    if db_value != manifest_value:
      raise ValueError(f"certified dataset {field} evidence mismatch")
  return {
    "directory": candidate,
    "manifest_path": manifest_path,
    "manifest": manifest,
    "panel_path": panel_path,
    "manifest_sha256": declared,
    "manifest_file_sha256": _sha256_file(manifest_path),
  }


def find_research_run_directory(
  *,
  parent_run_id: str,
  expected_run_key: str,
  root: Path | None = None,
) -> Path:
  """Locate and verify one parent Research run below the configured root."""

  run_id = str(parent_run_id or "").strip()
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
    raise ValueError("parent Research run id is not safe")
  stable_key = str(expected_run_key or "").strip().lower()
  if not _HEX_RE.fullmatch(stable_key):
    raise ValueError("parent Research run_key is invalid")
  research_root = (root or research_runs_root()).absolute()
  candidate = research_root / run_id
  if not _within(research_root, candidate) or not candidate.is_dir():
    raise ValueError("parent Research run is not present below the configured root")
  _reject_symlink_components(research_root, candidate)
  manifest_path = candidate / "manifest.json"
  if not manifest_path.is_file():
    raise ValueError("parent Research run manifest is missing")
  _reject_symlink_components(research_root, manifest_path)
  manifest = _json_read(manifest_path)
  if (
    str(manifest.get("run_id") or "") != run_id
    or str(manifest.get("run_kind") or "") != "DEVELOPMENT"
    or str(manifest.get("status") or "") != "SUCCEEDED"
    or _stable_research_run_key(manifest) != stable_key
    or (
      manifest.get("run_key") is not None
      and str(manifest.get("run_key") or "").strip().lower() != stable_key
    )
  ):
    raise ValueError("parent Research run manifest identity does not match")
  return candidate


def _to_json(value: Any) -> Any:
  if isinstance(value, datetime):
    return value.isoformat()
  if isinstance(value, date):
    return value.isoformat()
  if isinstance(value, Mapping):
    return {str(key): _to_json(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_to_json(item) for item in value]
  if isinstance(value, float) and not math.isfinite(value):
    raise ValueError("JSON cannot contain NaN or Infinity")
  return value


def _reject_links(path: Path) -> None:
  absolute = Path(os.path.abspath(path))
  current = Path(absolute.anchor)
  for component in absolute.parts[1:]:
    current /= component
    if _is_link_or_junction(current):
      raise ValueError("training control path contains a symlink or junction")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
  _reject_links(path)
  temporary = path.with_name(f".{path.name}.partial")
  temporary.write_text(
    json.dumps(
      _to_json(value),
      ensure_ascii=False,
      sort_keys=True,
      separators=(",", ":"),
      allow_nan=False,
    ),
    encoding="utf-8",
  )
  temporary.replace(path)


def _write_cancel_request(path: Path) -> None:
  """Write the exact JSON cancellation contract consumed by Research."""

  _write_json(path, {"cancel": True})


def _value(item: Any, name: str, default: Any = None) -> Any:
  if isinstance(item, Mapping):
    return item.get(name, default)
  return getattr(item, name, default)


def _safe_public_details(value: Any) -> dict[str, Any]:
  if hasattr(value, "to_dict"):
    value = value.to_dict()
  if not isinstance(value, Mapping):
    value = {"status": str(value)}
  blocked = {
    "path",
    "root",
    "directory",
    "panel_path",
    "manifest_path",
    "instance_id",
    "device_serial",
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
  }

  def scrub(item: Any, key: str = "") -> Any:
    if key.lower() in blocked:
      return None
    if isinstance(item, Mapping):
      return {
        str(name): scrub(child, str(name))
        for name, child in item.items()
        if str(name).lower() not in blocked
      }
    if isinstance(item, (list, tuple, set)):
      return [scrub(child, key) for child in item]
    return item

  result = scrub(value)
  return result if isinstance(result, dict) else {"status": str(result)}


def _probe_details(probe: Any) -> tuple[str, dict[str, Any]]:
  details = _safe_public_details(probe)
  status = str(details.get("status") or "GPU_UNAVAILABLE_BUILD").upper()
  details.setdefault("status", status)
  raw_qualification = details.get("qualification")
  if isinstance(raw_qualification, Mapping):
    details["qualification"] = {
      "status": raw_qualification.get("status"),
      "acceleration": raw_qualification.get("acceleration"),
      "minimum_sample_count": raw_qualification.get("minimum_sample_count"),
      "peak_memory_fraction": raw_qualification.get("peak_memory_fraction"),
      "gates_passed": raw_qualification.get("gates_passed"),
      "evidence_sha256": raw_qualification.get("evidence_sha256"),
    }
  else:
    details["qualification"] = {
      "status": status,
      "acceleration": None,
      "minimum_sample_count": None,
      "peak_memory_fraction": None,
      "gates_passed": False,
      "evidence_sha256": None,
    }
  return status, details


def _control_directory(run_id: str) -> Path:
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(run_id)):
    raise ValueError("invalid training run id")
  directory = control_root() / str(run_id)
  _reject_links(directory)
  directory.mkdir(parents=True, exist_ok=True)
  _reject_links(directory)
  return directory


def _research_spec_payload(
  spec: Any,
  dataset: Any,
  output_root: Path,
  *,
  capability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
  """Project the DB snapshot into Research's strict nested config contract."""

  split_value = _value(spec, "split_spec")
  model_value = _value(spec, "model_spec")
  evaluation_value = _value(spec, "evaluation_spec")
  if not isinstance(split_value, Mapping):
    raise ValueError("training spec split_spec is missing")
  if not isinstance(model_value, Mapping):
    raise ValueError("training spec model_spec is missing")
  if not isinstance(evaluation_value, Mapping):
    raise ValueError("training spec evaluation_spec is missing")
  split = dict(split_value)
  model = dict(model_value)
  evaluation = dict(evaluation_value)
  raw_universe = _value(spec, "universe_spec")
  if not isinstance(raw_universe, Mapping):
    raise ValueError("training spec universe_spec is missing")
  universe = dict(raw_universe)
  split_required = {
    "minimum_training_months": 30,
    "calibration_months": 6,
    "validation_months": 1,
    "frozen_test_months": 12,
  }
  if not split.get("date_start") or not split.get("date_end"):
    raise ValueError("training spec split_spec must contain date_start and date_end")
  for field, expected in split_required.items():
    if field not in split:
      raise ValueError(f"training spec split_spec is missing {field}")
    try:
      actual = int(split[field])
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError(f"training spec split_spec.{field} is invalid") from exc
    if actual != expected:
      raise ValueError(f"training spec split_spec.{field} must be fixed at {expected}")
  date_start = split["date_start"]
  date_end = split["date_end"]

  required_universe = {
    "kind",
    "index_code",
    "benchmark_code",
    "stock_codes",
    "minimum_listing_days",
  }
  if set(universe) != required_universe:
    raise ValueError("training spec universe_spec must use the canonical fields")
  universe_kind = universe["kind"]
  if universe_kind not in {"ORDINARY_A_SHARE", "CERTIFIED_INDEX", "EXPLICIT"}:
    raise ValueError("training spec universe_spec.kind is invalid")
  index_code = universe.get("index_code")
  benchmark_code = universe["benchmark_code"]
  canonical_code = re.compile(r"^[0-9]{6}\.(?:SH|SZ)$")
  if not isinstance(benchmark_code, str) or not canonical_code.fullmatch(benchmark_code):
    raise ValueError("training spec benchmark_code is invalid")
  if index_code is not None and (
    not isinstance(index_code, str) or not canonical_code.fullmatch(index_code)
  ):
    raise ValueError("training spec index_code is invalid")
  if universe_kind == "CERTIFIED_INDEX":
    if not index_code or not re.fullmatch(r"[0-9]{6}\.(?:SH|SZ)", index_code):
      raise ValueError("CERTIFIED_INDEX training spec requires index_code")
    if index_code != benchmark_code:
      raise ValueError("CERTIFIED_INDEX index_code must equal benchmark_code")
  elif index_code is not None:
    raise ValueError("non-index training spec must not carry index_code")
  raw_stock_codes = universe.get("stock_codes")
  stock_codes = None
  if raw_stock_codes is not None:
    if not isinstance(raw_stock_codes, (list, tuple)) or not raw_stock_codes:
      raise ValueError("training spec stock_codes must be a non-empty sequence")
    stock_codes = tuple(raw_stock_codes)
    if any(not isinstance(code, str) or not canonical_code.fullmatch(code) for code in stock_codes):
      raise ValueError("training spec stock_codes are invalid")
    if len(set(stock_codes)) != len(stock_codes):
      raise ValueError("training spec stock_codes must not contain duplicates")
  if universe_kind == "CERTIFIED_INDEX" and stock_codes is not None:
    raise ValueError("CERTIFIED_INDEX training spec must not carry stock_codes")
  if universe_kind == "EXPLICIT" and stock_codes is None:
    raise ValueError("EXPLICIT training spec requires stock_codes")
  minimum_listing_days = universe["minimum_listing_days"]
  if isinstance(minimum_listing_days, bool):
    raise ValueError("training spec minimum_listing_days is invalid")
  try:
    minimum_listing_days = int(minimum_listing_days)
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("training spec minimum_listing_days is invalid") from exc
  if minimum_listing_days < 252:
    raise ValueError("training spec minimum_listing_days must be at least 252")
  model_sections = {
    "logistic": {
      "c_values", "max_iter",
    },
    "lightgbm": {
      "num_leaves", "reg_lambda", "learning_rate", "n_estimators",
      "min_child_samples", "subsample", "colsample_bytree", "max_bin",
      "gpu_use_dp", "gpu_platform_id", "gpu_device_id",
    },
    "calibration": {
      "bins", "isotonic_minimum_positives",
      "isotonic_minimum_relative_brier_improvement",
    },
    "candidate_gate": {
      "brier_skill_minimum", "ece_maximum", "minimum_probability",
      "minimum_factor_completeness", "minimum_valid_history", "level_a_size",
      "level_b_size",
    },
  }
  if set(model) != set(model_sections):
    raise ValueError("training spec model_spec must use the canonical sections")
  for section, fields in model_sections.items():
    value = model[section]
    if not isinstance(value, Mapping) or set(value) != fields:
      raise ValueError(f"training spec model_spec.{section} is not canonical")
  if set(evaluation) != {"bootstrap_samples"}:
    raise ValueError("training spec evaluation_spec must use bootstrap_samples")
  try:
    bootstrap_samples = int(evaluation["bootstrap_samples"])
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("training spec evaluation_spec.bootstrap_samples is invalid") from exc
  if not 100 <= bootstrap_samples <= 20_000:
    raise ValueError("training spec evaluation_spec.bootstrap_samples is invalid")
  requested_backend = _value(spec, "requested_backend")
  resolved_backend = _value(spec, "resolved_backend")
  if requested_backend not in {"AUTO", "CPU", "GPU_REQUIRED"}:
    raise ValueError("training spec requested_backend is invalid")
  if resolved_backend not in {"CPU", "LIGHTGBM_OPENCL_GPU"}:
    raise ValueError("training spec resolved_backend is invalid")
  random_seed = _value(spec, "random_seed")
  if isinstance(random_seed, bool):
    raise ValueError("training spec random_seed is invalid")
  try:
    random_seed = int(random_seed)
    batch_size = int(_value(spec, "worker_batch_size"))
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("training spec numeric fields are invalid") from exc
  if not 1 <= batch_size <= 1000:
    raise ValueError("training spec worker_batch_size is invalid")
  locked_hashes: dict[str, str] = {}
  for field in (
    "spec_hash",
    "coordinate_hash",
    "environment_requirement_hash",
  ):
    value = _value(spec, field)
    if (
      not isinstance(value, str)
      or value != value.lower()
      or not _HEX_RE.fullmatch(value)
    ):
      raise ValueError(f"training spec {field} is missing or invalid")
    locked_hashes[field] = value

  if resolved_backend == "LIGHTGBM_OPENCL_GPU":
    capability_value = capability if isinstance(capability, Mapping) else {}
    raw_qualification = capability_value.get("qualification")
    if not isinstance(raw_qualification, Mapping):
      raise ValueError("GPU training spec is missing canonical capability qualification")
    qualification = {
      "status": raw_qualification.get("status"),
      "acceleration": raw_qualification.get("acceleration"),
      "minimum_sample_count": raw_qualification.get("minimum_sample_count"),
      "peak_memory_fraction": raw_qualification.get("peak_memory_fraction"),
      "gates_passed": raw_qualification.get("gates_passed"),
      "evidence_sha256": raw_qualification.get("evidence_sha256"),
    }
    if (
      qualification["status"] != "GPU_AVAILABLE"
      or qualification["gates_passed"] is not True
      or not isinstance(qualification["evidence_sha256"], str)
      or qualification["evidence_sha256"] != qualification["evidence_sha256"].lower()
      or not _HEX_RE.fullmatch(qualification["evidence_sha256"])
    ):
      raise ValueError("GPU training spec capability qualification is not certified")
    minimum = qualification["minimum_sample_count"]
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
      raise ValueError("GPU training spec capability sample threshold is invalid")
    try:
      acceleration = float(qualification["acceleration"])
      peak_memory_fraction = float(qualification["peak_memory_fraction"])
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError("GPU training spec capability metrics are invalid") from exc
    if not math.isfinite(acceleration) or acceleration < 0 or not math.isfinite(peak_memory_fraction) or not 0 <= peak_memory_fraction <= 1:
      raise ValueError("GPU training spec capability metrics are invalid")
    qualification_version = capability_value.get("qualification_version")
    requirement_hash = capability_value.get("requirement_hash")
    if (
      not isinstance(qualification_version, str)
      or not qualification_version
      or len(qualification_version) > 96
      or not isinstance(requirement_hash, str)
      or requirement_hash != requirement_hash.lower()
      or not _HEX_RE.fullmatch(requirement_hash)
    ):
      raise ValueError("GPU training spec capability identifiers are invalid")
    qualification["qualification_version"] = qualification_version
    qualification["requirement_hash"] = requirement_hash
  else:
    # A CPU run records an explicit CPU qualification projection.  It carries
    # no GPU evidence or aliases, even when the worker heartbeat found a GPU.
    qualification = {
      "status": "CPU_AVAILABLE",
      "acceleration": None,
      "minimum_sample_count": None,
      "peak_memory_fraction": None,
      "gates_passed": True,
      "evidence_sha256": None,
    }
  data = {
    "date_range": [str(date_start), str(date_end)],
    "universe_kind": universe_kind,
    "index_code": index_code,
    "benchmark_code": benchmark_code,
    "stock_codes": stock_codes,
    "minimum_listing_days": minimum_listing_days,
    "market_data_archive": None,
    "verified_panel_path": None,
    "historical_st_membership_path": None,
    "historical_industry_membership_path": None,
    "historical_delisting_status_path": None,
  }
  logistic = dict(model["logistic"])
  lightgbm = dict(model["lightgbm"])
  calibration = dict(model["calibration"])
  candidate_gate = dict(model["candidate_gate"])
  return {
    "study": "next-day-selection",
    "version": "v1",
    **locked_hashes,
    "random_seed": random_seed,
    "data": data,
    "walk_forward": {
      "frozen_test_months": split_required["frozen_test_months"],
      "minimum_training_months": split_required["minimum_training_months"],
      "calibration_months": split_required["calibration_months"],
      "validation_months": split_required["validation_months"],
    },
    "logistic": logistic,
    "lightgbm": lightgbm,
    "calibration": calibration,
    "evaluation": {
      "bootstrap_samples": bootstrap_samples,
    },
    "candidate_gate": candidate_gate,
    "runtime": {
      "batch_size": batch_size,
      "output_root": str(output_root),
      "minimum_available_memory_gib": 8.0,
      "memory_sample_interval_seconds": 0.25,
    },
    "requested_backend": requested_backend,
    "resolved_backend": resolved_backend,
    "qualification": qualification,
  }


def build_training_request(
  run: Any,
  spec: Any,
  dataset: Any,
  dataset_files: Mapping[str, Any],
  control_directory: Path,
  *,
  parent_research_run_directory: Path | None = None,
  capability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
  output_root = research_runs_root()
  request = {
    "run_id": str(_value(run, "run_id", "")),
    "run_kind": str(_value(run, "run_kind", "")),
    "spec": _research_spec_payload(
      spec,
      dataset,
      output_root,
      capability=capability,
    ),
    "dataset_directory": str(dataset_files["directory"]),
    "output_root": str(output_root),
    "parent_run_directory": str(parent_research_run_directory) if parent_research_run_directory else None,
    "cancel_file": str(control_directory / "cancel.request"),
    "progress_file": str(control_directory / "progress.json"),
    "frozen_test_access_count": int(_value(spec, "frozen_test_access_count", 0)),
  }
  return request


def _spawn_process(request_path: Path, control_directory: Path) -> Any:
  stdout = (control_directory / "stdout.log").open("ab")
  stderr = (control_directory / "stderr.log").open("ab")
  command = [
    *_research_cli_command(),
    "run-next-day-selection-job",
    "--request-file",
    str(request_path),
  ]
  creationflags = 0
  if os.name == "nt":
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
      getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    )
  try:
    process = subprocess.Popen(
      command,
      stdin=subprocess.DEVNULL,
      stdout=stdout,
      stderr=stderr,
      creationflags=creationflags,
      close_fds=os.name != "nt",
    )
  except BaseException:
    stdout.close()
    stderr.close()
    raise
  for name, stream in (
    ("_quantx_training_stdout", stdout),
    ("_quantx_training_stderr", stderr),
  ):
    try:
      setattr(process, name, stream)
    except (AttributeError, TypeError):
      # A small injected process double may use slots.  The child process is
      # still valid; closing the handles is handled by the owning Popen object.
      pass
  return process


def _process_alive(process: Any) -> bool:
  try:
    return process.poll() is None
  except (OSError, AttributeError):
    return False


def _redact_sensitive_text(value: Any) -> str:
  """Remove credentials and complete absolute paths from worker text."""

  text = str(value or "")
  text = re.sub(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?<![A-Za-z0-9])\\\\[^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?<![A-Za-z0-9])/(?!/)[^\r\n]*", "[PATH]", text)
  text = re.sub(
    r"(?i)(password|secret|token|credential|api[_ -]?key)\s*[:=]\s*[^\s,;]+",
    r"\1=[REDACTED]",
    text,
  )
  text = re.sub(r"[\r\n\t]+", " ", text)
  return text.strip()


def _tail_logs(control_directory: Path, limit: int = 512) -> str:
  pieces: list[str] = []
  for name in ("stderr.log", "stdout.log"):
    try:
      pieces.append((control_directory / name).read_text(encoding="utf-8", errors="replace")[-4096:])
    except OSError:
      continue
  return _redact_sensitive_text(" ".join(pieces))[-limit:]


def _result_payload(
  control_directory: Path,
  *,
  output_root: Path | None = None,
  run_id: str | None = None,
) -> dict[str, Any]:
  del control_directory  # Result identity is the immutable run directory.
  if output_root is not None and run_id:
    safe_run_id = str(run_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", safe_run_id):
      raise ValueError("training result run id is not safe")
    root = output_root.absolute()
    run_directory = root / safe_run_id
    if not _within(root, run_directory):
      raise ValueError("training result directory escapes output root")
    _reject_symlink_components(root, run_directory)
    manifest_path = run_directory / "manifest.json"
    if manifest_path.is_file():
      payload = _json_read(manifest_path)
      payload["manifest_sha256"] = _sha256_file(manifest_path)
      metrics_path = run_directory / "metrics.json"
      if metrics_path.is_file():
        payload["metrics_summary"] = _json_read(metrics_path)
      quality_path = run_directory / "data-quality.json"
      if quality_path.is_file():
        payload["environment_evidence"] = {
          "data_quality": _json_read(quality_path),
          "runtime": payload.get("environment", {}),
        }
      payload["gate_summary"] = payload.get("gates", {})
      return payload
  return {}


def _stable_research_run_key(result: Mapping[str, Any]) -> str | None:
  study_id = str(result.get("study_id") or "next-day-selection")
  version = str(result.get("version") or "v1")
  research_id = str(result.get("run_id") or "").strip()
  if not research_id:
    return None
  return hashlib.sha256(f"{study_id}\0{version}\0{research_id}".encode("utf-8")).hexdigest()


async def recover_lost_training_runs(repository: Any, *, now: datetime | None = None) -> list[str]:
  """Mark DB RUNNING rows whose local child process cannot be proven alive."""

  try:
    rows = await repository.list_runs(status="RUNNING")
  except (AttributeError, TypeError):
    return []
  lost: list[str] = []
  for row in rows or ():
    run_id = str(_value(row, "run_id", ""))
    process = _processes.get(run_id)
    if process is not None and _process_alive(process):
      continue
    try:
      await repository.fail_run(
        run_id,
        error_code="WORKER_PROCESS_LOST",
        error_message="worker process no longer exists after restart",
        environment_evidence=_value(row, "environment_evidence", {}) or {},
        metrics_summary=_value(row, "metrics_summary", {}) or {},
        gate_summary=_value(row, "gate_summary", {}) or {},
        completed_at=now or _now(),
      )
      lost.append(run_id)
    except Exception:
      continue
  return lost


async def _run_claimed_job(
  repository: Any,
  run: Any,
  spec: Any,
  dataset: Any,
  *,
  capability: Mapping[str, Any] | None = None,
  poll_interval_seconds: float = 0.25,
) -> dict[str, Any]:
  run_id = str(_value(run, "run_id", ""))
  control_directory: Path | None = None
  process: Any | None = None
  child_started = False
  try:
    control_directory = _control_directory(run_id)
    files = resolve_dataset_directory(dataset)
    run_kind = str(_value(run, "run_kind", "")).upper()
    if run_kind not in {"DEVELOPMENT", "FINAL_EVALUATION"}:
      raise ValueError("training run_kind is invalid")
    parent_directory: Path | None = None
    if run_kind == "FINAL_EVALUATION":
      parent_run_id = str(_value(run, "parent_run_id", "") or "").strip()
      if not parent_run_id:
        raise ValueError("final evaluation run is missing parent_run_id")
      parent = await repository.get_run(parent_run_id)
      if parent is None:
        raise ValueError("final evaluation parent run is missing")
      if (
        str(_value(parent, "run_kind", "")).upper() != "DEVELOPMENT"
        or str(_value(parent, "status", "")).upper() != "SUCCEEDED"
      ):
        raise ValueError("final evaluation parent must be a succeeded DEVELOPMENT run")
      parent_key = str(_value(parent, "run_key", "") or "").strip().lower()
      if not _HEX_RE.fullmatch(parent_key):
        raise ValueError("final evaluation parent has an invalid Research run_key")
      parent_artifact_hash = str(
        _value(parent, "artifact_manifest_sha256", "") or ""
      ).strip().lower()
      if not _HEX_RE.fullmatch(parent_artifact_hash):
        raise ValueError("final evaluation parent has incomplete Research manifest evidence")
      parent_directory = find_research_run_directory(
        parent_run_id=parent_run_id,
        expected_run_key=parent_key,
        root=research_runs_root(),
      )
      parent_manifest_path = parent_directory / "manifest.json"
      if _sha256_file(parent_manifest_path) != parent_artifact_hash:
        raise ValueError("final evaluation parent manifest hash does not match the DB evidence")
      # Keep the parent identity internal to request.json.  It is never copied
      # to the public environment evidence or flow result.
    request = build_training_request(
      run,
      spec,
      dataset,
      files,
      control_directory,
      capability=capability,
      parent_research_run_directory=parent_directory,
    )
    request_path = control_directory / "request.json"
    progress_path = control_directory / "progress.json"
    cancel_path = control_directory / "cancel.request"
    _write_json(request_path, request)
    process = _spawn_process(request_path, control_directory)
    child_started = True
    _processes[run_id] = process
    last_progress: tuple[Any, ...] | None = None
    cancel_sent = False
    while _process_alive(process):
      current = await repository.get_run(run_id)
      if _value(current, "cancel_requested_at") is not None and not cancel_sent:
        _write_cancel_request(cancel_path)
        cancel_sent = True
      if progress_path.is_file():
        progress = _json_read(progress_path)
        phase = str(progress.get("phase") or "").upper()
        if phase:
          completed = int(progress.get("completed_units", 0))
          total = int(progress.get("total_units", completed))
          marker = phase, completed, total
          if marker != last_progress:
            await repository.update_progress(
              run_id,
              phase=phase,
              completed_units=completed,
              total_units=total,
            )
            last_progress = marker
      await asyncio.sleep(max(0.01, float(poll_interval_seconds)))
    returncode = process.poll()
    current = await repository.get_run(run_id)
    result = _result_payload(
      control_directory,
      output_root=research_runs_root(),
      run_id=run_id,
    )
    result_status = str(result.get("status") or "").lower()
    cancelled = (
      int(returncode) == 3
      or cancel_sent
      or _value(current, "cancel_requested_at") is not None
      or result_status == "cancelled"
    )
    if cancelled:
      await repository.mark_cancelled(
        run_id,
        completed_at=_now(),
        error_code="CANCELLED_BY_USER",
        error_message="training cancellation requested",
      )
      return {"run_id": run_id, "status": "CANCELLED"}
    result_run_id = str(result.get("run_id") or "").strip()
    if (
      int(returncode) == 0
      and result_status in {"success", "succeeded"}
      and result_run_id == run_id
      and str(result.get("run_kind") or "").upper() == run_kind
    ):
      manifest = str(result.get("manifest_sha256") or "").lower()
      stable_key = _stable_research_run_key(result)
      supplied_key = str(result.get("run_key") or "").strip().lower()
      if _HEX_RE.fullmatch(manifest) and stable_key and (not supplied_key or supplied_key == stable_key):
        environment = _safe_public_details(result.get("environment_evidence") or {})
        metrics = result.get("metrics_summary") or {}
        gates = result.get("gate_summary") or {}
        await repository.complete_run(
          run_id,
          run_key=stable_key,
          artifact_manifest_sha256=manifest,
          environment_evidence=environment,
          metrics_summary=metrics if isinstance(metrics, Mapping) else {},
          gate_summary=gates if isinstance(gates, Mapping) else {},
          completed_at=_now(),
        )
        return {"run_id": run_id, "status": "SUCCEEDED", "run_key": stable_key}
      error_code = "INVALID_SUCCESS_MANIFEST"
    else:
      error_code = str(result.get("error_code") or "RESEARCH_PROCESS_FAILED")[:64]
    await repository.fail_run(
      run_id,
      error_code=error_code,
      error_message=_redact_sensitive_text(
        str(result.get("error_message") or _tail_logs(control_directory))
      ),
      environment_evidence=_safe_public_details(result.get("environment_evidence") or {}),
      metrics_summary=result.get("metrics_summary") if isinstance(result.get("metrics_summary"), Mapping) else {},
      gate_summary=result.get("gate_summary") if isinstance(result.get("gate_summary"), Mapping) else {},
      completed_at=_now(),
    )
    return {"run_id": run_id, "status": "FAILED", "error_code": error_code}
  except asyncio.CancelledError:
    try:
      if process is not None and _process_alive(process):
        process.terminate()
    finally:
      raise
  except Exception as exc:
    try:
      if process is not None and _process_alive(process):
        process.terminate()
      await repository.fail_run(
        run_id,
        error_code="WORKER_DISPATCH_FAILED" if child_started else "TRAINING_EVIDENCE_INVALID",
        error_message=_redact_sensitive_text(
          (
            _tail_logs(control_directory) if control_directory is not None else ""
          )
          or str(exc)
        ),
        completed_at=_now(),
      )
    except Exception:
      pass
    return {
      "run_id": run_id,
      "status": "FAILED",
      "error_code": "WORKER_DISPATCH_FAILED" if child_started else "TRAINING_EVIDENCE_INVALID",
    }
  finally:
    _processes.pop(run_id, None)
    if process is not None:
      for stream_name in ("_quantx_training_stdout", "_quantx_training_stderr"):
        stream = getattr(process, stream_name, None)
        if stream is not None:
          try:
            stream.close()
          except OSError:
            pass


@flow(
  name="stock-selection-training-dispatch",
  description="每分钟收敛一次用户发起的次日上涨概率训练任务",
  retries=0,
)
async def stock_selection_training_dispatch_flow(
  now: datetime | None = None,
  *,
  poll_interval_seconds: float = 0.25,
  trading_dates: Any | None = None,
  prefect_flow_run_id: str = "",
) -> dict[str, Any]:
  logger = get_run_logger()
  timestamp = now or _now()
  probe = _probe_capability()
  heartbeat_status, heartbeat_details = _probe_details(probe)
  async with AsyncSessionLocal() as db:
    repository = StockSelectionTrainingRepository(db)
    try:
      await repository.upsert_capability_heartbeat(
        status=heartbeat_status,
        details=heartbeat_details,
        now=timestamp,
      )
    except Exception:
      logger.exception("更新 stock-selection-training 能力心跳失败")
    if _full_live_runtime() and await is_critical_trading_window(timestamp, trading_dates=trading_dates):
      return {
        "status": "QUEUED",
        "reason": "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW",
        "capability": heartbeat_details,
      }
    lost = await recover_lost_training_runs(repository, now=timestamp)
    flow_id = prefect_flow_run_id or os.environ.get("PREFECT_FLOW_RUN_ID", "") or "stock-selection-training-dispatch"
    run = await repository.claim_next_queued(flow_id, timestamp)
    if run is None:
      return {
        "status": "IDLE",
        "reason": "NO_QUEUED_RUN_OR_RUNNING_LIMIT",
        "recovered_run_ids": lost,
        "capability": heartbeat_details,
      }
    spec = await repository.get_spec(str(run.spec_id))
    dataset = await repository.get_dataset(str(spec.dataset_version)) if spec is not None else None
    if spec is None or dataset is None:
      await repository.fail_run(
        str(run.run_id),
        error_code="TRAINING_EVIDENCE_MISSING",
        error_message="immutable training spec or certified dataset is missing",
        completed_at=timestamp,
      )
      return {"status": "FAILED", "run_id": str(run.run_id), "error_code": "TRAINING_EVIDENCE_MISSING"}
    logger.info("开始隔离次日上涨概率训练: run_id=%s", run.run_id)
    result = await _run_claimed_job(
      repository,
      run,
      spec,
      dataset,
      capability=heartbeat_details,
      poll_interval_seconds=poll_interval_seconds,
    )
    result["recovered_run_ids"] = lost
    result["capability"] = heartbeat_details
    return result


__all__ = [
  "CONTROL_DIRECTORY_NAME",
  "CRITICAL_WINDOW_END",
  "CRITICAL_WINDOW_START",
  "find_research_run_directory",
  "is_critical_trading_window",
  "recover_lost_training_runs",
  "resolve_dataset_directory",
  "stock_selection_training_dispatch_flow",
]
