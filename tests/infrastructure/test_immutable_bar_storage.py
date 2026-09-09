"""Real SDK key serialization and bounded Arrow reads; no live Influx server."""

import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone

import pyarrow as pa
import pytest
from influxdb_client_3.write_client.client.write.dataframe_serializer import (
  DataframeSerializer,
)
from influxdb_client_3.write_client.client.write_api import PointSettings
from quantx_infrastructure.services import immutable_bar_storage as storage
from quantx_infrastructure.services import market_data_content_verification as content
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MarketDataValidationError,
)

from tests.infrastructure.test_market_data_transfer_ingestion import (
  ManifestStore,
  _kline_row,
  _payload,
  _summary,
  _tick_row,
  _write_chunk,
)


class VersionStorage:
  def __init__(self):
    self._stats = {"writes": 0, "errors": 0}
    self.points, self.lines, self.queries = {}, [], []
    self.ignore_version = False

  @contextmanager
  def get_client(self, **kwargs):
    yield self

  def write(self, record, **kwargs):
    assert kwargs["data_frame_tag_columns"] == [
      "stock_code",
      "period",
      "storage_version",
    ]
    lines = DataframeSerializer(record, PointSettings(), **kwargs).serialize()
    for row, line in zip(record.to_dict("records"), lines, strict=True):
      self.lines.append(line)
      assert "storage_version=" in line.split(" ")[0]
      # The SDK-produced table/tag set and timestamp are the storage identity.
      key = (line.split(" ")[0], line.rsplit(" ", 1)[-1])
      self.points[key] = (kwargs["data_frame_measurement_name"], row)

  def query(self, **kwargs):
    self.queries.append(kwargs)
    sql, params = kwargs["query"], kwargs["query_parameters"]
    assert re.search(r"storage_version\s*=\s*\$storage_version", sql)
    table = re.search(r"FROM (\w+)", sql)[1]
    assert table.endswith("_versions")
    start = datetime.fromisoformat(re.search(r"time >= '([^']+)'", sql)[1])
    end = datetime.fromisoformat(re.search(r"time <=? '([^']+)'", sql)[1])
    cursor = re.search(r"time > '([^']+)'", sql)
    after = (
      datetime.fromisoformat(cursor[1])
      if cursor
      else datetime.min.replace(tzinfo=timezone.utc)
    )
    rows = sorted(
      [
        row
        for measurement, row in self.points.values()
        if measurement == table
        and row["stock_code"] == params["stock_code"]
        and row["period"] == params["period"]
        and (self.ignore_version or row["storage_version"] == params["storage_version"])
        and start <= row["time"] <= end
        and row["time"] > after
      ],
      key=lambda row: row["time"],
    )
    rows = rows[: int(re.search(r"LIMIT (\d+)", sql)[1])]

    class Reader:
      def __iter__(self):
        if rows:
          yield pa.RecordBatch.from_pylist(rows)

      def close(self):
        pass

    return Reader()


async def version(tmp_path, rows, period):
  tmp_path.mkdir(parents=True, exist_ok=True)
  store = ManifestStore(
    payload=_payload(periods=[period]),
    manifest=[_write_chunk(tmp_path, [*rows, _summary(rows, period=period)])],
  )
  return await storage.prepare_immutable_bar_version(store, "request-1")


@pytest.mark.parametrize("period", ["tick", "1m", "1d"])
async def test_late_old_write_cannot_overwrite_selected_new_content(tmp_path, period):
  row = _tick_row() if period == "tick" else _kline_row(period=period)
  field = "lastPrice" if period == "tick" else "close"
  old = await version(tmp_path / "old", [row], period)
  new = await version(tmp_path / "new", [{**row, field: row[field] + 0.1}], period)
  assert old.storage_version != new.storage_version
  connection = VersionStorage()
  await storage.write_immutable_bar_version(new, connection=connection)
  before = await storage.verify_immutable_bar_version(new, connection=connection)
  # This is the server-side completion order after an old process lost its lease.
  await storage.write_immutable_bar_version(old, connection=connection)
  after = await storage.verify_immutable_bar_version(new, connection=connection)
  assert before == after
  assert after["persisted_sha256"] == new.content_sha256
  assert (await storage.verify_immutable_bar_version(old, connection=connection))[
    "persisted_sha256"
  ] == old.content_sha256
  assert len(connection.points) == 2
  await storage.write_immutable_bar_version(new, connection=connection)
  assert len(connection.points) == 2


async def test_same_normalized_content_has_same_version_despite_raw_rounding(tmp_path):
  row = _kline_row()
  first = await version(tmp_path / "first", [{**row, "close": 10.12301}], "1m")
  second = await version(tmp_path / "second", [{**row, "close": 10.12302}], "1m")
  assert first.content_sha256 == second.content_sha256
  assert first.storage_version == second.storage_version


async def test_explicit_version_filter_is_preserved_across_pages(tmp_path, monkeypatch):
  monkeypatch.setattr(content, "CONTENT_PAGE_ROWS", 1)
  row = _tick_row()
  prepared = await version(tmp_path, [row, {**row, "time": row["time"] + 1}], "tick")
  connection = VersionStorage()
  await storage.write_immutable_bar_version(prepared, connection=connection)
  proof = await storage.verify_immutable_bar_version(prepared, connection=connection)
  assert proof["records_verified"] == 2
  assert len(connection.queries) == 2
  assert all(
    q["query_parameters"]["storage_version"] == prepared.storage_version
    for q in connection.queries
  )


async def test_changed_source_is_rejected_before_first_write(tmp_path):
  prepared = await version(tmp_path, [_tick_row()], "tick")
  _write_chunk(
    tmp_path, [_tick_row(last_price=11), _summary([_tick_row(last_price=11)])]
  )
  connection = VersionStorage()
  with pytest.raises(MarketDataValidationError):
    await storage.write_immutable_bar_version(prepared, connection=connection)
  assert not connection.lines


async def test_forged_version_is_rejected_before_io(tmp_path):
  prepared = await version(tmp_path, [_tick_row()], "tick")
  connection = VersionStorage()
  with pytest.raises(ValueError, match="version changed"):
    await storage.write_immutable_bar_version(
      replace(prepared, storage_version="a" * 64), connection=connection
    )
  assert not connection.lines


async def test_query_returning_different_version_is_rejected(tmp_path):
  old = await version(tmp_path / "old", [_tick_row()], "tick")
  new = await version(tmp_path / "new", [_tick_row(last_price=11)], "tick")
  connection = VersionStorage()
  await storage.write_immutable_bar_version(old, connection=connection)
  connection.ignore_version = True
  with pytest.raises(content.MarketDataPersistenceMismatchError):
    await storage.verify_immutable_bar_version(new, connection=connection)


@pytest.mark.parametrize("invalid", ["", "x' OR true--", True])
async def test_invalid_version_is_rejected_before_query(invalid):
  connection = VersionStorage()

  async def frames():
    if False:
      yield

  with pytest.raises(ValueError, match="invalid storage version"):
    await content.verify_persisted_bar_content(
      frames(), connection=connection, storage_version=invalid
    )
  assert not connection.queries


async def test_partition_scope_cannot_be_widened(tmp_path):
  row = _tick_row()
  store = ManifestStore(
    payload=_payload(stock_list=["600000.SH", "000001.SZ"]),
    manifest=[_write_chunk(tmp_path, [row, _summary([row])])],
  )
  with pytest.raises(ValueError, match="one daily partition"):
    await storage.prepare_immutable_bar_version(store, "request-1")


async def test_repeated_cancellation_joins_immutable_write(tmp_path, monkeypatch):
  import asyncio
  import threading

  from tests.infrastructure.test_market_data_write_cancellation import cancel_repeatedly

  prepared = await version(tmp_path, [_tick_row()], "tick")
  loop = asyncio.get_running_loop()
  entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()

  def write(*args):
    loop.call_soon_threadsafe(entered.set)
    try:
      if not release.wait(5):
        raise RuntimeError("test writer did not settle")
    finally:
      finished.set()

  monkeypatch.setattr(storage, "_write_frame", write)
  task = asyncio.create_task(
    storage.write_immutable_bar_version(prepared, connection=VersionStorage())
  )
  try:
    await asyncio.wait_for(entered.wait(), 2)
    await cancel_repeatedly(task)
    assert not finished.is_set()
  finally:
    release.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, 2)
  assert finished.is_set()
