from datetime import datetime
from itertools import pairwise
from types import SimpleNamespace

import pandas as pd
import pytest
from quantx_research.data import InfrastructureResearchDataSource


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
  def __init__(self, rows) -> None:
    super().__init__()
    self.rows = rows

  async def execute(self, statement):
    self.statements.append(str(statement))
    return FakeRowsResult(self.rows)


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

  assert session.statements == ["SET TRANSACTION READ ONLY"]
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
  session = FakeCoverageSession(
    [
      (
        "factor-request",
        {
          "operation": "divid_factors",
          "source": "qmt-get-divid-factors-v1",
          "stock_list": ["000001.SZ", "000002.SZ"],
          "start_time": "20240101",
          "end_time": "20240131",
        },
        "COMPLETED",
        2,
        2,
        datetime(2024, 2, 1),
      ),
      (
        "bars-request",
        {
          "operation": "bars",
          "stock_list": ["000001.SZ"],
          "start_time": "20240101",
          "end_time": "20240131",
        },
        "COMPLETED",
        1,
        1,
        datetime(2024, 2, 1),
      ),
    ]
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

  assert result["request_id"].tolist() == ["factor-request"]
  assert result.loc[0, "stock_codes"] == ["000001.SZ", "000002.SZ"]
  assert result.loc[0, "expected_chunks"] == 2
