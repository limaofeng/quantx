"""
财务数据模型 - 资产负债表、利润表、现金流量表、股本结构、股东信息
"""

from sqlalchemy import DECIMAL, Column, Date, Integer, String, UniqueConstraint

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class FinancialBalanceSheet(Base, TimestampMixin):
  """资产负债表"""

  __tablename__ = "financial_balance_sheet"
  __table_args__ = (
    UniqueConstraint(
      "stock_code",
      "report_date",
      name="uq_financial_balance_sheet_stock_report",
    ),
  )

  id = Column(Integer, primary_key=True, autoincrement=True)
  stock_code = Column(String(20), nullable=False, index=True, comment="标的代码")
  report_date = Column(Date, nullable=False, comment="报告截止日")
  announce_date = Column(Date, comment="公告日期")

  # 核心资产项
  total_assets = Column("total_assets", DECIMAL(20, 4), comment="资产总计")
  total_current_assets = Column(DECIMAL(20, 4), comment="流动资产合计")
  total_non_current_assets = Column(DECIMAL(20, 4), comment="非流动资产合计")
  cash_equivalents = Column(DECIMAL(20, 4), comment="货币资金")
  tradable_fin_assets = Column(DECIMAL(20, 4), comment="交易性金融资产")
  bill_receivable = Column(DECIMAL(20, 4), comment="应收票据")
  account_receivable = Column(DECIMAL(20, 4), comment="应收账款")
  advance_payment = Column(DECIMAL(20, 4), comment="预付款项")
  other_receivable = Column(DECIMAL(20, 4), comment="其他应收款")
  inventories = Column(DECIMAL(20, 4), comment="存货")
  other_current_assets = Column(DECIMAL(20, 4), comment="其他流动资产")
  long_term_eqy_invest = Column(DECIMAL(20, 4), comment="长期股权投资")
  fix_assets = Column(DECIMAL(20, 4), comment="固定资产")
  constru_in_process = Column(DECIMAL(20, 4), comment="在建工程")
  intang_assets = Column(DECIMAL(20, 4), comment="无形资产")
  goodwill = Column(DECIMAL(20, 4), comment="商誉")
  deferred_tax_assets = Column(DECIMAL(20, 4), comment="递延所得税资产")

  # 核心负债项
  total_liabilities = Column("total_liabilities", DECIMAL(20, 4), comment="负债合计")
  total_current_liability = Column(DECIMAL(20, 4), comment="流动负债合计")
  non_current_liabilities = Column(DECIMAL(20, 4), comment="非流动负债合计")
  shortterm_loan = Column(DECIMAL(20, 4), comment="短期借款")
  notes_payable = Column(DECIMAL(20, 4), comment="应付票据")
  accounts_payable = Column(DECIMAL(20, 4), comment="应付账款")
  advance_peceipts = Column(DECIMAL(20, 4), comment="预收账款")
  empl_ben_payable = Column(DECIMAL(20, 4), comment="应付职工薪酬")
  taxes_surcharges_payable = Column(DECIMAL(20, 4), comment="应交税费")
  other_payable = Column(DECIMAL(20, 4), comment="其他应付款")
  long_term_loans = Column(DECIMAL(20, 4), comment="长期借款")
  bonds_payable = Column(DECIMAL(20, 4), comment="应付债券")
  deferred_tax_liab = Column(DECIMAL(20, 4), comment="递延所得税负债")

  # 核心权益项
  total_equity = Column(DECIMAL(20, 4), comment="所有者权益合计")
  cap_stk = Column(DECIMAL(20, 4), comment="实收资本(或股本)")
  cap_rsrv = Column(DECIMAL(20, 4), comment="资本公积")
  surplus_rsrv = Column(DECIMAL(20, 4), comment="盈余公积")
  undistributed_profit = Column(DECIMAL(20, 4), comment="未分配利润")
  minority_int = Column(DECIMAL(20, 4), comment="少数股东权益")
  tot_shrhldr_eqy_excl_min_int = Column(DECIMAL(20, 4), comment="归母股东权益合计")
  tot_liab_shrhldr_eqy = Column(DECIMAL(20, 4), comment="负债和股东权益总计")


class FinancialIncomeStatement(Base, TimestampMixin):
  """利润表"""

  __tablename__ = "financial_income_statement"
  __table_args__ = (
    UniqueConstraint(
      "stock_code",
      "report_date",
      name="uq_financial_income_statement_stock_report",
    ),
  )

  id = Column(Integer, primary_key=True, autoincrement=True)
  stock_code = Column(String(20), nullable=False, index=True, comment="标的代码")
  report_date = Column(Date, nullable=False, comment="报告截止日")
  announce_date = Column(Date, comment="公告日期")

  # 收入相关
  revenue = Column(DECIMAL(20, 4), comment="营业总收入")
  revenue_inc = Column(DECIMAL(20, 4), comment="营业收入")
  total_operating_cost = Column(DECIMAL(20, 4), comment="营业总成本")
  total_expense = Column(DECIMAL(20, 4), comment="营业成本")

  # 费用相关
  sale_expense = Column(DECIMAL(20, 4), comment="销售费用")
  less_gerl_admin_exp = Column(DECIMAL(20, 4), comment="管理费用")
  financial_expense = Column(DECIMAL(20, 4), comment="财务费用")
  research_expenses = Column(DECIMAL(20, 4), comment="研发费用")
  less_impair_loss_assets = Column(DECIMAL(20, 4), comment="资产减值损失")

  # 利润相关
  plus_net_invest_inc = Column(DECIMAL(20, 4), comment="投资收益")
  incl_inc_invest_assoc_jv_entp = Column(DECIMAL(20, 4), comment="联营企业和合营企业投资收益")
  change_income_fair_value = Column(DECIMAL(20, 4), comment="公允价值变动收益")
  oper_profit = Column(DECIMAL(20, 4), comment="营业利润")
  plus_non_oper_rev = Column(DECIMAL(20, 4), comment="营业外收入")
  less_non_oper_exp = Column(DECIMAL(20, 4), comment="营业外支出")
  tot_profit = Column(DECIMAL(20, 4), comment="利润总额")
  inc_tax = Column(DECIMAL(20, 4), comment="所得税费用")
  net_profit_incl_min_int_inc = Column(DECIMAL(20, 4), comment="净利润")
  net_profit_excl_min_int_inc = Column(DECIMAL(20, 4), comment="归母净利润")
  minority_int_inc = Column(DECIMAL(20, 4), comment="少数股东损益")
  net_profit_incl_min_int_inc_after = Column(DECIMAL(20, 4), comment="扣非净利润")

  # 每股指标
  s_fa_eps_basic = Column(DECIMAL(10, 4), comment="基本每股收益")
  s_fa_eps_diluted = Column(DECIMAL(10, 4), comment="稀释每股收益")

  # 综合收益
  other_compreh_inc = Column(DECIMAL(20, 4), comment="其他综合收益")
  total_income = Column(DECIMAL(20, 4), comment="综合收益总额")


class FinancialCashFlow(Base, TimestampMixin):
  """现金流量表"""

  __tablename__ = "financial_cash_flow"
  __table_args__ = (
    UniqueConstraint(
      "stock_code",
      "report_date",
      name="uq_financial_cash_flow_stock_report",
    ),
  )

  id = Column(Integer, primary_key=True, autoincrement=True)
  stock_code = Column(String(20), nullable=False, index=True, comment="标的代码")
  report_date = Column(Date, nullable=False, comment="报告截止日")
  announce_date = Column(Date, comment="公告日期")

  # 经营活动
  goods_sale_and_service_render_cash = Column(DECIMAL(20, 4), comment="销售商品、提供劳务收到的现金")
  tax_levy_refund = Column(DECIMAL(20, 4), comment="收到的税费与返还")
  other_cash_recp_ral_oper_act = Column(DECIMAL(20, 4), comment="收到的其他与经营活动有关的现金")
  stot_cash_inflows_oper_act = Column(DECIMAL(20, 4), comment="经营活动现金流入小计")
  goods_and_services_cash_paid = Column(DECIMAL(20, 4), comment="购买商品、接受劳务支付的现金")
  cash_pay_beh_empl = Column(DECIMAL(20, 4), comment="支付给职工以及为职工支付的现金")
  pay_all_typ_tax = Column(DECIMAL(20, 4), comment="支付的各项税费")
  other_cash_pay_ral_oper_act = Column(DECIMAL(20, 4), comment="支付其他与经营活动有关的现金")
  stot_cash_outflows_oper_act = Column(DECIMAL(20, 4), comment="经营活动现金流出小计")
  net_cash_flows_oper_act = Column(DECIMAL(20, 4), comment="经营活动产生的现金流量净额")

  # 投资活动
  cash_recp_disp_withdrwl_invest = Column(DECIMAL(20, 4), comment="收回投资所收到的现金")
  cash_recp_return_invest = Column(DECIMAL(20, 4), comment="取得投资收益所收到的现金")
  net_cash_recp_disp_fiolta = Column(DECIMAL(20, 4), comment="处置固定资产等收到的现金")
  other_cash_recp_ral_inv_act = Column(DECIMAL(20, 4), comment="收到的其他与投资活动有关的现金")
  stot_cash_inflows_inv_act = Column(DECIMAL(20, 4), comment="投资活动现金流入小计")
  cash_pay_acq_const_fiolta = Column(DECIMAL(20, 4), comment="购建固定资产等支付的现金")
  cash_paid_invest = Column(DECIMAL(20, 4), comment="投资支付的现金")
  stot_cash_outflows_inv_act = Column(DECIMAL(20, 4), comment="投资活动现金流出小计")
  net_cash_flows_inv_act = Column(DECIMAL(20, 4), comment="投资活动产生的现金流量净额")

  # 筹资活动
  cash_recp_cap_contrib = Column(DECIMAL(20, 4), comment="吸收投资收到的现金")
  cash_recp_borrow = Column(DECIMAL(20, 4), comment="取得借款收到的现金")
  proc_issue_bonds = Column(DECIMAL(20, 4), comment="发行债券收到的现金")
  other_cash_recp_ral_fnc_act = Column(DECIMAL(20, 4), comment="收到其他与筹资活动有关的现金")
  stot_cash_inflows_fnc_act = Column(DECIMAL(20, 4), comment="筹资活动现金流入小计")
  cash_prepay_amt_borr = Column(DECIMAL(20, 4), comment="偿还债务支付现金")
  cash_pay_dist_dpcp_int_exp = Column(DECIMAL(20, 4), comment="分配股利、利润或偿付利息支付的现金")
  other_cash_pay_ral_fnc_act = Column(DECIMAL(20, 4), comment="支付其他与筹资的现金")
  stot_cash_outflows_fnc_act = Column(DECIMAL(20, 4), comment="筹资活动现金流出小计")
  net_cash_flows_fnc_act = Column(DECIMAL(20, 4), comment="筹资活动产生的现金流量净额")

  # 汇总
  eff_fx_flu_cash = Column(DECIMAL(20, 4), comment="汇率变动对现金的影响")
  net_incr_cash_cash_equ = Column(DECIMAL(20, 4), comment="现金及现金等价物净增加额")
  cash_cash_equ_beg_period = Column(DECIMAL(20, 4), comment="期初现金及现金等价物余额")
  cash_cash_equ_end_period = Column(DECIMAL(20, 4), comment="期末现金及现金等价物余额")


class FinancialCapital(Base, TimestampMixin):
  """股本结构表"""

  __tablename__ = "financial_capital"
  __table_args__ = (
    UniqueConstraint(
      "stock_code",
      "report_date",
      name="uq_financial_capital_stock_report",
    ),
  )

  id = Column(Integer, primary_key=True, autoincrement=True)
  stock_code = Column(String(20), nullable=False, index=True, comment="标的代码")
  report_date = Column(Date, nullable=False, comment="报告截止日")
  announce_date = Column(Date, comment="公告日期")

  total_capital = Column(DECIMAL(20, 2), comment="总股本")
  circulating_capital = Column(DECIMAL(20, 2), comment="流通A股")
  restrict_circulating_capital = Column(DECIMAL(20, 2), comment="限售流通股")
  free_float_capital = Column(DECIMAL(20, 2), comment="自由流通股本")


class FinancialHolderNum(Base, TimestampMixin):
  """股东数表"""

  __tablename__ = "financial_holder_num"

  id = Column(Integer, primary_key=True, autoincrement=True)
  stock_code = Column(String(20), nullable=False, index=True, comment="标的代码")
  report_date = Column(Date, nullable=False, comment="报告截止日")
  announce_date = Column(Date, comment="公告日期")

  holder_num = Column(Integer, comment="股东总数")
  a_holder_num = Column(Integer, comment="A股股东数")
  b_holder_num = Column(Integer, comment="B股股东数")
  h_holder_num = Column(Integer, comment="H股股东数")
  holder_num_change = Column(DECIMAL(10, 4), comment="股东数变化率")


class FinancialShareholder(Base, TimestampMixin):
  """十大股东/流通股东表"""

  __tablename__ = "financial_shareholder"

  id = Column(Integer, primary_key=True, autoincrement=True)
  stock_code = Column(String(20), nullable=False, index=True, comment="标的代码")
  report_date = Column(Date, nullable=False, comment="报告截止日")
  announce_date = Column(Date, comment="公告日期")

  holder_type = Column(String(20), nullable=False, comment="类型(Top10Holder/Top10FlowHolder)")
  holder_name = Column(String(255), comment="股东名称")
  holder_type_desc = Column(String(50), comment="股东类型")
  quantity = Column(DECIMAL(20, 2), comment="持股数量")
  ratio = Column(DECIMAL(10, 4), comment="持股比例(%)")
  change_reason = Column(String(100), comment="变动原因")
  share_nature = Column(String(100), comment="股份性质")
  rank = Column(Integer, comment="排名")
