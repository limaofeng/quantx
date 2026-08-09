from datetime import date
from decimal import Decimal

import pytest
from quantx_infrastructure.services.announcement_provider import (
  AnnouncementRecord,
  RepurchaseRecord,
)
from quantx_infrastructure.services.announcement_sync_service import (
  AnnouncementSyncService,
)


@pytest.mark.asyncio
async def test_refresh_uses_akshare_by_default_without_feature_flag(monkeypatch):
  import quantx_infrastructure.services.announcement_sync_service as module

  class FakeProvider:
    def fetch_announcements(self, stock_code, begin_date, end_date):
      assert stock_code == "002594.SZ"
      assert begin_date
      assert end_date
      return [
        AnnouncementRecord(
          stock_code="002594.SZ",
          stock_name="比亚迪",
          title="关于回购公司股份的公告",
          announcement_type="回购",
          announce_date=date(2026, 6, 1),
          source="EASTMONEY_AKSHARE",
          source_url="https://example.com/notice.pdf",
          pdf_url="https://example.com/notice.pdf",
          is_repurchase_related=True,
          raw_payload={"公告标题": "关于回购公司股份的公告"},
        )
      ]

    def fetch_repurchase_events(self, stock_code):
      assert stock_code == "002594.SZ"
      return [
        RepurchaseRecord(
          stock_code="002594.SZ",
          stock_name="比亚迪",
          source="EASTMONEY_AKSHARE",
          source_url="https://example.com/repurchase",
          latest_announce_date=date(2026, 6, 1),
          progress_status="实施中",
          price_floor=None,
          price_ceiling=Decimal("120"),
          planned_quantity_lower=None,
          planned_quantity_average=None,
          planned_quantity_upper=None,
          planned_amount_lower=Decimal("100000000"),
          planned_amount_upper=Decimal("200000000"),
          repurchased_quantity=Decimal("1000000"),
          repurchased_amount=Decimal("90000000"),
          repurchased_ratio=Decimal("0.1"),
          raw_payload={"股票代码": "002594"},
        )
      ]

  class FakeRepo:
    saved_announcements = []
    saved_repurchases = []

    def __init__(self, db):
      self.db = db

    async def latest_sync_run(self, stock_code):
      assert stock_code == "002594.SZ"
      return None

    async def save_sync_run(self, run):
      return run

    async def upsert_announcements(self, items):
      self.__class__.saved_announcements = list(items)
      return len(items)

    async def upsert_repurchase_events(self, items):
      self.__class__.saved_repurchases = list(items)
      return len(items)

  async def fake_db():
    yield object()

  monkeypatch.setattr(module, "get_async_db", fake_db)
  monkeypatch.setattr(module, "AnnouncementRepository", FakeRepo)

  result = await AnnouncementSyncService(
    provider=FakeProvider()
  ).refresh_stock_disclosures("SZ.002594")

  assert result.success is True
  assert result.stock_code == "002594.SZ"
  assert result.source_status == "SUCCESS"
  assert result.announcement_count == 1
  assert result.repurchase_count == 1
  assert FakeRepo.saved_announcements[0].is_repurchase_related is True
  assert FakeRepo.saved_repurchases[0].repurchased_amount == Decimal("90000000")


@pytest.mark.asyncio
async def test_summary_returns_stable_empty_shape(monkeypatch):
  import quantx_infrastructure.services.announcement_sync_service as module

  class FakeRepo:
    def __init__(self, db):
      self.db = db

    async def find_announcements(self, stock_code, limit=20):
      assert stock_code == "002594.SZ"
      assert limit == 20
      return []

    async def find_repurchase_events(self, stock_code, limit=5):
      assert stock_code == "002594.SZ"
      return []

    async def latest_sync_run(self, stock_code):
      assert stock_code == "002594.SZ"
      return None

  async def fake_db():
    yield object()

  monkeypatch.setattr(module, "get_async_db", fake_db)
  monkeypatch.setattr(module, "AnnouncementRepository", FakeRepo)

  summary = await AnnouncementSyncService().get_summary("002594", limit=20)

  assert summary.stock_code == "002594.SZ"
  assert summary.source_status == "READY"
  assert summary.announcements == []
  assert summary.repurchase_events == []
