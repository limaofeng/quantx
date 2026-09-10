"""Engine lifecycle with a PostgreSQL single-instance lease."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import uuid
from dataclasses import dataclass, field
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
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)
from quantx_infrastructure.services.auto_exit_plan_service import (
  ActiveRuntimeExitPlanOwnerAuditError,
  ActiveRuntimeExitPlanOwnerAuditFailure,
  AutoExitPlanService,
)
from quantx_infrastructure.services.engine_archive_generation import (
  ENGINE_LOCK_NAME,
  register_engine_archive_generation,
)
from quantx_infrastructure.services.limit_up_radar import limit_up_radar_monitor
from quantx_infrastructure.services.t_trade_monitor_projection_service import (
  t_trade_monitor_projection_service,
)
from sqlalchemy import text

from .command_processor import run_command_consumer
from .conditional_liquidation import conditional_liquidation_monitor
from .exit_plan_runtime import exit_plan_runtime
from .limit_up_board_runtime import limit_up_board_assistant
from .realtime_manager import realtime_manager
from .report_processor import run_report_consumer
from .risk_increase_admission_runtime import risk_increase_admission_runtime
from .strategy_manager import strategy_manager
from .subscription_bridge import (
  run_market_query_bridge,
  run_subscription_bridge,
)
from .t_order_lifecycle import run_t_order_lifecycle
from .t_trade_observability import t_trade_runtime_observability
from .t_trade_runtime import t_trade_global_monitor
from .warm_cache import intraday_warm_cache

logger = logging.getLogger(__name__)
ENGINE_LEASE_ACQUIRE_TIMEOUT_SECONDS = 90.0
ENGINE_LEASE_RETRY_SECONDS = 2.0
ENGINE_LEASE_IDLE_TIMEOUT_SECONDS = 60
ENGINE_DATABASE_OPERATION_TIMEOUT_SECONDS = 10.0
ENGINE_HEARTBEAT_RETRY_SECONDS = 1.0
ENGINE_RUNTIME_OWNER_AUDIT_SECONDS = 2.0
ENGINE_RUNTIME_OWNER_RETRY_SECONDS = 5.0
ENGINE_SHUTDOWN_TIMEOUT_SECONDS = 15.0
ENGINE_RESTART_MAX_DELAY_SECONDS = 30.0


@dataclass
class EngineOperationalState:
  """In-process safety state published independently from Engine liveness."""

  status: str = "starting"
  reason_code: str = "ENGINE_TRADING_RUNTIME_STARTING"
  trading_ready: bool = False
  owner_audit: dict[str, object] = field(default_factory=dict)
  transitioned_at: str = field(default_factory=lambda: utcnow().isoformat())

  def mark_ready(self, audit: dict[str, object]) -> None:
    self.status = "ready"
    self.reason_code = ""
    self.trading_ready = True
    self.owner_audit = {
      "status": "passed",
      "examined": int(audit.get("examined") or 0),
      "verified": len(list(audit.get("verified") or [])),
      "healthyCycles": 2,
    }
    self.transitioned_at = utcnow().isoformat()

  def mark_owner_audit_degraded(
    self,
    error: ActiveRuntimeExitPlanOwnerAuditError,
  ) -> None:
    self.status = "degraded"
    self.reason_code = error.code
    self.trading_ready = False
    self.owner_audit = {
      "status": "failed",
      "failures": [item.to_dict() for item in error.failures],
      "affectedAccountIds": list(error.account_ids),
    }
    self.transitioned_at = utcnow().isoformat()

  def snapshot(self) -> dict[str, object]:
    return {
      "status": self.status.upper(),
      "reasonCode": self.reason_code,
      "tradingReady": self.trading_ready,
      "ownerAudit": dict(self.owner_audit),
      "transitionedAt": self.transitioned_at,
    }


def _engine_instance_id() -> str:
  configured = os.environ.get("QUANTX_ENGINE_INSTANCE_ID", "").strip()
  if not configured:
    return str(uuid.uuid4())
  if len(configured) > 64:
    raise RuntimeError("QUANTX_ENGINE_INSTANCE_ID exceeds the heartbeat schema limit")
  return configured


async def _write_heartbeat_once(
  instance_id: str,
  operational_state: EngineOperationalState | None = None,
) -> None:
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
    heartbeat_status = "ready"
    if operational_state is not None:
      runtime_safety = operational_state.snapshot()
      heartbeat_status = operational_state.status
      details.update(
        {
          "reasonCode": operational_state.reason_code,
          "tradingReady": operational_state.trading_ready,
          "runtimeSafety": runtime_safety,
        }
      )
    if heartbeat is None:
      db.add(
        RuntimeComponentHeartbeat(
          component="engine",
          instance_id=instance_id,
          status=heartbeat_status,
          details=details,
          updated_at=utcnow(),
        )
      )
    else:
      heartbeat.instance_id = instance_id
      heartbeat.status = heartbeat_status
      heartbeat.details = details
      heartbeat.updated_at = utcnow()
    await db.commit()


async def _heartbeat(
  stopped: asyncio.Event,
  instance_id: str,
  operational_state: EngineOperationalState | None = None,
) -> None:
  while not stopped.is_set():
    retry_delay = 15.0
    try:
      heartbeat_write = (
        _write_heartbeat_once(instance_id)
        if operational_state is None
        else _write_heartbeat_once(instance_id, operational_state)
      )
      await asyncio.wait_for(
        heartbeat_write,
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


async def _runtime_owner_watchdog(stopped: asyncio.Event) -> None:
  """Continuously enforce that every active public plan has its sole consumer."""

  service = AutoExitPlanService()
  while not stopped.is_set():
    await service.audit_active_runtime_owned_plans()
    if not exit_plan_runtime.is_running:
      raise ActiveRuntimeExitPlanOwnerAuditError(
        [
          ActiveRuntimeExitPlanOwnerAuditFailure(
            plan_id="",
            strategy_run_id="",
            account_id="",
            owner_kind="PUBLIC_EXIT_PLAN_RUNTIME",
            reason_code="EXIT_PLAN_RUNTIME_STOPPED",
            message="ExitPlanRuntime public plan consumer is not running",
            stage="runtime",
          )
        ]
      )
    if not risk_increase_admission_runtime.is_running:
      raise RuntimeError("public LIVE BUY admission dispatcher is not running")
    try:
      await asyncio.wait_for(
        stopped.wait(),
        timeout=ENGINE_RUNTIME_OWNER_AUDIT_SECONDS,
      )
    except asyncio.TimeoutError:
      pass


async def _start_and_reconcile_runtime_exit_plans() -> dict[str, object]:
  """Restore source runtimes independently, then re-audit public plans."""

  await strategy_manager.start()
  service = AutoExitPlanService()
  audit = await service.audit_active_runtime_owned_plans()
  if audit["examined"]:
    logger.info(
      "Runtime exit-plan owner audit passed: examined=%s verified=%s",
      audit["examined"],
      len(audit["verified"]),
    )
  return {"audit": audit}


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


def _owner_audit_error_from(
  error: BaseException,
) -> ActiveRuntimeExitPlanOwnerAuditError | None:
  current: BaseException | None = error
  visited: set[int] = set()
  while current is not None and id(current) not in visited:
    visited.add(id(current))
    if isinstance(current, ActiveRuntimeExitPlanOwnerAuditError):
      return current
    current = current.__cause__ or current.__context__
  return None


async def _relay_parent_stop(
  parent_stopped: asyncio.Event,
  cycle_stopped: asyncio.Event,
) -> None:
  await parent_stopped.wait()
  cycle_stopped.set()


async def _pause_owner_audit_accounts(
  error: ActiveRuntimeExitPlanOwnerAuditError,
) -> None:
  failures = [item.to_dict() for item in error.failures]
  service = AccountExecutionSafetyService()
  for account_id in error.account_ids:
    changed = await service.pause_for_runtime_owner_audit(
      account_id,
      failures=failures,
    )
    logger.error(
      "Engine owner audit closed account execution: account_id=%s changed=%s",
      account_id,
      changed,
    )


async def _trading_runtime_supervisor(
  stopped: asyncio.Event,
  operational_state: EngineOperationalState,
) -> None:
  """Keep the core data plane alive while trading ownership is unsafe."""

  while not stopped.is_set():
    cycle_stopped = asyncio.Event()
    cycle_tasks: list[asyncio.Task] = []
    exit_plan_started = False
    strategy_start_attempted = False
    conditional_started = False
    t_trade_started = False
    limit_up_board_started = False
    admission_started = False
    owner_failure: ActiveRuntimeExitPlanOwnerAuditError | None = None
    try:
      service = AutoExitPlanService()
      await service.preflight_active_runtime_owned_plans()
      await exit_plan_runtime.start()
      exit_plan_started = True
      strategy_start_attempted = True
      startup = await _start_and_reconcile_runtime_exit_plans()
      first_audit = dict(startup["audit"])
      try:
        await asyncio.wait_for(
          stopped.wait(),
          timeout=ENGINE_RUNTIME_OWNER_AUDIT_SECONDS,
        )
        return
      except asyncio.TimeoutError:
        pass
      second_audit = await service.audit_active_runtime_owned_plans()
      if not exit_plan_runtime.is_running:
        raise ActiveRuntimeExitPlanOwnerAuditError(
          [
            ActiveRuntimeExitPlanOwnerAuditFailure(
              plan_id="",
              strategy_run_id="",
              account_id="",
              owner_kind="PUBLIC_EXIT_PLAN_RUNTIME",
              reason_code="EXIT_PLAN_RUNTIME_STOPPED",
              message="ExitPlanRuntime public plan consumer is not running",
              stage="startup",
            )
          ]
        )
      await conditional_liquidation_monitor.start()
      conditional_started = True
      await t_trade_global_monitor.start()
      t_trade_started = True
      await limit_up_board_assistant.start()
      limit_up_board_started = True
      await risk_increase_admission_runtime.start()
      admission_started = True
      operational_state.mark_ready(second_audit)
      cycle_tasks = [
        asyncio.create_task(
          run_t_order_lifecycle(cycle_stopped),
          name="t-order-lifecycle",
        ),
        asyncio.create_task(
          _relay_parent_stop(stopped, cycle_stopped),
          name="engine-trading-stop-relay",
        ),
        asyncio.create_task(
          _runtime_owner_watchdog(cycle_stopped),
          name="runtime-exit-plan-owner-watchdog",
        ),
        asyncio.create_task(
          run_command_consumer(cycle_stopped),
          name="engine-command-consumer",
        ),
      ]
      logger.info(
        "Engine trading runtime ready after owner audit: "
        "first_examined=%s second_examined=%s",
        first_audit.get("examined"),
        second_audit.get("examined"),
      )
      await _wait_for_stop_or_failure(cycle_stopped, cycle_tasks)
      if stopped.is_set():
        return
      raise RuntimeError("Engine trading runtime stopped unexpectedly")
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      owner_failure = _owner_audit_error_from(exc)
      if owner_failure is None:
        raise
      cycle_stopped.set()
      operational_state.mark_owner_audit_degraded(owner_failure)
      logger.error(
        "Engine trading runtime degraded by owner audit: diagnostics=%s",
        json.dumps(
          owner_failure.to_dict(),
          ensure_ascii=False,
          separators=(",", ":"),
          sort_keys=True,
        ),
      )
      try:
        await _pause_owner_audit_accounts(owner_failure)
      except Exception:
        # The degraded Engine heartbeat and stopped command consumer still
        # close execution. Retry the durable account pause on the next audit.
        logger.exception("Could not persist Engine owner-audit account pause")
    finally:
      cycle_stopped.set()
      await _stop_engine_tasks(cycle_tasks)
      if limit_up_board_started:
        await _stop_component(
          "limit-up board assistant",
          limit_up_board_assistant.stop,
        )
      if admission_started or risk_increase_admission_runtime.is_running:
        await _stop_component(
          "risk-increase admission runtime",
          risk_increase_admission_runtime.stop,
        )
      if t_trade_started:
        await _stop_component("t-trade monitor", t_trade_global_monitor.stop)
      if conditional_started:
        await _stop_component(
          "conditional liquidation monitor",
          conditional_liquidation_monitor.stop,
        )
      if strategy_start_attempted or bool(
        getattr(strategy_manager, "running", False)
      ):
        await _stop_component("strategy manager", strategy_manager.stop)
      if exit_plan_started or exit_plan_runtime.is_running:
        await _stop_component("exit plan runtime", exit_plan_runtime.stop)

    if owner_failure is None or stopped.is_set():
      return
    try:
      await asyncio.wait_for(
        stopped.wait(),
        timeout=ENGINE_RUNTIME_OWNER_RETRY_SECONDS,
      )
    except asyncio.TimeoutError:
      pass


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
    archive_generation = await register_engine_archive_generation(
      lock_connection, str(uuid.uuid4())
    )
  except Exception:
    await lock_connection.close()
    await db_manager.shutdown()
    raise

  instance_id = _engine_instance_id()
  operational_state = EngineOperationalState()
  tasks: list[asyncio.Task] = []
  try:
    set_intraday_warm_cache(intraday_warm_cache)
    await market_data_service.initialize()
    await whole_quote_hub.start()
    await realtime_manager.start(archive_generation=archive_generation)
    await limit_up_radar_monitor.start()
    await intraday_warm_cache.start()
    tasks = [
      asyncio.create_task(
        _heartbeat(stopped, instance_id, operational_state),
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
        run_subscription_bridge(stopped),
        name="runtime-subscription-bridge",
      ),
      asyncio.create_task(
        run_market_query_bridge(stopped),
        name="runtime-market-query-bridge",
      ),
      asyncio.create_task(
        _trading_runtime_supervisor(stopped, operational_state),
        name="engine-trading-runtime-supervisor",
      ),
    ]
    logger.info(
      "QuantX engine core data plane online: instance_id=%s",
      instance_id,
    )
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
    await _stop_component("exit plan runtime", exit_plan_runtime.stop)
    await _stop_component(
      "risk-increase admission runtime",
      risk_increase_admission_runtime.stop,
    )
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
