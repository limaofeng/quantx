from __future__ import annotations

import asyncio

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  StrategyContext,
  StrategyRunMode,
)
from quantx_infrastructure.core.runtime_state_manager import (
  BUCKET_LEDGER_RECONCILE_REQUIRED_KEY,
  GRID_BOOK_CUSTOM_STATE_KEY,
  RUNTIME_RECONCILIATION_STATUS_KEY,
  RuntimeStateManager,
)


@pytest.mark.asyncio
async def test_delta_coalescing_preserves_only_persisted_strategy_changes() -> None:
  context = StrategyContext(
    run_id="state-sync-rewarm",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  queue = strategy.subscribe_state(maxsize=1)
  manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=False,
  )
  manager._running = True
  manager._state_queue = queue
  manager._state_sync_strategy = strategy
  manager._state["custom"] = {
    RUNTIME_RECONCILIATION_STATUS_KEY: "RECONCILE_REQUIRED",
    BUCKET_LEDGER_RECONCILE_REQUIRED_KEY: True,
    GRID_BOOK_CUSTOM_STATE_KEY: {"revision": 2, "source": "api-cas"},
  }

  rewarm = {
    "version": 1,
    "instruments": {
      "600000.SH": {
        "reason": "MARKET_EVENT_QUEUE_OVERFLOW",
        "started_at_ms": 0,
      }
    },
  }
  strategy.state.set("signal_window_rewarm", rewarm)
  # A non-persistent durable-callback mutation must neither evict a persisted
  # delta nor leak into a later checkpoint when the subscriber queue is full.
  strategy.state.set("callback_only_state", "must-not-persist", persist=False)
  strategy.state.set(RUNTIME_RECONCILIATION_STATUS_KEY, "READY")
  strategy.state.set(BUCKET_LEDGER_RECONCILE_REQUIRED_KEY, False)
  strategy.state.set(
    GRID_BOOK_CUSTOM_STATE_KEY,
    {"revision": 1, "source": "stale-strategy-snapshot"},
    notify=False,
  )
  sample_windows = {"version": 1, "instruments": {}}
  strategy.state.set("signal_sample_windows", sample_windows)
  assert queue.qsize() == 1

  sync_task = asyncio.create_task(manager._state_sync_loop())
  manager._state_sync_task = sync_task
  await asyncio.wait_for(queue.join(), timeout=1.0)
  assert await manager.drain_strategy_state_changes()

  assert manager.get_custom("signal_window_rewarm") == rewarm
  assert manager.get_custom("signal_sample_windows") == sample_windows
  assert (
    manager.get_custom(RUNTIME_RECONCILIATION_STATUS_KEY)
    == "RECONCILE_REQUIRED"
  )
  assert manager.get_custom(BUCKET_LEDGER_RECONCILE_REQUIRED_KEY) is True
  assert manager.get_custom("callback_only_state") is None
  assert manager.get_custom(GRID_BOOK_CUSTOM_STATE_KEY) == {
    "revision": 2,
    "source": "api-cas",
  }

  explicit_grid = {"revision": 3, "source": "strategy-explicit"}
  strategy.state.set(GRID_BOOK_CUSTOM_STATE_KEY, explicit_grid)
  await asyncio.wait_for(queue.join(), timeout=1.0)
  # Passive source capture cannot overwrite the manager-owned grid-book
  # authority. Explicit durable callback patches below still may do so.
  assert manager.get_custom(GRID_BOOK_CUSTOM_STATE_KEY) == {
    "revision": 2,
    "source": "api-cas",
  }

  api_grid = {"revision": 4, "source": "newer-api-cas"}
  manager.set_custom(GRID_BOOK_CUSTOM_STATE_KEY, api_grid)
  assert await manager.checkpoint_durable_runtime_event(
    "order:grid-ownership",
    custom_updates={
      GRID_BOOK_CUSTOM_STATE_KEY: {"revision": 3, "source": "stale-callback"},
      "durable_callback_state": {"applied": True},
    },
    strategy_updates={
      GRID_BOOK_CUSTOM_STATE_KEY: {
        "revision": 5,
        "source": "durable-callback-delta",
      }
    },
  )
  assert manager.get_custom(GRID_BOOK_CUSTOM_STATE_KEY) == {
    "revision": 5,
    "source": "durable-callback-delta",
  }
  assert manager.get_custom("durable_callback_state") == {"applied": True}

  manager.set_custom(GRID_BOOK_CUSTOM_STATE_KEY, api_grid)
  assert await manager.checkpoint_durable_runtime_event(
    "order:grid-full-snapshot-only",
    custom_updates={
      GRID_BOOK_CUSTOM_STATE_KEY: {"revision": 3, "source": "stale-callback"}
    },
  )
  assert manager.get_custom(GRID_BOOK_CUSTOM_STATE_KEY) == api_grid

  manager._running = False
  sync_task.cancel()
  await asyncio.gather(sync_task, return_exceptions=True)
  assert queue._unfinished_tasks == 0
