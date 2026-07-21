"""
日常市场数据同步流的集成测试（带依赖模拟）

这些测试通过最小化的依赖替身来验证流程的关键路径，
同时仍可在需要时替换为真实环境以做端到端验收。
"""

import importlib
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from database.connection import redis_client
from miniqmt.manager_registry import XTDataManagerRegistry
from prefector.flows.daily_market_data_sync_flow import (
  _process_single_day_data,
  daily_market_data_sync_flow,
)
from services.instrument_service import InstrumentService

daily_market_data_sync_module = importlib.import_module(
  "prefector.flows.daily_market_data_sync_flow"
)


@pytest.fixture
def redis_stub(monkeypatch):
  """
  记录并控制 Redis exists/set 行为，避免真正写入外部缓存。
  """
  state = {
    "exists": False,
    "exists_calls": [],
    "set_calls": [],
    "get_calls": [],
    "delete_calls": [],
    "values": {},
  }

  def fake_exists(key: str) -> bool:
    state["exists_calls"].append(key)
    return state["exists"]

  def fake_set(key: str, value: str, ex=None, nx=False) -> bool:
    state["set_calls"].append((key, value, ex, nx))
    if nx and key in state["values"]:
      return False
    state["values"][key] = value
    return True

  def fake_get(key: str):
    state["get_calls"].append(key)
    return state["values"].get(key)

  def fake_delete(*keys: str) -> int:
    state["delete_calls"].append(keys)
    deleted = 0
    for key in keys:
      if key in state["values"]:
        deleted += 1
        del state["values"][key]
    return deleted

  monkeypatch.setattr(redis_client, "exists", fake_exists)
  monkeypatch.setattr(redis_client, "set", fake_set)
  monkeypatch.setattr(redis_client, "get", fake_get)
  monkeypatch.setattr(redis_client, "delete", fake_delete)
  return state


@pytest.fixture
def instrument_stub(monkeypatch):
  """
  拦截 InstrumentService.find_all，返回可配置的股票列表。
  """
  state = {
    "return": [
      SimpleNamespace(id="000001.SZ"),
      SimpleNamespace(id="000002.SZ"),
    ],
    "calls": [],
  }

  async def fake_find_all(self, where=None, sort=None, limit=None, skip=None):
    state["calls"].append({"where": where, "sort": sort})
    return state["return"]

  monkeypatch.setattr(
    InstrumentService,
    "find_all",
    fake_find_all,
    raising=False,
  )
  return state


@pytest.fixture
def trading_calendar_stub(monkeypatch):
  """
  控制交易日返回，防止访问真实行情服务。
  """
  state = {"dates": []}

  class FakeManager:
    def get_trading_dates(self, market, start_date, end_date):
      return state["dates"]

  fake_manager = FakeManager()
  monkeypatch.setattr(
    XTDataManagerRegistry,
    "get_manager",
    lambda self: fake_manager,
    raising=False,
  )
  return state


@pytest.fixture(autouse=True)
def report_tasks_stub(monkeypatch):
  """
  使生成报告与写文件的任务成为空操作，避免外部 IO。
  """
  report_state = {"calls": [], "saved": []}

  async def fake_generate_sync_report(**kwargs):
    report_state["calls"].append(kwargs)
    return {"mock_report": True, **kwargs}

  async def fake_save_report_to_file(report, report_type):
    report_state["saved"].append({"report": report, "type": report_type})

  monkeypatch.setattr(
    daily_market_data_sync_module,
    "generate_sync_report",
    fake_generate_sync_report,
  )
  monkeypatch.setattr(
    daily_market_data_sync_module,
    "save_report_to_file",
    fake_save_report_to_file,
  )
  return report_state


@pytest.mark.integration
class TestDailyMarketDataSyncFlowIntegration:
  """
  覆盖关键路径与参数校验的集成级测试（依赖模拟）。
  """

  @pytest.mark.asyncio
  async def test_skip_when_overall_cache_exists(
    self,
    redis_stub,
    instrument_stub,
    trading_calendar_stub,
  ):
    redis_stub["exists"] = True
    trading_calendar_stub["dates"] = [datetime(2025, 1, 8).date()]

    result = await daily_market_data_sync_flow(
      sectors=["测试板块"],
      start_time="20250108",
      end_time="20250108",
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "already_completed"
    assert "cache_key" in result
    assert result["stock_count"] == len(instrument_stub["return"])
    assert redis_stub["exists_calls"], "应查询整体缓存标记"
    assert not redis_stub["set_calls"], "跳过时不应写入完成标记或运行锁"

  @pytest.mark.asyncio
  async def test_skip_when_same_scope_is_already_running(
    self,
    monkeypatch,
    redis_stub,
    instrument_stub,
    trading_calendar_stub,
  ):
    trading_calendar_stub["dates"] = [datetime(2025, 1, 8).date()]

    monkeypatch.setattr(
      daily_market_data_sync_module,
      "_acquire_sync_lock",
      lambda lock_key, lock_token: False,
    )

    result = await daily_market_data_sync_flow(
      sectors=["测试板块"],
      start_time="20250108",
      end_time="20250108",
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "already_running"
    assert "lock_key" in result

  @pytest.mark.asyncio
  async def test_empty_time_uses_prefect_scheduled_day(
    self,
    monkeypatch,
    redis_stub,
    instrument_stub,
    trading_calendar_stub,
  ):
    trading_calendar_stub["dates"] = [datetime(2025, 1, 8).date()]
    call_state = {}

    monkeypatch.setattr(
      daily_market_data_sync_module,
      "_get_prefect_scheduled_start_time",
      lambda: datetime(2025, 1, 8, 15, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    async def fake_process_single_day_data(
      stock_list,
      single_day_time,
      periods,
      skip_download=False,
    ):
      call_state["single_day_time"] = single_day_time
      return {
        "success_count": len(stock_list),
        "failed_count": 0,
        "errors": [],
        "chunk_results": [],
        "duration_seconds": 0,
      }

    monkeypatch.setattr(
      daily_market_data_sync_module,
      "_process_single_day_data",
      fake_process_single_day_data,
    )

    result = await daily_market_data_sync_flow(
      sectors=["测试板块"],
      periods=["1m"],
    )

    assert result["status"] == "success"
    assert call_state["single_day_time"] == "20250108"

  @pytest.mark.asyncio
  async def test_fail_on_invalid_periods(self, redis_stub):
    result = await daily_market_data_sync_flow(
      stock_list=["000001.SZ"],
      start_time="20250108",
      end_time="20250108",
      periods=["5m"],  # 不支持的周期
    )

    assert result["status"] == "failed"
    assert result["reason"] == "invalid_periods"
    assert not redis_stub["set_calls"], "校验失败时不应落盘缓存"

  @pytest.mark.asyncio
  async def test_fail_when_target_missing(self):
    result = await daily_market_data_sync_flow(
      start_time="20250108",
      end_time="20250108",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "missing_target"

  @pytest.mark.asyncio
  async def test_successful_execution_aggregates_results(
    self,
    monkeypatch,
    redis_stub,
    instrument_stub,
    trading_calendar_stub,
  ):
    trading_calendar_stub["dates"] = [datetime(2025, 1, 8).date()]

    call_state = {}

    async def fake_process_single_day_data(
      stock_list,
      single_day_time,
      periods,
      skip_download=False,
    ):
      call_state["args"] = {
        "stock_list": stock_list,
        "single_day_time": single_day_time,
        "periods": periods,
        "skip_download": skip_download,
      }
      return {
        "success_count": len(stock_list),
        "failed_count": 0,
        "errors": [],
        "chunk_results": [{"chunk_idx": 1, "date": single_day_time}],
        "duration_seconds": 1.23,
      }

    monkeypatch.setattr(
      daily_market_data_sync_module,
      "_process_single_day_data",
      fake_process_single_day_data,
    )

    result = await daily_market_data_sync_flow(
      sectors=["测试板块"],
      start_time="20250108",
      end_time="20250108",
      periods=["1m"],
    )

    assert result["status"] == "success"
    assert result["success_count"] == len(instrument_stub["return"])
    assert result["failed_count"] == 0
    assert result["processed_dates"], "应记录按日处理结果"
    assert redis_stub["set_calls"], "成功后应写入整体完成标记"

    assert call_state["args"]["stock_list"] == [
      inst.id for inst in instrument_stub["return"]
    ]
    assert call_state["args"]["periods"] == ["1m"]
    assert call_state["args"]["single_day_time"] == "20250108"

  @pytest.mark.asyncio
  async def test_empty_download_result_is_not_marked_completed(
    self,
    monkeypatch,
    redis_stub,
  ):
    """
    下载后仍为空的数据不能写入单日完成缓存，否则后续回测补齐会被假完成标记跳过。
    """
    logger = SimpleNamespace(
      info=lambda *args, **kwargs: None,
      warning=lambda *args, **kwargs: None,
      error=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
      daily_market_data_sync_module,
      "get_run_logger",
      lambda: logger,
    )

    async def fake_download_market_data(**kwargs):
      return None

    async def fake_save_market_data(period, market_data):
      return {"saved_count": 0}

    class FakeDataManager:
      def get_market_data(self, **kwargs):
        return {"562500.SH": pd.DataFrame()}

    monkeypatch.setattr(
      daily_market_data_sync_module,
      "download_market_data",
      fake_download_market_data,
    )
    monkeypatch.setattr(
      daily_market_data_sync_module,
      "save_market_data",
      fake_save_market_data,
    )
    monkeypatch.setattr(
      XTDataManagerRegistry,
      "get_manager",
      lambda self: FakeDataManager(),
      raising=False,
    )

    result = await _process_single_day_data(
      stock_list=["562500.SH"],
      single_day_time="20260407",
      periods=["tick"],
    )

    assert result["success_count"] == 0
    assert result["failed_count"] == 1
    assert result["chunk_results"][0]["results"] == [
      {
        "stock_code": "562500.SH",
        "status": "failed",
        "error": "empty_market_data",
      }
    ]
    assert (
      "daily_market_data_stock:562500.SH:20260407:tick",
      "done",
      None,
      False,
    ) not in redis_stub["set_calls"]
