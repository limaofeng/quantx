"""技术指标计算 Prefect tasks。"""

from datetime import date
from typing import Any, Dict, List

from prefect import get_run_logger, task

from services.daily_indicator_snapshot_service import DailyIndicatorSnapshotService


@task(
  name="计算并保存单批技术指标快照",
  description="读取本地日线 K 线缓存 → 计算 MA/RSI/KDJ/BOLL → upsert PostgreSQL",
  retries=2,
  retry_delay_seconds=30,
)
async def compute_and_save_indicator_batch(
  codes: List[str],
  snapshot_date: date,
  instrument_type_map: Dict[str, str],
  name_map: Dict[str, str],
  lookback_days: int = 310,
) -> Dict[str, Any]:
  """为一批标的计算技术指标快照并写入数据库。"""
  service = DailyIndicatorSnapshotService(logger_=get_run_logger())
  return await service.compute_and_save_batch(
    codes=codes,
    snapshot_date=snapshot_date,
    instrument_type_map=instrument_type_map,
    name_map=name_map,
    lookback_days=lookback_days,
  )


@task(
  name="清理过期技术指标快照",
  description="删除超出保留天数的历史快照记录",
  retries=1,
)
async def cleanup_old_snapshots(retain_days: int = 30) -> int:
  """删除 retain_days 天之前的快照记录。"""
  service = DailyIndicatorSnapshotService(logger_=get_run_logger())
  return await service.cleanup_old_snapshots(retain_days=retain_days)
