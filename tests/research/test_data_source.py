from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from types import SimpleNamespace

import pandas as pd
import pytest
from quantx_infrastructure.repositories.divid_factor_repository import (
  divid_factor_codes_sha256,
  divid_factor_rows_sha256,
)
from quantx_research.data import (
  InfrastructureResearchDataSource,
  build_dividend_factor_coverage_report,
)


class FakeSession:
  def __init__(self, dialect: str = "postgresql") -> None:
    self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
    self.statements: list[str] = []
    self.rolled_back = False
    self.closed = False

  def get_bind(self):
    return self.bind

  async def execute(self, statement) -> None:
    self.statements.append(str(statement))

  async def rollback(self) -> None:
    self.rolled_back = True

  async def close(self) -> None:
    self.closed = True


class FakeRowsResult:
  def __init__(self, rows) -> None:
    self.rows = rows

  def all(self):
    return list(self.rows)


class FakeCoverageSession(FakeSession):
  def __init__(self, *result_sets) -> None:
    super().__init__()
    self.result_sets = list(result_sets)

  async def execute(self, statement):
    sql = str(statement)
    self.statements.append(sql)
    if (
      sql.startswith("SET TRANSACTION")
      or sql.startswith("SET LOCAL idle_in_transaction_session_timeout")
      or "pg_advisory_xact_lock_shared" in sql
    ):
      return FakeRowsResult([])
    return FakeRowsResult(self.result_sets.pop(0))


class FailingTimeoutSession(FakeSession):
  async def execute(self, statement) -> None:
    sql = str(statement)
    self.statements.append(sql)
    if sql.startswith("SET LOCAL idle_in_transaction_session_timeout"):
      raise RuntimeError("timeout setting failed")


class FakeInstrumentRepository:
  def __init__(self) -> None:
    self.requested_types = []

  async def find_by_ids(self, codes):
    return [
      SimpleNamespace(
        id=code,
        type="index" if code == "000300.SH" else "stock",
        name=code,
        market=code[-2:],
        open_date=None,
        expire_date=None,
      )
      for code in codes
    ]

  async def find_all_by_type(self, instrument_type):
    self.requested_types.append(instrument_type)
    return [
      SimpleNamespace(
        id="000001.SZ",
        type=instrument_type,
        name="平安银行",
        market="SZ",
        open_date=None,
        expire_date=None,
      )
    ]


class FakeFactorRepository:
  def __init__(self) -> None:
    self.calls: list[str] = []

  async def find_by_stock_code(
    self,
    stock_code,
    start_time=None,
    end_time=None,
    limit=None,
  ):
    self.calls.append(stock_code)
    return [
      SimpleNamespace(
        stock_code=stock_code,
        time=datetime(2024, 1, 2),
        dr=1.1,
      )
    ]


class FakeBulkFactorRepository(FakeFactorRepository):
  def __init__(self) -> None:
    super().__init__()
    self.bulk_calls = 0

  async def find_all(
    self,
    filters=None,
    start_time=None,
    end_time=None,
    limit=None,
    order_by="time ASC",
  ):
    del filters, start_time, end_time, limit, order_by
    self.bulk_calls += 1
    return [
      SimpleNamespace(
        stock_code="000001.SZ",
        time=datetime(2024, 1, 2),
        dr=1.1,
      ),
      SimpleNamespace(
        stock_code="999999.SZ",
        time=datetime(2024, 1, 2),
        dr=1.2,
      ),
    ]


class FakeKLineRepository:
  def __init__(self) -> None:
    self.calls: list[tuple[tuple[str, ...], bool]] = []
    self.windows: list[tuple[datetime, datetime]] = []

  def find_daily_batch(self, stock_codes, start, end, *, use_cache):
    self.calls.append((tuple(stock_codes), use_cache))
    self.windows.append((start, end))
    return {
      code: pd.DataFrame(
        [
          {
            "stock_code": code,
            "time": "2024-01-02T00:00:00Z",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 100,
            "amount": 1_000,
            "suspend_flag": 0,
          }
        ]
      )
      for code in stock_codes
    }


@pytest.mark.asyncio
async def test_owned_postgres_session_is_read_only_and_rolled_back() -> None:
  session = FakeSession()
  source = InfrastructureResearchDataSource(
    session_factory=lambda: session,
    instrument_repository=FakeInstrumentRepository(),
    dividend_factor_repository=FakeFactorRepository(),
    kline_repository=FakeKLineRepository(),
  )

  async with source:
    pass

  assert session.statements == [
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
    "SET LOCAL idle_in_transaction_session_timeout = '15min'",
  ]
  assert session.rolled_back
  assert session.closed


@pytest.mark.asyncio
async def test_owned_session_is_closed_when_read_only_initialization_fails() -> None:
  session = FailingTimeoutSession()
  source = InfrastructureResearchDataSource(
    session_factory=lambda: session,
    kline_repository=FakeKLineRepository(),
  )

  with pytest.raises(RuntimeError, match="timeout setting failed"):
    await source.__aenter__()

  assert session.rolled_back
  assert session.closed


@pytest.mark.asyncio
async def test_non_postgres_relational_session_fails_closed() -> None:
  source = InfrastructureResearchDataSource(
    session_factory=lambda: FakeSession("sqlite"),
    kline_repository=FakeKLineRepository(),
  )

  with pytest.raises(RuntimeError, match="只允许 PostgreSQL"):
    await source.__aenter__()


@pytest.mark.asyncio
async def test_daily_reads_are_batched_and_influx_cache_is_disabled() -> None:
  kline_repository = FakeKLineRepository()
  source = InfrastructureResearchDataSource(
    instrument_repository=FakeInstrumentRepository(),
    dividend_factor_repository=FakeFactorRepository(),
    kline_repository=kline_repository,
    enforce_postgres_read_only=False,
  )
  codes = [f"{value:06d}.SZ" for value in range(5)]

  bars = await source.load_daily_bars(
    codes,
    datetime(2024, 1, 1),
    datetime(2024, 1, 3),
    batch_size=2,
  )

  assert len(bars) == 5
  assert [len(call[0]) for call in kline_repository.calls] == [2, 2, 1]
  assert all(use_cache is False for _, use_cache in kline_repository.calls)


@pytest.mark.asyncio
async def test_long_daily_reads_are_split_into_non_overlapping_time_windows() -> None:
  kline_repository = FakeKLineRepository()
  source = InfrastructureResearchDataSource(
    instrument_repository=FakeInstrumentRepository(),
    dividend_factor_repository=FakeFactorRepository(),
    kline_repository=kline_repository,
    enforce_postgres_read_only=False,
  )

  await source.load_daily_bars(
    ["000001.SZ"],
    datetime(2023, 1, 1),
    datetime(2024, 1, 1),
  )

  assert len(kline_repository.windows) == 3
  assert all(
    current_end < next_start
    for (_, current_end), (next_start, _) in pairwise(kline_repository.windows)
  )


@pytest.mark.asyncio
async def test_instrument_and_factor_repository_results_are_normalized() -> None:
  factors = FakeFactorRepository()
  source = InfrastructureResearchDataSource(
    instrument_repository=FakeInstrumentRepository(),
    dividend_factor_repository=factors,
    kline_repository=FakeKLineRepository(),
    enforce_postgres_read_only=False,
  )

  instruments = await source.list_instruments(
    instrument_types=("stock",),
    codes=["000001.SZ", "000300.SH"],
  )
  factor_frame = await source.load_dividend_factors(["000001.sz", "000002.SZ"])

  assert instruments["stock_code"].tolist() == ["000001.SZ"]
  assert factor_frame["stock_code"].tolist() == ["000001.SZ", "000002.SZ"]
  assert factors.calls == ["000001.SZ", "000002.SZ"]


@pytest.mark.asyncio
async def test_instrument_type_name_resolves_real_strawberry_enum() -> None:
  from quantx_infrastructure.models.enums import InstrumentType

  instruments = FakeInstrumentRepository()
  source = InfrastructureResearchDataSource(
    instrument_repository=instruments,
    dividend_factor_repository=FakeFactorRepository(),
    kline_repository=FakeKLineRepository(),
    enforce_postgres_read_only=False,
  )

  result = await source.list_instruments(instrument_types=("stock",))

  assert instruments.requested_types == [InstrumentType.STOCK]
  assert result.loc[0, "instrument_type"] == "stock"


@pytest.mark.asyncio
async def test_large_factor_universe_uses_one_bulk_read() -> None:
  factors = FakeBulkFactorRepository()
  source = InfrastructureResearchDataSource(
    instrument_repository=FakeInstrumentRepository(),
    dividend_factor_repository=factors,
    kline_repository=FakeKLineRepository(),
    enforce_postgres_read_only=False,
  )
  codes = [f"{value:06d}.SZ" for value in range(50)]

  result = await source.load_dividend_factors(codes)

  assert factors.bulk_calls == 1
  assert factors.calls == []
  assert result["stock_code"].tolist() == ["000001.SZ"]


@pytest.mark.asyncio
async def test_factor_coverage_reads_completed_durable_database_requests() -> None:
  factor_row = (
    "000001.SZ",
    datetime(2024, 1, 2, 8),
    "20240102",
    Decimal("1.0000"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("1.100000"),
  )
  digest = divid_factor_rows_sha256([factor_row])
  payload = {
    "operation": "divid_factors",
    "source": "qmt-get-divid-factors-v1",
    "stock_list": ["000001.SZ"],
    "start_time": "20240101",
    "end_time": "20240131",
  }
  ingestion_result = {
    "operation": "divid_factors",
    "records_received": 1,
    "records_saved": 1,
    "replacement_audit": {
      "audit_schema_version": 2,
      "stock_count": 1,
      "stock_codes_sha256": divid_factor_codes_sha256(["000001.SZ"]),
      "prior_count": 0,
      "deleted_count": 0,
      "inserted_count": 1,
      "verified_count": 1,
      "source_sha256": digest,
      "persisted_sha256": digest,
      "start_ex_date": "20240101",
      "end_ex_date": "20240131",
      "code_audits": {
        "000001.SZ": {
          "record_count": 1,
          "source_sha256": digest,
          "persisted_sha256": digest,
        }
      },
    },
  }
  session = FakeCoverageSession(
    [
      (
        "factor-request",
        payload,
        "COMPLETED",
        2,
        2,
        datetime(2024, 2, 1),
        ingestion_result,
      ),
      (
        "legacy-factor-request",
        payload,
        "COMPLETED",
        1,
        1,
        datetime(2024, 2, 1),
        {
          "operation": "divid_factors",
          "records_received": 0,
          "records_saved": 0,
          "replacement_audit": {"audit_schema_version": 1},
        },
      ),
    ],
    [factor_row],
  )
  source = InfrastructureResearchDataSource(
    session=session,
    kline_repository=FakeKLineRepository(),
    enforce_postgres_read_only=False,
  )

  result = await source.load_dividend_factor_coverage(
    ["000001.SZ"],
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 31),
  )

  assert result["request_id"].tolist() == [
    "factor-request",
    "legacy-factor-request",
  ]
  valid = result[result["request_id"] == "factor-request"].iloc[0]
  assert valid["stock_codes"] == ["000001.SZ"]
  assert valid["expected_chunks"] == 2
  assert valid["audit_schema_version"] == 2
  assert type(valid["audit_schema_version"]) is int
  assert valid["record_count"] == 1
  assert type(valid["record_count"]) is int
  assert valid["content_sha256"] == digest
  assert valid["current_record_count"] == 1
  assert valid["current_content_sha256"] == digest
  assert valid["current_matches"]
  report = build_dividend_factor_coverage_report(
    result,
    requested_codes=["000001.SZ"],
    requested_start=datetime(2024, 1, 1),
    requested_end=datetime(2024, 1, 31),
  )
  assert report.is_complete
  assert report.invalid_evidence_count == 1
  assert "pg_advisory_xact_lock_shared" in session.statements[0]
  assert "market_data_request" in session.statements[1]
  assert "divid_factors" in session.statements[2]


@pytest.mark.asyncio
async def test_factor_coverage_rejects_legacy_audit_before_reading_factor_rows() -> (
  None
):
  session = FakeCoverageSession(
    [
      (
        "legacy-factor-request",
        {
          "operation": "divid_factors",
          "source": "qmt-get-divid-factors-v1",
          "stock_list": ["000001.SZ"],
          "start_time": "20240101",
          "end_time": "20240131",
        },
        "COMPLETED",
        1,
        1,
        datetime(2024, 2, 1),
        {
          "operation": "divid_factors",
          "records_received": 0,
          "records_saved": 0,
          "replacement_audit": {"audit_schema_version": 1},
        },
      )
    ],
  )
  source = InfrastructureResearchDataSource(
    session=session,
    kline_repository=FakeKLineRepository(),
  )

  result = await source.load_dividend_factor_coverage(
    ["000001.SZ"],
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 31),
  )

  assert result.loc[0, "audit_schema_version"] == 1
  assert not result.loc[0, "current_matches"]
  assert session.statements[0] == (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
  )
  assert session.statements[1] == (
    "SET LOCAL idle_in_transaction_session_timeout = '15min'"
  )
  assert "pg_advisory_xact_lock_shared" in session.statements[2]
  assert "market_data_request" in session.statements[3]
