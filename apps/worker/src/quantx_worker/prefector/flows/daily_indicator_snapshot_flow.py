"""收盘后日级技术指标快照 Flow。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from typing import Any, Iterable, Optional

from prefect import flow, get_run_logger
from prefect.runtime import flow_run as flow_run_runtime
from quantx_domain.factors import FACTOR_VERSION
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.database.relational_connection import (
  engine as relational_engine,
)
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.sector import Sector
from quantx_infrastructure.models.sector_stock import SectorStock
from quantx_infrastructure.repositories.daily_signal_run_repository import (
  DailySignalRunRepository,
)
from quantx_infrastructure.repositories.divid_factor_repository import (
  DIVID_FACTOR_WRITE_LOCK_KEY,
)
from quantx_infrastructure.services.daily_indicator_snapshot_service import (
  DailyIndicatorSnapshotService,
)
from quantx_infrastructure.services.snapshot_fencing import (
  SNAPSHOT_SESSION_LOCK_NAMESPACE,
  SnapshotFenceLost,
  acquire_snapshot_fences,
  acquire_snapshot_publish_guard,
  assert_snapshot_publish_owner,
  assert_snapshot_run_owner,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper
from sqlalchemy import or_, select, text
from sqlalchemy.exc import SQLAlchemyError

DEFAULT_SNAPSHOT_SECTORS = ["沪深A股", "沪深ETF"]
SNAPSHOT_CUTOFF = time(15, 35)
MAX_BACKFILL_DAYS = 30
SNAPSHOT_LOCK_NAMESPACE = SNAPSHOT_SESSION_LOCK_NAMESPACE

_MARKET_SECTOR_TYPES = {
  "沪深A股": InstrumentType.STOCK,
  "沪深ETF": InstrumentType.ETF,
  "沪深指数": InstrumentType.INDEX,
}


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
  for index in range(0, len(values), size):
    yield values[index : index + size]


def _parse_date(value: str) -> date:
  normalized = str(value or "").strip().replace("-", "")
  if len(normalized) < 8:
    raise ValueError(f"日期格式无效: {value}")
  return datetime.strptime(normalized[:8], "%Y%m%d").date()


def _scheduled_start_time() -> Optional[datetime]:
  try:
    return flow_run_runtime.get_scheduled_start_time()
  except Exception:
    return None


async def expected_snapshot_date(
  reference: datetime,
  *,
  trading_dates: Optional[TradingDateHelper] = None,
  cutoff: time = SNAPSHOT_CUTOFF,
) -> date:
  """根据上海时间和收盘计算时间返回最近应有快照交易日。"""
  helper = trading_dates or TradingDateHelper()
  shanghai_reference = time_utils.to_shanghai(reference)
  current = shanghai_reference.date()
  if (
    await helper.is_trading_date("SH", current) and shanghai_reference.time() >= cutoff
  ):
    return current
  return await helper.trading_time_service.get_previous_trading_day("SH", current)


async def resolve_snapshot_dates(
  start_time: str,
  end_time: str,
  *,
  reference: Optional[datetime] = None,
  trading_dates: Optional[TradingDateHelper] = None,
) -> list[date]:
  """显式日期优先，否则使用计划时间或最近已收盘交易日。"""
  helper = trading_dates or TradingDateHelper()
  start_text = str(start_time or "").strip()
  end_text = str(end_time or "").strip()
  if start_text or end_text:
    start_date = _parse_date(start_text or end_text)
    end_date = _parse_date(end_text or start_text)
    if end_date < start_date:
      raise ValueError("指标补算结束日期不能早于开始日期")
    if (end_date - start_date).days + 1 > MAX_BACKFILL_DAYS:
      raise ValueError(f"指标补算日期范围最多 {MAX_BACKFILL_DAYS} 天")
    return await helper.get_trading_calendar(
      "SH",
      start_date=start_date,
      end_date=end_date,
    )

  scheduled = _scheduled_start_time()
  target_reference = reference or scheduled or time_utils.now()
  return [
    await expected_snapshot_date(
      target_reference,
      trading_dates=helper,
    )
  ]


async def resolve_instruments(
  sectors: Optional[list[str]],
  stock_list: Optional[list[str]],
  *,
  allowed_types: Optional[set[InstrumentType]] = None,
  active_on: Optional[date] = None,
) -> list[dict[str, Any]]:
  """从 PostgreSQL 解析范围，并按目标日排除明确未上市或已退市标的。"""
  requested_types = allowed_types or {
    InstrumentType.STOCK,
    InstrumentType.ETF,
    InstrumentType.INDEX,
  }
  async with AsyncSessionLocal() as db:
    stmt = select(
      Instrument.id,
      Instrument.name,
      Instrument.type,
      Instrument.float_volume,
      Instrument.open_date,
      Instrument.expire_date,
    )
    if stock_list:
      codes = list(
        dict.fromkeys(
          str(code or "").strip().upper()
          for code in stock_list
          if str(code or "").strip()
        )
      )
      stmt = stmt.where(
        Instrument.id.in_(codes),
        Instrument.type.in_(requested_types),
      )
    else:
      requested_sectors = list(
        dict.fromkeys(str(item or "").strip() for item in (sectors or []))
      )
      scope_conditions = []
      market_types = {
        _MARKET_SECTOR_TYPES[name]
        for name in requested_sectors
        if name in _MARKET_SECTOR_TYPES
      } & requested_types
      if market_types:
        scope_conditions.append(Instrument.type.in_(market_types))

      relation_sectors = [
        name for name in requested_sectors if name not in _MARKET_SECTOR_TYPES
      ]
      if relation_sectors:
        related_codes = (
          select(SectorStock.stock_code)
          .join(Sector, SectorStock.sector_id == Sector.id)
          .where(
            or_(
              Sector.name.in_(relation_sectors),
              Sector.code.in_(relation_sectors),
            )
          )
        )
        scope_conditions.append(Instrument.id.in_(related_codes))

      stmt = stmt.where(Instrument.type.in_(requested_types))
      if scope_conditions:
        stmt = stmt.where(or_(*scope_conditions))

    if active_on is not None:
      stmt = stmt.where(
        or_(Instrument.open_date.is_(None), Instrument.open_date <= active_on),
        or_(Instrument.expire_date.is_(None), Instrument.expire_date >= active_on),
      )

    rows = (await db.execute(stmt.order_by(Instrument.id.asc()))).all()
  return [
    {
      "code": code,
      "name": name or "",
      "instrument_type": instrument_type.name.lower(),
      "float_volume": float(float_volume) if float_volume else None,
      "open_date": open_date,
      "expire_date": expire_date,
    }
    for code, name, instrument_type, float_volume, open_date, expire_date in rows
    if instrument_type in requested_types
  ]


class SnapshotLockLost(RuntimeError):
  """The dedicated PostgreSQL session no longer owns every date lock."""


class SnapshotDatabaseLocks:
  def __init__(self, snapshot_dates: list[date]) -> None:
    self.snapshot_dates = tuple(sorted(set(snapshot_dates)))
    self.connection = None
    self.backend_pid: int | None = None

  async def acquire(self) -> None:
    self.connection = await relational_engine.connect()
    try:
      self.backend_pid = int(
        await self.connection.scalar(text("SELECT pg_backend_pid()"))
      )
      for target in self.snapshot_dates:
        acquired = await self.connection.scalar(
          text("SELECT pg_try_advisory_lock(:namespace, :date_key)"),
          {
            "namespace": SNAPSHOT_LOCK_NAMESPACE,
            "date_key": target.toordinal(),
          },
        )
        if not acquired:
          raise RuntimeError(
            f"{target.isoformat()} 已有快照任务运行中，请等待当前任务完成"
          )
      # Keep the factor table generation stable from evidence verification
      # through snapshot certification. Authoritative writers take the
      # exclusive transaction variant of this same bigint key.
      await self.connection.scalar(
        text("SELECT pg_advisory_lock_shared(:lock_key)"),
        {"lock_key": DIVID_FACTOR_WRITE_LOCK_KEY},
      )
      await self.assert_held()
    except BaseException:
      await self.release()
      raise

  async def assert_held(self) -> None:
    connection = self.connection
    if connection is None or connection.closed or self.backend_pid is None:
      raise SnapshotLockLost("日级快照数据库锁连接已关闭")
    try:
      row = (
        await connection.execute(
          text(
            "SELECT pg_backend_pid(), "
            "(SELECT count(*) FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
            "AND granted AND classid = CAST(:namespace AS oid) "
            "AND objsubid = 2), "
            "(SELECT count(*) FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
            "AND granted AND mode = 'ShareLock' "
            "AND classid::bigint = "
            "((CAST(:factor_key AS bigint) >> 32) & 4294967295) "
            "AND objid::bigint = "
            "(CAST(:factor_key AS bigint) & 4294967295) "
            "AND objsubid = 1)"
          ),
          {
            "namespace": SNAPSHOT_LOCK_NAMESPACE,
            "factor_key": DIVID_FACTOR_WRITE_LOCK_KEY,
          },
        )
      ).one()
    except SQLAlchemyError as exc:
      raise SnapshotLockLost("日级快照数据库锁连接已失效") from exc
    current_pid, lock_count, factor_lock_count = map(int, row)
    if (
      current_pid != self.backend_pid
      or lock_count != len(self.snapshot_dates)
      or factor_lock_count != 1
    ):
      raise SnapshotLockLost("日级快照数据库锁所有权已丢失")

  async def release(self) -> None:
    connection = self.connection
    self.connection = None
    self.backend_pid = None
    if connection is None:
      return
    try:
      if not connection.closed:
        await connection.scalar(text("SELECT pg_advisory_unlock_all()"))
    except BaseException:
      try:
        await connection.invalidate()
      except BaseException:
        pass
    finally:
      try:
        await connection.close()
      except BaseException:
        pass


async def _acquire_snapshot_locks(snapshot_dates: list[date]) -> SnapshotDatabaseLocks:
  locks = SnapshotDatabaseLocks(snapshot_dates)
  await locks.acquire()
  return locks


async def _assert_snapshot_locks(locks: Any) -> None:
  # Unit tests may substitute a sentinel; production always supplies the
  # database-backed owner above.
  if isinstance(locks, SnapshotDatabaseLocks):
    await locks.assert_held()


def _snapshot_lock_backend_pid(locks: Any) -> int:
  if isinstance(locks, SnapshotDatabaseLocks) and locks.backend_pid is not None:
    return int(locks.backend_pid)
  return 0


async def _release_snapshot_locks(locks: Any) -> None:
  if isinstance(locks, SnapshotDatabaseLocks):
    await locks.release()


async def _release_snapshot_locks_safely(locks: Any) -> None:
  cleanup = asyncio.create_task(_release_snapshot_locks(locks))
  try:
    await asyncio.shield(cleanup)
  except asyncio.CancelledError:
    await cleanup
    raise


def _requests_full_snapshot_scope(
  sectors: Optional[list[str]],
  stock_list: Optional[list[str]],
) -> bool:
  """Only the exact default market request may certify whole-day readiness."""
  if any(str(code or "").strip() for code in (stock_list or [])):
    return False
  requested = {
    str(item or "").strip() for item in (sectors or DEFAULT_SNAPSHOT_SECTORS)
  }
  return requested == set(DEFAULT_SNAPSHOT_SECTORS)


def _instrument_is_active_on(instrument: dict[str, Any], target: date) -> bool:
  open_date = instrument.get("open_date")
  expire_date = instrument.get("expire_date")
  return (open_date is None or open_date <= target) and (
    expire_date is None or expire_date >= target
  )


async def _freeze_snapshot_scope(
  *,
  sectors: Optional[list[str]],
  stock_list: Optional[list[str]],
  target_dates: list[date],
) -> dict[str, Any]:
  """Resolve one immutable instrument generation while date locks are held."""

  instruments = await resolve_instruments(
    sectors or DEFAULT_SNAPSHOT_SECTORS,
    stock_list,
    allowed_types={InstrumentType.STOCK, InstrumentType.ETF},
  )
  if not instruments:
    raise RuntimeError("未找到匹配的股票或 ETF 请求范围")
  instruments_by_date = {
    target: [
      instrument
      for instrument in instruments
      if _instrument_is_active_on(instrument, target)
    ]
    for target in target_dates
  }
  target_codes = {
    target: [item["code"] for item in instruments_by_date[target]]
    for target in target_dates
  }
  instruments_by_code = {item["code"]: item for item in instruments}
  codes = sorted(instruments_by_code)
  ordered_instruments = [instruments_by_code[code] for code in codes]
  requested_full_scope = _requests_full_snapshot_scope(sectors, stock_list)
  return {
    "codes": codes,
    "target_codes": target_codes,
    "skipped_dates": [
      target for target in target_dates if not instruments_by_date[target]
    ],
    "full_scope": {
      target: requested_full_scope and bool(target_codes[target])
      for target in target_dates
    },
    "name_map": {item["code"]: item["name"] for item in ordered_instruments},
    "instrument_type_map": {
      item["code"]: item["instrument_type"] for item in ordered_instruments
    },
    "float_volume_map": {
      item["code"]: item["float_volume"]
      for item in ordered_instruments
      if item["float_volume"] is not None
    },
  }


def _run_status(
  saved: int,
  failed: int,
  *,
  full_scope: bool,
  missing_target: int,
  has_errors: bool = False,
) -> str:
  if saved <= 0:
    return "failed"
  if failed > 0 or missing_target > 0 or has_errors:
    return "partial_failure"
  return "success" if full_scope else "scoped_success"


def _run_warnings(result: dict[str, Any], errors: list[str]) -> str:
  warnings = []
  if result["saved"] <= 0:
    warnings.append("未保存任何日级因子快照")
  if result["missing_target"]:
    warnings.append(f"{result['missing_target']} 只标的目标日无行情")
  if result["inactive_target"]:
    warnings.append(f"{result['inactive_target']} 只标的目标日停牌或无成交，已跳过")
  if result["insufficient_history"]:
    warnings.append(f"{result['insufficient_history']} 只新股或历史数据不足")
  if result["failed"]:
    warnings.append(f"{result['failed']} 只标的计算或写入失败")
  warnings.extend(errors[:3])
  return "; ".join(warnings)


def _result_conservation_error(result: dict[str, Any], total_codes: int) -> str:
  processed = result["saved"] + result["skipped"] + result["failed"]
  if processed == total_codes:
    return ""
  return f"快照结果计数不守恒: total={total_codes} processed={processed}"


def _merge_batch_date_result(
  aggregate: dict[str, Any],
  batch_result: dict[str, Any],
  target: date,
) -> None:
  """Merge only counters and errors belonging to one snapshot date."""
  target_result = batch_result["dates"][target.isoformat()]
  for key in (
    "saved",
    "skipped",
    "failed",
    "missing_target",
    "inactive_target",
    "insufficient_history",
  ):
    aggregate[key] += int(target_result[key])
  aggregate["errors"].extend(target_result.get("errors", []))


async def _create_signal_runs(
  snapshot_dates: list[date],
  total_codes: dict[date, int],
  locks: Any,
) -> dict[date, int]:
  run_ids: dict[date, int] = {}
  for target in snapshot_dates:
    await _assert_snapshot_locks(locks)
    async with AsyncSessionLocal() as db:
      await acquire_snapshot_fences(db, [target])
      run = await DailySignalRunRepository(db).create_run(
        {
          "snapshot_date": target,
          "signal_version": FACTOR_VERSION,
          "score_version": "score-v1",
          "status": "running",
          "started_at": time_utils.now(),
          "total_codes": total_codes[target],
        }
      )
      run_ids[target] = run.id
    await _assert_snapshot_locks(locks)
  return run_ids


async def _finish_signal_run(
  run_id: int,
  *,
  snapshot_date: date,
  locked_snapshot_dates: list[date],
  lock_backend_pid: int,
  started_at: datetime,
  status: str,
  total_codes: int,
  result: dict[str, Any],
  warnings: str,
) -> None:
  completed_at = time_utils.now()
  async with AsyncSessionLocal() as db:
    # The transaction-level shared lock closes the check/update race: if the
    # session owner vanished, an exclusive factor replacement either completes
    # first (and this check fails) or remains blocked through this commit.
    await acquire_snapshot_publish_guard(
      db,
      lock_backend_pid=lock_backend_pid,
      snapshot_dates=locked_snapshot_dates,
    )
    await assert_snapshot_run_owner(db, {snapshot_date: run_id})
    await assert_snapshot_publish_owner(
      db,
      lock_backend_pid=lock_backend_pid,
      snapshot_dates=locked_snapshot_dates,
    )
    await DailySignalRunRepository(db).update_run(
      run_id,
      {
        "status": status,
        "completed_at": completed_at,
        "total_codes": total_codes,
        "saved": result["saved"],
        "skipped": result["skipped"],
        "failed": result["failed"],
        "elapsed_seconds": (completed_at - started_at).total_seconds(),
        "warnings": warnings or None,
      },
    )


async def _abandon_signal_runs(
  run_ids: dict[date, int],
  *,
  started_at: datetime,
  reason: str,
) -> None:
  """Fail only this flow's run rows after cancellation or ownership loss."""

  completed_at = time_utils.now()
  for run_id in run_ids.values():
    try:
      async with AsyncSessionLocal() as db:
        await DailySignalRunRepository(db).update_run(
          run_id,
          {
            "status": "failed",
            "completed_at": completed_at,
            "elapsed_seconds": (completed_at - started_at).total_seconds(),
            "warnings": reason[:2000],
          },
        )
    except Exception:
      # This path never certifies readiness. Failure to annotate an abandoned
      # generation must not hide the original cancellation/fence loss.
      continue


@flow(
  name="每日技术指标快照",
  description="从已入库 1d K 线计算股票与 ETF 日级选股快照",
  retries=0,
)
async def daily_indicator_snapshot_flow(
  sectors: Optional[list[str]] = None,
  stock_list: Optional[list[str]] = None,
  start_time: str = "",
  end_time: str = "",
  batch_size: int = 300,
  retain_days: int = 30,
) -> dict[str, Any]:
  logger = get_run_logger()
  if batch_size <= 0:
    raise ValueError("batch_size 必须大于 0")
  if retain_days <= 0:
    raise ValueError("retain_days 必须大于 0")

  target_dates = await resolve_snapshot_dates(start_time, end_time)
  if not target_dates:
    raise ValueError("指定范围内没有交易日")

  # Acquire every target date before reading Instrument. A later flow cannot
  # certify a newer universe and then be overwritten by an older frozen read.
  locks = await _acquire_snapshot_locks(target_dates)
  try:
    scope = await _freeze_snapshot_scope(
      sectors=sectors,
      stock_list=stock_list,
      target_dates=target_dates,
    )
  except BaseException:
    await _release_snapshot_locks_safely(locks)
    raise

  codes = scope["codes"]
  target_codes = scope["target_codes"]
  full_scope = scope["full_scope"]
  name_map = scope["name_map"]
  instrument_type_map = scope["instrument_type_map"]
  float_volume_map = scope["float_volume_map"]
  if scope["skipped_dates"]:
    logger.info(
      "快照日期没有生命周期内的目标标的，仅失效旧行: %s",
      [target.isoformat() for target in scope["skipped_dates"]],
    )
  logger.info(
    "开始日级快照: target_dates=%s total_codes=%s batch_size=%s",
    [item.isoformat() for item in target_dates],
    len(codes),
    batch_size,
  )

  started_at = time_utils.now()
  run_ids: dict[date, int] = {}
  date_results = {
    target: {
      "saved": 0,
      "skipped": 0,
      "failed": 0,
      "missing_target": 0,
      "inactive_target": 0,
      "insufficient_history": 0,
      "errors": [],
    }
    for target in target_dates
  }
  try:
    total_codes = {target: len(target_codes[target]) for target in target_dates}
    run_ids = await _create_signal_runs(target_dates, total_codes, locks)
    service = DailyIndicatorSnapshotService()
    for batch_index, batch in enumerate(_chunks(codes, batch_size), start=1):
      await _assert_snapshot_locks(locks)
      batch_set = set(batch)
      batch_result = await service.compute_and_save_dates_batch(
        codes=batch,
        snapshot_dates=target_dates,
        instrument_type_map=instrument_type_map,
        name_map=name_map,
        float_volume_map=float_volume_map,
        codes_by_snapshot_date={
          target: [code for code in target_codes[target] if code in batch_set]
          for target in target_dates
        },
        snapshot_run_ids=run_ids,
        lock_backend_pid=_snapshot_lock_backend_pid(locks),
      )
      await _assert_snapshot_locks(locks)
      logger.info(
        "指标批次 %s 完成: codes=%s saved=%s skipped=%s failed=%s",
        batch_index,
        len(batch),
        batch_result["saved"],
        batch_result["skipped"],
        batch_result["failed"],
      )
      for target in target_dates:
        _merge_batch_date_result(date_results[target], batch_result, target)

    reports = []
    for target in target_dates:
      await _assert_snapshot_locks(locks)
      target_result = date_results[target]
      conservation_error = _result_conservation_error(
        target_result, total_codes[target]
      )
      if conservation_error:
        target_result["errors"].append(conservation_error)
      status = _run_status(
        target_result["saved"],
        target_result["failed"],
        full_scope=full_scope[target],
        missing_target=target_result["missing_target"],
        has_errors=bool(target_result["errors"]),
      )
      warnings = _run_warnings(target_result, target_result["errors"])
      if status == "scoped_success":
        warnings = "; ".join(
          filter(
            None,
            [
              "仅完成指定标的，不代表全市场因子快照就绪",
              warnings,
            ],
          )
        )
      await _finish_signal_run(
        run_ids[target],
        snapshot_date=target,
        locked_snapshot_dates=target_dates,
        lock_backend_pid=_snapshot_lock_backend_pid(locks),
        started_at=started_at,
        status=status,
        total_codes=total_codes[target],
        result=target_result,
        warnings=warnings,
      )
      await _assert_snapshot_locks(locks)
      report = {
        "snapshot_date": target.isoformat(),
        "status": status,
        "total_codes": total_codes[target],
        **{
          key: target_result[key]
          for key in (
            "saved",
            "skipped",
            "failed",
            "missing_target",
            "inactive_target",
            "insufficient_history",
          )
        },
        "warnings": warnings,
      }
      reports.append(report)
      logger.info(
        "快照日期完成: date=%s status=%s saved=%s skipped=%s failed=%s",
        target.isoformat(),
        status,
        target_result["saved"],
        target_result["skipped"],
        target_result["failed"],
      )

    # Retention is deliberately outside the computation path. Date-scoped
    # locks cannot protect a broad delete from removing a disjoint concurrent
    # run, so a separately coordinated maintenance job must own that cleanup.
    deleted_snapshots = 0
    deleted_runs = 0
    return {
      "status": (
        "success"
        if all(item["status"] in {"success", "scoped_success"} for item in reports)
        else "failed"
      ),
      "dates": reports,
      "deleted_old_snapshots": deleted_snapshots,
      "deleted_old_runs": deleted_runs,
    }
  except asyncio.CancelledError:
    await asyncio.shield(
      _abandon_signal_runs(
        run_ids,
        started_at=started_at,
        reason="日级快照任务被取消",
      )
    )
    raise
  except (SnapshotLockLost, SnapshotFenceLost) as exc:
    logger.exception("日级快照数据库锁或运行代次所有权丢失，停止写入")
    await _abandon_signal_runs(
      run_ids,
      started_at=started_at,
      reason=f"日级快照所有权丢失: {exc}",
    )
    raise
  except Exception as exc:
    logger.exception("日级快照 Flow 失败: %s", exc)
    for target, run_id in run_ids.items():
      target_result = date_results[target]
      if target_result["saved"] <= 0 and target_result["failed"] <= 0:
        target_result["failed"] = len(target_codes[target])
      try:
        await _finish_signal_run(
          run_id,
          snapshot_date=target,
          locked_snapshot_dates=target_dates,
          lock_backend_pid=_snapshot_lock_backend_pid(locks),
          started_at=started_at,
          status="failed",
          total_codes=len(target_codes[target]),
          result=target_result,
          warnings=f"系统性失败: {exc}",
        )
      except Exception:
        logger.exception("更新失败运行日志失败: %s", target.isoformat())
    raise
  finally:
    await _release_snapshot_locks_safely(locks)
