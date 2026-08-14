"""Financial statement persistence and derived-metric convergence."""

import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable

import pandas as pd
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.financial import (
  FinancialBalanceSheet,
  FinancialCapital,
  FinancialCashFlow,
  FinancialIncomeStatement,
)
from quantx_infrastructure.services.financial_metric_snapshot_service import (
  FinancialMetricSnapshotService,
)
from quantx_infrastructure.services.financial_report_date import (
  normalize_financial_report_date,
)

logger = logging.getLogger(__name__)


class FinancialService:
  """Persist the supported XTData financial tables atomically per batch."""

  SUPPORTED_TABLES = ("Balance", "Income", "CashFlow", "Capital")
  UPSERT_CHUNK_SIZE = 250

  def __init__(self, db_session=None, db_factory=get_async_db):
    self.db_session = db_session
    self.db_factory = db_factory

  @staticmethod
  def _parse_date(value: Any) -> date | None:
    if value is None or isinstance(value, bool):
      return None
    try:
      if bool(pd.isna(value)):
        return None
    except (TypeError, ValueError):
      pass
    if isinstance(value, datetime):
      return value.date()
    if isinstance(value, date):
      return value
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
      try:
        parsed = to_pydatetime()
      except Exception:
        parsed = None
      if isinstance(parsed, datetime):
        return parsed.date()

    candidate = str(value).strip()
    if candidate.endswith(".0") and candidate[:-2].isdigit():
      candidate = candidate[:-2]
    if len(candidate) == 8 and candidate.isdigit():
      try:
        return datetime.strptime(candidate, "%Y%m%d").date()
      except ValueError:
        return None
    if candidate.isdigit():
      raw = int(candidate)
      if raw < 100_000_000_000:
        raw *= 1000
      elif raw >= 100_000_000_000_000:
        raw //= 1000
      try:
        return datetime.fromtimestamp(raw / 1000, timezone.utc).date()
      except (OSError, OverflowError, ValueError):
        return None
    try:
      return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
    except ValueError:
      return None

  @classmethod
  def _parse_report_date(cls, value: Any) -> date | None:
    return normalize_financial_report_date(cls._parse_date(value))

  @staticmethod
  def _safe_decimal(value: Any) -> float | None:
    if value is None:
      return None
    try:
      numeric = float(value)
    except (TypeError, ValueError):
      return None
    return numeric if math.isfinite(numeric) else None

  @classmethod
  def _base_record(cls, stock_code: str, row: Any) -> dict[str, Any]:
    report_date = cls._parse_report_date(row.get("m_timetag"))
    if report_date is None:
      raise ValueError(f"财务报告期无效: {stock_code}/{row.get('m_timetag')}")
    return {
      "stock_code": stock_code,
      "report_date": report_date,
      "announce_date": cls._parse_date(row.get("m_anntime")),
    }

  @classmethod
  def _records_for_table(
    cls,
    stock_code: str,
    table: str,
    frame: pd.DataFrame,
  ) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
      record = cls._base_record(stock_code, row)
      if table == "Balance":
        record.update(
          {
            "total_assets": cls._safe_decimal(row.get("tot_assets")),
            "total_current_assets": cls._safe_decimal(row.get("total_current_assets")),
            "total_non_current_assets": cls._safe_decimal(
              row.get("total_non_current_assets")
            ),
            "cash_equivalents": cls._safe_decimal(row.get("cash_equivalents")),
            "tradable_fin_assets": cls._safe_decimal(row.get("tradable_fin_assets")),
            "inventories": cls._safe_decimal(row.get("inventories")),
            "total_liabilities": cls._safe_decimal(row.get("tot_liab")),
            "total_current_liability": cls._safe_decimal(
              row.get("total_current_liability")
            ),
            "non_current_liabilities": cls._safe_decimal(
              row.get("non_current_liabilities")
            ),
            "total_equity": cls._safe_decimal(row.get("total_equity")),
            "tot_shrhldr_eqy_excl_min_int": cls._safe_decimal(
              row.get("tot_shrhldr_eqy_excl_min_int")
            ),
            "minority_int": cls._safe_decimal(row.get("minority_int")),
          }
        )
      elif table == "Income":
        record.update(
          {
            "revenue": cls._safe_decimal(row.get("revenue")),
            "revenue_inc": cls._safe_decimal(row.get("revenue_inc")),
            "total_operating_cost": cls._safe_decimal(row.get("total_operating_cost")),
            "oper_profit": cls._safe_decimal(row.get("oper_profit")),
            "tot_profit": cls._safe_decimal(row.get("tot_profit")),
            "net_profit_incl_min_int_inc": cls._safe_decimal(
              row.get("net_profit_incl_min_int_inc")
            ),
            "net_profit_excl_min_int_inc": cls._safe_decimal(
              row.get("net_profit_excl_min_int_inc")
            ),
            "s_fa_eps_basic": cls._safe_decimal(row.get("s_fa_eps_basic")),
          }
        )
      elif table == "CashFlow":
        record.update(
          {
            "net_cash_flows_oper_act": cls._safe_decimal(
              row.get("net_cash_flows_oper_act")
            ),
            "net_cash_flows_inv_act": cls._safe_decimal(
              row.get("net_cash_flows_inv_act")
            ),
            "net_cash_flows_fnc_act": cls._safe_decimal(
              row.get("net_cash_flows_fnc_act")
            ),
            "net_incr_cash_cash_equ": cls._safe_decimal(
              row.get("net_incr_cash_cash_equ")
            ),
            "cash_cash_equ_end_period": cls._safe_decimal(
              row.get("cash_cash_equ_end_period")
            ),
          }
        )
      elif table == "Capital":
        record.update(
          {
            "total_capital": cls._safe_decimal(row.get("total_capital")),
            "circulating_capital": cls._safe_decimal(row.get("circulating_capital")),
            "restrict_circulating_capital": cls._safe_decimal(
              row.get("restrict_circulating_capital")
            ),
            "free_float_capital": cls._safe_decimal(row.get("freeFloatCapital")),
          }
        )
      else:
        raise ValueError(f"不支持的财务表: {table}")
      records.append(record)
    return records

  @staticmethod
  async def _bulk_upsert(db, model, records: Iterable[dict[str, Any]]) -> int:
    rows = list(records)
    if not rows:
      return 0
    for offset in range(0, len(rows), FinancialService.UPSERT_CHUNK_SIZE):
      chunk = rows[offset : offset + FinancialService.UPSERT_CHUNK_SIZE]
      statement = insert(model).values(chunk)
      excluded = statement.excluded
      supplied_fields = set().union(*(row.keys() for row in chunk))
      update_fields = {
        column.name: func.coalesce(
          getattr(excluded, column.name),
          getattr(model, column.name),
        )
        for column in model.__table__.columns
        if column.name in supplied_fields
        and column.name not in {"id", "stock_code", "report_date", "created_at"}
      }
      statement = statement.on_conflict_do_update(
        index_elements=["stock_code", "report_date"],
        set_=update_fields,
      )
      await db.execute(statement)
    return len(rows)

  async def save_batch_financial_data_with_audit(
    self,
    financial_data_map: Dict[str, Any],
  ) -> dict[str, Any]:
    if not financial_data_map:
      return {
        "rows_received": 0,
        "rows_upserted": 0,
        "rows_rejected": 0,
        "metric_codes_rebuilt": 0,
        "metric_rows_rebuilt": 0,
        "statement_rows_by_code": {},
        "metric_rows_by_code": {},
      }
    if self.db_session is not None:
      return await self._save_with_db(self.db_session, financial_data_map)

    async for db in self.db_factory():
      try:
        return await self._save_with_db(db, financial_data_map)
      except Exception:
        await db.rollback()
        logger.exception("保存财务数据失败")
        raise
    raise RuntimeError("数据库连接不可用")

  async def _save_with_db(
    self,
    db,
    financial_data_map: Dict[str, Any],
  ) -> dict[str, Any]:
    models = {
      "Balance": FinancialBalanceSheet,
      "Income": FinancialIncomeStatement,
      "CashFlow": FinancialCashFlow,
      "Capital": FinancialCapital,
    }
    records_by_table: dict[str, list[dict[str, Any]]] = {
      table: [] for table in self.SUPPORTED_TABLES
    }
    for raw_code, tables in financial_data_map.items():
      stock_code = str(raw_code).strip().upper()
      if not stock_code or not isinstance(tables, dict):
        raise ValueError("财务数据必须按股票代码和表名组织")
      unexpected = sorted(set(tables) - set(self.SUPPORTED_TABLES))
      if unexpected:
        raise ValueError(f"不支持的财务表: {unexpected}")
      for table in self.SUPPORTED_TABLES:
        frame = tables.get(table)
        if frame is None:
          continue
        if not isinstance(frame, pd.DataFrame):
          raise ValueError(f"财务表必须是 DataFrame: {stock_code}/{table}")
        if frame.empty:
          continue
        records_by_table[table].extend(
          self._records_for_table(stock_code, table, frame)
        )

    rows_received = sum(len(rows) for rows in records_by_table.values())
    statement_rows_by_code: dict[str, int] = {}
    for rows in records_by_table.values():
      for row in rows:
        code = str(row["stock_code"])
        statement_rows_by_code[code] = statement_rows_by_code.get(code, 0) + 1
    rows_upserted = 0
    try:
      for table in self.SUPPORTED_TABLES:
        rows_upserted += await self._bulk_upsert(
          db,
          models[table],
          records_by_table[table],
        )
      metric_result = await FinancialMetricSnapshotService(
        db_session=db
      ).rebuild_for_codes(
        list(financial_data_map),
        commit=False,
      )
      await db.commit()
    except Exception:
      await db.rollback()
      raise

    return {
      "rows_received": rows_received,
      "rows_upserted": rows_upserted,
      "rows_rejected": 0,
      "metric_codes_rebuilt": int(metric_result.get("codes", 0)),
      "metric_rows_rebuilt": int(metric_result.get("records", 0)),
      "statement_rows_by_code": statement_rows_by_code,
      "metric_rows_by_code": dict(metric_result.get("metric_rows_by_code") or {}),
    }

  async def save_batch_financial_data(
    self,
    financial_data_map: Dict[str, Any],
  ) -> int:
    """Backward-compatible count-only wrapper."""
    audit = await self.save_batch_financial_data_with_audit(financial_data_map)
    return audit["rows_upserted"]
