"""Conservative XTData adapter for the local outbound QMT Agent."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterable

import pandas as pd
from xtquant import xtdata

from .connection_discovery import XTDataEndpoint, discover_xtdata_endpoint

logger = logging.getLogger(__name__)
XTDATA_RECONNECT_INTERVAL_SECONDS = 5.0


class XTDataUnavailableError(RuntimeError):
  """The local XTData service is not ready for a data operation."""


def _codes(value: str | Iterable[str] | None) -> list[str]:
  if value is None:
    return []
  if isinstance(value, str):
    return [value]
  return [str(item) for item in value if str(item)]


class XTDataManager:
  """Thin, local-only wrapper around the official ``xtdata`` module."""

  def __init__(self, config: Any = None, *, auto_connect: bool = True) -> None:
    self.config = config
    self.is_connected = False
    self._subscription_ids: set[int] = set()
    self._connection_lock = threading.RLock()
    self._client: Any = None
    self._connected_endpoint: XTDataEndpoint | None = None
    self._last_connection_attempt = 0.0
    self._last_connection_error = ""
    if auto_connect:
      self._init_connection()

  def _ensure_connection_state(self) -> None:
    """Backfill state for compatibility tests constructing via ``__new__``."""
    if not hasattr(self, "_connection_lock"):
      self._connection_lock = threading.RLock()
    if not hasattr(self, "_client"):
      self._client = None
    if not hasattr(self, "_connected_endpoint"):
      self._connected_endpoint = None
    if not hasattr(self, "_last_connection_attempt"):
      self._last_connection_attempt = 0.0
    if not hasattr(self, "_last_connection_error"):
      self._last_connection_error = ""

  @property
  def last_connection_error(self) -> str:
    self._ensure_connection_state()
    return self._last_connection_error

  @property
  def connected_endpoint(self) -> XTDataEndpoint | None:
    self._ensure_connection_state()
    return self._connected_endpoint

  def _connection_alive(self) -> bool:
    if not self.is_connected:
      return False
    self._ensure_connection_state()
    if self._client is None:
      return True
    try:
      connected = bool(self._client.is_connected())
    except Exception:
      connected = False
    if not connected:
      self.is_connected = False
    return connected

  def _init_connection(self) -> bool:
    return self.ensure_connected(force=True)

  def ensure_connected(self, *, force: bool = False) -> bool:
    """Connect once per retry interval using one verified loopback endpoint."""
    self._ensure_connection_state()
    with self._connection_lock:
      if self._connection_alive():
        return True
      now = time.monotonic()
      if (
        not force
        and now - self._last_connection_attempt
        < XTDATA_RECONNECT_INTERVAL_SECONDS
      ):
        return False
      self._last_connection_attempt = now
      endpoint: XTDataEndpoint | None = None
      try:
        endpoint = discover_xtdata_endpoint()
        client = xtdata.connect(
          ip=endpoint.host,
          port=endpoint.port,
          remember_if_success=True,
        )
        if client is None or not bool(client.is_connected()):
          raise RuntimeError("SDK returned a disconnected client")
        self._client = client
        self._connected_endpoint = endpoint
        self._last_connection_error = ""
        self.is_connected = True
        logger.info(
          "XTData connected: endpoint=%s:%s source=%s",
          endpoint.host,
          endpoint.port,
          endpoint.source,
        )
        return True
      except Exception as exc:
        self._client = None
        self._connected_endpoint = None
        self.is_connected = False
        endpoint_text = (
          f"{endpoint.host}:{endpoint.port}" if endpoint is not None else "none"
        )
        self._last_connection_error = (
          f"{exc.__class__.__name__}: {exc}"
        )
        logger.warning(
          "XTData connection failed: endpoint=%s error=%s",
          endpoint_text,
          self._last_connection_error,
        )
        return False

  def _require_connection(self) -> None:
    if self.ensure_connected():
      return
    detail = self.last_connection_error or "connection retry is throttled"
    raise XTDataUnavailableError(f"XTData unavailable: {detail}")

  def _mark_operation_failed(self, operation: str, exc: Exception) -> None:
    self._ensure_connection_state()
    self.is_connected = False
    self._last_connection_error = f"{exc.__class__.__name__}: {exc}"
    logger.warning(
      "XTData %s failed: %s",
      operation,
      self._last_connection_error,
    )

  def _call_xtdata(
    self,
    operation: str,
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
  ) -> Any:
    self._require_connection()
    try:
      return function(*args, **kwargs)
    except Exception as exc:
      self._mark_operation_failed(operation, exc)
      raise XTDataUnavailableError(
        f"XTData {operation} failed: {exc.__class__.__name__}: {exc}"
      ) from exc

  def get_market_data(
    self,
    stock_list: str | Iterable[str],
    period: str = "1d",
    start_time: str = "",
    end_time: str = "",
    count: int = -1,
    dividend_type: str = "none",
    field_list: list[str] | None = None,
    fill_data: bool = True,
  ) -> Any:
    """Return one DataFrame per instrument without triggering a download."""
    self._require_connection()
    try:
      result = xtdata.get_market_data_ex(
        field_list=field_list or [],
        stock_list=_codes(stock_list),
        period=period,
        start_time=start_time,
        end_time=end_time,
        count=count,
        dividend_type=dividend_type,
        fill_data=fill_data,
      )
      return result
    except Exception as exc:
      self._mark_operation_failed("market-data query", exc)
      raise XTDataUnavailableError(
        f"XTData market-data query failed: {exc.__class__.__name__}: {exc}"
      ) from exc

  def download_market_data(
    self,
    stock_list: str | Iterable[str],
    period: str,
    start_time: str = "",
    end_time: str = "",
    callback: Callable | None = None,
    incrementally: bool | None = True,
  ) -> Any:
    self._require_connection()
    try:
      return xtdata.download_history_data2(
        _codes(stock_list),
        period,
        start_time=start_time,
        end_time=end_time,
        callback=callback,
        incrementally=incrementally,
      )
    except Exception as exc:
      self._mark_operation_failed("history download", exc)
      raise XTDataUnavailableError(
        f"XTData history download failed: {exc.__class__.__name__}: {exc}"
      ) from exc

  def subscribe_quote(
    self,
    stock_code: str | Iterable[str],
    *,
    period: str = "1d",
    start_time: str = "",
    end_time: str = "",
    count: int = 0,
    callback: Callable | None = None,
  ) -> int | list[int]:
    self._require_connection()
    codes = _codes(stock_code)
    try:
      ids = [
        int(
          xtdata.subscribe_quote2(
            code,
            period=period,
            start_time=start_time,
            end_time=end_time,
            count=count,
            callback=callback,
          )
        )
        for code in codes
      ]
    except Exception as exc:
      self._mark_operation_failed("quote subscription", exc)
      raise XTDataUnavailableError(
        "XTData quote subscription failed: "
        f"{exc.__class__.__name__}: {exc}"
      ) from exc
    self._subscription_ids.update(item for item in ids if item > 0)
    return ids[0] if len(ids) == 1 else ids

  def subscribe_whole_quote(
    self,
    code_list: Iterable[str],
    *,
    callback: Callable | None = None,
  ) -> int:
    self._require_connection()
    try:
      subscription_id = int(
        xtdata.subscribe_whole_quote(_codes(code_list), callback=callback)
      )
    except Exception as exc:
      self._mark_operation_failed("whole-quote subscription", exc)
      raise XTDataUnavailableError(
        "XTData whole-quote subscription failed: "
        f"{exc.__class__.__name__}: {exc}"
      ) from exc
    if subscription_id > 0:
      self._subscription_ids.add(subscription_id)
    return subscription_id

  def unsubscribe_quote(self, subscription_id: int) -> None:
    xtdata.unsubscribe_quote(int(subscription_id))
    self._subscription_ids.discard(int(subscription_id))

  def unsubscribe_whole_quote(self, subscription_id: int) -> None:
    self.unsubscribe_quote(subscription_id)

  def get_full_tick(self, stock_list: str | Iterable[str]) -> dict[str, Any]:
    self._require_connection()
    result = xtdata.get_full_tick(_codes(stock_list))
    return result if isinstance(result, dict) else {}

  def get_current_data(self, stock_list: str | Iterable[str]) -> dict[str, Any]:
    return self.get_full_tick(stock_list)

  def get_trading_dates(
    self,
    market: str,
    start_date: Any = "",
    end_date: Any = "",
    count: int = -1,
  ) -> list[Any]:
    start_time = (
      start_date.strftime("%Y%m%d")
      if hasattr(start_date, "strftime")
      else str(start_date or "")
    )
    end_time = (
      end_date.strftime("%Y%m%d")
      if hasattr(end_date, "strftime")
      else str(end_date or "")
    )
    return list(
      self._call_xtdata(
        "trading-dates query",
        xtdata.get_trading_dates,
        market,
        start_time=start_time,
        end_time=end_time,
        count=count,
      )
      or []
    )

  def get_stock_list_in_sector(self, sector_name: str) -> list[str]:
    return list(
      self._call_xtdata(
        "sector-instruments query",
        xtdata.get_stock_list_in_sector,
        sector_name,
      )
      or []
    )

  def get_stock_list(self, market: str = "") -> pd.DataFrame:
    sector = {
      "SH": "上证A股",
      "SZ": "深证A股",
      "BJ": "北京A股",
      "": "沪深A股",
    }.get(market.upper(), market)
    return pd.DataFrame({"stock_code": self.get_stock_list_in_sector(sector)})

  def get_instrument_detail(
    self,
    stock_code: str,
    iscomplete: bool = False,
  ) -> dict[str, Any] | None:
    value = self._call_xtdata(
      "instrument-detail query",
      xtdata.get_instrument_detail,
      stock_code,
      iscomplete=iscomplete,
    )
    return value if isinstance(value, dict) else None

  def get_instrument_detail_list(
    self,
    stock_list: Iterable[str],
    iscomplete: bool = False,
  ) -> dict[str, Any]:
    value = self._call_xtdata(
      "instrument-details query",
      xtdata.get_instrument_detail_list,
      _codes(stock_list),
      iscomplete=iscomplete,
    )
    return value if isinstance(value, dict) else {}

  def get_divid_factors(
    self,
    stock_code: str,
    start_time: str = "",
    end_time: str = "",
  ) -> Any:
    return self._call_xtdata(
      "dividend-factors query",
      xtdata.get_divid_factors,
      stock_code,
      start_time,
      end_time,
    )

  def get_financial_data(
    self,
    stock_code: str,
    table: str = "Income",
    start_time: str = "",
    end_time: str = "",
  ) -> pd.DataFrame:
    result = self._call_xtdata(
      "financial-data query",
      xtdata.get_financial_data,
      [stock_code],
      table_list=[table] if table else [],
      start_time=start_time,
      end_time=end_time,
    )
    table_data = (result or {}).get(stock_code, {}).get(table)
    return table_data if isinstance(table_data, pd.DataFrame) else pd.DataFrame()

  def get_financial_data_list(
    self,
    stock_list: Iterable[str],
    table_list: list[str] | None = None,
    start_time: str = "",
    end_time: str = "",
    report_type: str = "report_time",
  ) -> dict[str, Any]:
    result = self._call_xtdata(
      "financial-data-list query",
      xtdata.get_financial_data,
      _codes(stock_list),
      table_list=table_list or [],
      start_time=start_time,
      end_time=end_time,
      report_type=report_type,
    )
    return result if isinstance(result, dict) else {}

  def download_financial_data_list(
    self,
    stock_list: Iterable[str],
    table_list: list[str] | None = None,
    start_time: str = "",
    end_time: str = "",
  ) -> None:
    """Synchronously refresh MiniQMT's local financial-data cache."""
    self._call_xtdata(
      "financial-data-list download",
      xtdata.download_financial_data2,
      _codes(stock_list),
      table_list=table_list or [],
      start_time=start_time,
      end_time=end_time,
    )

  def close_connection(self) -> None:
    self._ensure_connection_state()
    for subscription_id in list(self._subscription_ids):
      try:
        xtdata.unsubscribe_quote(subscription_id)
      except Exception:
        logger.debug("XTData unsubscribe failed during close", exc_info=True)
    self._subscription_ids.clear()
    disconnect = getattr(xtdata, "disconnect", None)
    if callable(disconnect):
      try:
        disconnect()
      except Exception:
        logger.debug("XTData disconnect failed", exc_info=True)
    self.is_connected = False
    self._client = None
    self._connected_endpoint = None


class LazyXTDataManager:
  """Avoid connecting to miniQMT during module import."""

  def __init__(self) -> None:
    self._manager: XTDataManager | None = None
    self._lock = threading.RLock()

  def _get_manager(self) -> XTDataManager:
    with self._lock:
      if self._manager is None:
        self._manager = XTDataManager()
      return self._manager

  @property
  def is_connected(self) -> bool:
    return bool(self._manager and self._manager.is_connected)

  def close_connection(self) -> None:
    with self._lock:
      manager = self._manager
      self._manager = None
    if manager is not None:
      manager.close_connection()

  def __getattr__(self, name: str) -> Any:
    return getattr(self._get_manager(), name)


xt_data_manager = LazyXTDataManager()
