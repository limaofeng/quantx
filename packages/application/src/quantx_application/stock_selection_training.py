"""Pure application use cases for web initiated next-day model training.

This module deliberately knows nothing about SQLAlchemy, Prefect, files, or
network services.  Infrastructure supplies ``StockSelectionTrainingPort`` and
Worker supplies the capability snapshot and durable state transitions.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence

from quantx_domain.stock_selection_training import (
  BackendResolutionError,
  build_training_time_split,
  resolve_backend,
  stable_json_sha256,
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[0-9]{6}\.(?:SH|SZ)$", re.IGNORECASE)
_FIXED_SPLIT = {
  "minimum_training_months": 30,
  "calibration_months": 6,
  "validation_months": 1,
  "frozen_test_months": 12,
}
_BACKENDS = {"AUTO", "CPU", "GPU_REQUIRED"}
_UNIVERSE_KINDS = {"ORDINARY_A_SHARE", "CERTIFIED_INDEX", "EXPLICIT"}
_PHASES = (
  "PREFLIGHT",
  "DATASET_BUILD",
  "WALK_FORWARD",
  "FINAL_FIT",
  "CALIBRATION",
  "FROZEN_TEST",
  "ARTIFACT_PUBLISH",
)


class TrainingApplicationError(ValueError):
  """A client-visible, deterministic training validation error."""


class TrainingBackendUnavailable(TrainingApplicationError):
  """GPU_REQUIRED cannot be satisfied by the current worker capability."""


class TrainingPreviewMismatch(TrainingApplicationError):
  """The submitted immutable configuration no longer matches its preview."""


class TrainingStateConflict(TrainingApplicationError):
  """An optimistic-concurrency request used a stale state version."""


class ContractMapping(dict[str, Any]):
  """Mapping result with attribute access for API adapters and tests."""

  def __getattr__(self, name: str) -> Any:
    try:
      return self[name]
    except KeyError as exc:
      raise AttributeError(name) from exc


class StockSelectionTrainingPort(Protocol):
  """Persistence boundary used by the pure application service."""

  async def get_dataset(self, dataset_version: str) -> Any: ...

  async def get_capability(self, *, now: datetime | None = None) -> Mapping[str, Any]: ...

  async def create_spec(self, values: Mapping[str, Any]) -> Any: ...

  async def create_run(self, values: Mapping[str, Any]) -> Any: ...

  async def create_development(
    self,
    spec_values: Mapping[str, Any],
    run_values: Mapping[str, Any],
  ) -> tuple[Any, Any, bool]: ...

  async def create_final_evaluation(
    self,
    spec_values: Mapping[str, Any],
    run_values: Mapping[str, Any],
  ) -> tuple[Any, Any, bool]: ...

  async def get_run(self, run_id: str) -> Any: ...

  async def get_spec(self, spec_id: str) -> Any: ...

  async def get_run_by_idempotency_key(self, idempotency_key: str) -> Any: ...

  async def request_cancel(
    self,
    run_id: str,
    *,
    expected_state_version: int,
    idempotency_key: str,
    now: datetime | None = None,
  ) -> Any: ...

  async def comparison(self, run_ids: Sequence[str]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class StockSelectionTrainingPreviewRequest:
  """User-editable values accepted by the preflight use case."""

  dataset_version: str
  date_start: date | None = None
  date_end: date | None = None
  requested_backend: str = "CPU"
  stock_codes: tuple[str, ...] | None = None
  benchmark_code: str = "000300.SH"
  minimum_listing_days: int = 252
  bootstrap_samples: int = 2000
  worker_batch_size: int = 100
  random_seed: int = 20260901
  note: str = ""
  split_spec: Mapping[str, Any] = field(default_factory=dict)
  model_spec: Mapping[str, Any] = field(default_factory=dict)
  evaluation_spec: Mapping[str, Any] = field(default_factory=dict)
  universe_spec: Mapping[str, Any] = field(default_factory=dict)
  created_by: str = ""
  preview_fingerprint: str = ""
  idempotency_key: str = ""

  @classmethod
  def from_value(
    cls,
    value: "StockSelectionTrainingPreviewRequest | Mapping[str, Any] | None" = None,
    **kwargs: Any,
  ) -> "StockSelectionTrainingPreviewRequest":
    if isinstance(value, cls):
      if not kwargs:
        return value
      raw = value.to_dict()
    elif value is None:
      raw = {}
    elif isinstance(value, Mapping):
      raw = dict(value)
    else:
      raise TypeError("training preview request must be a mapping")
    raw.update(kwargs)
    allowed_fields = {
      "dataset_version",
      "date_start",
      "date_end",
      "requested_backend",
      "stock_codes",
      "benchmark_code",
      "minimum_listing_days",
      "bootstrap_samples",
      "worker_batch_size",
      "random_seed",
      "note",
      "split_spec",
      "model_spec",
      "evaluation_spec",
      "universe_spec",
      "created_by",
      "preview_fingerprint",
      "idempotency_key",
    }
    unknown = set(raw) - allowed_fields
    if unknown:
      raise ValueError(
        "training preview request contains unsupported fields: "
        + ", ".join(sorted(map(str, unknown)))
      )
    universe = raw.get("universe_spec")
    if universe is None:
      universe = {}
    elif not isinstance(universe, Mapping):
      raise ValueError("universe_spec must be a mapping")
    else:
      universe = dict(universe)
    start = raw.get("date_start")
    end = raw.get("date_end")
    codes = raw.get("stock_codes")
    if codes is None:
      codes = universe.get("stock_codes")
    if codes is not None and not isinstance(codes, (list, tuple, set)):
      raise ValueError("stock_codes must be a list")
    split = raw.get("split_spec")
    if split is None:
      split = {}
    elif not isinstance(split, Mapping):
      raise ValueError("split_spec must be a mapping")
    else:
      split = dict(split)
    model = raw.get("model_spec")
    if model is None:
      model = {}
    elif not isinstance(model, Mapping):
      raise ValueError("model_spec must be a mapping")
    else:
      model = dict(model)
    evaluation = raw.get("evaluation_spec")
    if evaluation is None:
      evaluation = {}
    elif not isinstance(evaluation, Mapping):
      raise ValueError("evaluation_spec must be a mapping")
    else:
      evaluation = dict(evaluation)
    if not evaluation:
      evaluation = {"bootstrap_samples": raw.get("bootstrap_samples", 2000)}
    requested = raw.get("requested_backend", "CPU")
    return cls(
      dataset_version=str(raw.get("dataset_version") or "").strip(),
      date_start=_parse_date(start),
      date_end=_parse_date(end),
      requested_backend=str(requested or "CPU").upper(),
      stock_codes=tuple(str(code).strip().upper() for code in codes) if codes is not None else None,
      benchmark_code=str(
        raw.get("benchmark_code", universe.get("benchmark_code", "000300.SH"))
        or ""
      ).strip().upper(),
      minimum_listing_days=int(
        raw.get("minimum_listing_days", universe.get("minimum_listing_days", 252))
      ),
      bootstrap_samples=int(
        raw.get("bootstrap_samples", evaluation.get("bootstrap_samples", 2000))
      ),
      worker_batch_size=int(
        raw.get("worker_batch_size", 100)
      ),
      random_seed=int(raw.get("random_seed", 20260901)),
      note=str(raw.get("note") or ""),
      split_spec=split,
      model_spec=dict(model) if isinstance(model, Mapping) else {},
      evaluation_spec=dict(evaluation),
      universe_spec=universe,
      created_by=str(raw.get("created_by") or "").strip(),
      preview_fingerprint=str(raw.get("preview_fingerprint", "") or "").strip().lower(),
      idempotency_key=str(raw.get("idempotency_key", "") or "").strip(),
    )

  def to_dict(self) -> dict[str, Any]:
    return {
      "dataset_version": self.dataset_version,
      "date_start": self.date_start.isoformat() if self.date_start else None,
      "date_end": self.date_end.isoformat() if self.date_end else None,
      "requested_backend": self.requested_backend,
      "stock_codes": list(self.stock_codes) if self.stock_codes is not None else None,
      "benchmark_code": self.benchmark_code,
      "minimum_listing_days": self.minimum_listing_days,
      "bootstrap_samples": self.bootstrap_samples,
      "worker_batch_size": self.worker_batch_size,
      "random_seed": self.random_seed,
      "note": self.note,
      "split_spec": dict(self.split_spec),
      "model_spec": dict(self.model_spec),
      "evaluation_spec": dict(self.evaluation_spec),
      "universe_spec": dict(self.universe_spec),
      "created_by": self.created_by,
      "preview_fingerprint": self.preview_fingerprint,
      "idempotency_key": self.idempotency_key,
    }


@dataclass(frozen=True)
class StartFinalEvaluationRequest:
  parent_run_id: str
  idempotency_key: str
  created_by: str

  @classmethod
  def from_value(
    cls,
    value: "StartFinalEvaluationRequest | Mapping[str, Any] | None" = None,
    **kwargs: Any,
  ) -> "StartFinalEvaluationRequest":
    raw = value.to_dict() if isinstance(value, cls) else dict(value or {})
    raw.update(kwargs)
    unknown = set(raw) - {"parent_run_id", "idempotency_key", "created_by"}
    if unknown:
      raise ValueError(
        "final evaluation request contains unsupported fields: "
        + ", ".join(sorted(map(str, unknown)))
      )
    return cls(
      parent_run_id=str(raw.get("parent_run_id", "") or "").strip(),
      idempotency_key=str(raw.get("idempotency_key", "") or "").strip(),
      created_by=str(raw.get("created_by") or "").strip(),
    )

  def to_dict(self) -> dict[str, str]:
    return {
      "parent_run_id": self.parent_run_id,
      "idempotency_key": self.idempotency_key,
      "created_by": self.created_by,
    }


@dataclass(frozen=True)
class CancelStockSelectionTrainingRequest:
  run_id: str
  expected_state_version: int
  idempotency_key: str

  @classmethod
  def from_value(
    cls,
    value: "CancelStockSelectionTrainingRequest | Mapping[str, Any] | None" = None,
    **kwargs: Any,
  ) -> "CancelStockSelectionTrainingRequest":
    raw = value.to_dict() if isinstance(value, cls) else dict(value or {})
    raw.update(kwargs)
    unknown = set(raw) - {"run_id", "expected_state_version", "idempotency_key"}
    if unknown:
      raise ValueError(
        "cancel request contains unsupported fields: "
        + ", ".join(sorted(map(str, unknown)))
      )
    return cls(
      run_id=str(raw.get("run_id", "") or "").strip(),
      expected_state_version=int(
        raw.get("expected_state_version", 0)
      ),
      idempotency_key=str(raw.get("idempotency_key", "") or "").strip(),
    )

  def to_dict(self) -> dict[str, Any]:
    return {
      "run_id": self.run_id,
      "expected_state_version": self.expected_state_version,
      "idempotency_key": self.idempotency_key,
    }


class StockSelectionTrainingApplication:
  """Preview, submit, cancel, and compare training runs."""

  def __init__(self, port: StockSelectionTrainingPort):
    self.port = port

  async def preview(
    self,
    request: StockSelectionTrainingPreviewRequest | Mapping[str, Any] | None = None,
    **kwargs: Any,
  ) -> ContractMapping:
    value = StockSelectionTrainingPreviewRequest.from_value(request, **kwargs)
    dataset = await self.port.get_dataset(value.dataset_version)
    if dataset is None or str(_value(dataset, "status", "CERTIFIED")).upper() != "CERTIFIED":
      raise TrainingApplicationError("dataset is not certified")
    if not value.dataset_version:
      raise TrainingApplicationError("dataset_version is required")
    capability = await self.port.get_capability(now=datetime.now(timezone.utc))
    if capability is None:
      capability = {
        "status": "CPU_AVAILABLE",
        "gpu_status": "GPU_UNAVAILABLE_RUNTIME",
        "fresh": False,
      }
    result = _build_preview(value, dataset, capability)
    return ContractMapping(result)

  async def start_development(
    self,
    request: StockSelectionTrainingPreviewRequest | Mapping[str, Any] | None = None,
    **kwargs: Any,
  ) -> ContractMapping:
    value = StockSelectionTrainingPreviewRequest.from_value(request, **kwargs)
    if not value.idempotency_key:
      raise TrainingApplicationError("idempotency_key is required")
    if len(value.idempotency_key) > 160:
      raise TrainingApplicationError("idempotency_key is too long")
    if not value.created_by:
      raise TrainingApplicationError("created_by is required")
    preview = await self.preview(value)
    if not value.preview_fingerprint:
      raise TrainingApplicationError("preview_fingerprint is required")
    if value.preview_fingerprint.lower() != preview["preview_fingerprint"]:
      raise TrainingPreviewMismatch("preview fingerprint no longer matches configuration")
    if preview["blockers"]:
      raise TrainingApplicationError(
        "training preview is blocked: " + ", ".join(preview["blockers"])
      )
    existing = await self.port.get_run_by_idempotency_key(value.idempotency_key)
    if existing is not None:
      existing_spec = await _spec_for_run(self.port, existing)
      if (
        str(_value(existing, "run_kind", "")).upper() != "DEVELOPMENT"
        or str(_value(existing_spec, "spec_hash", "")) != preview["spec_payload"]["spec_hash"]
      ):
        raise TrainingApplicationError("idempotency key is already bound to another payload")
      return ContractMapping(
        {
          "run": existing,
          "spec": existing_spec,
          "preview": preview,
          "idempotent": True,
        }
      )
    spec_payload = dict(preview["spec_payload"])
    spec_payload.update(
      {
        "spec_id": str(uuid.uuid4()),
        "run_kind": "DEVELOPMENT",
        "created_by": value.created_by[:64],
        "frozen_test_access_count": 0,
      }
    )
    run_payload = _new_run_payload(
      spec_id=spec_payload["spec_id"],
      run_kind="DEVELOPMENT",
      idempotency_key=value.idempotency_key,
      requested_at=datetime.now(timezone.utc),
    )
    spec, run, idempotent = await self.port.create_development(
      spec_payload,
      run_payload,
    )
    return ContractMapping(
      {"run": run, "spec": spec, "preview": preview, "idempotent": idempotent}
    )

  async def start_final(
    self,
    request: StartFinalEvaluationRequest | Mapping[str, Any] | None = None,
    **kwargs: Any,
  ) -> ContractMapping:
    value = StartFinalEvaluationRequest.from_value(request, **kwargs)
    if not value.parent_run_id:
      raise TrainingApplicationError("parent_run_id is required")
    if not value.idempotency_key:
      raise TrainingApplicationError("idempotency_key is required")
    if len(value.idempotency_key) > 160:
      raise TrainingApplicationError("idempotency_key is too long")
    if not value.created_by:
      raise TrainingApplicationError("created_by is required")
    existing = await self.port.get_run_by_idempotency_key(value.idempotency_key)
    parent = await self.port.get_run(value.parent_run_id)
    if parent is None:
      raise TrainingApplicationError("parent development run does not exist")
    if str(_value(parent, "run_kind", "")).upper() != "DEVELOPMENT":
      raise TrainingApplicationError("final evaluation requires a DEVELOPMENT parent")
    if str(_value(parent, "status", "")).upper() != "SUCCEEDED":
      raise TrainingApplicationError("development parent must have SUCCEEDED")
    parent_run_key = str(_value(parent, "run_key", "") or "").strip()
    parent_manifest = str(_value(parent, "artifact_manifest_sha256", "") or "").lower()
    if not parent_run_key or not _HASH_RE.fullmatch(parent_manifest):
      raise TrainingApplicationError("development parent has incomplete research evidence")
    parent_spec = await _spec_for_run(self.port, parent)
    if parent_spec is None:
      raise TrainingApplicationError("development parent spec does not exist")
    if existing is not None:
      existing_parent = _value(existing, "parent_run_id", "")
      existing_spec = await _spec_for_run(self.port, existing)
      if (
        str(_value(existing, "run_kind", "")).upper() != "FINAL_EVALUATION"
        or str(existing_parent) != value.parent_run_id
        or str(_value(existing_spec, "experiment_group_hash", ""))
        != str(_value(parent_spec, "experiment_group_hash", ""))
        or str(_value(existing_spec, "spec_hash", ""))
        != str(_value(parent_spec, "spec_hash", ""))
        or str(_value(existing_spec, "coordinate_hash", ""))
        != str(_value(parent_spec, "coordinate_hash", ""))
      ):
        raise TrainingApplicationError("idempotency key is already bound to another payload")
      return ContractMapping(
        {
          "run": existing,
          "spec": existing_spec,
          "parent_run": parent,
          "parent_spec": parent_spec,
          "idempotent": True,
        }
      )
    spec_payload = {
      field: _value(parent_spec, field)
      for field in (
        "dataset_version",
        "universe_spec",
        "split_spec",
        "model_spec",
        "evaluation_spec",
        "requested_backend",
        "resolved_backend",
        "random_seed",
        "worker_batch_size",
        "note",
        "environment_requirement_hash",
        "coordinate_hash",
        "experiment_group_hash",
      )
    }
    spec_payload.update(
      {
        "spec_id": str(uuid.uuid4()),
        "run_kind": "FINAL_EVALUATION",
        # The repository assigns this under a transaction that locks the
        # experiment group's development coordinate.
        "frozen_test_access_count": 0,
        "created_by": value.created_by[:64],
      }
    )
    # FINAL is the same locked experiment semantic as DEVELOPMENT.  The
    # run kind belongs to the lifecycle row, not to the immutable spec hash.
    spec_payload["spec_hash"] = _value(parent_spec, "spec_hash")
    run_payload = _new_run_payload(
      spec_id=spec_payload["spec_id"],
      run_kind="FINAL_EVALUATION",
      parent_run_id=value.parent_run_id,
      idempotency_key=value.idempotency_key,
      requested_at=datetime.now(timezone.utc),
    )
    spec, run, idempotent = await self.port.create_final_evaluation(spec_payload, run_payload)
    if idempotent:
      existing_spec = await _spec_for_run(self.port, run)
      return ContractMapping(
        {
          "run": run,
          "spec": existing_spec,
          "parent_run": parent,
          "parent_spec": parent_spec,
          "idempotent": True,
        }
      )
    return ContractMapping(
      {
        "run": run,
        "spec": spec,
        "parent_run": parent,
        "parent_spec": parent_spec,
        "idempotent": False,
      }
    )

  async def cancel(
    self,
    request: CancelStockSelectionTrainingRequest | Mapping[str, Any] | None = None,
    **kwargs: Any,
  ) -> ContractMapping:
    value = CancelStockSelectionTrainingRequest.from_value(request, **kwargs)
    if not value.run_id:
      raise TrainingApplicationError("run_id is required")
    if value.expected_state_version < 1:
      raise TrainingApplicationError("expected_state_version must be positive")
    if not value.idempotency_key:
      raise TrainingApplicationError("idempotency_key is required")
    result = await self.port.request_cancel(
      value.run_id,
      expected_state_version=value.expected_state_version,
      idempotency_key=value.idempotency_key,
      now=datetime.now(timezone.utc),
    )
    return ContractMapping({"run": result})

  async def comparison(self, run_ids: Sequence[str]) -> ContractMapping:
    ids = tuple(dict.fromkeys(str(run_id).strip() for run_id in run_ids if str(run_id).strip()))
    if len(ids) < 2:
      raise TrainingApplicationError("comparison requires at least two runs")
    result = await self.port.comparison(ids)
    return ContractMapping(dict(result))


async def _spec_for_run(port: Any, run: Any) -> Any:
  spec_id = _value(run, "spec_id")
  if not spec_id:
    return None
  return await port.get_spec(str(spec_id))


def _value(item: Any, name: str, default: Any = None) -> Any:
  if isinstance(item, Mapping):
    return item.get(name, default)
  return getattr(item, name, default)


def _parse_date(value: Any) -> date | None:
  if value is None or value == "":
    return None
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  text = str(value).strip()
  try:
    return date.fromisoformat(text[:10])
  except ValueError as exc:
    raise ValueError(f"invalid date: {value}") from exc


def _canonical(value: Any) -> Any:
  if isinstance(value, datetime):
    return value.isoformat()
  if isinstance(value, date):
    return value.isoformat()
  if isinstance(value, Mapping):
    return {str(key): _canonical(item) for key, item in sorted(value.items())}
  if isinstance(value, (list, tuple, set)):
    return [_canonical(item) for item in value]
  return value


def _month_start(value: date) -> date:
  return date(value.year, value.month, 1)


def _month_date(value: Any) -> date:
  text = str(value).strip()
  try:
    return date.fromisoformat(f"{text[:7]}-01")
  except ValueError as exc:
    raise TrainingApplicationError(f"invalid domain training month: {value}") from exc


def _month_end(value: date) -> date:
  next_month = _add_month(_month_start(value), 1)
  return next_month - timedelta(days=1)


def _add_month(value: date, offset: int) -> date:
  index = value.year * 12 + value.month - 1 + int(offset)
  return date(index // 12, index % 12 + 1, 1)


def _months_between(start: date, end: date) -> list[date]:
  current = _month_start(start)
  result: list[date] = []
  while current <= end:
    result.append(current)
    current = _add_month(current, 1)
  return result


def _period(month: date, start: date, end: date) -> tuple[date, date]:
  return max(start, month), min(end, _month_end(month))


def _fixed_split_values(split: Mapping[str, Any]) -> dict[str, int]:
  unknown = set(split) - set(_FIXED_SPLIT)
  if unknown:
    raise TrainingApplicationError(
      "split_spec contains unsupported fields: " + ", ".join(sorted(map(str, unknown)))
    )
  normalized = dict(_FIXED_SPLIT)
  for raw_name, expected in _FIXED_SPLIT.items():
    if raw_name in split:
      try:
        actual = int(split[raw_name])
      except (TypeError, ValueError, OverflowError) as exc:
        raise TrainingApplicationError(f"{raw_name} must be fixed at {expected}") from exc
      if actual != expected:
        raise TrainingApplicationError(f"{raw_name} must be fixed at {expected}")
      normalized[raw_name] = actual
  return normalized


def _capability_value(capability: Any, name: str, default: Any = None) -> Any:
  return _value(capability, name, default)


def _normalized_universe(
  request: StockSelectionTrainingPreviewRequest,
  dataset: Any,
  stock_codes: Sequence[str] | None,
) -> dict[str, Any]:
  requested = dict(request.universe_spec)
  allowed_fields = {
    "kind",
    "index_code",
    "benchmark_code",
    "stock_codes",
    "minimum_listing_days",
  }
  unknown_requested = set(requested) - allowed_fields
  if unknown_requested:
    raise TrainingApplicationError(
      "universe_spec contains unsupported fields: "
      + ", ".join(sorted(map(str, unknown_requested)))
    )
  raw_kind = requested.get("kind")
  certified = _value(dataset, "universe_spec", {})
  certified = dict(certified) if isinstance(certified, Mapping) else {}
  unknown_certified = set(certified) - allowed_fields
  if unknown_certified:
    raise TrainingApplicationError(
      "certified dataset universe_spec contains unsupported fields: "
      + ", ".join(sorted(map(str, unknown_certified)))
    )
  certified_raw_kind = certified.get("kind")
  certified_kind = str(certified_raw_kind or "").upper()
  if certified_kind not in _UNIVERSE_KINDS:
    raise TrainingApplicationError(
      "certified dataset universe_spec.kind is missing or unsupported"
    )

  # An omitted kind means "use the certified dataset's complete universe".
  # A symbol list is then a permitted narrowing of that certified universe;
  # it does not silently turn an ordinary or index certification into an
  # explicit, independently certified universe.
  kind = str(raw_kind or certified_kind).upper()
  if kind not in _UNIVERSE_KINDS:
    raise TrainingApplicationError(
      "universe_spec.kind must be ORDINARY_A_SHARE, CERTIFIED_INDEX, or EXPLICIT"
    )
  certified_benchmark = str(certified.get("benchmark_code") or "").strip().upper()
  if certified_benchmark and certified_benchmark != request.benchmark_code:
    raise TrainingApplicationError(
      "benchmark_code must match the certified dataset universe"
    )
  try:
    certified_minimum = int(certified.get("minimum_listing_days", 252))
  except (TypeError, ValueError, OverflowError) as exc:
    raise TrainingApplicationError(
      "certified dataset minimum_listing_days is invalid"
    ) from exc
  if certified_minimum < 252 or request.minimum_listing_days < certified_minimum:
    raise TrainingApplicationError(
      "minimum_listing_days must not weaken the certified dataset universe"
    )
  output: dict[str, Any] = {
    "kind": kind,
    "index_code": None,
    "benchmark_code": request.benchmark_code,
    "minimum_listing_days": request.minimum_listing_days,
  }
  if kind == "EXPLICIT":
    if stock_codes is None or not stock_codes:
      raise TrainingApplicationError("EXPLICIT universe requires stock_codes")
    if certified_kind != "EXPLICIT":
      raise TrainingApplicationError(
        "EXPLICIT universe requires an explicitly certified dataset"
      )
    certified_codes = certified.get("stock_codes")
    if not isinstance(certified_codes, (list, tuple, set)) or not certified_codes:
      raise TrainingApplicationError(
        "EXPLICIT universe certification is missing stock_codes"
      )
    certified_set = {
      str(code).strip().upper() for code in certified_codes
    }
    requested_set = {str(code).strip().upper() for code in stock_codes}
    if not requested_set <= certified_set:
      raise TrainingApplicationError(
        "EXPLICIT universe contains stocks outside the certified dataset"
      )
    output["stock_codes"] = sorted(requested_set)
  elif kind == "ORDINARY_A_SHARE":
    if certified_kind != "ORDINARY_A_SHARE":
      raise TrainingApplicationError("certified dataset universe is not ORDINARY_A_SHARE")
    output["stock_codes"] = sorted(set(stock_codes)) if stock_codes is not None else None
  else:
    index_code = str(requested.get("index_code") or "").strip().upper()
    if not _CODE_RE.fullmatch(index_code):
      raise TrainingApplicationError("CERTIFIED_INDEX requires a valid index_code")
    certified_index = str(certified.get("index_code") or "").strip().upper()
    if certified_kind != "CERTIFIED_INDEX" or certified_index != index_code:
      raise TrainingApplicationError(
        "CERTIFIED_INDEX must match the certified point-in-time universe"
      )
    quality = _value(dataset, "quality_summary", {})
    quality = dict(quality) if isinstance(quality, Mapping) else {}
    point_in_time = certified.get("point_in_time")
    if point_in_time is None:
      point_in_time = certified.get("point_in_time_complete")
    if point_in_time is None:
      coverage = quality.get("coverage", {})
      if isinstance(coverage, Mapping):
        historical = coverage.get("historical_universe", {})
        if isinstance(historical, Mapping):
          point_in_time = historical.get("complete")
    if point_in_time is not True:
      raise TrainingApplicationError(
        "CERTIFIED_INDEX requires complete point-in-time universe evidence"
      )
    output["index_code"] = index_code
    output["stock_codes"] = None
  return output


def _resolve_backend(
  requested: str,
  capability: Any,
  *,
  sample_count: int,
  estimated_gpu_memory_mib: int,
) -> tuple[str, list[str], list[str]]:
  requested = str(requested or "").upper()
  if requested not in _BACKENDS:
    raise TrainingApplicationError("requested_backend must be AUTO, CPU, or GPU_REQUIRED")
  snapshot = dict(capability) if isinstance(capability, Mapping) else {}
  warnings: list[str] = []
  fresh = snapshot.get("fresh") is True
  raw_status = str(snapshot.get("status") or "").upper()
  if not fresh:
    effective_status = "GPU_UNAVAILABLE_RUNTIME"
    warnings.append("GPU_CAPABILITY_MISSING_OR_STALE")
  else:
    effective_status = raw_status or "GPU_UNQUALIFIED"
  qualification = snapshot.get("qualification")
  qualification = dict(qualification) if isinstance(qualification, Mapping) else {}
  gates = qualification.get("gates_passed")
  if gates is False or (isinstance(gates, (list, tuple, set)) and not all(gates)):
    effective_status = "GPU_UNQUALIFIED"
  qualification["status"] = effective_status
  available_memory = snapshot.get("available_memory_mib")
  try:
    memory_fraction = estimated_gpu_memory_mib / float(available_memory)
  except (TypeError, ValueError, ZeroDivisionError):
    memory_fraction = None
  minimum_samples = qualification.get("minimum_sample_count")
  try:
    minimum_samples = int(minimum_samples) if minimum_samples is not None else None
  except (TypeError, ValueError, OverflowError):
    minimum_samples = None
  try:
    decision = resolve_backend(
      requested,
      qualification,
      sample_count=sample_count,
      estimated_memory_fraction=memory_fraction,
      minimum_sample_count=minimum_samples,
    )
  except BackendResolutionError as exc:
    raise TrainingBackendUnavailable(str(exc)) from exc
  resolved = getattr(decision.resolved_backend, "value", decision.resolved_backend)
  resolved = str(resolved).upper()
  reason = str(getattr(decision, "reason", "") or "")
  if requested == "AUTO" and resolved == "CPU" and reason:
    warnings.append("AUTO_RESOLVED_TO_CPU: " + reason)
  return resolved, list(dict.fromkeys(warnings)), []


def _canonical_model_spec(value: Mapping[str, Any]) -> dict[str, Any]:
  """Materialize the one model preset accepted by Research."""

  defaults: dict[str, dict[str, Any]] = {
    "logistic": {
      "c_values": [0.1, 1.0, 10.0],
      "max_iter": 1000,
    },
    "lightgbm": {
      "num_leaves": [15, 31],
      "reg_lambda": [1.0, 5.0],
      "learning_rate": 0.03,
      "n_estimators": 500,
      "min_child_samples": 100,
      "subsample": 0.8,
      "colsample_bytree": 0.8,
      "max_bin": 63,
      "gpu_use_dp": False,
      "gpu_platform_id": None,
      "gpu_device_id": None,
    },
    "calibration": {
      "bins": 10,
      "isotonic_minimum_positives": 20_000,
      "isotonic_minimum_relative_brier_improvement": 0.01,
    },
    "candidate_gate": {
      "brier_skill_minimum": 0.0,
      "ece_maximum": 0.03,
      "minimum_probability": 0.6,
      "minimum_factor_completeness": 0.9,
      "minimum_valid_history": 252,
      "level_a_size": 20,
      "level_b_size": 30,
    },
  }
  if not isinstance(value, Mapping):
    raise TrainingApplicationError("model_spec must be a mapping")
  unknown = set(value) - set(defaults)
  if unknown:
    raise TrainingApplicationError(
      "model_spec contains unsupported fields: "
      + ", ".join(sorted(map(str, unknown)))
    )
  result: dict[str, Any] = {}
  for section, preset in defaults.items():
    supplied = value.get(section, {})
    if not isinstance(supplied, Mapping):
      raise TrainingApplicationError(f"model_spec.{section} must be a mapping")
    unknown_section = set(supplied) - set(preset)
    if unknown_section:
      raise TrainingApplicationError(
        f"model_spec.{section} contains unsupported fields: "
        + ", ".join(sorted(map(str, unknown_section)))
      )
    merged = dict(preset)
    merged.update(supplied)
    result[section] = merged
  return result


def _canonical_evaluation_spec(
  value: Mapping[str, Any], bootstrap_samples: int
) -> dict[str, Any]:
  """Materialize the strict evaluation preset without legacy aliases."""

  if not isinstance(value, Mapping):
    raise TrainingApplicationError("evaluation_spec must be a mapping")
  unknown = set(value) - {"bootstrap_samples"}
  if unknown:
    raise TrainingApplicationError(
      "evaluation_spec contains unsupported fields: "
      + ", ".join(sorted(map(str, unknown)))
    )
  if "bootstrap_samples" in value:
    try:
      supplied = int(value["bootstrap_samples"])
    except (TypeError, ValueError, OverflowError) as exc:
      raise TrainingApplicationError("evaluation_spec.bootstrap_samples is invalid") from exc
    if supplied != int(bootstrap_samples):
      raise TrainingApplicationError(
        "evaluation_spec.bootstrap_samples must match bootstrap_samples"
      )
  return {"bootstrap_samples": int(bootstrap_samples)}


def _build_preview(
  request: StockSelectionTrainingPreviewRequest,
  dataset: Any,
  capability: Any,
) -> dict[str, Any]:
  if not request.dataset_version:
    raise TrainingApplicationError("dataset_version is required")
  dataset_start = _parse_date(_value(dataset, "date_start"))
  dataset_end = _parse_date(_value(dataset, "date_end"))
  if dataset_start is None or dataset_end is None or dataset_start > dataset_end:
    raise TrainingApplicationError("certified dataset has invalid date bounds")
  start, end = request.date_start or dataset_start, request.date_end or dataset_end
  if start < dataset_start or end > dataset_end or start > end:
    raise TrainingApplicationError("requested date range is outside the certified dataset")
  split = _fixed_split_values(request.split_spec)
  if not _CODE_RE.fullmatch(request.benchmark_code):
    raise TrainingApplicationError("benchmark_code must use 000000.SH or 000000.SZ format")
  codes = request.stock_codes
  if codes is not None:
    if not codes or len(codes) > 5000:
      raise TrainingApplicationError("stock_codes must contain 1..5000 codes")
    if len(set(codes)) != len(codes):
      raise TrainingApplicationError("stock_codes must not contain duplicates")
    if any(not _CODE_RE.fullmatch(code) for code in codes):
      raise TrainingApplicationError("stock_codes contains an invalid A-share code")
  universe_payload = _normalized_universe(request, dataset, codes)
  if request.minimum_listing_days < 252:
    raise TrainingApplicationError("minimum_listing_days must be at least 252")
  if not 100 <= request.bootstrap_samples <= 20_000:
    raise TrainingApplicationError("bootstrap_samples must be between 100 and 20000")
  if not 1 <= request.worker_batch_size <= 1000:
    raise TrainingApplicationError("worker_batch_size must be between 1 and 1000")
  if len(request.note) > 500:
    raise TrainingApplicationError("note must be at most 500 characters")
  months = _months_between(start, end)
  try:
    domain_split = build_training_time_split(
      months,
      frozen_test_months=split["frozen_test_months"],
      minimum_training_months=split["minimum_training_months"],
      calibration_months=split["calibration_months"],
      validation_months=split["validation_months"],
    )
  except (TypeError, ValueError) as exc:
    raise TrainingApplicationError(str(exc)) from exc
  frozen_months = [_month_date(item) for item in domain_split.frozen_test_months]
  development_months = [_month_date(item) for item in domain_split.development_months]
  frozen_start, frozen_end = _period(frozen_months[0], start, end)
  development_start, development_end = _period(development_months[0], start, end)
  final_calibration_months = development_months[-split["calibration_months"] :]
  final_training_months = development_months[: -split["calibration_months"]]
  folds: list[dict[str, Any]] = []
  for fold in domain_split.folds:
    train_months = [_month_date(item) for item in fold.train_months]
    calibration_months = [_month_date(item) for item in fold.calibration_months]
    validation_month = _month_date(fold.validation_month)
    train_range = _range_from_months(train_months, start, end)
    calibration_range = _range_from_months(calibration_months, start, end)
    validation_range = _period(validation_month, start, end)
    folds.append(
      {
        "train_start": train_range[0].isoformat(),
        "train_end": train_range[1].isoformat(),
        "calibration_start": calibration_range[0].isoformat(),
        "calibration_end": calibration_range[1].isoformat(),
        "validation_start": validation_range[0].isoformat(),
        "validation_end": validation_range[1].isoformat(),
        "train_months": [item.isoformat() for item in train_months],
        "calibration_months": [item.isoformat() for item in calibration_months],
        "validation_month": validation_month.isoformat(),
      }
    )
  if not folds:
    raise TrainingApplicationError("requested range cannot form a walk-forward validation fold")
  source_quality = _value(dataset, "quality_summary", {})
  source_quality = dict(source_quality) if isinstance(source_quality, Mapping) else {}
  coverage, leakage, shadow_reasons = _quality_evidence(source_quality)
  sample_count = max(0, int(_value(dataset, "sample_count", 0) or 0))
  stock_count = max(0, int(_value(dataset, "stock_count", 0) or 0))
  trading_day_count = max(0, int(_value(dataset, "trading_day_count", 0) or 0))
  resource = _resource_estimate(
    sample_count=sample_count,
    stock_count=stock_count,
    trading_day_count=trading_day_count,
    worker_batch_size=request.worker_batch_size,
    fold_count=len(folds),
  )
  resolved_backend, warnings, blockers = _resolve_backend(
    request.requested_backend,
    capability,
    sample_count=sample_count,
    estimated_gpu_memory_mib=resource["gpu_memory_mib"],
  )
  indicator_version = str(_value(dataset, "indicator_version", ""))
  factor_set_version = str(_value(dataset, "factor_set_version", ""))
  factor_set_hash = str(_value(dataset, "factor_set_hash", "")).lower()
  label_version = str(_value(dataset, "label_version", ""))
  manifest_sha256 = str(_value(dataset, "manifest_sha256", "")).lower()
  split_payload = {
    "date_start": start.isoformat(),
    "date_end": end.isoformat(),
    "minimum_training_months": 30,
    "calibration_months": 6,
    "validation_months": 1,
    "frozen_test_months": 12,
    "development_start": development_start.isoformat(),
    "development_end": development_end.isoformat(),
    "frozen_test_start": frozen_start.isoformat(),
    "frozen_test_end": frozen_end.isoformat(),
    "final_training_start": _range_from_months(final_training_months, start, end)[0].isoformat(),
    "final_training_end": _range_from_months(final_training_months, start, end)[1].isoformat(),
    "final_calibration_start": _range_from_months(final_calibration_months, start, end)[0].isoformat(),
    "final_calibration_end": _range_from_months(final_calibration_months, start, end)[1].isoformat(),
    "folds": folds,
  }
  model_payload = _canonical_model_spec(request.model_spec)
  evaluation_payload = _canonical_evaluation_spec(
    request.evaluation_spec,
    request.bootstrap_samples,
  )
  semantic = {
    "dataset_version": request.dataset_version,
    "manifest_sha256": manifest_sha256,
    "date_start": start.isoformat(),
    "date_end": end.isoformat(),
    "indicator_version": indicator_version,
    "factor_set_version": factor_set_version,
    "factor_set_hash": factor_set_hash,
    "label_version": label_version,
    "universe_spec": universe_payload,
    "split_spec": split_payload,
    "model_spec": model_payload,
    "evaluation_spec": evaluation_payload,
    "requested_backend": request.requested_backend,
    "resolved_backend": resolved_backend,
    "random_seed": int(request.random_seed),
    "worker_batch_size": request.worker_batch_size,
  }
  capability_hash = str(_capability_value(capability, "environment_requirement_hash", "") or "").lower()
  if not _HASH_RE.fullmatch(capability_hash):
    capability_hash = stable_json_sha256(
      {
        "requested_backend": request.requested_backend,
        "resolved_backend": resolved_backend,
        "status": _capability_value(capability, "status", "CPU_AVAILABLE"),
        "qualification": _capability_value(capability, "qualification", {}),
      }
    )
  coordinate = {
    "manifest_sha256": manifest_sha256,
    "split_spec": split_payload,
    "universe_spec": universe_payload,
    "indicator_version": indicator_version,
    "factor_set_version": factor_set_version,
    "factor_set_hash": factor_set_hash,
    "label_version": label_version,
    "evaluation_spec": evaluation_payload,
    "evaluation_code_version": evaluation_payload.get("code_version", "next-day-selection-v1"),
  }
  coordinate_hash = stable_json_sha256(coordinate)
  spec_payload = {
    "dataset_version": request.dataset_version,
    "universe_spec": universe_payload,
    "run_kind": "DEVELOPMENT",
    "split_spec": split_payload,
    "model_spec": model_payload,
    "evaluation_spec": evaluation_payload,
    "requested_backend": request.requested_backend,
    "resolved_backend": resolved_backend,
    "random_seed": semantic["random_seed"],
    "worker_batch_size": request.worker_batch_size,
    "note": request.note,
    "spec_hash": stable_json_sha256(semantic),
    "environment_requirement_hash": capability_hash,
    "coordinate_hash": coordinate_hash,
    "experiment_group_hash": coordinate_hash,
    "frozen_test_access_count": 0,
    "created_by": request.created_by[:64],
  }
  fingerprint = stable_json_sha256(
    {
      "dataset_version": request.dataset_version,
      "manifest_sha256": manifest_sha256,
      "date_start": start.isoformat(),
      "date_end": end.isoformat(),
      "spec": {
        key: value for key, value in spec_payload.items() if key != "created_by"
      },
      "coverage": coverage,
      "leakage": leakage,
      "requested_backend": request.requested_backend,
      "resolved_backend": resolved_backend,
    }
  )
  return {
    "preview_fingerprint": fingerprint,
    "spec_payload": spec_payload,
    "folds": folds,
    "coverage": {
      "dataset_start": dataset_start.isoformat(),
      "dataset_end": dataset_end.isoformat(),
      "requested_start": start.isoformat(),
      "requested_end": end.isoformat(),
      "strict_non_overlap": True,
      "sample_count": sample_count,
      "stock_count": stock_count,
      "trading_day_count": trading_day_count,
      "frozen_test_start": frozen_start.isoformat(),
      "frozen_test_end": frozen_end.isoformat(),
      "quality": coverage,
    },
    "leakage": leakage,
    "shadow_reasons": shadow_reasons,
    "resource_estimate": resource,
    "requested_backend": request.requested_backend,
    "resolved_backend": resolved_backend,
    "warnings": list(dict.fromkeys(warnings)),
    "blockers": list(
      dict.fromkeys(
        [*blockers, *_quality_blockers(coverage, leakage)]
      )
    ),
    "capability": _canonical(dict(capability) if isinstance(capability, Mapping) else capability),
  }


def _range_from_months(months: Sequence[date], start: date, end: date) -> tuple[date, date]:
  if not months:
    raise TrainingApplicationError("empty training interval")
  return _period(months[0], start, end)[0], _period(months[-1], start, end)[1]


def _quality_evidence(summary: Mapping[str, Any]) -> tuple[Any, Any, list[Any]]:
  coverage = summary.get("coverage", {})
  leakage = summary.get("leakage_checks", {})
  reasons = summary.get("shadow_reasons", [])
  if isinstance(reasons, str):
    reasons = [reasons]
  elif not isinstance(reasons, (list, tuple, set)):
    reasons = []
  reasons = list(reasons)
  historical = coverage.get("historical_universe") if isinstance(coverage, Mapping) else None
  if isinstance(historical, Mapping) and historical.get("complete") is False:
    reasons.append("HISTORICAL_UNIVERSE_INCOMPLETE")
  return coverage, leakage, list(dict.fromkeys(reasons))


def _quality_blockers(coverage: Any, leakage: Any) -> list[str]:
  """Translate explicit failed quality evidence into stable blocker codes."""

  blockers: list[str] = []
  if isinstance(leakage, Mapping):
    if any(value is False for value in leakage.values()):
      blockers.append("DATA_LEAKAGE")
  elif leakage is False:
    blockers.append("DATA_LEAKAGE")
  if coverage is False or (
    isinstance(coverage, Mapping)
    and _required_coverage_failed(coverage)
  ):
    blockers.append("DATA_COVERAGE_INCOMPLETE")
  return blockers


def _required_coverage_failed(value: Any, key: str = "") -> bool:
  """Find explicit required-coverage failures without blocking shadow-only universes."""

  if key == "historical_universe":
    return False
  if value is False:
    return True
  if isinstance(value, Mapping):
    return any(
      _required_coverage_failed(child, str(name))
      for name, child in value.items()
    )
  if isinstance(value, (list, tuple, set)):
    return any(_required_coverage_failed(child, key) for child in value)
  return False


def _resource_estimate(
  *,
  sample_count: int,
  stock_count: int,
  trading_day_count: int,
  worker_batch_size: int,
  fold_count: int,
) -> dict[str, Any]:
  # The estimate is intentionally conservative and monotonic in dataset size;
  # it is a scheduling hint, never a reason to silently change a backend.
  memory = max(256, int(sample_count * 48 / 1024 / 1024) + 256)
  disk = max(64, int(sample_count * 96 / 1024 / 1024) + stock_count // 100 + 64)
  gpu = max(128, int(sample_count * 24 / 1024 / 1024) + 128)
  work = max(1, fold_count) * max(1, sample_count) / max(1, worker_batch_size)
  estimated_minutes = max(1, int(work / 250) + stock_count // 1000 + 1)
  level = "LOW" if estimated_minutes <= 10 else "MEDIUM" if estimated_minutes <= 60 else "HIGH"
  return {
    "memory_mib": memory,
    "estimated_memory_mib": memory,
    "disk_mib": disk,
    "estimated_disk_mib": disk,
    "gpu_memory_mib": gpu,
    "estimated_gpu_memory_mib": gpu,
    "estimated_minutes": estimated_minutes,
    "duration_level": level,
    "sample_count": sample_count,
    "stock_count": stock_count,
    "trading_day_count": trading_day_count,
    "fold_count": fold_count,
  }


def _new_run_payload(
  *,
  spec_id: str,
  run_kind: str,
  idempotency_key: str,
  requested_at: datetime,
  parent_run_id: str | None = None,
) -> dict[str, Any]:
  return {
    "run_id": str(uuid.uuid4()),
    "run_key": None,
    "spec_id": spec_id,
    "run_kind": run_kind,
    "parent_run_id": parent_run_id,
    "status": "QUEUED",
    "phase": "PREFLIGHT",
    "completed_units": 0,
    "total_units": 0,
    "prefect_flow_run_id": None,
    "requested_at": requested_at,
    "started_at": None,
    "completed_at": None,
    "cancel_requested_at": None,
    "artifact_manifest_sha256": None,
    "environment_evidence": {},
    "metrics_summary": {},
    "gate_summary": {},
    "error_code": None,
    "error_message": None,
    "state_version": 1,
    "idempotency_key": idempotency_key,
  }


__all__ = [
  "CancelStockSelectionTrainingRequest",
  "ContractMapping",
  "StartFinalEvaluationRequest",
  "StockSelectionTrainingApplication",
  "StockSelectionTrainingPort",
  "StockSelectionTrainingPreviewRequest",
  "TrainingApplicationError",
  "TrainingBackendUnavailable",
  "TrainingPreviewMismatch",
  "TrainingStateConflict",
]
