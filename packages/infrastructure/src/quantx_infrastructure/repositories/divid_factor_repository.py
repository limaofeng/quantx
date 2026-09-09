"""
除权除息/复权因子数据仓储（PostgreSQL，异步）
"""

import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, List, Optional, Sequence

from quantx_domain.indicators import INDICATOR_VERSION
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.daily_signal_run import DailySignalRun
from quantx_infrastructure.models.divid_factor import DividFactor, DividFactorTable
from quantx_infrastructure.models.indicator_snapshot import IndicatorSnapshot

FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")
DIVID_FACTOR_WRITE_LOCK_KEY = int.from_bytes(
  # Keep the deployed replacement key so a rolling restart cannot split the
  # lock domain between an older authoritative writer and these full writers.
  hashlib.sha256(b"quantx:divid-factor-replacement-v1").digest()[:8],
  byteorder="big",
  signed=True,
)


def canonical_divid_factor_rows(
  rows: Iterable[Sequence[Any]],
) -> list[tuple[str, ...]]:
  """Return the storage-precision identity for dividend-factor rows."""

  canonical: list[tuple[str, ...]] = []
  for row in rows:
    if len(row) != 10:
      raise ValueError("divid factor audit row must contain exactly 10 values")
    factor_time = row[1]
    if not isinstance(factor_time, datetime):
      raise ValueError("divid factor audit time must be a datetime")
    if factor_time.tzinfo is not None:
      raise ValueError("divid factor audit time must be naive Shanghai time")
    if any(value is None for value in row[3:]):
      raise ValueError("divid factor audit numeric values must not be null")
    canonical.append(
      (
        str(row[0]).strip().upper(),
        factor_time.isoformat(timespec="microseconds"),
        str(row[2]).strip(),
        format(
          Decimal(str(row[3] if row[3] is not None else 0)).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          "f",
        ),
        format(
          Decimal(str(row[4] if row[4] is not None else 0)).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          "f",
        ),
        format(
          Decimal(str(row[5] if row[5] is not None else 0)).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          "f",
        ),
        format(
          Decimal(str(row[6] if row[6] is not None else 0)).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          "f",
        ),
        format(
          Decimal(str(row[7] if row[7] is not None else 0)).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          "f",
        ),
        format(
          Decimal(str(row[8] if row[8] is not None else 0)).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          "f",
        ),
        format(
          Decimal(str(row[9] if row[9] is not None else 0)).quantize(
            SIX_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          "f",
        ),
      )
    )
  return sorted(canonical, key=lambda row: (row[0], row[2], row[1]))


def divid_factor_rows_sha256(rows: Iterable[Sequence[Any]]) -> str:
  """Hash exact dividend-factor content at PostgreSQL storage precision."""

  encoded = json.dumps(
    canonical_divid_factor_rows(rows),
    ensure_ascii=True,
    separators=(",", ":"),
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def divid_factor_codes_sha256(stock_codes: Iterable[str]) -> str:
  """Hash the exact sorted replacement universe."""

  canonical = "\n".join(
    sorted({str(code).strip().upper() for code in stock_codes if str(code).strip()})
  )
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DividFactorRepository:
  """复权因子数据仓储（PostgreSQL，异步）"""

  def __init__(self, db_session: AsyncSession):
    self.db = db_session

  async def _acquire_write_lock(self) -> None:
    """Serialize every legacy-table mutation in the current transaction."""

    await self.db.execute(
      select(func.pg_advisory_xact_lock(DIVID_FACTOR_WRITE_LOCK_KEY))
    )

  async def _invalidate_published_snapshots(self) -> None:
    """Revoke every certified snapshot in the same factor-write transaction."""

    await self.db.execute(
      update(IndicatorSnapshot)
      .where(IndicatorSnapshot.calculation_version == INDICATOR_VERSION)
      .values(calculation_version=None)
    )
    await self.db.execute(
      update(DailySignalRun)
      .where(
        DailySignalRun.signal_version == INDICATOR_VERSION,
        DailySignalRun.status.in_(["success", "scoped_success"]),
      )
      .values(
        status="invalidated",
        warnings="复权因子已更新，需要重新计算日级指标快照",
      )
    )

  def _to_model(self, db_factor: DividFactorTable) -> DividFactor:
    return DividFactor(
      id=db_factor.id,
      stock_code=db_factor.stock_code,
      time=db_factor.time,
      ex_date=db_factor.ex_date,
      interest=db_factor.interest,
      stock_bonus=db_factor.stock_bonus,
      stock_gift=db_factor.stock_gift,
      allot_num=db_factor.allot_num,
      allot_price=db_factor.allot_price,
      gugai=db_factor.gugai,
      dr=db_factor.dr,
      created_at=db_factor.created_at,
      updated_at=db_factor.updated_at,
    )

  async def save(self, factor: DividFactor) -> DividFactor:
    """
    保存单个复权因子

    Args:
        factor: 复权因子对象

    Returns:
        保存后的复权因子对象
    """
    db_factor = DividFactorTable(
      stock_code=factor.stock_code,
      time=factor.time,
      ex_date=factor.ex_date,
      interest=factor.interest,
      stock_bonus=factor.stock_bonus,
      stock_gift=factor.stock_gift,
      allot_num=factor.allot_num,
      allot_price=factor.allot_price,
      gugai=factor.gugai,
      dr=factor.dr,
    )
    try:
      await self._acquire_write_lock()
      self.db.add(db_factor)
      await self._invalidate_published_snapshots()
      await self.db.commit()
    except Exception:
      await self.db.rollback()
      raise
    await self.db.refresh(db_factor)

    factor.id = db_factor.id
    factor.created_at = db_factor.created_at
    factor.updated_at = db_factor.updated_at
    return factor

  async def bulk_save(self, factors: List[DividFactor]) -> int:
    """
    批量保存复权因子

    Args:
        factors: 复权因子列表

    Returns:
        保存的记录数
    """
    if not factors:
      return 0

    payload = [
      {
        "stock_code": f.stock_code,
        "time": f.time,
        "ex_date": f.ex_date,
        "interest": f.interest,
        "stock_bonus": f.stock_bonus,
        "stock_gift": f.stock_gift,
        "allot_num": f.allot_num,
        "allot_price": f.allot_price,
        "gugai": f.gugai,
        "dr": f.dr,
      }
      for f in factors
    ]
    try:
      # This append-only method is retained for the two legacy service entry
      # points. It must share the authoritative replacement lock even though
      # new QMT ingestion always uses ``replace_range``.
      await self._acquire_write_lock()
      await self.db.execute(insert(DividFactorTable), payload)
      await self._invalidate_published_snapshots()
      await self.db.commit()
    except Exception:
      await self.db.rollback()
      raise
    return len(payload)

  async def replace_range(
    self,
    factors: List[DividFactor],
    *,
    stock_codes: List[str],
    start_ex_date: str,
    end_ex_date: str,
  ) -> dict[str, Any]:
    """Atomically replace an authoritative QMT factor window.

    ``divid_factors`` predates a uniqueness constraint, so a delete followed by
    a plain insert is the only idempotent write available on existing
    installations. Both operations and the exact-key verification are kept in
    one transaction; any mismatch rolls the deletion back.
    """
    codes = sorted(
      {str(code).strip().upper() for code in stock_codes if str(code).strip()}
    )
    if not codes:
      raise ValueError("stock_codes must not be empty")
    for label, value in (
      ("start_ex_date", start_ex_date),
      ("end_ex_date", end_ex_date),
    ):
      if len(value) != 8 or not value.isdigit():
        raise ValueError(f"{label} must be YYYYMMDD")
    if end_ex_date < start_ex_date:
      raise ValueError("end_ex_date precedes start_ex_date")

    expected_keys: list[tuple[str, str]] = []
    expected_rows: list[tuple[Any, ...]] = []
    payload: list[dict[str, Any]] = []
    for factor in factors:
      code = str(factor.stock_code or "").strip().upper()
      ex_date = str(factor.ex_date or "").strip()
      if code not in codes:
        raise ValueError(f"factor code is outside replacement scope: {code}")
      if ex_date < start_ex_date or ex_date > end_ex_date:
        raise ValueError(
          f"factor ex_date is outside replacement scope: {code}/{ex_date}"
        )
      if factor.time is None or factor.dr is None or factor.dr <= 0:
        raise ValueError(f"factor time/dr is invalid: {code}/{ex_date}")
      expected_keys.append((code, ex_date))
      expected_rows.append(
        (
          code,
          factor.time,
          ex_date,
          factor.interest,
          factor.stock_bonus,
          factor.stock_gift,
          factor.allot_num,
          factor.allot_price,
          factor.gugai,
          factor.dr,
        )
      )
      payload.append(
        {
          "stock_code": code,
          "time": factor.time,
          "ex_date": ex_date,
          "interest": factor.interest,
          "stock_bonus": factor.stock_bonus,
          "stock_gift": factor.stock_gift,
          "allot_num": factor.allot_num,
          "allot_price": factor.allot_price,
          "gugai": factor.gugai,
          "dr": factor.dr,
        }
      )
    if len(set(expected_keys)) != len(expected_keys):
      raise ValueError("replacement payload contains duplicate code/ex_date keys")

    scope = (
      DividFactorTable.stock_code.in_(codes),
      DividFactorTable.ex_date >= start_ex_date,
      DividFactorTable.ex_date <= end_ex_date,
    )
    try:
      # The legacy table has no uniqueness constraint.  Serialize every
      # authoritative replacement transaction so overlapping scopes cannot
      # both validate against their own uncommitted rows and then form a union.
      await self._acquire_write_lock()
      prior = (
        await self.db.execute(
          select(
            func.count(DividFactorTable.id),
            func.min(DividFactorTable.ex_date),
            func.max(DividFactorTable.ex_date),
          ).where(*scope)
        )
      ).one()
      deleted = await self.db.execute(delete(DividFactorTable).where(*scope))
      if int(deleted.rowcount or 0) != int(prior[0] or 0):
        raise RuntimeError(
          "divid factor replacement delete-count verification failed: "
          f"expected={int(prior[0] or 0)} "
          f"actual={int(deleted.rowcount or 0)}"
        )
      if payload:
        await self.db.execute(insert(DividFactorTable), payload)
      persisted_rows = (
        await self.db.execute(
          select(
            DividFactorTable.stock_code,
            DividFactorTable.time,
            DividFactorTable.ex_date,
            DividFactorTable.interest,
            DividFactorTable.stock_bonus,
            DividFactorTable.stock_gift,
            DividFactorTable.allot_num,
            DividFactorTable.allot_price,
            DividFactorTable.gugai,
            DividFactorTable.dr,
          )
          .where(*scope)
          .order_by(
            DividFactorTable.stock_code.asc(),
            DividFactorTable.ex_date.asc(),
          )
        )
      ).all()
      actual_rows = [tuple(row) for row in persisted_rows]
      canonical_expected = canonical_divid_factor_rows(expected_rows)
      canonical_actual = canonical_divid_factor_rows(actual_rows)
      if canonical_actual != canonical_expected:
        raise RuntimeError(
          "divid factor replacement exact-row verification failed: "
          f"expected={len(canonical_expected)} actual={len(canonical_actual)}"
        )
      await self._invalidate_published_snapshots()
      await self.db.commit()
    except Exception:
      await self.db.rollback()
      raise

    source_sha256 = divid_factor_rows_sha256(expected_rows)
    persisted_sha256 = divid_factor_rows_sha256(actual_rows)
    expected_rows_by_code = {code: [] for code in codes}
    actual_rows_by_code = {code: [] for code in codes}
    for row in expected_rows:
      expected_rows_by_code[str(row[0]).strip().upper()].append(row)
    for row in actual_rows:
      actual_rows_by_code[str(row[0]).strip().upper()].append(row)
    code_audits = {
      code: {
        "record_count": len(actual_rows_by_code[code]),
        "source_sha256": divid_factor_rows_sha256(expected_rows_by_code[code]),
        "persisted_sha256": divid_factor_rows_sha256(actual_rows_by_code[code]),
      }
      for code in codes
    }
    return {
      "audit_schema_version": 2,
      "stock_count": len(codes),
      "stock_codes_sha256": divid_factor_codes_sha256(codes),
      "prior_count": int(prior[0] or 0),
      "prior_min_ex_date": str(prior[1] or ""),
      "prior_max_ex_date": str(prior[2] or ""),
      "deleted_count": int(deleted.rowcount or 0),
      "inserted_count": len(payload),
      "verified_count": len(actual_rows),
      "source_sha256": source_sha256,
      "persisted_sha256": persisted_sha256,
      "code_audits": code_audits,
      "start_ex_date": start_ex_date,
      "end_ex_date": end_ex_date,
    }

  async def verify_replaced_range(
    self,
    factors: List[DividFactor],
    *,
    stock_codes: List[str],
    start_ex_date: str,
    end_ex_date: str,
    audit: dict[str, Any],
  ) -> None:
    """Recheck a committed replacement against its original upload, without writes.

    Include the entire requested window, including codes with no source rows,
    so extra/duplicate rows cannot hide behind an exact-key lookup. The caller
    validates the audit schema/scope and owns the transaction holding this lock.
    """
    columns = (
      "stock_code",
      "time",
      "ex_date",
      "interest",
      "stock_bonus",
      "stock_gift",
      "allot_num",
      "allot_price",
      "gugai",
      "dr",
    )
    expected = [tuple(getattr(factor, name) for name in columns) for factor in factors]
    if divid_factor_rows_sha256(expected) != audit["source_sha256"]:
      raise RuntimeError("divid factor recovery source digest mismatch")
    by_code = {code: [] for code in stock_codes}
    for row in expected:
      by_code[row[0]].append(row)
    for code, rows in by_code.items():
      proof = audit["code_audits"][code]
      if len(rows) != proof["record_count"] or (
        divid_factor_rows_sha256(rows) != proof["source_sha256"]
      ):
        raise RuntimeError("divid factor recovery per-code source mismatch")

    # Every factor writer already takes this transaction lock. Keep all bounded
    # reads on one stable version, including empty ranges (row locks cannot
    # protect missing rows). No commit, rewrite or snapshot invalidation here.
    await self._acquire_write_lock()
    for offset in range(0, len(stock_codes), 64):
      codes = stock_codes[offset : offset + 64]
      source_rows = [row for code in codes for row in by_code[code]]
      actual = (
        await self.db.execute(
          select(*(getattr(DividFactorTable, name) for name in columns))
          .where(
            DividFactorTable.stock_code.in_(codes),
            DividFactorTable.ex_date >= start_ex_date,
            DividFactorTable.ex_date <= end_ex_date,
          )
          .order_by(DividFactorTable.stock_code, DividFactorTable.ex_date)
          .limit(len(source_rows) + 1)
        )
      ).all()
      if canonical_divid_factor_rows(actual) != canonical_divid_factor_rows(
        source_rows
      ):
        raise RuntimeError("divid factor recovery persisted content mismatch")

  async def find_by_stock_code(
    self,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
  ) -> List[DividFactor]:
    """
    根据股票代码查询复权因子

    Args:
        stock_code: 股票代码
        start_time: 开始时间
        end_time: 结束时间
        limit: 限制数量

    Returns:
        复权因子列表
    """
    query = select(DividFactorTable).filter(DividFactorTable.stock_code == stock_code)

    if start_time:
      query = query.filter(DividFactorTable.time >= start_time)
    if end_time:
      query = query.filter(DividFactorTable.time <= end_time)

    query = query.order_by(DividFactorTable.time.asc())
    if limit:
      query = query.limit(limit)

    result = await self.db.execute(query)
    db_factors = result.scalars().all()
    return [self._to_model(factor) for factor in db_factors]

  async def find_all(
    self,
    filters: dict = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    order_by: str = "time ASC",
  ) -> List[DividFactor]:
    """
    查询复权因子

    Args:
        filters: 过滤条件
        start_time: 开始时间
        end_time: 结束时间
        limit: 限制数量
        order_by: 排序方式

    Returns:
        复权因子列表
    """
    query = select(DividFactorTable)

    if filters and "stock_code" in filters:
      query = query.filter(DividFactorTable.stock_code == filters["stock_code"])

    if start_time:
      query = query.filter(DividFactorTable.time >= start_time)
    if end_time:
      query = query.filter(DividFactorTable.time <= end_time)

    if order_by == "time ASC":
      query = query.order_by(DividFactorTable.time.asc())
    elif order_by == "time DESC":
      query = query.order_by(DividFactorTable.time.desc())

    if limit:
      query = query.limit(limit)

    result = await self.db.execute(query)
    db_factors = result.scalars().all()
    return [self._to_model(factor) for factor in db_factors]

  async def delete_by_stock_code(self, stock_code: str) -> int:
    """
    删除指定股票的复权因子

    Args:
        stock_code: 股票代码

    Returns:
        删除的记录数
    """
    try:
      await self._acquire_write_lock()
      result = await self.db.execute(
        delete(DividFactorTable).filter(DividFactorTable.stock_code == stock_code)
      )
      await self._invalidate_published_snapshots()
      await self.db.commit()
    except Exception:
      await self.db.rollback()
      raise
    return result.rowcount
