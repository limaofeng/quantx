import asyncio
import logging.config
import os
import signal
import sys
import threading
import time

# Trigger reload
from contextlib import asynccontextmanager
from ipaddress import ip_address
from typing import Callable

import uvicorn
from core.data.intraday_warm_cache import intraday_warm_cache
from core.data.market_data_service import market_data_service
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth.router import auth_router
from auth.service import AuthService
from config.settings import create_log_directory, settings
from core.realtime_manager import realtime_manager
from core.strategy_manager import strategy_manager
from database.manager import db_manager
from database.relational_connection import get_async_db
from gqlapi import setup_graphql
from monitoring import get_prometheus_metrics
from monitoring.metrics import REQUEST_COUNT, REQUEST_DURATION
from prefector.prefect_manager import prefect_manager
from services.liquidation_service import conditional_liquidation_monitor
from services.t_trade_global_monitor import t_trade_global_monitor

# 创建日志目录
create_log_directory()

# 配置日志
logging.config.dictConfig(settings.get_log_config())
logger = logging.getLogger(__name__)

SHUTDOWN_WATCHDOG_SECONDS = 40.0
RELOAD_WORKER_EXIT_SECONDS = 10.0
WINDOWS_CLIENT_DISCONNECT_WINERRORS = {10053, 10054, 10058}
DEV_SHUTDOWN_PATH = "/_dev/shutdown"


def _configure_windows_event_loop_policy() -> None:
  if sys.platform != "win32":
    return

  selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
  if selector_policy is None:
    return

  try:
    asyncio.set_event_loop_policy(selector_policy())
  except Exception as exc:
    logging.getLogger(__name__).debug(
      "设置 Windows SelectorEventLoopPolicy 失败: %s", exc
    )


_configure_windows_event_loop_policy()


def _signal_name(sig: int) -> str:
  try:
    return signal.Signals(sig).name
  except Exception:
    return str(sig)


def _format_live_threads() -> str:
  threads = []
  current_thread = threading.current_thread()
  for thread in threading.enumerate():
    if thread is current_thread or not thread.is_alive():
      continue
    threads.append(
      f"{thread.name}(daemon={thread.daemon}, ident={thread.ident})"
    )
  return ", ".join(threads) if threads else "none"


def _is_windows_client_disconnect(exc: BaseException) -> bool:
  if sys.platform != "win32" or not isinstance(exc, ConnectionResetError):
    return False

  codes = {
    getattr(exc, "winerror", None),
    getattr(exc, "errno", None),
    *(arg for arg in getattr(exc, "args", ()) if isinstance(arg, int)),
  }
  return bool(codes & WINDOWS_CLIENT_DISCONNECT_WINERRORS)


def _install_asyncio_client_disconnect_filter() -> None:
  if sys.platform != "win32":
    return

  try:
    loop = asyncio.get_running_loop()
  except RuntimeError:
    return

  if getattr(loop, "_quantx_client_disconnect_filter", False):
    return

  previous_handler = loop.get_exception_handler()

  def handle_loop_exception(loop, context):
    exc = context.get("exception")
    if exc is not None and _is_windows_client_disconnect(exc):
      logger.debug("忽略 Windows 客户端主动断开连接: %s", exc)
      return

    if previous_handler:
      previous_handler(loop, context)
    else:
      loop.default_exception_handler(context)

  loop.set_exception_handler(handle_loop_exception)
  setattr(loop, "_quantx_client_disconnect_filter", True)


def _is_local_request(request: Request) -> bool:
  client_host = request.client.host if request.client else ""
  if not client_host:
    return False

  try:
    return ip_address(client_host).is_loopback
  except ValueError:
    return client_host.lower() == "localhost"


def _schedule_dev_shutdown() -> None:
  def request_shutdown() -> None:
    time.sleep(0.2)
    try:
      signal.raise_signal(signal.SIGINT)
    except Exception as exc:
      logger.error("触发开发环境关闭信号失败: %s", exc)

  thread = threading.Thread(
    target=request_shutdown,
    name="QuantXDevShutdownRequest",
    daemon=True,
  )
  thread.start()


class ShutdownWatchdog:
  """Force process exit when third-party SDK threads block graceful shutdown."""

  def __init__(self, timeout: float):
    self.timeout = timeout
    self._cancel_event = threading.Event()
    self._lock = threading.Lock()
    self._started = False
    self._reason = ""

  def start(self, reason: str) -> None:
    with self._lock:
      if self._started:
        return
      self._started = True
      self._reason = reason
      thread = threading.Thread(
        target=self._run,
        name="QuantXShutdownWatchdog",
        daemon=True,
      )
      thread.start()

  def cancel(self) -> None:
    self._cancel_event.set()

  def _run(self) -> None:
    if self._cancel_event.wait(self.timeout):
      return

    try:
      logger.critical(
        "应用退出超时 %.1f 秒，将强制结束进程。reason=%s, live_threads=%s",
        self.timeout,
        self._reason,
        _format_live_threads(),
      )
    except Exception:
      pass
    os._exit(130)


_shutdown_watchdog = ShutdownWatchdog(SHUTDOWN_WATCHDOG_SECONDS)


class QuantXUvicornServer(uvicorn.Server):
  """Uvicorn Server with QuantX shutdown diagnostics and force-exit guard."""

  def handle_exit(self, sig, frame) -> None:
    if self.should_exit and sig == signal.SIGINT:
      logger.warning("再次收到 Ctrl+C，请求 uvicorn 强制退出")
    else:
      logger.info("收到退出信号 %s，开始优雅关闭", _signal_name(sig))

    _shutdown_watchdog.start(f"signal={_signal_name(sig)}")
    super().handle_exit(sig, frame)

  def run(self, sockets=None) -> None:
    try:
      return super().run(sockets=sockets)
    finally:
      _shutdown_watchdog.cancel()


async def _run_blocking_cleanup(
  name: str,
  cleanup: Callable[[], None],
  timeout: float,
) -> None:
  """Run blocking SDK cleanup in a daemon thread so it cannot pin shutdown."""
  loop = asyncio.get_running_loop()
  future = loop.create_future()

  def finish(result=None, error=None):
    if future.done():
      return
    if error is not None:
      future.set_exception(error)
    else:
      future.set_result(result)

  def notify(result=None, error=None) -> None:
    try:
      loop.call_soon_threadsafe(finish, result, error)
    except RuntimeError:
      pass

  def runner() -> None:
    try:
      cleanup()
      notify()
    except Exception as exc:
      notify(None, exc)

  thread = threading.Thread(
    target=runner,
    name=f"QuantXCleanup-{name}",
    daemon=True,
  )
  thread.start()
  await asyncio.wait_for(future, timeout=timeout)


async def _shutdown_miniqmt_managers() -> None:
  """Close miniQMT registry-owned connections created outside data service."""
  from miniqmt.manager_registry import XTDataManagerRegistry, XTTradingManagerRegistry

  def cleanup() -> None:
    XTTradingManagerRegistry().clear_all_managers()
    XTDataManagerRegistry().clear_all_managers()

  await _run_blocking_cleanup("miniQMT", cleanup, timeout=3.0)


def _request_process_exit(process, name: str, timeout: float) -> None:
  if process is None or not process.is_alive():
    return

  pid = getattr(process, "pid", None)
  logger.info("正在停止 %s (PID: %s)", name, pid)
  if sys.platform == "win32":
    try:
      os.kill(pid, signal.CTRL_C_EVENT)
      sys.stdout.write(" ")
      sys.stdout.flush()
    except Exception as exc:
      logger.warning("发送 CTRL_C_EVENT 到 %s 失败: %s，使用 terminate", name, exc)
      process.terminate()
  else:
    process.terminate()

  process.join(timeout=timeout)
  if not process.is_alive():
    return

  logger.warning("%s 未在 %.1f 秒内退出，使用 terminate", name, timeout)
  process.terminate()
  process.join(timeout=3.0)
  if not process.is_alive():
    return

  kill = getattr(process, "kill", None)
  if callable(kill):
    logger.warning("%s terminate 后仍未退出，使用 kill", name)
    kill()
    process.join(timeout=3.0)


def _quantx_reload_supervisor(base_cls):
  class QuantXReloadSupervisor(base_cls):
    def signal_handler(self, sig, frame) -> None:
      logger.info("收到 reload 监督进程退出信号 %s", _signal_name(sig))
      _shutdown_watchdog.start(f"reload-supervisor-signal={_signal_name(sig)}")
      super().signal_handler(sig, frame)

    def shutdown(self) -> None:
      if sys.platform != "win32":
        return super().shutdown()

      self.should_exit.set()
      _request_process_exit(
        getattr(self, "process", None),
        "Uvicorn reload worker",
        RELOAD_WORKER_EXIT_SECONDS,
      )
      for sock in self.sockets:
        sock.close()

      logging.getLogger("uvicorn.error").info(
        "Stopping reloader process [%s]",
        str(getattr(self, "pid", os.getpid())),
      )

  QuantXReloadSupervisor.__name__ = "QuantXReloadSupervisor"
  return QuantXReloadSupervisor


def _probe_miniqmt_health() -> dict:
  """Probe miniQMT by querying the default account status.

  The trading gateway is only considered healthy when account status is OK,
  not merely when the XTQuant trader reports a connected session.
  """
  from miniqmt.manager_registry import XTDataManagerRegistry, XTTradingManagerRegistry

  data_registry = XTDataManagerRegistry()
  trading_registry = XTTradingManagerRegistry()

  data_manager_stats = data_registry.get_stats()
  trading_manager_stats = trading_registry.get_stats()
  account_checked = False
  account_connected = False
  account_error = ""

  try:
    with trading_registry._lock:
      trading_manager = next(iter(trading_registry._managers.values()), None)

    account_checked = trading_manager is not None
    account_connected = (
      trading_manager.is_account_status_ok() if trading_manager else False
    )
    if account_checked and not account_connected:
      account_error = "account_status_not_ok"
  except Exception as exc:
    account_checked = True
    account_error = exc.__class__.__name__

  trading_manager_stats = trading_registry.get_stats()

  return {
    "available": account_connected,
    "data_connected": data_manager_stats.get("connected_data_managers", 0) > 0,
    "trading_connected": trading_manager_stats.get("connected_managers", 0) > 0,
    "account_checked": account_checked,
    "account_connected": account_connected,
    "connected": account_connected,
    "connection_state": "account_verified"
    if account_connected
    else (
      "connected_account_unavailable"
      if trading_manager_stats.get("connected_managers", 0) > 0
      else "disconnected"
    ),
    "account_error": account_error,
    "data_managers": data_manager_stats,
    "trading_managers": trading_manager_stats,
  }


@asynccontextmanager
async def lifespan(app: FastAPI):
  """应用生命周期管理"""
  # 启动时
  _install_asyncio_client_disconnect_filter()
  logger.info("启动 QuantX API 服务器...")
  logger.info(f"环境: {settings.environment}")
  logger.info(f"调试模式: {settings.debug}")
  logger.info(f"API 地址: http://{settings.host}:{settings.port}")

  # 初始化数据库
  try:
    await db_manager.initialize()
    logger.info("数据库初始化完成")
    async for auth_db in get_async_db():
      await AuthService.bootstrap_from_settings(auth_db)
      break
  except Exception as e:
    logger.error(f"数据库初始化失败: {e}")
    raise

  # 初始化数据提供者
  try:
    await market_data_service.initialize()
    logger.info("数据提供者初始化完成")
  except Exception as e:
    logger.error(f"数据提供者初始化失败: {e}")
    # 数据提供者初始化失败不阻止应用启动，但记录警告
    logger.warning("数据提供者初始化失败，某些功能可能受影响")

  # 启动实时数据管理器
  await realtime_manager.start()
  await intraday_warm_cache.start()

  # 启动策略管理器 (内部会自动完成策略发现和同步)
  await strategy_manager.start()

  # 启动条件清仓单监控器
  await conditional_liquidation_monitor.start()

  # 启动账户级全局持仓做 T 编排器
  await t_trade_global_monitor.start()

  # 启动 Prefect 服务
  await prefect_manager.start()

  # MCP integration is temporarily disabled. Keep the original code commented so
  # it can be restored quickly after the startup/shutdown issues are resolved.
  mcp_task = None
  _mcp_http_app = None
  # try:
  #   from config.mcp_config import mcp_settings
  #   if mcp_settings.enabled:
  #     mode = mcp_settings.mode.lower().strip()
  #     if mode == "stdio":
  #       logger.info("启动 MCP Server (stdio)...")
  #       from quantx_mcp import start_background_mcp_server
  #       mcp_task = await start_background_mcp_server()
  #       logger.info("MCP Server 已启动 (stdio)")
  #     # HTTP 模式 - 手动启动 session manager
  #     elif mode in ("sse", "streamable-http", "http"):
  #       logger.info("启动 MCP HTTP session manager...")
  #       from quantx_mcp import create_mcp_http_app
  #       _mcp_http_app = create_mcp_http_app(mode=mode)
  #       app.mount(mcp_settings.transport.path, _mcp_http_app)
  #       logger.info(f"MCP HTTP 路由已挂载: {mcp_settings.transport.path} (mode={mode})")
  #       # 启动 session manager
  #       await _mcp_http_app.mcp_manager.start()
  #       logger.info("MCP HTTP session manager 已启动")
  # except ImportError as e:
  #   logger.warning(f"MCP 模块未安装，跳过 MCP Server 启动: {e}")
  # except Exception as e:
  #   logger.error(f"MCP Server 启动失败: {e}")
  #   logger.warning("MCP Server 启动失败，但主应用继续运行")

  yield

  # 关闭时 - 使用统一的超时包装函数
  async def stop_with_timeout(name, coro, timeout=5.0):
    """带超时的停止操作"""
    try:
      await asyncio.wait_for(coro, timeout=timeout)
      logger.info(f"{name} 已停止")
    except asyncio.TimeoutError:
      logger.warning(f"{name} 停止超时({timeout}秒),强制跳过")
    except asyncio.CancelledError:
      logger.info(f"{name} 已取消")
    except Exception as e:
      logger.error(f"停止 {name} 时出错: {e}")

  # MCP integration is temporarily disabled.
  # # 停止 MCP stdio Server
  # if mcp_task:
  #   try:
  #     mcp_task.cancel()
  #     await asyncio.wait_for(mcp_task, timeout=3.0)
  #     logger.info("MCP Server 已停止 (stdio)")
  #   except asyncio.TimeoutError:
  #     logger.warning("MCP Server 停止超时,强制跳过 (stdio)")
  #   except asyncio.CancelledError:
  #     logger.info("MCP Server 已取消 (stdio)")
  #   except Exception as e:
  #     logger.error(f"停止 MCP Server 时出错: {e}")

  # # 停止 MCP HTTP session manager
  # if _mcp_http_app is not None:
  #   await stop_with_timeout("MCP HTTP session manager", _mcp_http_app.mcp_manager.stop(), 5.0)

  # 停止其他组件(按依赖关系倒序)
  await stop_with_timeout("Prefect 任务调度服务", prefect_manager.stop(), 3.0)
  await stop_with_timeout("全局做 T 监控器", t_trade_global_monitor.stop(), 3.0)
  await stop_with_timeout("条件清仓单监控器", conditional_liquidation_monitor.stop(), 3.0)
  await stop_with_timeout("策略管理器", strategy_manager.stop(), 5.0)
  await stop_with_timeout("日内热缓存", intraday_warm_cache.shutdown(), 3.0)
  await stop_with_timeout("实时数据管理器", realtime_manager.stop(), 3.0)
  await stop_with_timeout("数据提供者", market_data_service.shutdown(), 3.0)
  await stop_with_timeout("miniQMT 管理器", _shutdown_miniqmt_managers(), 4.0)

  # 关闭数据库连接
  await stop_with_timeout("数据库连接", db_manager.shutdown(), 3.0)

  logger.info("QuantX API 服务器已关闭")


app = FastAPI(
  title="QuantX API",
  description="量化交易系统API - GraphQL版本",
  version="2.0.0",
  lifespan=lifespan,
  debug=settings.debug,
  docs_url="/docs" if settings.is_development else None,
  redoc_url="/redoc" if settings.is_development else None,
)

# 配置CORS
app.add_middleware(
  CORSMiddleware,
  allow_origins=settings.cors_origins,
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
  """Attach a bounded request ID without logging credentials or request bodies."""
  incoming = request.headers.get("x-request-id", "").strip()
  if not incoming or len(incoming) > 64 or not incoming.replace("-", "").isalnum():
    incoming = f"req-{time.time_ns():x}"
  request.state.request_id = incoming
  response = await call_next(request)
  response.headers["X-Request-ID"] = incoming
  return response


if settings.metrics_enabled:

  @app.middleware("http")
  async def metrics_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
      return await call_next(request)

    start_time = time.time()
    method = request.method
    endpoint = request.url.path

    try:
      response = await call_next(request)
      REQUEST_COUNT.labels(
        method=method, endpoint=endpoint, status_code=str(response.status_code)
      ).inc()
      REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(
        time.time() - start_time
      )
      return response

    except Exception:
      REQUEST_COUNT.labels(
        method=method, endpoint=endpoint, status_code="500"
      ).inc()
      REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(
        time.time() - start_time
      )
      raise

if settings.environment != "production" or settings.debug:

  @app.middleware("http")
  async def request_logging_middleware(request: Request, call_next):
    start_time = time.time()
    client_ip = request.client.host if request.client else "unknown"

    try:
      response = await call_next(request)
      process_time = time.time() - start_time
      logger.info(
        f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s - {client_ip}"
      )
      return response
    except Exception as exc:
      process_time = time.time() - start_time
      logger.error(
        f"{request.method} {request.url.path} - ERROR: {str(exc)} - {process_time:.3f}s - {client_ip}"
      )
      raise

@app.middleware("http")
async def error_handler_middleware(request: Request, call_next):
  try:
    response = await call_next(request)
    return response

  except HTTPException as exc:
    raise exc

  except asyncio.CancelledError:
    raise

  except Exception as exc:
    error_id = f"error_{int(time.time())}"
    logger.error(f"Unhandled exception [{error_id}]: {str(exc)} - {request.method} {request.url.path}")
    return JSONResponse(
      status_code=500,
      content={
        "error": "Internal Server Error",
        "message": "An unexpected error occurred",
        "error_id": error_id,
        "timestamp": int(time.time()),
      },
    )


# 设置认证 REST API 与 GraphQL
app.include_router(auth_router)
setup_graphql(app)


@app.get("/")
async def root():
  """根路径端点"""
  return {
    "message": "QuantX API is running with GraphQL",
    "version": "2.0.0",
    "environment": settings.environment,
    "graphql_endpoint": "/graphql",
    "docs_url": "/docs" if settings.is_development else None,
  }


@app.post(DEV_SHUTDOWN_PATH)
async def request_dev_shutdown(request: Request):
  """Request a local development shutdown so lifespan cleanup can run."""
  if not (settings.is_development or settings.debug):
    raise HTTPException(status_code=404, detail="Not found")

  if not _is_local_request(request):
    raise HTTPException(status_code=403, detail="Forbidden")

  logger.info("收到本地开发环境关闭请求，准备优雅退出")
  _schedule_dev_shutdown()
  return {"status": "shutdown_requested"}


@app.get("/health")
async def health_check():
  """健康检查端点"""
  try:
    db_health = await asyncio.wait_for(
      asyncio.to_thread(db_manager.health_check), timeout=2.0
    )
  except asyncio.TimeoutError:
    db_health = {
      "relational_db": True,
      "timeseries_db": False,
      "timeseries_error": "timeout",
    }
  except Exception as exc:
    db_health = {
      "relational_db": True,
      "timeseries_db": False,
      "timeseries_error": exc.__class__.__name__,
    }

  prefect_status = prefect_manager.get_status()

  # 获取策略管理器状态
  strategy_runs = strategy_manager.get_all_runs()
  strategy_status = {
    "running": strategy_manager.running,
    "total_instances": len(strategy_runs),
    "running_instances": len(
      [i for i in strategy_runs if i.status.value == "running"]
    ),
    "error_instances": len(
      [i for i in strategy_runs if i.status.value == "error"]
    ),
  }

  # 获取数据提供者状态
  try:
    market_data_stats = await asyncio.wait_for(
      market_data_service.get_statistics(), timeout=2.0
    )
  except asyncio.TimeoutError:
    market_data_stats = {
      "is_initialized": getattr(market_data_service, "_is_initialized", False),
      "is_connected": False,
      "price_cache_count": 0,
      "position_cache_count": 0,
      "error": "timeout",
    }
  except Exception as exc:
    market_data_stats = {
      "is_initialized": getattr(market_data_service, "_is_initialized", False),
      "is_connected": False,
      "price_cache_count": 0,
      "position_cache_count": 0,
      "error": exc.__class__.__name__,
    }

  market_data_status = {
    "initialized": market_data_stats["is_initialized"],
    "connected": market_data_stats["is_connected"],
    "price_cache_count": market_data_stats["price_cache_count"],
    "position_cache_count": market_data_stats["position_cache_count"],
  }
  if "error" in market_data_stats:
    market_data_status["error"] = market_data_stats["error"]

  # 获取 miniQMT/XTQuant 状态。账户快照可读才认为交易网关健康。
  miniqmt_status = {
    "available": False,
    "data_connected": False,
    "trading_connected": False,
    "account_checked": False,
    "account_connected": False,
    "connected": False,
    "connection_state": "unknown",
    "account_error": "",
    "data_managers": {},
    "trading_managers": {},
  }
  try:
    miniqmt_status.update(
      await asyncio.wait_for(asyncio.to_thread(_probe_miniqmt_health), timeout=5.0)
    )
  except asyncio.TimeoutError:
    miniqmt_status["connection_state"] = "probe_timeout"
    miniqmt_status["account_error"] = "timeout"
  except Exception as exc:
    miniqmt_status["available"] = False
    miniqmt_status["connection_state"] = "unavailable"
    miniqmt_status["account_error"] = exc.__class__.__name__

  return {
    "status": "healthy",
    "version": "2.0.0",
    "api_type": "GraphQL",
    "environment": settings.environment,
    "debug": settings.debug,
    "realtime_enabled": True,
    "database": db_health,
    "prefect": prefect_status,
    "strategy_manager": strategy_status,
    "market_data_service": market_data_status,
    "miniqmt": miniqmt_status,
  }


@app.get("/metrics")
async def metrics():
  """Prometheus指标端点"""
  if not settings.metrics_enabled:
    return {"error": "Metrics are disabled"}
  return get_prometheus_metrics()


def run_api_server() -> None:
  """Run uvicorn with QuantX-specific Ctrl+C and child-process cleanup."""
  from uvicorn.main import STARTUP_FAILURE
  from uvicorn.supervisors import ChangeReload, Multiprocess

  config = uvicorn.Config(
    "main:app",
    host=settings.host,
    port=settings.port,
    log_level="warning",  # 降低 uvicorn 自身的日志级别
    access_log=False,  # 禁用访问日志（已由 middleware 处理）
    reload=settings.debug,
    reload_dirs=["./"] if settings.debug else None,
    reload_includes=["*.py"] if settings.debug else None,
    timeout_keep_alive=5,
    timeout_graceful_shutdown=25,  # 给 lifespan 清理留足够时间
  )
  server = QuantXUvicornServer(config=config)

  try:
    if config.should_reload:
      sock = config.bind_socket()
      reload_cls = _quantx_reload_supervisor(ChangeReload)
      reload_cls(config, target=server.run, sockets=[sock]).run()
    elif config.workers > 1:
      sock = config.bind_socket()
      Multiprocess(config, target=server.run, sockets=[sock]).run()
    else:
      server.run()
  except KeyboardInterrupt:
    pass
  finally:
    _shutdown_watchdog.cancel()
    if config.uds and os.path.exists(config.uds):
      os.remove(config.uds)

  if not server.started and not config.should_reload and config.workers == 1:
    sys.exit(STARTUP_FAILURE)


if __name__ == "__main__":
  run_api_server()
