"""GraphQL boundary for model evidence and read-only probability candidates."""

from __future__ import annotations

from datetime import date
from typing import Optional

import strawberry
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.stock_selection import StockPredictionRun
from quantx_infrastructure.repositories.stock_selection_repository import (
  StockSelectionRepository,
)
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
  StockSelectionModel,
  StockSelectionModelStage,
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


@strawberry.type(description="次日上涨概率候选与模型证据查询")
class StockSelectionQuery:
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


@strawberry.type(description="概率模型人工登记与阶段控制")
class StockSelectionMutation:
  @strawberry.mutation(description="从已完成研究运行登记 CANDIDATE 模型")
  async def register_stock_selection_model(
    self,
    info: strawberry.types.Info,
    run_key: str,
  ) -> StockSelectionModel:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      row = await StockSelectionModelService(StockSelectionRepository(db)).register(
        run_key
      )
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
