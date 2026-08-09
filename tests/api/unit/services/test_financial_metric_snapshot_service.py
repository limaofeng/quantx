from datetime import date, datetime
from types import SimpleNamespace

from quantx_infrastructure.services.financial_metric_snapshot_service import (
  UNKNOWN_AS_OF_DATE,
  calculate_metric_snapshot,
)
from quantx_infrastructure.services.financial_report_date import (
  normalize_financial_report_date,
)
from quantx_infrastructure.services.financial_service import FinancialService


def income(report_date, announce_date, profit, revenue):
  return SimpleNamespace(
    stock_code="000001.SZ",
    report_date=report_date,
    announce_date=announce_date,
    net_profit_excl_min_int_inc=profit,
    revenue=revenue,
  )


def balance(report_date, announce_date, equity):
  return SimpleNamespace(
    stock_code="000001.SZ",
    report_date=report_date,
    announce_date=announce_date,
    tot_shrhldr_eqy_excl_min_int=equity,
  )


def calculate(current_income, incomes, balances):
  return calculate_metric_snapshot(
    stock_code="000001.SZ",
    current_income=current_income,
    income_by_date={row.report_date: row for row in incomes},
    balance_by_date={row.report_date: row for row in balances},
    calculated_at=datetime(2026, 6, 4, 16, 0, 0),
  )


def test_normalizes_xtquant_quarter_end_offsets():
  assert normalize_financial_report_date(date(2025, 3, 30)) == date(2025, 3, 31)
  assert normalize_financial_report_date(date(2025, 6, 29)) == date(2025, 6, 30)
  assert normalize_financial_report_date(date(2025, 9, 29)) == date(2025, 9, 30)
  assert normalize_financial_report_date(date(2025, 12, 30)) == date(2025, 12, 31)
  assert normalize_financial_report_date(date(2025, 4, 20)) == date(2025, 4, 20)


def test_financial_service_normalizes_report_date_not_announce_date():
  assert FinancialService._parse_report_date("20250330") == date(2025, 3, 31)
  assert FinancialService._parse_date("20250330") == date(2025, 3, 30)


def test_offset_report_dates_match_ttm_sources_after_normalization():
  current_previous = income(date(2025, 6, 29), date(2025, 8, 25), 50.0, 400.0)
  prior_same_previous = income(date(2024, 6, 29), date(2024, 8, 25), 30.0, 350.0)
  prior_same = income(date(2024, 9, 29), date(2024, 10, 25), 40.0, 500.0)
  prior_year = income(date(2024, 12, 30), date(2025, 4, 20), 100.0, 900.0)
  current = income(date(2025, 9, 29), date(2025, 10, 25), 70.0, 600.0)

  result = calculate(
    current,
    [prior_same_previous, prior_same, prior_year, current_previous, current],
    [
      balance(date(2024, 9, 29), date(2024, 10, 25), 900.0),
      balance(date(2025, 9, 29), date(2025, 10, 25), 1100.0),
    ],
  )

  assert result["report_date"] == date(2025, 9, 30)
  assert result["net_profit_ttm"] == 130.0
  assert result["roe_ttm"] == 13.0
  assert result["net_profit_growth_pct"] == 75.0
  assert result["revenue_growth_pct"] == 20.0
  assert result["net_profit_quarter_growth_pct"] == 100.0
  assert result["revenue_quarter_growth_pct"] == 33.3333
  assert result["quality_status"] == "valid"


def test_offset_annual_report_date_is_treated_as_annual_report():
  current_previous = income(date(2025, 9, 30), date(2025, 10, 25), 90.0, 750.0)
  prior_same_previous = income(date(2024, 9, 30), date(2024, 10, 25), 80.0, 700.0)
  current = income(date(2025, 12, 30), date(2026, 4, 20), 120.0, 1000.0)
  previous = income(date(2024, 12, 30), date(2025, 4, 20), 100.0, 900.0)

  result = calculate(
    current,
    [prior_same_previous, previous, current_previous, current],
    [
      balance(date(2024, 12, 30), date(2025, 4, 20), 800.0),
      balance(date(2025, 12, 30), date(2026, 4, 20), 1000.0),
    ],
  )

  assert result["report_date"] == date(2025, 12, 31)
  assert result["net_profit_ttm"] == 120.0
  assert result["roe_ttm"] == 13.3333
  assert result["net_profit_quarter_growth_pct"] == 50.0
  assert result["revenue_quarter_growth_pct"] == 25.0
  assert result["quality_status"] == "valid"


def test_calculates_annual_ttm_roe_and_growth():
  current_previous = income(date(2025, 9, 30), date(2025, 10, 20), 90.0, 750.0)
  prior_same_previous = income(date(2024, 9, 30), date(2024, 10, 20), 80.0, 700.0)
  current = income(date(2025, 12, 31), date(2026, 4, 20), 120.0, 1000.0)
  previous = income(date(2024, 12, 31), date(2025, 4, 20), 100.0, 900.0)

  result = calculate(
    current,
    [prior_same_previous, previous, current_previous, current],
    [
      balance(date(2024, 12, 31), date(2025, 4, 20), 800.0),
      balance(date(2025, 12, 31), date(2026, 4, 20), 1000.0),
    ],
  )

  assert result["as_of_date"] == date(2026, 4, 20)
  assert result["net_profit_ttm"] == 120.0
  assert result["roe_ttm"] == 13.3333
  assert result["net_profit_growth_pct"] == 20.0
  assert result["revenue_growth_pct"] == 11.1111
  assert result["net_profit_quarter_growth_pct"] == 50.0
  assert result["revenue_quarter_growth_pct"] == 25.0
  assert result["quality_status"] == "valid"


def test_calculates_non_annual_ttm_from_current_prior_year_and_prior_same():
  current_previous = income(date(2025, 3, 31), date(2025, 4, 25), 20.0, 250.0)
  prior_same_previous = income(date(2024, 3, 31), date(2024, 4, 25), 15.0, 200.0)
  prior_same = income(date(2024, 6, 30), date(2024, 8, 25), 40.0, 500.0)
  prior_year = income(date(2024, 12, 31), date(2025, 4, 20), 100.0, 900.0)
  current = income(date(2025, 6, 30), date(2025, 8, 25), 70.0, 600.0)

  result = calculate(
    current,
    [prior_same_previous, prior_same, prior_year, current_previous, current],
    [
      balance(date(2024, 6, 30), date(2024, 8, 25), 900.0),
      balance(date(2025, 6, 30), date(2025, 8, 25), 1100.0),
    ],
  )

  assert result["net_profit_ttm"] == 130.0
  assert result["roe_ttm"] == 13.0
  assert result["net_profit_growth_pct"] == 75.0
  assert result["revenue_growth_pct"] == 20.0
  assert result["net_profit_quarter_growth_pct"] == 100.0
  assert result["revenue_quarter_growth_pct"] == 16.6667
  assert result["quality_status"] == "valid"


def test_calculates_q1_quarter_growth_from_q1_accumulated_values():
  prior_same = income(date(2024, 3, 31), date(2024, 4, 25), 20.0, 200.0)
  prior_year = income(date(2024, 12, 31), date(2025, 4, 20), 100.0, 900.0)
  current = income(date(2025, 3, 31), date(2025, 4, 25), 30.0, 260.0)

  result = calculate(
    current,
    [prior_same, prior_year, current],
    [
      balance(date(2024, 3, 31), date(2024, 4, 25), 900.0),
      balance(date(2025, 3, 31), date(2025, 4, 25), 1100.0),
    ],
  )

  assert result["net_profit_quarter_growth_pct"] == 50.0
  assert result["revenue_quarter_growth_pct"] == 30.0
  assert result["quality_status"] == "valid"


def test_missing_prior_reports_nulls_ttm_and_marks_quality_flags():
  current = income(date(2025, 9, 30), date(2025, 10, 25), 70.0, 600.0)

  result = calculate(
    current,
    [current],
    [
      balance(date(2024, 9, 30), date(2024, 10, 25), 900.0),
      balance(date(2025, 9, 30), date(2025, 10, 25), 1100.0),
    ],
  )

  assert result["net_profit_ttm"] is None
  assert result["roe_ttm"] is None
  assert result["net_profit_growth_pct"] is None
  assert "missing_prior_same_income" in result["quality_flags"]
  assert "missing_prior_year_income" in result["quality_flags"]
  assert result["quality_status"] == "invalid"


def test_missing_current_previous_period_nulls_quarter_growth():
  prior_same_previous = income(date(2024, 3, 31), date(2024, 4, 25), 15.0, 200.0)
  prior_same = income(date(2024, 6, 30), date(2024, 8, 25), 40.0, 500.0)
  prior_year = income(date(2024, 12, 31), date(2025, 4, 20), 100.0, 900.0)
  current = income(date(2025, 6, 30), date(2025, 8, 25), 70.0, 600.0)

  result = calculate(
    current,
    [prior_same_previous, prior_same, prior_year, current],
    [
      balance(date(2024, 6, 30), date(2024, 8, 25), 900.0),
      balance(date(2025, 6, 30), date(2025, 8, 25), 1100.0),
    ],
  )

  assert result["net_profit_quarter_growth_pct"] is None
  assert result["revenue_quarter_growth_pct"] is None
  assert "missing_current_previous_period_income" in result["quality_flags"]
  assert "missing_previous_period_current_quarter_net_profit" in result["quality_flags"]
  assert result["quality_status"] == "partial"


def test_zero_prior_same_quarter_value_nulls_quarter_growth():
  current_previous = income(date(2025, 3, 31), date(2025, 4, 25), 20.0, 250.0)
  prior_same_previous = income(date(2024, 3, 31), date(2024, 4, 25), 40.0, 500.0)
  prior_same = income(date(2024, 6, 30), date(2024, 8, 25), 40.0, 500.0)
  prior_year = income(date(2024, 12, 31), date(2025, 4, 20), 100.0, 900.0)
  current = income(date(2025, 6, 30), date(2025, 8, 25), 70.0, 600.0)

  result = calculate(
    current,
    [prior_same_previous, prior_same, prior_year, current_previous, current],
    [
      balance(date(2024, 6, 30), date(2024, 8, 25), 900.0),
      balance(date(2025, 6, 30), date(2025, 8, 25), 1100.0),
    ],
  )

  assert result["net_profit_quarter_growth_pct"] is None
  assert result["revenue_quarter_growth_pct"] is None
  assert "zero_previous_quarter_net_profit" in result["quality_flags"]
  assert "zero_previous_quarter_revenue" in result["quality_flags"]


def test_non_positive_average_equity_nulls_roe():
  current_previous = income(date(2025, 9, 30), date(2025, 10, 20), 90.0, 750.0)
  prior_same_previous = income(date(2024, 9, 30), date(2024, 10, 20), 80.0, 700.0)
  current = income(date(2025, 12, 31), date(2026, 4, 20), 120.0, 1000.0)
  previous = income(date(2024, 12, 31), date(2025, 4, 20), 100.0, 900.0)

  result = calculate(
    current,
    [prior_same_previous, previous, current_previous, current],
    [
      balance(date(2024, 12, 31), date(2025, 4, 20), -100.0),
      balance(date(2025, 12, 31), date(2026, 4, 20), 100.0),
    ],
  )

  assert result["roe_ttm"] is None
  assert "non_positive_average_shareholder_equity" in result["quality_flags"]
  assert result["quality_status"] == "partial"


def test_missing_current_announce_date_is_not_visible_to_history():
  current_previous = income(date(2025, 9, 30), date(2025, 10, 20), 90.0, 750.0)
  prior_same_previous = income(date(2024, 9, 30), date(2024, 10, 20), 80.0, 700.0)
  current = income(date(2025, 12, 31), None, 120.0, 1000.0)
  previous = income(date(2024, 12, 31), date(2025, 4, 20), 100.0, 900.0)

  result = calculate(
    current,
    [prior_same_previous, previous, current_previous, current],
    [
      balance(date(2024, 12, 31), date(2025, 4, 20), 800.0),
      balance(date(2025, 12, 31), date(2026, 4, 20), 1000.0),
    ],
  )

  assert result["as_of_date"] == UNKNOWN_AS_OF_DATE
  assert "missing_current_income_announce_date" in result["quality_flags"]
  assert "not_visible_without_current_announce_date" in result["quality_flags"]
