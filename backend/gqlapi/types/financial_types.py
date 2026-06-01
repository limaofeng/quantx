from datetime import date
from decimal import Decimal
from typing import List, Optional

import strawberry


def _float(value) -> Optional[float]:
  if value is None:
    return None
  if isinstance(value, Decimal):
    return float(value)
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


@strawberry.type(description="利润表核心字段")
class FinancialIncomeStatementData:
  stock_code: str = strawberry.field(description="标的代码")
  report_date: date = strawberry.field(description="报告截止日")
  announce_date: Optional[date] = strawberry.field(description="公告日期")
  revenue: Optional[float] = strawberry.field(description="营业总收入")
  revenue_inc: Optional[float] = strawberry.field(description="营业收入")
  total_operating_cost: Optional[float] = strawberry.field(description="营业总成本")
  oper_profit: Optional[float] = strawberry.field(description="营业利润")
  total_profit: Optional[float] = strawberry.field(description="利润总额")
  net_profit: Optional[float] = strawberry.field(description="净利润")
  net_profit_excl_min_int_inc: Optional[float] = strawberry.field(
    description="归母净利润"
  )
  eps_basic: Optional[float] = strawberry.field(description="基本每股收益")

  @staticmethod
  def from_model(model) -> "FinancialIncomeStatementData":
    return FinancialIncomeStatementData(
      stock_code=model.stock_code,
      report_date=model.report_date,
      announce_date=model.announce_date,
      revenue=_float(model.revenue),
      revenue_inc=_float(model.revenue_inc),
      total_operating_cost=_float(model.total_operating_cost),
      oper_profit=_float(model.oper_profit),
      total_profit=_float(model.tot_profit),
      net_profit=_float(model.net_profit_incl_min_int_inc),
      net_profit_excl_min_int_inc=_float(model.net_profit_excl_min_int_inc),
      eps_basic=_float(model.s_fa_eps_basic),
    )


@strawberry.type(description="资产负债表核心字段")
class FinancialBalanceSheetData:
  stock_code: str = strawberry.field(description="标的代码")
  report_date: date = strawberry.field(description="报告截止日")
  announce_date: Optional[date] = strawberry.field(description="公告日期")
  total_assets: Optional[float] = strawberry.field(description="资产总计")
  total_current_assets: Optional[float] = strawberry.field(description="流动资产合计")
  total_non_current_assets: Optional[float] = strawberry.field(
    description="非流动资产合计"
  )
  cash_equivalents: Optional[float] = strawberry.field(description="货币资金")
  inventories: Optional[float] = strawberry.field(description="存货")
  total_liabilities: Optional[float] = strawberry.field(description="负债合计")
  total_current_liability: Optional[float] = strawberry.field(
    description="流动负债合计"
  )
  non_current_liabilities: Optional[float] = strawberry.field(
    description="非流动负债合计"
  )
  total_equity: Optional[float] = strawberry.field(description="所有者权益合计")
  shareholder_equity: Optional[float] = strawberry.field(description="归母股东权益合计")

  @staticmethod
  def from_model(model) -> "FinancialBalanceSheetData":
    return FinancialBalanceSheetData(
      stock_code=model.stock_code,
      report_date=model.report_date,
      announce_date=model.announce_date,
      total_assets=_float(model.total_assets),
      total_current_assets=_float(model.total_current_assets),
      total_non_current_assets=_float(model.total_non_current_assets),
      cash_equivalents=_float(model.cash_equivalents),
      inventories=_float(model.inventories),
      total_liabilities=_float(model.total_liabilities),
      total_current_liability=_float(model.total_current_liability),
      non_current_liabilities=_float(model.non_current_liabilities),
      total_equity=_float(model.total_equity),
      shareholder_equity=_float(model.tot_shrhldr_eqy_excl_min_int),
    )


@strawberry.type(description="现金流量表核心字段")
class FinancialCashFlowData:
  stock_code: str = strawberry.field(description="标的代码")
  report_date: date = strawberry.field(description="报告截止日")
  announce_date: Optional[date] = strawberry.field(description="公告日期")
  net_cash_flows_oper_act: Optional[float] = strawberry.field(
    description="经营活动现金流净额"
  )
  net_cash_flows_inv_act: Optional[float] = strawberry.field(
    description="投资活动现金流净额"
  )
  net_cash_flows_fnc_act: Optional[float] = strawberry.field(
    description="筹资活动现金流净额"
  )
  net_incr_cash_cash_equ: Optional[float] = strawberry.field(
    description="现金及现金等价物净增加额"
  )
  cash_cash_equ_end_period: Optional[float] = strawberry.field(
    description="期末现金及现金等价物余额"
  )

  @staticmethod
  def from_model(model) -> "FinancialCashFlowData":
    return FinancialCashFlowData(
      stock_code=model.stock_code,
      report_date=model.report_date,
      announce_date=model.announce_date,
      net_cash_flows_oper_act=_float(model.net_cash_flows_oper_act),
      net_cash_flows_inv_act=_float(model.net_cash_flows_inv_act),
      net_cash_flows_fnc_act=_float(model.net_cash_flows_fnc_act),
      net_incr_cash_cash_equ=_float(model.net_incr_cash_cash_equ),
      cash_cash_equ_end_period=_float(model.cash_cash_equ_end_period),
    )


@strawberry.type(description="股本结构核心字段")
class FinancialCapitalData:
  stock_code: str = strawberry.field(description="标的代码")
  report_date: date = strawberry.field(description="报告截止日")
  announce_date: Optional[date] = strawberry.field(description="公告日期")
  total_capital: Optional[float] = strawberry.field(description="总股本")
  circulating_capital: Optional[float] = strawberry.field(description="流通A股")
  restrict_circulating_capital: Optional[float] = strawberry.field(
    description="限售流通股"
  )
  free_float_capital: Optional[float] = strawberry.field(description="自由流通股本")

  @staticmethod
  def from_model(model) -> "FinancialCapitalData":
    return FinancialCapitalData(
      stock_code=model.stock_code,
      report_date=model.report_date,
      announce_date=model.announce_date,
      total_capital=_float(model.total_capital),
      circulating_capital=_float(model.circulating_capital),
      restrict_circulating_capital=_float(model.restrict_circulating_capital),
      free_float_capital=_float(model.free_float_capital),
    )


@strawberry.type(description="单票财务四表")
class FinancialStatements:
  stock_code: str = strawberry.field(description="标的代码")
  income: List[FinancialIncomeStatementData] = strawberry.field(description="利润表")
  balance: List[FinancialBalanceSheetData] = strawberry.field(description="资产负债表")
  cash_flow: List[FinancialCashFlowData] = strawberry.field(description="现金流量表")
  capital: List[FinancialCapitalData] = strawberry.field(description="股本结构")


@strawberry.type(description="财务数据页报表行")
class FinancialReportSummary:
  stock_code: str = strawberry.field(description="标的代码")
  stock_name: Optional[str] = strawberry.field(description="标的名称")
  report_date: date = strawberry.field(description="报告截止日")
  announce_date: Optional[date] = strawberry.field(description="公告日期")
  revenue: Optional[float] = strawberry.field(description="营业总收入")
  net_profit_excl_min_int_inc: Optional[float] = strawberry.field(
    description="归母净利润"
  )
  eps_basic: Optional[float] = strawberry.field(description="基本每股收益")

  @staticmethod
  def from_model(model, stock_name: Optional[str]) -> "FinancialReportSummary":
    return FinancialReportSummary(
      stock_code=model.stock_code,
      stock_name=stock_name,
      report_date=model.report_date,
      announce_date=model.announce_date,
      revenue=_float(model.revenue),
      net_profit_excl_min_int_inc=_float(model.net_profit_excl_min_int_inc),
      eps_basic=_float(model.s_fa_eps_basic),
    )


@strawberry.type(description="财务数据页报表分页")
class FinancialReportPage:
  items: List[FinancialReportSummary] = strawberry.field(description="报表行")
  total: int = strawberry.field(description="总数")


@strawberry.type(description="财务数据页整体统计")
class FinancialOverview:
  report_count: int = strawberry.field(description="利润表记录数")
  instrument_count: int = strawberry.field(description="覆盖标的数")
  latest_report_date: Optional[date] = strawberry.field(description="最新报告期")
  latest_announce_date: Optional[date] = strawberry.field(description="最新公告日")


@strawberry.type(description="单票财务摘要")
class FinancialSummary:
  stock_code: str = strawberry.field(description="标的代码")
  latest_report_date: Optional[date] = strawberry.field(description="最新报告期")
  latest_announce_date: Optional[date] = strawberry.field(description="最新公告日")
  revenue: Optional[float] = strawberry.field(description="营业总收入")
  net_profit_excl_min_int_inc: Optional[float] = strawberry.field(
    description="归母净利润"
  )
  eps_basic: Optional[float] = strawberry.field(description="基本每股收益")
  total_assets: Optional[float] = strawberry.field(description="资产总计")
  total_liabilities: Optional[float] = strawberry.field(description="负债合计")
  total_equity: Optional[float] = strawberry.field(description="所有者权益合计")
  operating_cash_flow: Optional[float] = strawberry.field(description="经营现金流")
  cash_balance: Optional[float] = strawberry.field(description="期末现金余额")
  total_capital: Optional[float] = strawberry.field(description="总股本")
  circulating_capital: Optional[float] = strawberry.field(description="流通A股")
  income_count: int = strawberry.field(description="利润表记录数")
  balance_count: int = strawberry.field(description="资产负债表记录数")
  cash_flow_count: int = strawberry.field(description="现金流记录数")
  capital_count: int = strawberry.field(description="股本结构记录数")
