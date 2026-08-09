"""收盘后日级技术指标快照 Flow。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Optional

from prefect import flow, get_run_logger
from prefect.runtime import flow_run as flow_run_runtime
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.sector import Sector
from quantx_infrastructure.models.sector_stock import SectorStock
from quantx_infrastructure.repositories.daily_signal_run_repository import (
  DailySignalRunRepository,
)
from quantx_infrastructure.services.daily_indicator_snapshot_service import (
  DailyIndicatorSnapshotService,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper
from sqlalchemy import or_, select

DEFAULT_SNAPSHOT_SECTORS = ["沪深A股", "沪深ETF"]
SNAPSHOT_CUTOFF = time(15, 35)
SNAPSHOT_LOCK_TTL_SECONDS = 4 * 60 * 60
MAX_BACKFILL_DAYS = 30

_MARKET_SECTOR_TYPES = {
  "沪深A股": InstrumentType.STOCK,
  "沪深ETF": InstrumentType.ETF,
  "沪深指数": InstrumentType.INDEX,
}
_LOCK_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""


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
    await helper.is_trading_date("SH", current)
    and shanghai_reference.time() >= cutoff
  ):
    return current
  return await helper.trading_time_service.get_previous_trading_day(
    "SH", current
  )


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
) -> list[dict[str, Any]]:
  """从 PostgreSQL instruments / sector_stocks 解析实际标的。"""
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

    rows = (await db.execute(stmt.order_by(Instrument.id.asc()))).all()
  return [
    {
      "code": code,
      "name": name or "",
      "instrument_type": instrument_type.name.lower(),
      "float_volume": float(float_volume) if float_volume else None,
    }
    for code, name, instrument_type, float_volume in rows
    if instrument_type in requested_types
  ]


async def _acquire_snapshot_locks(
  snapshot_dates: list[date],
) -> dict[date, tuple[str, str]]:
  redis = await redis_pubsub.get_redis()
  acquired: dict[date, tuple[str, str]] = {}
  try:
    for target in snapshot_dates:
      key = f"daily-indicator-snapshot:{target.isoformat()}"
      token = uuid.uuid4().hex
      locked = await redis.set(
        key,
        token,
        ex=SNAPSHOT_LOCK_TTL_SECONDS,
        nx=True,
      )
      if not locked:
        raise RuntimeError(
          f"{target.isoformat()} 已有快照任务运行中，请等待当前任务完成"
        )
      acquired[target] = (key, token)
    return acquired
  except Exception:
    for key, token in acquired.values():
      await redis.eval(_LOCK_RELEASE_SCRIPT, 1, key, token)
    raise


async def _release_snapshot_locks(
  locks: dict[date, tuple[str, str]],
) -> None:
  redis = await redis_pubsub.get_redis()
  for key, token in locks.values():
    await redis.eval(_LOCK_RELEASE_SCRIPT, 1, key, token)


def _run_status(saved: int, failed: int) -> str:
  if saved <= 0:
    return "failed"
  if failed > 0:
    return "partial_failure"
  return "success"


def _run_warnings(result: dict[str, Any], errors: list[str]) -> str:
  warnings = []
  if result["saved"] <= 0:
    warnings.append("未保存任何日级信号快照")
  if result["missing_target"]:
    warnings.append(f"{result['missing_target']} 只标的目标日无行情")
  if result["insufficient_history"]:
    warnings.append(f"{result['insufficient_history']} 只新股或历史数据不足")
  if result["failed"]:
    warnings.append(f"{result['failed']} 只标的计算或写入失败")
  warnings.extend(errors[:3])
  return "; ".join(warnings)


async def _create_signal_runs(
  snapshot_dates: list[date],
  total_codes: int,
) -> dict[date, int]:
  run_ids: dict[date, int] = {}
  for target in snapshot_dates:
    async with AsyncSessionLocal() as db:
      run = await DailySignalRunRepository(db).create_run(
        {
          "snapshot_date": target,
          "signal_version": f"daily-signal-v2:{target.isoformat()}",
          "score_version": "score-v1",
          "status": "running",
          "started_at": time_utils.now(),
          "total_codes": total_codes,
        }
      )
      run_ids[target] = run.id
  return run_ids


async def _finish_signal_run(
  run_id: int,
  *,
  started_at: datetime,
  status: str,
  total_codes: int,
  result: dict[str, Any],
  warnings: str,
) -> None:
  completed_at = time_utils.now()
  async with AsyncSessionLocal() as db:
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
  instruments = await resolve_instruments(
    sectors or DEFAULT_SNAPSHOT_SECTORS,
    stock_list,
    allowed_types={InstrumentType.STOCK, InstrumentType.ETF},
  )
  if not instruments:
    raise RuntimeError("PostgreSQL 中没有匹配的股票或 ETF 标的")

  codes = [item["code"] for item in instruments]
  name_map = {item["code"]: item["name"] for item in instruments}
  instrument_type_map = {
    item["code"]: item["instrument_type"] for item in instruments
  }
  float_volume_map = {
    item["code"]: item["float_volume"]
    for item in instruments
    if item["float_volume"] is not None
  }
  logger.info(
    "开始日级快照: target_dates=%s total_codes=%s batch_size=%s",
    [item.isoformat() for item in target_dates],
    len(codes),
    batch_size,
  )

  locks = await _acquire_snapshot_locks(target_dates)
  started_at = time_utils.now()
  run_ids: dict[date, int] = {}
  date_results = {
    target: {
      "saved": 0,
      "skipped": 0,
      "failed": 0,
      "missing_target": 0,
      "insufficient_history": 0,
      "errors": [],
    }
    for target in target_dates
  }
  try:
    run_ids = await _create_signal_runs(target_dates, len(codes))
    service = DailyIndicatorSnapshotService()
    for batch_index, batch in enumerate(_chunks(codes, batch_size), start=1):
      batch_result = await service.compute_and_save_dates_batch(
        codes=batch,
        snapshot_dates=target_dates,
        instrument_type_map=instrument_type_map,
        name_map=name_map,
        float_volume_map=float_volume_map,
      )
      logger.info(
        "指标批次 %s 完成: codes=%s saved=%s skipped=%s failed=%s",
        batch_index,
        len(batch),
        batch_result["saved"],
        batch_result["skipped"],
        batch_result["failed"],
      )
      for target in target_dates:
        target_result = batch_result["dates"][target.isoformat()]
        aggregate = date_results[target]
        for key in (
          "saved",
          "skipped",
          "failed",
          "missing_target",
          "insufficient_history",
        ):
          aggregate[key] += int(target_result[key])
        aggregate["errors"].extend(batch_result["errors"])

    reports = []
    for target in target_dates:
      target_result = date_results[target]
      status = _run_status(target_result["saved"], target_result["failed"])
      warnings = _run_warnings(target_result, target_result["errors"])
      await _finish_signal_run(
        run_ids[target],
        started_at=started_at,
        status=status,
        total_codes=len(codes),
        result=target_result,
        warnings=warnings,
      )
      report = {
        "snapshot_date": target.isoformat(),
        "status": status,
        "total_codes": len(codes),
        **{
          key: target_result[key]
          for key in (
            "saved",
            "skipped",
            "failed",
            "missing_target",
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

    deleted_snapshots = await service.cleanup_old_snapshots(retain_days)
    async with AsyncSessionLocal() as db:
      deleted_runs = await DailySignalRunRepository(db).delete_older_than(
        date.today() - timedelta(days=retain_days)
      )
    return {
      "status": (
        "success"
        if all(item["status"] == "success" for item in reports)
        else "failed"
      ),
      "dates": reports,
      "deleted_old_snapshots": deleted_snapshots,
      "deleted_old_runs": deleted_runs,
    }
  except Exception as exc:
    logger.exception("日级快照 Flow 失败: %s", exc)
    for target, run_id in run_ids.items():
      target_result = date_results[target]
      if target_result["saved"] <= 0 and target_result["failed"] <= 0:
        target_result["failed"] = len(codes)
      try:
        await _finish_signal_run(
          run_id,
          started_at=started_at,
          status="failed",
          total_codes=len(codes),
          result=target_result,
          warnings=f"系统性失败: {exc}",
        )
      except Exception:
        logger.exception("更新失败运行日志失败: %s", target.isoformat())
    raise
  finally:
    await _release_snapshot_locks(locks)
