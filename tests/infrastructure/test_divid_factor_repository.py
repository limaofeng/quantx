from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from quantx_infrastructure.models.divid_factor import DividFactor
from quantx_infrastructure.repositories.divid_factor_repository import (
  DividFactorRepository,
  divid_factor_rows_sha256,
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


def _persisted_row(*, interest: Decimal = Decimal("17.0250")):
  return (
    "600519.SH",
    datetime(2020, 6, 24, 8),
    "20200624",
    interest,
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("1.011677"),
  )


def _assert_write_lock_is_first(session) -> None:
  assert "pg_advisory_xact_lock" in str(session.execute.await_args_list[0].args[0])


def _assert_snapshot_certification_invalidated(session) -> None:
  statements = [
    str(call.args[0]).lower() for call in session.execute.await_args_list[-2:]
  ]
  assert statements[0].startswith("update indicator_snapshots")
  assert "calculation_version" in statements[0]
  assert statements[1].startswith("update daily_signal_runs")
  assert "signal_version" in statements[1]
  assert "status" in statements[1]
  assert "warnings" in statements[1]


@pytest.mark.asyncio
async def test_save_serializes_with_authoritative_replacement_writers():
  session = MagicMock()
  session.execute = AsyncMock(return_value=FakeResult())
  session.commit = AsyncMock()
  session.rollback = AsyncMock()
  session.refresh = AsyncMock()
  repository = DividFactorRepository(session)

  result = await repository.save(_factor())

  assert result.stock_code == "600519.SH"
  _assert_write_lock_is_first(session)
  _assert_snapshot_certification_invalidated(session)
  assert len(session.execute.await_args_list) == 3
  session.add.assert_called_once()
  session.commit.assert_awaited_once()
  session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_bulk_save_serializes_with_authoritative_replacement_writers():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(),
    FakeResult(),
    FakeResult(),
    FakeResult(),
  ]
  repository = DividFactorRepository(session)

  assert await repository.bulk_save([_factor()]) == 1

  _assert_write_lock_is_first(session)
  _assert_snapshot_certification_invalidated(session)
  assert len(session.execute.await_args_list) == 4
  session.commit.assert_awaited_once()
  session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_factor_write_rolls_back_when_snapshot_invalidation_fails():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(),
    FakeResult(),
    RuntimeError("invalidation failed"),
  ]
  repository = DividFactorRepository(session)

  with pytest.raises(RuntimeError, match="invalidation failed"):
    await repository.bulk_save([_factor()])

  _assert_write_lock_is_first(session)
  session.commit.assert_not_awaited()
  session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_serializes_with_authoritative_replacement_writers():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(),
    FakeResult(rowcount=3),
    FakeResult(),
    FakeResult(),
  ]
  repository = DividFactorRepository(session)

  assert await repository.delete_by_stock_code("600519.SH") == 3

  _assert_write_lock_is_first(session)
  _assert_snapshot_certification_invalidated(session)
  assert len(session.execute.await_args_list) == 4
  session.commit.assert_awaited_once()
  session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_range_commits_only_after_exact_key_verification():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(),
    FakeResult(one=(2, "20200624", "20210625")),
    FakeResult(rowcount=2),
    FakeResult(),
    FakeResult(all_rows=[_persisted_row()]),
    FakeResult(),
    FakeResult(),
  ]
  repository = DividFactorRepository(session)

  result = await repository.replace_range(
    [_factor()],
    stock_codes=["000001.SZ", "600519.SH"],
    start_ex_date="20200313",
    end_ex_date="20260729",
  )

  assert result["prior_count"] == 2
  assert result["deleted_count"] == 2
  assert result["inserted_count"] == result["verified_count"] == 1
  assert result["audit_schema_version"] == 2
  assert len(result["stock_codes_sha256"]) == 64
  assert result["source_sha256"] == result["persisted_sha256"]
  assert set(result["code_audits"]) == {"000001.SZ", "600519.SH"}
  assert result["code_audits"]["000001.SZ"] == {
    "record_count": 0,
    "source_sha256": divid_factor_rows_sha256([]),
    "persisted_sha256": divid_factor_rows_sha256([]),
  }
  assert result["code_audits"]["600519.SH"]["record_count"] == 1
  assert (
    result["code_audits"]["600519.SH"]["source_sha256"]
    == result["code_audits"]["600519.SH"]["persisted_sha256"]
  )
  _assert_write_lock_is_first(session)
  _assert_snapshot_certification_invalidated(session)
  session.commit.assert_awaited_once()
  session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_range_proves_an_authoritative_empty_result():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(),
    FakeResult(one=(2, "20200624", "20210625")),
    FakeResult(rowcount=2),
    FakeResult(all_rows=[]),
    FakeResult(),
    FakeResult(),
  ]
  repository = DividFactorRepository(session)

  result = await repository.replace_range(
    [],
    stock_codes=["000300.SH"],
    start_ex_date="20200313",
    end_ex_date="20260729",
  )

  assert result["prior_count"] == result["deleted_count"] == 2
  assert result["inserted_count"] == result["verified_count"] == 0
  assert result["source_sha256"] == result["persisted_sha256"]
  assert len(result["persisted_sha256"]) == 64
  assert result["code_audits"]["000300.SH"] == {
    "record_count": 0,
    "source_sha256": divid_factor_rows_sha256([]),
    "persisted_sha256": divid_factor_rows_sha256([]),
  }
  _assert_snapshot_certification_invalidated(session)
  session.commit.assert_awaited_once()
  session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_range_rolls_back_on_verification_mismatch():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(),
    FakeResult(one=(1, "20200624", "20200624")),
    FakeResult(rowcount=1),
    FakeResult(),
    FakeResult(all_rows=[]),
  ]
  repository = DividFactorRepository(session)

  with pytest.raises(RuntimeError, match="exact-row"):
    await repository.replace_range(
      [_factor()],
      stock_codes=["600519.SH"],
      start_ex_date="20200313",
      end_ex_date="20260729",
    )

  session.commit.assert_not_awaited()
  session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_replace_range_rolls_back_when_values_differ_for_same_key():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(),
    FakeResult(one=(1, "20200624", "20200624")),
    FakeResult(rowcount=1),
    FakeResult(),
    FakeResult(all_rows=[_persisted_row(interest=Decimal("99.0000"))]),
  ]
  repository = DividFactorRepository(session)

  with pytest.raises(RuntimeError, match="exact-row"):
    await repository.replace_range(
      [_factor()],
      stock_codes=["600519.SH"],
      start_ex_date="20200313",
      end_ex_date="20260729",
    )

  session.commit.assert_not_awaited()
  session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_replace_range_rejects_null_numeric_content():
  session = AsyncMock()
  session.execute.side_effect = [
    FakeResult(),
    FakeResult(one=(1, "20200624", "20200624")),
    FakeResult(rowcount=1),
    FakeResult(),
    FakeResult(all_rows=[_persisted_row(interest=None)]),
  ]
  repository = DividFactorRepository(session)

  with pytest.raises(ValueError, match="must not be null"):
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
