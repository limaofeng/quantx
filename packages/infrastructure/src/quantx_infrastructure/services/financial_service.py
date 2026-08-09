"""
财务数据服务层
提供财务报表相关的业务逻辑
"""

import logging
from datetime import date, datetime
from typing import Any, Dict

import pandas as pd

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.repositories.financial_repository import (
    FinancialBalanceSheetRepository,
    FinancialCapitalRepository,
    FinancialCashFlowRepository,
    FinancialIncomeStatementRepository,
)
from quantx_infrastructure.services.financial_metric_snapshot_service import (
    FinancialMetricSnapshotService,
)
from quantx_infrastructure.services.financial_report_date import (
    normalize_financial_report_date,
)

logger = logging.getLogger(__name__)


class FinancialService:
    """财务数据服务"""

    @staticmethod
    def _parse_date(val):
        """将 XTQuant 日期字符串转换为 date 对象"""
        if pd.isna(val) or val is None:
            return None
        if isinstance(val, str):
            try:
                return datetime.strptime(val, "%Y%m%d").date()
            except ValueError:
                return None
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
        if hasattr(val, "date"):
            try:
                parsed_date = val.date()
                if isinstance(parsed_date, date):
                    return parsed_date
            except (TypeError, ValueError):
                return None
        return val

    @classmethod
    def _parse_report_date(cls, val):
        """将财报报告期规范为真实季度末；公告日不得使用此方法。"""
        return normalize_financial_report_date(cls._parse_date(val))

    @staticmethod
    def _safe_decimal(val):
        """安全提取数值"""
        if pd.isna(val) or val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    async def save_batch_financial_data(
        self, financial_data_map: Dict[str, Any]
    ) -> int:
        """
        批量保存财务数据
        
        Args:
            financial_data_map: {stock_code: {table_name: DataFrame}} 格式的数据
            
        Returns:
            保存成功的记录总数
        """
        if not financial_data_map:
            return 0

        total_saved = 0

        async for db in get_async_db():
            try:
                balance_repo = FinancialBalanceSheetRepository(db)
                income_repo = FinancialIncomeStatementRepository(db)
                cashflow_repo = FinancialCashFlowRepository(db)
                capital_repo = FinancialCapitalRepository(db)

                for stock_code, tables in financial_data_map.items():
                    # 处理资产负债表
                    balance_df = tables.get("Balance")
                    if isinstance(balance_df, pd.DataFrame) and not balance_df.empty:
                        for _, row in balance_df.iterrows():
                            await balance_repo.upsert({
                                "stock_code": stock_code,
                                "report_date": self._parse_report_date(row.get("m_timetag")),
                                "announce_date": self._parse_date(row.get("m_anntime")),
                                "total_assets": self._safe_decimal(row.get("tot_assets")),
                                "total_current_assets": self._safe_decimal(row.get("total_current_assets")),
                                "total_non_current_assets": self._safe_decimal(row.get("total_non_current_assets")),
                                "cash_equivalents": self._safe_decimal(row.get("cash_equivalents")),
                                "tradable_fin_assets": self._safe_decimal(row.get("tradable_fin_assets")),
                                "inventories": self._safe_decimal(row.get("inventories")),
                                "total_liabilities": self._safe_decimal(row.get("tot_liab")),
                                "total_current_liability": self._safe_decimal(row.get("total_current_liability")),
                                "non_current_liabilities": self._safe_decimal(row.get("non_current_liabilities")),
                                "total_equity": self._safe_decimal(row.get("total_equity")),
                                "tot_shrhldr_eqy_excl_min_int": self._safe_decimal(row.get("tot_shrhldr_eqy_excl_min_int")),
                                "minority_int": self._safe_decimal(row.get("minority_int")),
                            })
                            total_saved += 1

                    # 处理利润表
                    income_df = tables.get("Income")
                    if isinstance(income_df, pd.DataFrame) and not income_df.empty:
                        for _, row in income_df.iterrows():
                            await income_repo.upsert({
                                "stock_code": stock_code,
                                "report_date": self._parse_report_date(row.get("m_timetag")),
                                "announce_date": self._parse_date(row.get("m_anntime")),
                                "revenue": self._safe_decimal(row.get("revenue")),
                                "revenue_inc": self._safe_decimal(row.get("revenue_inc")),
                                "total_operating_cost": self._safe_decimal(row.get("total_operating_cost")),
                                "oper_profit": self._safe_decimal(row.get("oper_profit")),
                                "tot_profit": self._safe_decimal(row.get("tot_profit")),
                                "net_profit_incl_min_int_inc": self._safe_decimal(row.get("net_profit_incl_min_int_inc")),
                                "net_profit_excl_min_int_inc": self._safe_decimal(row.get("net_profit_excl_min_int_inc")),
                                "s_fa_eps_basic": self._safe_decimal(row.get("s_fa_eps_basic")),
                            })
                            total_saved += 1

                    # 处理现金流量表
                    cashflow_df = tables.get("CashFlow")
                    if isinstance(cashflow_df, pd.DataFrame) and not cashflow_df.empty:
                        for _, row in cashflow_df.iterrows():
                            await cashflow_repo.upsert({
                                "stock_code": stock_code,
                                "report_date": self._parse_report_date(row.get("m_timetag")),
                                "announce_date": self._parse_date(row.get("m_anntime")),
                                "net_cash_flows_oper_act": self._safe_decimal(row.get("net_cash_flows_oper_act")),
                                "net_cash_flows_inv_act": self._safe_decimal(row.get("net_cash_flows_inv_act")),
                                "net_cash_flows_fnc_act": self._safe_decimal(row.get("net_cash_flows_fnc_act")),
                                "net_incr_cash_cash_equ": self._safe_decimal(row.get("net_incr_cash_cash_equ")),
                                "cash_cash_equ_end_period": self._safe_decimal(row.get("cash_cash_equ_end_period")),
                            })
                            total_saved += 1

                    # 处理股本表
                    capital_df = tables.get("Capital")
                    if isinstance(capital_df, pd.DataFrame) and not capital_df.empty:
                        for _, row in capital_df.iterrows():
                            await capital_repo.upsert({
                                "stock_code": stock_code,
                                "report_date": self._parse_report_date(row.get("m_timetag")),
                                "announce_date": self._parse_date(row.get("m_anntime")),
                                "total_capital": self._safe_decimal(row.get("total_capital")),
                                "circulating_capital": self._safe_decimal(row.get("circulating_capital")),
                                "restrict_circulating_capital": self._safe_decimal(row.get("restrict_circulating_capital")),
                                "free_float_capital": self._safe_decimal(row.get("freeFloatCapital")),
                            })
                            total_saved += 1

                await db.commit()

                metric_service = FinancialMetricSnapshotService(db_session=db)
                metric_result = await metric_service.rebuild_for_codes(
                    list(financial_data_map.keys())
                )
                logger.info(
                    "财务指标快照重算完成: codes=%s, records=%s",
                    metric_result.get("codes", 0),
                    metric_result.get("records", 0),
                )
                
            except Exception as e:
                await db.rollback()
                logger.error(f"保存财务数据失败: {e}", exc_info=True)
                raise  # 保留完整异常链

        return total_saved
