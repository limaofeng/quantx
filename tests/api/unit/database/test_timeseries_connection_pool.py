import threading
import time

import pytest
from quantx_infrastructure.database.timeseries_connection import (
  ConnectionError,
  ConnectionPool,
  TimeSeriesConnection,
)


class FakeClient:
  def __init__(self):
    self.closed = False

  def close(self):
    self.closed = True


def test_get_client_waits_for_returned_connection():
  pool = ConnectionPool(
    host="http://localhost:8181",
    token="token",
    database="quantx",
    max_connections=1,
    timeout=1.0,
    pool_acquire_timeout=1.0,
  )
  created = []
  pool._create_client = lambda: created.append(FakeClient()) or created[-1]

  first_client = pool.get_client()
  acquired = []
  worker_started = threading.Event()

  def acquire_second_client():
    worker_started.set()
    client = pool.get_client()
    acquired.append(client)
    pool.return_client(client)

  worker = threading.Thread(target=acquire_second_client)
  worker.start()
  assert worker_started.wait(timeout=0.5)
  time.sleep(0.05)
  assert acquired == []

  pool.return_client(first_client)
  worker.join(timeout=1.0)

  assert not worker.is_alive()
  assert acquired == [first_client]
  assert len(created) == 1


def test_get_client_times_out_when_pool_stays_full():
  pool = ConnectionPool(
    host="http://localhost:8181",
    token="token",
    database="quantx",
    max_connections=1,
    timeout=1.0,
    pool_acquire_timeout=0.01,
  )
  pool._create_client = FakeClient

  client = pool.get_client()
  try:
    with pytest.raises(ConnectionError, match="等待空闲连接超时"):
      pool.get_client()
  finally:
    pool.return_client(client)


def test_connection_context_discards_client_after_operation_error():
  connection = TimeSeriesConnection(
    host="http://localhost:8181",
    token="token",
    database="quantx",
    max_connections=1,
    timeout=1.0,
    pool_acquire_timeout=1.0,
  )
  created = []
  connection._pool._create_client = lambda: created.append(FakeClient()) or created[-1]
  failed_client = []

  try:
    with pytest.raises(RuntimeError, match="connection reset"):
      with connection.get_client() as client:
        failed_client.append(client)
        raise RuntimeError("connection reset")

    assert failed_client[0].closed
    assert connection._pool._pool == []

    with connection.get_client() as client:
      assert client is not failed_client[0]

    assert len(created) == 2
  finally:
    connection.close()
