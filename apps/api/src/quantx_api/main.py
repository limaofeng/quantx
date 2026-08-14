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
from pathlib import Path
from typing import Callable

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from quantx_infrastructure.config.settings import create_log_directory, settings
from quantx_infrastructure.database.manager import db_manager
from quantx_infrastructure.database.relational_connection import get_async_db

from quantx_api.agent_api import agent_router
from quantx_api.agent_hub import agent_connection_hub
from quantx_api.auth.router import auth_router
from quantx_api.auth.service import AuthService
from quantx_api.gqlapi import setup_graphql
from quantx_api.monitoring import get_prometheus_metrics
from quantx_api.monitoring.metrics import REQUEST_COUNT, REQUEST_DURATION
from quantx_api.runtime_status import component_status, readiness_status

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


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Manage API-owned resources only; other processes are independently supervised."""
  _install_asyncio_client_disconnect_filter()
  settings.validate_production()
  logger.info("启动 QuantX API 服务器...")
  logger.info(f"环境: {settings.environment}")
  logger.info(f"调试模式: {settings.debug}")
  logger.info(f"API 地址: http://{settings.host}:{settings.port}")

  try:
    await db_manager.initialize()
    logger.info("数据库初始化完成")
    async for auth_db in get_async_db():
      await AuthService.bootstrap_from_settings(auth_db)
      await AuthService.reconcile_development_auto_login_permissions(auth_db)
      break
  except Exception as e:
    logger.error(f"数据库初始化失败: {e}")
    raise

  agent_hub_stopped = asyncio.Event()
  agent_hub_task = asyncio.create_task(
    agent_connection_hub.run_control_relay(agent_hub_stopped)
  )
  yield
  agent_hub_stopped.set()
  agent_hub_task.cancel()
  await asyncio.gather(agent_hub_task, return_exceptions=True)
  try:
    await asyncio.wait_for(db_manager.shutdown(), timeout=3.0)
  except asyncio.TimeoutError:
    logger.warning("数据库连接停止超时")
  logger.info("QuantX API 服务器已关闭")


app = FastAPI(
  title="QuantX API",
  description="量化交易系统API - GraphQL版本",
  version="2.0.0",
  lifespan=lifespan,
  debug=settings.debug,
  docs_url="/_dev/api-docs" if settings.is_development else None,
  redoc_url="/_dev/api-redoc" if settings.is_development else None,
  openapi_url="/_dev/openapi.json" if settings.is_development else None,
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
    route = request.scope.get("route")
    endpoint = getattr(route, "path", None) or "unmatched"

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
app.include_router(agent_router)
setup_graphql(app)


@app.get("/")
async def root():
  """根路径端点"""
  return {
    "message": "QuantX API is running with GraphQL",
    "version": "2.0.0",
    "environment": settings.environment,
    "graphql_endpoint": "/graphql",
    "docs_url": "/docs/",
    "internal_api_docs_url": (
      "/_dev/api-docs" if settings.is_development else None
    ),
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


@app.get("/health/live")
async def health_live():
  return {"status": "alive", "component": "api"}


@app.get("/health/components")
async def health_components():
  return {"components": await component_status()}


@app.get("/health/ready")
async def health_ready():
  ready, payload = await readiness_status()
  return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/health")
async def health_check():
  return await health_ready()


@app.get("/metrics")
async def metrics():
  """Prometheus指标端点"""
  if not settings.metrics_enabled:
    return {"error": "Metrics are disabled"}
  return await get_prometheus_metrics()


def run_api_server() -> None:
  """Run uvicorn with QuantX-specific Ctrl+C and child-process cleanup."""
  from uvicorn.main import STARTUP_FAILURE
  from uvicorn.supervisors import ChangeReload, Multiprocess

  config = uvicorn.Config(
    "quantx_api.main:app",
    host=settings.host,
    port=settings.port,
    log_level="warning",  # 降低 uvicorn 自身的日志级别
    access_log=False,  # 禁用访问日志（已由 middleware 处理）
    reload=settings.debug,
    reload_dirs=[str(Path(__file__).resolve().parent)] if settings.debug else None,
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
