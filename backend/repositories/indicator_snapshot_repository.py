"""
技术指标快照仓储层
"""

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, exists, func, not_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.indicator_snapshot import IndicatorSnapshot
from models.sector import Sector
from models.sector_stock import SectorStock


class IndicatorSnapshotRepository(BaseRepository[IndicatorSnapshot]):
  """技术指标快照仓储"""

  model_class = IndicatorSnapshot
 
  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def upsert(self, data: Dict[str, Any]) -> None:
    """按 (code, snapshot_date) 插入或更新一条快照"""
    stmt = insert(IndicatorSnapshot).values(**data)
    stmt = stmt.on_conflict_do_update(
      index_elements=["code", "snapshot_date"],
      set_={
        k: v
        for k, v in data.items()
        if k not in ("code", "snapshot_date")
      },
    )
    await self.db.execute(stmt)

  async def bulk_upsert(self, records: List[Dict[str, Any]]) -> int:
    """批量 upsert，最后统一 commit"""
    if not records:
      return 0
    for record in records:
      await self.upsert(record)
    await self.db.commit()
    return len(records)

  async def find_by_date(
    self, snapshot_date: date
  ) -> List[IndicatorSnapshot]:
    """获取某交易日所有标的的快照"""
    result = await self.db.execute(
      select(IndicatorSnapshot).filter(
        IndicatorSnapshot.snapshot_date == snapshot_date
      )
    )
    return list(result.scalars().all())

  async def get_latest_snapshot_date(self) -> Optional[date]:
    """获取最新可用快照交易日"""
    result = await self.db.execute(select(func.max(IndicatorSnapshot.snapshot_date)))
    return result.scalar_one_or_none()

  async def get_latest_calculated_at(self, snapshot_date: date):
    """获取指定快照日期最后更新时间"""
    result = await self.db.execute(
      select(func.max(IndicatorSnapshot.updated_at)).where(
        IndicatorSnapshot.snapshot_date == snapshot_date
      )
    )
    return result.scalar_one_or_none()

  def _industry_condition(self, names: List[str], include: bool = True):
    if not names:
      return None
    expanded_names = list(dict.fromkeys(
      names + [name + "加权" for name in names if not name.endswith("加权")]
    ))
    sector_exists = exists(
      select(SectorStock.id)
      .join(Sector, SectorStock.sector_id == Sector.id)
      .where(
        SectorStock.stock_code == IndicatorSnapshot.code,
        Sector.name.in_(expanded_names),
      )
    )
    return sector_exists if include else not_(sector_exists)

  async def find_industry_names_by_codes(self, codes: List[str]) -> Dict[str, str]:
    """批量获取标的行业名称，优先返回 SW1 板块。"""
    if not codes:
      return {}
    result = await self.db.execute(
      select(SectorStock.stock_code, Sector.name)
      .join(Sector, SectorStock.sector_id == Sector.id)
      .where(SectorStock.stock_code.in_(codes))
      .order_by(Sector.classification.asc(), Sector.level.asc(), Sector.name.asc())
    )
    industry_map: Dict[str, str] = {}
    for code, name in result.all():
      if code not in industry_map and name:
        industry_map[code] = name.replace("加权", "")
    return industry_map

  async def screen_snapshots(
    self,
    snapshot_date: date,
    signal_codes: Optional[List[str]] = None,
    field_conditions: Optional[List[Dict[str, Any]]] = None,
    include_industries: Optional[List[str]] = None,
    exclude_industries: Optional[List[str]] = None,
    limit: int = 200,
    offset: int = 0,
  ) -> tuple[List[IndicatorSnapshot], int]:
    """基于已落库日级快照做条件选股。"""
    conditions = [IndicatorSnapshot.snapshot_date == snapshot_date]

    for signal_code in signal_codes or []:
      conditions.append(IndicatorSnapshot.matched_signals.any(signal_code))

    allowed_fields = {
      "current_price": IndicatorSnapshot.current_price,
      "change_pct": IndicatorSnapshot.change_pct,
      "volume_ratio": IndicatorSnapshot.volume_ratio,
      "price_drop_pct": IndicatorSnapshot.price_drop_pct,
      "price_rise_pct": IndicatorSnapshot.price_rise_pct,
      "days_since_peak": IndicatorSnapshot.days_since_peak,
      "days_since_low": IndicatorSnapshot.days_since_low,
      "consecutive_down_days": IndicatorSnapshot.consecutive_down_days,
      "consecutive_down_pct": IndicatorSnapshot.consecutive_down_pct,
      "rsi6": IndicatorSnapshot.rsi6,
      "rsi12": IndicatorSnapshot.rsi12,
      "rsi24": IndicatorSnapshot.rsi24,
      "kdj_k": IndicatorSnapshot.kdj_k,
      "kdj_d": IndicatorSnapshot.kdj_d,
      "kdj_j": IndicatorSnapshot.kdj_j,
      "ma5": IndicatorSnapshot.ma5,
      "ma10": IndicatorSnapshot.ma10,
      "ma20": IndicatorSnapshot.ma20,
      "boll_percent_b": IndicatorSnapshot.boll_percent_b,
      "boll_bandwidth": IndicatorSnapshot.boll_bandwidth,
    }
    for item in field_conditions or []:
      field = allowed_fields.get(item.get("field"))
      if field is None:
        continue
      operator = item.get("operator") or "gte"
      value = item.get("value")
      value_to = item.get("value_to")
      if value is None:
        continue
      if operator == "lte":
        conditions.append(field <= value)
      elif operator == "lt":
        conditions.append(field < value)
      elif operator == "gt":
        conditions.append(field > value)
      elif operator == "eq":
        conditions.append(field == value)
      elif operator == "between" and value_to is not None:
        conditions.append(and_(field >= value, field <= value_to))
      else:
        conditions.append(field >= value)

    include_condition = self._industry_condition(include_industries or [], True)
    if include_condition is not None:
      conditions.append(include_condition)
    exclude_condition = self._industry_condition(exclude_industries or [], False)
    if exclude_condition is not None:
      conditions.append(exclude_condition)

    base = select(IndicatorSnapshot).where(*conditions)
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await self.db.execute(count_stmt)).scalar_one() or 0

    stmt = (
      base.order_by(
        IndicatorSnapshot.change_pct.desc(),
        IndicatorSnapshot.volume_ratio.desc(),
        IndicatorSnapshot.code.asc(),
      )
      .offset(offset)
      .limit(limit)
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all()), total

  async def find_latest_by_code(
    self, code: str
  ) -> Optional[IndicatorSnapshot]:
    """获取某标的最新一条快照"""
    result = await self.db.execute(
      select(IndicatorSnapshot)
      .filter(IndicatorSnapshot.code == code)
      .order_by(IndicatorSnapshot.snapshot_date.desc())
      .limit(1)
    )
    return result.scalar_one_or_none()

  async def find_by_code_date_range(
    self, code: str, start_date: date, end_date: date
  ) -> List[IndicatorSnapshot]:
    """获取某标的某日期区间的快照列表（用于穿越点检测等连续性分析）"""
    result = await self.db.execute(
      select(IndicatorSnapshot)
      .filter(
        IndicatorSnapshot.code == code,
        IndicatorSnapshot.snapshot_date >= start_date,
        IndicatorSnapshot.snapshot_date <= end_date,
      )
      .order_by(IndicatorSnapshot.snapshot_date.asc())
    )
    return list(result.scalars().all())

  async def delete_older_than(self, cutoff_date: date) -> int:
    """删除 cutoff_date 之前的历史数据（保留近 N 天）"""
    result = await self.db.execute(
      delete(IndicatorSnapshot).where(
        IndicatorSnapshot.snapshot_date < cutoff_date
      )
    )
    await self.db.commit()
    return result.rowcount
