from datetime import datetime

from quantx_infrastructure.repositories.kline_repository import KLineRepository


class FakeOperations:
  def __init__(self, rows):
    self.rows = rows
    self.calls = []

  def query(self, sql, use_cache=True):
    self.calls.append((sql, use_cache))
    return self.rows


def test_daily_batch_groups_rows_by_code_with_one_query():
  operations = FakeOperations(
    [
      {
        "stock_code": "600000.SH",
        "period": "1d",
        "time": "2026-05-19T07:00:00Z",
        "open": 10,
        "high": 11,
        "low": 9,
        "close": 10.5,
        "volume": 100,
        "amount": 1050,
      },
      {
        "stock_code": "000001.SZ",
        "period": "1d",
        "time": "2026-05-19T07:00:00Z",
        "open": 9,
        "high": 10,
        "low": 8,
        "close": 9.5,
        "volume": 90,
        "amount": 900,
      },
    ]
  )
  repository = object.__new__(KLineRepository)
  repository.operations = operations

  result = repository.find_daily_batch(
    ["600000.SH", "000001.SZ"],
    datetime(2026, 1, 1),
    datetime(2026, 5, 20, 23, 59),
  )

  assert len(operations.calls) == 1
  assert set(result) == {"600000.SH", "000001.SZ"}
  assert result["600000.SH"].iloc[0]["close"] == 10.5
  assert "FROM kline_1d" in operations.calls[0][0]
  assert operations.calls[0][1] is False


def test_daily_batch_summary_uses_server_side_aggregates():
  operations = FakeOperations(
    [
      {
        "stock_code": "600000.SH",
        "row_count": 100,
        "distinct_times": 99,
        "invalid_rows": 2,
        "min_time": "2026-01-01T00:00:00Z",
        "max_time": "2026-05-19T00:00:00Z",
      }
    ]
  )
  repository = object.__new__(KLineRepository)
  repository.operations = operations

  result = repository.summarize_daily_batch(
    ["600000.SH", "600000.SH"],
    datetime(2026, 1, 1),
    datetime(2026, 5, 20, 23, 59),
  )

  assert result["600000.SH"]["row_count"] == 100
  assert result["600000.SH"]["distinct_times"] == 99
  assert result["600000.SH"]["invalid_rows"] == 2
  assert len(operations.calls) == 1
  sql, use_cache = operations.calls[0]
  assert "COUNT(DISTINCT time) AS distinct_times" in sql
  assert "GROUP BY stock_code" in sql
  assert sql.count("'600000.SH'") == 1
  assert use_cache is False
