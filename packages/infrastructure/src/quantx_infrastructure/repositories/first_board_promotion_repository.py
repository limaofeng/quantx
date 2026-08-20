"""Persistence and leasing for first-board promotion market artifacts."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional, Sequence

from quantx_domain.clock import utcnow
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.first_board_promotion import (
  FirstBoardCandidatePreference,
  FirstBoardModelRelease,
  FirstBoardPromotionAssessmentRecord,
  LimitUpChainSnapshot,
  LimitUpLifecycleSnapshot,
  LimitUpResearchArtifact,
  LimitUpResearchJob,
)


class FirstBoardPromotionRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def get_model_release(
    self, model_version: str
  ) -> Optional[FirstBoardModelRelease]:
    return await self.db.get(FirstBoardModelRelease, model_version)

  async def get_lifecycle(
    self, trade_date: date, instrument_code: str, snapshot_version: str
  ) -> Optional[LimitUpLifecycleSnapshot]:
    return (
      await self.db.execute(
        select(LimitUpLifecycleSnapshot).where(
          LimitUpLifecycleSnapshot.trade_date == trade_date,
          LimitUpLifecycleSnapshot.instrument_code == instrument_code,
          LimitUpLifecycleSnapshot.snapshot_version == snapshot_version,
        )
      )
    ).scalar_one_or_none()

  async def save_lifecycle_and_assessment(
    self,
    *,
    trade_date: date,
    instrument_code: str,
    as_of: datetime,
    snapshot_version: str,
    feature_version: str,
    stage: str,
    ever_touched_limit: bool,
    break_count: int,
    lifecycle_payload: dict[str, Any],
    assessment_payload: dict[str, Any],
  ) -> FirstBoardPromotionAssessmentRecord:
    lifecycle = await self.get_lifecycle(
      trade_date, instrument_code, snapshot_version
    )
    if lifecycle is None:
      lifecycle = LimitUpLifecycleSnapshot(
        trade_date=trade_date,
        instrument_code=instrument_code,
        stage=stage,
        as_of=as_of,
        snapshot_version=snapshot_version,
        feature_version=feature_version,
        ever_touched_limit=ever_touched_limit,
        break_count=break_count,
        payload=lifecycle_payload,
      )
      self.db.add(lifecycle)
      await self.db.flush()
    model_version = str(assessment_payload["model_version"])
    assessment = (
      await self.db.execute(
        select(FirstBoardPromotionAssessmentRecord).where(
          FirstBoardPromotionAssessmentRecord.lifecycle_snapshot_id == lifecycle.id,
          FirstBoardPromotionAssessmentRecord.model_version == model_version,
        )
      )
    ).scalar_one_or_none()
    if assessment is None:
      assessment = FirstBoardPromotionAssessmentRecord(
        lifecycle_snapshot_id=lifecycle.id,
        trade_date=trade_date,
        instrument_code=instrument_code,
        as_of=as_of,
        model_version=model_version,
        exit_policy_version=str(assessment_payload["exit_policy_version"]),
        segment=str(assessment_payload["segment"]),
        eligible=bool(assessment_payload["eligible"]),
        rank_score=float(assessment_payload["rank_score"]),
        first_board_close_probability=float(
          assessment_payload["first_board_close_probability"]
        ),
        next_day_limit_touch_probability=float(
          assessment_payload["next_day_limit_touch_probability"]
        ),
        next_day_limit_seal_probability=float(
          assessment_payload["next_day_limit_seal_probability"]
        ),
        expected_net_return_pct=float(assessment_payload["expected_net_return_pct"]),
        cvar95_loss_pct=float(assessment_payload["cvar95_loss_pct"]),
        high_position_type=str(assessment_payload["high_position_type"]),
        veto_reasons=list(assessment_payload.get("veto_reasons") or []),
        payload=assessment_payload,
      )
      self.db.add(assessment)
    await self.db.commit()
    await self.db.refresh(assessment)
    return assessment

  async def save_chain(self, snapshot: LimitUpChainSnapshot) -> LimitUpChainSnapshot:
    existing = (
      await self.db.execute(
        select(LimitUpChainSnapshot).where(
          LimitUpChainSnapshot.trade_date == snapshot.trade_date,
          LimitUpChainSnapshot.snapshot_version == snapshot.snapshot_version,
        )
      )
    ).scalar_one_or_none()
    if existing is not None:
      return existing
    self.db.add(snapshot)
    await self.db.commit()
    await self.db.refresh(snapshot)
    return snapshot

  async def latest_assessments(
    self,
    trade_date: date,
    *,
    eligible_only: bool = False,
    limit: int = 200,
  ) -> list[FirstBoardPromotionAssessmentRecord]:
    latest_as_of = (
      select(
        FirstBoardPromotionAssessmentRecord.instrument_code,
        func.max(FirstBoardPromotionAssessmentRecord.as_of).label("latest_as_of"),
      )
      .where(FirstBoardPromotionAssessmentRecord.trade_date == trade_date)
      .group_by(FirstBoardPromotionAssessmentRecord.instrument_code)
      .subquery()
    )
    stmt = select(FirstBoardPromotionAssessmentRecord).join(
      latest_as_of,
      (FirstBoardPromotionAssessmentRecord.instrument_code == latest_as_of.c.instrument_code)
      & (FirstBoardPromotionAssessmentRecord.as_of == latest_as_of.c.latest_as_of),
    )
    if eligible_only:
      stmt = stmt.where(FirstBoardPromotionAssessmentRecord.eligible.is_(True))
    result = await self.db.execute(
      stmt.order_by(
        FirstBoardPromotionAssessmentRecord.rank_score.desc(),
        FirstBoardPromotionAssessmentRecord.instrument_code.asc(),
      ).limit(max(1, min(int(limit), 500)))
    )
    return list(result.scalars().all())

  async def list_replay_facts(
    self,
    start_time: datetime,
    end_time: datetime,
  ) -> list[tuple[LimitUpLifecycleSnapshot, FirstBoardPromotionAssessmentRecord]]:
    """Return legacy sparse facts for degraded replay compatibility."""

    result = await self.db.execute(
      select(
        LimitUpLifecycleSnapshot,
        FirstBoardPromotionAssessmentRecord,
      )
      .join(
        FirstBoardPromotionAssessmentRecord,
        FirstBoardPromotionAssessmentRecord.lifecycle_snapshot_id
        == LimitUpLifecycleSnapshot.id,
      )
      .where(
        LimitUpLifecycleSnapshot.as_of >= start_time,
        LimitUpLifecycleSnapshot.as_of <= end_time,
      )
      .order_by(
        LimitUpLifecycleSnapshot.as_of.asc(),
        LimitUpLifecycleSnapshot.instrument_code.asc(),
      )
    )
    return [(lifecycle, assessment) for lifecycle, assessment in result.all()]

  async def list_lifecycle(
    self,
    trade_date: date,
    instrument_code: str,
    *,
    limit: int = 200,
  ) -> list[LimitUpLifecycleSnapshot]:
    result = await self.db.execute(
      select(LimitUpLifecycleSnapshot)
      .where(
        LimitUpLifecycleSnapshot.trade_date == trade_date,
        LimitUpLifecycleSnapshot.instrument_code == instrument_code,
      )
      .order_by(LimitUpLifecycleSnapshot.as_of.asc())
      .limit(max(1, min(int(limit), 500)))
    )
    return list(result.scalars().all())

  async def latest_artifacts(
    self, trade_date: date, instrument_codes: Sequence[str]
  ) -> dict[str, LimitUpResearchArtifact]:
    codes = tuple({str(code).upper() for code in instrument_codes if code})
    if not codes:
      return {}
    result = await self.db.execute(
      select(LimitUpResearchArtifact)
      .where(
        LimitUpResearchArtifact.trade_date == trade_date,
        LimitUpResearchArtifact.instrument_code.in_(codes),
      )
      .order_by(LimitUpResearchArtifact.generated_at.desc())
    )
    artifacts: dict[str, LimitUpResearchArtifact] = {}
    for artifact in result.scalars().all():
      artifacts.setdefault(artifact.instrument_code, artifact)
    return artifacts

  async def upsert_preference(
    self,
    *,
    account_id: str,
    trade_date: date,
    instrument_code: str,
    preference: str,
    actor_id: str,
    idempotency_key: str,
  ) -> FirstBoardCandidatePreference:
    row = (
      await self.db.execute(
        select(FirstBoardCandidatePreference).where(
          FirstBoardCandidatePreference.account_id == account_id,
          FirstBoardCandidatePreference.trade_date == trade_date,
          FirstBoardCandidatePreference.instrument_code == instrument_code,
        )
      )
    ).scalar_one_or_none()
    if row is None:
      row = FirstBoardCandidatePreference(
        account_id=account_id,
        trade_date=trade_date,
        instrument_code=instrument_code,
      )
    elif idempotency_key and row.idempotency_key == idempotency_key:
      return row
    row.preference = preference
    row.actor_id = actor_id[:64]
    row.idempotency_key = idempotency_key[:128]
    row.version = int(row.version or 0) + 1
    self.db.add(row)
    await self.db.commit()
    await self.db.refresh(row)
    return row

  async def list_preferences(
    self, account_id: str, trade_date: date
  ) -> dict[str, FirstBoardCandidatePreference]:
    result = await self.db.execute(
      select(FirstBoardCandidatePreference).where(
        FirstBoardCandidatePreference.account_id == account_id,
        FirstBoardCandidatePreference.trade_date == trade_date,
      )
    )
    return {row.instrument_code: row for row in result.scalars().all()}

  async def create_research_job(
    self,
    *,
    assessment: FirstBoardPromotionAssessmentRecord,
    input_snapshot_version: str,
    priority: int,
  ) -> LimitUpResearchJob:
    key = f"limit-up-research:{assessment.trade_date}:{assessment.instrument_code}"
    existing = (
      await self.db.execute(
        select(LimitUpResearchJob).where(LimitUpResearchJob.idempotency_key == key)
      )
    ).scalar_one_or_none()
    if existing is not None:
      return existing
    job = LimitUpResearchJob(
      assessment_id=assessment.id,
      trade_date=assessment.trade_date,
      instrument_code=assessment.instrument_code,
      input_snapshot_version=input_snapshot_version,
      priority=priority,
      idempotency_key=key,
    )
    self.db.add(job)
    await self.db.commit()
    await self.db.refresh(job)
    return job

  async def count_daily_jobs(self, trade_date: date) -> int:
    return int(
      await self.db.scalar(
        select(func.count()).select_from(LimitUpResearchJob).where(
          LimitUpResearchJob.trade_date == trade_date
        )
      )
      or 0
    )

  async def claim_next_research_job(
    self, *, instance_id: str, lease_seconds: int
  ) -> Optional[LimitUpResearchJob]:
    now = utcnow()
    candidate_id = await self.db.scalar(
      select(LimitUpResearchJob.id)
      .where(
        or_(
          LimitUpResearchJob.status == "QUEUED",
          (LimitUpResearchJob.status == "RUNNING")
          & (LimitUpResearchJob.lease_expires_at < now),
        )
      )
      .order_by(LimitUpResearchJob.priority.desc(), LimitUpResearchJob.created_at.asc())
      .with_for_update(skip_locked=True)
      .limit(1)
    )
    if candidate_id is None:
      return None
    await self.db.execute(
      update(LimitUpResearchJob)
      .where(LimitUpResearchJob.id == candidate_id)
      .values(
        status="RUNNING",
        lease_owner=instance_id,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        started_at=now,
      )
    )
    await self.db.commit()
    return await self.db.get(LimitUpResearchJob, candidate_id)

  async def complete_research_job(
    self,
    job: LimitUpResearchJob,
    *,
    artifact: LimitUpResearchArtifact,
    input_tokens: int = 0,
    output_tokens: int = 0,
  ) -> LimitUpResearchArtifact:
    existing = (
      await self.db.execute(
        select(LimitUpResearchArtifact).where(
          LimitUpResearchArtifact.job_id == job.id
        )
      )
    ).scalar_one_or_none()
    if existing is None:
      self.db.add(artifact)
      existing = artifact
    job.status = "COMPLETED"
    job.finished_at = utcnow()
    job.lease_expires_at = None
    job.input_tokens = max(0, int(input_tokens))
    job.output_tokens = max(0, int(output_tokens))
    job.error_code = None
    job.error_message = None
    await self.db.commit()
    await self.db.refresh(existing)
    return existing

  async def fail_research_job(
    self, job: LimitUpResearchJob, *, error_code: str, error_message: str
  ) -> None:
    job.status = "FAILED"
    job.finished_at = utcnow()
    job.lease_expires_at = None
    job.error_code = str(error_code or "RESEARCH_FAILED")[:64]
    job.error_message = str(error_message or "研究任务失败")[:512]
    await self.db.commit()
