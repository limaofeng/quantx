"""Pure financial quality policy shared by persistence and screening reads."""

from datetime import date

ROE_QUALITY_VALID = "VALID"
ROE_QUALITY_STALE = "STALE"
ROE_QUALITY_SUSPICIOUS = "SUSPICIOUS"
ROE_QUALITY_INVALID = "INVALID"
ROE_QUALITY_UNVERIFIED = "UNVERIFIED"

ROE_QUALITY_STATUSES = {
  ROE_QUALITY_VALID,
  ROE_QUALITY_STALE,
  ROE_QUALITY_SUSPICIOUS,
  ROE_QUALITY_INVALID,
  ROE_QUALITY_UNVERIFIED,
}


def minimum_required_financial_report_date(as_of_date: date) -> date:
  """Return the oldest report period allowed after statutory deadlines.

  The switch happens on the day after each disclosure deadline, so a report
  due on April 30 is not treated as stale during April 30 itself.
  """

  if as_of_date.month >= 11:
    return date(as_of_date.year, 9, 30)
  if as_of_date.month >= 9:
    return date(as_of_date.year, 6, 30)
  if as_of_date.month >= 5:
    return date(as_of_date.year, 3, 31)
  return date(as_of_date.year - 1, 9, 30)
