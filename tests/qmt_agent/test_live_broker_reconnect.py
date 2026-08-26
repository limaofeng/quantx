import threading
from types import SimpleNamespace

from quantx_qmt_agent.broker import LiveBroker, _LiveReportSink
from quantx_qmt_agent.miniqmt.local_agent import MiniQmtLocalAgent
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
  assert registry.connection_generation("account-1") == 1


def test_live_broker_observes_same_manager_health_probe_reconnect():
  registry = object.__new__(XTTradingManagerRegistry)
  health_checks = 0
  reconnect_calls = 0

  class Manager:
    is_connected = True

    def is_account_status_ok(self) -> bool:
      nonlocal health_checks
      health_checks += 1
      return False

    def reconnect(self) -> bool:
      nonlocal reconnect_calls
      reconnect_calls += 1
      self.is_connected = True
      return True

  manager = Manager()
  registry._managers = {"account-1": manager}
  registry._last_reconnect_attempts = {}
  registry._connection_generations = {}
  registry._reconnect_interval = 0.0

  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": FakeAgent(manager)}
  broker._trading_registry = registry
  broker._trading_journal = SimpleNamespace()
  broker._trading_access_lock = threading.RLock()
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 0
  broker._trading_reconciled_generation = 0
  broker._registry_trading_generations = {"account-1": 0}

  assert broker.ensure_trading_ready() is True
  assert health_checks == 1
  assert reconnect_calls == 1
  assert broker.trading_connection_generation() == 1
  assert broker.trading_requires_reconciliation() is True


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
  assert broker.trading_connection_generation() == 1


def test_live_broker_rejects_new_orders_until_generation_is_reconciled():
  placed_orders: list[dict] = []
  cancelled_orders: list[str] = []

  class Agent:
    trading_manager = SimpleNamespace(is_connected=True)

    @staticmethod
    def mark_report_received() -> None:
      return None

    @staticmethod
    def place_order(command):
      placed_orders.append(command)
      return {"success": True, "message": "", "order_id": "order-1"}

    @staticmethod
    def cancel_order(order_id):
      cancelled_orders.append(order_id)
      return {"success": True, "message": ""}

  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": Agent()}
  broker._trading_access_lock = threading.RLock()
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 1
  broker._trading_reconciled_generation = 0
  broker.ensure_trading_ready = lambda: True

  rejected = broker.execute(
    {
      "account_id": "account-1",
      "client_order_id": "client-1",
      "command_kind": "PLACE_ORDER",
      "side": "BUY",
      "order_type": "LIMIT",
      "limit_price": 10,
    }
  )
  cancelled = broker.execute(
    {
      "account_id": "account-1",
      "command_kind": "CANCEL_ORDER",
      "broker_order_id": "order-previous",
    }
  )

  assert rejected == {
    "accepted": False,
    "reason": "local_reconciliation_required",
    "reports": [],
  }
  assert placed_orders == []
  assert cancelled["accepted"] is True
  assert cancelled_orders == ["order-previous"]
  assert broker.mark_trading_reconciled(0) is False
  assert broker.mark_trading_reconciled(1) is True

  accepted = broker.execute(
    {
      "account_id": "account-1",
      "client_order_id": "client-2",
      "command_kind": "PLACE_ORDER",
      "side": "BUY",
      "order_type": "LIMIT",
      "limit_price": 10,
    }
  )

  assert accepted["accepted"] is True
  assert accepted["broker_order_id"] == "order-1"
  assert len(placed_orders) == 1


def test_disconnected_live_snapshot_is_never_complete():
  manager = SimpleNamespace(is_connected=False)
  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": FakeAgent(manager)}
  broker._trading_access_lock = threading.RLock()

  snapshot = broker.full_snapshot()

  assert snapshot["is_complete"] is False
  assert snapshot["unavailable_accounts"] == ["account-1"]
  assert snapshot["positions_by_account"] == {"account-1": []}


def test_trade_query_failure_marks_live_snapshot_sections_incomplete():
  class Manager:
    is_connected = True

    def get_account_info(self):
      return {"cash": 100_000}

    def get_positions(self):
      return []

    def get_orders(self, _cancelable_only=False):
      return []

    def get_trades(self):
      raise RuntimeError("native trade query failed")

  agent = MiniQmtLocalAgent(Manager())
  local_snapshot = agent.full_snapshot()

  assert local_snapshot["trades"] == []
  assert local_snapshot["section_completeness"] == {
    "account": True,
    "positions": True,
    "orders": True,
    "trades": False,
  }
  assert local_snapshot["is_complete"] is False
  assert agent.preflight_check()["status"] == "RECONCILE_REQUIRED"

  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": agent}
  broker._trading_access_lock = threading.RLock()

  snapshot = broker.full_snapshot()

  assert snapshot["is_complete"] is False
  assert snapshot["unavailable_accounts"] == ["account-1"]
  assert snapshot["section_completeness_by_account"]["account-1"][
    "trades"
  ] is False
  assert agent.last_report_time is None


def test_native_position_query_failure_cannot_become_authoritative_empty():
  class NativeTrader:
    @staticmethod
    def query_stock_positions(_account):
      raise RuntimeError("native position query failed")

    @staticmethod
    def query_stock_orders(_account, _cancelable_only):
      return []

    @staticmethod
    def query_stock_trades(_account):
      return []

  manager = object.__new__(XTTradingManager)
  manager.account_id = "account-1"
  manager.acc = object()
  manager.xttrader = NativeTrader()
  manager.is_connected = True
  manager.get_account_info = lambda: {"cash": 100_000}
  agent = MiniQmtLocalAgent(manager)

  snapshot = agent.full_snapshot()

  assert snapshot["positions"] == []
  assert snapshot["section_completeness"]["positions"] is False
  assert snapshot["is_complete"] is False
  assert agent.preflight_check()["ok"] is False
