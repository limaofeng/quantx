import asyncio
import contextlib

import pytest
from quantx_infrastructure.services.intraday_volume_scanner import IntradayVolumeScanner


class FakeManager:
  def __init__(self):
    self.callback = None
    self.market_codes = None
    self.unsubscribed = None

  def subscribe_whole_quote(self, market_codes, callback):
    self.market_codes = market_codes
    self.callback = callback
    return 1001

  def unsubscribe_whole_quote(self, sub_id):
    self.unsubscribed = sub_id


class FakeRegistry:
  def __init__(self, manager):
    self.manager = manager

  def get_manager(self):
    return self.manager


def baseline():
  return {
    "code": "000001.SZ",
    "name": "平安银行",
    "industry": "银行",
    "instrument_type": "stock",
    "avg_volume_20": 2400.0,
    "avg_amount_20": 24000.0,
    "float_volume": 8_000_000.0,
  }


def feed_two_ticks(scanner: IntradayVolumeScanner):
  scanner.update_tick(
    "000001.SZ",
    {
      "lastPrice": 10.0,
      "preClose": 9.8,
      "volume": 1000,
      "amount": 10000,
      "transactionNum": 100,
      "bidVol": [1000, 800, 600, 400, 200],
      "askVol": [100, 80, 60, 40, 20],
      "timetag": "20260604 09:59:00",
    },
  )
  scanner.update_tick(
    "000001.SZ",
    {
      "lastPrice": 10.2,
      "preClose": 9.8,
      "volume": 2600,
      "amount": 30000,
      "transactionNum": 108,
      "bidVol": [1000, 800, 600, 400, 200],
      "askVol": [100, 80, 60, 40, 20],
      "timetag": "20260604 10:00:00",
    },
  )


@pytest.mark.asyncio
async def test_intraday_volume_scanner_subscribes_whole_market_and_scores_ticks():
  manager = FakeManager()
  scanner = IntradayVolumeScanner(
    data_registry_factory=lambda: FakeRegistry(manager),
    idle_stop_seconds=10,
  )

  try:
    assert await scanner.start() is True
    assert manager.market_codes == ["SH", "SZ"]
    assert callable(manager.callback)

    feed_two_ticks(scanner)
    result = scanner.screen(
      [baseline()],
      min_volume_pace_ratio=2.0,
      min_amount_pace_ratio=2.0,
      min_last_5m_volume_ratio=2.0,
      min_intraday_turnover_rate=1.0,
      min_depth_imbalance_5=0.2,
    )

    assert result["total"] == 1
    item = result["items"][0]
    assert item["code"] == "000001.SZ"
    assert item["volume_ratio"] > 1
    assert item["volume_pace_ratio"] > 2
    assert item["amount_pace_ratio"] > 2
    assert item["last_5m_volume_ratio"] > 2
    assert item["intraday_turnover_rate_pct"] > 1
    assert item["depth_imbalance_5"] > 0.2
    assert "盘中放量" in item["matched_signals"]
    assert "成交额加速" in item["matched_signals"]
    assert "近5分钟放量" in item["matched_signals"]
    assert "买盘占优" in item["matched_signals"]
    assert "成交活跃" in item["matched_signals"]
  finally:
    scanner.stop()
    if scanner._idle_task is not None:
      scanner._idle_task.cancel()
      with contextlib.suppress(asyncio.CancelledError):
        await scanner._idle_task


def test_intraday_volume_scanner_filters_by_thresholds():
  scanner = IntradayVolumeScanner(
    data_registry_factory=lambda: FakeRegistry(FakeManager())
  )
  feed_two_ticks(scanner)

  result = scanner.screen([baseline()], min_volume_pace_ratio=100.0)

  assert result["total"] == 0
