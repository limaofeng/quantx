import threading
from types import SimpleNamespace

from quantx_qmt_agent.broker import LiveBroker, _LiveReportSink
from quantx_qmt_agent.miniqmt.manager_registry import XTTradingManagerRegistry
from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager


class FakeAgent:
  def __init__(self, manager):
    self.trading_manager = manager

  def mark_report_received(self) -> None:
    return None

  def full_snapshot(self):
    raise AssertionError("disconnected accounts must not be queried")


def test_registry_reuses_native_manager_during_reconnect():
  registry = object.__new__(XTTradingManagerRegistry)
  reconnect_calls = 0
  manager = SimpleNamespace(is_connected=False)

  def reconnect() -> bool:
    nonlocal reconnect_calls
    reconnect_calls += 1
    manager.is_connected = True
    return True

  manager.reconnect = reconnect
  registry._managers = {"account-1": manager}
  registry._last_reconnect_attempts = {}
  registry._reconnect_interval = 0.0

  result = registry.get_manager("account-1")

  assert result is manager
  assert registry._managers["account-1"] is manager
  assert reconnect_calls == 1


def test_trading_manager_reconnect_does_not_restart_native_client():
  class NativeTrader:
    def __init__(self) -> None:
      self.connect_calls = 0

    def connect(self) -> int:
      self.connect_calls += 1
      return 0

  native = NativeTrader()
  manager = object.__new__(XTTradingManager)
  manager.account_id = "account-1"
  manager.session_id = 123
  manager.is_connected = False
  manager.xttrader = native
  manager._native_started = True

  assert manager.reconnect() is True
  assert manager.is_connected is True
  assert native.connect_calls == 1


def test_live_broker_rebinds_reconnected_manager():
  old_manager = SimpleNamespace(is_connected=False)
  new_manager = SimpleNamespace(is_connected=True)
  agent = FakeAgent(old_manager)
  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": agent}
  broker._trading_registry = SimpleNamespace(
    get_manager=lambda account_id, reconnect: new_manager
  )
  broker._trading_journal = SimpleNamespace()
  broker._trading_access_lock = threading.RLock()

  assert broker.ensure_trading_ready() is True
  assert agent.trading_manager is new_manager
  assert isinstance(new_manager.trading_service, _LiveReportSink)


def test_disconnected_live_snapshot_is_never_complete():
  manager = SimpleNamespace(is_connected=False)
  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": FakeAgent(manager)}
  broker._trading_access_lock = threading.RLock()

  snapshot = broker.full_snapshot()

  assert snapshot["is_complete"] is False
  assert snapshot["unavailable_accounts"] == ["account-1"]
  assert snapshot["positions_by_account"] == {"account-1": []}
