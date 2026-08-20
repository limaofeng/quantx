"""Persistence for board-assistant replay inputs and job projections."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.limit_up_board_replay import (
  LimitUpBoardReplayJob,
  LimitUpBoardReplayScenario,
  LimitUpBoardUniverseSnapshot,
)

ACTIVE_LIMIT_UP_BOARD_REPLAY_STATUSES = (
  "PENDING",
  "STARTING",
  "RUNNING",
)


class LimitUpBoardReplayRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def save_universe_snapshot(
    self,
    *,
    snapshot_key: str,
    trade_date: date,
    observed_at: datetime,
    source_max_at: Optional[datetime],
    snapshot_version: str,
    score_version: str,
    feature_version: str,
    model_version: str,
    exit_policy_version: str,
    candidate_count: int,
    eligible_count: int,
    payload: dict[str, Any],
  ) -> LimitUpBoardUniverseSnapshot:
    normalized_payload = dict(payload or {})
    candidates = list(normalized_payload.get("candidates") or [])
    normalized_candidate_count = int(candidate_count)
    normalized_eligible_count = int(eligible_count)
    if not str(snapshot_key or "").strip() or not str(snapshot_version or "").strip():
      raise ValueError("候选池快照键和内容指纹不能为空")
    if source_max_at is not None and _datetime_gt(source_max_at, observed_at):
      raise ValueError("候选池行情截止时间不能晚于实际可见时间")
    if normalized_candidate_count != len(candidates):
      raise ValueError("候选池快照候选数量与 payload 不一致")
    payload_eligible_count = sum(
      bool(dict(item or {}).get("promotion_eligible")) for item in candidates
    )
    if normalized_eligible_count != payload_eligible_count:
      raise ValueError("候选池快照合格数量与 payload 不一致")
    for candidate in candidates:
      updated_at = _parse_datetime(dict(candidate or {}).get("updated_at"))
      if updated_at is not None and _datetime_gt(updated_at, observed_at):
        raise ValueError("候选行情时间不能晚于候选池实际可见时间")

    existing = (
      await self.db.execute(
        select(LimitUpBoardUniverseSnapshot).where(
          LimitUpBoardUniverseSnapshot.snapshot_key == snapshot_key
        )
      )
    ).scalar_one_or_none()
    if existing is not None:
      if str(existing.snapshot_version) != str(snapshot_version):
        raise ValueError("候选池快照键碰撞且内容指纹不一致")
      return existing
    row = LimitUpBoardUniverseSnapshot(
      snapshot_key=snapshot_key,
      trade_date=trade_date,
      observed_at=observed_at,
      source_max_at=source_max_at,
      schema_version=1,
      snapshot_version=snapshot_version,
      score_version=score_version,
      feature_version=feature_version,
      model_version=model_version,
      exit_policy_version=exit_policy_version,
      candidate_count=normalized_candidate_count,
      eligible_count=normalized_eligible_count,
      payload=normalized_payload,
    )
    self.db.add(row)
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = (
        await self.db.execute(
          select(LimitUpBoardUniverseSnapshot).where(
            LimitUpBoardUniverseSnapshot.snapshot_key == snapshot_key
          )
        )
      ).scalar_one_or_none()
      if existing is None:
        raise
      if str(existing.snapshot_version) != str(snapshot_version):
        raise ValueError("候选池快照键碰撞且内容指纹不一致")
      return existing
    await self.db.refresh(row)
    return row

  async def list_universe_snapshots(
    self,
    start_time: datetime,
    end_time: datetime,
  ) -> list[LimitUpBoardUniverseSnapshot]:
    result = await self.db.execute(
      select(LimitUpBoardUniverseSnapshot)
      .where(
        LimitUpBoardUniverseSnapshot.observed_at >= start_time,
        LimitUpBoardUniverseSnapshot.observed_at <= end_time,
      )
      .order_by(
        LimitUpBoardUniverseSnapshot.observed_at.asc(),
        LimitUpBoardUniverseSnapshot.id.asc(),
      )
    )
    return list(result.scalars().all())
  async def iter_universe_snapshots(
    self,
    start_time: datetime,
    end_time: datetime,
  ):
    result = await self.db.stream_scalars(
      select(LimitUpBoardUniverseSnapshot)
      .where(
        LimitUpBoardUniverseSnapshot.observed_at >= start_time,
        LimitUpBoardUniverseSnapshot.observed_at <= end_time,
      )
      .order_by(
        LimitUpBoardUniverseSnapshot.observed_at.asc(),
        LimitUpBoardUniverseSnapshot.id.asc(),
      )
      .execution_options(yield_per=500)
    )
    async for row in result:
      yield row

  async def get_job(
    self,
    job_id: str,
    *,
    for_update: bool = False,
  ) -> Optional[LimitUpBoardReplayJob]:
    stmt = select(LimitUpBoardReplayJob).where(LimitUpBoardReplayJob.id == job_id)
    if for_update:
      stmt = stmt.with_for_update()
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def list_jobs(
    self,
    account_id: str,
    limit: int = 20,
  ) -> list[LimitUpBoardReplayJob]:
    result = await self.db.execute(
      select(LimitUpBoardReplayJob)
      .where(LimitUpBoardReplayJob.account_id == account_id)
      .order_by(
        LimitUpBoardReplayJob.created_at.desc(),
        LimitUpBoardReplayJob.id.desc(),
      )
      .limit(max(1, min(int(limit or 20), 100)))
    )
    return list(result.scalars().all())

  async def has_active_job(self, account_id: str) -> bool:
    result = await self.db.execute(
      select(LimitUpBoardReplayJob.id)
      .where(
        LimitUpBoardReplayJob.account_id == account_id,
        LimitUpBoardReplayJob.status.in_(
          ACTIVE_LIMIT_UP_BOARD_REPLAY_STATUSES
        ),
      )
      .limit(1)
    )
    return result.scalar_one_or_none() is not None

  async def list_scenarios(
    self,
    job_id: str,
  ) -> list[LimitUpBoardReplayScenario]:
    result = await self.db.execute(
      select(LimitUpBoardReplayScenario)
      .where(LimitUpBoardReplayScenario.job_id == job_id)
      .order_by(LimitUpBoardReplayScenario.scenario_id.asc())
    )
    return list(result.scalars().all())


def _parse_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  if not value:
    return None
  try:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except ValueError as exc:
    raise ValueError("候选行情时间格式无效") from exc


def _datetime_gt(left: datetime, right: datetime) -> bool:
  if left.tzinfo is None and right.tzinfo is not None:
    left = left.replace(tzinfo=right.tzinfo)
  if left.tzinfo is not None and right.tzinfo is None:
    right = right.replace(tzinfo=left.tzinfo)
  return left > right
