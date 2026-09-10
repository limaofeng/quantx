"""Read-only replay uses real manifests/readers and keeps provider secrets out of evidence."""

import copy
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from quantx_infrastructure.services import market_data_readback_diagnostic as diagnostic
from quantx_infrastructure.services.market_data_persistence_verification import (
  readback_trace_scope,
)
from sqlalchemy import event, text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_persistence_verification import _tick_reader
from tests.infrastructure.test_market_data_transfer_ingestion import (
  _payload,
  _summary,
  _tick_row,
  _write_chunk,
)


@pytest.mark.parametrize(
  "mode", ["success", "missing", "capacity", "reader_error", "checksum"]
)
async def test_diagnostic_replays_upload_without_mutation_or_secret_output(
  tmp_path, mode
):
  row = _tick_row()
  item = _write_chunk(tmp_path, [row, _summary([row])])
  if mode == "checksum":
    item["checksum_sha256"] = "0" * 64
  snapshot = diagnostic.RequestSnapshot(
    "request-1",
    {
      "status": "BLOCKED",
      "expected_chunks": 1,
      "received_chunks": 1,
      "request_payload": _payload(),
    },
    [item],
  )
  before = copy.deepcopy(snapshot)
  original_bytes = Path(item["storage_reference"]).read_bytes()
  events, calls = [], []

  class Client:
    def query(self, **kwargs):
      calls.append(kwargs)
      assert kwargs["mode"] == "reader" and kwargs["query"].startswith("SELECT ")
      if mode == "capacity":
        raise ValueError(
          "scan 432 Parquet files exceeding file limit secret://credential"
        )
      if mode == "reader_error":

        class BrokenReader:
          schema = SimpleNamespace(names=["time", "source_time_ms", "tick_ordinal"])

          def __iter__(self):
            raise ValueError("secret://credential raw provider failure")
            yield

          def close(self):
            pass

        return BrokenReader()
      return _tick_reader([] if mode == "missing" else [(row["time"], 0)])

  class Connection:
    @contextmanager
    def get_client(self, *, timeout=None):
      assert 0 < timeout <= 60
      yield Client()

  result = await diagnostic.diagnose_readback(
    snapshot, "request-1", connection=Connection(), emit=events.append
  )
  assert snapshot == before
  assert Path(item["storage_reference"]).read_bytes() == original_bytes
  assert readback_trace_scope.get() == {}
  serialized = json.dumps(events)
  assert "secret://" not in serialized and str(tmp_path) not in serialized
  assert "SELECT " not in serialized
  assert result["status"] == ("verified" if mode == "success" else "failed")
  if mode == "checksum":
    assert calls == []
  else:
    page = next(e for e in events if e["event"] == "query")
    assert page["expected_keys"] == 1 and page["group_index"] == 1
    assert page["page_index"] == 1 and len(page["query_sha256"]) == 64
    assert page["time_predicates"]
  if mode == "capacity":
    assert result["reason_code"] == "DEPENDENCY_QUERY_CAPACITY_BLOCKED"
    assert any(
      e["event"] == "query_error" and e["exception_type"] == "ValueError"
      for e in events
    )
  if mode == "reader_error":
    assert any(
      e["event"] == "reader_error" and e["exception_type"] == "ValueError"
      for e in events
    )


async def test_snapshot_uses_one_read_only_transaction(durable_store):  # noqa: F811
  store, _ = durable_store
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""CREATE TEMP TABLE market_data_transfer (
      request_id text, chunk_index integer, checksum_sha256 text, record_count integer,
      compressed boolean, compressed_bytes integer, storage_reference text)""")
    )
    await connection.execute(
      text(
        "INSERT INTO market_data_transfer VALUES ('request-1',0,'digest',1,true,12,'relative.gz')"
      )
    )
  statements = []

  def record(connection, cursor, statement, parameters, context, executemany):
    statements.append(statement.strip())

  event.listen(store.engine.sync_engine, "before_cursor_execute", record)
  try:
    snapshot = await diagnostic.read_request_snapshot(store.engine, "request-1")
  finally:
    event.remove(store.engine.sync_engine, "before_cursor_execute", record)
  assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
  assert len(statements) == 3 and all(
    value.startswith("SELECT ") for value in statements[1:]
  )
  assert snapshot.request["status"] == "UPLOADED"
  assert snapshot.transfers[0]["storage_reference"] == "relative.gz"


def test_cli_rejects_production_on_mac_before_configuration(monkeypatch):
  path = Path(__file__).resolve().parents[2] / "ops/diagnose_market_data_readback.py"
  spec = importlib.util.spec_from_file_location("readback_cli", path)
  cli = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(cli)
  monkeypatch.setattr(cli.sys, "platform", "darwin")
  with pytest.raises(ValueError, match="PRODUCTION_REQUIRES_WINDOWS"):
    cli.configure("production")


async def test_diagnostic_page_cursor_retains_group_and_advances():
  from quantx_infrastructure.services.market_data_persistence_verification import (
    verify_persisted_bar_summaries,
  )

  from tests.infrastructure.test_market_data_persistence_verification import (
    _key_batches,
  )
  from tests.infrastructure.test_market_data_persistence_verification import (
    _summary as expected_summary,
  )

  times = [1_699_977_600_000, 1_699_977_600_001]
  events = []

  class Connection:
    @contextmanager
    def get_client(self, *, timeout=None):
      remaining = iter(times)

      class Client:
        def query(self, **kwargs):
          return _tick_reader([(next(remaining), 0)])

      yield Client()

  result = await verify_persisted_bar_summaries(
    code_summaries=[
      expected_summary(code="600000.SH", period="tick", times=times, ordinals=[0, 0])
    ],
    expected_key_batches=_key_batches(
      code="600000.SH", period="tick", times=times, ordinals=[0, 0]
    ),
    start_ms=times[0],
    end_exclusive_ms=times[-1] + 1,
    connection=diagnostic.TracedReadConnection(Connection(), events.append),
    page_rows=1,
    max_attempts=1,
    retry_delays=(),
    concurrency=1,
  )
  assert result["records_verified"] == 2
  pages = [event for event in events if event["event"] == "query"]
  assert [page["page_index"] for page in pages] == [1, 2]
  assert pages[0]["group_sha256"] == pages[1]["group_sha256"]
  assert pages[0]["query_sha256"] != pages[1]["query_sha256"]
  assert any(operator == ">" for operator, _ in pages[1]["time_predicates"])


def test_cli_snapshot_uses_explicit_config_root_and_its_own_code(monkeypatch, tmp_path):
  import sys
  from types import SimpleNamespace

  path = Path(__file__).resolve().parents[2] / "ops/diagnose_market_data_readback.py"
  spec = importlib.util.spec_from_file_location("readback_cli", path)
  cli = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(cli)
  prefix = tmp_path / "quantx"
  (prefix / "conda-meta").mkdir(parents=True)
  deployment = tmp_path / "deployment"
  calls = []

  def load(root, environment):
    calls.append((root, environment))
    return {"QUANTX_ROOT": str(root), "ENABLE_REAL_TRADING": "true"}

  monkeypatch.setitem(
    sys.modules, "runtime_config", SimpleNamespace(load_environment=load)
  )
  monkeypatch.setattr(cli.sys, "prefix", str(prefix))
  monkeypatch.setattr(cli.sys, "path", list(sys.path))
  monkeypatch.setattr(cli.os, "environ", {})
  cli.configure("development", deployment)
  assert calls == [(deployment.resolve(), "development")]
  assert cli.os.environ["QUANTX_ROOT"] == str(deployment)
  assert cli.os.environ["ENABLE_REAL_TRADING"] == "false"
  assert cli.os.environ["DATABASE_PROCESS_ROLE"] == "tooling"
  assert str(cli.ROOT / "packages/infrastructure/src") in cli.sys.path
  assert str(deployment / "packages/infrastructure/src") not in cli.sys.path
