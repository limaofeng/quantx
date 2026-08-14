"""Local protection and reconcile agent for miniQMT live trading."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from quantx_qmt_agent import clock
from quantx_qmt_agent.qmt_types import OrderType, PriceType

_QMT_ORDER_STATUS_NAMES = {
  48: "PENDING",
  49: "SUBMITTED",
  50: "SUBMITTED",
  51: "SUBMITTED",
  52: "PARTIAL_FILLED",
  53: "CANCELLED",
  54: "CANCELLED",
  55: "PARTIAL_FILLED",
  56: "FILLED",
  57: "REJECTED",
  255: "PENDING",
}
_ACTIVE_QMT_ORDER_STATUSES = {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}
_ASHARE_SESSION_CLOSE = time(15, 0)


def _normalized_qmt_order_status(value: Any) -> str:
  try:
    return _QMT_ORDER_STATUS_NAMES[int(value)]
  except (TypeError, ValueError, KeyError):
    text = str(value or "").strip().upper()
    aliases = {
      "UNREPORTED": "PENDING",
      "WAIT_REPORTING": "SUBMITTED",
      "REPORTED": "SUBMITTED",
      "REPORTED_CANCEL": "SUBMITTED",
      "PARTSUCC_CANCEL": "PARTIAL_FILLED",
      "PART_SUCC": "PARTIAL_FILLED",
      "PART_CANCEL": "CANCELLED",
      "CANCELED": "CANCELLED",
      "SUCCEEDED": "FILLED",
      "JUNK": "REJECTED",
    }
    return aliases.get(text, text or "PENDING")


def _order_time_in_shanghai(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return clock.to_shanghai(value)
  text = str(value or "").strip()
  if not text:
    return None
  if text.isdigit() and len(text) == 14 and text.startswith(("19", "20")):
    try:
      return datetime.strptime(text, "%Y%m%d%H%M%S").replace(
        tzinfo=clock.SHANGHAI_TZ
      )
    except ValueError:
      return None
  try:
    numeric = float(text)
  except (TypeError, ValueError):
    try:
      return clock.to_shanghai(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
      return None
  if numeric > 10_000_000_000:
    numeric /= 1000.0
  try:
    return datetime.fromtimestamp(numeric, tz=clock.SHANGHAI_TZ)
  except (OSError, OverflowError, ValueError):
    return None


def _order_identity(value: Dict[str, Any]) -> str:
  return str(value.get("order_id") or value.get("broker_order_id") or "")


def _with_effective_order_status(
  value: Dict[str, Any],
  *,
  observed_at: datetime,
  cancelable_order_ids: Optional[set[str]],
) -> Dict[str, Any]:
  """Preserve QMT's raw status and derive A-share day-order expiry."""
  order = dict(value)
  raw_status = order.get("order_status", order.get("status"))
  effective_status = _normalized_qmt_order_status(raw_status)
  identity = _order_identity(order)
  can_cancel = (
    identity in cancelable_order_ids
    if identity and cancelable_order_ids is not None
    else None
  )
  order_time = _order_time_in_shanghai(order.get("order_time"))
  observed = clock.to_shanghai(observed_at)
  stock_code = str(
    order.get("stock_code") or order.get("instrument_code") or ""
  ).upper()
  is_ashare = bool(re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", stock_code))
  session_closed = bool(
    order_time
    and (
      order_time.date() < observed.date()
      or (
        order_time.date() == observed.date()
        and observed.timetz().replace(tzinfo=None) >= _ASHARE_SESSION_CLOSE
      )
    )
  )
  session_expired = bool(
    is_ashare
    and effective_status in _ACTIVE_QMT_ORDER_STATUSES
    and int(order.get("traded_volume") or 0) == 0
    and session_closed
  )
  if session_expired:
    effective_status = "EXPIRED"

  order["effective_order_status"] = effective_status
  order["session_expired"] = session_expired
  order["can_cancel"] = can_cancel
  order["effective_status_reason"] = (
    "MARKET_SESSION_CLOSED" if session_expired else ""
  )
  if order_time is not None:
    order["order_session_date"] = order_time.date().isoformat()
  return order


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

  def __init__(
    self,
    trading_manager: Any,
    *,
    market_data_manager: Any = None,
    max_report_lag_seconds: int = 90,
    max_quote_lag_seconds: int = 15,
  ) -> None:
    self.trading_manager = trading_manager
    self.market_data_manager = market_data_manager
    self.max_report_lag_seconds = int(max_report_lag_seconds or 30)
    self.max_quote_lag_seconds = int(max_quote_lag_seconds or 15)
    self.status = LocalAgentStatus.READY
    self.last_report_time: Optional[datetime] = None
    self.last_full_snapshot: Dict[str, Any] = {}

  def mark_report_received(self, at: Optional[datetime] = None) -> None:
    """Record a locally persisted callback or successful reconciliation snapshot."""
    self.last_report_time = clock.to_shanghai(at or clock.now())

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
    command_check = self._command_preflight(data, stock_code, volume)
    if not command_check.get("ok"):
      return {
        "success": False,
        "preflight": command_check,
        "message": command_check.get("reason"),
      }

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

  def _command_preflight(
    self,
    data: Dict[str, Any],
    stock_code: str,
    volume: int,
  ) -> Dict[str, Any]:
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", stock_code.upper()):
      return {"ok": False, "status": "REJECTED", "reason": "invalid A-share code"}
    side = str(data.get("order_type") or "").split(".")[-1].upper()
    if side not in {"BUY", "SELL", "23", "24"}:
      return {"ok": False, "status": "REJECTED", "reason": "invalid order side"}
    is_buy = side in {"BUY", "23"}
    if is_buy and volume % 100 != 0:
      return {
        "ok": False,
        "status": "REJECTED",
        "reason": "buy volume must be a board lot",
      }
    price = float(data.get("price") or data.get("limit_price") or 0.0)
    if price <= 0:
      return {
        "ok": False,
        "status": "REJECTED",
        "reason": "invalid protected limit price",
      }

    if str(data.get("execution_mode") or "").lower() == "live":
      current = clock.now_aware().time()
      in_session = (
        time(9, 30) <= current <= time(11, 30)
        or time(13, 0) <= current <= time(15, 0)
      )
      if not in_session:
        return {
          "ok": False,
          "status": "REJECTED",
          "reason": "outside trading session",
        }
      market_check = self._market_preflight(stock_code, price, is_buy)
      if not market_check.get("ok"):
        return market_check

    if is_buy:
      account = self.query_account()
      available_cash = float(
        account.get("cash")
        or account.get("available_cash")
        or account.get("cash_balance")
        or 0.0
      )
      if price * volume > available_cash:
        return {
          "ok": False,
          "status": "REJECTED",
          "reason": "insufficient cash",
        }
    else:
      position = next(
        (
          item
          for item in self.query_positions()
          if str(
            item.get("stock_code") or item.get("instrument_code") or ""
          ).upper()
          == stock_code.upper()
        ),
        None,
      )
      available = int(
        (position or {}).get("can_use_volume")
        or (position or {}).get("available_volume")
        or 0
      )
      if volume > available:
        return {
          "ok": False,
          "status": "REJECTED",
          "reason": "insufficient available volume",
        }
    return {"ok": True, "status": LocalAgentStatus.READY.value, "reason": ""}

  def _market_preflight(
    self,
    stock_code: str,
    price: float,
    is_buy: bool,
  ) -> Dict[str, Any]:
    manager = self.market_data_manager
    if manager is None:
      return {
        "ok": False,
        "status": "REJECTED",
        "reason": "live market metadata unavailable",
      }
    detail = _safe_call_with_args(
      manager,
      "get_instrument_detail",
      stock_code,
      True,
      default=None,
    )
    ticks = _safe_call_with_args(
      manager,
      "get_full_tick",
      [stock_code],
      default={},
    )
    tick = dict((ticks or {}).get(stock_code) or {})
    if not isinstance(detail, dict) or not detail or not tick:
      return {
        "ok": False,
        "status": "REJECTED",
        "reason": "live quote or instrument metadata unavailable",
      }

    suspension = str(
      detail.get("InstrumentStatus")
      or detail.get("instrument_status")
      or tick.get("stockStatus")
      or tick.get("stock_status")
      or ""
    ).upper()
    if bool(detail.get("is_suspended") or detail.get("suspended")) or any(
      marker in suspension for marker in ("SUSPEND", "HALT", "停牌")
    ):
      return {"ok": False, "status": "REJECTED", "reason": "instrument suspended"}

    quote_time = _quote_datetime(tick.get("time") or tick.get("timestamp"))
    if quote_time is None:
      return {
        "ok": False,
        "status": "REJECTED",
        "reason": "quote timestamp unavailable",
      }
    quote_lag = (clock.now_aware() - quote_time).total_seconds()
    if quote_lag < -5 or quote_lag > self.max_quote_lag_seconds:
      return {"ok": False, "status": "REJECTED", "reason": "stale live quote"}

    price_tick = float(
      detail.get("PriceTick")
      or detail.get("price_tick")
      or detail.get("priceTick")
      or 0
    )
    upper = float(
      detail.get("UpStopPrice")
      or detail.get("up_stop_price")
      or tick.get("upperLimit")
      or tick.get("upStopPrice")
      or 0
    )
    lower = float(
      detail.get("DownStopPrice")
      or detail.get("down_stop_price")
      or tick.get("lowerLimit")
      or tick.get("downStopPrice")
      or 0
    )
    last_price = float(tick.get("lastPrice") or tick.get("last_price") or 0)
    if price_tick <= 0 or upper <= 0 or lower <= 0 or last_price <= 0:
      return {
        "ok": False,
        "status": "REJECTED",
        "reason": "incomplete live trading limits",
      }
    tick_units = price / price_tick
    if abs(tick_units - round(tick_units)) > 1e-6:
      return {"ok": False, "status": "REJECTED", "reason": "invalid price tick"}
    if price > upper + 1e-6 or price < lower - 1e-6:
      return {
        "ok": False,
        "status": "REJECTED",
        "reason": "price outside daily limits",
      }
    if is_buy and last_price >= upper - 1e-6:
      return {"ok": False, "status": "REJECTED", "reason": "limit-up buy blocked"}
    if not is_buy and last_price <= lower + 1e-6:
      return {"ok": False, "status": "REJECTED", "reason": "limit-down sell blocked"}
    return {"ok": True, "status": LocalAgentStatus.READY.value, "reason": ""}

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

  def query_cancelable_orders(self) -> Optional[List[Dict[str, Any]]]:
    """Return MiniQMT's authoritative cancelable set, or unknown on failure."""
    try:
      orders = self.trading_manager.get_orders(True)
    except (AttributeError, TypeError):
      return None
    except Exception:
      return None
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
      report_id=f"delta-{int(clock.now_aware().timestamp() * 1000)}",
      generated_at=clock.now(),
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
      report_id=f"reconcile-{int(clock.now_aware().timestamp() * 1000)}",
      generated_at=clock.now(),
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
    observed_at = clock.now_aware()
    orders = self.query_orders()
    cancelable_orders = self.query_cancelable_orders()
    cancelable_order_ids = (
      {_order_identity(item) for item in cancelable_orders if _order_identity(item)}
      if cancelable_orders is not None
      else None
    )
    return {
      "account": self.query_account(),
      "positions": self.query_positions(),
      "orders": [
        _with_effective_order_status(
          order,
          observed_at=observed_at,
          cancelable_order_ids=cancelable_order_ids,
        )
        for order in orders
      ],
      "trades": self.query_trades(),
      "connected": bool(getattr(self.trading_manager, "is_connected", False)),
    }

  def should_kill_switch(self, now: Optional[datetime] = None) -> bool:
    if self.last_report_time is None:
      return False
    current_time = clock.to_shanghai(now or clock.now())
    last_report_time = clock.to_shanghai(self.last_report_time)
    return current_time - last_report_time > timedelta(
      seconds=self.max_report_lag_seconds
    )


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


def _quote_datetime(value: Any) -> Optional[datetime]:
  try:
    numeric = float(value)
  except (TypeError, ValueError):
    return None
  if numeric > 10_000_000_000:
    numeric /= 1000.0
  try:
    return clock.to_shanghai(datetime.fromtimestamp(numeric, tz=clock.SHANGHAI_TZ))
  except (OSError, OverflowError, ValueError):
    return None


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
  return getattr(OrderType, name)


def _to_miniqmt_price_type(value: Any, price: Any = 0.0) -> Any:
  text = str(getattr(value, "value", value) or "").upper()
  if not text:
    text = "FIX_PRICE" if float(price or 0.0) > 0 else "LATEST_PRICE"
  if text in {"LIMIT", "FIX", "FIX_PRICE"}:
    name = "FIX_PRICE"
  elif text in {"MARKET_CONVERT_5_LIMIT", "MARKET_CONVERT_5_CANCEL"}:
    # The shared execution path uses MARKET_CONVERT_5_LIMIT as its portable
    # protective-market name. TradingManager resolves it to the exchange-
    # specific SH/SZ five-level immediate-or-cancel order type.
    name = "MARKET_CONVERT_5_LIMIT"
  elif text in {"MARKET_PEER_PRICE_FIRST", "PEER_PRICE_FIRST"}:
    name = "MARKET_PEER_PRICE_FIRST"
  elif text in {"MARKET_MINE_PRICE_FIRST", "MINE_PRICE_FIRST"}:
    name = "MARKET_MINE_PRICE_FIRST"
  else:
    name = "LATEST_PRICE"
  return getattr(PriceType, name)


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
