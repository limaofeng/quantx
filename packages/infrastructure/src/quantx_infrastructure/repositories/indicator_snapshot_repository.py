"""
技术指标快照仓储层
"""

from datetime import date
from typing import Any, Dict, List, Mapping, Optional

from quantx_domain.factors import (
  FACTOR_DEFINITIONS,
  FACTOR_VERSION,
  normalize_conditions,
)
from sqlalchemy import and_, case, delete, exists, func, not_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from quantx_infrastructure.core.financial_quality import (
  ROE_QUALITY_INVALID,
  ROE_QUALITY_STALE,
  ROE_QUALITY_SUSPICIOUS,
  ROE_QUALITY_UNVERIFIED,
  ROE_QUALITY_VALID,
  minimum_required_financial_report_date,
)
from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.models.financial_metric_roe_quality import (
  FinancialMetricRoeQuality,
)
from quantx_infrastructure.models.financial_metric_snapshot import (
  FinancialMetricSnapshot,
)
from quantx_infrastructure.models.financial_sync_code_audit import (
  FinancialSyncCodeAudit,
)
from quantx_infrastructure.models.financial_sync_run import FinancialSyncRun
from quantx_infrastructure.models.indicator_snapshot import IndicatorSnapshot
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.sector import Sector
from quantx_infrastructure.models.sector_stock import SectorStock
from quantx_infrastructure.services.snapshot_fencing import (
  acquire_snapshot_publish_guard,
  assert_snapshot_publish_owner,
  assert_snapshot_run_owner,
)

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


def _effective_roe_quality(
  metric: Optional[FinancialMetricSnapshot],
  quality: Optional[FinancialMetricRoeQuality],
  audit: Optional[FinancialSyncCodeAudit],
  snapshot_date: date,
) -> tuple[str, List[str]]:
  flags = list(getattr(quality, "flags", None) or [])
  if audit is None or audit.status == "FAILED":
    return ROE_QUALITY_UNVERIFIED, [*flags, "financial_sync_unverified"]
  if audit.status == "EMPTY":
    return ROE_QUALITY_INVALID, [*flags, "financial_sync_empty"]
  if audit.status != "SUCCESS":
    return ROE_QUALITY_UNVERIFIED, [*flags, "financial_sync_unverified"]
  if metric is None:
    return ROE_QUALITY_INVALID, [*flags, "missing_roe_metric"]
  if quality is None:
    return ROE_QUALITY_UNVERIFIED, [*flags, "roe_quality_unverified"]

  static_status = quality.status or ROE_QUALITY_UNVERIFIED
  if static_status != ROE_QUALITY_VALID:
    return static_status, flags
  minimum_report_date = minimum_required_financial_report_date(snapshot_date)
  if metric.report_date < minimum_report_date:
    return ROE_QUALITY_STALE, [*flags, "financial_report_stale"]
  return ROE_QUALITY_VALID, flags


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
      set_={k: v for k, v in data.items() if k not in ("code", "snapshot_date")},
    )
    await self.db.execute(stmt)

  async def bulk_upsert(
    self,
    records: List[Dict[str, Any]],
    *,
    snapshot_run_ids: Mapping[date, int],
    lock_backend_pid: int,
  ) -> int:
    """Use bounded multi-row statements and commit the complete batch once."""
    if not records:
      return 0
    snapshot_dates = tuple(sorted(snapshot_run_ids))
    try:
      await acquire_snapshot_publish_guard(
        self.db,
        lock_backend_pid=lock_backend_pid,
        snapshot_dates=snapshot_dates,
      )
      await assert_snapshot_run_owner(self.db, snapshot_run_ids)
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
      await assert_snapshot_publish_owner(
        self.db,
        lock_backend_pid=lock_backend_pid,
        snapshot_dates=snapshot_dates,
      )
      await self.db.commit()
    except BaseException:
      await self.db.rollback()
      raise
    return len(records)

  async def invalidate_factor_scope(
    self,
    codes: List[str],
    snapshot_dates: List[date],
    *,
    snapshot_run_ids: Mapping[date, int],
  ) -> None:
    """Fail closed before recomputation, retaining old values for audit/baselines.

    Commit separately from the later upsert so a read/calculation/write failure
    cannot resurrect a previously valid factor row for this exact target scope.
    """
    if not codes or not snapshot_dates:
      return
    await assert_snapshot_run_owner(self.db, snapshot_run_ids)
    await self.db.execute(
      update(IndicatorSnapshot)
      .where(
        IndicatorSnapshot.code.in_(codes),
        IndicatorSnapshot.snapshot_date.in_(snapshot_dates),
        IndicatorSnapshot.calculation_version == FACTOR_VERSION,
      )
      .values(calculation_version=None)
    )
    await self.db.commit()

  async def find_by_date(self, snapshot_date: date) -> List[IndicatorSnapshot]:
    """获取某交易日所有标的的快照"""
    result = await self.db.execute(
      select(IndicatorSnapshot).filter(IndicatorSnapshot.snapshot_date == snapshot_date)
    )
    return list(result.scalars().all())

  async def get_latest_snapshot_date(self) -> Optional[date]:
    """获取最新可用快照交易日"""
    result = await self.db.execute(select(func.max(IndicatorSnapshot.snapshot_date)))
    return result.scalar_one_or_none()

  async def get_latest_factor_snapshot_date(self) -> Optional[date]:
    """日级因子选股只读取当前定义版本，不能借用雷达历史基线。"""
    result = await self.db.execute(
      select(func.max(IndicatorSnapshot.snapshot_date)).where(
        IndicatorSnapshot.calculation_version == FACTOR_VERSION
      )
    )
    return result.scalar_one_or_none()

  async def find_snapshot_dates(
    self,
    start_date: date,
    end_date: date,
    *,
    calculation_version: Optional[str] = None,
  ) -> List[date]:
    """返回日期区间内实际存在快照的交易日。"""
    conditions = [
      IndicatorSnapshot.snapshot_date >= start_date,
      IndicatorSnapshot.snapshot_date <= end_date,
    ]
    if calculation_version is not None:
      conditions.append(IndicatorSnapshot.calculation_version == calculation_version)
    result = await self.db.execute(
      select(IndicatorSnapshot.snapshot_date)
      .where(*conditions)
      .distinct()
      .order_by(IndicatorSnapshot.snapshot_date.asc())
    )
    return list(result.scalars().all())

  async def find_factor_snapshot_dates(
    self, start_date: date, end_date: date
  ) -> List[date]:
    return await self.find_snapshot_dates(
      start_date, end_date, calculation_version=FACTOR_VERSION
    )

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
    expanded_names = list(
      dict.fromkeys(
        names + [name + "加权" for name in names if not name.endswith("加权")]
      )
    )
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
    snapshot_type = func.lower(
      func.coalesce(IndicatorSnapshot.instrument_type, "stock")
    )
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
    snapshot_type = func.lower(
      func.coalesce(IndicatorSnapshot.instrument_type, "stock")
    )
    return or_(
      Instrument.type == InstrumentType.STOCK,
      and_(Instrument.type.is_(None), snapshot_type == "stock"),
    )

  def _st_name_condition(self):
    display_name = func.upper(
      func.trim(func.coalesce(IndicatorSnapshot.name, Instrument.name, ""))
    )
    return or_(*[display_name.like(f"{prefix}%") for prefix in ST_NAME_PREFIXES])

  def _exclude_st_condition(self):
    return not_(and_(self._stock_type_condition(), self._st_name_condition()))

  async def list_baseline_snapshots(
    self,
    snapshot_date: date,
    *,
    universe: str = "stock_and_etf",
    exclude_st: bool = True,
  ) -> List[IndicatorSnapshot]:
    """Radar's existing daily baselines are independent of factor-study readiness."""
    conditions = [
      IndicatorSnapshot.snapshot_date == snapshot_date,
      self._universe_condition(universe),
    ]
    if exclude_st:
      conditions.append(self._exclude_st_condition())
    result = await self.db.execute(
      select(IndicatorSnapshot)
      .outerjoin(Instrument, Instrument.id == IndicatorSnapshot.code)
      .where(*conditions)
      .order_by(
        IndicatorSnapshot.change_pct.desc(),
        IndicatorSnapshot.volume_ratio.desc(),
        IndicatorSnapshot.code.asc(),
      )
      .limit(20_000)
    )
    return list(result.scalars().all())

  async def screen_factor_snapshots(
    self,
    snapshot_date: date,
    factor_conditions: Optional[List[Dict[str, Any]]] = None,
    include_industries: Optional[List[str]] = None,
    exclude_industries: Optional[List[str]] = None,
    sort: Optional[Dict[str, str]] = None,
    limit: int = 200,
    offset: int = 0,
    universe: str = "stock",
    exclude_st: bool = True,
  ) -> tuple[List[IndicatorSnapshot], int]:
    """基于已落库日级快照做条件选股。"""
    conditions = [
      IndicatorSnapshot.snapshot_date == snapshot_date,
      IndicatorSnapshot.calculation_version == FACTOR_VERSION,
      self._universe_condition(universe),
    ]
    normalized_conditions = normalize_conditions(factor_conditions or [])

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
    roe_quality_join = and_(
      FinancialMetricRoeQuality.code == FinancialMetricSnapshot.code,
      FinancialMetricRoeQuality.as_of_date == FinancialMetricSnapshot.as_of_date,
      FinancialMetricRoeQuality.report_date == FinancialMetricSnapshot.report_date,
    )
    latest_sync_run_id = (
      select(FinancialSyncRun.id)
      .order_by(FinancialSyncRun.started_at.desc(), FinancialSyncRun.id.desc())
      .limit(1)
      .scalar_subquery()
    )
    financial_audit_join = and_(
      FinancialSyncCodeAudit.run_id == latest_sync_run_id,
      FinancialSyncCodeAudit.stock_code == IndicatorSnapshot.code,
    )
    minimum_report_date = minimum_required_financial_report_date(snapshot_date)
    valid_roe_condition = and_(
      FinancialSyncCodeAudit.status == "SUCCESS",
      FinancialMetricRoeQuality.status == ROE_QUALITY_VALID,
      FinancialMetricSnapshot.report_date >= minimum_report_date,
      FinancialMetricSnapshot.roe_ttm.isnot(None),
    )
    effective_roe = case(
      (valid_roe_condition, FinancialMetricSnapshot.roe_ttm),
      else_=None,
    )
    valid_growth_condition = and_(
      FinancialSyncCodeAudit.status == "SUCCESS",
      FinancialMetricSnapshot.quality_status.in_(["valid", "partial"]),
      FinancialMetricSnapshot.report_date >= minimum_report_date,
    )
    allowed_fields = {
      definition.id: getattr(IndicatorSnapshot, definition.id)
      for definition in FACTOR_DEFINITIONS
      if hasattr(IndicatorSnapshot, definition.id)
    }
    allowed_fields.update(
      {
        "roe_ttm": effective_roe,
        "net_profit_growth_pct": case(
          (
            valid_growth_condition,
            FinancialMetricSnapshot.net_profit_quarter_growth_pct,
          ),
          else_=None,
        ),
        "revenue_growth_pct": case(
          (valid_growth_condition, FinancialMetricSnapshot.revenue_quarter_growth_pct),
          else_=None,
        ),
      }
    )
    for item in normalized_conditions:
      field = allowed_fields[item["factor_id"]]
      value = item["value"]
      operator = item["operator"]
      if operator == "between":
        conditions.append(and_(field >= value, field <= item["value_to"]))
      else:
        conditions.append(
          {
            "gte": field.__ge__,
            "lte": field.__le__,
            "gt": field.__gt__,
            "lt": field.__lt__,
            "eq": field.__eq__,
          }[operator](value)
        )

    base = (
      select(
        IndicatorSnapshot,
        FinancialMetricSnapshot,
        FinancialMetricRoeQuality,
        FinancialSyncCodeAudit,
      )
      .outerjoin(Instrument, Instrument.id == IndicatorSnapshot.code)
      .outerjoin(FinancialMetricSnapshot, financial_metric_join)
      .outerjoin(FinancialMetricRoeQuality, roe_quality_join)
      .outerjoin(FinancialSyncCodeAudit, financial_audit_join)
      .where(*conditions)
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await self.db.execute(count_stmt)).scalar_one() or 0

    sortable_fields = {
      **allowed_fields,
      "code": IndicatorSnapshot.code,
      "name": IndicatorSnapshot.name,
    }
    order_by = [
      IndicatorSnapshot.change_pct.desc().nulls_last(),
      IndicatorSnapshot.code.asc(),
    ]
    if sort:
      sort_field = sortable_fields.get(sort.get("field") or "")
      if sort_field is None:
        raise ValueError("未知排序因子")
      direction = (sort.get("direction") or "desc").lower()
      if direction not in {"asc", "desc"}:
        raise ValueError("未知排序方向")
      sort_expression = sort_field.asc() if direction == "asc" else sort_field.desc()
      order_by = [sort_expression.nulls_last(), IndicatorSnapshot.code.asc()]

    stmt = base.order_by(*order_by).offset(offset).limit(limit)
    result = await self.db.execute(stmt)
    records = []
    for snapshot, financial_metric, roe_quality, financial_audit in result.all():
      roe_quality_status, roe_quality_flags = _effective_roe_quality(
        financial_metric,
        roe_quality,
        financial_audit,
        snapshot_date,
      )
      setattr(snapshot, "financial_metric", financial_metric)
      setattr(snapshot, "financial_audit", financial_audit)
      setattr(snapshot, "roe_quality_status", roe_quality_status)
      setattr(snapshot, "roe_quality_flags", roe_quality_flags)
      setattr(
        snapshot,
        "financial_growth_selectable",
        bool(
          financial_metric is not None
          and financial_audit is not None
          and financial_audit.status == "SUCCESS"
          and financial_metric.quality_status in {"valid", "partial"}
          and financial_metric.report_date >= minimum_report_date
        ),
      )
      records.append(snapshot)
    return records, total

  async def financial_quality_counts(
    self,
    snapshot_date: date,
    *,
    include_industries: Optional[List[str]] = None,
    exclude_industries: Optional[List[str]] = None,
    universe: str = "stock",
    exclude_st: bool = True,
  ) -> Dict[str, int]:
    """Count effective ROE states for the requested screening universe."""
    conditions = [
      IndicatorSnapshot.snapshot_date == snapshot_date,
      IndicatorSnapshot.calculation_version == FACTOR_VERSION,
      self._universe_condition(universe),
    ]
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
      .order_by(latest_metric.as_of_date.desc(), latest_metric.report_date.desc())
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
      .order_by(latest_metric.as_of_date.desc(), latest_metric.report_date.desc())
      .limit(1)
      .correlate(IndicatorSnapshot)
      .scalar_subquery()
    )
    financial_metric_join = and_(
      FinancialMetricSnapshot.code == IndicatorSnapshot.code,
      FinancialMetricSnapshot.as_of_date == latest_metric_as_of,
      FinancialMetricSnapshot.report_date == latest_metric_report,
    )
    roe_quality_join = and_(
      FinancialMetricRoeQuality.code == FinancialMetricSnapshot.code,
      FinancialMetricRoeQuality.as_of_date == FinancialMetricSnapshot.as_of_date,
      FinancialMetricRoeQuality.report_date == FinancialMetricSnapshot.report_date,
    )
    latest_sync_run_id = (
      select(FinancialSyncRun.id)
      .order_by(FinancialSyncRun.started_at.desc(), FinancialSyncRun.id.desc())
      .limit(1)
      .scalar_subquery()
    )
    financial_audit_join = and_(
      FinancialSyncCodeAudit.run_id == latest_sync_run_id,
      FinancialSyncCodeAudit.stock_code == IndicatorSnapshot.code,
    )
    effective_status = case(
      (
        or_(
          FinancialSyncCodeAudit.id.is_(None),
          FinancialSyncCodeAudit.status == "FAILED",
          FinancialSyncCodeAudit.status.notin_(["SUCCESS", "EMPTY"]),
        ),
        ROE_QUALITY_UNVERIFIED,
      ),
      (FinancialSyncCodeAudit.status == "EMPTY", ROE_QUALITY_INVALID),
      (FinancialMetricSnapshot.code.is_(None), ROE_QUALITY_INVALID),
      (
        FinancialMetricRoeQuality.status == ROE_QUALITY_SUSPICIOUS,
        ROE_QUALITY_SUSPICIOUS,
      ),
      (
        FinancialMetricRoeQuality.status == ROE_QUALITY_INVALID,
        ROE_QUALITY_INVALID,
      ),
      (
        or_(
          FinancialMetricRoeQuality.code.is_(None),
          FinancialMetricRoeQuality.status != ROE_QUALITY_VALID,
        ),
        ROE_QUALITY_UNVERIFIED,
      ),
      (
        FinancialMetricSnapshot.report_date
        < minimum_required_financial_report_date(snapshot_date),
        ROE_QUALITY_STALE,
      ),
      else_=ROE_QUALITY_VALID,
    )
    base = (
      select(effective_status.label("roe_quality_status"))
      .select_from(IndicatorSnapshot)
      .outerjoin(Instrument, Instrument.id == IndicatorSnapshot.code)
      .outerjoin(FinancialMetricSnapshot, financial_metric_join)
      .outerjoin(FinancialMetricRoeQuality, roe_quality_join)
      .outerjoin(FinancialSyncCodeAudit, financial_audit_join)
      .where(*conditions)
    ).subquery()
    result = await self.db.execute(
      select(
        func.count().label("total"),
        func.count().filter(base.c.roe_quality_status == ROE_QUALITY_VALID),
        func.count().filter(base.c.roe_quality_status == ROE_QUALITY_STALE),
        func.count().filter(base.c.roe_quality_status == ROE_QUALITY_SUSPICIOUS),
        func.count().filter(base.c.roe_quality_status == ROE_QUALITY_INVALID),
        func.count().filter(base.c.roe_quality_status == ROE_QUALITY_UNVERIFIED),
      ).select_from(base)
    )
    row = result.one()
    verified_result = await self.db.execute(
      select(func.count())
      .select_from(IndicatorSnapshot)
      .outerjoin(Instrument, Instrument.id == IndicatorSnapshot.code)
      .outerjoin(FinancialSyncCodeAudit, financial_audit_join)
      .where(*conditions, FinancialSyncCodeAudit.status == "SUCCESS")
    )
    return {
      "total": int(row[0] or 0),
      "selectable": int(row[1] or 0),
      "stale": int(row[2] or 0),
      "suspicious": int(row[3] or 0),
      "invalid": int(row[4] or 0),
      "unverified": int(row[5] or 0),
      "verified": int(verified_result.scalar_one() or 0),
    }

  async def find_latest_by_code(self, code: str) -> Optional[IndicatorSnapshot]:
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
      delete(IndicatorSnapshot).where(IndicatorSnapshot.snapshot_date < cutoff_date)
    )
    await self.db.commit()
    return result.rowcount
