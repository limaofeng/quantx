from datetime import date, datetime

import pytest

from models.stock_disclosure import StockAnnouncement
from repositories.announcement_repository import AnnouncementRepository


class FakeSession:
  def __init__(self):
    self.commits = 0
    self.items = {}

  async def merge(self, item):
    self.items[(item.__class__, item.id)] = item
    return item

  async def commit(self):
    self.commits += 1


@pytest.mark.asyncio
async def test_upsert_announcements_dedupes_same_stable_id():
  session = FakeSession()
  repo = AnnouncementRepository(session)
  first = _announcement("stable-id", title="关于回购公司股份的公告")
  duplicate = _announcement("stable-id", title="关于回购公司股份的公告")

  saved_count = await repo.upsert_announcements([first, duplicate])

  assert saved_count == 1
  assert len(session.items) == 1
  assert session.commits == 1


@pytest.mark.asyncio
async def test_upsert_announcements_does_not_commit_empty_batch():
  session = FakeSession()
  repo = AnnouncementRepository(session)

  saved_count = await repo.upsert_announcements([])

  assert saved_count == 0
  assert session.items == {}
  assert session.commits == 0


def _announcement(item_id: str, title: str) -> StockAnnouncement:
  return StockAnnouncement(
    id=item_id,
    stock_code="002594.SZ",
    stock_name="比亚迪",
    title=title,
    announcement_type="重大事项",
    announce_date=date(2026, 6, 1),
    source="EASTMONEY_AKSHARE",
    source_url="https://example.com/notice.pdf",
    pdf_url="https://example.com/notice.pdf",
    is_repurchase_related=True,
    fetched_at=datetime(2026, 6, 5, 15, 30),
    source_payload={"公告标题": title},
  )
