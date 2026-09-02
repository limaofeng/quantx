"""Authoritative, cross-source research lifecycle run index.

The index is intentionally assembled at the API boundary.  Offline research
artifacts are bounded filesystem reads, while next-day selection training runs
come from a joined database query.  Neither source is paginated before the
merge: the final sort, duplicate suppression, total, and offset/limit are
global properties of this connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from anyio import to_thread
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
)

from .research_artifacts import ResearchArtifactStore, ResearchRunRecord

NEXT_DAY_SELECTION_STUDY_ID = "next-day-selection"
_STUDY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_SEARCH_LENGTH = 128
_MAX_LIMIT = 100
_MAX_OFFSET = 100_000

_TRAINING_STAGES = frozenset({"DEVELOPMENT", "FINAL_EVALUATION"})
_ALL_STAGES = frozenset({"RESEARCH", *_TRAINING_STAGES})
_ALL_STATUSES = frozenset(
  {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}
)


class ResearchLifecycleIndexError(ValueError):
  """A source could not be read without exposing implementation details."""


@dataclass(frozen=True)
class ResearchLifecycleArtifactRecord:
  key: str
  version: str
  event_count: int | None
  elapsed_seconds: float | None
  config_hash: str | None
  has_metrics: bool
  artifact_errors: tuple[str, ...]


@dataclass(frozen=True)
class ResearchLifecycleTrainingRecord:
  run_key: str | None
  dataset_version: str | None
  requested_backend: str | None
  resolved_backend: str | None
  phase: str
  completed_units: int
  total_units: int
  conclusion: str | None
  registerable: bool
  can_start_final: bool
  queue_reason: str | None
  error_code: str | None
  error_message: str | None


@dataclass(frozen=True)
class ResearchLifecycleRunRecord:
  id: str
  run_id: str
  study_id: str
  stage: str
  status: str
  requested_at: datetime | None
  started_at: datetime | None
  completed_at: datetime | None
  updated_at: datetime | None
  target: str
  artifact: ResearchLifecycleArtifactRecord | None = None
  training: ResearchLifecycleTrainingRecord | None = None


@dataclass(frozen=True)
class ResearchLifecycleQuery:
  """Validated resolver input used by the source and merge layer."""

  study_id: str | None = None
  stages: tuple[str, ...] | None = None
  statuses: tuple[str, ...] | None = None
  date_from: date | None = None
  date_to: date | None = None
  search: str | None = None


def validate_research_lifecycle_query(
  *,
  study_id: str | None = None,
  stages: Sequence[Any] | None = None,
  statuses: Sequence[Any] | None = None,
  date_from: date | None = None,
  date_to: date | None = None,
  search: str | None = None,
  limit: int = 50,
  offset: int = 0,
) -> ResearchLifecycleQuery:
  """Validate and normalize the public connection arguments."""

  if not 1 <= limit <= _MAX_LIMIT:
    raise ValueError("limit 必须在 1 到 100 之间")
  if not 0 <= offset <= _MAX_OFFSET:
    raise ValueError("offset 必须在 0 到 100000 之间")

  normalized_study_id: str | None = None
  if study_id is not None:
    normalized_study_id = str(study_id)
    if _STUDY_ID_PATTERN.fullmatch(normalized_study_id) is None:
      raise ValueError("studyId 格式无效")

  normalized_stages = _normalize_filter_values(
    stages,
    allowed=_ALL_STAGES,
    field="stages",
  )
  normalized_statuses = _normalize_filter_values(
    statuses,
    allowed=_ALL_STATUSES,
    field="statuses",
  )
  if date_from is not None and not isinstance(date_from, date):
    raise ValueError("dateFrom 格式无效")
  if date_to is not None and not isinstance(date_to, date):
    raise ValueError("dateTo 格式无效")
  if date_from is not None and date_to is not None and date_from > date_to:
    raise ValueError("dateFrom 不能晚于 dateTo")

  normalized_search = str(search).strip() if search is not None else None
  if normalized_search == "":
    normalized_search = None
  if normalized_search is not None and len(normalized_search) > _MAX_SEARCH_LENGTH:
    raise ValueError("search 不能超过 128 个字符")

  return ResearchLifecycleQuery(
    study_id=normalized_study_id,
    stages=normalized_stages,
    statuses=normalized_statuses,
    date_from=date_from,
    date_to=date_to,
    search=normalized_search,
  )


async def list_research_lifecycle_runs(
  query: ResearchLifecycleQuery,
) -> list[ResearchLifecycleRunRecord]:
  """Read, merge, filter, deduplicate, and globally sort lifecycle runs."""

  artifact_records: list[ResearchRunRecord] = []
  training_rows: list[tuple[Any, Any]] = []
  training_run_ids: set[str] = set()
  try:
    if _should_read_artifacts(query):
      artifact_records = await to_thread.run_sync(
        lambda: ResearchArtifactStore().list_runs_for_index()
      )

    if _should_read_training(query) or _should_read_training_identities(query):
      async with AsyncSessionLocal() as db:
        repository = StockSelectionTrainingRepository(db)
        if _should_read_training(query):
          training_rows = await repository.list_runs_for_index(
            statuses=query.statuses,
            run_kinds=_training_run_kinds(query.stages),
            date_from=query.date_from,
            date_to=query.date_to,
            search=query.search,
          )
        if _should_read_training_identities(query):
          training_run_ids = {
            str(run_id)
            for run_id in await repository.list_run_ids_for_index()
            if run_id is not None
          }
  except ResearchLifecycleIndexError:
    raise
  except Exception as exc:
    raise ResearchLifecycleIndexError("研究运行索引读取失败") from exc

  artifact_pairs = [
    (source, _artifact_record(source))
    for source in artifact_records
  ]
  # Dedupe against the complete authoritative DB identity set before applying
  # the request's stage/status/date/search filter.  Otherwise a filtered-out
  # DB row could make its stale next-day-selection artifact visible again.
  artifact_pairs = [
    (source, item)
    for source, item in artifact_pairs
    if not (
      item.target == "RESEARCH_EVIDENCE"
      and item.study_id == NEXT_DAY_SELECTION_STUDY_ID
      and item.run_id in training_run_ids
    )
  ]
  records = [
    item
    for source, item in artifact_pairs
    if _matches_artifact(source, query)
  ]
  training_records = [
    _training_record(run, spec)
    for run, spec in training_rows
    if _matches_training(run, spec, query)
  ]

  records.extend(training_records)
  records.sort(key=_sort_key)
  return records


def paginate_research_lifecycle_runs(
  records: Sequence[ResearchLifecycleRunRecord],
  *,
  limit: int,
  offset: int,
) -> tuple[list[ResearchLifecycleRunRecord], int]:
  """Apply the one global offset/limit after merge and duplicate suppression."""

  return list(records[offset : offset + limit]), len(records)


def _normalize_filter_values(
  values: Sequence[Any] | None,
  *,
  allowed: frozenset[str],
  field: str,
) -> tuple[str, ...] | None:
  if values is None:
    return None
  normalized = tuple(
    str(getattr(value, "value", value)).strip().upper()
    for value in values
  )
  if any(value not in allowed for value in normalized if value):
    raise ValueError(f"{field} 包含不支持的值")
  # An empty list is equivalent to an omitted optional filter.  This keeps the
  # connection useful for typed clients that always send array variables.
  return tuple(value for value in normalized if value) or None


def _should_read_artifacts(query: ResearchLifecycleQuery) -> bool:
  if query.stages is not None and "RESEARCH" not in query.stages:
    return False
  if query.statuses is not None and not (
    {"SUCCEEDED", "FAILED"}.intersection(query.statuses)
  ):
    return False
  return True


def _should_read_training(query: ResearchLifecycleQuery) -> bool:
  if query.study_id is not None and query.study_id != NEXT_DAY_SELECTION_STUDY_ID:
    return False
  if query.stages is not None and not _TRAINING_STAGES.intersection(query.stages):
    return False
  return True


def _should_read_training_identities(query: ResearchLifecycleQuery) -> bool:
  """Read all DB run IDs whenever artifacts could need authoritative dedupe."""

  if not _should_read_artifacts(query):
    return False
  return query.study_id in {None, NEXT_DAY_SELECTION_STUDY_ID}


def _training_run_kinds(stages: tuple[str, ...] | None) -> tuple[str, ...] | None:
  if stages is None:
    return None
  kinds = tuple(stage for stage in stages if stage in _TRAINING_STAGES)
  return kinds or None


def _artifact_record(record: ResearchRunRecord) -> ResearchLifecycleRunRecord:
  status = "SUCCEEDED" if _enum_text(record.status).lower() == "success" else "FAILED"
  return ResearchLifecycleRunRecord(
    id=f"artifact:{record.key}",
    run_id=record.run_id,
    study_id=record.study_id,
    stage="RESEARCH",
    status=status,
    requested_at=None,
    started_at=_as_utc(record.started_at),
    completed_at=_as_utc(record.completed_at),
    updated_at=_as_utc(record.completed_at or record.started_at),
    target="RESEARCH_EVIDENCE",
    artifact=ResearchLifecycleArtifactRecord(
      key=record.key,
      version=record.version,
      event_count=record.event_count,
      elapsed_seconds=record.elapsed_seconds,
      config_hash=record.config_hash,
      has_metrics=record.has_metrics,
      artifact_errors=tuple(
        error
        for item in record.artifact_errors
        if (error := _safe_error(item)) is not None
      ),
    ),
  )


def _training_record(run: Any, spec: Any) -> ResearchLifecycleRunRecord:
  run_id = str(_row_value(run, "run_id", ""))
  run_kind = _safe_enum(
    _row_value(run, "run_kind", "DEVELOPMENT"),
    set(_TRAINING_STAGES),
  ) or "DEVELOPMENT"
  status = _safe_enum(
    _row_value(run, "status", "QUEUED"),
    set(_ALL_STATUSES),
  ) or "QUEUED"
  run_key_value = _row_value(run, "run_key")
  run_key = str(run_key_value).strip() if run_key_value else None
  run_key = run_key or None
  artifact_manifest_sha256 = str(
    _row_value(run, "artifact_manifest_sha256", "") or ""
  ).lower()
  gates = _row_value(run, "gate_summary", {})
  if not isinstance(gates, Mapping):
    gates = {}
  conclusion = _safe_enum(gates.get("conclusion"), {"BLOCKED", "SHADOW_ELIGIBLE", "ACTIVE_ELIGIBLE"})
  registerable = bool(
    gates.get("registerable") is True
    and run_kind == "FINAL_EVALUATION"
    and status == "SUCCEEDED"
  )
  can_start_final = bool(
    run_kind == "DEVELOPMENT"
    and status == "SUCCEEDED"
    and run_key
    and _SHA256_PATTERN.fullmatch(artifact_manifest_sha256)
  )
  requested_at = _as_utc(_row_value(run, "requested_at"))
  started_at = _as_utc(_row_value(run, "started_at"))
  completed_at = _as_utc(_row_value(run, "completed_at"))
  cancel_requested_at = _as_utc(_row_value(run, "cancel_requested_at"))
  updated_at = completed_at or cancel_requested_at or started_at or requested_at
  return ResearchLifecycleRunRecord(
    id=f"training:{run_id}",
    run_id=run_id,
    study_id=NEXT_DAY_SELECTION_STUDY_ID,
    stage=run_kind,
    status=status,
    requested_at=requested_at,
    started_at=started_at,
    completed_at=completed_at,
    updated_at=updated_at,
    target="TRAINING_RUN",
    training=ResearchLifecycleTrainingRecord(
      run_key=run_key,
      dataset_version=_optional_text(_row_value(spec, "dataset_version")),
      requested_backend=_safe_enum(
        _row_value(spec, "requested_backend"),
        {"AUTO", "CPU", "GPU_REQUIRED"},
      ),
      resolved_backend=_safe_enum(
        _row_value(spec, "resolved_backend"),
        {"CPU", "LIGHTGBM_OPENCL_GPU"},
      ),
      phase=_safe_enum(
        _row_value(run, "phase", "PREFLIGHT"),
        {
          "PREFLIGHT",
          "DATASET_BUILD",
          "WALK_FORWARD",
          "FINAL_FIT",
          "CALIBRATION",
          "FROZEN_TEST",
          "ARTIFACT_PUBLISH",
        },
      )
      or "PREFLIGHT",
      completed_units=_safe_nonnegative_int(_row_value(run, "completed_units")),
      total_units=_safe_nonnegative_int(_row_value(run, "total_units")),
      conclusion=conclusion,
      registerable=registerable,
      can_start_final=can_start_final,
      queue_reason=_safe_error(
        _row_value(gates, "queue_reason") or _row_value(run, "queue_reason")
      ),
      error_code=_optional_bounded_text(
        _safe_error(_row_value(run, "error_code")),
        64,
      ),
      error_message=_safe_error(_row_value(run, "error_message")),
    ),
  )


def _matches_artifact(
  record: ResearchRunRecord,
  query: ResearchLifecycleQuery,
) -> bool:
  if query.study_id is not None and record.study_id != query.study_id:
    return False
  status = "SUCCEEDED" if _enum_text(record.status).lower() == "success" else "FAILED"
  if query.stages is not None and "RESEARCH" not in query.stages:
    return False
  if query.statuses is not None and status not in query.statuses:
    return False
  updated_at = _as_utc(record.completed_at or record.started_at)
  if not _matches_date(updated_at, query.date_from, query.date_to):
    return False
  if query.search is not None:
    haystack = _search_text(
      record.run_id,
      record.study_id,
      record.version,
      record.key,
    )
    if query.search.casefold() not in haystack:
      return False
  return True


def _matches_training(run: Any, spec: Any, query: ResearchLifecycleQuery) -> bool:
  if query.study_id is not None and query.study_id != NEXT_DAY_SELECTION_STUDY_ID:
    return False
  run_kind = _enum_text(_row_value(run, "run_kind", ""))
  status = _enum_text(_row_value(run, "status", ""))
  if query.stages is not None and run_kind not in query.stages:
    return False
  if query.statuses is not None and status not in query.statuses:
    return False
  updated_at = _as_utc(
    _row_value(run, "completed_at")
    or _row_value(run, "cancel_requested_at")
    or _row_value(run, "started_at")
    or _row_value(run, "requested_at")
  )
  if not _matches_date(updated_at, query.date_from, query.date_to):
    return False
  if query.search is not None:
    haystack = _search_text(
      _row_value(run, "run_id"),
      _row_value(run, "run_key"),
      _row_value(run, "parent_run_id"),
      NEXT_DAY_SELECTION_STUDY_ID,
      run_kind,
      _row_value(spec, "dataset_version"),
    )
    if query.search.casefold() not in haystack:
      return False
  return True


def _matches_date(
  timestamp: datetime | None,
  date_from: date | None,
  date_to: date | None,
) -> bool:
  if date_from is None and date_to is None:
    return True
  if timestamp is None:
    return False
  current = timestamp.astimezone(timezone.utc).date()
  return (date_from is None or current >= date_from) and (
    date_to is None or current <= date_to
  )


def _sort_key(record: ResearchLifecycleRunRecord) -> tuple[int, float, str]:
  if record.updated_at is None:
    return (1, 0.0, record.id)
  return (0, -record.updated_at.astimezone(timezone.utc).timestamp(), record.id)


def _search_text(*values: Any) -> str:
  return " ".join(str(value) for value in values if value is not None).casefold()


def _row_value(row: Any, name: str, default: Any = None) -> Any:
  if isinstance(row, Mapping):
    return row.get(name, default)
  return getattr(row, name, default)


def _as_utc(value: Any) -> datetime | None:
  if value is None:
    return None
  if isinstance(value, datetime):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
  if isinstance(value, str):
    try:
      parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
      return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
  return None


def _safe_enum(value: Any, allowed: set[str]) -> str | None:
  if value is None:
    return None
  normalized = _enum_text(value)
  return normalized if normalized in allowed else None


def _enum_text(value: Any) -> str:
  return str(getattr(value, "value", value)).strip().upper()


def _optional_text(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _optional_bounded_text(value: Any, maximum: int) -> str | None:
  text = _optional_text(value)
  return text[:maximum] if text else None


def _safe_nonnegative_int(value: Any) -> int:
  if isinstance(value, bool):
    return 0
  try:
    result = int(value or 0)
  except (TypeError, ValueError, OverflowError):
    return 0
  return max(0, result)


def _safe_error(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value)
  text = re.sub(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?<![A-Za-z0-9])\\\\[^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?<![A-Za-z0-9])/(?!/)[^\r\n]*", "[PATH]", text)
  text = re.sub(
    r"(?i)(password|secret|token|credential|api[_ -]?key)\s*[:=]\s*[^\s,;]+",
    r"\1=[REDACTED]",
    text,
  )
  return re.sub(r"[\r\n\t]+", " ", text).strip()[:256] or None
