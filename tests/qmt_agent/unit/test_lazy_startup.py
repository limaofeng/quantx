import asyncio

import pytest

pytest.importorskip("xtquant", reason="miniQMT SDK is only available on the QMT host")


def test_global_xt_data_manager_is_lazy(monkeypatch):
  from quantx_qmt_agent.miniqmt.data import data_manager as data_manager_module

  data_manager_module.xt_data_manager.close_connection()

  def fail_if_connected(_self):
    raise AssertionError("XTDataManager should not connect during import/proxy checks")

  monkeypatch.setattr(
    data_manager_module.XTDataManager,
    "_init_connection",
    fail_if_connected,
  )

  assert data_manager_module.xt_data_manager.is_connected is False
  assert data_manager_module.xt_data_manager._manager is None


def test_subscribe_quote_passes_scalar_stock_code_to_xtquant(monkeypatch):
  from quantx_qmt_agent.miniqmt.data import data_manager as data_manager_module

  calls = []

  class FakeXTData:
    def subscribe_quote2(self, stock_code, **kwargs):
      calls.append((stock_code, kwargs))
      return 42

  monkeypatch.setattr(data_manager_module, "xtdata", FakeXTData())

  manager = object.__new__(data_manager_module.XTDataManager)
  manager.is_connected = True
  manager._subscription_ids = set()
  sub_id = manager.subscribe_quote(["600900.SH"], period="1m", count=-1)

  assert sub_id == 42
  assert calls[0][0] == "600900.SH"
  assert calls[0][1]["period"] == "1m"
  assert calls[0][1]["count"] == -1


def test_unified_subscription_manager_defers_data_manager():
  from quantx_infrastructure.core.data.unified_subscription_manager import (
    UnifiedDataSubscriptionManager,
  )

  class FakeRegistry:
    def __init__(self):
      self.called = False

    def get_manager(self):
      self.called = True
      return object()

  manager = UnifiedDataSubscriptionManager()
  registry = FakeRegistry()
  manager._data_manager = None
  manager.data_manager_registry = registry

  assert manager._data_manager is None
  assert registry.called is False

  assert manager.data_manager is not None
  assert registry.called is True


def test_unified_subscription_manager_uses_unique_handles_for_same_owner():
  from quantx_infrastructure.core.data.unified_subscription_manager import (
    UnifiedDataSubscriptionManager,
  )

  async def run_case():
    class FakeHub:
      def __init__(self):
        self.callbacks = {}
        self.cancelled = []

      async def subscribe_tick(self, stock_code, callback, *, delivery):
        del stock_code, delivery
        handle = f"hub-{len(self.callbacks) + 1}"
        self.callbacks[handle] = callback
        return handle

      async def unsubscribe(self, handle):
        self.cancelled.append(handle)
        return True

      def snapshot(self):
        return {}

    original = UnifiedDataSubscriptionManager._instance
    try:
      UnifiedDataSubscriptionManager._instance = None
      manager = UnifiedDataSubscriptionManager()
      manager.hub = FakeHub()
      first = await manager.subscribe(
        "600900.SH", lambda _data: None, "same-owner", "tick"
      )
      second = await manager.subscribe(
        "600900.SH", lambda _data: None, "same-owner", "tick"
      )
      assert first != second
      assert await manager.unsubscribe(first)
      assert manager.hub.cancelled == ["hub-1"]
      assert await manager.unsubscribe(second)
      assert manager.hub.cancelled == ["hub-1", "hub-2"]
    finally:
      UnifiedDataSubscriptionManager._instance = original

  asyncio.run(run_case())


def test_realtime_adapter_attempts_xtquant_by_default():
  from quantx_infrastructure.core.data.realtime import RealtimeDataAdapter

  class ConnectedDataManager:
    is_connected = True

  class FakeSubscriptionManager:
    def __init__(self):
      self.called = False

    @property
    def data_manager(self):
      self.called = True
      return ConnectedDataManager()

  adapter = RealtimeDataAdapter()
  subscription_manager = FakeSubscriptionManager()
  adapter.subscription_manager = subscription_manager

  result = asyncio.run(adapter.connect())

  assert result is True
  assert adapter.is_connected is True
  assert subscription_manager.called is True


def test_realtime_adapter_degrades_when_xtquant_connect_fails():
  from quantx_infrastructure.core.data.realtime import RealtimeDataAdapter

  class FailingSubscriptionManager:
    @property
    def data_manager(self):
      raise RuntimeError("miniQMT unavailable")

  adapter = RealtimeDataAdapter()
  adapter.subscription_manager = FailingSubscriptionManager()

  result = asyncio.run(adapter.connect())

  assert result is False
  assert adapter.is_connected is False


def test_xt_trading_manager_account_status_ok():
  from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager
  from xtquant import xtconstant

  class FakeAccount:
    account_id = "300000013250"
    account_type = "STOCK"

  class FakeStatus:
    account_id = "300000013250"
    account_type = "STOCK"
    status = xtconstant.ACCOUNT_STATUS_OK

  class FakeTrader:
    def query_account_status(self):
      return [FakeStatus()]

  manager = object.__new__(XTTradingManager)
  manager.is_connected = True
  manager.acc = FakeAccount()
  manager.xttrader = FakeTrader()

  assert manager.is_account_status_ok() is True


def test_xt_trading_manager_account_status_rejects_non_ok_status():
  from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager
  from xtquant import xtconstant

  class FakeAccount:
    account_id = "300000013250"
    account_type = "STOCK"

  class FakeStatus:
    account_id = "300000013250"
    account_type = "STOCK"
    status = xtconstant.ACCOUNT_STATUS_FAIL

  class FakeTrader:
    def query_account_status(self):
      return [FakeStatus()]

  manager = object.__new__(XTTradingManager)
  manager.is_connected = True
  manager.acc = FakeAccount()
  manager.xttrader = FakeTrader()

  assert manager.is_account_status_ok() is False


def test_xt_trading_manager_close_connection_uses_stop_fallback():
  from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager

  class FakeTrader:
    def __init__(self):
      self.stopped = False

    def stop(self):
      self.stopped = True

  manager = object.__new__(XTTradingManager)
  manager.is_connected = True
  manager.session_id = 123
  manager.xttrader = FakeTrader()
  manager.event_loop = None
  manager.event_loop_thread = None

  manager.close_connection()

  assert manager.xttrader.stopped is True
  assert manager.is_connected is False
  assert manager.session_id is None
