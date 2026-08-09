"""
技术指标快照仓储层
"""

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, exists, func, not_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.models.financial_metric_snapshot import (
  FinancialMetricSnapshot,
)
from quantx_infrastructure.models.indicator_snapshot import IndicatorSnapshot
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.sector import Sector
from quantx_infrastructure.models.sector_stock import SectorStock

ST_NAME_PREFIXES = ("ST", "*ST", "S*ST", "SST", "＊ST", "S＊ST")
MAX_BULK_UPSERT_RECORDS = 500


def _normalize_instrument_type(value: Any) -> Optional[str]:
  if value is None:
    return None
  enum_name = getattr(value, "name", None)
  if enum_name:
    return str(enum_name).lower()
  enum_value = getattr(value, "value", None)
  if enum_value is not None:
    nested_value = getattr(enum_value, "value", enum_value)
    return str(nested_value).lower()
  text = str(value)
  if "." in text:
    text = text.rsplit(".", 1)[-1]
  return text.lower()


def _is_st_stock_name(name: Optional[str]) -> bool:
  normalized = (name or "").strip().upper()
  return normalized.startswith(ST_NAME_PREFIXES)


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
    """Use bounded multi-row statements and commit the complete batch once."""
    if not records:
      return 0
    for offset in range(0, len(records), MAX_BULK_UPSERT_RECORDS):
      batch = records[offset : offset + MAX_BULK_UPSERT_RECORDS]
      insert_stmt = insert(IndicatorSnapshot).values(batch)
      update_values = {
        key: getattr(insert_stmt.excluded, key)
        for key in batch[0]
        if key not in ("code", "snapshot_date")
      }
      update_values["updated_at"] = func.now()
      stmt = insert_stmt.on_conflict_do_update(
        index_elements=["code", "snapshot_date"],
        set_=update_values,
      )
      await self.db.execute(stmt)
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

  async def find_snapshot_dates(
    self, start_date: date, end_date: date
  ) -> List[date]:
    """返回日期区间内实际存在快照的交易日。"""
    result = await self.db.execute(
      select(IndicatorSnapshot.snapshot_date)
      .where(
        IndicatorSnapshot.snapshot_date >= start_date,
        IndicatorSnapshot.snapshot_date <= end_date,
      )
      .distinct()
      .order_by(IndicatorSnapshot.snapshot_date.asc())
    )
    return list(result.scalars().all())

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

  async def find_instrument_types_by_codes(self, codes: List[str]) -> Dict[str, str]:
    """批量获取标的真实类型，优先使用 instruments 表。"""
    if not codes:
      return {}
    result = await self.db.execute(
      select(Instrument.id, Instrument.type).where(Instrument.id.in_(codes))
    )
    type_map: Dict[str, str] = {}
    for code, instrument_type in result.all():
      normalized = _normalize_instrument_type(instrument_type)
      if normalized:
        type_map[code] = normalized
    return type_map

  async def find_float_volume_by_codes(self, codes: List[str]) -> Dict[str, float]:
    """批量获取流通股本，用于换手率估算。"""
    if not codes:
      return {}
    result = await self.db.execute(
      select(Instrument.id, Instrument.float_volume).where(Instrument.id.in_(codes))
    )
    float_volume_map: Dict[str, float] = {}
    for code, float_volume in result.all():
      if float_volume:
        float_volume_map[code] = float(float_volume)
    return float_volume_map

  def _universe_condition(self, universe: str):
    universe = (universe or "stock").lower()
    snapshot_type = func.lower(func.coalesce(IndicatorSnapshot.instrument_type, "stock"))
    if universe == "etf":
      return or_(
        Instrument.type == InstrumentType.ETF,
        and_(Instrument.type.is_(None), snapshot_type == "etf"),
      )
    if universe == "stock_and_etf":
      return or_(
        Instrument.type.in_([InstrumentType.STOCK, InstrumentType.ETF]),
        and_(Instrument.type.is_(None), snapshot_type.in_(["stock", "etf"])),
      )
    return or_(
      Instrument.type == InstrumentType.STOCK,
      and_(Instrument.type.is_(None), snapshot_type == "stock"),
    )

  def _stock_type_condition(self):
    snapshot_type = func.lower(func.coalesce(IndicatorSnapshot.instrument_type, "stock"))
    return or_(
      Instrument.type == InstrumentType.STOCK,
      and_(Instrument.type.is_(None), snapshot_type == "stock"),
    )

  def _st_name_condition(self):
    display_name = func.upper(
      func.trim(func.coalesce(IndicatorSnapshot.name, Instrument.name, ""))
    )
    return or_(
      *[display_name.like(f"{prefix}%") for prefix in ST_NAME_PREFIXES]
    )

  def _exclude_st_condition(self):
    return not_(and_(self._stock_type_condition(), self._st_name_condition()))

  async def screen_snapshots(
    self,
    snapshot_date: date,
    signal_codes: Optional[List[str]] = None,
    field_conditions: Optional[List[Dict[str, Any]]] = None,
    include_industries: Optional[List[str]] = None,
    exclude_industries: Optional[List[str]] = None,
    sort: Optional[Dict[str, str]] = None,
    min_roe: Optional[float] = None,
    min_net_profit_growth: Optional[float] = None,
    min_yoy_growth: Optional[float] = None,
    limit: int = 200,
    offset: int = 0,
    universe: str = "stock",
    exclude_st: bool = True,
  ) -> tuple[List[IndicatorSnapshot], int]:
    """基于已落库日级快照做条件选股。"""
    conditions = [
      IndicatorSnapshot.snapshot_date == snapshot_date,
      self._universe_condition(universe),
    ]

    for signal_code in signal_codes or []:
      conditions.append(IndicatorSnapshot.matched_signals.any(signal_code))

    allowed_fields = {
      "current_price": IndicatorSnapshot.current_price,
      "change_pct": IndicatorSnapshot.change_pct,
      "volume_ratio": IndicatorSnapshot.volume_ratio,
      "avg_volume_5": IndicatorSnapshot.avg_volume_5,
      "avg_volume_20": IndicatorSnapshot.avg_volume_20,
      "volume_ratio_5": IndicatorSnapshot.volume_ratio_5,
      "avg_amount_20": IndicatorSnapshot.avg_amount_20,
      "amount_ratio_20": IndicatorSnapshot.amount_ratio_20,
      "turnover_rate_pct": IndicatorSnapshot.turnover_rate_pct,
      "volume_percentile_60": IndicatorSnapshot.volume_percentile_60,
      "amount_percentile_60": IndicatorSnapshot.amount_percentile_60,
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
    if exclude_st:
      conditions.append(self._exclude_st_condition())

    latest_metric = aliased(FinancialMetricSnapshot)
    latest_metric_as_of = (
      select(latest_metric.as_of_date)
      .where(
        latest_metric.code == IndicatorSnapshot.code,
        latest_metric.as_of_date <= snapshot_date,
      )
      .order_by(
        latest_metric.as_of_date.desc(),
        latest_metric.report_date.desc(),
      )
      .limit(1)
      .correlate(IndicatorSnapshot)
      .scalar_subquery()
    )
    latest_metric_report = (
      select(latest_metric.report_date)
      .where(
        latest_metric.code == IndicatorSnapshot.code,
        latest_metric.as_of_date <= snapshot_date,
      )
      .order_by(
        latest_metric.as_of_date.desc(),
        latest_metric.report_date.desc(),
      )
      .limit(1)
      .correlate(IndicatorSnapshot)
      .scalar_subquery()
    )
    financial_metric_join = and_(
      FinancialMetricSnapshot.code == IndicatorSnapshot.code,
      FinancialMetricSnapshot.as_of_date == latest_metric_as_of,
      FinancialMetricSnapshot.report_date == latest_metric_report,
    )
    if min_roe is not None:
      conditions.extend([
        FinancialMetricSnapshot.roe_ttm.isnot(None),
        FinancialMetricSnapshot.roe_ttm >= min_roe,
      ])
    if min_net_profit_growth is not None:
      conditions.extend([
        FinancialMetricSnapshot.net_profit_quarter_growth_pct.isnot(None),
        FinancialMetricSnapshot.net_profit_quarter_growth_pct >= min_net_profit_growth,
      ])
    if min_yoy_growth is not None:
      conditions.extend([
        FinancialMetricSnapshot.revenue_quarter_growth_pct.isnot(None),
        FinancialMetricSnapshot.revenue_quarter_growth_pct >= min_yoy_growth,
      ])

    base = (
      select(IndicatorSnapshot, FinancialMetricSnapshot)
      .outerjoin(Instrument, Instrument.id == IndicatorSnapshot.code)
      .outerjoin(FinancialMetricSnapshot, financial_metric_join)
      .where(*conditions)
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await self.db.execute(count_stmt)).scalar_one() or 0

    signal_count = func.coalesce(
      func.array_length(IndicatorSnapshot.matched_signals, 1),
      0,
    )
    sortable_fields = {
      "code": IndicatorSnapshot.code,
      "name": IndicatorSnapshot.name,
      "current_price": IndicatorSnapshot.current_price,
      "change_pct": IndicatorSnapshot.change_pct,
      "signal_count": signal_count,
      "kdj_j": IndicatorSnapshot.kdj_j,
      "rsi12": IndicatorSnapshot.rsi12,
      "volume_ratio": IndicatorSnapshot.volume_ratio,
      "volume_ratio_5": IndicatorSnapshot.volume_ratio_5,
      "amount_ratio_20": IndicatorSnapshot.amount_ratio_20,
      "turnover_rate_pct": IndicatorSnapshot.turnover_rate_pct,
      "volume_percentile_60": IndicatorSnapshot.volume_percentile_60,
      "amount_percentile_60": IndicatorSnapshot.amount_percentile_60,
      "price_drop_pct": IndicatorSnapshot.price_drop_pct,
      "days_since_peak": IndicatorSnapshot.days_since_peak,
      "roe_ttm": FinancialMetricSnapshot.roe_ttm,
      "net_profit_growth_pct": FinancialMetricSnapshot.net_profit_quarter_growth_pct,
      "revenue_growth_pct": FinancialMetricSnapshot.revenue_quarter_growth_pct,
    }
    order_by = [
      IndicatorSnapshot.change_pct.desc(),
      IndicatorSnapshot.volume_ratio.desc(),
      IndicatorSnapshot.code.asc(),
    ]
    if sort:
      sort_field = sortable_fields.get(sort.get("field") or "")
      if sort_field is not None:
        direction = (sort.get("direction") or "desc").lower()
        sort_expression = sort_field.asc() if direction == "asc" else sort_field.desc()
        order_by = [
          sort_expression.nulls_last(),
          IndicatorSnapshot.code.asc(),
        ]

    stmt = (
      base.order_by(*order_by)
      .offset(offset)
      .limit(limit)
    )
    result = await self.db.execute(stmt)
    records = []
    for snapshot, financial_metric in result.all():
      setattr(snapshot, "financial_metric", financial_metric)
      records.append(snapshot)
    return records, total

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
