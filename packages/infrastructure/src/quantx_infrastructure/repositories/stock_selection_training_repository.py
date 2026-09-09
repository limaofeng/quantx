"""Durable repository for certified datasets and isolated training runs.

The repository is the only component that mutates the training state machine.
Application code receives plain mappings through its port; Worker code uses
the same methods to converge progress and terminal facts.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from quantx_contracts.training_bundle import TrainingBundle
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.models.stock_selection import (
  TRAINING_RUN_PHASES,
  TRAINING_RUN_STATUSES,
  StockSelectionDatasetVersion,
  StockSelectionTrainingRun,
  StockSelectionTrainingSpec,
)

COMPONENT_NAME = "stock-selection-training"
HEARTBEAT_MAX_AGE_SECONDS = 180
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
_RUN_KINDS = frozenset({"DEVELOPMENT", "FINAL_EVALUATION"})
_REQUESTED_BACKENDS = frozenset({"AUTO", "CPU", "GPU_REQUIRED"})
_RESOLVED_BACKENDS = frozenset({"CPU", "LIGHTGBM_OPENCL_GPU"})
_IMMUTABLE_DATASET_FIELDS = (
  "source_kind",
  "source_reference",
  "date_start",
  "date_end",
  "universe_spec",
  "indicator_version",
  "factor_set_version",
  "factor_set_hash",
  "label_version",
  "manifest_sha256",
  "sample_count",
  "stock_count",
  "trading_day_count",
  "quality_summary",
)
_SPEC_FIELDS = (
  "dataset_version",
  "universe_spec",
  "run_kind",
  "split_spec",
  "model_spec",
  "evaluation_spec",
  "requested_backend",
  "resolved_backend",
  "random_seed",
  "worker_batch_size",
  "note",
  "spec_hash",
  "environment_requirement_hash",
  "coordinate_hash",
  "experiment_group_hash",
  "frozen_test_access_count",
  "created_by",
)
_PHASE_INDEX = {phase: index for index, phase in enumerate(TRAINING_RUN_PHASES)}
_PRIVATE_CAPABILITY_KEYS = {
  "path",
  "root",
  "directory",
  "panel_path",
  "manifest_path",
  "device_path",
  "instance_id",
  "device_serial",
  "password",
  "secret",
  "token",
  "credential",
  "api_key",
}


class TrainingRepositoryError(ValueError):
  """Base error exposed by the infrastructure port."""


class TrainingStateConflict(TrainingRepositoryError):
  """The caller attempted to mutate a stale state version."""


class TrainingNotFound(TrainingRepositoryError):
  """A requested dataset, spec, or run does not exist."""


def _utcnow() -> datetime:
  return datetime.now(timezone.utc)


def _as_utc(value: datetime | None, field: str = "datetime") -> datetime | None:
  """Normalize a timestamp to an aware UTC value.

  PostgreSQL preserves the offset for ``TIMESTAMP WITH TIME ZONE``.  SQLite
  (used by unit tests) returns an offset-naive value after round-tripping the
  same column, so a naive value read from persistence is interpreted as UTC
  rather than being allowed to participate in a mixed-aware comparison.
  """

  if value is None:
    return None
  if not isinstance(value, datetime):
    raise TrainingRepositoryError(f"{field} must be a datetime")
  if value.tzinfo is None:
    return value.replace(tzinfo=timezone.utc)
  return value.astimezone(timezone.utc)


def _normalize_row_timestamps(row: Any) -> Any:
  """Keep timestamps exposed by this repository timezone-aware on all DBs."""

  for field in (
    "created_at",
    "requested_at",
    "started_at",
    "execution_heartbeat_at",
    "completed_at",
    "cancel_requested_at",
    "updated_at",
  ):
    if hasattr(row, field):
      value = getattr(row, field)
      if isinstance(value, datetime) and value.tzinfo is None:
        setattr(row, field, value.replace(tzinfo=timezone.utc))
  return row


def _as_mapping(values: Mapping[str, Any] | None, kwargs: Mapping[str, Any]) -> dict[str, Any]:
  result: dict[str, Any] = dict(values or {})
  result.update(kwargs)
  return result


def _row_value(row: Any, name: str, default: Any = None) -> Any:
  if isinstance(row, Mapping):
    return row.get(name, default)
  return getattr(row, name, default)


def _canonical(value: Any) -> Any:
  if isinstance(value, datetime):
    return value.isoformat()
  if hasattr(value, "isoformat") and value.__class__.__name__ == "date":
    return value.isoformat()
  if isinstance(value, Mapping):
    return {
      str(key): _canonical(item)
      for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
  if isinstance(value, (list, tuple)):
    return [_canonical(item) for item in value]
  if isinstance(value, set):
    return [_canonical(item) for item in sorted(value, key=str)]
  if isinstance(value, float) and not math.isfinite(value):
    raise TrainingRepositoryError("JSON evidence cannot contain NaN or Infinity")
  return value


def _validate_json(value: Any, field: str) -> Any:
  """Validate a JSON field without accepting non-finite numeric values."""

  canonical = _canonical(value)
  try:
    json.dumps(canonical, ensure_ascii=False, sort_keys=True, allow_nan=False)
  except (TypeError, ValueError) as exc:
    raise TrainingRepositoryError(f"{field} must be finite JSON") from exc
  return canonical


def _safe_capability_json(value: Any, key: str = "") -> Any:
  if key.lower() in _PRIVATE_CAPABILITY_KEYS:
    return None
  if isinstance(value, Mapping):
    return {
      str(name): child
      for name, item in value.items()
      if (child := _safe_capability_json(item, str(name))) is not None
    }
  if isinstance(value, (list, tuple, set)):
    return [item for child in value if (item := _safe_capability_json(child, key)) is not None]
  return value


def _identity_for_spec(row: Any) -> dict[str, Any]:
  return {field: _canonical(_row_value(row, field)) for field in _SPEC_FIELDS}


def _final_spec_matches(row: Any, payload: Mapping[str, Any]) -> bool:
  """Compare a retry payload without treating the access counter as identity."""

  if row is None:
    return False
  return all(
    field == "frozen_test_access_count"
    or _same_fact(_row_value(row, field), payload[field])
    for field in _SPEC_FIELDS
  )


def _identity_for_run(row: Any) -> tuple[Any, ...]:
  return (
    _row_value(row, "spec_id"),
    _row_value(row, "run_kind"),
    _row_value(row, "parent_run_id"),
  )


def _validate_hash(value: Any, field: str, *, required: bool = True) -> str | None:
  text = str(value or "").strip().lower()
  if not text and not required:
    return None
  if not _HASH_RE.fullmatch(text):
    raise TrainingRepositoryError(f"{field} must be a SHA-256 hex digest")
  return text


def _validate_source_reference(value: Any) -> str:
  text = str(value or "").strip().replace("\\", "/")
  parts = text.split("/")
  if (
    not _SAFE_SOURCE_RE.fullmatch(text)
    or text.startswith("/")
    or any(part in {"", ".", ".."} for part in parts)
    or ":" in text
  ):
    raise TrainingRepositoryError("source_reference must be a safe relative directory key")
  return text


def _coerce_date(value: Any, field: str) -> date:
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  try:
    return date.fromisoformat(str(value).strip()[:10])
  except (TypeError, ValueError) as exc:
    raise TrainingRepositoryError(f"{field} must be an ISO date") from exc


def _safe_error_message(value: Any) -> str:
  text = str(value or "")
  # A path is private evidence even when it contains spaces.  Once an
  # absolute drive/UNC/Unix root is seen, redact to end-of-line so no tail of
  # the path can leak through tokenization.
  text = re.sub(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?<![A-Za-z0-9])\\\\[^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?<![A-Za-z0-9])/(?!/)[^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?i)(password|secret|token|credential|api[_ -]?key)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
  text = re.sub(r"[\r\n\t]+", " ", text)
  return text.strip()[:512]


def _index_filter_values(
  values: str | Sequence[str] | None,
) -> tuple[str, ...] | None:
  """Normalize optional enum filters for the read-only index query."""

  if values is None:
    return None
  source = [values] if isinstance(values, str) else values
  normalized = tuple(
    item
    for item in (str(value).strip().upper() for value in source or ())
    if item
  )
  return normalized or None


def _index_like_pattern(value: str) -> str:
  """Build a case-insensitive SQL LIKE pattern with literal user input."""

  escaped = (
    value.replace("\\", "\\\\")
    .replace("%", "\\%")
    .replace("_", "\\_")
  )
  return f"%{escaped}%"


def _same_fact(left: Any, right: Any) -> bool:
  return _canonical(left) == _canonical(right)


def _validated_spec_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
  values = dict(payload)
  values["spec_id"] = str(values.get("spec_id") or uuid.uuid4())
  for field in _SPEC_FIELDS:
    if field not in values:
      raise TrainingRepositoryError(f"training spec missing field: {field}")
  if values["run_kind"] not in _RUN_KINDS:
    raise TrainingRepositoryError("unknown training spec run_kind")
  if values["requested_backend"] not in _REQUESTED_BACKENDS:
    raise TrainingRepositoryError("unknown requested backend")
  if values["resolved_backend"] not in _RESOLVED_BACKENDS:
    raise TrainingRepositoryError("unknown resolved backend")
  for field in (
    "spec_hash",
    "environment_requirement_hash",
    "coordinate_hash",
    "experiment_group_hash",
  ):
    values[field] = _validate_hash(values[field], field)
  try:
    batch_size = int(values["worker_batch_size"])
    frozen_access = int(values["frozen_test_access_count"])
  except (TypeError, ValueError, OverflowError) as exc:
    raise TrainingRepositoryError("training spec numeric field is invalid") from exc
  if not 1 <= batch_size <= 1000:
    raise TrainingRepositoryError("worker_batch_size must be between 1 and 1000")
  if frozen_access < 0:
    raise TrainingRepositoryError("frozen_test_access_count must be non-negative")
  values["worker_batch_size"] = batch_size
  values["frozen_test_access_count"] = frozen_access
  values["note"] = str(values["note"] or "")
  if len(values["note"]) > 500:
    raise TrainingRepositoryError("note must be at most 500 characters")
  values["created_by"] = str(values["created_by"] or "")[:64]
  if not values["created_by"]:
    raise TrainingRepositoryError("created_by is required")
  if not isinstance(values["universe_spec"], Mapping):
    raise TrainingRepositoryError("universe_spec must be a mapping")
  values["universe_spec"] = _validate_json(values["universe_spec"], "universe_spec")
  values["split_spec"] = _validate_json(values["split_spec"], "split_spec")
  values["model_spec"] = _validate_json(values["model_spec"], "model_spec")
  values["evaluation_spec"] = _validate_json(values["evaluation_spec"], "evaluation_spec")
  values["universe_spec"] = dict(values["universe_spec"])
  values["split_spec"] = dict(values["split_spec"])
  values["model_spec"] = dict(values["model_spec"])
  values["evaluation_spec"] = dict(values["evaluation_spec"])
  return values


def _validated_run_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
  values = dict(payload)
  values["run_id"] = str(values.get("run_id") or uuid.uuid4())
  values.setdefault("status", "QUEUED")
  values.setdefault("phase", "PREFLIGHT")
  values.setdefault("completed_units", 0)
  values.setdefault("total_units", 0)
  values.setdefault("environment_evidence", {})
  values.setdefault("metrics_summary", {})
  values.setdefault("gate_summary", {})
  values.setdefault("cancel_idempotency_key", None)
  if values.get("run_kind") not in _RUN_KINDS:
    raise TrainingRepositoryError("unknown training run run_kind")
  if values.get("status") not in TRAINING_RUN_STATUSES:
    raise TrainingRepositoryError("unknown training run status")
  if values.get("phase") not in TRAINING_RUN_PHASES:
    raise TrainingRepositoryError("unknown training run phase")
  parent = values.get("parent_run_id")
  if parent is not None:
    parent = str(parent).strip() or None
    values["parent_run_id"] = parent
  if (values["run_kind"] == "FINAL_EVALUATION") != bool(parent):
    raise TrainingRepositoryError("final runs require a parent and development runs do not")
  try:
    completed = int(values["completed_units"])
    total = int(values["total_units"])
  except (TypeError, ValueError, OverflowError) as exc:
    raise TrainingRepositoryError("run units are invalid") from exc
  if completed < 0 or total < 0 or completed > total:
    raise TrainingRepositoryError("run units must be non-negative and bounded")
  values["completed_units"], values["total_units"] = completed, total
  values["state_version"] = max(1, int(values.get("state_version", 1)))
  idem = str(values.get("idempotency_key") or "").strip()
  if not idem or len(idem) > 160:
    raise TrainingRepositoryError("idempotency_key must be non-empty and at most 160 characters")
  values["idempotency_key"] = idem
  cancel_idem = values.get("cancel_idempotency_key")
  if cancel_idem is not None:
    cancel_idem = str(cancel_idem).strip()
    if not cancel_idem or len(cancel_idem) > 160:
      raise TrainingRepositoryError("cancel_idempotency_key is invalid")
  values["cancel_idempotency_key"] = cancel_idem
  for field in ("environment_evidence", "metrics_summary", "gate_summary"):
    if not isinstance(values[field], Mapping):
      raise TrainingRepositoryError(f"{field} must be a mapping")
    values[field] = dict(_validate_json(values[field], field))
  return values


class StockSelectionTrainingRepository:
  """Async SQLAlchemy adapter implementing the training state machine."""

  def __init__(self, db: AsyncSession):
    self.db = db

  # ---------------------------------------------------------------------
  # Dataset and capability reads
  # ---------------------------------------------------------------------
  async def get_dataset(self, dataset_version: str) -> StockSelectionDatasetVersion | None:
    row = await self.db.get(StockSelectionDatasetVersion, str(dataset_version))
    return _normalize_row_timestamps(row)

  async def list_datasets(
    self, *, limit: int = 100, offset: int = 0
  ) -> list[StockSelectionDatasetVersion]:
    """List only certified datasets that are eligible for a new run."""

    result = await self.db.execute(
      select(StockSelectionDatasetVersion)
      .where(StockSelectionDatasetVersion.status == "CERTIFIED")
      .order_by(
        StockSelectionDatasetVersion.date_end.desc(),
        StockSelectionDatasetVersion.dataset_version.asc(),
      )
      .offset(max(0, int(offset)))
      .limit(max(1, min(int(limit), 500)))
    )
    return [_normalize_row_timestamps(row) for row in result.scalars().all()]

  async def _read_capability_certificate(
    self,
    *,
    now: datetime | None = None,
    max_age_seconds: int = HEARTBEAT_MAX_AGE_SECONDS,
  ) -> tuple[Mapping[str, Any], datetime | None, bool]:
    heartbeat = await self.db.get(RuntimeComponentHeartbeat, COMPONENT_NAME)
    current = _as_utc(now or _utcnow(), "now")
    updated = _as_utc(_row_value(heartbeat, "updated_at"), "updated_at")
    fresh = bool(
      heartbeat is not None
      and updated is not None
      and current is not None
      and current - updated <= timedelta(seconds=max(1, int(max_age_seconds)))
      and current - updated >= timedelta(seconds=-5)
    )
    details = _safe_capability_json(_row_value(heartbeat, "details", {}) or {})
    if not isinstance(details, Mapping):
      details = {}
    return {
      **details,
      "status": details.get("status") or _row_value(heartbeat, "status", ""),
    }, updated, fresh

  async def get_execution_capability(
    self, *, now: datetime | None = None,
  ) -> dict[str, Any]:
    """Internal execution evidence; stale certificates never authorize work."""
    details, _, fresh = await self._read_capability_certificate(now=now)
    return dict(details) if fresh else {}

  async def get_capability(
    self,
    *,
    now: datetime | None = None,
    max_age_seconds: int = HEARTBEAT_MAX_AGE_SECONDS,
  ) -> dict[str, Any]:
    """Read the public projection of the latest capability certificate."""
    details, updated, fresh = await self._read_capability_certificate(
      now=now, max_age_seconds=max_age_seconds,
    )
    qualification = details.get("qualification")
    if not isinstance(qualification, Mapping):
      qualification = {}
    memory = details.get("available_memory_mib")
    try:
      memory = max(0, int(memory)) if memory is not None else None
    except (TypeError, ValueError, OverflowError):
      memory = None
    environment_hash = str(details.get("environment_requirement_hash") or "").lower()
    if not _HASH_RE.fullmatch(environment_hash):
      environment_hash = ""
    raw_status = str(details.get("status") or "").upper()
    if not fresh:
      status = "CPU_AVAILABLE"
      gpu_status = "GPU_UNAVAILABLE_RUNTIME"
    else:
      status = (
        raw_status
        if raw_status
        in {
          "CPU_AVAILABLE",
          "GPU_AVAILABLE",
          "GPU_UNAVAILABLE_BUILD",
          "GPU_UNAVAILABLE_RUNTIME",
          "GPU_INSUFFICIENT_MEMORY",
          "GPU_UNQUALIFIED",
        }
        else "CPU_AVAILABLE"
      )
      gpu_status = str(details.get("gpu_status") or status).upper()
      if gpu_status not in {
        "GPU_AVAILABLE",
        "GPU_UNAVAILABLE_BUILD",
        "GPU_UNAVAILABLE_RUNTIME",
        "GPU_INSUFFICIENT_MEMORY",
        "GPU_UNQUALIFIED",
      }:
        gpu_status = "GPU_UNAVAILABLE_RUNTIME"
    return {
      "status": status,
      "gpu_status": gpu_status,
      "environment_requirement_hash": environment_hash,
      "available_memory_mib": memory,
      "qualification": dict(_safe_capability_json(qualification) or {}),
      "fresh": fresh,
      "cpu_available": fresh and details.get("cpu_available") is True,
      "updated_at": updated,
    }

  async def upsert_capability_heartbeat(
    self,
    *,
    status: str,
    details: Mapping[str, Any] | None = None,
    instance_id: str = "stock-selection-training-worker",
    now: datetime | None = None,
  ) -> RuntimeComponentHeartbeat:
    row = await self.db.get(RuntimeComponentHeartbeat, COMPONENT_NAME)
    aware_timestamp = _as_utc(now or _utcnow(), "now")
    assert aware_timestamp is not None
    timestamp = aware_timestamp.replace(tzinfo=None)
    safe_details = _safe_capability_json(dict(details or {}))
    safe_details = dict(_validate_json(safe_details, "capability details"))
    if row is None:
      row = RuntimeComponentHeartbeat(
        component=COMPONENT_NAME,
        instance_id=str(instance_id)[:64],
        status=str(status)[:32],
        details=safe_details,
        updated_at=timestamp,
      )
      self.db.add(row)
    else:
      row.instance_id = str(instance_id)[:64]
      row.status = str(status)[:32]
      row.details = safe_details
      row.updated_at = timestamp
    await self.db.commit()
    await self.db.refresh(row)
    # API normalization makes timestamps aware; detach first so a later query
    # cannot autoflush that projection back into PostgreSQL's naive column.
    self.db.expunge(row)
    return _normalize_row_timestamps(row)

  # ---------------------------------------------------------------------
  # Immutable dataset/spec creation and idempotent queue insertion
  # ---------------------------------------------------------------------
  async def certify_dataset(
    self,
    values: Mapping[str, Any] | None = None,
    **kwargs: Any,
  ) -> StockSelectionDatasetVersion:
    payload = _as_mapping(values, kwargs)
    required = (
      "dataset_version",
      "status",
      "source_kind",
      "source_reference",
      "date_start",
      "date_end",
      "universe_spec",
      "indicator_version",
      "factor_set_version",
      "factor_set_hash",
      "label_version",
      "manifest_sha256",
      "sample_count",
      "stock_count",
      "trading_day_count",
      "quality_summary",
    )
    missing = [field for field in required if field not in payload]
    if missing:
      raise TrainingRepositoryError(f"certify_dataset missing fields: {', '.join(missing)}")
    version = str(payload["dataset_version"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", version):
      raise TrainingRepositoryError("dataset_version must be a non-empty safe id")
    status = str(payload["status"]).upper()
    if status not in {"CERTIFIED", "RETIRED"}:
      raise TrainingRepositoryError("unknown dataset status")
    start = _coerce_date(payload["date_start"], "date_start")
    end = _coerce_date(payload["date_end"], "date_end")
    if start > end:
      raise TrainingRepositoryError("dataset date_start must not follow date_end")
    source_reference = _validate_source_reference(payload["source_reference"])
    if not isinstance(payload["universe_spec"], Mapping):
      raise TrainingRepositoryError("universe_spec must be a mapping")
    universe_spec = _validate_json(payload["universe_spec"], "universe_spec")
    if not isinstance(payload["quality_summary"], Mapping):
      raise TrainingRepositoryError("quality_summary must be a mapping")
    quality_summary = _validate_json(payload["quality_summary"], "quality_summary")
    factor_hash = _validate_hash(payload["factor_set_hash"], "factor_set_hash")
    manifest_hash = _validate_hash(payload["manifest_sha256"], "manifest_sha256")
    counts: dict[str, int] = {}
    for field in ("sample_count", "stock_count", "trading_day_count"):
      try:
        count = int(payload[field])
      except (TypeError, ValueError, OverflowError) as exc:
        raise TrainingRepositoryError(f"{field} must be a non-negative integer") from exc
      if count < 0:
        raise TrainingRepositoryError(f"{field} must be a non-negative integer")
      counts[field] = count
    evidence = {
      "source_kind": str(payload["source_kind"]),
      "source_reference": source_reference,
      "date_start": start,
      "date_end": end,
      "universe_spec": dict(universe_spec),
      "indicator_version": str(payload["indicator_version"]),
      "factor_set_version": str(payload["factor_set_version"]),
      "factor_set_hash": factor_hash,
      "label_version": str(payload["label_version"]),
      "manifest_sha256": manifest_hash,
      **counts,
      "quality_summary": dict(quality_summary),
    }
    existing = await self.get_dataset(version)
    if existing is not None:
      changed = [
        field
        for field in _IMMUTABLE_DATASET_FIELDS
        if not _same_fact(getattr(existing, field), evidence[field])
      ]
      if changed:
        raise TrainingRepositoryError(
          f"dataset version immutable evidence changed: {', '.join(changed)}"
        )
      if existing.status != status:
        existing.status = status
        await self.db.commit()
        await self.db.refresh(existing)
      return _normalize_row_timestamps(existing)
    row = StockSelectionDatasetVersion(
      dataset_version=version,
      status=status,
      **evidence,
    )
    self.db.add(row)
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = await self.get_dataset(version)
      if existing is None:
        raise
      changed = [
        field
        for field in _IMMUTABLE_DATASET_FIELDS
        if not _same_fact(getattr(existing, field), evidence[field])
      ]
      if changed:
        raise TrainingRepositoryError(
          f"dataset version immutable evidence changed: {', '.join(changed)}"
        )
      return _normalize_row_timestamps(existing)
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def create_spec(
    self,
    values: Mapping[str, Any] | None = None,
    **kwargs: Any,
  ) -> StockSelectionTrainingSpec:
    payload = _validated_spec_payload(_as_mapping(values, kwargs))
    dataset = await self.get_dataset(payload["dataset_version"])
    if dataset is None or dataset.status != "CERTIFIED":
      raise TrainingNotFound("certified training dataset does not exist")
    spec_id = payload["spec_id"]
    existing = await self.db.get(StockSelectionTrainingSpec, spec_id)
    if existing is not None:
      if _identity_for_spec(existing) != _identity_for_spec(payload):
        raise TrainingRepositoryError("training spec is immutable")
      return _normalize_row_timestamps(existing)
    row = StockSelectionTrainingSpec(**{field: payload[field] for field in _SPEC_FIELDS}, spec_id=spec_id)
    self.db.add(row)
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = await self.db.get(StockSelectionTrainingSpec, spec_id)
      if existing is not None and _identity_for_spec(existing) == _identity_for_spec(payload):
        return _normalize_row_timestamps(existing)
      raise
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def create_development(
    self,
    spec_values: Mapping[str, Any],
    run_values: Mapping[str, Any],
  ) -> tuple[StockSelectionTrainingSpec, StockSelectionTrainingRun, bool]:
    """Atomically create one DEVELOPMENT spec and its queued run.

    The spec and run are one idempotent unit.  In particular, a concurrent
    retry may use a different generated spec/run id, but a unique
    ``idempotency_key`` conflict is resolved to the already committed fact
    only when its semantic ``spec_hash`` and lifecycle kind match.  A failed
    run insert therefore rolls back the speculative spec in the same
    transaction instead of leaving an orphan row behind.
    """

    spec_payload = _validated_spec_payload(spec_values)
    if spec_payload["run_kind"] != "DEVELOPMENT":
      raise TrainingRepositoryError("development creation requires a DEVELOPMENT spec")
    run_payload = _validated_run_payload(run_values)
    if run_payload["run_kind"] != "DEVELOPMENT":
      raise TrainingRepositoryError("development creation requires a DEVELOPMENT run")
    if run_payload["spec_id"] != spec_payload["spec_id"]:
      raise TrainingRepositoryError("development spec and run ids do not match")
    if run_payload.get("parent_run_id") is not None:
      raise TrainingRepositoryError("development runs cannot have a parent")
    dataset = await self.get_dataset(spec_payload["dataset_version"])
    if dataset is None or str(dataset.status).upper() != "CERTIFIED":
      raise TrainingNotFound("certified training dataset does not exist")

    async def existing_fact() -> tuple[StockSelectionTrainingSpec, StockSelectionTrainingRun, bool] | None:
      result = await self.db.execute(
        select(StockSelectionTrainingRun).where(
          StockSelectionTrainingRun.idempotency_key == run_payload["idempotency_key"]
        )
      )
      existing_run = result.scalar_one_or_none()
      if existing_run is None:
        return None
      existing_spec = await self.get_spec(existing_run.spec_id)
      if (
        existing_run.run_kind != "DEVELOPMENT"
        or existing_spec is None
        or existing_spec.spec_hash != spec_payload["spec_hash"]
      ):
        raise TrainingRepositoryError(
          "idempotency key is already bound to another development payload"
        )
      return (
        existing_spec,
        _normalize_row_timestamps(existing_run),
        True,
      )

    already = await existing_fact()
    if already is not None:
      return already

    spec = StockSelectionTrainingSpec(
      **{field: spec_payload[field] for field in _SPEC_FIELDS},
      spec_id=spec_payload["spec_id"],
    )
    run = StockSelectionTrainingRun(**run_payload)
    self.db.add(spec)
    self.db.add(run)
    try:
      await self.db.commit()
    except IntegrityError:
      # The only expected concurrent conflict is the unique request key.  A
      # rollback removes both speculative rows before resolving the winner.
      await self.db.rollback()
      already = await existing_fact()
      if already is not None:
        return already
      raise
    await self.db.refresh(spec)
    await self.db.refresh(run)
    return (
      _normalize_row_timestamps(spec),
      _normalize_row_timestamps(run),
      False,
    )

  async def get_spec(self, spec_id: str) -> StockSelectionTrainingSpec | None:
    return _normalize_row_timestamps(
      await self.db.get(StockSelectionTrainingSpec, str(spec_id))
    )

  async def list_specs(
    self, *, limit: int = 100, offset: int = 0
  ) -> list[StockSelectionTrainingSpec]:
    result = await self.db.execute(
      select(StockSelectionTrainingSpec)
      .order_by(StockSelectionTrainingSpec.created_at.desc(), StockSelectionTrainingSpec.spec_id.asc())
      .offset(max(0, int(offset)))
      .limit(max(1, min(int(limit), 500)))
    )
    return [_normalize_row_timestamps(row) for row in result.scalars().all()]

  async def get_run(self, run_id: str) -> StockSelectionTrainingRun | None:
    return _normalize_row_timestamps(
      await self.db.get(StockSelectionTrainingRun, str(run_id), populate_existing=True)
    )

  async def get_run_by_run_key(self, run_key: str) -> StockSelectionTrainingRun | None:
    """Resolve the immutable Research run key without guessing a directory."""

    key = str(run_key or "").strip()
    if not key:
      return None
    result = await self.db.execute(
      select(StockSelectionTrainingRun).where(
        StockSelectionTrainingRun.run_key == key
      )
    )
    return _normalize_row_timestamps(result.scalar_one_or_none())

  async def get_run_by_idempotency_key(
    self, idempotency_key: str
  ) -> StockSelectionTrainingRun | None:
    result = await self.db.execute(
      select(StockSelectionTrainingRun).where(
        StockSelectionTrainingRun.idempotency_key == str(idempotency_key)
      )
    )
    return _normalize_row_timestamps(result.scalar_one_or_none())

  async def create_run(
    self,
    values: Mapping[str, Any] | None = None,
    **kwargs: Any,
  ) -> StockSelectionTrainingRun:
    payload = _validated_run_payload(_as_mapping(values, kwargs))
    spec = await self.get_spec(payload["spec_id"])
    if spec is None:
      raise TrainingNotFound("training spec does not exist")
    if str(spec.run_kind) != str(payload["run_kind"]):
      raise TrainingRepositoryError("training run kind does not match its spec")
    parent = payload.get("parent_run_id")
    if parent is not None:
      parent_row = await self.get_run(parent)
      if parent_row is None:
        raise TrainingNotFound("parent training run does not exist")
      if str(parent_row.run_kind) != "DEVELOPMENT":
        raise TrainingRepositoryError("final training parent must be DEVELOPMENT")
    idem = payload["idempotency_key"]
    existing = await self.get_run_by_idempotency_key(idem)
    if existing is not None:
      if _identity_for_run(existing) != (
        payload["spec_id"],
        payload["run_kind"],
        parent,
      ):
        raise TrainingRepositoryError("idempotency key is already bound to another run")
      return _normalize_row_timestamps(existing)
    row = StockSelectionTrainingRun(**payload)
    self.db.add(row)
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = await self.get_run_by_idempotency_key(idem)
      if existing is not None and _identity_for_run(existing) == (
        payload["spec_id"],
        payload["run_kind"],
        parent,
      ):
        return _normalize_row_timestamps(existing)
      raise
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def create_final_evaluation(
    self,
    spec_values: Mapping[str, Any],
    run_values: Mapping[str, Any],
  ) -> tuple[StockSelectionTrainingSpec, StockSelectionTrainingRun, bool]:
    """Create one FINAL spec/run while serializing its experiment counter.

    Development specs are the durable coordination rows for an experiment
    group.  Locking every development coordinate in that group makes the
    count-and-insert operation atomic on PostgreSQL without a process-local
    mutex; the partial RUNNING index independently protects execution claim.
    """

    spec_payload = _validated_spec_payload(spec_values)
    if spec_payload["run_kind"] != "FINAL_EVALUATION":
      raise TrainingRepositoryError("final evaluation requires a FINAL_EVALUATION spec")
    run_payload = _validated_run_payload(run_values)
    if run_payload["run_kind"] != "FINAL_EVALUATION":
      raise TrainingRepositoryError("final evaluation requires a FINAL_EVALUATION run")
    if run_payload["spec_id"] != spec_payload["spec_id"]:
      raise TrainingRepositoryError("final spec and run ids do not match")
    parent_id = run_payload["parent_run_id"]
    parent_result = await self.db.execute(
      select(StockSelectionTrainingRun)
      .where(StockSelectionTrainingRun.run_id == parent_id)
      .with_for_update()
    )
    parent = parent_result.scalar_one_or_none()
    if parent is None:
      raise TrainingNotFound("parent training run does not exist")
    if parent.run_kind != "DEVELOPMENT":
      raise TrainingRepositoryError("final training parent must be DEVELOPMENT")
    parent_spec_result = await self.db.execute(
      select(StockSelectionTrainingSpec)
      .where(StockSelectionTrainingSpec.spec_id == parent.spec_id)
      .with_for_update()
    )
    parent_spec = parent_spec_result.scalar_one_or_none()
    if parent_spec is None:
      raise TrainingNotFound("parent training spec does not exist")
    group = spec_payload["experiment_group_hash"]
    if parent_spec.experiment_group_hash != group:
      raise TrainingRepositoryError(
        "final training spec does not match the parent experiment coordinate"
      )
    if spec_payload["spec_hash"] != parent_spec.spec_hash:
      raise TrainingRepositoryError(
        "final training spec_hash must match the parent experiment semantic"
      )
    if spec_payload["coordinate_hash"] != parent_spec.coordinate_hash:
      raise TrainingRepositoryError(
        "final training coordinate_hash must match the parent experiment coordinate"
      )
    # Serialize every final counter in this experiment group, not just final
    # requests sharing one parent.  Different successful DEVELOPMENT parents
    # can carry the same coordinate and must still receive 1, 2, ... exactly
    # once under concurrent requests.
    await self.db.execute(
      select(StockSelectionTrainingSpec)
      .where(
        StockSelectionTrainingSpec.run_kind == "DEVELOPMENT",
        StockSelectionTrainingSpec.experiment_group_hash == group,
      )
      .order_by(StockSelectionTrainingSpec.spec_id.asc())
      .with_for_update()
    )
    existing_result = await self.db.execute(
      select(StockSelectionTrainingRun).where(
        StockSelectionTrainingRun.idempotency_key == run_payload["idempotency_key"]
      )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
      existing_spec = await self.get_spec(existing.spec_id)
      if (
        existing.run_kind != "FINAL_EVALUATION"
        or existing.parent_run_id != run_payload.get("parent_run_id")
        or not _final_spec_matches(existing_spec, spec_payload)
      ):
        raise TrainingRepositoryError("idempotency key is already bound to another final payload")
      return existing_spec, _normalize_row_timestamps(existing), True
    count_value = await self.db.scalar(
      select(func.count())
      .select_from(StockSelectionTrainingSpec)
      .where(
        StockSelectionTrainingSpec.run_kind == "FINAL_EVALUATION",
        StockSelectionTrainingSpec.experiment_group_hash == group,
      )
    )
    spec_payload["frozen_test_access_count"] = int(count_value or 0) + 1
    spec = StockSelectionTrainingSpec(
      **{field: spec_payload[field] for field in _SPEC_FIELDS},
      spec_id=spec_payload["spec_id"],
    )
    run = StockSelectionTrainingRun(**run_payload)
    self.db.add(spec)
    self.db.add(run)
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing_result = await self.db.execute(
        select(StockSelectionTrainingRun).where(
          StockSelectionTrainingRun.idempotency_key == run_payload["idempotency_key"]
        )
      )
      existing = existing_result.scalar_one_or_none()
      if existing is None:
        raise
      existing_spec = await self.get_spec(existing.spec_id)
      if (
        existing.run_kind != "FINAL_EVALUATION"
        or existing.parent_run_id != run_payload.get("parent_run_id")
        or not _final_spec_matches(existing_spec, spec_payload)
      ):
        raise TrainingRepositoryError("idempotency key is already bound to another final payload")
      return existing_spec, _normalize_row_timestamps(existing), True
    await self.db.refresh(spec)
    await self.db.refresh(run)
    return (
      _normalize_row_timestamps(spec),
      _normalize_row_timestamps(run),
      False,
    )

  async def list_runs(
    self,
    *,
    status: str | None = None,
    run_kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
  ) -> list[StockSelectionTrainingRun]:
    statement = select(StockSelectionTrainingRun)
    if status is not None:
      statement = statement.where(StockSelectionTrainingRun.status == str(status).upper())
    if run_kind is not None:
      statement = statement.where(StockSelectionTrainingRun.run_kind == str(run_kind).upper())
    result = await self.db.execute(
      statement.order_by(
        StockSelectionTrainingRun.requested_at.desc(),
        StockSelectionTrainingRun.run_id.asc(),
      )
      .offset(max(0, int(offset)))
      .limit(max(1, min(int(limit), 500)))
    )
    return [_normalize_row_timestamps(row) for row in result.scalars().all()]

  async def list_runs_for_index(
    self,
    *,
    statuses: Sequence[str] | None = None,
    run_kinds: Sequence[str] | None = None,
    status: str | None = None,
    run_kind: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
  ) -> list[tuple[StockSelectionTrainingRun, StockSelectionTrainingSpec]]:
    """Read every training run/spec pair needed by the unified run index.

    The index must apply one global ordering and pagination after merging the
    database and artifact sources.  Consequently this method deliberately has
    no limit/offset arguments.  It also joins the immutable spec in the same
    SQL statement so callers never need an N+1 ``get_spec`` lookup per run.
    """

    status_values = _index_filter_values(statuses if statuses is not None else status)
    run_kind_values = _index_filter_values(
      run_kinds if run_kinds is not None else run_kind
    )
    normalized_search = str(search or "").strip()
    if len(normalized_search) > 128:
      raise TrainingRepositoryError("search must be at most 128 characters")
    if date_from is not None and date_to is not None and date_from > date_to:
      raise TrainingRepositoryError("date_from cannot be later than date_to")

    updated_at = func.coalesce(
      StockSelectionTrainingRun.completed_at,
      StockSelectionTrainingRun.cancel_requested_at,
      StockSelectionTrainingRun.started_at,
      StockSelectionTrainingRun.requested_at,
    )
    statement = (
      select(StockSelectionTrainingRun, StockSelectionTrainingSpec)
      .join(
        StockSelectionTrainingSpec,
        StockSelectionTrainingSpec.spec_id == StockSelectionTrainingRun.spec_id,
      )
    )
    if status_values:
      statement = statement.where(StockSelectionTrainingRun.status.in_(status_values))
    if run_kind_values:
      statement = statement.where(StockSelectionTrainingRun.run_kind.in_(run_kind_values))
    if date_from is not None:
      start_at = datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc)
      statement = statement.where(updated_at >= start_at)
    if date_to is not None:
      end_at = datetime.combine(
        date_to + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
      )
      statement = statement.where(updated_at < end_at)
    if normalized_search:
      search_pattern = _index_like_pattern(normalized_search.lower())
      statement = statement.where(
        or_(
          func.lower(StockSelectionTrainingRun.run_id).like(search_pattern, escape="\\"),
          func.lower(StockSelectionTrainingRun.run_key).like(search_pattern, escape="\\"),
          func.lower(StockSelectionTrainingRun.parent_run_id).like(
            search_pattern,
            escape="\\",
          ),
          func.lower(StockSelectionTrainingRun.run_kind).like(
            search_pattern,
            escape="\\",
          ),
          func.lower(StockSelectionTrainingSpec.dataset_version).like(
            search_pattern,
            escape="\\",
          ),
        )
      )

    result = await self.db.execute(
      statement.order_by(updated_at.desc().nullslast(), StockSelectionTrainingRun.run_id.asc())
    )
    return [
      (_normalize_row_timestamps(run), spec)
      for run, spec in result.all()
    ]

  async def list_run_ids_for_index(self) -> list[str]:
    """Read all durable run identities for cross-source artifact dedupe.

    This is a single set-based identity query.  It deliberately has no
    filters because the API must suppress a stale artifact even when the
    corresponding authoritative DB row is excluded by the caller's final
    stage, status, date, or search filter.
    """

    result = await self.db.execute(
      select(StockSelectionTrainingRun.run_id)
    )
    return [str(run_id) for run_id in result.scalars().all() if run_id is not None]

  async def count_runs(
    self,
    *,
    status: str | None = None,
    run_kind: str | None = None,
  ) -> int:
    """Count durable runs using the same filters as :meth:`list_runs`."""

    statement = select(func.count()).select_from(StockSelectionTrainingRun)
    if status is not None:
      statement = statement.where(StockSelectionTrainingRun.status == str(status).upper())
    if run_kind is not None:
      statement = statement.where(StockSelectionTrainingRun.run_kind == str(run_kind).upper())
    value = await self.db.scalar(statement)
    return int(value or 0)

  async def count_final_evaluations(self, experiment_group_hash: str) -> int:
    value = await self.db.scalar(
      select(func.count())
      .select_from(StockSelectionTrainingSpec)
      .where(
        StockSelectionTrainingSpec.run_kind == "FINAL_EVALUATION",
        StockSelectionTrainingSpec.experiment_group_hash == str(experiment_group_hash),
      )
    )
    return int(value or 0)

  # ---------------------------------------------------------------------
  # Queue claim, progress, cancellation, and terminal facts
  # ---------------------------------------------------------------------
  async def claim_next_queued(
    self,
    prefect_flow_run_id: str,
    now: datetime | None = None,
  ) -> StockSelectionTrainingRun | None:
    owner = str(prefect_flow_run_id or "").strip()
    if not owner or len(owner) > 128:
      raise TrainingRepositoryError("claim requires a non-empty execution identity of at most 128 characters")
    current = _as_utc(now or _utcnow(), "now")
    running = list(
      (
        await self.db.execute(
          select(StockSelectionTrainingRun)
          .where(StockSelectionTrainingRun.status == "RUNNING")
          .with_for_update(skip_locked=True)
        )
      ).scalars().all()
    )
    if running:
      return None
    result = await self.db.execute(
      select(StockSelectionTrainingRun)
      .where(
        StockSelectionTrainingRun.status == "QUEUED",
        StockSelectionTrainingRun.cancel_requested_at.is_(None),
      )
      .order_by(
        StockSelectionTrainingRun.requested_at.asc(),
        StockSelectionTrainingRun.run_id.asc(),
      )
      .limit(1)
      .with_for_update(skip_locked=True)
    )
    row = result.scalar_one_or_none()
    if row is None:
      return None
    row.status = "RUNNING"
    row.phase = "PREFLIGHT"
    row.started_at = current
    row.prefect_flow_run_id = owner
    row.execution_heartbeat_at = current
    row.state_version += 1
    try:
      await self.db.commit()
    except IntegrityError:
      # PostgreSQL's partial unique index is the database-level arbiter when
      # two workers race between the RUNNING read and QUEUED claim.
      await self.db.rollback()
      return None
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def heartbeat_execution(
    self,
    run_id: str,
    *,
    expected_flow_run_id: str,
    now: datetime | None = None,
  ) -> StockSelectionTrainingRun:
    """Persist owner liveness without invalidating a user's cancellation version."""
    current = _as_utc(now or _utcnow(), "now")
    row = await self._locked_run(run_id)
    if row is None:
      raise TrainingNotFound("training run does not exist")
    self._assert_execution_owner(row, expected_flow_run_id)
    if row.status != "RUNNING":
      raise TrainingStateConflict("training execution is no longer running")
    previous = _as_utc(row.execution_heartbeat_at, "execution_heartbeat_at")
    row.execution_heartbeat_at = max(previous, current) if previous else current
    await self.db.commit()
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def update_progress(
    self,
    run_id: str,
    *,
    expected_flow_run_id: str,
    phase: str,
    completed_units: int,
    total_units: int | None = None,
    expected_state_version: int | None = None,
    now: datetime | None = None,
  ) -> StockSelectionTrainingRun:
    del now  # Progress timestamps are represented by the state version only.
    row = await self._locked_run(run_id)
    if row is None:
      raise TrainingNotFound("training run does not exist")
    self._assert_execution_owner(row, expected_flow_run_id)
    if row.status != "RUNNING":
      raise TrainingRepositoryError("only RUNNING runs accept progress")
    self._assert_version(row, expected_state_version)
    phase_name = str(phase).upper()
    if phase_name not in _PHASE_INDEX:
      raise TrainingRepositoryError("unknown training run phase")
    if _PHASE_INDEX[phase_name] < _PHASE_INDEX[str(row.phase)]:
      raise TrainingRepositoryError("training phase cannot move backwards")
    try:
      completed = int(completed_units)
      total = row.total_units if total_units is None else int(total_units)
    except (TypeError, ValueError, OverflowError) as exc:
      raise TrainingRepositoryError("training progress units are invalid") from exc
    if (
      completed < int(row.completed_units)
      or total < int(row.total_units)
      or total < 0
      or completed < 0
      or completed > total
    ):
      raise TrainingRepositoryError("training progress units must be monotonic and bounded")
    row.phase = phase_name
    row.completed_units = completed
    row.total_units = total
    row.state_version += 1
    await self.db.commit()
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def request_cancel(
    self,
    run_id: str,
    *,
    expected_state_version: int,
    idempotency_key: str,
    now: datetime | None = None,
  ) -> StockSelectionTrainingRun:
    cancel_key = str(idempotency_key or "").strip()
    if not cancel_key or len(cancel_key) > 160:
      raise TrainingRepositoryError(
        "cancel idempotency_key must be non-empty and at most 160 characters"
      )
    row = await self._locked_run(run_id)
    if row is None:
      raise TrainingNotFound("training run does not exist")
    if row.cancel_idempotency_key is not None:
      if row.cancel_idempotency_key == cancel_key:
        return _normalize_row_timestamps(row)
      raise TrainingRepositoryError("a different cancellation idempotency key was already recorded")
    timestamp = _as_utc(now or _utcnow(), "now")
    if row.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
      # Persist the request key even when the terminal state wins the race.
      # A retry with the same key is then a true idempotent no-op, while a
      # different key is rejected instead of silently creating another fact.
      self._assert_version(row, expected_state_version)
      row.cancel_idempotency_key = cancel_key
      row.cancel_requested_at = row.cancel_requested_at or timestamp
      row.state_version += 1
      await self.db.commit()
      await self.db.refresh(row)
      return _normalize_row_timestamps(row)
    self._assert_version(row, expected_state_version)
    if row.cancel_requested_at is not None:
      row.cancel_idempotency_key = cancel_key
      row.state_version += 1
      await self.db.commit()
      await self.db.refresh(row)
      return _normalize_row_timestamps(row)
    row.cancel_idempotency_key = cancel_key
    row.cancel_requested_at = timestamp
    if row.status == "QUEUED":
      row.status = "CANCELLED"
      row.completed_at = timestamp
      row.error_code = "CANCELLED_BY_USER"
      row.error_message = None
      row.artifact_manifest_sha256 = None
      row.run_key = None
    row.state_version += 1
    await self.db.commit()
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def mark_cancelled(
    self,
    run_id: str,
    *,
    expected_flow_run_id: str,
    completed_at: datetime | None = None,
    error_code: str | None = "CANCELLED_BY_USER",
    error_message: str | None = None,
    expected_state_version: int | None = None,
  ) -> StockSelectionTrainingRun:
    row = await self._locked_run(run_id)
    if row is None:
      raise TrainingNotFound("training run does not exist")
    self._assert_execution_owner(row, expected_flow_run_id)
    timestamp = _as_utc(completed_at or _utcnow(), "completed_at")
    if row.status == "CANCELLED":
      if (
        (error_code is None or row.error_code == str(error_code)[:64])
        and (error_message is None or row.error_message == _safe_error_message(error_message))
      ):
        return _normalize_row_timestamps(row)
      raise TrainingRepositoryError("cancelled terminal fact conflicts")
    if row.status in {"SUCCEEDED", "FAILED"}:
      raise TrainingRepositoryError("terminal training run cannot be cancelled")
    self._assert_version(row, expected_state_version)
    row.status = "CANCELLED"
    row.completed_at = timestamp
    row.cancel_requested_at = row.cancel_requested_at or timestamp
    row.artifact_manifest_sha256 = None
    row.run_key = None
    row.error_code = str(error_code or "CANCELLED")[:64]
    row.error_message = _safe_error_message(error_message) if error_message else None
    row.state_version += 1
    await self.db.commit()
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def record_artifact_bundle(
    self, run_id: str, *, expected_flow_run_id: str, bundle: TrainingBundle,
  ) -> StockSelectionTrainingRun:
    """Record a read-back-verified remote inventory under the execution fence."""
    bundle = TrainingBundle.model_validate(bundle.model_dump(mode="json"))
    if bundle.kind != "RESULT" or bundle.source_id != run_id:
      raise TrainingRepositoryError("artifact bundle does not identify this training result")
    if not any(entry.path == "manifest.json" for entry in bundle.files):
      raise TrainingRepositoryError("artifact bundle has no result manifest")
    payload = bundle.model_dump(mode="json")
    row = await self._locked_run(run_id)
    if row is None:
      raise TrainingNotFound("training run does not exist")
    self._assert_execution_owner(row, expected_flow_run_id)
    if row.artifact_bundle is not None:
      previous = TrainingBundle.model_validate(row.artifact_bundle)
      if previous.bundle_id != bundle.bundle_id:
        raise TrainingStateConflict("published training artifact inventory conflicts")
      if row.status in {"RUNNING", "SUCCEEDED"} and row.cancel_requested_at is None:
        return _normalize_row_timestamps(row)
    if row.status != "RUNNING" or row.cancel_requested_at is not None:
      raise TrainingStateConflict("training publication no longer owns an active run")
    row.artifact_bundle = payload
    await self.db.commit()
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def complete_run(
    self,
    run_id: str,
    *,
    expected_flow_run_id: str,
    run_key: str,
    artifact_manifest_sha256: str,
    environment_evidence: Mapping[str, Any] | None = None,
    metrics_summary: Mapping[str, Any] | None = None,
    gate_summary: Mapping[str, Any] | None = None,
    completed_at: datetime | None = None,
    expected_state_version: int | None = None,
  ) -> StockSelectionTrainingRun:
    manifest = _validate_hash(artifact_manifest_sha256, "artifact_manifest_sha256")
    stable_key = str(run_key or "").strip()
    if not stable_key or len(stable_key) > 160:
      raise TrainingRepositoryError("successful run requires a stable run_key")
    evidence_values = {
      "environment_evidence": environment_evidence or {},
      "metrics_summary": metrics_summary or {},
      "gate_summary": gate_summary or {},
    }
    for field, value in evidence_values.items():
      if not isinstance(value, Mapping):
        raise TrainingRepositoryError(f"{field} must be a mapping")
      _validate_json(value, field)
    row = await self._locked_run(run_id)
    if row is None:
      raise TrainingNotFound("training run does not exist")
    self._assert_execution_owner(row, expected_flow_run_id)
    if row.artifact_bundle is not None:
      bundle = TrainingBundle.model_validate(row.artifact_bundle)
      if not any(entry.path == "manifest.json" and entry.sha256 == manifest for entry in bundle.files):
        raise TrainingStateConflict("success manifest differs from published artifact bundle")
    if row.status == "SUCCEEDED":
      facts = (
        row.run_key == stable_key,
        row.artifact_manifest_sha256 == manifest,
        _same_fact(row.environment_evidence, dict(environment_evidence or {})),
        _same_fact(row.metrics_summary, dict(metrics_summary or {})),
        _same_fact(row.gate_summary, dict(gate_summary or {})),
      )
      if all(facts):
        return _normalize_row_timestamps(row)
      raise TrainingRepositoryError("successful terminal fact conflicts")
    if row.status in {"FAILED", "CANCELLED"}:
      raise TrainingRepositoryError("failed or cancelled training run cannot succeed")
    if row.status != "RUNNING":
      raise TrainingRepositoryError("only RUNNING runs can succeed")
    if row.cancel_requested_at is not None:
      raise TrainingRepositoryError("cancelled training run cannot succeed")
    self._assert_version(row, expected_state_version)
    timestamp = _as_utc(completed_at or _utcnow(), "completed_at")
    row.status = "SUCCEEDED"
    row.phase = "ARTIFACT_PUBLISH"
    row.completed_units = max(int(row.completed_units), int(row.total_units))
    row.total_units = max(int(row.total_units), int(row.completed_units))
    row.completed_at = timestamp
    row.run_key = stable_key
    row.artifact_manifest_sha256 = manifest
    row.environment_evidence = dict(evidence_values["environment_evidence"])
    row.metrics_summary = dict(evidence_values["metrics_summary"])
    row.gate_summary = dict(evidence_values["gate_summary"])
    row.error_code = None
    row.error_message = None
    row.state_version += 1
    await self.db.commit()
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def fail_run(
    self,
    run_id: str,
    *,
    expected_flow_run_id: str,
    error_code: str,
    error_message: str,
    environment_evidence: Mapping[str, Any] | None = None,
    metrics_summary: Mapping[str, Any] | None = None,
    gate_summary: Mapping[str, Any] | None = None,
    completed_at: datetime | None = None,
    expected_state_version: int | None = None,
  ) -> StockSelectionTrainingRun:
    code = str(error_code or "TRAINING_FAILED")[:64]
    message = _safe_error_message(error_message)
    evidence_values = {
      "environment_evidence": environment_evidence or {},
      "metrics_summary": metrics_summary or {},
      "gate_summary": gate_summary or {},
    }
    for field, value in evidence_values.items():
      if not isinstance(value, Mapping):
        raise TrainingRepositoryError(f"{field} must be a mapping")
      _validate_json(value, field)
    row = await self._locked_run(run_id)
    if row is None:
      raise TrainingNotFound("training run does not exist")
    self._assert_execution_owner(row, expected_flow_run_id)
    if row.status == "FAILED":
      facts = (
        row.error_code == code,
        row.error_message == message,
        _same_fact(row.environment_evidence, dict(environment_evidence or {})),
        _same_fact(row.metrics_summary, dict(metrics_summary or {})),
        _same_fact(row.gate_summary, dict(gate_summary or {})),
      )
      if all(facts):
        return _normalize_row_timestamps(row)
      raise TrainingRepositoryError("failed terminal fact conflicts")
    if row.status in {"SUCCEEDED", "CANCELLED"}:
      raise TrainingRepositoryError("successful or cancelled run cannot fail")
    self._assert_version(row, expected_state_version)
    timestamp = _as_utc(completed_at or _utcnow(), "completed_at")
    row.status = "FAILED"
    row.completed_at = timestamp
    row.artifact_manifest_sha256 = None
    row.run_key = None
    row.environment_evidence = dict(evidence_values["environment_evidence"])
    row.metrics_summary = dict(evidence_values["metrics_summary"])
    row.gate_summary = dict(evidence_values["gate_summary"])
    row.error_code = code
    row.error_message = message
    row.state_version += 1
    await self.db.commit()
    await self.db.refresh(row)
    return _normalize_row_timestamps(row)

  async def _locked_run(self, run_id: str) -> StockSelectionTrainingRun | None:
    result = await self.db.execute(
      select(StockSelectionTrainingRun)
      .where(StockSelectionTrainingRun.run_id == str(run_id))
      .with_for_update()
      .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()

  @staticmethod
  def _assert_execution_owner(row: StockSelectionTrainingRun, expected: str) -> None:
    if not expected or row.prefect_flow_run_id != expected:
      raise TrainingStateConflict("training execution ownership lost")

  @staticmethod
  def _assert_version(row: StockSelectionTrainingRun, expected: int | None) -> None:
    if expected is not None and int(row.state_version) != int(expected):
      raise TrainingStateConflict("training run state version conflict")

  # ---------------------------------------------------------------------
  # Read-only comparison
  # ---------------------------------------------------------------------
  async def comparison(self, run_ids: Sequence[str]) -> dict[str, Any]:
    ids = tuple(dict.fromkeys(str(value) for value in run_ids if str(value)))
    if len(ids) < 2:
      raise TrainingRepositoryError("comparison requires at least two runs")
    result = await self.db.execute(
      select(StockSelectionTrainingRun).where(StockSelectionTrainingRun.run_id.in_(ids))
    )
    runs = [_normalize_row_timestamps(row) for row in result.scalars().all()]
    if len(runs) != len(ids):
      missing = sorted(set(ids) - {str(row.run_id) for row in runs})
      raise TrainingNotFound(f"training run does not exist: {', '.join(missing)}")
    if any(row.status != "SUCCEEDED" or row.run_kind != "FINAL_EVALUATION" for row in runs):
      return {
        "comparable": False,
        "mismatch_fields": ["status_or_run_kind"],
        "mismatched_fields": {"status_or_run_kind": [row.status for row in runs]},
        "runs": runs,
      }
    specs_result = await self.db.execute(
      select(StockSelectionTrainingSpec).where(
        StockSelectionTrainingSpec.spec_id.in_([row.spec_id for row in runs])
      )
    )
    specs = {str(row.spec_id): row for row in specs_result.scalars().all()}
    coordinates = {
      "dataset_version",
      "split_spec",
      "evaluation_spec",
      "coordinate_hash",
      "experiment_group_hash",
    }
    mismatches: dict[str, list[Any]] = {}
    for field in sorted(coordinates):
      values = [_canonical(_row_value(specs.get(str(run.spec_id)), field)) for run in runs]
      if any(value != values[0] for value in values[1:]):
        mismatches[field] = values
    return {
      "comparable": not mismatches,
      "mismatch_fields": list(mismatches),
      "mismatched_fields": mismatches,
      "runs": runs,
      "metrics": [dict(row.metrics_summary or {}) for row in runs] if not mismatches else [],
      "gates": [dict(row.gate_summary or {}) for row in runs] if not mismatches else [],
    }


__all__ = [
  "COMPONENT_NAME",
  "HEARTBEAT_MAX_AGE_SECONDS",
  "StockSelectionTrainingRepository",
  "TrainingNotFound",
  "TrainingRepositoryError",
  "TrainingStateConflict",
]
