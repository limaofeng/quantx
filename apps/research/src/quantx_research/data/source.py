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
_MAX_FACTOR_EVIDENCE_REQUESTS = 4_096
_RESEARCH_IDLE_TRANSACTION_TIMEOUT = "15min"


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

  PostgreSQL 会话在首个查询前进入只读 ``REPEATABLE READ``，关闭时一律
  回滚。InfluxDB 适配器只暴露 KLineRepository 的查询方法且禁用缓存，不向
  研究应用暴露写入或删除接口。
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
    try:
      await self._ensure_relational_ready()
      self._get_kline_repository()
      return self
    except BaseException:
      await self.close()
      raise

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

  async def latest_daily_date(self, benchmark_code: str) -> date:
    """Resolve latest from persisted benchmark bars, never from wall-clock time."""
    repository = self._get_kline_repository()
    rows = await asyncio.to_thread(
      repository.find_latest_by_stock_code_and_period,
      benchmark_code,
      "1d",
      1,
    )
    if not rows:
      raise ValueError("缺少已持久化基准日线，无法解析 latest 研究截止日")
    timestamp = pd.Timestamp(rows[0].time)
    if timestamp.tzinfo is not None:
      timestamp = timestamp.tz_convert("Asia/Shanghai")
    return timestamp.date()

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
    """Read schema-v2 evidence and verify it against current factor rows."""
    codes = set(_unique_codes(stock_codes))
    if not codes:
      return pd.DataFrame()
    start_at = as_datetime(start)
    end_at = as_datetime(end)
    if end_at < start_at:
      raise ValueError("复权因子覆盖结束时间不能早于开始时间")

    session = await self._ensure_relational_ready()
    from quantx_infrastructure.models.agent_runtime import MarketDataRequest
    from quantx_infrastructure.models.divid_factor import DividFactorTable
    from quantx_infrastructure.repositories.divid_factor_repository import (
      DIVID_FACTOR_WRITE_LOCK_KEY,
      divid_factor_rows_sha256,
    )
    from quantx_infrastructure.services.divid_factor_evidence import (
      current_rows_match_evidence,
      parse_divid_factor_evidence,
    )
    from sqlalchemy import String, bindparam, cast, func, select
    from sqlalchemy.dialects.postgresql import ARRAY, JSONB

    # The shared transaction lock prevents an authoritative replacement from
    # moving between request-audit and current-row reads. REPEATABLE READ also
    # pins the broader research session to one relational snapshot.
    await session.execute(
      select(func.pg_advisory_xact_lock_shared(DIVID_FACTOR_WRITE_LOCK_KEY))
    )
    evidence_codes_parameter = bindparam(
      "research_factor_evidence_codes",
      value=sorted(codes),
      type_=ARRAY(String()),
    )

    rows = (
      await session.execute(
        select(
          MarketDataRequest.request_id,
          MarketDataRequest.request_payload,
          MarketDataRequest.status,
          MarketDataRequest.expected_chunks,
          MarketDataRequest.received_chunks,
          MarketDataRequest.completed_at,
          MarketDataRequest.ingestion_result,
        )
        .where(
          MarketDataRequest.status == "COMPLETED",
          MarketDataRequest.request_payload["operation"].as_string() == "divid_factors",
          MarketDataRequest.request_payload["source"].as_string()
          == "qmt-get-divid-factors-v1",
          MarketDataRequest.request_payload["end_time"].as_string()
          >= start_at.strftime("%Y%m%d"),
          MarketDataRequest.request_payload["start_time"].as_string()
          <= end_at.strftime("%Y%m%d"),
          cast(MarketDataRequest.request_payload["stock_list"], JSONB).op("?|")(
            evidence_codes_parameter
          ),
        )
        .order_by(
          MarketDataRequest.completed_at.desc(),
          MarketDataRequest.request_id.desc(),
        )
        .limit(_MAX_FACTOR_EVIDENCE_REQUESTS + 1)
      )
    ).all()
    if len(rows) > _MAX_FACTOR_EVIDENCE_REQUESTS:
      raise RuntimeError("复权因子覆盖请求超过安全扫描预算")

    parsed_evidence: list[tuple[Any, Any, Any]] = []
    invalid_evidence: list[dict[str, Any]] = []
    for (
      request_id,
      payload,
      status,
      expected_chunks,
      received_chunks,
      completed_at,
      ingestion_result,
    ) in rows:
      items = parse_divid_factor_evidence(
        request_id=request_id,
        request_payload=payload,
        status=status,
        expected_chunks=expected_chunks,
        received_chunks=received_chunks,
        completed_at=completed_at,
        ingestion_result=ingestion_result,
      )
      if items is not None:
        parsed_evidence.extend(
          (item, expected_chunks, received_chunks)
          for item in items
          if item.stock_code in codes
          and item.end_date >= start_at.date()
          and item.start_date <= end_at.date()
        )
        continue
      payload_object = _json_object(payload)
      ingestion_object = _json_object(ingestion_result)
      audit = (
        ingestion_object.get("replacement_audit")
        if isinstance(ingestion_object, dict)
        else None
      )
      payload_codes = _unique_codes(
        payload_object.get("stock_list") or ()
        if isinstance(payload_object, dict)
        else ()
      )
      invalid_evidence.append(
        {
          "request_id": str(request_id),
          "source": str(
            payload_object.get("source") or ""
            if isinstance(payload_object, dict)
            else ""
          ),
          "status": str(status),
          "start_date": (
            payload_object.get("start_time")
            if isinstance(payload_object, dict)
            else None
          ),
          "end_date": (
            payload_object.get("end_time") if isinstance(payload_object, dict) else None
          ),
          "stock_codes": sorted(codes.intersection(payload_codes)),
          "expected_chunks": expected_chunks,
          "received_chunks": received_chunks,
          "completed_at": completed_at,
          "audit_schema_version": (
            audit.get("audit_schema_version") if isinstance(audit, dict) else None
          ),
          "record_count": None,
          "content_sha256": "",
          "current_record_count": None,
          "current_content_sha256": "",
          "current_matches": False,
        }
      )

    if not parsed_evidence:
      return pd.DataFrame(invalid_evidence, dtype=object)
    evidence_codes = sorted({item.stock_code for item, _, _ in parsed_evidence})
    evidence_start = min(item.start_date for item, _, _ in parsed_evidence)
    evidence_end = max(item.end_date for item, _, _ in parsed_evidence)
    factor_rows = (
      await session.execute(
        select(
          DividFactorTable.stock_code,
          DividFactorTable.time,
          DividFactorTable.ex_date,
          DividFactorTable.interest,
          DividFactorTable.stock_bonus,
          DividFactorTable.stock_gift,
          DividFactorTable.allot_num,
          DividFactorTable.allot_price,
          DividFactorTable.gugai,
          DividFactorTable.dr,
        )
        .where(
          DividFactorTable.stock_code.in_(evidence_codes),
          DividFactorTable.ex_date >= evidence_start.strftime("%Y%m%d"),
          DividFactorTable.ex_date <= evidence_end.strftime("%Y%m%d"),
        )
        .order_by(
          DividFactorTable.stock_code.asc(),
          DividFactorTable.ex_date.asc(),
          DividFactorTable.time.asc(),
        )
      )
    ).all()
    factor_rows = [tuple(row) for row in factor_rows]
    evidence: list[dict[str, Any]] = []
    for item, expected_chunks, received_chunks in parsed_evidence:
      current_rows = [
        row
        for row in factor_rows
        if str(row[0]).strip().upper() == item.stock_code
        and item.start_date
        <= datetime.strptime(str(row[2]), "%Y%m%d").date()
        <= item.end_date
      ]
      current_digest = divid_factor_rows_sha256(current_rows)
      evidence.append(
        {
          "request_id": item.request_id,
          "source": "qmt-get-divid-factors-v1",
          "status": "COMPLETED",
          "start_date": item.start_date.strftime("%Y%m%d"),
          "end_date": item.end_date.strftime("%Y%m%d"),
          "stock_codes": [item.stock_code],
          "expected_chunks": expected_chunks,
          "received_chunks": received_chunks,
          "completed_at": item.completed_at,
          "audit_schema_version": 2,
          "record_count": item.record_count,
          "content_sha256": item.content_sha256,
          "current_record_count": len(current_rows),
          "current_content_sha256": current_digest,
          "current_matches": current_rows_match_evidence(item, factor_rows),
        }
      )
    # Preserve exact integer schema/count types when legacy invalid rows add
    # nulls to the same columns; pandas float coercion must not turn schema 2
    # into 2.0 and make otherwise valid evidence fail closed accidentally.
    return pd.DataFrame([*evidence, *invalid_evidence], dtype=object)

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

      # 此语句必须是会话首个 SQL：既固定关系库快照，也禁止研究写库。
      await self._session.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
      )
      # 因子证据核验和 archive 特征构造会在同一冻结快照内进行较长时间的
      # CPU/文件处理。服务进程的 30 秒 idle-in-transaction 保护不适用于这个
      # 有明确生命周期的离线只读事务；只在当前事务提升到有界上限，退出
      # source 时仍会统一 rollback/close，不改变连接池或在线服务的全局保护。
      await self._session.execute(
        text(
          "SET LOCAL idle_in_transaction_session_timeout = "
          f"'{_RESEARCH_IDLE_TRANSACTION_TIMEOUT}'"
        )
      )
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


def _json_object(value: Any) -> dict[str, Any] | None:
  if isinstance(value, str):
    try:
      value = json.loads(value)
    except (TypeError, ValueError):
      return None
  return value if isinstance(value, dict) else None


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
