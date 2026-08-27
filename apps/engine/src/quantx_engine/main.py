"""Engine lifecycle with a PostgreSQL single-instance lease."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid
from typing import Awaitable, Callable

from quantx_domain.clock import utcnow
from quantx_infrastructure.core.data.market_data_service import market_data_service
from quantx_infrastructure.core.data.realtime import set_intraday_warm_cache
from quantx_infrastructure.core.data.whole_quote_hub import whole_quote_hub
from quantx_infrastructure.database.manager import db_manager
from quantx_infrastructure.database.relational_connection import (
  AsyncSessionLocal,
  database_pool_snapshot,
  engine,
)
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.services.limit_up_radar import limit_up_radar_monitor
from quantx_infrastructure.services.t_trade_monitor_projection_service import (
  t_trade_monitor_projection_service,
)
from sqlalchemy import text

from .command_processor import run_command_consumer
from .conditional_liquidation import conditional_liquidation_monitor
from .exit_plan_monitor import exit_plan_monitor
from .limit_up_board_runtime import limit_up_board_assistant
from .realtime_manager import realtime_manager
from .report_processor import run_report_consumer
from .strategy_manager import strategy_manager
from .subscription_bridge import (
  run_market_query_bridge,
  run_subscription_bridge,
)
from .t_trade_observability import t_trade_runtime_observability
from .t_trade_runtime import t_trade_global_monitor
from .warm_cache import intraday_warm_cache

logger = logging.getLogger(__name__)
ENGINE_LOCK_NAME = "quantx-engine-singleton-v1"
ENGINE_LEASE_ACQUIRE_TIMEOUT_SECONDS = 90.0
ENGINE_LEASE_RETRY_SECONDS = 2.0
ENGINE_LEASE_IDLE_TIMEOUT_SECONDS = 60
ENGINE_DATABASE_OPERATION_TIMEOUT_SECONDS = 10.0
ENGINE_HEARTBEAT_RETRY_SECONDS = 1.0
ENGINE_SHUTDOWN_TIMEOUT_SECONDS = 15.0
ENGINE_RESTART_MAX_DELAY_SECONDS = 30.0


def _engine_instance_id() -> str:
  configured = os.environ.get("QUANTX_ENGINE_INSTANCE_ID", "").strip()
  if not configured:
    return str(uuid.uuid4())
  if len(configured) > 64:
    raise RuntimeError("QUANTX_ENGINE_INSTANCE_ID exceeds the heartbeat schema limit")
  return configured


async def _write_heartbeat_once(instance_id: str) -> None:
  # Capture before opening the heartbeat session so the metric describes the
  # workload pool instead of counting the observer itself.
  pool_snapshot = database_pool_snapshot()
  async with AsyncSessionLocal() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "engine")
    details = {
      "pid": os.getpid(),
      "host": socket.gethostname(),
      "databasePool": pool_snapshot,
      "tTradeV3": t_trade_runtime_observability.snapshot(),
      "tTradeProjection": t_trade_monitor_projection_service.metrics_snapshot(),
    }
    if heartbeat is None:
      db.add(
        RuntimeComponentHeartbeat(
          component="engine",
          instance_id=instance_id,
          status="ready",
          details=details,
          updated_at=utcnow(),
        )
      )
    else:
      heartbeat.instance_id = instance_id
      heartbeat.status = "ready"
      heartbeat.details = details
      heartbeat.updated_at = utcnow()
    await db.commit()


async def _heartbeat(stopped: asyncio.Event, instance_id: str) -> None:
  while not stopped.is_set():
    retry_delay = 15.0
    try:
      await asyncio.wait_for(
        _write_heartbeat_once(instance_id),
        timeout=ENGINE_DATABASE_OPERATION_TIMEOUT_SECONDS,
      )
    except Exception as exc:
      # The advisory-lease watchdog is the Engine liveness authority.  A
      # transient observability write must not cancel active strategy runs or
      # force a supervised restart while that independent lease is healthy.
      logger.warning(
        "Engine heartbeat write failed; retrying without restarting: %s",
        exc,
      )
      retry_delay = ENGINE_HEARTBEAT_RETRY_SECONDS
    try:
      await asyncio.wait_for(stopped.wait(), timeout=retry_delay)
    except asyncio.TimeoutError:
      pass


async def _lease_watchdog(stopped: asyncio.Event, lock_connection) -> None:
  """Fail the Engine if the session holding its advisory lease is lost."""
  while not stopped.is_set():
    try:
      await asyncio.wait_for(stopped.wait(), timeout=5.0)
      continue
    except asyncio.TimeoutError:
      pass
    try:
      async def check_lease() -> None:
        await lock_connection.execute(text("SELECT 1"))
        await lock_connection.commit()

      await asyncio.wait_for(
        check_lease(),
        timeout=ENGINE_DATABASE_OPERATION_TIMEOUT_SECONDS,
      )
    except Exception as exc:
      stopped.set()
      raise RuntimeError("Engine database lease connection was lost") from exc


def _detach_engine_lease_connection(lock_connection) -> None:
  """Reserve the singleton lease connection without consuming a pool slot."""

  sync_connection = getattr(lock_connection, "sync_connection", None)
  if sync_connection is None:
    raise RuntimeError("Engine database lease connection is not initialized")
  sync_connection.detach()


async def _acquire_engine_lease(
  lock_connection,
  *,
  timeout_seconds: float = ENGINE_LEASE_ACQUIRE_TIMEOUT_SECONDS,
  retry_seconds: float = ENGINE_LEASE_RETRY_SECONDS,
) -> None:
  """Acquire the singleton lease while allowing a crashed session to expire."""
  await lock_connection.execute(
    text(
      """
      SELECT
        set_config('application_name', 'quantx-engine-lease', false),
        set_config('idle_session_timeout', :idle_timeout, false),
        set_config('tcp_keepalives_idle', :keepalive_idle, false),
        set_config('tcp_keepalives_interval', :keepalive_interval, false),
        set_config('tcp_keepalives_count', :keepalive_count, false)
      """
    ),
    {
      "idle_timeout": f"{ENGINE_LEASE_IDLE_TIMEOUT_SECONDS}s",
      "keepalive_idle": "15",
      "keepalive_interval": "5",
      "keepalive_count": "3",
    },
  )
  await lock_connection.commit()

  loop = asyncio.get_running_loop()
  deadline = loop.time() + max(0.0, timeout_seconds)
  warned = False
  while True:
    lock_acquired = bool(
      (
        await lock_connection.execute(
          text("SELECT pg_try_advisory_lock(hashtext(:lock_name))"),
          {"lock_name": ENGINE_LOCK_NAME},
        )
      ).scalar()
    )
    await lock_connection.commit()
    if lock_acquired:
      if warned:
        logger.info("Expired Engine lease released; startup can continue")
      return

    remaining = deadline - loop.time()
    if remaining <= 0:
      raise RuntimeError("已有 QuantX Engine 实例持有数据库租约")
    if not warned:
      logger.warning(
        "Engine lease is busy; waiting up to %.0fs for crash recovery",
        timeout_seconds,
      )
      warned = True
    await asyncio.sleep(min(max(0.01, retry_seconds), remaining))


async def _wait_for_stop_or_failure(
  stopped: asyncio.Event,
  tasks: list[asyncio.Task],
) -> None:
  """Keep the process alive only while every critical Engine task is alive."""
  stop_waiter = asyncio.create_task(stopped.wait(), name="engine-stop-waiter")
  try:
    done, _ = await asyncio.wait(
      [stop_waiter, *tasks],
      return_when=asyncio.FIRST_COMPLETED,
    )
    completed_tasks = [task for task in tasks if task in done]
    if completed_tasks:
      for failed in completed_tasks:
        if failed.cancelled():
          if stopped.is_set():
            continue
          raise RuntimeError(
            f"Engine task was cancelled: {failed.get_name()}"
          )
        error = failed.exception()
        if error is not None:
          raise RuntimeError(
            f"Engine task failed: {failed.get_name()}"
          ) from error
      if stopped.is_set() or stop_waiter in done:
        return
      failed = completed_tasks[0]
      raise RuntimeError(
        f"Engine task exited unexpectedly: {failed.get_name()}"
      )
    if stopped.is_set() or stop_waiter in done:
      return
  finally:
    stop_waiter.cancel()
    await asyncio.gather(stop_waiter, return_exceptions=True)


async def _mark_engine_offline(instance_id: str) -> None:
  try:
    async with AsyncSessionLocal() as db:
      heartbeat = await db.get(RuntimeComponentHeartbeat, "engine")
      if heartbeat is None or heartbeat.instance_id != instance_id:
        return
      heartbeat.status = "offline"
      heartbeat.updated_at = utcnow()
      await db.commit()
  except Exception as exc:
    logger.warning("Could not mark Engine heartbeat offline: %s", exc)


async def _stop_component(
  name: str,
  callback: Callable[[], Awaitable[None]],
) -> None:
  try:
    await asyncio.wait_for(callback(), timeout=10.0)
  except Exception as exc:
    logger.warning("Stopping %s failed: %s", name, exc)


async def _stop_engine_tasks(tasks: list[asyncio.Task]) -> None:
  if not tasks:
    return
  pending = [task for task in tasks if not task.done()]
  try:
    await asyncio.wait_for(
      asyncio.gather(*tasks, return_exceptions=True),
      timeout=ENGINE_SHUTDOWN_TIMEOUT_SECONDS,
    )
    return
  except asyncio.TimeoutError:
    logger.warning(
      "Engine tasks did not stop within %.0fs; cancelling %s task(s)",
      ENGINE_SHUTDOWN_TIMEOUT_SECONDS,
      len(pending),
    )
  for task in pending:
    if not task.done():
      task.cancel()
  await asyncio.gather(*tasks, return_exceptions=True)


async def run_engine() -> None:
  stopped = asyncio.Event()
  loop = asyncio.get_running_loop()
  for signal_name in ("SIGINT", "SIGTERM"):
    process_signal = getattr(signal, signal_name, None)
    if process_signal is None:
      continue
    try:
      loop.add_signal_handler(process_signal, stopped.set)
    except NotImplementedError:
      signal.signal(
        process_signal,
        lambda *_: loop.call_soon_threadsafe(stopped.set),
      )

  await db_manager.initialize()
  lock_connection = await engine.connect()
  try:
    # The advisory lease is a process-lifetime dedicated connection, not
    # workload. Detaching keeps one shared QueuePool for Engine business work
    # while the detached connection is physically closed during shutdown.
    _detach_engine_lease_connection(lock_connection)
    await _acquire_engine_lease(lock_connection)
  except Exception:
    await lock_connection.close()
    await db_manager.shutdown()
    raise

  instance_id = _engine_instance_id()
  tasks: list[asyncio.Task] = []
  try:
    set_intraday_warm_cache(intraday_warm_cache)
    await market_data_service.initialize()
    await whole_quote_hub.start()
    await realtime_manager.start()
    await limit_up_radar_monitor.start()
    await intraday_warm_cache.start()
    await strategy_manager.start()
    await exit_plan_monitor.start()
    await conditional_liquidation_monitor.start()
    await t_trade_global_monitor.start()
    await limit_up_board_assistant.start()
    tasks = [
      asyncio.create_task(
        _heartbeat(stopped, instance_id),
        name="engine-heartbeat",
      ),
      asyncio.create_task(
        _lease_watchdog(stopped, lock_connection),
        name="engine-lease-watchdog",
      ),
      asyncio.create_task(
        run_report_consumer(stopped),
        name="agent-report-consumer",
      ),
      asyncio.create_task(
        run_command_consumer(stopped),
        name="engine-command-consumer",
      ),
      asyncio.create_task(
        run_subscription_bridge(stopped),
        name="runtime-subscription-bridge",
      ),
      asyncio.create_task(
        run_market_query_bridge(stopped),
        name="runtime-market-query-bridge",
      ),
    ]
    logger.info("QuantX engine ready: instance_id=%s", instance_id)
    await _wait_for_stop_or_failure(stopped, tasks)
  finally:
    stopped.set()
    await _stop_engine_tasks(tasks)
    await _stop_component("t-trade monitor", t_trade_global_monitor.stop)
    await _stop_component(
      "limit-up board assistant", limit_up_board_assistant.stop
    )
    await _stop_component(
      "conditional liquidation monitor",
      conditional_liquidation_monitor.stop,
    )
    await _stop_component("exit plan monitor", exit_plan_monitor.stop)
    await _stop_component("strategy manager", strategy_manager.stop)
    await _stop_component("intraday warm cache", intraday_warm_cache.shutdown)
    await _stop_component("limit-up radar", limit_up_radar_monitor.stop)
    await _stop_component("realtime manager", realtime_manager.stop)
    await _stop_component("whole quote hub", whole_quote_hub.stop)
    await _stop_component("market data", market_data_service.shutdown)
    set_intraday_warm_cache(None)
    await _mark_engine_offline(instance_id)
    try:
      try:
        await lock_connection.execute(
          text("SELECT pg_advisory_unlock(hashtext(:lock_name))"),
          {"lock_name": ENGINE_LOCK_NAME},
        )
        await lock_connection.commit()
      except Exception as exc:
        logger.warning("Could not explicitly release Engine lease: %s", exc)
    finally:
      await lock_connection.close()
      await db_manager.shutdown()
    logger.info("QuantX engine stopped")


async def run_engine_supervised() -> None:
  """Restart the Engine after a critical task or database lease failure."""
  delay = 1.0
  while True:
    try:
      await run_engine()
      return
    except asyncio.CancelledError:
      raise
    except Exception:
      logger.exception(
        "QuantX engine failed; restarting in %.0fs",
        delay,
      )
      await asyncio.sleep(delay)
      delay = min(delay * 2, ENGINE_RESTART_MAX_DELAY_SECONDS)


def main() -> None:
  logging.basicConfig(level=logging.INFO)
  asyncio.run(run_engine_supervised())


if __name__ == "__main__":
  main()
