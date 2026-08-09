from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.models.divid_factor import DividFactor
from quantx_infrastructure.repositories.divid_factor_repository import (
  DividFactorRepository,
)


class FakeResult:
  def __init__(self, *, one=None, all_rows=None, rowcount=0):
    self._one = one
    self._all = all_rows or []
    self.rowcount = rowcount

  def one(self):
    return self._one

  def all(self):
    return self._all


def _factor():
  return DividFactor(
    stock_code="600519.SH",
    time=datetime(2020, 6, 24, 8),
    ex_date="20200624",
    interest=Decimal("17.025"),
    dr=Decimal("1.011677"),
  )


@pytest.mark.asyncio
async def test_replace_range_commits_only_after_exact_key_verification():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(one=(2, "20200624", "20210625")),
    FakeResult(rowcount=2),
    FakeResult(),
    FakeResult(all_rows=[("600519.SH", "20200624")]),
  ]
  repository = DividFactorRepository(session)

  result = await repository.replace_range(
    [_factor()],
    stock_codes=["600519.SH"],
    start_ex_date="20200313",
    end_ex_date="20260729",
  )

  assert result["prior_count"] == 2
  assert result["deleted_count"] == 2
  assert result["inserted_count"] == result["verified_count"] == 1
  session.commit.assert_awaited_once()
  session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_range_rolls_back_on_verification_mismatch():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(one=(1, "20200624", "20200624")),
    FakeResult(rowcount=1),
    FakeResult(),
    FakeResult(all_rows=[]),
  ]
  repository = DividFactorRepository(session)

  with pytest.raises(RuntimeError, match="exact-key"):
    await repository.replace_range(
      [_factor()],
      stock_codes=["600519.SH"],
      start_ex_date="20200313",
      end_ex_date="20260729",
    )

  session.commit.assert_not_awaited()
  session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_replace_range_rejects_out_of_scope_rows_before_sql():
  session = AsyncMock()
  repository = DividFactorRepository(session)
  factor = _factor()
  factor.stock_code = "000001.SZ"

  with pytest.raises(ValueError, match="outside replacement scope"):
    await repository.replace_range(
      [factor],
      stock_codes=["600519.SH"],
      start_ex_date="20200313",
      end_ex_date="20260729",
    )

  session.execute.assert_not_awaited()
