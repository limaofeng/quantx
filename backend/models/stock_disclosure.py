"""上市公司公告与股票回购数据模型。"""

from datetime import date, datetime
from hashlib import md5
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, Column, Date, DateTime, Index, Integer, JSON, Numeric, String

from database.relational_base import Base, TimestampMixin


class StockAnnouncement(Base, TimestampMixin):
  """上市公司公告索引。

  公告只作为披露信息和审计线索，不作为交易状态真源。
  """

  __tablename__ = "stock_announcements"
  __table_args__ = (
    Index("ix_stock_announcements_stock_date", "stock_code", "announce_date"),
    Index("ix_stock_announcements_repurchase", "stock_code", "is_repurchase_related"),
  )

  id = Column(String(40), primary_key=True, index=True, comment="稳定公告ID")
  stock_code = Column(String(20), nullable=False, index=True, comment="证券代码")
  stock_name = Column(String(80), nullable=True, comment="证券简称")
  title = Column(String(500), nullable=False, comment="公告标题")
  announcement_type = Column(String(80), nullable=True, comment="公告类型")
  announce_date = Column(Date, nullable=True, index=True, comment="公告日期")
  source = Column(String(40), nullable=False, default="EASTMONEY_AKSHARE")
  source_url = Column(String(1000), nullable=True, comment="公告页URL")
  pdf_url = Column(String(1000), nullable=True, comment="公告PDF URL")
  is_repurchase_related = Column(Boolean, nullable=False, default=False)
  fetched_at = Column(DateTime, nullable=False, comment="抓取时间")
  source_payload = Column("raw_payload", JSON, nullable=False, default=dict)

  @staticmethod
  def make_id(
    *,
    source: str,
    stock_code: str,
    announce_date: Optional[date],
    title: str,
    source_url: Optional[str],
  ) -> str:
    raw = "|".join(
      [
        source.strip().upper(),
        stock_code.strip().upper(),
        announce_date.isoformat() if announce_date else "",
        title.strip(),
        (source_url or "").strip(),
      ]
    )
    return md5(raw.encode("utf-8")).hexdigest()

  def to_dict(self) -> Dict[str, Any]:
    return {
      "id": self.id,
      "stock_code": self.stock_code,
      "stock_name": self.stock_name,
      "title": self.title,
      "announcement_type": self.announcement_type,
      "announce_date": self.announce_date.isoformat()
      if self.announce_date
      else None,
      "source": self.source,
      "source_url": self.source_url,
      "pdf_url": self.pdf_url,
      "is_repurchase_related": bool(self.is_repurchase_related),
      "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
      "raw_payload": self.source_payload or {},
    }


class StockRepurchaseEvent(Base, TimestampMixin):
  """股票回购事件摘要。"""

  __tablename__ = "stock_repurchase_events"
  __table_args__ = (
    Index("ix_stock_repurchase_stock_date", "stock_code", "latest_announce_date"),
  )

  id = Column(String(40), primary_key=True, index=True, comment="稳定回购事件ID")
  stock_code = Column(String(20), nullable=False, index=True, comment="证券代码")
  stock_name = Column(String(80), nullable=True, comment="证券简称")
  source = Column(String(40), nullable=False, default="EASTMONEY_AKSHARE")
  source_url = Column(String(1000), nullable=True, comment="来源URL")
  latest_announce_date = Column(Date, nullable=True, index=True, comment="最新公告日期")
  progress_status = Column(String(80), nullable=True, comment="实施进度")
  price_floor = Column(Numeric(18, 4), nullable=True, comment="计划回购价格下限")
  price_ceiling = Column(Numeric(18, 4), nullable=True, comment="计划回购价格上限")
  planned_quantity_lower = Column(Numeric(24, 4), nullable=True, comment="计划数量下限")
  planned_quantity_average = Column(Numeric(24, 4), nullable=True, comment="计划数量均值")
  planned_quantity_upper = Column(Numeric(24, 4), nullable=True, comment="计划数量上限")
  planned_amount_lower = Column(Numeric(24, 4), nullable=True, comment="计划金额下限")
  planned_amount_upper = Column(Numeric(24, 4), nullable=True, comment="计划金额上限")
  repurchased_quantity = Column(Numeric(24, 4), nullable=True, comment="已回购数量")
  repurchased_amount = Column(Numeric(24, 4), nullable=True, comment="已回购金额")
  repurchased_ratio = Column(Numeric(12, 6), nullable=True, comment="已回购比例")
  fetched_at = Column(DateTime, nullable=False, comment="抓取时间")
  source_payload = Column("raw_payload", JSON, nullable=False, default=dict)

  @staticmethod
  def make_id(
    *,
    source: str,
    stock_code: str,
    latest_announce_date: Optional[date],
    source_url: Optional[str],
    payload_hint: Optional[str] = None,
  ) -> str:
    raw = "|".join(
      [
        source.strip().upper(),
        stock_code.strip().upper(),
        latest_announce_date.isoformat() if latest_announce_date else "",
        (source_url or "").strip(),
        (payload_hint or "").strip(),
      ]
    )
    return md5(raw.encode("utf-8")).hexdigest()


class AnnouncementSyncRun(Base, TimestampMixin):
  """公告同步运行记录。"""

  __tablename__ = "announcement_sync_runs"
  __table_args__ = (
    Index("ix_announcement_sync_stock_started", "stock_code", "started_at"),
  )

  id = Column(String(40), primary_key=True, index=True, comment="同步运行ID")
  scope = Column(String(40), nullable=False, comment="single_stock/batch")
  stock_code = Column(String(20), nullable=True, index=True, comment="证券代码")
  source = Column(String(40), nullable=False, default="AKSHARE")
  source_status = Column(String(40), nullable=False, default="RUNNING")
  message = Column(String(500), nullable=True, comment="同步摘要")
  error_message = Column(String(1000), nullable=True, comment="错误信息")
  started_at = Column(DateTime, nullable=False, index=True)
  finished_at = Column(DateTime, nullable=True)
  announcement_count = Column(Integer, nullable=False, default=0)
  repurchase_count = Column(Integer, nullable=False, default=0)

  @staticmethod
  def make_id(scope: str, stock_code: Optional[str], started_at: datetime) -> str:
    raw = f"{scope}:{stock_code or ''}:{started_at.isoformat()}"
    return md5(raw.encode("utf-8")).hexdigest()
