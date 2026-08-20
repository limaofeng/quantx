import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from quantx_infrastructure.database import timeseries_connection as connection_module
from quantx_infrastructure.database.timeseries_connection import (
  ConnectionPool,
  TimeSeriesConnection,
)
from quantx_infrastructure.database.timeseries_operations import TimeSeriesOperations


@pytest.fixture
def cache_operations():
  connection = TimeSeriesConnection(
    host="http://influx.invalid",
    token="test-token",
    database="test-database",
    enable_cache=True,
  )
  try:
    yield connection, TimeSeriesOperations(connection)
  finally:
    connection.close()


@pytest.mark.parametrize("operation_name", ["get", "set", "clear"])
def test_cache_operations_use_shared_lock(cache_operations, operation_name: str) -> None:
  connection, operations = cache_operations
  operations._set_cache("existing", [1])
  started = threading.Event()
  finished = threading.Event()

  def invoke() -> None:
    started.set()
    if operation_name == "get":
      operations._get_from_cache("existing")
    elif operation_name == "set":
      operations._set_cache("new", [2])
    else:
      operations.clear_cache()
    finished.set()

  executor = ThreadPoolExecutor(max_workers=1)
  lock_held = True
  connection._cache_lock.acquire()
  try:
    future = executor.submit(invoke)
    assert started.wait(timeout=1)
    assert not finished.wait(timeout=0.05)
    connection._cache_lock.release()
    lock_held = False
    future.result(timeout=1)
  finally:
    if lock_held:
      connection._cache_lock.release()
    executor.shutdown(wait=True)

  assert finished.is_set()


def test_concurrent_cache_reads_keep_exact_hit_statistics(cache_operations) -> None:
  _connection, operations = cache_operations
  expected = [{"close": 10.5}]
  operations._set_cache("shared", expected)

  with ThreadPoolExecutor(max_workers=16) as executor:
    results = list(executor.map(operations._get_from_cache, ["shared"] * 1000))

  assert results == [expected] * 1000
  statistics = operations.get_statistics()
  assert statistics["cache_hits"] == 1000
  assert statistics["cache_misses"] == 0
  assert statistics["cache_hit_rate"] == 1
  assert statistics["cache_size"] == 1


def test_concurrent_expired_cache_reads_are_atomic(cache_operations) -> None:
  connection, operations = cache_operations
  operations._set_cache("expired", [{"close": 10.5}])
  with connection._cache_lock:
    connection._cache_timestamps["expired"] = 0

  with ThreadPoolExecutor(max_workers=16) as executor:
    results = list(executor.map(operations._get_from_cache, ["expired"] * 1000))

  assert results == [None] * 1000
  statistics = operations.get_statistics()
  assert statistics["cache_hits"] == 0
  assert statistics["cache_misses"] == 1000
  assert statistics["cache_hit_rate"] == 0
  assert statistics["cache_size"] == 0


def test_concurrent_first_client_creation_waits_for_lazy_import(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import_started = threading.Event()
  allow_import = threading.Event()
  second_started = threading.Event()

  class FakeInfluxClient:
    def __init__(self, **kwargs) -> None:
      self.kwargs = kwargs

  def import_module(name: str):
    assert name == "influxdb_client_3"
    import_started.set()
    assert allow_import.wait(timeout=1)
    return SimpleNamespace(InfluxDBClient3=FakeInfluxClient)

  monkeypatch.setattr(connection_module, "_InfluxDBClient3", None)
  monkeypatch.setattr(connection_module, "_INFLUXDB_IMPORT_ERROR", None)
  monkeypatch.setattr(connection_module, "_INFLUXDB_IMPORT_ATTEMPTED", False)
  monkeypatch.setattr(connection_module.importlib, "import_module", import_module)

  pool = ConnectionPool(
    host="http://influx.invalid",
    token="test-token",
    database="test-database",
  )

  def create_second_client():
    second_started.set()
    return pool._create_client()

  executor = ThreadPoolExecutor(max_workers=2)
  try:
    first = executor.submit(pool._create_client)
    assert import_started.wait(timeout=1)
    second = executor.submit(create_second_client)
    assert second_started.wait(timeout=1)
    assert not second.done()
    allow_import.set()
    clients = [first.result(timeout=1), second.result(timeout=1)]
  finally:
    allow_import.set()
    executor.shutdown(wait=True)

  assert all(isinstance(client, FakeInfluxClient) for client in clients)
