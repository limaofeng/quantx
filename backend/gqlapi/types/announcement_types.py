"""GraphQL types for stock announcements and repurchase events."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

import strawberry
from strawberry.scalars import JSON


def _float(value) -> Optional[float]:
  if value is None:
    return None
  if isinstance(value, Decimal):
    return float(value)
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


@strawberry.type(description="上市公司公告")
class StockAnnouncement:
  id: str = strawberry.field(description="公告ID")
  stock_code: str = strawberry.field(description="证券代码")
  stock_name: Optional[str] = strawberry.field(description="证券简称")
  title: str = strawberry.field(description="公告标题")
  announcement_type: Optional[str] = strawberry.field(description="公告类型")
  announce_date: Optional[date] = strawberry.field(description="公告日期")
  source: str = strawberry.field(description="数据来源")
  source_url: Optional[str] = strawberry.field(description="公告来源链接")
  pdf_url: Optional[str] = strawberry.field(description="PDF链接")
  is_repurchase_related: bool = strawberry.field(description="是否回购相关")
  fetched_at: datetime = strawberry.field(description="抓取时间")
  raw_payload: JSON = strawberry.field(description="来源原始字段")

  @staticmethod
  def from_model(model) -> "StockAnnouncement":
    return StockAnnouncement(
      id=model.id,
      stock_code=model.stock_code,
      stock_name=model.stock_name,
      title=model.title,
      announcement_type=model.announcement_type,
      announce_date=model.announce_date,
      source=model.source,
      source_url=model.source_url,
      pdf_url=model.pdf_url,
      is_repurchase_related=bool(model.is_repurchase_related),
      fetched_at=model.fetched_at,
      raw_payload=model.source_payload or {},
    )


@strawberry.type(description="股票回购事件摘要")
class StockRepurchaseEvent:
  id: str = strawberry.field(description="回购事件ID")
  stock_code: str = strawberry.field(description="证券代码")
  stock_name: Optional[str] = strawberry.field(description="证券简称")
  source: str = strawberry.field(description="数据来源")
  source_url: Optional[str] = strawberry.field(description="来源链接")
  latest_announce_date: Optional[date] = strawberry.field(description="最新公告日期")
  progress_status: Optional[str] = strawberry.field(description="实施进度")
  price_floor: Optional[float] = strawberry.field(description="计划回购价格下限")
  price_ceiling: Optional[float] = strawberry.field(description="计划回购价格上限")
  planned_quantity_lower: Optional[float] = strawberry.field(description="计划数量下限")
  planned_quantity_average: Optional[float] = strawberry.field(description="计划数量均值")
  planned_quantity_upper: Optional[float] = strawberry.field(description="计划数量上限")
  planned_amount_lower: Optional[float] = strawberry.field(description="计划金额下限")
  planned_amount_upper: Optional[float] = strawberry.field(description="计划金额上限")
  repurchased_quantity: Optional[float] = strawberry.field(description="已回购数量")
  repurchased_amount: Optional[float] = strawberry.field(description="已回购金额")
  repurchased_ratio: Optional[float] = strawberry.field(description="已回购比例")
  fetched_at: datetime = strawberry.field(description="抓取时间")
  raw_payload: JSON = strawberry.field(description="来源原始字段")

  @staticmethod
  def from_model(model) -> "StockRepurchaseEvent":
    return StockRepurchaseEvent(
      id=model.id,
      stock_code=model.stock_code,
      stock_name=model.stock_name,
      source=model.source,
      source_url=model.source_url,
      latest_announce_date=model.latest_announce_date,
      progress_status=model.progress_status,
      price_floor=_float(model.price_floor),
      price_ceiling=_float(model.price_ceiling),
      planned_quantity_lower=_float(model.planned_quantity_lower),
      planned_quantity_average=_float(model.planned_quantity_average),
      planned_quantity_upper=_float(model.planned_quantity_upper),
      planned_amount_lower=_float(model.planned_amount_lower),
      planned_amount_upper=_float(model.planned_amount_upper),
      repurchased_quantity=_float(model.repurchased_quantity),
      repurchased_amount=_float(model.repurchased_amount),
      repurchased_ratio=_float(model.repurchased_ratio),
      fetched_at=model.fetched_at,
      raw_payload=model.source_payload or {},
    )


@strawberry.type(description="公告同步状态")
class AnnouncementSyncStatus:
  success: bool = strawberry.field(description="是否成功")
  stock_code: str = strawberry.field(description="证券代码")
  source_status: str = strawberry.field(description="数据源状态")
  message: Optional[str] = strawberry.field(description="状态摘要")
  started_at: Optional[datetime] = strawberry.field(description="开始时间")
  finished_at: Optional[datetime] = strawberry.field(description="结束时间")
  announcement_count: int = strawberry.field(description="公告数量")
  repurchase_count: int = strawberry.field(description="回购事件数量")
  error_message: Optional[str] = strawberry.field(description="错误信息")

  @staticmethod
  def from_model(model) -> "AnnouncementSyncStatus":
    return AnnouncementSyncStatus(
      success=model.source_status in ("SUCCESS", "PARTIAL", "SKIPPED"),
      stock_code=model.stock_code or "",
      source_status=model.source_status,
      message=model.message,
      started_at=model.started_at,
      finished_at=model.finished_at,
      announcement_count=int(model.announcement_count or 0),
      repurchase_count=int(model.repurchase_count or 0),
      error_message=model.error_message,
    )

  @staticmethod
  def from_result(result) -> "AnnouncementSyncStatus":
    return AnnouncementSyncStatus(
      success=bool(result.success),
      stock_code=result.stock_code,
      source_status=result.source_status,
      message=result.message,
      started_at=result.started_at,
      finished_at=result.finished_at,
      announcement_count=int(result.announcement_count or 0),
      repurchase_count=int(result.repurchase_count or 0),
      error_message=result.error_message,
    )


@strawberry.type(description="单票公告与回购摘要")
class StockDisclosureSummary:
  stock_code: str = strawberry.field(description="证券代码")
  source_status: str = strawberry.field(description="数据源状态")
  source_message: Optional[str] = strawberry.field(description="数据源状态说明")
  latest_announcement_date: Optional[date] = strawberry.field(description="最新公告日期")
  latest_repurchase_date: Optional[date] = strawberry.field(description="最新回购公告日期")
  latest_sync: Optional[AnnouncementSyncStatus] = strawberry.field(description="最近同步")
  announcements: List[StockAnnouncement] = strawberry.field(description="公告列表")
  repurchase_events: List[StockRepurchaseEvent] = strawberry.field(
    description="回购事件列表"
  )

  @staticmethod
  def from_data(data) -> "StockDisclosureSummary":
    announcements = [StockAnnouncement.from_model(item) for item in data.announcements]
    repurchases = [
      StockRepurchaseEvent.from_model(item) for item in data.repurchase_events
    ]
    return StockDisclosureSummary(
      stock_code=data.stock_code,
      source_status=data.source_status,
      source_message=data.source_message,
      latest_announcement_date=announcements[0].announce_date
      if announcements
      else None,
      latest_repurchase_date=repurchases[0].latest_announce_date
      if repurchases
      else None,
      latest_sync=AnnouncementSyncStatus.from_model(data.latest_sync)
      if data.latest_sync
      else None,
      announcements=announcements,
      repurchase_events=repurchases,
    )
