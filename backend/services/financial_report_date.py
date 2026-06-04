"""财报报告期日期规范化工具。"""

from datetime import date
from typing import Optional


QUARTER_END_DAYS = {
  3: 31,
  6: 30,
  9: 30,
  12: 31,
}


def normalize_financial_report_date(report_date: Optional[date]) -> Optional[date]:
  """把 XTQuant 季度末前一天报告期规范为真实季度末。"""
  if report_date is None:
    return None

  quarter_end_day = QUARTER_END_DAYS.get(report_date.month)
  if quarter_end_day is None:
    return report_date

  if report_date.day in {quarter_end_day, quarter_end_day - 1}:
    return date(report_date.year, report_date.month, quarter_end_day)

  return report_date
