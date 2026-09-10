"""GraphQL boundary for model evidence and read-only probability candidates."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

import strawberry
from quantx_application.stock_selection_training import (
  StockSelectionTrainingApplication,
)
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.stock_selection import StockPredictionRun
from quantx_infrastructure.repositories.stock_selection_repository import (
  StockSelectionRepository,
)
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
)
from quantx_infrastructure.repositories.trainer_status_repository import TrainerStatusRepository
from sqlalchemy import select

from quantx_api.stock_selection_model_service import StockSelectionModelService

from ..security import principal_from_context
from ..types.stock_selection_types import (
  StockCandidateLevel,
  StockPredictionRunStatus,
  StockPredictionRunStatusPage,
  StockProbabilityCalibrationBucket,
  StockProbabilityCandidate,
  StockProbabilityCandidateInput,
  StockProbabilityCandidatePage,
  StockSelectionDatasetVersion,
  StockSelectionModel,
  StockSelectionModelStage,
  StockSelectionResolvedBackend,
  StockSelectionTrainingBackend,
  StockSelectionTrainingCapabilities,
  StockSelectionTrainerStatus,
  StockSelectionTrainerDispatch,
  StockSelectionTrainingComparison,
  StockSelectionTrainingConclusion,
  StockSelectionTrainingFold,
  StockSelectionTrainingGpuStatus,
  StockSelectionTrainingInput,
  StockSelectionTrainingPhase,
  StockSelectionTrainingPreview,
  StockSelectionTrainingResourceEstimate,
  StockSelectionTrainingRun,
  StockSelectionTrainingRunKind,
  StockSelectionTrainingRunPage,
  StockSelectionTrainingRunStatus,
)
from .research_preparation_schema import (
  ResearchPreparationMutation,
  ResearchPreparationQuery,
)


def _validate_candidate_input(input: StockProbabilityCandidateInput) -> None:
  if not 1 <= input.limit <= 200:
    raise ValueError("limit 必须在 1 到 200 之间")
  if not 0 <= input.offset <= 1_000_000:
    raise ValueError("offset 超出允许范围")
  if input.minimum_probability is not None and not 0 <= input.minimum_probability <= 1:
    raise ValueError("minimumProbability 必须在 0 到 1 之间")
  if input.search is not None and len(input.search.strip()) > 64:
    raise ValueError("搜索文本过长")


def _resolve_candidate_runs(
  runs: list[StockPredictionRun],
  *,
  current_stages: dict[str, str],
  requested_date: date | None,
) -> tuple[date | None, list[StockPredictionRun], list[str]]:
  relevant = sorted(
    (run for run in runs if current_stages.get(run.model_version) == run.model_stage),
    key=lambda run: (run.as_of_date, run.started_at),
    reverse=True,
  )
  resolved_date = requested_date or (relevant[0].as_of_date if relevant else None)
  if resolved_date is None:
    return None, [], ["尚无当前模型阶段的概率推理运行"]
  latest_by_version: dict[str, StockPredictionRun] = {}
  for run in relevant:
    if run.as_of_date == resolved_date and run.model_version not in latest_by_version:
      latest_by_version[run.model_version] = run
  successful: list[StockPredictionRun] = []
  warnings: list[str] = []
  for version in current_stages:
    attempt = latest_by_version.get(version)
    if attempt is None:
      warnings.append(f"模型 {version} 在指定日期没有推理运行")
    elif attempt.status == "SUCCESS":
      successful.append(attempt)
    else:
      warnings.append(f"模型 {version} 最新运行状态为 {attempt.status}，未复用旧候选")
  return resolved_date, successful, warnings


_SENSITIVE_KEYS = (
  "path",
  "root",
  "directory",
  "reference",
  "password",
  "secret",
  "token",
  "credential",
  "api_key",
)
_ABSOLUTE_PATH_START = re.compile(
  r"(?<![A-Za-z0-9_.-])(?:[A-Za-z]:[\\/]|\\\\|//|/(?!/))"
)


def _row_value(row: Any, name: str, default: Any = None) -> Any:
  if isinstance(row, Mapping):
    return row.get(name, default)
  return getattr(row, name, default)


def _safe_public_json(value: Any, *, key: str = "") -> Any:
  lowered = key.lower()
  if any(token in lowered for token in _SENSITIVE_KEYS):
    return None
  if isinstance(value, datetime):
    return value.isoformat()
  if isinstance(value, date):
    return value.isoformat()
  if isinstance(value, Mapping):
    result: dict[str, Any] = {}
    for name, item in value.items():
      safe = _safe_public_json(item, key=str(name))
      if safe is not None:
        result[str(name)] = safe
    return result
  if isinstance(value, (list, tuple, set)):
    return [_safe_public_json(item, key=key) for item in list(value)[:200]]
  if isinstance(value, str):
    return value[:512]
  if isinstance(value, (bool, int, float)) or value is None:
    return value
  return str(value)[:512]


def _safe_error(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value)
  path_match = _ABSOLUTE_PATH_START.search(text)
  if path_match:
    text = f"{text[:path_match.start()]}<path>"
  text = re.sub(r"(?i)(password|secret|token|credential|api[_ -]?key)\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", text)
  return text[:256]


def _as_date(value: Any, *, fallback: date | None = None) -> date | None:
  if value is None:
    return fallback
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  try:
    return date.fromisoformat(str(value)[:10])
  except ValueError:
    return fallback


def _as_datetime(value: Any) -> datetime | None:
  if value is None:
    return None
  if isinstance(value, datetime):
    if value.tzinfo is None:
      return value.replace(tzinfo=timezone.utc)
    return value
  try:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except ValueError:
    return None
  return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _enum_value(value: Any) -> str:
  return str(getattr(value, "value", value) or "")


def _training_input_payload(input: StockSelectionTrainingInput) -> dict[str, Any]:
  payload: dict[str, Any] = {
    "dataset_version": input.dataset_version,
    "date_start": input.date_start,
    "date_end": input.date_end,
    "requested_backend": _enum_value(input.requested_backend),
    "bootstrap_samples": input.bootstrap_samples,
    "worker_batch_size": input.worker_batch_size,
    "random_seed": input.random_seed,
    "note": input.note,
  }
  if input.universe is not None:
    payload["universe_spec"] = {
      "kind": _enum_value(input.universe.kind),
      "stock_codes": list(input.universe.stock_codes or []) or None,
      "index_code": input.universe.index_code,
      "benchmark_code": input.universe.benchmark_code,
      "minimum_listing_days": input.universe.minimum_listing_days,
    }
  return payload


def _resource_estimate(value: Any) -> StockSelectionTrainingResourceEstimate:
  return StockSelectionTrainingResourceEstimate(
    memory_mib=int(_row_value(value, "memory_mib", _row_value(value, "estimated_memory_mib", 0)) or 0),
    disk_mib=int(_row_value(value, "disk_mib", _row_value(value, "estimated_disk_mib", 0)) or 0),
    gpu_memory_mib=int(_row_value(value, "gpu_memory_mib", _row_value(value, "estimated_gpu_memory_mib", 0)) or 0),
    estimated_minutes=int(_row_value(value, "estimated_minutes", 0) or 0),
    duration_level=str(_row_value(value, "duration_level", "UNKNOWN") or "UNKNOWN")[:32],
    sample_count=int(_row_value(value, "sample_count", 0) or 0),
    stock_count=int(_row_value(value, "stock_count", 0) or 0),
    trading_day_count=int(_row_value(value, "trading_day_count", 0) or 0),
    fold_count=int(_row_value(value, "fold_count", 0) or 0),
  )


def _training_folds(value: Any) -> list[StockSelectionTrainingFold]:
  if not isinstance(value, (list, tuple)):
    return []
  result: list[StockSelectionTrainingFold] = []
  for item in value[:200]:
    if not isinstance(item, Mapping):
      continue
    parsed = {
      name: _as_date(item.get(name))
      for name in (
        "train_start",
        "train_end",
        "calibration_start",
        "calibration_end",
        "validation_start",
        "validation_end",
        "validation_month",
      )
    }
    if any(item is None for item in parsed.values()):
      continue
    result.append(StockSelectionTrainingFold(**parsed))
  return result


def _capabilities(value: Any) -> StockSelectionTrainingCapabilities:
  status = str(
    _row_value(
      value, "gpu_status", _row_value(value, "status", "GPU_UNAVAILABLE_RUNTIME")
    )
  ).upper()
  if status not in {item.value for item in StockSelectionTrainingGpuStatus}:
    status = "GPU_UNAVAILABLE_RUNTIME"
  return StockSelectionTrainingCapabilities(
    cpu_available=_row_value(value, "cpu_available", False) is True,
    gpu_status=StockSelectionTrainingGpuStatus(status),
    fresh=bool(_row_value(value, "fresh", False)),
    updated_at=_as_datetime(_row_value(value, "updated_at")),
    available_memory_mib=(
      int(_row_value(value, "available_memory_mib"))
      if _row_value(value, "available_memory_mib") is not None
      else None
    ),
    environment_requirement_hash=(
      str(_row_value(value, "environment_requirement_hash"))
      if _row_value(value, "environment_requirement_hash")
      else None
    ),
    qualification=_safe_public_json(_row_value(value, "qualification", {})),
    environment_summary=_safe_public_json(_row_value(value, "environment_summary", {})),
  )


def _dataset_projection(row: Any) -> StockSelectionDatasetVersion:
  return StockSelectionDatasetVersion(
    dataset_version=str(_row_value(row, "dataset_version", "")),
    status=str(_row_value(row, "status", "CERTIFIED")),
    source_kind=str(_row_value(row, "source_kind", ""))[:64],
    date_start=_as_date(_row_value(row, "date_start"), fallback=date.min) or date.min,
    date_end=_as_date(_row_value(row, "date_end"), fallback=date.min) or date.min,
    universe_spec=_safe_public_json(_row_value(row, "universe_spec", {})),
    indicator_version=str(_row_value(row, "indicator_version", "")),
    factor_set_version=str(_row_value(row, "factor_set_version", "")),
    factor_set_hash=str(_row_value(row, "factor_set_hash", "")),
    label_version=str(_row_value(row, "label_version", "")),
    manifest_sha256=str(_row_value(row, "manifest_sha256", "")),
    sample_count=int(_row_value(row, "sample_count", 0) or 0),
    stock_count=int(_row_value(row, "stock_count", 0) or 0),
    trading_day_count=int(_row_value(row, "trading_day_count", 0) or 0),
    quality_summary=_safe_public_json(_row_value(row, "quality_summary", {})),
    created_at=_as_datetime(_row_value(row, "created_at")) or datetime.now(timezone.utc),
  )


def _preview_projection(value: Mapping[str, Any]) -> StockSelectionTrainingPreview:
  spec = value.get("spec_payload") if isinstance(value.get("spec_payload"), Mapping) else {}
  blockers = [str(item)[:160] for item in value.get("blockers", []) if item is not None]
  fingerprint = str(value.get("preview_fingerprint") or "")
  return StockSelectionTrainingPreview(
    preview_fingerprint=fingerprint,
    dataset_version=str(value.get("dataset_version") or spec.get("dataset_version") or ""),
    requested_backend=StockSelectionTrainingBackend(str(value.get("requested_backend", "CPU"))),
    resolved_backend=StockSelectionResolvedBackend(str(value.get("resolved_backend", "CPU"))),
    folds=_training_folds(value.get("folds")),
    coverage=_safe_public_json(value.get("coverage", {})),
    leakage=_safe_public_json(value.get("leakage", {})),
    resource_estimate=_resource_estimate(value.get("resource_estimate", {})),
    shadow_reasons=[str(item)[:160] for item in value.get("shadow_reasons", []) if item is not None],
    blockers=blockers,
    warnings=[str(item)[:160] for item in value.get("warnings", []) if item is not None],
    capability=_safe_public_json(value.get("capability", {})),
    spec_hash=str(value.get("spec_hash") or spec.get("spec_hash") or ""),
    coordinate_hash=str(value.get("coordinate_hash") or spec.get("coordinate_hash") or ""),
    can_submit=not blockers and bool(fingerprint),
  )


def _run_projection(row: Any, spec: Any = None) -> StockSelectionTrainingRun:
  status = str(_row_value(row, "status", "QUEUED")).upper()
  phase = str(_row_value(row, "phase", "PREFLIGHT")).upper()
  kind = str(_row_value(row, "run_kind", "DEVELOPMENT")).upper()
  status = status if status in {item.value for item in StockSelectionTrainingRunStatus} else "QUEUED"
  phase = phase if phase in {item.value for item in StockSelectionTrainingPhase} else "PREFLIGHT"
  kind = kind if kind in {item.value for item in StockSelectionTrainingRunKind} else "DEVELOPMENT"
  gates = _row_value(row, "gate_summary", {})
  gates = gates if isinstance(gates, Mapping) else {}
  conclusion = gates.get("conclusion")
  if conclusion not in {item.value for item in StockSelectionTrainingConclusion}:
    conclusion = None
  registerable = bool(gates.get("registerable") is True and kind == "FINAL_EVALUATION" and status == "SUCCEEDED")
  return StockSelectionTrainingRun(
    run_id=str(_row_value(row, "run_id", "")),
    run_key=(str(_row_value(row, "run_key")) if _row_value(row, "run_key") else None),
    run_kind=StockSelectionTrainingRunKind(kind),
    parent_run_id=(str(_row_value(row, "parent_run_id")) if _row_value(row, "parent_run_id") else None),
    status=StockSelectionTrainingRunStatus(status),
    phase=StockSelectionTrainingPhase(phase),
    completed_units=int(_row_value(row, "completed_units", 0) or 0),
    total_units=int(_row_value(row, "total_units", 0) or 0),
    requested_at=_as_datetime(_row_value(row, "requested_at")) or datetime.now(timezone.utc),
    started_at=_as_datetime(_row_value(row, "started_at")),
    completed_at=_as_datetime(_row_value(row, "completed_at")),
    cancel_requested_at=_as_datetime(_row_value(row, "cancel_requested_at")),
    state_version=int(_row_value(row, "state_version", 1) or 1),
    dataset_version=(str(_row_value(spec, "dataset_version")) if _row_value(spec, "dataset_version") else None),
    requested_backend=(
      StockSelectionTrainingBackend(str(_row_value(spec, "requested_backend")))
      if _row_value(spec, "requested_backend") in {item.value for item in StockSelectionTrainingBackend}
      else None
    ),
    resolved_backend=(
      StockSelectionResolvedBackend(str(_row_value(spec, "resolved_backend")))
      if _row_value(spec, "resolved_backend") in {item.value for item in StockSelectionResolvedBackend}
      else None
    ),
    spec_hash=(str(_row_value(spec, "spec_hash")) if _row_value(spec, "spec_hash") else None),
    environment_requirement_hash=(str(_row_value(spec, "environment_requirement_hash")) if _row_value(spec, "environment_requirement_hash") else None),
    coordinate_hash=(str(_row_value(spec, "coordinate_hash")) if _row_value(spec, "coordinate_hash") else None),
    experiment_group_hash=(str(_row_value(spec, "experiment_group_hash")) if _row_value(spec, "experiment_group_hash") else None),
    artifact_manifest_sha256=(str(_row_value(row, "artifact_manifest_sha256")) if _row_value(row, "artifact_manifest_sha256") else None),
    environment_evidence=_safe_public_json(_row_value(row, "environment_evidence", {})),
    metrics_summary=_safe_public_json(_row_value(row, "metrics_summary", {})),
    gate_summary=_safe_public_json(gates),
    conclusion=StockSelectionTrainingConclusion(conclusion) if conclusion else None,
    registerable=registerable,
    queue_reason=_safe_error(gates.get("queue_reason") or _row_value(row, "queue_reason")),
    error_code=(str(_row_value(row, "error_code"))[:64] if _row_value(row, "error_code") else None),
    error_message=_safe_error(_row_value(row, "error_message")),
  )


async def _training_run_projection(
  repository: StockSelectionTrainingRepository,
  row: Any,
) -> StockSelectionTrainingRun:
  spec = await repository.get_spec(str(_row_value(row, "spec_id"))) if _row_value(row, "spec_id") else None
  return _run_projection(row, spec)


@strawberry.type(description="次日上涨概率候选与模型证据查询")
class StockSelectionQuery(ResearchPreparationQuery):
  @strawberry.field(description="列出人工登记的概率模型版本")
  async def stock_selection_models(self) -> list[StockSelectionModel]:
    async with AsyncSessionLocal() as db:
      rows = await StockSelectionRepository(db).list_models()
      return [StockSelectionModel.from_record(row) for row in rows]

  @strawberry.field(description="读取一个概率模型版本及效果门禁")
  async def stock_selection_model(self, version: str) -> Optional[StockSelectionModel]:
    async with AsyncSessionLocal() as db:
      row = await StockSelectionRepository(db).get_model(version)
      return StockSelectionModel.from_record(row) if row is not None else None

  @strawberry.field(description="读取概率模型日推理状态；不会触发推理")
  async def stock_prediction_run_status(
    self, as_of: Optional[date] = None
  ) -> StockPredictionRunStatusPage:
    async with AsyncSessionLocal() as db:
      repository = StockSelectionRepository(db)
      runs = await repository.latest_runs(as_of_date=as_of, limit=50)
      models = await repository.runtime_models()
      return StockPredictionRunStatusPage(
        as_of=as_of or (runs[0].as_of_date if runs else None),
        runs=[StockPredictionRunStatus.from_record(run) for run in runs],
        has_active_model=any(model.stage == "ACTIVE" for model in models),
        warnings=(["尚未登记 ACTIVE 或 SHADOW 模型"] if not models else []),
      )

  @strawberry.field(description="读取只读次日上涨概率候选；不创建交易意图")
  async def stock_probability_candidates(
    self, input: StockProbabilityCandidateInput
  ) -> StockProbabilityCandidatePage:
    _validate_candidate_input(input)
    async with AsyncSessionLocal() as db:
      repository = StockSelectionRepository(db)
      runtime_models = await repository.runtime_models()
      by_version = {row.model_version: row for row in runtime_models}
      warnings: list[str] = []
      if input.model_version:
        selected = by_version.get(input.model_version)
        if selected is None:
          raise ValueError("只能查询当前 ACTIVE 或 SHADOW 模型的候选")
        model_versions = [selected.model_version]
        showing_shadow = selected.stage == "SHADOW"
      else:
        active = [row for row in runtime_models if row.stage == "ACTIVE"]
        if active:
          model_versions = [active[0].model_version]
          showing_shadow = False
        else:
          model_versions = [
            row.model_version for row in runtime_models if row.stage == "SHADOW"
          ]
          showing_shadow = bool(model_versions)
          if showing_shadow:
            warnings.append("当前没有 ACTIVE 模型，结果仅为 SHADOW 研究候选")
      all_runs = await repository.latest_runs(as_of_date=input.as_of, limit=100)
      resolved_date, selected_runs, run_warnings = _resolve_candidate_runs(
        all_runs,
        current_stages={
          version: by_version[version].stage for version in model_versions
        },
        requested_date=input.as_of,
      )
      warnings.extend(run_warnings)
      if resolved_date is None:
        return StockProbabilityCandidatePage(
          items=[],
          total=0,
          limit=input.limit,
          offset=input.offset,
          as_of=None,
          target_date=None,
          active_model_version=None,
          showing_shadow=showing_shadow,
          warnings=warnings,
        )
      if not selected_runs:
        warnings.append("指定日期没有可发布的最新成功概率推理运行")
      rows, total = await repository.list_candidates(
        as_of_date=resolved_date,
        prediction_run_ids=[run.id for run in selected_runs],
        levels=[level.value for level in input.levels or []],
        minimum_probability=input.minimum_probability,
        search=input.search,
        limit=input.limit,
        offset=input.offset,
      )
      codes = {candidate.instrument_code for candidate, _, _ in rows}
      names: dict[str, str] = {}
      if codes:
        instruments = await db.execute(
          select(Instrument.id, Instrument.name).where(Instrument.id.in_(codes))
        )
        names = {code: name or code for code, name in instruments.all()}
      items = [
        StockProbabilityCandidate(
          code=candidate.instrument_code,
          name=names.get(candidate.instrument_code, candidate.instrument_code),
          as_of=candidate.as_of_date,
          target_date=candidate.target_date,
          cutoff_at=run.cutoff_at,
          model_version=candidate.model_version,
          label_version=run.label_version,
          indicator_version=run.indicator_version,
          factor_set_version=run.factor_set_version,
          factor_set_hash=run.factor_set_hash,
          calibrator_version=run.calibrator_version,
          candidate_rule_version=run.candidate_rule_version,
          prediction_run_key=run.run_key,
          factor_snapshot_sha256=run.factor_snapshot_sha256,
          stage=StockSelectionModelStage(candidate.model_stage),
          is_shadow=candidate.model_stage == "SHADOW",
          calibrated_probability=candidate.calibrated_probability,
          raw_score=prediction.raw_score,
          logistic_probability=prediction.logistic_probability,
          lightgbm_probability=prediction.lightgbm_probability,
          rank=candidate.rank,
          confidence=candidate.confidence,
          candidate_level=StockCandidateLevel(candidate.candidate_level),
          factor_completeness=prediction.factor_completeness,
          ood_fit=prediction.ood_fit,
          reasons=list(candidate.reason_codes or []),
          risks=list(candidate.risk_flags or []),
          calibration_bucket=StockProbabilityCalibrationBucket(
            index=prediction.calibration_bin_index,
            sample_count=prediction.calibration_bin_samples,
            realized_rate=prediction.calibration_bin_realized_rate,
          ),
        )
        for candidate, prediction, run in rows
      ]
      active_version = next(
        (row.model_version for row in runtime_models if row.stage == "ACTIVE"), None
      )
      return StockProbabilityCandidatePage(
        items=items,
        total=total,
        limit=input.limit,
        offset=input.offset,
        as_of=resolved_date,
        target_date=selected_runs[0].target_date if selected_runs else None,
        active_model_version=active_version,
        showing_shadow=showing_shadow,
        warnings=warnings,
      )

  @strawberry.field(description="读取训练 worker 能力与脱敏环境摘要")
  async def stock_selection_training_capabilities(
    self, info: strawberry.types.Info
  ) -> StockSelectionTrainingCapabilities:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      return _capabilities(await repository.get_capability())

  @strawberry.field(description="读取独立 Trainer 服务与排队原因，不触发计算")
  async def stock_selection_trainer_status(self, info: strawberry.types.Info) -> StockSelectionTrainerStatus:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      value = await TrainerStatusRepository(db).read()
    return StockSelectionTrainerStatus(
      **{name: value[name] for name in ("service", "phase", "admission", "resource_reason", "fresh", "updated_at")},
      training=StockSelectionTrainerDispatch(**{name: value["training"][name] for name in ("state", "status", "reason")}),
      preparation=StockSelectionTrainerDispatch(**{name: value["preparation"][name] for name in ("state", "status", "reason")}),
    )

  @strawberry.field(description="列出可用于训练的认证数据集版本")
  async def stock_selection_dataset_versions(
    self,
    info: strawberry.types.Info,
    limit: int = 100,
    offset: int = 0,
  ) -> list[StockSelectionDatasetVersion]:
    principal_from_context(info.context)
    if not 1 <= limit <= 200:
      raise ValueError("limit 必须在 1 到 200 之间")
    if not 0 <= offset <= 1_000_000:
      raise ValueError("offset 超出允许范围")
    async with AsyncSessionLocal() as db:
      rows = await StockSelectionTrainingRepository(db).list_datasets(
        limit=limit, offset=offset
      )
      return [_dataset_projection(row) for row in rows]

  @strawberry.field(description="预览次日概率训练请求；不会创建运行")
  async def preview_stock_selection_training(
    self,
    info: strawberry.types.Info,
    input: StockSelectionTrainingInput,
  ) -> StockSelectionTrainingPreview:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      application = StockSelectionTrainingApplication(repository)
      try:
        result = await application.preview(
          _training_input_payload(input),
          created_by=principal.user_id,
        )
      except (TypeError, ValueError) as exc:
        raise ValueError(_safe_error(exc) or "训练预览失败") from exc
      return _preview_projection(result)

  @strawberry.field(description="读取次日概率训练运行列表")
  async def stock_selection_training_runs(
    self,
    info: strawberry.types.Info,
    status: Optional[StockSelectionTrainingRunStatus] = None,
    run_kind: Optional[StockSelectionTrainingRunKind] = None,
    limit: int = 50,
    offset: int = 0,
  ) -> StockSelectionTrainingRunPage:
    principal_from_context(info.context)
    if not 1 <= limit <= 200:
      raise ValueError("limit 必须在 1 到 200 之间")
    if not 0 <= offset <= 1_000_000:
      raise ValueError("offset 超出允许范围")
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      rows = await repository.list_runs(
        status=_enum_value(status) if status is not None else None,
        run_kind=_enum_value(run_kind) if run_kind is not None else None,
        limit=limit,
        offset=offset,
      )
      total = await repository.count_runs(
        status=_enum_value(status) if status is not None else None,
        run_kind=_enum_value(run_kind) if run_kind is not None else None,
      )
      items = [await _training_run_projection(repository, row) for row in rows]
      return StockSelectionTrainingRunPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
      )

  @strawberry.field(description="读取一个次日概率训练运行")
  async def stock_selection_training_run(
    self,
    info: strawberry.types.Info,
    run_id: str,
  ) -> Optional[StockSelectionTrainingRun]:
    principal_from_context(info.context)
    if not run_id.strip() or len(run_id.strip()) > 128:
      raise ValueError("runId 无效")
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      row = await repository.get_run(run_id.strip())
      return await _training_run_projection(repository, row) if row is not None else None

  @strawberry.field(description="比较 2 到 5 个训练运行的同坐标证据")
  async def stock_selection_training_comparison(
    self,
    info: strawberry.types.Info,
    run_ids: list[str],
  ) -> StockSelectionTrainingComparison:
    principal_from_context(info.context)
    ids = list(dict.fromkeys(item.strip() for item in run_ids if item.strip()))
    if not 2 <= len(ids) <= 5:
      raise ValueError("runIds 必须包含 2 到 5 个运行")
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      result = await repository.comparison(ids)
      rows = []
      for item in result.get("runs", []):
        row = item.get("run") if isinstance(item, Mapping) else item
        if row is not None:
          rows.append(await _training_run_projection(repository, row))
      return StockSelectionTrainingComparison(
        comparable=bool(result.get("comparable", False)),
        mismatch_fields=[str(item)[:128] for item in result.get("mismatch_fields", [])],
        mismatched_fields=_safe_public_json(result.get("mismatched_fields", {})),
        runs=rows,
        metrics=_safe_public_json(result.get("metrics", {})),
        gates=_safe_public_json(result.get("gates", {})),
      )


@strawberry.type(description="概率模型人工登记与阶段控制")
class StockSelectionMutation(ResearchPreparationMutation):
  @strawberry.mutation(description="从已完成研究运行登记 CANDIDATE 模型")
  async def register_stock_selection_model(
    self,
    info: strawberry.types.Info,
    run_key: str,
  ) -> StockSelectionModel:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      row = await StockSelectionModelService(
        StockSelectionRepository(db), StockSelectionTrainingRepository(db)
      ).register(run_key)
      return StockSelectionModel.from_record(row)

  @strawberry.mutation(description="按乐观锁人工切换模型发布阶段")
  async def set_stock_selection_model_stage(
    self,
    info: strawberry.types.Info,
    model_version: str,
    stage: StockSelectionModelStage,
    expected_version: int,
  ) -> StockSelectionModel:
    if expected_version < 1:
      raise ValueError("expectedVersion 必须大于等于 1")
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      row = await StockSelectionRepository(db).set_model_stage(
        model_version,
        stage.value,
        expected_version=expected_version,
        approved_by=principal.user_id,
        approved_at=utcnow(),
      )
      return StockSelectionModel.from_record(row)

  @strawberry.mutation(description="启动一次 DEVELOPMENT 次日概率训练")
  async def start_stock_selection_development_training(
    self,
    info: strawberry.types.Info,
    input: StockSelectionTrainingInput,
    preview_fingerprint: str,
    idempotency_key: str,
  ) -> StockSelectionTrainingRun:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      application = StockSelectionTrainingApplication(repository)
      try:
        result = await application.start_development(
          _training_input_payload(input),
          preview_fingerprint=preview_fingerprint,
          idempotency_key=idempotency_key,
          created_by=principal.user_id,
        )
      except (TypeError, ValueError) as exc:
        raise ValueError(_safe_error(exc) or "无法启动 DEVELOPMENT 训练") from exc
      return await _training_run_projection(repository, result["run"])

  @strawberry.mutation(description="启动一次基于 DEVELOPMENT 的 FINAL_EVALUATION")
  async def start_stock_selection_final_evaluation(
    self,
    info: strawberry.types.Info,
    parent_run_id: str,
    idempotency_key: str,
  ) -> StockSelectionTrainingRun:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      application = StockSelectionTrainingApplication(repository)
      try:
        result = await application.start_final(
          parent_run_id=parent_run_id,
          idempotency_key=idempotency_key,
          created_by=principal.user_id,
        )
      except (TypeError, ValueError) as exc:
        raise ValueError(_safe_error(exc) or "无法启动 FINAL_EVALUATION") from exc
      return await _training_run_projection(repository, result["run"])

  @strawberry.mutation(description="请求取消一次尚未完成的次日概率训练")
  async def cancel_stock_selection_training_run(
    self,
    info: strawberry.types.Info,
    run_id: str,
    expected_version: int,
    idempotency_key: str,
  ) -> StockSelectionTrainingRun:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      repository = StockSelectionTrainingRepository(db)
      application = StockSelectionTrainingApplication(repository)
      try:
        result = await application.cancel(
          run_id=run_id,
          expected_state_version=expected_version,
          idempotency_key=idempotency_key,
        )
      except (TypeError, ValueError) as exc:
        raise ValueError(_safe_error(exc) or "无法取消训练") from exc
      return await _training_run_projection(repository, result["run"])
