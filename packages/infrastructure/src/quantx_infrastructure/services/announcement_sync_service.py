"""上市公司公告与回购同步服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, List, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.stock_disclosure import (
  AnnouncementSyncRun,
  StockAnnouncement,
  StockRepurchaseEvent,
)
from quantx_infrastructure.repositories.announcement_repository import (
  AnnouncementRepository,
)
from quantx_infrastructure.services.announcement_provider import (
  AkshareAnnouncementProvider,
  AnnouncementRecord,
  RepurchaseRecord,
  normalize_internal_stock_code,
  unique_stock_codes,
)

DEFAULT_NOTICE_LOOKBACK_DAYS = 365


@dataclass
class DisclosureSummaryData:
  stock_code: str
  announcements: List[StockAnnouncement]
  repurchase_events: List[StockRepurchaseEvent]
  latest_sync: Optional[AnnouncementSyncRun]
  source_status: str
  source_message: Optional[str]


@dataclass
class DisclosureSyncResult:
  success: bool
  stock_code: str
  source_status: str
  message: str
  started_at: Optional[object] = None
  finished_at: Optional[object] = None
  announcement_count: int = 0
  repurchase_count: int = 0
  error_message: Optional[str] = None


class AnnouncementSyncService:
  def __init__(self, provider: Optional[AkshareAnnouncementProvider] = None):
    self._provider = provider or AkshareAnnouncementProvider()

  async def get_summary(
    self,
    stock_code: str,
    *,
    limit: int = 20,
  ) -> DisclosureSummaryData:
    normalized_code = normalize_internal_stock_code(stock_code)
    limit = max(1, min(limit, 80))

    async for db in get_async_db():
      repo = AnnouncementRepository(db)
      announcements = await repo.find_announcements(normalized_code, limit=limit)
      repurchases = await repo.find_repurchase_events(normalized_code, limit=5)
      latest_sync = await repo.latest_sync_run(normalized_code)
      if latest_sync:
        source_status = latest_sync.source_status
        source_message = latest_sync.message or latest_sync.error_message
      else:
        source_status = "READY"
        source_message = "等待首次同步"
      return DisclosureSummaryData(
        stock_code=normalized_code,
        announcements=announcements,
        repurchase_events=repurchases,
        latest_sync=latest_sync,
        source_status=source_status,
        source_message=source_message,
      )

    return DisclosureSummaryData(
      stock_code=normalized_code,
      announcements=[],
      repurchase_events=[],
      latest_sync=None,
      source_status="UNAVAILABLE",
      source_message="数据库会话不可用",
    )

  async def refresh_stock_disclosures(
    self,
    stock_code: str,
    *,
    force: bool = False,
  ) -> DisclosureSyncResult:
    normalized_code = normalize_internal_stock_code(stock_code)
    if not normalized_code:
      return DisclosureSyncResult(
        success=False,
        stock_code="",
        source_status="INVALID",
        message="stock_code is required",
      )

    async for db in get_async_db():
      repo = AnnouncementRepository(db)
      latest = await repo.latest_sync_run(normalized_code)
      if latest and not force and _is_successful_today(latest):
        return DisclosureSyncResult(
          success=True,
          stock_code=normalized_code,
          source_status="SKIPPED",
          message="今日已同步，未重复抓取",
          started_at=latest.started_at,
          finished_at=latest.finished_at,
          announcement_count=int(latest.announcement_count or 0),
          repurchase_count=int(latest.repurchase_count or 0),
        )

      started_at = time_utils.now()
      run = AnnouncementSyncRun(
        id=AnnouncementSyncRun.make_id("single_stock", normalized_code, started_at),
        scope="single_stock",
        stock_code=normalized_code,
        source="AKSHARE",
        source_status="RUNNING",
        started_at=started_at,
      )
      run = await repo.save_sync_run(run)

      end_date = time_utils.today()
      begin_date = end_date - timedelta(days=DEFAULT_NOTICE_LOOKBACK_DAYS)
      begin_text = begin_date.strftime("%Y%m%d")
      end_text = end_date.strftime("%Y%m%d")

      errors = []
      announcement_records: List[AnnouncementRecord] = []
      repurchase_records: List[RepurchaseRecord] = []

      try:
        announcement_records = await asyncio.to_thread(
          self._provider.fetch_announcements,
          normalized_code,
          begin_date=begin_text,
          end_date=end_text,
        )
      except Exception as exc:
        errors.append(f"公告同步失败: {exc}")

      try:
        repurchase_records = await asyncio.to_thread(
          self._provider.fetch_repurchase_events,
          normalized_code,
        )
      except Exception as exc:
        errors.append(f"回购同步失败: {exc}")

      now = time_utils.now()
      announcements = [
        _announcement_model_from_record(record, fetched_at=now)
        for record in announcement_records
      ]
      repurchases = [
        _repurchase_model_from_record(record, fetched_at=now)
        for record in repurchase_records
      ]

      saved_announcements = await repo.upsert_announcements(announcements)
      saved_repurchases = await repo.upsert_repurchase_events(repurchases)

      if errors and not (saved_announcements or saved_repurchases):
        status = "FAILED"
      elif errors:
        status = "PARTIAL"
      else:
        status = "SUCCESS"

      run.source_status = status
      run.finished_at = time_utils.now()
      run.announcement_count = saved_announcements
      run.repurchase_count = saved_repurchases
      run.error_message = "; ".join(errors) if errors else None
      run.message = (
        f"公告 {saved_announcements} 条，回购 {saved_repurchases} 条"
        if status != "FAILED"
        else "同步失败"
      )
      run = await repo.save_sync_run(run)

      return DisclosureSyncResult(
        success=status in ("SUCCESS", "PARTIAL"),
        stock_code=normalized_code,
        source_status=status,
        message=run.message or "",
        started_at=run.started_at,
        finished_at=run.finished_at,
        announcement_count=saved_announcements,
        repurchase_count=saved_repurchases,
        error_message=run.error_message,
      )

    return DisclosureSyncResult(
      success=False,
      stock_code=normalized_code,
      source_status="UNAVAILABLE",
      message="数据库会话不可用",
    )

  async def refresh_stock_codes(
    self,
    stock_codes: Iterable[str],
    *,
    force: bool = False,
  ) -> List[DisclosureSyncResult]:
    results = []
    for code in unique_stock_codes(stock_codes):
      results.append(await self.refresh_stock_disclosures(code, force=force))
    return results


def _is_successful_today(run: AnnouncementSyncRun) -> bool:
  return (
    run.source_status == "SUCCESS"
    and run.started_at is not None
    and run.started_at.date() == time_utils.today()
  )


def _announcement_model_from_record(
  record: AnnouncementRecord,
  *,
  fetched_at,
) -> StockAnnouncement:
  payload = dict(record.raw_payload or {})
  content = next(
    (
      str(payload.get(key) or "").strip()
      for key in (
        "公告内容",
        "公告正文",
        "正文",
        "content",
        "announcementContent",
        "adjunctContent",
      )
      if str(payload.get(key) or "").strip()
    ),
    "",
  )
  source = str(record.source or "").upper()
  source_authority = "CNINFO" if "CNINFO" in source else None
  return StockAnnouncement(
    id=StockAnnouncement.make_id(
      source=record.source,
      stock_code=record.stock_code,
      announce_date=record.announce_date,
      title=record.title,
      source_url=record.source_url,
    ),
    stock_code=record.stock_code,
    stock_name=record.stock_name,
    title=record.title,
    announcement_type=record.announcement_type,
    announce_date=record.announce_date,
    source=record.source,
    source_url=record.source_url,
    pdf_url=record.pdf_url,
    is_repurchase_related=record.is_repurchase_related,
    fetched_at=fetched_at,
    source_payload=payload,
    source_authority=source_authority,
    content_text=content or None,
    content_hash=(
      hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
    ),
    content_fetched_at=fetched_at if content else None,
  )


def _repurchase_model_from_record(
  record: RepurchaseRecord,
  *,
  fetched_at,
) -> StockRepurchaseEvent:
  payload_hint = json.dumps(
    record.raw_payload,
    ensure_ascii=False,
    sort_keys=True,
    default=str,
  )[:300]
  return StockRepurchaseEvent(
    id=StockRepurchaseEvent.make_id(
      source=record.source,
      stock_code=record.stock_code,
      latest_announce_date=record.latest_announce_date,
      source_url=record.source_url,
      payload_hint=payload_hint,
    ),
    stock_code=record.stock_code,
    stock_name=record.stock_name,
    source=record.source,
    source_url=record.source_url,
    latest_announce_date=record.latest_announce_date,
    progress_status=record.progress_status,
    price_floor=record.price_floor,
    price_ceiling=record.price_ceiling,
    planned_quantity_lower=record.planned_quantity_lower,
    planned_quantity_average=record.planned_quantity_average,
    planned_quantity_upper=record.planned_quantity_upper,
    planned_amount_lower=record.planned_amount_lower,
    planned_amount_upper=record.planned_amount_upper,
    repurchased_quantity=record.repurchased_quantity,
    repurchased_amount=record.repurchased_amount,
    repurchased_ratio=record.repurchased_ratio,
    fetched_at=fetched_at,
    source_payload=record.raw_payload,
  )
