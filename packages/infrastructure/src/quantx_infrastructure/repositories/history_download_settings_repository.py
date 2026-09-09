"""Shared durable policy, read afresh at both admission gates."""

from dataclasses import dataclass
from datetime import datetime

from quantx_contracts.history_download_settings import HistoryDownloadPolicy
from quantx_domain.clock import utcnow
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.history_download_settings import (
  HistoryDownloadSettingsRecord,
)


class HistoryDownloadSettingsConflict(ValueError):
  pass


@dataclass(frozen=True)
class HistoryDownloadSettings:
  policy: HistoryDownloadPolicy
  version: int = 0
  updated_at: datetime | None = None


def project(record):
  if record is None:
    return HistoryDownloadSettings(HistoryDownloadPolicy())
  return HistoryDownloadSettings(
    HistoryDownloadPolicy.model_validate(record.policy),
    record.version,
    record.updated_at,
  )


class HistoryDownloadSettingsRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def get(self) -> HistoryDownloadSettings:
    return project(await self.db.get(HistoryDownloadSettingsRecord, "global"))

  async def update(
    self, *, policy: HistoryDownloadPolicy, expected_version: int, user_id: str
  ) -> HistoryDownloadSettings:
    record = await self.db.scalar(
      select(HistoryDownloadSettingsRecord)
      .where(HistoryDownloadSettingsRecord.id == "global")
      .with_for_update()
    )
    current = project(record)
    if current.version != expected_version:
      raise HistoryDownloadSettingsConflict("配置已被更新，请刷新后重试")
    now = utcnow()
    if record is None:
      record = HistoryDownloadSettingsRecord(id="global", created_at=now)
      self.db.add(record)
    record.policy = policy.model_dump(mode="json")
    record.version = current.version + 1
    record.updated_by_user_id = user_id
    record.updated_at = now
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      raise HistoryDownloadSettingsConflict("配置已被更新，请刷新后重试") from None
    await self.db.refresh(record)
    return project(record)
