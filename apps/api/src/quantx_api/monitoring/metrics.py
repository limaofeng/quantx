"""
Prometheus 指标定义和收集中间件
"""

import logging
import time
from datetime import datetime, timezone

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

MESSAGE_BOX_MESSAGES = Gauge(
  "quantx_message_box_messages",
  "Durable message box rows by state",
  ["box", "status"],
)
MESSAGE_BOX_OLDEST_AGE = Gauge(
  "quantx_message_box_oldest_age_seconds",
  "Age of the oldest durable message by state",
  ["box", "status"],
)
DELIVERY_LATENCY = Gauge(
  "quantx_delivery_latency_seconds",
  "Observed durable handoff latency",
  ["phase", "statistic"],
)
RECONCILIATION_AGE = Gauge(
  "quantx_reconciliation_age_seconds",
  "Age of the last authoritative account snapshot",
  ["account_id"],
)
BACKUP_AGE = Gauge(
  "quantx_backup_age_seconds",
  "Age of the last verified backup",
  ["account_id"],
)
KILL_SWITCH_STATE = Gauge(
  "quantx_kill_switch_active",
  "Account hard-kill state",
  ["account_id"],
)
AGENT_JOURNAL_SIZE = Gauge(
  "quantx_agent_journal_size_bytes",
  "QMT Agent local journal size",
  ["device_id"],
)
AGENT_JOURNAL_PENDING = Gauge(
  "quantx_agent_journal_pending_reports",
  "QMT Agent local unacknowledged reports",
  ["device_id"],
)
AGENT_JOURNAL_INTEGRITY = Gauge(
  "quantx_agent_journal_integrity",
  "QMT Agent journal integrity state (1 is ok)",
  ["device_id"],
)
OPERATIONAL_ALERTS = Gauge(
  "quantx_operational_alerts",
  "Persistent operational alerts by severity and status",
  ["severity", "status"],
)
DATABASE_MIGRATION_HEAD = Gauge(
  "quantx_database_migration_head",
  "Database migration identity and compatibility",
  ["current", "expected", "relation"],
)
METRICS_COLLECTION_FAILURES = Counter(
  "quantx_metrics_collection_failures_total",
  "Best-effort metric collection failures",
  ["collector"],
)
MARKET_STREAM_CONNECTIONS = Gauge(
  "quantx_market_stream_connections",
  "Active dedicated QMT Agent market connections",
)
MARKET_STREAM_FRAMES = Counter(
  "quantx_market_stream_frames_total",
  "Whole-market frames accepted by API",
  ["kind"],
)
MARKET_STREAM_RESYNCS = Counter(
  "quantx_market_stream_resyncs_total",
  "Whole-market streams invalidated for convergence",
  ["reason"],
)
MARKET_STREAM_PROCESSING = Histogram(
  "quantx_market_stream_processing_seconds",
  "API validation plus Redis cache/publish time",
)
MARKET_STREAM_FRAME_BYTES = Gauge(
  "quantx_market_stream_frame_bytes",
  "Last whole-market binary frame size",
)
MARKET_STREAM_SEQUENCE = Gauge(
  "quantx_market_stream_sequence",
  "Last whole-market sequence committed by API",
)
MARKET_STREAM_INSTRUMENTS = Gauge(
  "quantx_market_stream_instruments",
  "Instrument count in the last whole-market frame",
)


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
    try:
      response = await call_next(request)
      status_code = str(response.status_code)
      route = request.scope.get("route")
      endpoint = getattr(route, "path", None) or "unmatched"

      # 记录指标
      REQUEST_COUNT.labels(
        method=method, endpoint=endpoint, status_code=status_code
      ).inc()
      REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(
        time.time() - start_time
      )

      return response

    except Exception as exc:
      route = request.scope.get("route")
      endpoint = getattr(route, "path", None) or "unmatched"
      # 记录错误请求
      REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code="500").inc()
      REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(
        time.time() - start_time
      )
      raise exc


def _age_seconds(value: datetime | None, now: datetime) -> float:
  if value is None:
    return 0.0
  if value.tzinfo is not None:
    value = value.astimezone(timezone.utc).replace(tzinfo=None)
  if now.tzinfo is not None:
    now = now.astimezone(timezone.utc).replace(tzinfo=None)
  return max(0.0, (now - value).total_seconds())


def _set_latency(phase: str, values: list[float]) -> None:
  values = [max(0.0, value) for value in values]
  DELIVERY_LATENCY.labels(phase=phase, statistic="average").set(
    sum(values) / len(values) if values else 0
  )
  DELIVERY_LATENCY.labels(phase=phase, statistic="maximum").set(
    max(values, default=0)
  )


async def update_operational_metrics() -> None:
  from quantx_domain.clock import utcnow
  from quantx_infrastructure.database.relational_connection import (
    AsyncSessionLocal,
    engine,
  )
  from quantx_infrastructure.database.schema_control import (
    _inspect_schema,
  )
  from quantx_infrastructure.models.agent_runtime import (
    AccountTradingRollout,
    AgentReportInbox,
    OperationalAlert,
    RuntimeComponentHeartbeat,
    TradeCommandOutbox,
  )
  from sqlalchemy import func, select

  now = utcnow()
  async with AsyncSessionLocal() as db:
    for box, model, status_column, time_column in (
      (
        "trade_command_outbox",
        TradeCommandOutbox,
        TradeCommandOutbox.delivery_status,
        TradeCommandOutbox.created_at,
      ),
      (
        "agent_report_inbox",
        AgentReportInbox,
        AgentReportInbox.processing_status,
        AgentReportInbox.received_at,
      ),
    ):
      rows = (
        await db.execute(
          select(
            status_column,
            func.count(),
            func.min(time_column),
          ).group_by(status_column)
        )
      ).all()
      for status, count, oldest in rows:
        MESSAGE_BOX_MESSAGES.labels(box=box, status=str(status)).set(count)
        MESSAGE_BOX_OLDEST_AGE.labels(
          box=box,
          status=str(status),
        ).set(_age_seconds(oldest, now))

    delivered = (
      await db.execute(
        select(
          TradeCommandOutbox.created_at,
          TradeCommandOutbox.delivered_at,
        )
        .where(TradeCommandOutbox.delivered_at.is_not(None))
        .order_by(TradeCommandOutbox.delivered_at.desc())
        .limit(500)
      )
    ).all()
    _set_latency(
      "command_delivery",
      [
        _age_seconds(created_at, delivered_at)
        for created_at, delivered_at in delivered
      ],
    )
    processed = (
      await db.execute(
        select(
          AgentReportInbox.received_at,
          AgentReportInbox.processed_at,
        )
        .where(
          AgentReportInbox.message_type == "execution_report",
          AgentReportInbox.processed_at.is_not(None),
        )
        .order_by(AgentReportInbox.processed_at.desc())
        .limit(500)
      )
    ).all()
    _set_latency(
      "execution_convergence",
      [
        _age_seconds(received_at, processed_at)
        for received_at, processed_at in processed
      ],
    )

    rollouts = (
      await db.execute(select(AccountTradingRollout))
    ).scalars().all()
    for rollout in rollouts:
      account_id = str(rollout.account_id)
      RECONCILIATION_AGE.labels(account_id=account_id).set(
        _age_seconds(rollout.last_snapshot_at, now)
      )
      BACKUP_AGE.labels(account_id=account_id).set(
        _age_seconds(rollout.last_backup_at, now)
      )
      KILL_SWITCH_STATE.labels(account_id=account_id).set(
        1 if rollout.kill_switch else 0
      )

    heartbeats = (
      await db.execute(
        select(RuntimeComponentHeartbeat).where(
          RuntimeComponentHeartbeat.component.like("qmt-agent:%")
        )
      )
    ).scalars().all()
    for heartbeat in heartbeats:
      details = dict(heartbeat.details or {})
      device_id = str(heartbeat.instance_id)
      AGENT_JOURNAL_SIZE.labels(device_id=device_id).set(
        int(details.get("journalSizeBytes") or 0)
      )
      AGENT_JOURNAL_PENDING.labels(device_id=device_id).set(
        int(details.get("journalPendingReports") or 0)
      )
      AGENT_JOURNAL_INTEGRITY.labels(device_id=device_id).set(
        1 if details.get("journalIntegrity") == "ok" else 0
      )

    alert_rows = (
      await db.execute(
        select(
          OperationalAlert.severity,
          OperationalAlert.status,
          func.count(),
        ).group_by(OperationalAlert.severity, OperationalAlert.status)
      )
    ).all()
    for severity, status, count in alert_rows:
      OPERATIONAL_ALERTS.labels(
        severity=str(severity),
        status=str(status),
      ).set(count)

  async with engine.connect() as connection:
    schema = await connection.run_sync(_inspect_schema)
  DATABASE_MIGRATION_HEAD.clear()
  DATABASE_MIGRATION_HEAD.labels(
    current=",".join(schema["current_heads"]) or "unversioned",
    expected=",".join(schema["expected_heads"]),
    relation=str(schema["revision_relation"]),
  ).set(1)


async def get_prometheus_metrics() -> Response:
  """获取Prometheus格式的指标"""
  try:
    # 导入并更新系统指标
    from .system_metrics import SystemMetrics

    try:
      SystemMetrics.update_system_metrics()
    except Exception as exc:
      METRICS_COLLECTION_FAILURES.labels(collector="system").inc()
      logger.warning("System metric collection degraded: %s", exc.__class__.__name__)
    try:
      await update_operational_metrics()
    except Exception as exc:
      # Prometheus scraping must remain available during a database incident;
      # the failure counter and stale operational gauges make that degradation
      # visible without turning the whole endpoint into a second outage.
      METRICS_COLLECTION_FAILURES.labels(collector="operational").inc()
      logger.warning(
        "Operational metric collection degraded: %s",
        exc.__class__.__name__,
      )

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
