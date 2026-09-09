"""Minute-level dispatcher for isolated next-day selection training jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Mapping

from prefect import flow, get_run_logger
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
  TrainingStateConflict,
)
from quantx_infrastructure.training_dataset_store import (
  _is_link_or_junction,
  _json_read,
  _reject_symlink_components,
  _sha256_file,
  _to_json,
  _value,
  _within,
  resolve_dataset_directory,
)
from quantx_infrastructure.training_host_guard import (
  HostAdmissionDenied,
  HostResourceGuard,
  host_guard_root,
)
from quantx_infrastructure.training_process_evidence import (
  begin_execution,
  inspect_execution,
  record_exit,
  record_spawn,
)
from quantx_infrastructure.training_result import (
  safe_public_details as _safe_public_details,
)

from quantx_trainer.publication import (
  PublicationError,
  publish_generated_result,
  publish_result,
)
from quantx_trainer.runtime import current_config, training_session

RESEARCH_DATASETS_ENV = "QUANTX_RESEARCH_DATASETS_ROOT"
RESEARCH_RUNS_ENV = "QUANTX_RESEARCH_RUNS_ROOT"
CONTROL_DIRECTORY_NAME = "stock-selection-training-control"
_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
_processes: dict[str, Any] = {}


def _repo_root() -> Path:
  return current_config().code_root


def research_datasets_root() -> Path:
  return current_config().state_root / "datasets"


def research_runs_root() -> Path:
  return current_config().state_root / "runs"


def control_root() -> Path:
  return current_config().state_root / "control"


def _now() -> datetime:
  return datetime.now(timezone.utc)


def _research_cli_command() -> list[str]:
  """Run the isolated Research protocol with the Worker's own interpreter."""

  return [sys.executable, "-m", "quantx_research.cli"]


def _probe_capability() -> dict[str, Any]:
  """Read the Research GPU capability protocol through an isolated process."""

  unavailable = {
    "probe_failed": True,
    "cpu_available": False,
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
      env=current_config().research_environment(os.environ),
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


def _host_admission_reason() -> str | None:
  """Read host policy and resources without initializing a compute backend.

  The child still acquires the exclusive host guard and rechecks admission.
  """
  try:
    return HostResourceGuard(host_guard_root()).resource_reason()
  except HostAdmissionDenied as exc:
    return str(exc)


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


def _probe_details(probe: Any) -> tuple[str, dict[str, Any]]:
  details = _safe_public_details(probe)
  details["environment_requirement_hash"] = details.get("requirement_hash")
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
      env=current_config().research_environment(os.environ),
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


async def _stop_research_process(process: Any, *, grace_seconds: float = 5) -> None:
  def stop() -> None:
    if not _process_alive(process):
      return
    try:
      process.terminate()
    except ProcessLookupError:
      if process.poll() is not None:
        return
      raise
    try:
      process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
      process.kill()
      process.wait(timeout=grace_seconds)

  await asyncio.to_thread(stop)


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
  """Converge only stopped supervisor/Research identities; unknown evidence stays pending."""

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
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
      continue
    directory = control_root() / run_id
    owner = str(_value(row, "prefect_flow_run_id", "") or "")
    state = await asyncio.to_thread(
      inspect_execution, directory / "process.json",
      run_id=run_id, owner=owner, request=directory / "request.json",
    )
    if state != "EXITED":
      continue
    manifest_path = research_runs_root() / run_id / "manifest.json"
    if manifest_path.is_file() and _json_read(manifest_path).get("status") == "SUCCEEDED":
      try:
        await publish_result(current_config(), repository, run_id=run_id, owner=owner)
        lost.append(run_id)
      except PublicationError:
        pass  # Preserve completed computation until transfer/registration can resume.
      continue
    try:
      await repository.fail_run(
        run_id,
        expected_flow_run_id=str(_value(row, "prefect_flow_run_id", "") or ""),
        error_code="TRAINER_PROCESS_LOST",
        error_message="recorded research process has exited; execution evidence retained",
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
  execution_owner = str(_value(run, "prefect_flow_run_id", "") or "")
  control_directory: Path | None = None
  process: Any | None = None
  child_started = False
  try:
    control_directory = _control_directory(run_id)
    files = resolve_dataset_directory(dataset, root=research_datasets_root())
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
    evidence_path = control_directory / "process.json"
    begin_execution(evidence_path, run_id=run_id, owner=execution_owner, request=request_path)
    process = _spawn_process(request_path, control_directory)
    child_started = True
    record_spawn(evidence_path, run_id=run_id, owner=execution_owner, request=request_path, process=process)
    _processes[run_id] = process
    last_progress: tuple[Any, ...] | None = None
    cancel_sent = False
    next_heartbeat = 0.0
    while _process_alive(process):
      current = await repository.get_run(run_id)
      if (
        _value(current, "status") != "RUNNING"
        or _value(current, "prefect_flow_run_id") != execution_owner
      ):
        raise TrainingStateConflict("training execution ownership lost")
      if monotonic() >= next_heartbeat:
        current = await repository.heartbeat_execution(
          run_id, expected_flow_run_id=execution_owner,
        )
        next_heartbeat = monotonic() + 10.0
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
              expected_flow_run_id=execution_owner,
              phase=phase,
              completed_units=completed,
              total_units=total,
            )
            last_progress = marker
      await asyncio.sleep(max(0.01, float(poll_interval_seconds)))
    returncode = process.poll()
    record_exit(evidence_path, run_id=run_id, owner=execution_owner, request=request_path, returncode=returncode)
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
        expected_flow_run_id=execution_owner,
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
        try:
          published = await publish_generated_result(current_config(), repository, run_id=run_id, owner=execution_owner)
        except PublicationError as exc:
          return {"run_id": run_id, "status": "RUNNING", "reason": str(exc)}
        return {**published, "run_key": stable_key}
      error_code = "INVALID_SUCCESS_MANIFEST"
    else:
      error_code = str(result.get("error_code") or "RESEARCH_PROCESS_FAILED")[:64]
    await repository.fail_run(
      run_id,
      expected_flow_run_id=execution_owner,
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
  except TrainingStateConflict:
    if process is not None and _process_alive(process):
      await _stop_research_process(process)
    return {"run_id": run_id, "status": "OWNERSHIP_LOST"}
  except asyncio.CancelledError:
    try:
      if process is not None and _process_alive(process):
        await _stop_research_process(process)
    finally:
      raise
  except Exception as exc:
    try:
      if process is not None and _process_alive(process):
        await _stop_research_process(process)
      await repository.fail_run(
        run_id,
        expected_flow_run_id=execution_owner,
        error_code="TRAINER_DISPATCH_FAILED" if child_started else "TRAINING_EVIDENCE_INVALID",
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
      "error_code": "TRAINER_DISPATCH_FAILED" if child_started else "TRAINING_EVIDENCE_INVALID",
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
  config_path: str,
  poll_interval_seconds: float = 0.25,
  prefect_flow_run_id: str = "",
) -> dict[str, Any]:
  async with training_session(config_path) as db:
    logger = get_run_logger()
    timestamp = now or _now()
    repository = StockSelectionTrainingRepository(db)
    lost = await recover_lost_training_runs(repository, now=timestamp)
    heartbeat_details = await repository.get_execution_capability(now=now or _now())
    if heartbeat_details.get("cpu_available") is not True:
      return {"status": "QUEUED", "reason": "CPU_TRAINING_UNAVAILABLE"}
    admission_reason = await asyncio.to_thread(_host_admission_reason)
    if admission_reason:
      return {"status": "QUEUED", "reason": admission_reason}
    flow_id = (
      prefect_flow_run_id
      or os.environ.get("PREFECT_FLOW_RUN_ID", "")
      or str(uuid.uuid4())
    )
    run = await repository.claim_next_queued(flow_id, timestamp)
    if run is None:
      return {
        "status": "IDLE",
        "reason": "NO_QUEUED_RUN_OR_RUNNING_LIMIT",
        "recovered_run_ids": lost,
        "capability": heartbeat_details,
      }
    spec = await repository.get_spec(str(run.spec_id))
    dataset = (
      await repository.get_dataset(str(spec.dataset_version))
      if spec is not None
      else None
    )
    if spec is None or dataset is None:
      await repository.fail_run(
        str(run.run_id),
        expected_flow_run_id=flow_id,
        error_code="TRAINING_EVIDENCE_MISSING",
        error_message="immutable training spec or certified dataset is missing",
        completed_at=timestamp,
      )
      return {
        "status": "FAILED",
        "run_id": str(run.run_id),
        "error_code": "TRAINING_EVIDENCE_MISSING",
      }
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


@flow(name="stock-selection-training-capability", retries=0)
async def stock_selection_training_capability_flow(config_path: str) -> dict[str, Any]:
  """Only periodic capability writer; never claims or waits for training."""
  async with training_session(config_path) as db:
    probe = await asyncio.to_thread(_probe_capability)
    if probe.get("probe_failed") or not isinstance(probe.get("cpu_available"), bool):
      raise RuntimeError("Research 能力探测未返回有效结果；保留上次成功心跳")
    status, details = _probe_details(probe)
    await StockSelectionTrainingRepository(db).upsert_capability_heartbeat(
      status=status, details=details, now=_now()
    )
    return details


__all__ = [
  "CONTROL_DIRECTORY_NAME",
  "find_research_run_directory",
  "recover_lost_training_runs",
  "resolve_dataset_directory",
  "stock_selection_training_dispatch_flow",
]
