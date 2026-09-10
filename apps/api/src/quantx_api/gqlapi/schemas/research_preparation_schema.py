"""Authenticated preparation settings and durable job operations."""

import asyncio
from datetime import datetime, timezone
from enum import Enum

import strawberry
from quantx_contracts.research_preparation import ResearchPreparationConfig
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.services.research_preparation import (
  ResearchPreparationRepository,
  download_preview,
  list_evidence_files,
)
from strawberry.scalars import JSON

from ..security import principal_from_context


@strawberry.enum
class ResearchPreparationKind(Enum):
  COVERAGE = "COVERAGE"
  DOWNLOAD = "DOWNLOAD"
  CERTIFY = "CERTIFY"
  GPU = "GPU"


@strawberry.type
class ResearchPreparationTask:
  job_id: str
  kind: ResearchPreparationKind
  status: str
  phase: str
  config: JSON
  dataset_version: str | None
  flow_run_id: str | None
  result: JSON
  error: str | None
  created_at: datetime
  updated_at: datetime


def project(row):
  return ResearchPreparationTask(
    job_id=row.job_id,
    kind=ResearchPreparationKind(row.kind),
    status=row.status,
    phase=row.phase,
    config=row.request["config"],
    dataset_version=row.request.get("dataset_version"),
    flow_run_id=row.flow_run_id,
    result=row.result or {},
    error=row.error,
    created_at=row.created_at.replace(tzinfo=timezone.utc),
    updated_at=row.updated_at.replace(tzinfo=timezone.utc),
  )


@strawberry.type
class ResearchPreparationState:
  config: JSON
  evidence_files: list[str]
  jobs: list[ResearchPreparationTask]


@strawberry.type
class ResearchPreparationQuery:
  @strawberry.field(description="读取研究数据准备配置及任务状态")
  async def research_preparation(
    self, info: strawberry.types.Info
  ) -> ResearchPreparationState:
    principal_from_context(info.context)
    files = await asyncio.to_thread(list_evidence_files)
    async with AsyncSessionLocal() as db:
      repo = ResearchPreparationRepository(db)
      return ResearchPreparationState(
        config=await repo.config(),
        evidence_files=files,
        jobs=[project(row) for row in await repo.jobs()],
      )

  @strawberry.field(description="预览研究配置所需的行情下载范围")
  async def preview_research_download(
    self, info: strawberry.types.Info, config: JSON
  ) -> JSON:
    principal_from_context(info.context)
    return await download_preview(ResearchPreparationConfig.model_validate(config))


@strawberry.type
class ResearchPreparationMutation:
  @strawberry.mutation(description="保存研究数据准备配置")
  async def save_research_preparation(
    self, info: strawberry.types.Info, config: JSON
  ) -> JSON:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      return await ResearchPreparationRepository(db).save(config)

  @strawberry.mutation(description="提交指定类型的研究数据准备任务")
  async def start_research_preparation(
    self,
    info: strawberry.types.Info,
    kind: ResearchPreparationKind,
    config: JSON,
    request_key: str,
    dataset_version: str | None = None,
  ) -> ResearchPreparationTask:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      row = await ResearchPreparationRepository(db).submit(
        kind=kind.value,
        config=config,
        request_key=request_key,
        dataset_version=dataset_version,
      )
      return project(row)

  @strawberry.mutation(description="重试指定研究数据准备任务")
  async def retry_research_preparation(
    self, info: strawberry.types.Info, job_id: str
  ) -> ResearchPreparationTask:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      return project(await ResearchPreparationRepository(db).retry(job_id))
