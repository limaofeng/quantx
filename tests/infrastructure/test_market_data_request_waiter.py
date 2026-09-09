"""Only the standalone worker may claim ingestion; callers observe its audit."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from quantx_infrastructure.runtime_store import DurableRuntimeStore
from quantx_infrastructure.services import market_data_request_waiter as waiter


async def test_waiter_observes_worker_completion_without_claiming(monkeypatch):
  store = SimpleNamespace(
    market_data_request=AsyncMock(
      side_effect=[
        {"status": "PROCESSING"},
        {"status": "COMPLETED", "ingestion_result": {"records_saved": 7}},
      ]
    ),
    close=AsyncMock(),
  )
  monkeypatch.setattr(waiter, "DurableRuntimeStore", lambda: store)
  result = await waiter.wait_for_market_data_ingestion("source-1", timeout_seconds=5)
  assert result == {"request_id": "source-1", "status": "completed", "records_saved": 7}
  assert store.market_data_request.await_count == 2
  store.close.assert_awaited_once()


@pytest.mark.parametrize("status", ["BLOCKED", "FAILED", "CANCELLED"])
async def test_waiter_does_not_resume_terminal_or_blocked_work(monkeypatch, status):
  store = SimpleNamespace(
    market_data_request=AsyncMock(return_value={"status": status}), close=AsyncMock()
  )
  monkeypatch.setattr(waiter, "DurableRuntimeStore", lambda: store)
  with pytest.raises(RuntimeError, match="did not complete"):
    await waiter.wait_for_market_data_ingestion("source-1")
  store.close.assert_awaited_once()


async def test_plain_runtime_store_cannot_obtain_ingestion_ownership():
  store = object.__new__(DurableRuntimeStore)

  @asynccontextmanager
  async def begin():
    yield SimpleNamespace(
      execute=AsyncMock(side_effect=AssertionError("must not write"))
    )

  store.engine = SimpleNamespace(begin=begin)
  with pytest.raises(RuntimeError, match="independent Data Worker"):
    await store.claim_market_data_request("source-1")


def test_only_standalone_worker_imports_ingestion_runner_and_old_schedule_is_removed():
  root = Path(__file__).resolve().parents[2]
  callers = []
  for directory in (root / "apps", root / "packages"):
    for path in directory.rglob("*.py"):
      if path.relative_to(directory).parts[1] not in {"src", "scripts"}:
        continue  # wheel build outputs are not source entrypoints
      if "claim_ingest_and_finish_market_data_request" in path.read_text():
        callers.append(path.relative_to(root).as_posix())
  assert sorted(callers) == [
    "apps/market-data/src/quantx_market_data/worker.py",
    "packages/infrastructure/src/quantx_infrastructure/services/market_data_transfer_ingestion.py",
  ]
  config = yaml.safe_load((root / "apps/worker/prefect.yaml").read_text())
  assert all(
    item["name"] != "market-data-ingestion-recovery" for item in config["deployments"]
  )
