"""财务指标快照计算服务。"""

import math
from collections import defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import get_async_db
from quantx_infrastructure.models.financial import (
  FinancialBalanceSheet,
  FinancialIncomeStatement,
)
from quantx_infrastructure.repositories.financial_metric_snapshot_repository import (
  FinancialMetricSnapshotRepository,
)
from quantx_infrastructure.services.financial_report_date import (
  normalize_financial_report_date,
)

UNKNOWN_AS_OF_DATE = date(9999, 12, 31)


def _finite_float(value: Any) -> Optional[float]:
  if value is None:
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if math.isfinite(number) else None


def _previous_year_same_period(report_date: date) -> date:
  return date(report_date.year - 1, report_date.month, report_date.day)


def _previous_year_end(report_date: date) -> date:
  return date(report_date.year - 1, 12, 31)


def _previous_report_period(report_date: date) -> Optional[date]:
  if report_date.month == 3 and report_date.day == 31:
    return None
  if report_date.month == 6 and report_date.day == 30:
    return date(report_date.year, 3, 31)
  if report_date.month == 9 and report_date.day == 30:
    return date(report_date.year, 6, 30)
  if report_date.month == 12 and report_date.day == 31:
    return date(report_date.year, 9, 30)
  return None


def _is_annual_report(report_date: date) -> bool:
  return report_date.month == 12 and report_date.day == 31


def _add_flag(flags: List[str], flag: str) -> None:
  if flag not in flags:
    flags.append(flag)


def _record_announce_date(
  row: Any,
  label: str,
  flags: List[str],
  announce_dates: List[date],
) -> bool:
  if row is None:
    _add_flag(flags, f"missing_{label}")
    return False
  announce_date = getattr(row, "announce_date", None)
  if announce_date is None:
    _add_flag(flags, f"missing_{label}_announce_date")
    return False
  announce_dates.append(announce_date)
  return True


def _growth_pct(
  current_value: Optional[float],
  previous_value: Optional[float],
  label: str,
  flags: List[str],
) -> Optional[float]:
  if current_value is None:
    _add_flag(flags, f"missing_current_{label}")
    return None
  if previous_value is None:
    _add_flag(flags, f"missing_previous_{label}")
    return None
  if previous_value == 0:
    _add_flag(flags, f"zero_previous_{label}")
    return None
  return round((current_value - previous_value) / abs(previous_value) * 100, 4)


def _metric_status(
  roe_ttm: Optional[float],
  net_profit_growth_pct: Optional[float],
  revenue_growth_pct: Optional[float],
  net_profit_quarter_growth_pct: Optional[float],
  revenue_quarter_growth_pct: Optional[float],
) -> str:
  values = [
    roe_ttm,
    net_profit_growth_pct,
    revenue_growth_pct,
    net_profit_quarter_growth_pct,
    revenue_quarter_growth_pct,
  ]
  valid_count = sum(value is not None for value in values)
  if valid_count == len(values):
    return "valid"
  if valid_count > 0:
    return "partial"
  return "invalid"


def _normalize_lookup(
  rows_by_date: Dict[date, Any],
) -> Dict[date, Any]:
  normalized: Dict[date, Any] = {}
  for report_date, row in rows_by_date.items():
    normalized_date = normalize_financial_report_date(report_date)
    if normalized_date is not None:
      normalized[normalized_date] = row
  return normalized


def _quarter_value(
  current_income: Any,
  previous_period_income: Optional[Any],
  field_name: str,
  label: str,
  flags: List[str],
) -> Optional[float]:
  current_value = _finite_float(getattr(current_income, field_name, None))
  report_date = normalize_financial_report_date(
    getattr(current_income, "report_date", None)
  )
  if current_value is None:
    _add_flag(flags, f"missing_{label}")
    return None
  if report_date is None:
    _add_flag(flags, f"missing_{label}_report_date")
    return None
  if _previous_report_period(report_date) is None:
    return current_value
  previous_value = _finite_float(getattr(previous_period_income, field_name, None))
  if previous_value is None:
    _add_flag(flags, f"missing_previous_period_{label}")
    return None
  return round(current_value - previous_value, 4)


def calculate_metric_snapshot(
  stock_code: str,
  current_income: FinancialIncomeStatement,
  income_by_date: Dict[date, FinancialIncomeStatement],
  balance_by_date: Dict[date, FinancialBalanceSheet],
  calculated_at,
) -> Dict[str, Any]:
  """计算单个报告期的派生财务指标。"""
  flags: List[str] = []
  announce_dates: List[date] = []
  report_date = normalize_financial_report_date(current_income.report_date)
  income_by_date = _normalize_lookup(income_by_date)
  balance_by_date = _normalize_lookup(balance_by_date)
  prior_same_date = _previous_year_same_period(report_date)
  prior_year_end_date = _previous_year_end(report_date)
  previous_period_date = _previous_report_period(report_date)
  prior_same_previous_period_date = _previous_report_period(prior_same_date)
  current_balance = balance_by_date.get(report_date)
  start_balance = balance_by_date.get(prior_same_date)
  prior_same_income = income_by_date.get(prior_same_date)
  prior_year_income = income_by_date.get(prior_year_end_date)
  previous_period_income = (
    income_by_date.get(previous_period_date)
    if previous_period_date is not None
    else None
  )
  prior_same_previous_period_income = (
    income_by_date.get(prior_same_previous_period_date)
    if prior_same_previous_period_date is not None
    else None
  )

  _record_announce_date(current_income, "current_income", flags, announce_dates)
  _record_announce_date(current_balance, "current_balance", flags, announce_dates)
  _record_announce_date(start_balance, "start_balance", flags, announce_dates)
  if previous_period_date is not None:
    _record_announce_date(
      previous_period_income,
      "current_previous_period_income",
      flags,
      announce_dates,
    )

  if _is_annual_report(report_date):
    _record_announce_date(prior_year_income, "prior_year_income", flags, announce_dates)
    prior_growth_income = prior_year_income
  else:
    _record_announce_date(prior_same_income, "prior_same_income", flags, announce_dates)
    _record_announce_date(prior_year_income, "prior_year_income", flags, announce_dates)
    prior_growth_income = prior_same_income
  if prior_same_previous_period_date is not None:
    _record_announce_date(
      prior_same_previous_period_income,
      "prior_same_previous_period_income",
      flags,
      announce_dates,
    )

  current_profit = _finite_float(current_income.net_profit_excl_min_int_inc)
  current_revenue = _finite_float(current_income.revenue)
  prior_growth_profit = _finite_float(
    getattr(prior_growth_income, "net_profit_excl_min_int_inc", None)
  )
  prior_growth_revenue = _finite_float(getattr(prior_growth_income, "revenue", None))

  if current_profit is None:
    _add_flag(flags, "missing_current_net_profit")
  if current_revenue is None:
    _add_flag(flags, "missing_current_revenue")

  if _is_annual_report(report_date):
    net_profit_ttm = current_profit
  else:
    prior_year_profit = _finite_float(
      getattr(prior_year_income, "net_profit_excl_min_int_inc", None)
    )
    prior_same_profit = _finite_float(
      getattr(prior_same_income, "net_profit_excl_min_int_inc", None)
    )
    if current_profit is None or prior_year_profit is None or prior_same_profit is None:
      if prior_year_profit is None:
        _add_flag(flags, "missing_prior_year_net_profit")
      if prior_same_profit is None:
        _add_flag(flags, "missing_prior_same_net_profit")
      net_profit_ttm = None
    else:
      net_profit_ttm = round(current_profit + prior_year_profit - prior_same_profit, 4)

  current_equity = _finite_float(
    getattr(current_balance, "tot_shrhldr_eqy_excl_min_int", None)
  )
  start_equity = _finite_float(
    getattr(start_balance, "tot_shrhldr_eqy_excl_min_int", None)
  )
  roe_ttm = None
  if net_profit_ttm is None:
    _add_flag(flags, "missing_ttm_net_profit")
  elif current_equity is None:
    _add_flag(flags, "missing_current_shareholder_equity")
  elif start_equity is None:
    _add_flag(flags, "missing_start_shareholder_equity")
  else:
    average_equity = (current_equity + start_equity) / 2
    if average_equity <= 0:
      _add_flag(flags, "non_positive_average_shareholder_equity")
    else:
      roe_ttm = round(net_profit_ttm / average_equity * 100, 4)

  net_profit_growth_pct = _growth_pct(
    current_profit,
    prior_growth_profit,
    "net_profit",
    flags,
  )
  revenue_growth_pct = _growth_pct(
    current_revenue,
    prior_growth_revenue,
    "revenue",
    flags,
  )

  current_quarter_profit = _quarter_value(
    current_income,
    previous_period_income,
    "net_profit_excl_min_int_inc",
    "current_quarter_net_profit",
    flags,
  )
  prior_same_quarter_profit = _quarter_value(
    prior_same_income,
    prior_same_previous_period_income,
    "net_profit_excl_min_int_inc",
    "prior_same_quarter_net_profit",
    flags,
  ) if prior_same_income is not None else None
  if prior_same_income is None:
    _add_flag(flags, "missing_prior_same_quarter_net_profit")
  current_quarter_revenue = _quarter_value(
    current_income,
    previous_period_income,
    "revenue",
    "current_quarter_revenue",
    flags,
  )
  prior_same_quarter_revenue = _quarter_value(
    prior_same_income,
    prior_same_previous_period_income,
    "revenue",
    "prior_same_quarter_revenue",
    flags,
  ) if prior_same_income is not None else None
  if prior_same_income is None:
    _add_flag(flags, "missing_prior_same_quarter_revenue")
  net_profit_quarter_growth_pct = _growth_pct(
    current_quarter_profit,
    prior_same_quarter_profit,
    "quarter_net_profit",
    flags,
  )
  revenue_quarter_growth_pct = _growth_pct(
    current_quarter_revenue,
    prior_same_quarter_revenue,
    "quarter_revenue",
    flags,
  )

  current_announce_date = getattr(current_income, "announce_date", None)
  as_of_date = (
    max(announce_dates)
    if current_announce_date is not None and announce_dates
    else UNKNOWN_AS_OF_DATE
  )
  if current_announce_date is None:
    _add_flag(flags, "not_visible_without_current_announce_date")

  return {
    "code": stock_code,
    "as_of_date": as_of_date,
    "report_date": report_date,
    "announce_date": current_announce_date,
    "roe_ttm": roe_ttm,
    "net_profit_ttm": net_profit_ttm,
    "net_profit_growth_pct": net_profit_growth_pct,
    "revenue_growth_pct": revenue_growth_pct,
    "net_profit_quarter_growth_pct": net_profit_quarter_growth_pct,
    "revenue_quarter_growth_pct": revenue_quarter_growth_pct,
    "quality_status": _metric_status(
      roe_ttm,
      net_profit_growth_pct,
      revenue_growth_pct,
      net_profit_quarter_growth_pct,
      revenue_quarter_growth_pct,
    ),
    "quality_flags": flags,
    "calculated_at": calculated_at,
  }


def _group_by_stock(rows: Iterable[Any]) -> Dict[str, List[Any]]:
  grouped: Dict[str, List[Any]] = defaultdict(list)
  for row in rows:
    grouped[row.stock_code].append(row)
  return grouped


class FinancialMetricSnapshotService:
  """将原始财务四表重算为条件选股可用的指标快照。"""

  def __init__(self, db_session=None, db_factory=get_async_db):
    self.db_session = db_session
    self.db_factory = db_factory

  async def rebuild_for_codes(
    self,
    stock_codes: Optional[List[str]] = None,
  ) -> Dict[str, Any]:
    if self.db_session is not None:
      return await self._rebuild_with_db(self.db_session, stock_codes, commit=True)

    async for db in self.db_factory():
      return await self._rebuild_with_db(db, stock_codes, commit=True)

    raise RuntimeError("数据库连接不可用")

  async def _rebuild_with_db(
    self,
    db,
    stock_codes: Optional[List[str]],
    commit: bool,
  ) -> Dict[str, Any]:
    repo = FinancialMetricSnapshotRepository(db)
    normalized_codes = (
      list(dict.fromkeys(code for code in stock_codes if code))
      if stock_codes is not None
      else await repo.find_distinct_income_codes()
    )
    if not normalized_codes:
      return {"codes": 0, "records": 0, "deleted": 0}

    income_rows = await repo.find_income_rows(normalized_codes)
    balance_rows = await repo.find_balance_rows(normalized_codes)
    income_by_stock = _group_by_stock(income_rows)
    balance_by_stock = _group_by_stock(balance_rows)
    calculated_at = time_utils.now()
    records: List[Dict[str, Any]] = []

    for code in normalized_codes:
      income_by_date = {
        normalize_financial_report_date(row.report_date): row
        for row in income_by_stock.get(code, [])
        if row.report_date is not None
      }
      balance_by_date = {
        normalize_financial_report_date(row.report_date): row
        for row in balance_by_stock.get(code, [])
        if row.report_date is not None
      }
      for income in income_by_stock.get(code, []):
        if income.report_date is None:
          continue
        records.append(
          calculate_metric_snapshot(
            stock_code=code,
            current_income=income,
            income_by_date=income_by_date,
            balance_by_date=balance_by_date,
            calculated_at=calculated_at,
          )
        )

    deleted = await repo.delete_by_codes(normalized_codes)
    saved = await repo.bulk_upsert(records)
    if commit:
      await db.commit()
    return {
      "codes": len(normalized_codes),
      "records": saved,
      "deleted": deleted,
    }
