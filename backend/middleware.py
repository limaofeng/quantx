"""
全局错误处理中间件
"""

import logging
import time
import traceback

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
  """全局错误处理中间件"""

  def __init__(self, app: ASGIApp):
    super().__init__(app)

  async def dispatch(self, request: Request, call_next):
    try:
      response = await call_next(request)
      return response

    except HTTPException as exc:
      # HTTP异常直接抛出
      logger.warning(f"HTTP {exc.status_code}: {exc.detail} - {request.method} {request.url.path}")
      raise exc

    except Exception as exc:
      # 未处理的异常
      error_id = f"error_{int(time.time())}"
      logger.error(f"Unhandled exception [{error_id}]: {str(exc)} - {request.method} {request.url.path}")
      logger.error(f"Traceback: {traceback.format_exc()}")

      return JSONResponse(
        status_code=500,
        content={
          "error": "Internal Server Error",
          "message": "An unexpected error occurred",
          "error_id": error_id,
          "timestamp": int(time.time()),
        },
      )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
  """请求日志中间件 - 简洁格式"""

  def __init__(self, app: ASGIApp):
    super().__init__(app)

  async def dispatch(self, request: Request, call_next):
    start_time = time.time()
    client_ip = request.client.host if request.client else "unknown"

    try:
      response = await call_next(request)
      process_time = time.time() - start_time

      # 简洁的单行日志格式
      logger.info(
        f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s - {client_ip}"
      )

      return response

    except Exception as exc:
      process_time = time.time() - start_time
      logger.error(
        f"{request.method} {request.url.path} - ERROR: {str(exc)} - {process_time:.3f}s - {client_ip}"
      )
      raise exc
