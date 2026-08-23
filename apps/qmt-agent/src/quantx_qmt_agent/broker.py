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
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from numbers import Integral, Real
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from quantx_contracts import (
  HISTORICAL_BAR_NO_DATA_REASON,
  HISTORICAL_BAR_TRANSFER_PERIODS,
  HISTORICAL_TICK_ORDINAL_FIELD,
  HISTORICAL_TICK_ORDINALS_PER_MILLISECOND,
  HISTORICAL_TICK_SOURCE_TIME_FIELD,
  AgentEnvelope,
  AgentMessageType,
  HistoricalBarSummary,
  historical_bar_key,
  historical_bar_transfer_fields,
)

logger = logging.getLogger(__name__)

MAX_MARKET_DATA_RECORDS = 500_000
MAX_MARKET_DATA_FRAME_RECORDS = 100_000
MAX_MARKET_DATA_CODES = 300
MAX_FINANCIAL_DATA_CODES = 100
WHOLE_QUOTE_INSTRUMENT_DETAIL_BATCH_SIZE = 500
WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE = 256
WHOLE_QUOTE_METADATA_REFRESH_RETRY_SECONDS = 60.0
WHOLE_QUOTE_SECTORS = ("沪深A股", "沪深指数")
WHOLE_QUOTE_TICK_FIELDS = (
  "time",
  "timetag",
  "lastPrice",
  "open",
  "high",
  "low",
  "lastClose",
  "amount",
  "volume",
  "pvolume",
  "tickvol",
  "stockStatus",
  "openInt",
  "lastSettlementPrice",
  "settlementPrice",
  "transactionNum",
  "askPrice",
  "bidPrice",
  "askVol",
  "bidVol",
  "priceTick",
  "upperLimit",
  "lowerLimit",
)
FINANCIAL_DATA_RECORD_FORMAT = "financial-row-v1"
SUPPORTED_FINANCIAL_TABLES = (
  "Balance",
  "Income",
  "CashFlow",
  "Capital",
)
SUPPORTED_HISTORICAL_BAR_PERIODS = HISTORICAL_BAR_TRANSFER_PERIODS
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
_NORMALIZED_MARKET_TIME_COLUMN = "__quantx_normalized_time_ms"
_RESERVED_HISTORICAL_BAR_COLUMNS = frozenset(
  {
    _NORMALIZED_MARKET_TIME_COLUMN,
    HISTORICAL_TICK_ORDINAL_FIELD,
    HISTORICAL_TICK_SOURCE_TIME_FIELD,
    *HistoricalBarSummary.model_fields,
  }
)


@dataclass(frozen=True)
class _WholeQuoteUniverse:
  trading_date: date
  codes: tuple[str, ...]
  code_set: frozenset[str]
  metadata: dict[str, dict[str, float]]
  fingerprint: str


_UNSTABLE_TICK_ORDER_FIELDS = frozenset(
  {
    "time",
    "tickvol",
    "pvolume",
    "stockStatus",
  }
)


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
  sequence = int(
    value.get("source_sequence") or value.get("sequence") or time.time_ns()
  )
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


def _canonical_tick_payload(
  record: dict[str, Any],
  *,
  excluded_fields: frozenset[str],
) -> str:
  payload = {
    str(key): _json_safe(value)
    for key, value in record.items()
    if key not in excluded_fields and not str(key).startswith("__quantx_")
  }
  return json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
  )


def _tick_numeric_order_key(value: Any) -> tuple[int, float | str]:
  if isinstance(value, bool):
    return (1, str(value))
  try:
    number = float(value)
  except (TypeError, ValueError, OverflowError):
    return (1, _canonical_tick_payload({"value": value}, excluded_fields=frozenset()))
  if not math.isfinite(number):
    return (1, str(number))
  return (0, number)


def _tick_record_order_key(record: dict[str, Any]) -> tuple[Any, ...]:
  stable_exclusions = _UNSTABLE_TICK_ORDER_FIELDS | _RESERVED_HISTORICAL_BAR_COLUMNS
  reserved_exclusions = _RESERVED_HISTORICAL_BAR_COLUMNS
  return (
    _tick_numeric_order_key(record.get("transactionNum")),
    _tick_numeric_order_key(record.get("volume")),
    _tick_numeric_order_key(record.get("amount")),
    _canonical_tick_payload(record, excluded_fields=stable_exclusions),
    _canonical_tick_payload(record, excluded_fields=reserved_exclusions),
  )


def _project_historical_bar_record(
  row: dict[str, Any],
  *,
  code: str,
  period: str,
  source_time_ms: int,
) -> dict[str, Any]:
  """Project one XTData row to the shared historical transfer contract.

  The QMT Agent is the vendor boundary.  It must not forward newly-added
  XTData columns (for example ``pe``) into the durable transfer, because the
  server intentionally validates this contract strictly.  ``time`` is the
  original source millisecond after timestamp normalization; tick ordinals are
  assigned later from the projected records in the same-millisecond group.
  """

  record: dict[str, Any] = {
    "code": code,
    "period": period,
    "time": source_time_ms,
  }
  agent_managed = {
    "code",
    "period",
    "time",
    HISTORICAL_TICK_ORDINAL_FIELD,
  }
  for field in historical_bar_transfer_fields(period):
    if field not in agent_managed and field in row:
      record[field] = row[field]
  return record


def _object_payload(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
  if isinstance(value, dict):
    source = value
  else:
    source = {field: getattr(value, field) for field in fields if hasattr(value, field)}
  return {field: _json_safe(source[field]) for field in fields if field in source}


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
    section_completeness = {
      account_id: {
        "account": False,
        "positions": True,
        "orders": False,
        "trades": False,
      }
      for account_id in self.allowed_accounts
    }
    return {
      "accounts": [],
      "positions_by_account": {account_id: [] for account_id in self.allowed_accounts},
      "orders": [],
      "trades": [],
      "sequence": int(time.time() * 1_000_000),
      "is_complete": not self.allowed_accounts,
      "unavailable_accounts": sorted(self.allowed_accounts),
      "section_completeness_by_account": section_completeness,
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

  def subscribe_whole_market(self, callback) -> bool:
    del callback
    return False

  def whole_market_codes(self) -> tuple[str, ...]:
    return ()

  def market_data_connection_generation(self) -> int:
    return 0

  def market_data_subscription_generation(self) -> int:
    return self.market_data_connection_generation()

  def is_whole_market_trading_session(self) -> bool:
    return False

  def whole_market_snapshot(self) -> dict[str, dict[str, Any]]:
    return {}

  def whole_market_snapshot_chunk(
    self,
    codes: list[str],
  ) -> dict[str, dict[str, Any]]:
    del codes
    return {}

  def prepare_whole_market_data(self, data: Any) -> dict[str, dict[str, Any]]:
    del data
    return {}

  def unsubscribe_whole_market(self) -> None:
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
    self._whole_quote_lifecycle_lock = threading.RLock()
    self._whole_quote_callback_condition = threading.Condition(self._lock)
    self._whole_quote_callbacks_inflight = 0
    self._whole_quote_active_universe: _WholeQuoteUniverse | None = None
    self._whole_quote_pending_universe: _WholeQuoteUniverse | None = None
    self._whole_quote_universe_generation = 0
    self._whole_quote_bound_universe_generation = 0
    self._whole_quote_metadata_refreshing = False
    self._whole_quote_metadata_last_attempt_monotonic = 0.0
    self._whole_quote_subscription: int | list[int] | None = None
    self._whole_quote_callback_epoch = 0
    self._whole_quote_calendar_date = None
    self._whole_quote_is_trading_date = False

  @staticmethod
  def _positive_number(value: Any) -> float:
    try:
      number = float(value)
    except (TypeError, ValueError):
      return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0

  @staticmethod
  def _build_whole_quote_universe(
    *,
    trading_date: date,
    codes: tuple[str, ...],
    metadata: dict[str, dict[str, float]],
  ) -> _WholeQuoteUniverse:
    fingerprint = hashlib.sha256("\n".join(codes).encode("utf-8")).hexdigest()[:16]
    return _WholeQuoteUniverse(
      trading_date=trading_date,
      codes=codes,
      code_set=frozenset(codes),
      metadata=metadata,
      fingerprint=fingerprint,
    )

  def _load_whole_quote_metadata(
    self,
    markets: list[str],
  ) -> tuple[tuple[str, ...], dict[str, dict[str, float]]]:
    normalized_markets: set[str] = set()
    for market in markets:
      candidate = str(market).strip().upper()
      if not candidate:
        continue
      exchange = candidate.rpartition(".")[2]
      normalized_markets.add(exchange if exchange in {"SH", "SZ"} else candidate)
    codes = sorted(
      {
        str(code).strip().upper()
        for sector in WHOLE_QUOTE_SECTORS
        for code in (self.data_manager.get_stock_list_in_sector(sector) or [])
        if str(code).strip()
        and str(code).strip().upper().rpartition(".")[2] in normalized_markets
      }
    )
    metadata: dict[str, dict[str, float]] = {}
    for start in range(0, len(codes), WHOLE_QUOTE_INSTRUMENT_DETAIL_BATCH_SIZE):
      batch = codes[start : start + WHOLE_QUOTE_INSTRUMENT_DETAIL_BATCH_SIZE]
      details = self.data_manager.get_instrument_detail_list(
        batch,
        iscomplete=True,
      )
      if not isinstance(details, dict):
        continue
      for code, raw_detail in details.items():
        detail = _as_dict(raw_detail)
        upper_limit = self._positive_number(
          detail.get("UpStopPrice")
          or detail.get("up_stop_price")
          or detail.get("upperLimit")
        )
        lower_limit = self._positive_number(
          detail.get("DownStopPrice")
          or detail.get("down_stop_price")
          or detail.get("lowerLimit")
        )
        price_tick = self._positive_number(
          detail.get("PriceTick")
          or detail.get("price_tick")
          or detail.get("priceTick")
        )
        values: dict[str, float] = {}
        if upper_limit > 0:
          values["upperLimit"] = upper_limit
        if lower_limit > 0:
          values["lowerLimit"] = lower_limit
        if price_tick > 0:
          values["priceTick"] = price_tick
        if values:
          metadata[str(code).strip().upper()] = values
    return tuple(codes), metadata

  def _refresh_whole_quote_metadata(self, markets: list[str]) -> bool:
    with self._whole_quote_lifecycle_lock:
      return self._refresh_whole_quote_metadata_serialized(markets)

  def _refresh_whole_quote_metadata_serialized(self, markets: list[str]) -> bool:
    refresh_date = datetime.now(SHANGHAI_TIMEZONE).date()
    with self._lock:
      self._whole_quote_metadata_last_attempt_monotonic = time.monotonic()
    try:
      with self._access_lock:
        codes, metadata = self._load_whole_quote_metadata(markets)
      if not codes:
        raise RuntimeError("QMT returned no SH/SZ instruments")
      candidate = self._build_whole_quote_universe(
        trading_date=refresh_date,
        codes=codes,
        metadata=metadata,
      )
      with self._lock:
        active = self._whole_quote_active_universe
        subscription_active = self._whole_quote_subscription is not None
        action = "metadata-updated"
        if active is None:
          self._whole_quote_active_universe = candidate
          self._whole_quote_pending_universe = None
          self._whole_quote_universe_generation += 1
          action = "activated"
        elif subscription_active and active.codes != candidate.codes:
          pending = self._whole_quote_pending_universe
          if pending is None or pending.codes != candidate.codes:
            self._whole_quote_universe_generation += 1
          self._whole_quote_pending_universe = candidate
          action = "staged"
        else:
          codes_changed = active.codes != candidate.codes
          self._whole_quote_active_universe = candidate
          self._whole_quote_pending_universe = None
          if codes_changed:
            self._whole_quote_universe_generation += 1
            action = "activated"
        generation = self._whole_quote_universe_generation
      logger.info(
        "Whole-quote universe refreshed: markets=%s instruments=%s "
        "fingerprint=%s generation=%s action=%s",
        markets,
        len(codes),
        candidate.fingerprint,
        generation,
        action,
      )
      return True
    except Exception as exc:
      logger.warning(
        "Whole-quote universe refresh failed: markets=%s error=%s",
        markets,
        exc.__class__.__name__,
      )
      return False

  def _refresh_whole_quote_metadata_in_background(
    self,
    markets: list[str],
  ) -> None:
    try:
      self._refresh_whole_quote_metadata(markets)
    finally:
      with self._lock:
        self._whole_quote_metadata_refreshing = False

  def _filter_whole_quote_data(self, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
      return {}
    with self._lock:
      active = self._whole_quote_active_universe
      allowed_codes = active.code_set if active is not None else frozenset()
    if not allowed_codes:
      return {}
    return {
      normalized_code: raw_tick
      for code, raw_tick in data.items()
      if (normalized_code := str(code).strip().upper()) in allowed_codes
    }

  def _invalidate_whole_quote_callback_epoch(self, callback_epoch: int) -> None:
    with self._whole_quote_callback_condition:
      if callback_epoch == self._whole_quote_callback_epoch:
        self._whole_quote_callback_epoch += 1
      while self._whole_quote_callbacks_inflight > 0:
        self._whole_quote_callback_condition.wait()

  def _ensure_whole_quote_metadata_current(self, markets: list[str]) -> None:
    today = datetime.now(SHANGHAI_TIMEZONE).date()
    now_monotonic = time.monotonic()
    with self._lock:
      newest = (
        self._whole_quote_pending_universe
        or self._whole_quote_active_universe
      )
      if (
        (newest is not None and newest.trading_date == today)
        or self._whole_quote_metadata_refreshing
        or (
          self._whole_quote_metadata_last_attempt_monotonic > 0
          and now_monotonic - self._whole_quote_metadata_last_attempt_monotonic
          < WHOLE_QUOTE_METADATA_REFRESH_RETRY_SECONDS
        )
      ):
        return
      self._whole_quote_metadata_refreshing = True
      self._whole_quote_metadata_last_attempt_monotonic = now_monotonic
    threading.Thread(
      target=self._refresh_whole_quote_metadata_in_background,
      args=(list(markets),),
      name="qmt-whole-quote-metadata-refresh",
      daemon=True,
    ).start()

  def _enrich_whole_quote_data(self, data: Any) -> Any:
    if not isinstance(data, dict):
      return data
    with self._lock:
      active = self._whole_quote_active_universe
      metadata = active.metadata if active is not None else {}
    for code, raw_tick in data.items():
      if not isinstance(raw_tick, dict):
        continue
      values = metadata.get(str(code).strip().upper())
      if not values:
        continue
      if self._positive_number(
        raw_tick.get("upperLimit")
        or raw_tick.get("UpStopPrice")
        or raw_tick.get("up_stop_price")
      ) <= 0 and values.get("upperLimit", 0) > 0:
        raw_tick["upperLimit"] = values["upperLimit"]
      if self._positive_number(
        raw_tick.get("lowerLimit")
        or raw_tick.get("DownStopPrice")
        or raw_tick.get("down_stop_price")
      ) <= 0 and values.get("lowerLimit", 0) > 0:
        raw_tick["lowerLimit"] = values["lowerLimit"]
      if self._positive_number(
        raw_tick.get("priceTick")
        or raw_tick.get("PriceTick")
        or raw_tick.get("price_tick")
      ) <= 0 and values.get("priceTick", 0) > 0:
        raw_tick["priceTick"] = values["priceTick"]
    return data

  @staticmethod
  def _valid_subscription(value: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    return bool(values) and all(isinstance(item, int) and item > 0 for item in values)

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
      safe_data = _json_safe(data)
      callback(
        {
          "subscription_id": subscription_id,
          "kind": kind,
          "stock_code": stock_code,
          "period": period,
          "data": safe_data,
        }
      )

    with self._access_lock:
      if kind == "quote" and stock_code:
        local_id = self.data_manager.subscribe_quote(
          stock_code,
          period=period,
          start_time=str(payload.get("start_time") or ""),
          end_time=str(payload.get("end_time") or ""),
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

  def subscribe_whole_market(self, callback) -> bool:
    """Subscribe once to the fixed SH/SZ A-share and index universe."""
    with self._whole_quote_lifecycle_lock:
      return self._subscribe_whole_market_serialized(callback)

  def _subscribe_whole_market_serialized(self, callback) -> bool:
    markets = ["SH", "SZ"]
    today = datetime.now(SHANGHAI_TIMEZONE).date()
    with self._lock:
      if self._whole_quote_subscription is not None:
        return True
      pending = self._whole_quote_pending_universe
      if pending is not None and pending.trading_date == today:
        self._whole_quote_active_universe = pending
        self._whole_quote_pending_universe = None
      active = self._whole_quote_active_universe
      universe_is_current = active is not None and active.trading_date == today
    if not universe_is_current:
      now_monotonic = time.monotonic()
      with self._lock:
        retry_allowed = (
          self._whole_quote_metadata_last_attempt_monotonic <= 0
          or now_monotonic - self._whole_quote_metadata_last_attempt_monotonic
          >= WHOLE_QUOTE_METADATA_REFRESH_RETRY_SECONDS
        )
      if retry_allowed:
        self._refresh_whole_quote_metadata(markets)
    with self._lock:
      active = self._whole_quote_active_universe
      if active is None:
        return False
      codes = active.codes
      callback_epoch = self._whole_quote_callback_epoch + 1
      self._whole_quote_callback_epoch = callback_epoch
    if not codes:
      return False

    def on_data(data: Any) -> None:
      filtered = self._filter_whole_quote_data(data)
      if not filtered:
        return
      # The native SDK may deliver a callback after unsubscribe_quote returns.
      # Invalidation rejects late callbacks and waits for callbacks that already
      # crossed this boundary before reset_source() can clear the old capture.
      with self._whole_quote_callback_condition:
        if callback_epoch != self._whole_quote_callback_epoch:
          return
        self._whole_quote_callbacks_inflight += 1
      try:
        callback(filtered)
      finally:
        with self._whole_quote_callback_condition:
          self._whole_quote_callbacks_inflight -= 1
          if self._whole_quote_callbacks_inflight == 0:
            self._whole_quote_callback_condition.notify_all()

    try:
      with self._access_lock:
        local_id = self.data_manager.subscribe_whole_quote(
          list(codes),
          callback=on_data,
        )
    except Exception:
      self._invalidate_whole_quote_callback_epoch(callback_epoch)
      raise
    if not self._valid_subscription(local_id):
      self._invalidate_whole_quote_callback_epoch(callback_epoch)
      return False
    with self._lock:
      if self._whole_quote_subscription is None:
        self._whole_quote_subscription = local_id
        self._whole_quote_bound_universe_generation = (
          self._whole_quote_universe_generation
        )
        logger.info(
          "Whole-quote explicit universe subscribed: instruments=%s "
          "fingerprint=%s generation=%s",
          len(codes),
          active.fingerprint,
          self._whole_quote_universe_generation,
        )
        return True
    self._unsubscribe_local(local_id)
    return True

  def whole_market_codes(self) -> tuple[str, ...]:
    today = datetime.now(SHANGHAI_TIMEZONE).date()
    with self._lock:
      active = self._whole_quote_active_universe
      needs_refresh = (
        active is None
        or (
          self._whole_quote_subscription is None
          and active.trading_date != today
        )
      )
    if needs_refresh:
      self._refresh_whole_quote_metadata(["SH", "SZ"])
    with self._lock:
      active = self._whole_quote_active_universe
      return active.codes if active is not None else ()

  def whole_market_universe_generation(self) -> int:
    with self._lock:
      return self._whole_quote_universe_generation

  def whole_market_bound_universe_generation(self) -> int:
    with self._lock:
      return self._whole_quote_bound_universe_generation

  def is_whole_market_trading_session(self) -> bool:
    # This health probe also drives the low-frequency daily universe refresh.
    # Loading happens on a daemon worker and never blocks the event loop.
    self._ensure_whole_quote_metadata_current(["SH", "SZ"])
    now = datetime.now(SHANGHAI_TIMEZONE)
    local_time = now.time().replace(tzinfo=None)
    if not (
      datetime_time(9, 30) <= local_time <= datetime_time(11, 30)
      or datetime_time(13, 0) <= local_time <= datetime_time(15, 0)
    ):
      return False
    today = now.date()
    with self._lock:
      if self._whole_quote_calendar_date == today:
        return self._whole_quote_is_trading_date
    reader = getattr(self.data_manager, "get_trading_dates", None)
    if not callable(reader):
      return today.weekday() < 5
    try:
      with self._access_lock:
        values = reader("SH", today, today)
      today_text = today.strftime("%Y%m%d")
      is_trading_date = any(
        str(value).replace("-", "")[:8] == today_text
        for value in (values or [])
      )
    except Exception as exc:
      logger.warning(
        "Could not verify whole-market trading date: error=%s",
        exc.__class__.__name__,
      )
      return False
    with self._lock:
      self._whole_quote_calendar_date = today
      self._whole_quote_is_trading_date = is_trading_date
    return is_trading_date

  def whole_market_snapshot(self) -> dict[str, dict[str, Any]]:
    """Read a complete SH/SZ state using bounded native SDK calls."""
    codes = self.whole_market_codes()
    if not codes:
      raise RuntimeError("SH/SZ instrument universe is empty")
    snapshot: dict[str, Any] = {}
    for start in range(0, len(codes), WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE):
      batch = list(codes[start : start + WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE])
      # Do not expose a partial result: an exception from any native call
      # aborts this method before the locally accumulated mapping is returned.
      snapshot.update(self.whole_market_snapshot_chunk(batch))
      if start + WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE < len(codes):
        # get_full_tick may hold the GIL. Explicitly yield between native calls
        # so the Agent heartbeat/event-loop thread gets a scheduling window.
        time.sleep(0)
    return self.prepare_whole_market_data(snapshot)

  def whole_market_snapshot_chunk(
    self,
    codes: list[str],
  ) -> dict[str, dict[str, Any]]:
    """Read at most one bounded native full-tick fragment."""
    requested = [str(code).strip().upper() for code in codes if str(code).strip()]
    if not requested:
      return {}
    if len(requested) > WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE:
      raise ValueError(
        "whole-market snapshot fragment exceeds native batch limit: "
        f"codes={len(requested)} max={WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE}"
      )
    with self._lock:
      active = self._whole_quote_active_universe
      allowed_codes = active.code_set if active is not None else frozenset()
    if any(code not in allowed_codes for code in requested):
      raise ValueError("whole-market snapshot fragment contains unknown code")
    with self._access_lock:
      raw_snapshot = self.data_manager.get_full_tick(list(requested))
    if not isinstance(raw_snapshot, dict):
      raise RuntimeError("XTData returned an invalid full-tick fragment")
    safe_snapshot = _json_safe(raw_snapshot)
    if not isinstance(safe_snapshot, dict):
      raise RuntimeError("XTData full-tick fragment could not be normalized")
    return safe_snapshot

  def prepare_whole_market_data(self, data: Any) -> dict[str, dict[str, Any]]:
    """Normalize one XT callback outside the callback thread."""
    filtered = self._filter_whole_quote_data(data)
    selected = {
      code: {
        field: raw_tick[field]
        for field in WHOLE_QUOTE_TICK_FIELDS
        if field in raw_tick
      }
      for code, raw_tick in filtered.items()
      if isinstance(raw_tick, dict)
    }
    safe_data = _json_safe(selected)
    enriched = self._enrich_whole_quote_data(safe_data)
    return enriched if isinstance(enriched, dict) else {}

  def unsubscribe_whole_market(self) -> None:
    with self._whole_quote_lifecycle_lock:
      self._unsubscribe_whole_market_serialized()

  def _unsubscribe_whole_market_serialized(self) -> None:
    with self._whole_quote_callback_condition:
      local_id = self._whole_quote_subscription
      self._whole_quote_subscription = None
      if local_id is not None:
        self._whole_quote_callback_epoch += 1
        self._whole_quote_bound_universe_generation = 0
        while self._whole_quote_callbacks_inflight > 0:
          self._whole_quote_callback_condition.wait()
    if local_id is not None:
      self._unsubscribe_local(local_id, suppress_errors=False)

  def _unsubscribe_local(
    self,
    local_id: int | list[int],
    *,
    suppress_errors: bool = True,
  ) -> None:
    values = local_id if isinstance(local_id, list) else [local_id]
    errors: list[Exception] = []
    with self._access_lock:
      for value in values:
        try:
          self.data_manager.unsubscribe_quote(int(value))
        except Exception as exc:
          errors.append(exc)
    if errors and not suppress_errors:
      raise RuntimeError(
        f"failed to cancel {len(errors)} XTData subscription(s)"
      ) from errors[0]

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
    with self._xtdata_access_lock:
      ready, _ = _observe_market_data_connection(self)
      return ready

  def ensure_market_data_ready(self) -> bool:
    with self._xtdata_access_lock:
      ready = _ensure_market_data_manager_connected(self.data_manager)
      _observe_market_data_connection(self)
      return ready

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

  def subscribe_whole_market(self, callback) -> bool:
    return self.market_streamer.subscribe_whole_market(callback)

  def whole_market_codes(self) -> tuple[str, ...]:
    return self.market_streamer.whole_market_codes()

  def market_data_connection_generation(self) -> int:
    with self._xtdata_access_lock:
      _, connection_generation = _observe_market_data_connection(self)
    return _combine_market_data_source_generation(
      connection_generation,
      self.market_streamer.whole_market_universe_generation(),
    )

  def market_data_subscription_generation(self) -> int:
    with self._xtdata_access_lock:
      _, connection_generation = _observe_market_data_connection(self)
    return _combine_market_data_source_generation(
      connection_generation,
      self.market_streamer.whole_market_bound_universe_generation(),
    )

  def is_whole_market_trading_session(self) -> bool:
    return self.market_streamer.is_whole_market_trading_session()

  def whole_market_snapshot(self) -> dict[str, dict[str, Any]]:
    return self.market_streamer.whole_market_snapshot()

  def whole_market_snapshot_chunk(
    self,
    codes: list[str],
  ) -> dict[str, dict[str, Any]]:
    return self.market_streamer.whole_market_snapshot_chunk(codes)

  def prepare_whole_market_data(self, data: Any) -> dict[str, dict[str, Any]]:
    return self.market_streamer.prepare_whole_market_data(data)

  def unsubscribe_whole_market(self) -> None:
    self.market_streamer.unsubscribe_whole_market()


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
    with self._xtdata_access_lock:
      ready, _ = _observe_market_data_connection(self)
      return ready

  def ensure_market_data_ready(self) -> bool:
    with self._xtdata_access_lock:
      ready = _ensure_market_data_manager_connected(self.data_manager)
      _observe_market_data_connection(self)
      return ready

  def full_snapshot(self) -> dict[str, Any]:
    accounts = []
    positions = {}
    orders = []
    trades = []
    unavailable_accounts = []
    section_completeness_by_account: dict[str, dict[str, bool]] = {}
    required_sections = ("account", "positions", "orders", "trades")
    with self._trading_access_lock:
      for account_id, agent in self.agents.items():
        if not bool(getattr(agent.trading_manager, "is_connected", False)):
          unavailable_accounts.append(account_id)
          section_completeness_by_account[account_id] = {
            section: False for section in required_sections
          }
          accounts.append(
            {
              "account_id": account_id,
              "connection_status": "DISCONNECTED",
            }
          )
          positions[account_id] = []
          continue
        snapshot = agent.full_snapshot()
        raw_section_completeness = snapshot.get("section_completeness")
        if isinstance(raw_section_completeness, dict):
          section_completeness = {
            section: raw_section_completeness.get(section) is True
            for section in required_sections
          }
        else:
          section_completeness = {
            section: False for section in required_sections
          }
        section_completeness_by_account[account_id] = section_completeness
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
        snapshot_complete = bool(
          snapshot.get("is_complete") is True
          and all(section_completeness.values())
        )
        if not snapshot_complete:
          unavailable_accounts.append(account_id)
        else:
          agent.mark_report_received()
        account["account_id"] = account_id
        account["snapshot_is_complete"] = snapshot_complete
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
      "section_completeness_by_account": section_completeness_by_account,
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
    command["order_remark"] = f"qx:{str(payload['client_order_id'])[:20]}"
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

  def subscribe_whole_market(self, callback) -> bool:
    return self.market_streamer.subscribe_whole_market(callback)

  def whole_market_codes(self) -> tuple[str, ...]:
    return self.market_streamer.whole_market_codes()

  def market_data_connection_generation(self) -> int:
    with self._xtdata_access_lock:
      _, connection_generation = _observe_market_data_connection(self)
    return _combine_market_data_source_generation(
      connection_generation,
      self.market_streamer.whole_market_universe_generation(),
    )

  def market_data_subscription_generation(self) -> int:
    with self._xtdata_access_lock:
      _, connection_generation = _observe_market_data_connection(self)
    return _combine_market_data_source_generation(
      connection_generation,
      self.market_streamer.whole_market_bound_universe_generation(),
    )

  def is_whole_market_trading_session(self) -> bool:
    return self.market_streamer.is_whole_market_trading_session()

  def whole_market_snapshot(self) -> dict[str, dict[str, Any]]:
    return self.market_streamer.whole_market_snapshot()

  def whole_market_snapshot_chunk(
    self,
    codes: list[str],
  ) -> dict[str, dict[str, Any]]:
    return self.market_streamer.whole_market_snapshot_chunk(codes)

  def prepare_whole_market_data(self, data: Any) -> dict[str, dict[str, Any]]:
    return self.market_streamer.prepare_whole_market_data(data)

  def unsubscribe_whole_market(self) -> None:
    self.market_streamer.unsubscribe_whole_market()


def _iter_locked_market_data_records(
  manager: Any,
  payload: dict[str, Any],
  access_lock: threading.RLock,
) -> Iterator[dict[str, Any]]:
  with access_lock:
    _ensure_market_data_manager_connected(manager)
    yield from _iter_market_data_records(manager, payload)


def _observe_market_data_connection(owner: Any) -> tuple[bool, int]:
  """Track native connection continuity without modifying the SDK adapter."""
  manager = owner.data_manager
  ready = bool(getattr(manager, "is_connected", False))
  client = getattr(manager, "_client", None)
  identity = id(client) if client is not None else id(manager)
  previous_ready = bool(
    getattr(owner, "_observed_market_data_ready", False)
  )
  previous_identity = int(
    getattr(owner, "_observed_market_data_identity", 0)
  )
  generation = int(getattr(owner, "_market_data_generation", 0))
  if ready and (not previous_ready or identity != previous_identity):
    generation += 1
  owner._observed_market_data_ready = ready
  owner._observed_market_data_identity = identity
  owner._market_data_generation = generation
  return ready, generation


def _combine_market_data_source_generation(
  connection_generation: int,
  universe_generation: int,
) -> int:
  """Expose native reconnects and universe changes through one monotonic token."""
  connection = max(0, int(connection_generation))
  universe = max(0, int(universe_generation))
  return (connection << 32) | (universe & 0xFFFFFFFF)


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
    yield from _financial_data_records(manager, payload)
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
      raise ValueError("XTData returned a non-object market-data result")
    normalized_values: dict[str, Any] = {}
    for code, frame in values.items():
      normalized_code = str(code).strip().upper()
      if normalized_code not in requested_codes:
        raise ValueError(f"XTData returned unrequested instrument: {normalized_code}")
      if normalized_code in normalized_values:
        raise ValueError(
          f"XTData returned duplicate normalized instrument: {normalized_code}"
        )
      normalized_values[normalized_code] = frame

    for normalized_code in sorted(request.codes):
      frame = normalized_values.get(normalized_code)
      if frame is None:
        yield HistoricalBarSummary(
          code=normalized_code,
          period=period,
          row_count=0,
          min_time=None,
          max_time=None,
          key_sha256=hashlib.sha256(b"").hexdigest(),
          no_data_reason=HISTORICAL_BAR_NO_DATA_REASON,
        ).model_dump(mode="json")
        continue
      if not all(
        hasattr(frame, attribute)
        for attribute in ("columns", "itertuples", "sort_values")
      ):
        raise ValueError(
          f"XTData returned a non-DataFrame result for {normalized_code}/{period}"
        )
      if len(frame) > MAX_MARKET_DATA_FRAME_RECORDS:
        raise ValueError("single market data frame exceeds record limit")
      normalized = (
        frame if "time" in getattr(frame, "columns", ()) else frame.reset_index()
      )
      if "time" not in normalized.columns and len(normalized.columns) > 0:
        normalized = normalized.rename(columns={normalized.columns[0]: "time"})
      if "time" not in normalized.columns:
        raise ValueError(
          f"market data frame for {normalized_code} has no time column"
        )
      reserved_columns = _RESERVED_HISTORICAL_BAR_COLUMNS.intersection(
        str(column) for column in normalized.columns
      )
      if reserved_columns:
        names = ", ".join(sorted(reserved_columns))
        raise ValueError(
          f"market data frame for {normalized_code} contains reserved columns: {names}"
        )
      normalize_time = (
        _normalize_daily_market_timestamp
        if period == "1d"
        else _normalize_market_timestamp
      )
      normalized_times = [normalize_time(value) for value in normalized["time"].array]
      normalized = normalized.assign(
        **{_NORMALIZED_MARKET_TIME_COLUMN: normalized_times}
      ).sort_values(
        by=_NORMALIZED_MARKET_TIME_COLUMN,
        kind="mergesort",
      )
      columns = tuple(normalized.columns)
      records: list[dict[str, Any]] = []
      for values_tuple in normalized.itertuples(index=False, name=None):
        row = dict(zip(columns, values_tuple, strict=True))
        record = _project_historical_bar_record(
          row,
          code=normalized_code,
          period=period,
          source_time_ms=int(row[_NORMALIZED_MARKET_TIME_COLUMN]),
        )
        if record["time"] < lower_bound or record["time"] > upper_bound:
          raise ValueError(
            "XTData returned bar time outside requested range: "
            f"{normalized_code}/{period}/{record['time']}"
          )
        records.append(record)

      if period == "tick":
        group_start = 0
        while group_start < len(records):
          timestamp = int(records[group_start]["time"])
          group_end = group_start + 1
          while (
            group_end < len(records)
            and int(records[group_end]["time"]) == timestamp
          ):
            group_end += 1
          group = records[group_start:group_end]
          if len(group) > HISTORICAL_TICK_ORDINALS_PER_MILLISECOND:
            raise ValueError(
              "XTData returned too many ticks for one millisecond: "
              f"{normalized_code}/{period}/{timestamp}/{len(group)}"
            )
          if len(group) == 1:
            group[0][HISTORICAL_TICK_ORDINAL_FIELD] = 0
            records[group_start:group_end] = group
            group_start = group_end
            continue
          ordered_group = sorted(group, key=_tick_record_order_key)
          for ordinal, record in enumerate(ordered_group):
            record[HISTORICAL_TICK_ORDINAL_FIELD] = ordinal
          records[group_start:group_end] = ordered_group
          group_start = group_end
      else:
        previous_time: int | None = None
        for record in records:
          current_time = int(record["time"])
          if previous_time is not None and current_time <= previous_time:
            raise ValueError(
              "XTData returned duplicate or unordered normalized bar key: "
              f"{normalized_code}/{period}/{current_time}"
            )
          previous_time = current_time

      key_digest = hashlib.sha256()
      for index, record in enumerate(records):
        if index:
          key_digest.update(b"\n")
        key_digest.update(
          historical_bar_key(
            code=normalized_code,
            period=period,
            time_ms=int(record["time"]),
            tick_ordinal=(
              int(record[HISTORICAL_TICK_ORDINAL_FIELD])
              if period == "tick"
              else None
            ),
          ).encode("utf-8")
        )
        yield record
      yield HistoricalBarSummary(
        code=normalized_code,
        period=period,
        row_count=len(records),
        min_time=int(records[0]["time"]) if records else None,
        max_time=int(records[-1]["time"]) if records else None,
        key_sha256=key_digest.hexdigest(),
        no_data_reason=(None if records else HISTORICAL_BAR_NO_DATA_REASON),
      ).model_dump(mode="json")


def _validate_bars_request(payload: dict[str, Any]) -> _ValidatedBarsRequest:
  raw_codes = payload.get("stock_list")
  if not isinstance(raw_codes, list) or not raw_codes:
    raise ValueError("bars request requires a non-empty stock_list")
  if any(
    not isinstance(code, str) or code != code.strip().upper()
    for code in raw_codes
  ):
    raise ValueError("bars request stock_list must use canonical instrument codes")
  codes = tuple(raw_codes)
  if len(codes) > MAX_MARKET_DATA_CODES:
    raise ValueError("bars request exceeds instrument count limit")
  if len(set(codes)) != len(codes):
    raise ValueError("bars request contains duplicate instruments")
  code_pattern = re.compile(r"^[A-Z0-9]{1,16}\.(?:SH|SZ|BJ)$")
  invalid_codes = [code for code in codes if not code_pattern.fullmatch(code)]
  if invalid_codes:
    raise ValueError(f"bars request contains invalid instruments: {invalid_codes}")

  raw_periods = payload.get("periods") or ["1d"]
  if not isinstance(raw_periods, list) or any(
    not isinstance(period, str) or period != period.strip().lower()
    for period in raw_periods
  ):
    raise ValueError("bars request periods must use canonical values")
  periods = tuple(raw_periods)
  if not periods or len(set(periods)) != len(periods):
    raise ValueError("bars request periods must be non-empty and unique")
  unsupported = [
    period for period in periods if period not in SUPPORTED_HISTORICAL_BAR_PERIODS
  ]
  if unsupported:
    raise ValueError(f"bars request contains unsupported periods: {unsupported}")

  start_text = str(payload.get("start_time") or "").strip()
  end_text = str(payload.get("end_time") or "").strip()
  try:
    start_local = datetime.strptime(start_text, "%Y%m%d").replace(
      tzinfo=SHANGHAI_TIMEZONE
    )
    end_local = datetime.strptime(end_text, "%Y%m%d").replace(tzinfo=SHANGHAI_TIMEZONE)
  except ValueError as exc:
    raise ValueError("bars request dates must be YYYYMMDD") from exc
  if end_local < start_local:
    raise ValueError("bars request end_time precedes start_time")
  _normalize_market_timestamp(start_local)
  _normalize_market_timestamp(end_local)
  span_days = (end_local.date() - start_local.date()).days + 1
  for period in periods:
    if span_days > MAX_BAR_DATE_SPAN_DAYS[period]:
      raise ValueError(f"bars request date span exceeds {period} limit")
  estimated_records = (
    len(codes)
    * span_days
    * sum(ESTIMATED_BAR_RECORDS_PER_DAY[period] for period in periods)
  )
  estimated_records += len(codes) * len(periods)
  if estimated_records > MAX_MARKET_DATA_RECORDS:
    raise ValueError("bars request estimated record count exceeds safe limit")
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


def _normalize_financial_date(value: Any) -> str | None:
  """Normalize XTData financial dates to the wire-format YYYYMMDD."""
  if value is None or isinstance(value, bool):
    return None
  try:
    if bool(value != value):
      return None
  except Exception:
    pass

  to_pydatetime = getattr(value, "to_pydatetime", None)
  if callable(to_pydatetime):
    try:
      value = to_pydatetime()
    except Exception as exc:
      raise ValueError("financial date is not supported") from exc
  if isinstance(value, datetime):
    return value.strftime("%Y%m%d")

  candidate = str(value).strip()
  if not candidate:
    return None
  if candidate.endswith(".0") and candidate[:-2].isdigit():
    candidate = candidate[:-2]
  if len(candidate) == 8 and candidate.isdigit():
    try:
      return datetime.strptime(candidate, "%Y%m%d").strftime("%Y%m%d")
    except ValueError as exc:
      raise ValueError("financial date is not supported") from exc
  if candidate.isdigit():
    timestamp = _normalize_market_timestamp(int(candidate))
    return (
      datetime.fromtimestamp(
        timestamp / 1000,
        timezone.utc,
      )
      .astimezone(SHANGHAI_TIMEZONE)
      .strftime("%Y%m%d")
    )
  try:
    return datetime.fromisoformat(candidate.replace("Z", "+00:00")).strftime("%Y%m%d")
  except ValueError as exc:
    raise ValueError("financial date is not supported") from exc


def _normalize_financial_report_date(value: Any) -> str | None:
  """Canonicalize XTData's occasional quarter-end-minus-one report date."""
  normalized = _normalize_financial_date(value)
  if normalized is None:
    return None
  parsed = datetime.strptime(normalized, "%Y%m%d")
  quarter_end_days = {3: 31, 6: 30, 9: 30, 12: 31}
  quarter_end_day = quarter_end_days.get(parsed.month)
  if quarter_end_day is not None and parsed.day == quarter_end_day - 1:
    return parsed.replace(day=quarter_end_day).strftime("%Y%m%d")
  return normalized


def _financial_json_safe(value: Any) -> Any:
  if value is None:
    return None
  if isinstance(value, Real) and not isinstance(value, bool):
    numeric = float(value)
    if not math.isfinite(numeric):
      return None
  return _json_safe(value)


def _financial_data_records(
  manager: Any,
  payload: dict[str, Any],
) -> Iterator[dict[str, Any]]:
  codes = tuple(
    sorted(
      {
        str(code).strip().upper()
        for code in payload.get("stock_list") or []
        if str(code).strip()
      }
    )
  )
  if not codes:
    raise ValueError("financial_data requires a non-empty stock_list")
  if len(codes) > MAX_FINANCIAL_DATA_CODES:
    raise ValueError(
      f"financial_data accepts at most {MAX_FINANCIAL_DATA_CODES} instruments"
    )

  record_format = str(payload.get("record_format") or FINANCIAL_DATA_RECORD_FORMAT)
  if record_format != FINANCIAL_DATA_RECORD_FORMAT:
    raise ValueError(f"unsupported financial_data record_format: {record_format}")
  requested_tables = list(
    dict.fromkeys(payload.get("table_list") or SUPPORTED_FINANCIAL_TABLES)
  )
  invalid_tables = [
    table for table in requested_tables if table not in SUPPORTED_FINANCIAL_TABLES
  ]
  if invalid_tables:
    raise ValueError(f"unsupported financial_data tables: {invalid_tables}")

  start_time = str(payload.get("start_time") or "")
  end_time = str(payload.get("end_time") or "")
  for label, value in (("start_time", start_time), ("end_time", end_time)):
    if len(value) != 8 or not value.isdigit():
      raise ValueError(f"financial_data {label} must be YYYYMMDD")
  if end_time < start_time:
    raise ValueError("financial_data end_time precedes start_time")

  if bool(payload.get("download", True)):
    manager.download_financial_data_list(
      list(codes),
      table_list=requested_tables,
      start_time=start_time,
      end_time=end_time,
    )
  values = manager.get_financial_data_list(
    list(codes),
    table_list=requested_tables,
    start_time=start_time,
    end_time=end_time,
    report_type="announce_time",
  )
  if not isinstance(values, dict):
    raise ValueError("unexpected financial_data result")
  unexpected_codes = sorted(
    str(code).strip().upper()
    for code in values
    if str(code).strip().upper() not in codes
  )
  if unexpected_codes:
    raise ValueError(
      f"XTData returned unrequested financial instruments: {unexpected_codes}"
    )

  for code in codes:
    tables = values.get(code) or {}
    if not isinstance(tables, dict):
      raise ValueError(f"unexpected financial_data result for {code}")
    table_counts: dict[str, int] = {}
    for table in requested_tables:
      frame = tables.get(table)
      if frame is None or bool(getattr(frame, "empty", False)):
        table_counts[table] = 0
        continue
      if not hasattr(frame, "reset_index") or not hasattr(frame, "to_dict"):
        raise ValueError(f"unexpected financial_data {table} for {code}")
      normalized = frame.reset_index()
      rows = normalized.to_dict(orient="records")
      rows_by_report_date: dict[str, tuple[str, int, dict[str, Any]]] = {}
      for row_index, raw_row in enumerate(rows):
        row = {str(key): _financial_json_safe(value) for key, value in raw_row.items()}
        row["m_timetag"] = _normalize_financial_report_date(row.get("m_timetag"))
        row["m_anntime"] = _normalize_financial_date(row.get("m_anntime"))
        if row["m_timetag"] is None:
          raise ValueError(f"financial_data row has no report date: {code}/{table}")
        report_date = str(row["m_timetag"])
        priority = (str(row.get("m_anntime") or ""), row_index)
        current = rows_by_report_date.get(report_date)
        if current is None or priority >= current[:2]:
          rows_by_report_date[report_date] = (*priority, row)
      table_counts[table] = len(rows_by_report_date)
      for report_date in sorted(rows_by_report_date):
        row = rows_by_report_date[report_date][2]
        yield {
          "record_type": "financial_row",
          "schema_version": 1,
          "code": code,
          "table": table,
          "row": row,
        }
    yield {
      "record_type": "financial_summary",
      "schema_version": 1,
      "code": code,
      "table_counts": table_counts,
    }


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
        delta.days * 86_400 + delta.seconds
      ) * 1000 + delta.microseconds // 1000
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
    raise ValueError("market data time is outside the supported range") from exc
  latest = datetime.now(timezone.utc) + timedelta(days=MARKET_TIMESTAMP_MAX_FUTURE_DAYS)
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
      normalized = normalized.rename(columns={normalized.columns[0]: "ex_date"})
    missing = [
      field for field in _DIVID_FACTOR_FIELDS if field not in normalized.columns
    ]
    if missing:
      raise ValueError(f"divid_factors result for {code} is missing fields: {missing}")

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
          raise ValueError(f"non-finite divid_factors {field} for {code}/{ex_date}")
        record[field] = numeric
      if record["time"] <= 0 or record["dr"] <= 0:
        raise ValueError(f"non-positive divid_factors time/dr for {code}/{ex_date}")
      records.append(record)
  return records
