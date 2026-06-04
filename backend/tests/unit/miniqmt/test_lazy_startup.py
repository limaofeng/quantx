import asyncio
import threading


def test_global_xt_data_manager_is_lazy(monkeypatch):
  from miniqmt.data import data_manager as data_manager_module

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
  from miniqmt.data import data_manager as data_manager_module

  calls = []

  class FakeXTData:
    def subscribe_quote2(self, stock_code, **kwargs):
      calls.append((stock_code, kwargs))
      return 42

  monkeypatch.setattr(data_manager_module, "xtdata", FakeXTData())

  manager = object.__new__(data_manager_module.XTDataManager)
  sub_id = manager.subscribe_quote(["600900.SH"], period="1m", count=-1)

  assert sub_id == 42
  assert calls[0][0] == "600900.SH"
  assert calls[0][1]["period"] == "1m"
  assert calls[0][1]["count"] == -1


def test_unified_subscription_manager_defers_data_manager():
  from core.data.unified_subscription_manager import UnifiedDataSubscriptionManager

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


def test_unified_subscription_manager_dispatches_sync_callback_to_main_loop():
  from core.data.unified_subscription_manager import UnifiedDataSubscriptionManager

  async def run_case():
    manager = UnifiedDataSubscriptionManager()
    manager.set_main_loop(asyncio.get_running_loop())
    called = asyncio.Event()

    async def mark_called():
      called.set()

    def callback(_data):
      asyncio.create_task(mark_called())

    thread = threading.Thread(
      target=lambda: manager._invoke_callback(callback, {"600900.SH": {}}, "600900.SH")
    )
    thread.start()
    thread.join(timeout=1)

    await asyncio.wait_for(called.wait(), timeout=1)

  asyncio.run(run_case())


def test_realtime_adapter_attempts_xtquant_by_default():
  from core.data.realtime import RealtimeDataAdapter

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
  from core.data.realtime import RealtimeDataAdapter

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
  from miniqmt.trading.trading_manager import XTTradingManager
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
  from miniqmt.trading.trading_manager import XTTradingManager
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
  from miniqmt.trading.trading_manager import XTTradingManager

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


def test_miniqmt_health_probe_requires_ok_account_status(monkeypatch):
  import main
  from miniqmt import manager_registry as registry_module
  from miniqmt.trading import trading_manager as trading_manager_module

  class FakeDataRegistry:
    def get_stats(self):
      return {
        "total_data_managers": 0,
        "connected_data_managers": 0,
        "disconnected_data_managers": 0,
      }

  class FakeTradingRegistry:
    def __init__(self):
      self._lock = threading.RLock()
      self._managers = {}

    def get_stats(self):
      return {
        "total_managers": len(self._managers),
        "connected_managers": sum(
          1 for manager in self._managers.values() if manager.is_connected
        ),
        "disconnected_managers": sum(
          1 for manager in self._managers.values() if not manager.is_connected
        ),
      }

  class FakeTradingManager:
    is_connected = True

    def __init__(self, account_id):
      self.account_id = account_id

    def is_account_status_ok(self):
      return True

  monkeypatch.setattr(registry_module, "XTDataManagerRegistry", FakeDataRegistry)
  monkeypatch.setattr(registry_module, "XTTradingManagerRegistry", FakeTradingRegistry)
  monkeypatch.setattr(trading_manager_module, "XTTradingManager", FakeTradingManager)

  status = main._probe_miniqmt_health()

  assert status["available"] is True
  assert status["account_checked"] is True
  assert status["account_connected"] is True
  assert status["connected"] is True
  assert status["connection_state"] == "account_verified"


def test_miniqmt_health_probe_rejects_connected_session_without_ok_account(monkeypatch):
  import main
  from miniqmt import manager_registry as registry_module
  from miniqmt.trading import trading_manager as trading_manager_module

  class FakeDataRegistry:
    def get_stats(self):
      return {
        "total_data_managers": 0,
        "connected_data_managers": 0,
        "disconnected_data_managers": 0,
      }

  class FakeTradingRegistry:
    def __init__(self):
      self._lock = threading.RLock()
      self._managers = {}

    def get_stats(self):
      return {
        "total_managers": len(self._managers),
        "connected_managers": sum(
          1 for manager in self._managers.values() if manager.is_connected
        ),
        "disconnected_managers": sum(
          1 for manager in self._managers.values() if not manager.is_connected
        ),
      }

  class FakeTradingManager:
    is_connected = True

    def __init__(self, account_id):
      self.account_id = account_id

    def is_account_status_ok(self):
      return False

  monkeypatch.setattr(registry_module, "XTDataManagerRegistry", FakeDataRegistry)
  monkeypatch.setattr(registry_module, "XTTradingManagerRegistry", FakeTradingRegistry)
  monkeypatch.setattr(trading_manager_module, "XTTradingManager", FakeTradingManager)

  status = main._probe_miniqmt_health()

  assert status["available"] is False
  assert status["trading_connected"] is True
  assert status["account_checked"] is True
  assert status["account_connected"] is False
  assert status["connected"] is False
  assert status["connection_state"] == "connected_account_unavailable"
  assert status["account_error"] == "account_status_not_ok"
