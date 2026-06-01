import asyncio
import logging.config
# Trigger reload
from contextlib import asynccontextmanager

import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config.settings import create_log_directory, settings
from core.data.market_data_service import market_data_service
from core.realtime_manager import realtime_manager
from core.strategy_manager import strategy_manager
from database.manager import db_manager
from gqlapi import setup_graphql
from monitoring import get_prometheus_metrics
from monitoring.metrics import REQUEST_COUNT, REQUEST_DURATION
from prefector.prefect_manager import prefect_manager

# 创建日志目录
create_log_directory()

# 配置日志
logging.config.dictConfig(settings.get_log_config())
logger = logging.getLogger(__name__)

_mcp_http_app = None
MINIQMT_HEALTH_ACCOUNT_ID = "300000013250"


def _probe_miniqmt_health() -> dict:
  """Probe miniQMT by querying the default account status.

  The trading gateway is only considered healthy when account status is OK,
  not merely when the XTQuant trader reports a connected session.
  """
  from miniqmt.manager_registry import XTDataManagerRegistry, XTTradingManagerRegistry
  from miniqmt.trading.trading_manager import XTTradingManager

  data_registry = XTDataManagerRegistry()
  trading_registry = XTTradingManagerRegistry()

  data_manager_stats = data_registry.get_stats()
  trading_manager_stats = trading_registry.get_stats()
  account_checked = False
  account_connected = False
  account_error = ""

  try:
    with trading_registry._lock:
      trading_manager = trading_registry._managers.get(MINIQMT_HEALTH_ACCOUNT_ID)

    if trading_manager is None or not getattr(trading_manager, "is_connected", False):
      previous_manager = trading_manager
      trading_manager = XTTradingManager(MINIQMT_HEALTH_ACCOUNT_ID)
      with trading_registry._lock:
        trading_registry._managers[MINIQMT_HEALTH_ACCOUNT_ID] = trading_manager
      if previous_manager is not None:
        previous_manager.close_connection()

    account_checked = True
    account_connected = (
      trading_manager.is_account_status_ok() if trading_manager else False
    )
    if not account_connected:
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
  logger.info("启动 QuantX API 服务器...")
  logger.info(f"环境: {settings.environment}")
  logger.info(f"调试模式: {settings.debug}")
  logger.info(f"API 地址: http://{settings.host}:{settings.port}")

  # 初始化数据库
  try:
    await db_manager.initialize()
    logger.info("数据库初始化完成")
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

  # 启动策略管理器 (内部会自动完成策略发现和同步)
  await strategy_manager.start()

  # 启动 Prefect 服务
  await prefect_manager.start()

  # 启动 MCP Server (if enabled)
  mcp_task = None
  _mcp_http_app = None
  try:
    from config.mcp_config import mcp_settings
    if mcp_settings.enabled:
      mode = mcp_settings.mode.lower().strip()
      if mode == "stdio":
        logger.info("启动 MCP Server (stdio)...")
        from quantx_mcp import start_background_mcp_server
        mcp_task = await start_background_mcp_server()
        logger.info("MCP Server 已启动 (stdio)")
      # HTTP 模式 - 手动启动 session manager
      elif mode in ("sse", "streamable-http", "http"):
        logger.info("启动 MCP HTTP session manager...")
        from quantx_mcp import create_mcp_http_app
        _mcp_http_app = create_mcp_http_app(mode=mode)
        app.mount(mcp_settings.transport.path, _mcp_http_app)
        logger.info(f"MCP HTTP 路由已挂载: {mcp_settings.transport.path} (mode={mode})")
        # 启动 session manager
        await _mcp_http_app.mcp_manager.start()
        logger.info("MCP HTTP session manager 已启动")
  except ImportError as e:
    logger.warning(f"MCP 模块未安装，跳过 MCP Server 启动: {e}")
  except Exception as e:
    logger.error(f"MCP Server 启动失败: {e}")
    logger.warning("MCP Server 启动失败，但主应用继续运行")

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

  # 停止 MCP stdio Server
  if mcp_task:
    try:
      mcp_task.cancel()
      await asyncio.wait_for(mcp_task, timeout=3.0)
      logger.info("MCP Server 已停止 (stdio)")
    except asyncio.TimeoutError:
      logger.warning("MCP Server 停止超时,强制跳过 (stdio)")
    except asyncio.CancelledError:
      logger.info("MCP Server 已取消 (stdio)")
    except Exception as e:
      logger.error(f"停止 MCP Server 时出错: {e}")

  # 停止 MCP HTTP session manager
  if _mcp_http_app is not None:
    await stop_with_timeout("MCP HTTP session manager", _mcp_http_app.mcp_manager.stop(), 5.0)

  # 停止其他组件(按依赖关系倒序)
  await stop_with_timeout("Prefect 任务调度服务", prefect_manager.stop(), 3.0)
  await stop_with_timeout("策略管理器", strategy_manager.stop(), 5.0)
  await stop_with_timeout("实时数据管理器", realtime_manager.stop(), 3.0)
  await stop_with_timeout("数据提供者", market_data_service.shutdown(), 3.0)

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

# 设置GraphQL
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


@app.get("/health")
async def health_check():
  """健康检查端点"""
  db_health = db_manager.health_check()
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
  market_data_stats = await market_data_service.get_statistics()
  market_data_status = {
    "initialized": market_data_stats["is_initialized"],
    "connected": market_data_stats["is_connected"],
    "price_cache_count": market_data_stats["price_cache_count"],
    "position_cache_count": market_data_stats["position_cache_count"],
  }

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


if __name__ == "__main__":
  import uvicorn

  # 交给 uvicorn 自己处理信号，避免打断其优雅关闭流程

  uvicorn.run(
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
