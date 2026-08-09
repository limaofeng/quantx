from datetime import date

import pandas as pd
from quantx_infrastructure.models.stock_disclosure import StockAnnouncement
from quantx_infrastructure.services.announcement_provider import (
  AkshareAnnouncementProvider,
  normalize_internal_stock_code,
  to_akshare_security,
)


def test_normalizes_common_stock_code_forms():
  assert normalize_internal_stock_code("002594.SZ") == "002594.SZ"
  assert normalize_internal_stock_code("SZ.002594") == "002594.SZ"
  assert normalize_internal_stock_code("002594") == "002594.SZ"
  assert normalize_internal_stock_code("600519") == "600519.SH"
  assert normalize_internal_stock_code("BJ.430047") == "430047.BJ"
  assert to_akshare_security("002594.SZ") == "002594"


def test_akshare_announcement_mapping_handles_repurchase_rows():
  class FakeAkshare:
    def stock_individual_notice_report(self, **kwargs):
      assert kwargs["security"] == "002594"
      return pd.DataFrame(
        [
          {
            "代码": "002594",
            "名称": "比亚迪",
            "公告标题": "关于回购公司股份方案的公告",
            "公告类型": "重大事项",
            "公告日期": "2026-05-20",
            "网址": "https://data.eastmoney.com/notices/detail/002594/foo.html",
          }
        ]
      )

  provider = AkshareAnnouncementProvider()
  provider._akshare = FakeAkshare()

  records = provider.fetch_announcements(
    "002594.SZ",
    begin_date="20260501",
    end_date="20260601",
  )

  assert len(records) == 1
  assert records[0].stock_code == "002594.SZ"
  assert records[0].announce_date == date(2026, 5, 20)
  assert records[0].is_repurchase_related is True
  assert records[0].source == "EASTMONEY_AKSHARE"


def test_akshare_repurchase_mapping_preserves_raw_units():
  class FakeAkshare:
    def stock_repurchase_em(self):
      return pd.DataFrame(
        [
          {
            "股票代码": "002594",
            "股票简称": "比亚迪",
            "计划回购价格区间": "不超过 300 元/股",
            "计划回购数量区间-下限": 1000000,
            "计划回购数量区间-平均": 1500000,
            "计划回购数量区间-上限": 2000000,
            "计划回购金额区间-下限": 100000000,
            "计划回购金额区间-上限": 600000000,
            "已回购金额": 120000000,
            "已回购数量": 400000,
            "占总股本比例": 0.13,
            "最新公告日期": "2026-05-21",
            "实施进度": "实施中",
          }
        ]
      )

  provider = AkshareAnnouncementProvider()
  provider._akshare = FakeAkshare()

  records = provider.fetch_repurchase_events("SZ.002594")

  assert len(records) == 1
  event = records[0]
  assert event.stock_code == "002594.SZ"
  assert event.price_ceiling == 300
  assert event.planned_quantity_lower == 1000000
  assert event.planned_amount_upper == 600000000
  assert event.repurchased_amount == 120000000
  assert event.latest_announce_date == date(2026, 5, 21)


def test_stable_announcement_id_uses_source_code_date_title_url():
  first = StockAnnouncement.make_id(
    source="EASTMONEY_AKSHARE",
    stock_code="002594.SZ",
    announce_date=date(2026, 5, 20),
    title="关于回购公司股份方案的公告",
    source_url="https://example.test/a",
  )
  second = StockAnnouncement.make_id(
    source="eastmoney_akshare",
    stock_code="002594.sz",
    announce_date=date(2026, 5, 20),
    title="关于回购公司股份方案的公告",
    source_url="https://example.test/a",
  )

  assert first == second
