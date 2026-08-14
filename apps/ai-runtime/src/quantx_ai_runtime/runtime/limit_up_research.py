"""Market-scoped, idempotent first-board research job consumer."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

from agents import Agent, Runner
from pydantic import BaseModel, Field
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.first_board_promotion import (
  FirstBoardPromotionAssessmentRecord,
  LimitUpChainSnapshot,
  LimitUpResearchArtifact,
  LimitUpResearchJob,
)
from quantx_infrastructure.models.stock_disclosure import StockAnnouncement
from quantx_infrastructure.repositories.first_board_promotion_repository import (
  FirstBoardPromotionRepository,
)
from sqlalchemy import select

from quantx_ai_runtime.config import AiRuntimeConfig, AiRuntimeConfigController

logger = logging.getLogger(__name__)
PROMPT_VERSION = "limit-up-research-v1"


class LimitUpResearchOutput(BaseModel):
  candidate_summary: str = Field(max_length=1200)
  catalysts: list[str] = Field(default_factory=list, max_length=8)
  announcement_risks: list[str] = Field(default_factory=list, max_length=8)
  citations: list[str] = Field(default_factory=list, max_length=20)
  data_gaps: list[str] = Field(default_factory=list, max_length=10)
  confidence_note: str = Field(max_length=500)


def _sanitize_citations(
  output: LimitUpResearchOutput,
  *,
  announcements: list[StockAnnouncement],
  input_snapshot_version: str,
) -> LimitUpResearchOutput:
  """Keep evidence references anchored to the persisted job input."""
  allowed = {input_snapshot_version}
  for announcement in announcements:
    allowed.update(
      value.strip()
      for value in (announcement.source_url, announcement.pdf_url)
      if value and value.strip()
    )
  citations = list(dict.fromkeys(item.strip() for item in output.citations if item.strip()))
  verified = [item for item in citations if item in allowed]
  if len(verified) == len(citations):
    return output
  data_gaps = list(output.data_gaps)
  gap = "UNVERIFIED_CITATIONS_DROPPED"
  if gap not in data_gaps:
    data_gaps.append(gap)
  return output.model_copy(update={"citations": verified, "data_gaps": data_gaps})


async def execute_limit_up_research_job(
  job_id: str, config: AiRuntimeConfig
) -> None:
  async with AsyncSessionLocal() as db:
    job = await db.get(LimitUpResearchJob, job_id)
    if job is None or job.status != "RUNNING":
      return
    assessment = await db.get(FirstBoardPromotionAssessmentRecord, job.assessment_id)
    if assessment is None:
      raise ValueError("FIRST_BOARD_ASSESSMENT_NOT_FOUND")
    chain = (
      await db.execute(
        select(LimitUpChainSnapshot)
        .where(LimitUpChainSnapshot.trade_date == job.trade_date)
        .order_by(LimitUpChainSnapshot.as_of.desc())
        .limit(1)
      )
    ).scalar_one_or_none()
    announcements = list(
      (
        await db.execute(
          select(StockAnnouncement)
          .where(
            StockAnnouncement.stock_code == job.instrument_code,
            StockAnnouncement.source_authority.is_not(None),
            StockAnnouncement.announce_date >= job.trade_date - timedelta(days=90),
          )
          .order_by(StockAnnouncement.announce_date.desc())
          .limit(20)
        )
      ).scalars().all()
    )

  facts = {
    "candidate": assessment.payload,
    "chain": dict(chain.payload or {}) if chain else None,
    "announcements": [item.to_dict() for item in announcements],
    "inputSnapshotVersion": job.input_snapshot_version,
    "generatedAt": utcnow().isoformat(),
  }
  agent = Agent(
    name="QuantX Limit-up Research Assistant",
    model=config.model,
    output_type=LimitUpResearchOutput,
    instructions=(
      "你是首板晋级市场研究助手。仅依据输入的确定性候选快照、连板梯队和"
      "已持久化公告做结构化摘要。不得输出买入、卖出、仓位或交易资格建议；"
      "不得推测缺失公告正文；引用使用公告 source_url/pdf_url 或快照版本。"
      "若证据不足必须列入 data_gaps。"
    ),
    tools=[],
  )
  async with asyncio.timeout(config.run_timeout_seconds):
    result = await Runner.run(
      agent,
      "请生成市场级共享研究产物：\n"
      + json.dumps(facts, ensure_ascii=False, default=str),
      max_turns=min(2, config.max_turns),
    )
  output = result.final_output
  if not isinstance(output, LimitUpResearchOutput):
    output = LimitUpResearchOutput.model_validate(output)
  output = _sanitize_citations(
    output,
    announcements=announcements,
    input_snapshot_version=job.input_snapshot_version,
  )
  usage = result.context_wrapper.usage
  generated_at = utcnow()
  async with AsyncSessionLocal() as db:
    repository = FirstBoardPromotionRepository(db)
    current_job = await db.get(LimitUpResearchJob, job_id)
    if current_job is None or current_job.status != "RUNNING":
      return
    await repository.complete_research_job(
      current_job,
      artifact=LimitUpResearchArtifact(
        job_id=current_job.id,
        assessment_id=current_job.assessment_id,
        trade_date=current_job.trade_date,
        instrument_code=current_job.instrument_code,
        input_snapshot_version=current_job.input_snapshot_version,
        agent_id="limit_up_research_assistant",
        model=config.model,
        prompt_version=PROMPT_VERSION,
        status="COMPLETED",
        summary=output.candidate_summary,
        content=output.model_dump(mode="json", exclude={"citations"}),
        citations=output.citations,
        generated_at=generated_at,
      ),
      input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
      output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


async def run_limit_up_research_consumer(
  stopped: asyncio.Event,
  *,
  instance_id: str,
  controller: AiRuntimeConfigController,
) -> None:
  while not stopped.is_set():
    config = controller.snapshot()
    if not config.configured:
      try:
        await asyncio.wait_for(stopped.wait(), timeout=1.0)
      except asyncio.TimeoutError:
        pass
      continue
    async with AsyncSessionLocal() as db:
      job = await FirstBoardPromotionRepository(db).claim_next_research_job(
        instance_id=instance_id,
        lease_seconds=config.lease_seconds,
      )
    if job is None:
      try:
        await asyncio.wait_for(stopped.wait(), timeout=1.0)
      except asyncio.TimeoutError:
        pass
      continue
    try:
      await execute_limit_up_research_job(job.id, config)
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      logger.error(
        "Limit-up research failed: job_id=%s error=%s",
        job.id,
        exc.__class__.__name__,
      )
      async with AsyncSessionLocal() as db:
        current = await db.get(LimitUpResearchJob, job.id)
        if current is not None:
          await FirstBoardPromotionRepository(db).fail_research_job(
            current,
            error_code=exc.__class__.__name__,
            error_message="首板研究生成失败",
          )
