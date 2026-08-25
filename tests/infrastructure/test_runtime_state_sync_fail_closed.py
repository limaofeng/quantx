from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


@pytest.mark.asyncio
async def test_stop_state_sync_rejects_done_consumer_with_pending_queue() -> None:
    manager = RuntimeStateManager(
        run_id="sync-consumer-done",
        persist_enabled=False,
    )
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(SimpleNamespace(persist=True, key="marker", value=True))
    consumer = asyncio.create_task(asyncio.sleep(0))
    await consumer
    manager._state_queue = queue
    manager._state_sync_task = consumer

    with pytest.raises(RuntimeError, match="队列仍有未处理事件"):
        await manager.stop_state_sync()

    assert manager._state_queue is queue
    assert manager._state_sync_task is consumer
    assert manager._state_sync_error is not None
    assert queue._unfinished_tasks == 1


@pytest.mark.asyncio
async def test_state_sync_apply_error_blocks_checkpoint_and_final_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RuntimeStateManager(
        run_id="sync-apply-failure",
        persist_enabled=True,
    )
    manager._running = True
    queue: asyncio.Queue = asyncio.Queue()
    manager._state_queue = queue
    manager._state_sync_strategy = SimpleNamespace(
        state=SimpleNamespace(to_dict=lambda: {})
    )
    monkeypatch.setattr(
        manager,
        "_stage_strategy_state_delta",
        MagicMock(side_effect=RuntimeError("cannot stage delta")),
    )
    manager.save_snapshot = AsyncMock(return_value=True)
    manager._state_sync_task = asyncio.create_task(manager._state_sync_loop())
    queue.put_nowait(SimpleNamespace(persist=True, key="marker", value=True))
    await asyncio.wait_for(manager._state_sync_task, timeout=1.0)

    assert manager._state_sync_error is not None
    assert queue._unfinished_tasks == 0
    assert await manager.checkpoint_strategy_state_changes() is False
    with pytest.raises(RuntimeError, match="策略状态同步应用失败"):
        await manager.stop_state_sync()
    with pytest.raises(RuntimeError, match="策略状态同步尚未权威收敛"):
        await manager.stop()

    manager.save_snapshot.assert_not_awaited()
    assert manager._state_queue is queue


@pytest.mark.asyncio
async def test_state_sync_join_timeout_cancels_consumer_but_retains_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RuntimeStateManager(
        run_id="sync-join-timeout",
        persist_enabled=False,
    )
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(SimpleNamespace(persist=True, key="marker", value=True))
    consumer = asyncio.create_task(asyncio.Event().wait())
    manager._state_queue = queue
    manager._state_sync_task = consumer

    async def timeout_join(_awaitable, *, timeout):
        del timeout
        if hasattr(_awaitable, "close"):
            _awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout_join)

    with pytest.raises(RuntimeError, match="停止前未能完全排空"):
        await manager.stop_state_sync()

    assert consumer.done()
    assert manager._state_queue is queue
    assert manager._state_sync_task is consumer
    assert manager._state_sync_error is not None
    assert queue._unfinished_tasks == 1
