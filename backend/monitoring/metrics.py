"""
Prometheus 指标定义和收集中间件
"""

import logging
import time

from fastapi import Request, Response
from prometheus_client import (
  CONTENT_TYPE_LATEST,
  Counter,
  Gauge,
  Histogram,
  generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Prometheus 指标定义
REQUEST_COUNT = Counter(
  "http_requests_total", "Total HTTP requests", ["method", "endpoint", "status_code"]
)

REQUEST_DURATION = Histogram(
  "http_request_duration_seconds",
  "HTTP request duration in seconds",
  ["method", "endpoint"],
)

ACTIVE_CONNECTIONS = Gauge(
  "websocket_connections_active", "Number of active WebSocket connections"
)

SUBSCRIPTION_COUNT = Gauge(
  "graphql_subscriptions_active",
  "Number of active GraphQL subscriptions",
  ["subscription_type"],
)

SYSTEM_CPU_USAGE = Gauge("system_cpu_usage_percent", "System CPU usage percentage")
SYSTEM_MEMORY_USAGE = Gauge(
  "system_memory_usage_percent", "System memory usage percentage"
)
SYSTEM_DISK_USAGE = Gauge("system_disk_usage_percent", "System disk usage percentage")


class MetricsMiddleware(BaseHTTPMiddleware):
  """指标收集中间件"""

  def __init__(self, app, enabled: bool = True):
    super().__init__(app)
    self.enabled = enabled

  async def dispatch(self, request: Request, call_next):
    if not self.enabled:
      return await call_next(request)

    # 跳过指标端点本身
    if request.url.path == "/metrics":
      return await call_next(request)

    start_time = time.time()
    method = request.method
    endpoint = request.url.path

    try:
      response = await call_next(request)
      status_code = str(response.status_code)

      # 记录指标
      REQUEST_COUNT.labels(
        method=method, endpoint=endpoint, status_code=status_code
      ).inc()
      REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(
        time.time() - start_time
      )

      return response

    except Exception as exc:
      # 记录错误请求
      REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code="500").inc()
      REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(
        time.time() - start_time
      )
      raise exc


def get_prometheus_metrics() -> Response:
  """获取Prometheus格式的指标"""
  try:
    # 导入并更新系统指标
    from .system_metrics import SystemMetrics

    SystemMetrics.update_system_metrics()

    # 生成Prometheus格式指标
    metrics_data = generate_latest()

    return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)
  except Exception as e:
    logger.error(f"Failed to generate Prometheus metrics: {e}")
    return Response(
      content=f"# Error generating metrics: {e}\n",
      media_type=CONTENT_TYPE_LATEST,
      status_code=500,
    )
