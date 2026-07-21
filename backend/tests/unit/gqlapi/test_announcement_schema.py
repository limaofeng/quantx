from datetime import date, datetime
from decimal import Decimal

import pytest

from models.stock_disclosure import (
  AnnouncementSyncRun,
  StockAnnouncement,
  StockRepurchaseEvent,
)
from services.announcement_sync_service import DisclosureSummaryData


DISCLOSURE_QUERY = """
query Disclosure($stockCode: String!) {
  stockDisclosureSummary(stockCode: $stockCode, limit: 20) {
    stockCode
    sourceStatus
    sourceMessage
    latestAnnouncementDate
    latestRepurchaseDate
    latestSync {
      success
      sourceStatus
      errorMessage
      announcementCount
      repurchaseCount
    }
    announcements {
      id
      title
      announcementType
      announceDate
      source
      sourceUrl
      isRepurchaseRelated
    }
    repurchaseEvents {
      id
      progressStatus
      latestAnnounceDate
      plannedAmountUpper
      repurchasedAmount
      repurchasedRatio
      sourceUrl
    }
  }
}
"""


@pytest.mark.asyncio
async def test_stock_disclosure_summary_returns_empty_shape(monkeypatch):
  result = await _execute_summary(
    monkeypatch,
    DisclosureSummaryData(
      stock_code="002594.SZ",
      announcements=[],
      repurchase_events=[],
      latest_sync=None,
      source_status="READY",
      source_message="等待首次同步",
    ),
  )

  assert result.errors is None
  summary = result.data["stockDisclosureSummary"]
  assert summary["stockCode"] == "002594.SZ"
  assert summary["sourceStatus"] == "READY"
  assert summary["announcements"] == []
  assert summary["repurchaseEvents"] == []
  assert summary["latestAnnouncementDate"] is None
  assert summary["latestRepurchaseDate"] is None


@pytest.mark.asyncio
async def test_stock_disclosure_summary_returns_failed_sync_status(monkeypatch):
  result = await _execute_summary(
    monkeypatch,
    DisclosureSummaryData(
      stock_code="002594.SZ",
      announcements=[],
      repurchase_events=[],
      latest_sync=AnnouncementSyncRun(
        id="run-failed",
        scope="single_stock",
        stock_code="002594.SZ",
        source="AKSHARE",
        source_status="FAILED",
        message="同步失败",
        error_message="AkShare timeout",
        started_at=datetime(2026, 6, 5, 15, 30),
        finished_at=datetime(2026, 6, 5, 15, 31),
        announcement_count=0,
        repurchase_count=0,
      ),
      source_status="FAILED",
      source_message="同步失败",
    ),
  )

  assert result.errors is None
  latest_sync = result.data["stockDisclosureSummary"]["latestSync"]
  assert latest_sync["success"] is False
  assert latest_sync["sourceStatus"] == "FAILED"
  assert latest_sync["errorMessage"] == "AkShare timeout"


@pytest.mark.asyncio
async def test_stock_disclosure_summary_returns_announcement_only(monkeypatch):
  result = await _execute_summary(
    monkeypatch,
    DisclosureSummaryData(
      stock_code="002594.SZ",
      announcements=[
        StockAnnouncement(
          id="notice-1",
          stock_code="002594.SZ",
          stock_name="比亚迪",
          title="关于回购公司股份方案的公告",
          announcement_type="重大事项",
          announce_date=date(2026, 6, 1),
          source="EASTMONEY_AKSHARE",
          source_url="https://example.com/notice.pdf",
          pdf_url="https://example.com/notice.pdf",
          is_repurchase_related=True,
          fetched_at=datetime(2026, 6, 5, 15, 30),
          source_payload={"公告标题": "关于回购公司股份方案的公告"},
        )
      ],
      repurchase_events=[],
      latest_sync=None,
      source_status="SUCCESS",
      source_message="公告 1 条，回购 0 条",
    ),
  )

  assert result.errors is None
  summary = result.data["stockDisclosureSummary"]
  assert summary["latestAnnouncementDate"] == "2026-06-01"
  assert summary["latestRepurchaseDate"] is None
  assert summary["announcements"][0]["isRepurchaseRelated"] is True
  assert summary["announcements"][0]["source"] == "EASTMONEY_AKSHARE"


@pytest.mark.asyncio
async def test_stock_disclosure_summary_returns_repurchase_only(monkeypatch):
  result = await _execute_summary(
    monkeypatch,
    DisclosureSummaryData(
      stock_code="002594.SZ",
      announcements=[],
      repurchase_events=[
        StockRepurchaseEvent(
          id="repurchase-1",
          stock_code="002594.SZ",
          stock_name="比亚迪",
          source="EASTMONEY_AKSHARE",
          source_url="https://example.com/repurchase",
          latest_announce_date=date(2026, 6, 2),
          progress_status="实施中",
          price_ceiling=Decimal("120"),
          planned_amount_upper=Decimal("200000000"),
          repurchased_amount=Decimal("90000000"),
          repurchased_ratio=Decimal("0.1"),
          fetched_at=datetime(2026, 6, 5, 15, 30),
          source_payload={"股票代码": "002594"},
        )
      ],
      latest_sync=None,
      source_status="SUCCESS",
      source_message="公告 0 条，回购 1 条",
    ),
  )

  assert result.errors is None
  summary = result.data["stockDisclosureSummary"]
  assert summary["latestAnnouncementDate"] is None
  assert summary["latestRepurchaseDate"] == "2026-06-02"
  event = summary["repurchaseEvents"][0]
  assert event["progressStatus"] == "实施中"
  assert event["plannedAmountUpper"] == 200000000.0
  assert event["repurchasedAmount"] == 90000000.0


async def _execute_summary(monkeypatch, summary_data):
  from gqlapi.resolvers.announcements import AnnouncementResolver
  from gqlapi.schema import schema
  from gqlapi.types.announcement_types import StockDisclosureSummary

  async def fake_summary(stock_code: str, limit: int = 20):
    assert stock_code == "002594.SZ"
    assert limit == 20
    return StockDisclosureSummary.from_data(summary_data)

  monkeypatch.setattr(
    AnnouncementResolver,
    "get_stock_disclosure_summary",
    staticmethod(fake_summary),
  )
  return await schema.execute(
    DISCLOSURE_QUERY,
    variable_values={"stockCode": "002594.SZ"},
  )
