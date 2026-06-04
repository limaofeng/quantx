"""
每日技术指标快照流程

调度时机：每个交易日 15:35（K 线数据同步完成后 30 分钟）
覆盖范围：沪深 A 股 + 沪深 ETF
存储目标：PostgreSQL indicator_snapshots 表（滚动保留近 30 天）

依赖前序 Flow：
  daily-market-data-sync (15:05) —— 必须先下载完日线 K 线本地缓存
"""

from typing import Any, Dict, List, Optional

from prefect import flow, get_run_logger

from core.utils import time_utils
from database.relational_connection import get_async_db
from miniqmt.manager_registry import XTDataManagerRegistry
from models.instrument import Instrument
from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from prefector.tasks.indicator_tasks import (
  cleanup_old_snapshots,
  compute_and_save_indicator_batch,
)
from repositories.daily_signal_run_repository import DailySignalRunRepository
from repositories.instrument_where_builder import InstrumentWhereBuilder
from services.instrument_service import InstrumentService

# 每批处理的标的数量
_BATCH_SIZE = 300
_ETF_CODE_PREFIXES = ("510", "511", "512", "513", "515", "516", "517", "518", "159")
_SH_INDEX_PREFIXES = ("000", "880", "881", "882", "883", "884", "885", "886", "887", "888", "899")


def _chunks(lst: List, n: int):
  """将列表按大小 n 分片"""
  for i in range(0, len(lst), n):
    yield lst[i : i + n]


def _signal_run_status(total_codes: int, saved: int, failed: int) -> str:
  if total_codes <= 0:
    return "failed"
  if saved <= 0:
    return "failed"
  if failed > 0:
    return "partial_failure"
  return "success"


def _signal_run_warnings(saved: int, skipped: int, failed: int, errors: List[str]) -> str:
  warnings = []
  if saved <= 0:
    warnings.append("未保存任何日级信号快照")
  if failed:
    warnings.append("部分标的信号计算失败")
  if skipped:
    warnings.append("部分标的数据不足被跳过")
  warnings.extend(errors[:3])
  return "; ".join(warnings)


def _normalize_instrument_type(value: Any) -> Optional[str]:
  if value is None:
    return None
  enum_value = getattr(value, "value", None)
  if enum_value is not None:
    return str(enum_value).lower()
  text = str(value)
  if "." in text:
    text = text.rsplit(".", 1)[-1]
  return text.lower()


def _infer_instrument_type(code: str, sector: Optional[str] = None) -> str:
  sector_text = (sector or "").upper()
  if "指数" in (sector or "") or "INDEX" in sector_text:
    return "index"
  if "ETF" in sector_text:
    return "etf"
  normalized_code = (code or "").upper()
  if normalized_code.startswith(_ETF_CODE_PREFIXES):
    return "etf"
  if normalized_code.endswith(".SH") and normalized_code.startswith(_SH_INDEX_PREFIXES):
    return "index"
  if normalized_code.endswith(".SZ") and normalized_code.startswith("399"):
    return "index"
  return "stock"


def _filter_signal_snapshot_codes(
  codes: List[str],
  instrument_type_map: Dict[str, str],
) -> List[str]:
  return [
    code
    for code in codes
    if instrument_type_map.get(code, "stock") != "index"
  ]


@flow(
  name="每日技术指标快照",
  description=(
    "收盘后批量计算 A 股 + ETF 技术指标（MA/RSI/KDJ/BOLL）"
    "并 upsert 至 PostgreSQL indicator_snapshots 表"
  ),
  retries=1,
  retry_delay_seconds=120,
  **STANDARD_FLOW_HOOKS,
)
async def daily_indicator_snapshot_flow(
  sectors: List[str] = None,
  stock_list: Optional[List[str]] = None,
  batch_size: int = _BATCH_SIZE,
  retain_days: int = 30,
) -> Dict[str, Any]:
  """
  每日技术指标快照主流程。

  Args:
      sectors:     板块列表，默认 ["沪深A股", "沪深ETF"]
      stock_list:  指定增量计算标的列表；为空时按 sectors 全量计算
      batch_size:  每批处理标的数量（影响内存与速度的平衡）
      retain_days: PostgreSQL 保留历史天数

  Returns:
      汇总报告字典
  """
  if sectors is None:
    sectors = ["沪深A股", "沪深ETF"]

  logger = get_run_logger()
  start_time = time_utils.now()
  snapshot_date = time_utils.today()

  logger.info("=" * 60)
  logger.info(f"开始每日技术指标快照流程  [{snapshot_date}]")
  logger.info(f"覆盖板块: {sectors}  每批: {batch_size}  保留: {retain_days}天")
  logger.info("=" * 60)

  signal_version = f"daily-signal-v2:{snapshot_date.isoformat()}"
  signal_run_id = None
  try:
    async for db in get_async_db():
      run_repo = DailySignalRunRepository(db)
      run = await run_repo.create_run(
        {
          "snapshot_date": snapshot_date,
          "signal_version": signal_version,
          "score_version": "score-v1",
          "status": "running",
          "started_at": start_time,
        }
      )
      signal_run_id = run.id
      break
  except Exception as e:
    logger.warning(f"创建信号运行日志失败，快照计算继续执行: {e}")

  # ── 步骤1：从 XTData 获取全量标的代码 ───────────────────
  logger.info("步骤1: 从 XTData 获取标的代码列表")

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()

  all_codes: List[str] = []
  instrument_type_map: Dict[str, str] = {}  # code → "stock"/"etf"

  if stock_list:
    all_codes = list(dict.fromkeys(stock_list))
    for code in all_codes:
      instrument_type_map[code] = _infer_instrument_type(code)
    logger.info(f"使用传入增量标的列表: {len(all_codes)} 只")
  else:
    for sector in sectors:
      codes = data_manager.get_stock_list_in_sector(sector)
      itype = _infer_instrument_type("", sector)
      for code in codes:
        instrument_type_map[code] = itype
      all_codes.extend(codes)
      logger.info(f"  [{sector}]  {len(codes)} 只标的")

  # 去重（不同板块可能有重叠）
  all_codes = list(dict.fromkeys(all_codes))
  logger.info(f"去重后共 {len(all_codes)} 只标的")

  # ── 步骤2：从数据库加载名称映射 ──────────────────────────
  logger.info("步骤2: 从数据库加载标的名称")
  name_map: Dict[str, str] = {}

  try:
    instrument_service = InstrumentService()
    where = InstrumentWhereBuilder().in_(Instrument.id, all_codes)
    instruments = await instrument_service.find_all(where=where, limit=20000)
    for inst in instruments:
      name_map[inst.id] = inst.name or ""
      instrument_type = _normalize_instrument_type(inst.type)
      if instrument_type in {"stock", "etf", "index"}:
        instrument_type_map[inst.id] = instrument_type
    logger.info(f"  加载到 {len(name_map)} 条名称")
  except Exception as e:
    logger.warning(f"  加载名称失败（将以空名称继续）: {e}")

  eligible_codes = _filter_signal_snapshot_codes(all_codes, instrument_type_map)
  skipped_index_count = len(all_codes) - len(eligible_codes)
  if skipped_index_count:
    logger.info(f"  已跳过 {skipped_index_count} 只指数标的，不写入选股信号快照")
  all_codes = eligible_codes

  # ── 步骤3：分批计算指标并写库 ────────────────────────────
  total_batches = (len(all_codes) + batch_size - 1) // batch_size
  logger.info(f"步骤3: 分 {total_batches} 批计算技术指标")

  total_saved = 0
  total_skipped = 0
  total_failed = 0
  batch_errors: List[str] = []

  for batch_idx, batch in enumerate(_chunks(all_codes, batch_size), start=1):
    logger.info(f"  批次 {batch_idx}/{total_batches}: {len(batch)} 只标的")
    batch_result = await compute_and_save_indicator_batch(
      codes=batch,
      snapshot_date=snapshot_date,
      instrument_type_map=instrument_type_map,
      name_map=name_map,
    )
    total_saved += batch_result.get("saved", 0)
    total_skipped += batch_result.get("skipped", 0)
    total_failed += batch_result.get("failed", 0)
    batch_errors.extend(batch_result.get("errors", []) or [])

  # ── 步骤4：清理过期历史 ───────────────────────────────────
  logger.info(f"步骤4: 清理超过 {retain_days} 天的历史快照")
  deleted_count = await cleanup_old_snapshots(retain_days=retain_days)

  # ── 汇总报告 ──────────────────────────────────────────────
  elapsed = (time_utils.now() - start_time).total_seconds()
  status = _signal_run_status(len(all_codes), total_saved, total_failed)
  warnings = _signal_run_warnings(
    total_saved,
    total_skipped,
    total_failed,
    batch_errors,
  )
  report = {
    "snapshot_date": str(snapshot_date),
    "sectors": sectors,
    "stock_list": stock_list,
    "signal_version": signal_version,
    "total_codes": len(all_codes),
    "total_batches": total_batches,
    "saved": total_saved,
    "skipped": total_skipped,
    "failed": total_failed,
    "deleted_old": deleted_count,
    "elapsed_seconds": round(elapsed, 1),
    "status": status,
    "warnings": warnings,
  }

  if signal_run_id is not None:
    try:
      async for db in get_async_db():
        run_repo = DailySignalRunRepository(db)
        await run_repo.update_run(
          signal_run_id,
          {
            "status": status,
            "completed_at": time_utils.now(),
            "total_codes": len(all_codes),
            "saved": total_saved,
            "skipped": total_skipped,
            "failed": total_failed,
            "elapsed_seconds": round(elapsed, 1),
            "warnings": warnings,
          },
        )
        break
    except Exception as e:
      logger.warning(f"更新信号运行日志失败: {e}")

  logger.info("=" * 60)
  logger.info(
    f"快照流程完成 ✓  "
    f"保存 {total_saved}  跳过 {total_skipped}  失败 {total_failed}  "
    f"清理 {deleted_count}  耗时 {elapsed:.1f}s"
  )
  logger.info("=" * 60)

  return report


if __name__ == "__main__":
  import asyncio
  asyncio.run(daily_indicator_snapshot_flow())
