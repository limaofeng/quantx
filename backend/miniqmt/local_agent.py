"""Local protection and reconcile agent for miniQMT live trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class LocalAgentStatus(str, Enum):
  READY = "READY"
  DISCONNECTED = "DISCONNECTED"
  RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
  KILL_SWITCH = "KILL_SWITCH"


@dataclass
class DeltaReport:
  report_id: str
  generated_at: datetime
  order_delta: List[Dict[str, Any]] = field(default_factory=list)
  trade_delta: List[Dict[str, Any]] = field(default_factory=list)
  position_delta: List[Dict[str, Any]] = field(default_factory=list)
  account_delta: Dict[str, Any] = field(default_factory=dict)
  status: LocalAgentStatus = LocalAgentStatus.READY
  reason: str = ""

  def to_dict(self) -> Dict[str, Any]:
    return {
      "report_id": self.report_id,
      "generated_at": self.generated_at.isoformat(),
      "order_delta": self.order_delta,
      "trade_delta": self.trade_delta,
      "position_delta": self.position_delta,
      "account_delta": self.account_delta,
      "status": self.status.value,
      "reason": self.reason,
    }


class MiniQmtLocalAgent:
  """Wrap XTTradingManager with conservative live-trading semantics."""

  def __init__(self, trading_manager: Any, *, max_report_lag_seconds: int = 30) -> None:
    self.trading_manager = trading_manager
    self.max_report_lag_seconds = int(max_report_lag_seconds or 30)
    self.status = LocalAgentStatus.READY
    self.last_report_time: Optional[datetime] = None
    self.last_full_snapshot: Dict[str, Any] = {}

  def place_order(self, command: Any = None, **overrides: Any) -> Dict[str, Any]:
    """Run local protection checks, then submit an order to miniQMT."""
    preflight = self.preflight_check()
    if not preflight.get("ok"):
      return {"success": False, "preflight": preflight, "message": preflight.get("reason")}

    data = _command_data(command)
    data.update({key: value for key, value in overrides.items() if value is not None})
    stock_code = str(
      data.get("stock_code")
      or data.get("instrument_code")
      or data.get("code")
      or ""
    )
    volume = int(data.get("order_volume", data.get("volume", data.get("quantity", 0))) or 0)
    if not stock_code or volume <= 0:
      return {"success": False, "preflight": preflight, "message": "invalid order command"}

    result = _safe_call_with_args(
      self.trading_manager,
      "place_order",
      stock_code,
      _to_miniqmt_order_type(data.get("order_type")),
      volume,
      _to_miniqmt_price_type(data.get("price_type"), data.get("price", 0.0)),
      float(data.get("price", 0.0) or 0.0),
      str(data.get("strategy_name", data.get("strategy_id", "")) or ""),
      str(data.get("order_remark", data.get("remark", "")) or ""),
      default={"success": False, "message": "miniQMT place_order unavailable"},
    )
    if isinstance(result, dict):
      result.setdefault("preflight", preflight)
      return result
    return {"success": bool(result), "result": result, "preflight": preflight}

  def cancel_order(self, order_id: Any) -> Dict[str, Any]:
    preflight = self.preflight_check()
    if not preflight.get("ok"):
      return {"success": False, "preflight": preflight, "message": preflight.get("reason")}
    normalized_order_id = _normalize_order_id(order_id)
    success = _safe_call_with_args(
      self.trading_manager,
      "cancel_order",
      normalized_order_id,
      default=False,
    )
    return {"success": bool(success), "order_id": normalized_order_id, "preflight": preflight}

  def query_account(self) -> Dict[str, Any]:
    return dict(_safe_call(self.trading_manager, "get_account_info", default={}) or {})

  def query_positions(self) -> List[Dict[str, Any]]:
    return [
      _to_dict(item)
      for item in _safe_call(self.trading_manager, "get_positions", default=[])
    ]

  def query_orders(self, *, cancelable_only: bool = False) -> List[Dict[str, Any]]:
    try:
      orders = self.trading_manager.get_orders(cancelable_only)
    except TypeError:
      orders = _safe_call(self.trading_manager, "get_orders", default=[])
    except Exception:
      orders = []
    return [_to_dict(item) for item in orders]

  def query_trades(self) -> List[Dict[str, Any]]:
    return [
      _to_dict(item)
      for item in _safe_call(self.trading_manager, "get_trades", default=[])
    ]

  def preflight_check(self) -> Dict[str, Any]:
    if not bool(getattr(self.trading_manager, "is_connected", False)):
      self.status = LocalAgentStatus.DISCONNECTED
      return {"ok": False, "status": self.status.value, "reason": "miniQMT disconnected"}
    if self.should_kill_switch():
      self.status = LocalAgentStatus.KILL_SWITCH
      return {"ok": False, "status": self.status.value, "reason": "broker report stale"}
    self.status = LocalAgentStatus.READY
    return {"ok": True, "status": self.status.value, "reason": ""}

  def build_delta_report(self, previous_snapshot: Optional[Dict[str, Any]] = None) -> DeltaReport:
    current = self.full_snapshot()
    previous = dict(previous_snapshot or self.last_full_snapshot or {})
    report = DeltaReport(
      report_id=f"delta-{int(datetime.now().timestamp() * 1000)}",
      generated_at=datetime.now(),
      order_delta=_diff_list(previous.get("orders", []), current.get("orders", []), "order_id"),
      trade_delta=_diff_list(previous.get("trades", []), current.get("trades", []), "trade_id"),
      position_delta=_diff_list(
        previous.get("positions", []), current.get("positions", []), "stock_code"
      ),
      account_delta=_diff_dict(previous.get("account", {}), current.get("account", {})),
      status=self.status,
    )
    self.last_report_time = report.generated_at
    self.last_full_snapshot = current
    return report

  def reconcile_snapshots(
    self, expected_snapshot: Dict[str, Any], actual_snapshot: Optional[Dict[str, Any]] = None
  ) -> DeltaReport:
    actual = actual_snapshot or self.full_snapshot()
    report = DeltaReport(
      report_id=f"reconcile-{int(datetime.now().timestamp() * 1000)}",
      generated_at=datetime.now(),
      order_delta=_diff_list(expected_snapshot.get("orders", []), actual.get("orders", []), "order_id"),
      trade_delta=_diff_list(expected_snapshot.get("trades", []), actual.get("trades", []), "trade_id"),
      position_delta=_diff_list(
        expected_snapshot.get("positions", []), actual.get("positions", []), "stock_code"
      ),
      account_delta=_diff_dict(expected_snapshot.get("account", {}), actual.get("account", {})),
    )
    if report.order_delta or report.trade_delta or report.position_delta or report.account_delta:
      report.status = LocalAgentStatus.RECONCILE_REQUIRED
      report.reason = "snapshot_mismatch"
      self.status = LocalAgentStatus.RECONCILE_REQUIRED
    else:
      report.status = LocalAgentStatus.READY
      self.status = LocalAgentStatus.READY
    self.last_report_time = report.generated_at
    self.last_full_snapshot = actual
    return report

  def build_full_reconcile_report(self, expected_snapshot: Dict[str, Any]) -> DeltaReport:
    return self.reconcile_snapshots(expected_snapshot, self.full_snapshot())

  def full_snapshot(self) -> Dict[str, Any]:
    return {
      "account": self.query_account(),
      "positions": self.query_positions(),
      "orders": self.query_orders(),
      "trades": self.query_trades(),
      "connected": bool(getattr(self.trading_manager, "is_connected", False)),
    }

  def should_kill_switch(self, now: Optional[datetime] = None) -> bool:
    if self.last_report_time is None:
      return False
    now = now or datetime.now()
    return now - self.last_report_time > timedelta(seconds=self.max_report_lag_seconds)


def _safe_call(obj: Any, method_name: str, *, default: Any) -> Any:
  try:
    method = getattr(obj, method_name)
  except AttributeError:
    return default
  try:
    return method()
  except Exception:
    return default


def _safe_call_with_args(obj: Any, method_name: str, *args: Any, default: Any) -> Any:
  try:
    method = getattr(obj, method_name)
  except AttributeError:
    return default
  try:
    return method(*args)
  except Exception:
    return default


def _command_data(command: Any) -> Dict[str, Any]:
  if command is None:
    return {}
  if isinstance(command, dict):
    data = dict(command)
  elif is_dataclass(command):
    data = asdict(command)
  else:
    data = _to_dict(command)
  metadata = data.get("metadata")
  if isinstance(metadata, dict):
    data.update({key: value for key, value in metadata.items() if key not in data})
  return data


def _normalize_order_id(order_id: Any) -> Any:
  text = str(order_id)
  if text.isdigit():
    return int(text)
  return order_id


def _to_miniqmt_order_type(value: Any) -> Any:
  text = str(getattr(value, "value", value) or "").upper()
  if text.endswith("SELL") or text == "SELL":
    name = "SELL"
  else:
    name = "BUY"
  try:
    from models.enums import OrderType as MiniOrderType

    return getattr(MiniOrderType, name)
  except Exception:
    return name


def _to_miniqmt_price_type(value: Any, price: Any = 0.0) -> Any:
  text = str(getattr(value, "value", value) or "").upper()
  if not text:
    text = "FIX_PRICE" if float(price or 0.0) > 0 else "LATEST_PRICE"
  if text in {"LIMIT", "FIX", "FIX_PRICE"}:
    name = "FIX_PRICE"
  else:
    name = "LATEST_PRICE"
  try:
    from models.enums import PriceType as MiniPriceType

    return getattr(MiniPriceType, name)
  except Exception:
    return name


def _to_dict(item: Any) -> Dict[str, Any]:
  if item is None:
    return {}
  if isinstance(item, dict):
    return dict(item)
  data = {}
  for key in dir(item):
    if key.startswith("_"):
      continue
    value = getattr(item, key, None)
    if callable(value):
      continue
    if isinstance(value, (str, int, float, bool, type(None))):
      data[key] = value
  return data


def _diff_list(old: List[Dict[str, Any]], new: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
  old_map = {str(item.get(key)): item for item in old if item.get(key) is not None}
  new_map = {str(item.get(key)): item for item in new if item.get(key) is not None}
  changes: List[Dict[str, Any]] = []
  for item_key, item in new_map.items():
    if item_key not in old_map:
      changes.append({"type": "added", "key": item_key, "value": item})
    elif old_map[item_key] != item:
      changes.append({"type": "changed", "key": item_key, "before": old_map[item_key], "after": item})
  for item_key, item in old_map.items():
    if item_key not in new_map:
      changes.append({"type": "removed", "key": item_key, "value": item})
  return changes


def _diff_dict(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
  changes: Dict[str, Any] = {}
  keys = set(old.keys()) | set(new.keys())
  for key in keys:
    if old.get(key) != new.get(key):
      changes[key] = {"before": old.get(key), "after": new.get(key)}
  return changes
