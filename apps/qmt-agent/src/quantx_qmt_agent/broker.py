"""Local broker capabilities for data-only, simulator, and explicit live mode."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from numbers import Integral, Real
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from quantx_contracts import AgentEnvelope, AgentMessageType

logger = logging.getLogger(__name__)

MAX_MARKET_DATA_RECORDS = 500_000
MAX_MARKET_DATA_FRAME_RECORDS = 100_000
MAX_MARKET_DATA_CODES = 300
SUPPORTED_HISTORICAL_BAR_PERIODS = frozenset({"tick", "1m", "1d"})
MAX_BAR_DATE_SPAN_DAYS = {
  "tick": 7,
  "1m": 31,
  "1d": 3_700,
}
ESTIMATED_BAR_RECORDS_PER_DAY = {
  "tick": 20_000,
  "1m": 300,
  "1d": 1,
}
MIN_MARKET_TIMESTAMP = datetime(1990, 1, 1, tzinfo=timezone.utc)
MARKET_TIMESTAMP_MAX_FUTURE_DAYS = 366
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class _ValidatedBarsRequest:
  codes: tuple[str, ...]
  periods: tuple[str, ...]
  start_text: str
  end_text: str
  start_local: datetime
  end_local: datetime

def enrich_report_payload(
  message_type: AgentMessageType,
  payload: dict[str, Any],
) -> dict[str, Any]:
  """Add protocol 1.1 report ordering and snapshot identity metadata."""
  value = dict(payload)
  sequence = int(value.get("source_sequence") or value.get("sequence") or time.time_ns())
  value["source_sequence"] = sequence
  value.setdefault("source_event_at", datetime.now(timezone.utc).isoformat())
  if message_type is AgentMessageType.DELTA_REPORT:
    value.setdefault("sequence", sequence)
    if bool(value.get("is_complete")):
      snapshot_id = str(
        value.get("snapshot_id") or value.get("report_id") or uuid.uuid4()
      )
      value["snapshot_id"] = snapshot_id
      value.setdefault("report_id", snapshot_id)
      hash_input = {key: item for key, item in value.items() if key != "snapshot_hash"}
      value["snapshot_hash"] = hashlib.sha256(
        json.dumps(
          hash_input,
          sort_keys=True,
          separators=(",", ":"),
          default=str,
        ).encode("utf-8")
      ).hexdigest()
  return value


def _as_dict(value: Any) -> dict[str, Any]:
  if isinstance(value, dict):
    return dict(value)
  if is_dataclass(value):
    return asdict(value)
  fields = getattr(value, "__dict__", {})
  return {
    key: item
    for key, item in fields.items()
    if not key.startswith("_") and not callable(item)
  }


def _json_safe(value: Any) -> Any:
  if hasattr(value, "reset_index") and hasattr(value, "to_dict"):
    return value.reset_index().to_dict(orient="records")
  if isinstance(value, dict):
    return {str(key): _json_safe(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_json_safe(item) for item in value]
  if is_dataclass(value):
    return _json_safe(asdict(value))
  if hasattr(value, "item"):
    try:
      return value.item()
    except Exception:
      pass
  if hasattr(value, "tolist"):
    try:
      return _json_safe(value.tolist())
    except Exception:
      pass
  if hasattr(value, "isoformat"):
    try:
      return value.isoformat()
    except Exception:
      pass
  return value


def _object_payload(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
  if isinstance(value, dict):
    source = value
  else:
    source = {
      field: getattr(value, field)
      for field in fields
      if hasattr(value, field)
    }
  return {
    field: _json_safe(source[field])
    for field in fields
    if field in source
  }


ORDER_FIELDS = (
  "account_id",
  "account_type",
  "order_id",
  "stock_code",
  "order_sysid",
  "order_time",
  "order_type",
  "order_volume",
  "price_type",
  "price",
  "traded_volume",
  "traded_price",
  "order_status",
  "status_msg",
  "strategy_name",
  "order_remark",
)
EXECUTION_FIELDS = (
  "account_id",
  "account_type",
  "order_id",
  "stock_code",
  "order_sysid",
  "traded_id",
  "execution_id",
  "order_type",
  "traded_time",
  "traded_price",
  "traded_volume",
  "traded_amount",
  "strategy_name",
  "order_remark",
)
POSITION_FIELDS = (
  "account_id",
  "account_type",
  "stock_code",
  "instrument_name",
  "volume",
  "can_use_volume",
  "frozen_volume",
  "on_road_volume",
  "yesterday_volume",
  "open_price",
  "avg_price",
  "market_value",
  "direction",
  "last_price",
)
ASSET_FIELDS = (
  "account_id",
  "account_type",
  "total_asset",
  "cash",
  "market_value",
  "frozen_cash",
)


class _LiveReportSink:
  """Persist miniQMT callbacks immediately into the Agent's local outbox."""

  def __init__(
    self,
    account_id: str,
    journal: Any,
    *,
    on_report=None,
  ) -> None:
    self.account_id = account_id
    self.journal = journal
    self.on_report = on_report

  def _client_order_id(self, value: dict[str, Any]) -> str | None:
    return self.journal.client_order_id_for_report(
      broker_order_id=value.get("order_id"),
      order_remark=str(value.get("order_remark") or ""),
    )

  def _persist(
    self,
    message_type: AgentMessageType,
    payload: dict[str, Any],
  ) -> None:
    envelope = AgentEnvelope(
      message_type=message_type,
      payload=enrich_report_payload(message_type, payload),
    )
    self.journal.add_report(envelope.message_id, envelope.model_dump_json())
    if self.on_report is not None:
      self.on_report()

  async def handle_order_callback(self, order: Any) -> None:
    value = _object_payload(order, ORDER_FIELDS)
    value["account_id"] = str(value.get("account_id") or self.account_id)
    self._persist(
      AgentMessageType.ORDER_REPORT,
      {
        "client_order_id": self._client_order_id(value),
        "order": value,
      },
    )

  async def handle_trade_callback(self, trade: Any) -> None:
    value = _object_payload(trade, EXECUTION_FIELDS)
    value["account_id"] = str(value.get("account_id") or self.account_id)
    if not value.get("execution_id") and value.get("traded_id"):
      value["execution_id"] = value["traded_id"]
    self._persist(
      AgentMessageType.EXECUTION_REPORT,
      {
        "client_order_id": self._client_order_id(value),
        "order_status": "FILLED",
        "execution": value,
      },
    )

  async def handle_asset_update(self, asset: Any) -> None:
    value = _object_payload(asset, ASSET_FIELDS)
    value["account_id"] = str(value.get("account_id") or self.account_id)
    self._persist(
      AgentMessageType.DELTA_REPORT,
      {
        "accounts": [value],
        "sequence": time.time_ns(),
        "is_complete": False,
      },
    )

  async def handle_position_update(self, position: Any) -> None:
    value = _object_payload(position, POSITION_FIELDS)
    value["account_id"] = str(value.get("account_id") or self.account_id)
    self._persist(
      AgentMessageType.DELTA_REPORT,
      {
        "account_id": self.account_id,
        "position_deltas": [value],
        "sequence": time.time_ns(),
        "is_complete": False,
      },
    )

  async def handle_order_error_callback(self, error: Any) -> None:
    value = _object_payload(
      error,
      ("account_id", "order_id", "error_id", "error_msg", "order_remark"),
    )
    value["account_id"] = str(value.get("account_id") or self.account_id)
    value["client_order_id"] = self._client_order_id(value)
    self._persist(
      AgentMessageType.DELTA_REPORT,
      {
        "order_errors": [value],
        "sequence": time.time_ns(),
        "is_complete": False,
      },
    )

  async def handle_cancel_error_callback(self, error: Any) -> None:
    value = _object_payload(
      error,
      ("account_id", "order_id", "error_id", "error_msg"),
    )
    value["account_id"] = str(value.get("account_id") or self.account_id)
    value["client_order_id"] = self._client_order_id(value)
    self._persist(
      AgentMessageType.DELTA_REPORT,
      {
        "cancel_errors": [value],
        "sequence": time.time_ns(),
        "is_complete": False,
      },
    )


class SimulatorBroker:
  def __init__(self, allowed_accounts: set[str], *, data_only: bool) -> None:
    self.allowed_accounts = allowed_accounts
    self.data_only = data_only

  def full_snapshot(self) -> dict[str, Any]:
    return {
      "accounts": [],
      "positions_by_account": {
        account_id: [] for account_id in self.allowed_accounts
      },
      "sequence": int(time.time() * 1_000_000),
      "is_complete": True,
      "mode": "data-only" if self.data_only else "paper",
    }

  def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
    if self.data_only:
      return {"accepted": False, "reason": "data_only_agent"}
    kind = str(payload.get("command_kind", ""))
    if kind == "CANCEL_ORDER":
      return {
        "accepted": True,
        "reason": "",
        "reports": [],
      }
    client_order_id = str(payload["client_order_id"])
    broker_id = int(hashlib.sha256(client_order_id.encode()).hexdigest()[:12], 16)
    broker_id %= 2_000_000_000
    side = str(payload.get("side", "BUY")).upper()
    volume = int(payload["volume"])
    price = float(payload.get("limit_price") or 0)
    now = int(time.time())
    order = {
      "client_order_id": client_order_id,
      "order": {
        "order_id": broker_id,
        "account_id": payload["account_id"],
        "account_type": 2,
        "stock_code": payload["instrument_code"],
        "order_sysid": str(broker_id)[-10:],
        "order_time": now,
        "order_type": 23 if side == "BUY" else 24,
        "order_volume": volume,
        "price_type": 50,
        "price": price,
        "traded_volume": 0,
        "traded_price": 0,
        "order_status": 50,
        "status_msg": "simulator accepted",
        "strategy_name": payload.get("strategy_name", ""),
        "order_remark": payload.get("order_remark", ""),
      },
    }
    execution = {
      "client_order_id": client_order_id,
      "order_status": "FILLED",
      "execution": {
        "execution_id": f"sim-{broker_id}",
        "order_id": broker_id,
        "account_id": payload["account_id"],
        "account_type": 2,
        "stock_code": payload["instrument_code"],
        "order_type": 23 if side == "BUY" else 24,
        "traded_time": now,
        "traded_price": price,
        "traded_volume": volume,
        "traded_amount": price * volume,
        "strategy_name": payload.get("strategy_name", ""),
        "order_remark": payload.get("order_remark", ""),
      },
    }
    return {
      "accepted": True,
      "reason": "",
      "reports": [
        ("order_report", order),
        ("execution_report", execution),
      ],
    }

  def market_data(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(self.iter_market_data(payload))

  def iter_market_data(
    self,
    payload: dict[str, Any],
  ) -> Iterator[dict[str, Any]]:
    del payload
    return iter(())

  def subscribe_market(self, payload, callback) -> bool:
    del payload, callback
    return False

  def unsubscribe_market(self, subscription_id: str) -> None:
    del subscription_id

  def reset_market_subscriptions(self) -> None:
    return None


class _LocalMarketStreamer:
  """Map server subscription identities to local XTData subscriptions."""

  def __init__(
    self,
    data_manager: Any,
    *,
    access_lock: threading.RLock | None = None,
  ) -> None:
    self.data_manager = data_manager
    self._access_lock = access_lock or threading.RLock()
    self._subscriptions: dict[str, int | list[int]] = {}
    self._lock = threading.RLock()

  @staticmethod
  def _valid_subscription(value: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    return bool(values) and all(
      isinstance(item, int) and item > 0 for item in values
    )

  def subscribe(self, payload: dict[str, Any], callback) -> bool:
    subscription_id = str(payload.get("subscription_id") or "")
    if not subscription_id:
      return False
    with self._lock:
      if subscription_id in self._subscriptions:
        return True

    kind = str(payload.get("kind") or "quote")
    stock_code = str(payload.get("stock_code") or "")
    period = str(payload.get("period") or "tick")

    def on_data(data: Any) -> None:
      callback(
        {
          "subscription_id": subscription_id,
          "kind": kind,
          "stock_code": stock_code,
          "period": period,
          "data": _json_safe(data),
        }
      )

    with self._access_lock:
      if kind == "whole":
        local_id = self.data_manager.subscribe_whole_quote(
          list(payload.get("stock_codes") or []),
          callback=on_data,
        )
      elif kind == "quote" and stock_code:
        local_id = self.data_manager.subscribe_quote(
          stock_code,
          period=period,
          count=int(payload.get("count") or 0),
          callback=on_data,
        )
      else:
        return False
    if not self._valid_subscription(local_id):
      return False
    with self._lock:
      duplicate = self._subscriptions.get(subscription_id)
      if duplicate is None:
        self._subscriptions[subscription_id] = local_id
        return True
    self._unsubscribe_local(local_id)
    return True

  def _unsubscribe_local(self, local_id: int | list[int]) -> None:
    values = local_id if isinstance(local_id, list) else [local_id]
    with self._access_lock:
      for value in values:
        try:
          self.data_manager.unsubscribe_quote(int(value))
        except Exception:
          pass

  def unsubscribe(self, subscription_id: str) -> None:
    with self._lock:
      local_id = self._subscriptions.pop(str(subscription_id), None)
    if local_id is not None:
      self._unsubscribe_local(local_id)

  def reset(self) -> None:
    with self._lock:
      local_ids = list(self._subscriptions.values())
      self._subscriptions.clear()
    for local_id in local_ids:
      self._unsubscribe_local(local_id)


class QmtDataBroker(SimulatorBroker):
  """XTData capability with either rejected or simulated trade commands."""

  def __init__(self, allowed_accounts: set[str], *, data_only: bool) -> None:
    super().__init__(allowed_accounts, data_only=data_only)
    from .miniqmt.manager_registry import XTDataManagerRegistry

    self.data_manager = XTDataManagerRegistry().get_manager()
    self._xtdata_access_lock = threading.RLock()
    self.market_streamer = _LocalMarketStreamer(
      self.data_manager,
      access_lock=self._xtdata_access_lock,
    )

  def is_market_data_ready(self) -> bool:
    """Return cached readiness without invoking the native SDK."""
    return bool(getattr(self.data_manager, "is_connected", False))

  def ensure_market_data_ready(self) -> bool:
    with self._xtdata_access_lock:
      return _ensure_market_data_manager_connected(self.data_manager)

  def market_data(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(self.iter_market_data(payload))

  def iter_market_data(
    self,
    payload: dict[str, Any],
  ) -> Iterator[dict[str, Any]]:
    return _iter_locked_market_data_records(
      self.data_manager,
      payload,
      self._xtdata_access_lock,
    )

  def subscribe_market(self, payload, callback) -> bool:
    return self.market_streamer.subscribe(payload, callback)

  def unsubscribe_market(self, subscription_id: str) -> None:
    self.market_streamer.unsubscribe(subscription_id)

  def reset_market_subscriptions(self) -> None:
    self.market_streamer.reset()


class LiveBroker:
  def __init__(self, allowed_accounts: set[str], *, journal: Any) -> None:
    self.allowed_accounts = allowed_accounts
    from .miniqmt.local_agent import MiniQmtLocalAgent
    from .miniqmt.manager_registry import (
      XTDataManagerRegistry,
      XTTradingManagerRegistry,
    )

    registry = XTTradingManagerRegistry()
    self._trading_registry = registry
    self._trading_journal = journal
    self._trading_access_lock = threading.RLock()
    self.data_manager = XTDataManagerRegistry().get_manager()
    self._xtdata_access_lock = threading.RLock()
    self.market_streamer = _LocalMarketStreamer(
      self.data_manager,
      access_lock=self._xtdata_access_lock,
    )
    self.agents = {}
    for account_id in allowed_accounts:
      manager = registry.get_manager(account_id)
      agent = MiniQmtLocalAgent(
        manager,
        market_data_manager=self.data_manager,
      )
      manager.trading_service = _LiveReportSink(
        account_id,
        journal,
        on_report=agent.mark_report_received,
      )
      self.agents[account_id] = agent

  def is_trading_ready(self) -> bool:
    """Return cached XTTrading readiness without calling the native SDK."""
    with self._trading_access_lock:
      return bool(self.agents) and all(
        bool(getattr(agent.trading_manager, "is_connected", False))
        for agent in self.agents.values()
      )

  def ensure_trading_ready(self) -> bool:
    """Reconnect dead XTTrading sessions and rebind their report sinks."""
    with self._trading_access_lock:
      for account_id, agent in self.agents.items():
        manager = self._trading_registry.get_manager(
          account_id,
          reconnect=True,
        )
        if manager is not agent.trading_manager:
          agent.trading_manager = manager
          manager.trading_service = _LiveReportSink(
            account_id,
            self._trading_journal,
            on_report=agent.mark_report_received,
          )
      return self.is_trading_ready()

  def is_market_data_ready(self) -> bool:
    """Return cached readiness without invoking the native SDK."""
    return bool(getattr(self.data_manager, "is_connected", False))

  def ensure_market_data_ready(self) -> bool:
    with self._xtdata_access_lock:
      return _ensure_market_data_manager_connected(self.data_manager)

  def full_snapshot(self) -> dict[str, Any]:
    accounts = []
    positions = {}
    orders = []
    trades = []
    unavailable_accounts = []
    with self._trading_access_lock:
      for account_id, agent in self.agents.items():
        if not bool(
          getattr(agent.trading_manager, "is_connected", False)
        ):
          unavailable_accounts.append(account_id)
          accounts.append(
            {
              "account_id": account_id,
              "connection_status": "DISCONNECTED",
            }
          )
          positions[account_id] = []
          continue
        snapshot = agent.full_snapshot()
        account = dict(snapshot.get("account") or {})
        if not snapshot.get("connected") or not account:
          agent.trading_manager.is_connected = False
          unavailable_accounts.append(account_id)
          accounts.append(
            {
              "account_id": account_id,
              "connection_status": "DISCONNECTED",
            }
          )
          positions[account_id] = []
          continue
        agent.mark_report_received()
        account["account_id"] = account_id
        accounts.append(account)
        positions[account_id] = list(snapshot.get("positions") or [])
        orders.extend(
          {
            "account_id": account_id,
            **dict(order),
          }
          for order in snapshot.get("orders") or []
        )
        trades.extend(
          {
            "account_id": account_id,
            **dict(trade),
          }
          for trade in snapshot.get("trades") or []
        )
    return {
      "accounts": accounts,
      "positions_by_account": positions,
      "orders": orders,
      "trades": trades,
      "sequence": int(time.time() * 1_000_000),
      "is_complete": not unavailable_accounts,
      "unavailable_accounts": unavailable_accounts,
      "mode": "live",
    }

  def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
    account_id = str(payload["account_id"])
    if not self.ensure_trading_ready():
      logger.warning(
        "拒绝交易命令：XTTrading 尚未连接: account=%s",
        account_id,
      )
      return {
        "accepted": False,
        "reason": "miniQMT trading connection unavailable",
        "reports": [],
      }
    agent = self.agents[account_id]
    if payload.get("command_kind") == "CANCEL_ORDER":
      result = agent.cancel_order(payload.get("broker_order_id"))
      return {
        "accepted": bool(result.get("success")),
        "reason": str(result.get("message") or ""),
        "reports": [],
      }
    command = dict(payload)
    command["order_type"] = payload.get("side")
    command["price_type"] = payload.get("order_type")
    command["price"] = float(payload.get("limit_price") or 0)
    command["order_remark"] = (
      f"qx:{str(payload['client_order_id'])[:20]}"
    )
    result = agent.place_order(command)
    return {
      "accepted": bool(result.get("success")),
      "reason": str(result.get("message") or ""),
      "reports": [],
      "broker_order_id": result.get("order_id"),
    }

  def market_data(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(self.iter_market_data(payload))

  def iter_market_data(
    self,
    payload: dict[str, Any],
  ) -> Iterator[dict[str, Any]]:
    return _iter_locked_market_data_records(
      self.data_manager,
      payload,
      self._xtdata_access_lock,
    )

  def subscribe_market(self, payload, callback) -> bool:
    return self.market_streamer.subscribe(payload, callback)

  def unsubscribe_market(self, subscription_id: str) -> None:
    self.market_streamer.unsubscribe(subscription_id)

  def reset_market_subscriptions(self) -> None:
    self.market_streamer.reset()


def _iter_locked_market_data_records(
  manager: Any,
  payload: dict[str, Any],
  access_lock: threading.RLock,
) -> Iterator[dict[str, Any]]:
  with access_lock:
    _ensure_market_data_manager_connected(manager)
    yield from _iter_market_data_records(manager, payload)


def _ensure_market_data_manager_connected(manager: Any) -> bool:
  ensure_connected = getattr(manager, "ensure_connected", None)
  if callable(ensure_connected):
    if bool(ensure_connected()):
      return True
  elif bool(getattr(manager, "is_connected", True)):
    return True
  from .miniqmt.data.data_manager import XTDataUnavailableError

  detail = str(getattr(manager, "last_connection_error", "") or "")
  suffix = f": {detail}" if detail else ""
  raise XTDataUnavailableError(f"XTData is not ready{suffix}")


def _market_data_records(
  manager: Any,
  payload: dict[str, Any],
) -> list[dict[str, Any]]:
  """Compatibility materializer; runtime upload uses the bounded iterator."""
  return list(_iter_market_data_records(manager, payload))


def _iter_market_data_records(
  manager: Any,
  payload: dict[str, Any],
  *,
  max_records: int = MAX_MARKET_DATA_RECORDS,
) -> Iterator[dict[str, Any]]:
  if max_records <= 0 or max_records > MAX_MARKET_DATA_RECORDS:
    raise ValueError("invalid market data record limit")
  for count, record in enumerate(
    _iter_market_data_records_unbounded(manager, payload),
    start=1,
  ):
    if count > max_records:
      raise ValueError("market data request exceeds record count limit")
    yield record


def _iter_market_data_records_unbounded(
  manager: Any,
  payload: dict[str, Any],
) -> Iterator[dict[str, Any]]:
  operation = str(payload.get("operation") or "bars")
  if operation == "sector_instruments":
    for sector in payload.get("sectors") or []:
      for code in manager.get_stock_list_in_sector(str(sector)) or []:
        yield {"sector": sector, "code": code}
    return
  if operation == "instrument_details":
    codes = list(payload.get("stock_list") or [])
    values = manager.get_instrument_detail_list(codes, iscomplete=True)
    if isinstance(values, dict):
      for code in sorted(values):
        yield {"code": code, **_as_dict(values[code])}
    return
  if operation == "financial_data":
    values = manager.get_financial_data_list(
      list(payload.get("stock_list") or [])
    )
    if isinstance(values, dict):
      for code in sorted(values):
        yield {
          "code": code,
          "financial_data": _json_safe(values[code]),
        }
    return
  if operation == "divid_factors":
    yield from _divid_factor_records(manager, payload)
    return

  request = _validate_bars_request(payload)
  requested_codes = set(request.codes)
  for period in request.periods:
    lower_bound, upper_bound = _bar_time_bounds(request, period)
    if bool(payload.get("download")):
      manager.download_market_data(
        stock_list=list(request.codes),
        period=period,
        start_time=request.start_text,
        end_time=request.end_text,
        incrementally=False,
      )
    values = manager.get_market_data(
      stock_list=list(request.codes),
      period=period,
      start_time=request.start_text,
      end_time=request.end_text,
    )
    if not isinstance(values, dict):
      continue
    for code in sorted(values):
      normalized_code = str(code).strip().upper()
      if normalized_code not in requested_codes:
        raise ValueError(
          f"XTData returned unrequested instrument: {normalized_code}"
        )
      frame = values[code]
      if len(frame) > MAX_MARKET_DATA_FRAME_RECORDS:
        raise ValueError("single market data frame exceeds record limit")
      normalized = (
        frame
        if "time" in getattr(frame, "columns", ())
        else frame.reset_index()
      )
      if "time" not in normalized.columns and len(normalized.columns) > 0:
        normalized = normalized.rename(columns={normalized.columns[0]: "time"})
      if "time" not in normalized.columns:
        raise ValueError(f"market data frame for {code} has no time column")
      normalized_time_column = "__quantx_normalized_time_ms"
      if normalized_time_column in normalized.columns:
        raise ValueError(
          f"market data frame for {code} contains a reserved column"
        )
      normalize_time = (
        _normalize_daily_market_timestamp
        if period == "1d"
        else _normalize_market_timestamp
      )
      normalized_times = [
        normalize_time(value) for value in normalized["time"].array
      ]
      normalized = normalized.assign(
        **{normalized_time_column: normalized_times}
      ).sort_values(
        by=normalized_time_column,
        kind="mergesort",
      )
      columns = tuple(normalized.columns)
      seen_timestamps: set[int] = set()
      for values_tuple in normalized.itertuples(index=False, name=None):
        row = dict(zip(columns, values_tuple, strict=True))
        record = dict(row)
        record["code"] = normalized_code
        record["period"] = period
        record["time"] = int(record.pop(normalized_time_column))
        if record["time"] < lower_bound or record["time"] > upper_bound:
          raise ValueError(
            "XTData returned bar time outside requested range: "
            f"{normalized_code}/{period}/{record['time']}"
          )
        if record["time"] in seen_timestamps:
          raise ValueError(
            "XTData returned duplicate normalized bar key: "
            f"{normalized_code}/{period}/{record['time']}"
          )
        seen_timestamps.add(record["time"])
        yield record


def _validate_bars_request(payload: dict[str, Any]) -> _ValidatedBarsRequest:
  codes = tuple(
    str(code).strip().upper()
    for code in payload.get("stock_list") or []
  )
  if not codes:
    raise ValueError("bars request requires a non-empty stock_list")
  if len(codes) > MAX_MARKET_DATA_CODES:
    raise ValueError("bars request exceeds instrument count limit")
  if len(set(codes)) != len(codes):
    raise ValueError("bars request contains duplicate instruments")
  code_pattern = re.compile(r"^[A-Z0-9]{1,16}\.(?:SH|SZ|BJ)$")
  invalid_codes = [code for code in codes if not code_pattern.fullmatch(code)]
  if invalid_codes:
    raise ValueError(f"bars request contains invalid instruments: {invalid_codes}")

  periods = tuple(
    str(period).strip().lower()
    for period in payload.get("periods") or ["1d"]
  )
  if not periods or len(set(periods)) != len(periods):
    raise ValueError("bars request periods must be non-empty and unique")
  unsupported = [
    period
    for period in periods
    if period not in SUPPORTED_HISTORICAL_BAR_PERIODS
  ]
  if unsupported:
    raise ValueError(f"bars request contains unsupported periods: {unsupported}")

  start_text = str(payload.get("start_time") or "").strip()
  end_text = str(payload.get("end_time") or "").strip()
  try:
    start_local = datetime.strptime(start_text, "%Y%m%d").replace(
      tzinfo=SHANGHAI_TIMEZONE
    )
    end_local = datetime.strptime(end_text, "%Y%m%d").replace(
      tzinfo=SHANGHAI_TIMEZONE
    )
  except ValueError as exc:
    raise ValueError("bars request dates must be YYYYMMDD") from exc
  if end_local < start_local:
    raise ValueError("bars request end_time precedes start_time")
  _normalize_market_timestamp(start_local)
  _normalize_market_timestamp(end_local)
  span_days = (end_local.date() - start_local.date()).days + 1
  for period in periods:
    if span_days > MAX_BAR_DATE_SPAN_DAYS[period]:
      raise ValueError(
        f"bars request date span exceeds {period} limit"
      )
  estimated_records = (
    len(codes)
    * span_days
    * sum(ESTIMATED_BAR_RECORDS_PER_DAY[period] for period in periods)
  )
  if estimated_records > MAX_MARKET_DATA_RECORDS:
    raise ValueError(
      "bars request estimated record count exceeds safe limit"
    )
  return _ValidatedBarsRequest(
    codes=codes,
    periods=periods,
    start_text=start_text,
    end_text=end_text,
    start_local=start_local,
    end_local=end_local,
  )


def _normalize_daily_market_timestamp(value: Any) -> int:
  instant_ms = _normalize_market_timestamp(value)
  instant = datetime.fromtimestamp(
    instant_ms / 1000,
    timezone.utc,
  ).astimezone(SHANGHAI_TIMEZONE)
  local_midnight = datetime(
    instant.year,
    instant.month,
    instant.day,
    tzinfo=SHANGHAI_TIMEZONE,
  )
  return _normalize_market_timestamp(local_midnight)


def _bar_time_bounds(
  request: _ValidatedBarsRequest,
  period: str,
) -> tuple[int, int]:
  start = _normalize_market_timestamp(request.start_local)
  if period == "1d":
    return start, _normalize_market_timestamp(request.end_local)
  end_exclusive = request.end_local + timedelta(days=1)
  return start, _normalize_market_timestamp(end_exclusive) - 1


def _normalize_market_timestamp(value: Any) -> int:
  """Normalize supported XTData/Pandas timestamps to Unix milliseconds."""
  if isinstance(value, bool) or value is None:
    raise ValueError("market data time is not a supported timestamp")

  to_pydatetime = getattr(value, "to_pydatetime", None)
  if callable(to_pydatetime):
    try:
      value = to_pydatetime()
    except Exception as exc:
      raise ValueError("market data time is not a supported timestamp") from exc

  if isinstance(value, datetime):
    try:
      normalized = (
        value.replace(tzinfo=SHANGHAI_TIMEZONE).astimezone(timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
      )
      epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
      delta = normalized - epoch
      timestamp = (
        (delta.days * 86_400 + delta.seconds) * 1000
        + delta.microseconds // 1000
      )
    except (OverflowError, TypeError, ValueError) as exc:
      raise ValueError("market data time is not a supported timestamp") from exc
    if not isinstance(timestamp, Integral):
      raise ValueError("market data time is not a supported timestamp")
    return _validate_market_timestamp(int(timestamp))

  if isinstance(value, str):
    candidate = value.strip()
    if not candidate:
      raise ValueError("market data time is not a supported timestamp")
    if candidate.isdigit():
      return _normalize_numeric_market_timestamp(int(candidate))
    try:
      parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
      raise ValueError("market data time is not a supported timestamp") from exc
    return _normalize_market_timestamp(parsed)

  if isinstance(value, Integral):
    return _normalize_numeric_market_timestamp(int(value))
  if isinstance(value, Real):
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
      raise ValueError("market data time is not a supported timestamp")
    return _normalize_numeric_market_timestamp(int(numeric))

  item = getattr(value, "item", None)
  if callable(item):
    try:
      scalar = item()
    except Exception as exc:
      raise ValueError("market data time is not a supported timestamp") from exc
    if scalar is not value:
      return _normalize_market_timestamp(scalar)
  raise ValueError("market data time is not a supported timestamp")


def _normalize_numeric_market_timestamp(value: int) -> int:
  if value <= 0:
    raise ValueError("market data time is outside the supported range")

  candidate = str(value)
  if len(candidate) == 8:
    try:
      parsed = datetime.strptime(candidate, "%Y%m%d")
    except ValueError:
      pass
    else:
      return _normalize_market_timestamp(parsed)

  if value < 100_000_000_000:
    normalized = value * 1000
  elif value < 100_000_000_000_000:
    normalized = value
  elif value < 100_000_000_000_000_000:
    normalized = value // 1000
  elif value < 100_000_000_000_000_000_000:
    normalized = value // 1_000_000
  else:
    raise ValueError("market data time is outside the supported range")
  return _validate_market_timestamp(normalized)


def _validate_market_timestamp(value: int) -> int:
  try:
    parsed = datetime.fromtimestamp(value / 1000, timezone.utc)
  except (OSError, OverflowError, ValueError) as exc:
    raise ValueError(
      "market data time is outside the supported range"
    ) from exc
  latest = datetime.now(timezone.utc) + timedelta(
    days=MARKET_TIMESTAMP_MAX_FUTURE_DAYS
  )
  if parsed < MIN_MARKET_TIMESTAMP or parsed > latest:
    raise ValueError("market data time is outside the supported range")
  return value


_DIVID_FACTOR_FIELDS = (
  "time",
  "interest",
  "stockBonus",
  "stockGift",
  "allotNum",
  "allotPrice",
  "gugai",
  "dr",
)


def _divid_factor_records(
  manager: Any,
  payload: dict[str, Any],
) -> list[dict[str, Any]]:
  """Read sparse QMT corporate-action factors without any trading access."""
  codes = sorted(
    {
      str(code).strip().upper()
      for code in payload.get("stock_list") or []
      if str(code).strip()
    }
  )
  if not codes:
    raise ValueError("divid_factors requires a non-empty stock_list")
  if len(codes) > 500:
    raise ValueError("divid_factors accepts at most 500 instruments per request")

  start_time = str(payload.get("start_time") or "")
  end_time = str(payload.get("end_time") or "")
  for label, value in (("start_time", start_time), ("end_time", end_time)):
    if len(value) != 8 or not value.isdigit():
      raise ValueError(f"divid_factors {label} must be YYYYMMDD")
  if end_time < start_time:
    raise ValueError("divid_factors end_time precedes start_time")

  records: list[dict[str, Any]] = []
  for code in codes:
    frame = manager.get_divid_factors(code, start_time, end_time)
    if frame is None or bool(getattr(frame, "empty", False)):
      continue
    if not hasattr(frame, "reset_index") or not hasattr(frame, "columns"):
      raise ValueError(f"unexpected divid_factors result for {code}")
    normalized = frame.reset_index()
    if "ex_date" not in normalized.columns:
      if len(normalized.columns) == 0:
        continue
      normalized = normalized.rename(
        columns={normalized.columns[0]: "ex_date"}
      )
    missing = [
      field for field in _DIVID_FACTOR_FIELDS if field not in normalized.columns
    ]
    if missing:
      raise ValueError(
        f"divid_factors result for {code} is missing fields: {missing}"
      )

    for row in normalized.to_dict(orient="records"):
      ex_date = str(row.get("ex_date") or "").strip()
      if len(ex_date) != 8 or not ex_date.isdigit():
        raise ValueError(f"invalid divid_factors ex_date for {code}: {ex_date}")
      if ex_date < start_time or ex_date > end_time:
        raise ValueError(
          f"divid_factors ex_date outside request range for {code}: {ex_date}"
        )
      record: dict[str, Any] = {"code": code, "ex_date": ex_date}
      for field in _DIVID_FACTOR_FIELDS:
        value = _json_safe(row.get(field))
        try:
          numeric = float(value)
        except (TypeError, ValueError) as exc:
          raise ValueError(
            f"invalid divid_factors {field} for {code}/{ex_date}"
          ) from exc
        if not math.isfinite(numeric):
          raise ValueError(
            f"non-finite divid_factors {field} for {code}/{ex_date}"
          )
        record[field] = numeric
      if record["time"] <= 0 or record["dr"] <= 0:
        raise ValueError(
          f"non-positive divid_factors time/dr for {code}/{ex_date}"
        )
      records.append(record)
  return records
