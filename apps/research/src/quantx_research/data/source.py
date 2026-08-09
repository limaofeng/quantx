"""只读研究数据源及 QuantX 基础设施适配器。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from .normalization import (
  as_datetime,
  normalize_daily_bars,
  normalize_dividend_factors,
  normalize_instrument_type,
  normalize_instruments,
)

_INFLUX_TIME_CHUNK_DAYS = 180
_FACTOR_BULK_THRESHOLD = 50


@runtime_checkable
class ResearchDataSource(Protocol):
  """研究运行器所需的最小只读数据端口。"""

  async def list_instruments(
    self,
    *,
    instrument_types: Sequence[str] = ("stock",),
    codes: Sequence[str] | None = None,
  ) -> pd.DataFrame: ...

  async def load_daily_bars(
    self,
    stock_codes: Sequence[str],
    start: date | datetime,
    end: date | datetime,
    *,
    batch_size: int = 300,
  ) -> pd.DataFrame: ...

  async def load_dividend_factors(
    self,
    stock_codes: Sequence[str],
    *,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
  ) -> pd.DataFrame: ...

  async def load_dividend_factor_coverage(
    self,
    stock_codes: Sequence[str],
    *,
    start: date | datetime,
    end: date | datetime,
  ) -> pd.DataFrame: ...


class InfrastructureResearchDataSource:
  """复用 QuantX 仓储的只读数据源。

  PostgreSQL 会话在首个查询前执行 ``SET TRANSACTION READ ONLY``，关闭时
  一律回滚。InfluxDB 适配器只暴露 KLineRepository 的查询方法且禁用缓存，
  不向研究应用暴露写入或删除接口。
  """

  def __init__(
    self,
    *,
    session: Any | None = None,
    session_factory: Callable[[], Any] | None = None,
    instrument_repository: Any | None = None,
    dividend_factor_repository: Any | None = None,
    kline_repository: Any | None = None,
    enforce_postgres_read_only: bool = True,
  ) -> None:
    self._session = session
    self._session_factory = session_factory
    self._instrument_repository = instrument_repository
    self._dividend_factor_repository = dividend_factor_repository
    self._kline_repository = kline_repository
    self._owns_session = session is None
    self._read_only_initialized = False
    self._enforce_postgres_read_only = enforce_postgres_read_only

  async def __aenter__(self) -> "InfrastructureResearchDataSource":
    await self._ensure_relational_ready()
    self._get_kline_repository()
    return self

  async def __aexit__(self, *_: object) -> None:
    await self.close()

  async def close(self) -> None:
    """回滚并关闭本适配器拥有的关系型数据库会话。"""
    if self._session is None:
      return
    if self._owns_session:
      try:
        await self._session.rollback()
      finally:
        await self._session.close()
    self._session = None
    self._read_only_initialized = False

  async def list_instruments(
    self,
    *,
    instrument_types: Sequence[str] = ("stock",),
    codes: Sequence[str] | None = None,
  ) -> pd.DataFrame:
    repository = await self._get_instrument_repository()
    normalized_codes = _unique_codes(codes or ())
    if normalized_codes:
      items = await repository.find_by_ids(normalized_codes)
      allowed = {
        normalized
        for value in instrument_types
        if (normalized := normalize_instrument_type(value)) is not None
      }
      items = [
        item
        for item in items
        if normalize_instrument_type(getattr(item, "type", None)) in allowed
      ]
      return normalize_instruments(items)

    enum_type = _instrument_type_enum()
    items: list[Any] = []
    for instrument_type in instrument_types:
      selected_type = _resolve_instrument_type(enum_type, instrument_type)
      items.extend(await repository.find_all_by_type(selected_type))
    return normalize_instruments(items)

  async def load_daily_bars(
    self,
    stock_codes: Sequence[str],
    start: date | datetime,
    end: date | datetime,
    *,
    batch_size: int = 300,
  ) -> pd.DataFrame:
    codes = _unique_codes(stock_codes)
    if not codes:
      return normalize_daily_bars(None)
    if batch_size <= 0:
      raise ValueError("batch_size 必须大于 0")
    start_at = as_datetime(start)
    end_at = as_datetime(end)
    if end_at < start_at:
      raise ValueError("日线查询结束时间不能早于开始时间")

    repository = self._get_kline_repository()
    parts: dict[str, list[pd.DataFrame]] = {}
    for window_start, window_end in _time_windows(
      start_at,
      end_at,
      days=_INFLUX_TIME_CHUNK_DAYS,
    ):
      for batch in _batches(codes, batch_size):
        result = await asyncio.to_thread(
          repository.find_daily_batch,
          list(batch),
          window_start,
          window_end,
          use_cache=False,
        )
        for code, frame in (result or {}).items():
          parts.setdefault(str(code).upper(), []).append(frame)
    frames = {
      code: pd.concat(code_parts, ignore_index=True, sort=False)
      for code, code_parts in parts.items()
    }
    return normalize_daily_bars(frames)

  async def load_dividend_factors(
    self,
    stock_codes: Sequence[str],
    *,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
  ) -> pd.DataFrame:
    repository = await self._get_dividend_factor_repository()
    codes = _unique_codes(stock_codes)
    start_at = as_datetime(start) if start is not None else None
    end_at = as_datetime(end) if end is not None else None
    if start_at and end_at and end_at < start_at:
      raise ValueError("复权因子查询结束时间不能早于开始时间")

    bulk_reader = getattr(repository, "find_all", None)
    if len(codes) >= _FACTOR_BULK_THRESHOLD and callable(bulk_reader):
      all_factors = await bulk_reader(
        start_time=start_at,
        end_time=end_at,
        limit=None,
        order_by="time ASC",
      )
      selected = set(codes)
      factors = [
        factor
        for factor in all_factors
        if str(getattr(factor, "stock_code", "")).strip().upper() in selected
      ]
      return normalize_dividend_factors(factors)

    factors: list[Any] = []
    for code in codes:
      factors.extend(
        await repository.find_by_stock_code(
          stock_code=code,
          start_time=start_at,
          end_time=end_at,
          limit=None,
        )
      )
    return normalize_dividend_factors(factors)

  async def load_dividend_factor_coverage(
    self,
    stock_codes: Sequence[str],
    *,
    start: date | datetime,
    end: date | datetime,
  ) -> pd.DataFrame:
    """Read durable database proof for authoritative sparse-factor windows."""
    codes = set(_unique_codes(stock_codes))
    if not codes:
      return pd.DataFrame()
    start_at = as_datetime(start)
    end_at = as_datetime(end)
    if end_at < start_at:
      raise ValueError("复权因子覆盖结束时间不能早于开始时间")

    session = await self._ensure_relational_ready()
    from quantx_infrastructure.models.agent_runtime import MarketDataRequest
    from sqlalchemy import select

    rows = (
      await session.execute(
        select(
          MarketDataRequest.request_id,
          MarketDataRequest.request_payload,
          MarketDataRequest.status,
          MarketDataRequest.expected_chunks,
          MarketDataRequest.received_chunks,
          MarketDataRequest.completed_at,
        ).where(MarketDataRequest.status == "COMPLETED")
      )
    ).all()
    evidence: list[dict[str, Any]] = []
    for (
      request_id,
      payload,
      status,
      expected_chunks,
      received_chunks,
      completed_at,
    ) in rows:
      if isinstance(payload, str):
        try:
          payload = json.loads(payload)
        except json.JSONDecodeError:
          continue
      if not isinstance(payload, dict):
        continue
      if str(payload.get("operation") or "") != "divid_factors":
        continue
      payload_codes = _unique_codes(payload.get("stock_list") or ())
      if not codes.intersection(payload_codes):
        continue
      evidence.append(
        {
          "request_id": str(request_id),
          "source": str(payload.get("source") or ""),
          "status": str(status),
          "start_date": payload.get("start_time"),
          "end_date": payload.get("end_time"),
          "stock_codes": payload_codes,
          "expected_chunks": expected_chunks,
          "received_chunks": received_chunks,
          "completed_at": completed_at,
        }
      )
    return pd.DataFrame(evidence)

  async def _ensure_relational_ready(self) -> Any:
    if self._read_only_initialized:
      return self._session
    if self._session is None:
      if self._session_factory is None:
        from quantx_infrastructure.database.relational_connection import (
          AsyncSessionLocal,
        )

        self._session_factory = AsyncSessionLocal
      self._session = self._session_factory()

    if self._enforce_postgres_read_only:
      bind = self._session.get_bind()
      dialect = getattr(getattr(bind, "dialect", None), "name", None)
      if dialect != "postgresql":
        raise RuntimeError(
          f"研究数据源只允许 PostgreSQL 关系库；当前 dialect={dialect or 'unknown'}"
        )
      from sqlalchemy import text

      # 此语句既启动事务也保证事务内所有 SQL 都无法写库。
      await self._session.execute(text("SET TRANSACTION READ ONLY"))
    self._read_only_initialized = True
    return self._session

  async def _get_instrument_repository(self) -> Any:
    if self._instrument_repository is None:
      session = await self._ensure_relational_ready()
      from quantx_infrastructure.repositories.instrument_repository import (
        InstrumentRepository,
      )

      self._instrument_repository = InstrumentRepository(session)
    return self._instrument_repository

  async def _get_dividend_factor_repository(self) -> Any:
    if self._dividend_factor_repository is None:
      session = await self._ensure_relational_ready()
      from quantx_infrastructure.repositories.divid_factor_repository import (
        DividFactorRepository,
      )

      self._dividend_factor_repository = DividFactorRepository(session)
    return self._dividend_factor_repository

  def _get_kline_repository(self) -> Any:
    if self._kline_repository is None:
      from quantx_infrastructure.repositories.kline_repository import (
        KLineRepository,
      )

      self._kline_repository = KLineRepository()
    return self._kline_repository


def _instrument_type_enum() -> type[Any]:
  from quantx_infrastructure.models.enums import InstrumentType

  return InstrumentType


def _resolve_instrument_type(enum_type: type[Any], value: Any) -> Any:
  normalized = normalize_instrument_type(value)
  if normalized is None:
    raise ValueError(f"不支持的证券类型: {value}")
  try:
    return enum_type[normalized.upper()]
  except KeyError:
    for member in enum_type:
      if normalize_instrument_type(member) == normalized:
        return member
  raise ValueError(f"不支持的证券类型: {value}")


def _unique_codes(codes: Iterable[str]) -> list[str]:
  return list(
    dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip())
  )


def _batches(values: Sequence[str], batch_size: int) -> Iterable[Sequence[str]]:
  for offset in range(0, len(values), batch_size):
    yield values[offset : offset + batch_size]


def _time_windows(
  start: datetime,
  end: datetime,
  *,
  days: int,
) -> Iterable[tuple[datetime, datetime]]:
  """生成无重叠的闭区间，规避 InfluxDB Core 单查询文件扫描上限。"""
  cursor = start
  window = timedelta(days=days)
  while cursor <= end:
    window_end = min(cursor + window, end)
    yield cursor, window_end
    cursor = window_end + timedelta(microseconds=1)
