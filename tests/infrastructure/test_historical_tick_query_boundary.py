import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from quantx_infrastructure.core.data.tick_identity import tick_storage_time
from quantx_infrastructure.repositories.tick_repository import TickRepository
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
  HistoricalTickPaginationError,
)


class _CapturingTickRepository:
  def __init__(self) -> None:
    self.kwargs = None

  def find_all(self, **kwargs):
    self.kwargs = kwargs
    return []


def test_get_tick_data_expands_end_to_last_ordinal_microsecond() -> None:
  repository = _CapturingTickRepository()
  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = repository
  end_time = datetime(2026, 8, 14, 9, 30, 0, 123000)

  result = asyncio.run(
    service.get_tick_data(
      stock_code="601318.SH",
      start_time=datetime(2026, 8, 14, 9, 30),
      end_time=end_time,
    )
  )

  assert result == []
  assert repository.kwargs is not None
  assert repository.kwargs["end_time"] == datetime(
    2026, 8, 14, 9, 30, 0, 123999
  )


def test_get_tick_data_forwards_replay_pagination_offset() -> None:
  repository = _CapturingTickRepository()
  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = repository

  result = asyncio.run(
    service.get_tick_data(
      stock_code="601318.SH",
      start_time=datetime(2026, 8, 14, 9, 30),
      end_time=datetime(2026, 8, 14, 15, 0),
      limit=6_000,
      offset=6_000,
      order="asc",
    )
  )

  assert result == []
  assert repository.kwargs is not None
  assert repository.kwargs["limit"] == 6_000
  assert repository.kwargs["offset"] == 6_000
  assert repository.kwargs["order_by"] == "time ASC"


def _tick(
  source_time_ms: int,
  ordinal: int,
  *,
  storage_time=None,
) -> SimpleNamespace:
  return SimpleNamespace(
    source_time_ms=source_time_ms,
    tick_ordinal=ordinal,
    time=(
      tick_storage_time(source_time_ms, ordinal)
      if storage_time is None
      else storage_time
    ),
  )


def test_source_identity_page_query_uses_keyset_without_offset() -> None:
  class _Operations:
    def __init__(self) -> None:
      self.sql = ""
      self.sqls = []

    def query(self, sql, **_kwargs):
      self.sqls.append(sql)
      self.sql = sql
      return []

  repository = TickRepository.__new__(TickRepository)
  operations = _Operations()
  repository.operations = operations
  repository.find_source_identity_page(
    stock_code="601318.SH",
    start_time=datetime(2026, 8, 14, 9, 30),
    end_time=datetime(2026, 8, 14, 15, 0),
    limit=10_000,
  )
  result = repository.find_source_identity_page(
    stock_code="601318.SH",
    start_time=datetime(2026, 8, 14, 9, 30),
    end_time=datetime(2026, 8, 14, 15, 0),
    after=(123, 4),
    limit=10_000,
  )

  assert result == []
  assert "period = 'tick'" in operations.sql
  assert "time > '1970-01-01T00:00:00.123004+00:00'" in operations.sql
  assert "ORDER BY time ASC" in operations.sql
  assert "ORDER BY source_time_ms" not in operations.sql
  assert "COUNT(" not in operations.sql.upper()
  assert "LIMIT 10000" in operations.sql
  assert " OFFSET " not in operations.sql
  assert "time > " not in operations.sqls[0]
  assert all("COUNT(" not in sql.upper() for sql in operations.sqls)


def test_source_identity_page_does_not_scan_count_for_duplicate_cursor_identity() -> None:
  class _Operations:
    def __init__(self) -> None:
      self.sql = ""

    def query(self, sql, **_kwargs):
      self.sql = sql
      return []

  repository = TickRepository.__new__(TickRepository)
  repository.operations = _Operations()
  operations = _Operations()
  repository.operations = operations
  assert repository.find_source_identity_page(
    stock_code="601318.SH",
    start_time=datetime(2026, 8, 14, 9, 30),
    end_time=datetime(2026, 8, 14, 15, 0),
    after=(123, 4),
    limit=10,
  ) == []
  assert "COUNT(" not in operations.sql.upper()


def test_iter_tick_pages_requires_strict_progress_and_terminal_probe() -> None:
  class _PagedRepository:
    def __init__(self) -> None:
      self.calls = []

    def find_source_identity_page(self, **kwargs):
      self.calls.append(kwargs["after"])
      pages = {
        None: [_tick(1, 0), _tick(2, 0)],
        (2, 0): [_tick(3, 0)],
        (3, 0): [],
      }
      return pages[kwargs["after"]]

  repository = _PagedRepository()
  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = repository

  async def collect():
    return [
      page
      async for page in service.iter_tick_pages(
        stock_code="601318.SH",
        start_time=datetime(2026, 8, 14, 9, 30),
        end_time=datetime(2026, 8, 14, 15, 0),
        page_size=2,
      )
    ]

  pages = asyncio.run(collect())
  assert [len(page) for page in pages] == [2, 1]
  assert repository.calls == [None, (2, 0), (3, 0)]


def test_iter_tick_pages_rejects_missing_or_mismatched_storage_identity() -> None:
  class _BadRepository:
    def __init__(self, page):
      self.page = page

    def find_source_identity_page(self, **_kwargs):
      page, self.page = self.page, []
      return page

  cases = [
    [
      SimpleNamespace(
        source_time_ms=None,
        tick_ordinal=0,
        time=tick_storage_time(1, 0),
      )
    ],
    [
      _tick(
        1,
        0,
        storage_time=tick_storage_time(1, 0) + timedelta(microseconds=1),
      )
    ],
  ]
  for page in cases:
    service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
    service.tick_repo = _BadRepository(page)

    async def collect():
      return [
        item
        async for item in service.iter_tick_pages(
          stock_code="601318.SH",
          start_time=datetime(2026, 8, 14, 9, 30),
          end_time=datetime(2026, 8, 14, 15, 0),
        )
      ]

    try:
      asyncio.run(collect())
    except HistoricalTickPaginationError:
      continue
    raise AssertionError("invalid Tick identity/time must fail closed")


def test_iter_tick_pages_labels_nullable_integer_bridge_nan_as_missing_identity() -> None:
  class _Repository:
    def __init__(self) -> None:
      self.calls = 0

    def find_source_identity_page(self, **_kwargs):
      self.calls += 1
      return (
        [
          SimpleNamespace(
            source_time_ms=float("nan"),
            tick_ordinal=float("nan"),
            time=tick_storage_time(1, 0),
          )
        ]
        if self.calls == 1
        else []
      )

  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = _Repository()

  async def collect():
    return [
      page
      async for page in service.iter_tick_pages(
        stock_code="601318.SH",
        start_time=datetime(2026, 8, 14, 9, 30),
        end_time=datetime(2026, 8, 14, 15, 0),
      )
    ]

  try:
    asyncio.run(collect())
  except HistoricalTickPaginationError as exc:
    assert str(exc) == "historical Tick source identity is missing"
  else:
    raise AssertionError("nullable integer bridge NaN must fail closed as missing")


def test_iter_tick_pages_rejects_same_identity_with_different_storage_time() -> None:
  class _Repository:
    def __init__(self) -> None:
      self.calls = 0

    def find_source_identity_page(self, **_kwargs):
      self.calls += 1
      if self.calls == 1:
        return [_tick(1, 0)]
      if self.calls == 2:
        return [
          _tick(
            1,
            0,
            storage_time=tick_storage_time(1, 0) + timedelta(microseconds=1),
          )
        ]
      return []

  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = _Repository()

  async def collect():
    return [
      page
      async for page in service.iter_tick_pages(
        stock_code="601318.SH",
        start_time=datetime(2026, 8, 14, 9, 30),
        end_time=datetime(2026, 8, 14, 15, 0),
      )
    ]

  try:
    asyncio.run(collect())
  except HistoricalTickPaginationError as exc:
    assert "storage time" in str(exc)
  else:
    raise AssertionError("same identity with a different storage time must fail")


def test_iter_tick_pages_does_not_treat_late_missing_identity_as_terminal() -> None:
  class _Repository:
    def __init__(self) -> None:
      self.calls = 0

    def find_source_identity_page(self, **_kwargs):
      self.calls += 1
      if self.calls == 1:
        return [_tick(1, 0)]
      if self.calls == 2:
        return [
          SimpleNamespace(
            source_time_ms=None,
            tick_ordinal=0,
            time=tick_storage_time(2, 0),
          )
        ]
      return []

  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = _Repository()

  async def collect():
    return [
      page
      async for page in service.iter_tick_pages(
        stock_code="601318.SH",
        start_time=datetime(2026, 8, 14, 9, 30),
        end_time=datetime(2026, 8, 14, 15, 0),
      )
    ]

  try:
    asyncio.run(collect())
  except HistoricalTickPaginationError:
    pass
  else:
    raise AssertionError("late missing identity must fail instead of terminating")


def test_iter_tick_pages_fails_closed_on_repeated_page_and_page_limit() -> None:
  class _BadRepository:
    def __init__(self, repeated: bool = False) -> None:
      self.repeated = repeated

    def find_source_identity_page(self, **kwargs):
      if kwargs["after"] is None:
        return [_tick(1, 0), _tick(2, 0)]
      if self.repeated:
        return [_tick(1, 0), _tick(2, 0)]
      return [_tick(3, 0), _tick(4, 0)]

  def collect(service):
    async def run():
      return [
        page
        async for page in service.iter_tick_pages(
          stock_code="601318.SH",
          start_time=datetime(2026, 8, 14, 9, 30),
          end_time=datetime(2026, 8, 14, 15, 0),
          page_size=2,
          max_pages=1,
        )
      ]

    return asyncio.run(run())

  repeated_service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  repeated_service.tick_repo = _BadRepository(repeated=True)
  try:
    collect(repeated_service)
  except HistoricalTickPaginationError:
    pass
  else:
    raise AssertionError("repeated historical Tick page must fail closed")

  limited_service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  limited_service.tick_repo = _BadRepository()
  try:
    collect(limited_service)
  except HistoricalTickPaginationError:
    pass
  else:
    raise AssertionError("historical Tick page limit must fail closed")


def test_iter_tick_pages_wraps_repository_integrity_value_error() -> None:
  class _Repository:
    def find_source_identity_page(self, **_kwargs):
      raise ValueError("duplicate cursor identity")

  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = _Repository()

  async def collect():
    return [
      page
      async for page in service.iter_tick_pages(
        stock_code="601318.SH",
        start_time=datetime(2026, 8, 14, 9, 30),
        end_time=datetime(2026, 8, 14, 15, 0),
      )
    ]

  try:
    asyncio.run(collect())
  except HistoricalTickPaginationError as exc:
    assert "could not prove page integrity" in str(exc)
  else:
    raise AssertionError("repository integrity ValueError must be wrapped")
