"""
Prometheus 指标定义和收集中间件
"""

import logging
import time
from datetime import datetime, timezone
from math import isfinite

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
DATABASE_POOL_CONNECTIONS = Gauge(
  "quantx_database_pool_connections",
  "Process-local SQLAlchemy pool connections by state",
  ["role", "state"],
)
GRAPHQL_QUERY_ADMISSION_ACTIVE = Gauge(
  "quantx_graphql_query_admission_active",
  "GraphQL query requests currently admitted",
)
GRAPHQL_QUERY_ADMISSION_WAIT = Histogram(
  "quantx_graphql_query_admission_wait_seconds",
  "Time spent waiting for a GraphQL query admission slot",
)
GRAPHQL_QUERY_ADMISSION_REJECTIONS = Counter(
  "quantx_graphql_query_admission_rejections_total",
  "GraphQL query requests rejected before execution",
  ["reason"],
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
AGENT_CONTROL_QUEUE_DEPTH = Gauge(
  "quantx_agent_control_queue_depth",
  "Queued QMT Agent control messages by direction",
  ["device_id", "direction"],
)
AGENT_CONTROL_QUEUE_OLDEST_AGE = Gauge(
  "quantx_agent_control_queue_oldest_age_seconds",
  "Age of the oldest queued QMT Agent control message",
  ["device_id", "direction"],
)
AGENT_CONTROL_STAGE_DURATION = Histogram(
  "quantx_agent_control_stage_duration_seconds",
  "QMT Agent control pipeline latency by stage and message type",
  ["stage", "message_type"],
)
AGENT_CONTROL_EVENTS = Counter(
  "quantx_agent_control_events_total",
  "QMT Agent control pipeline failures and lifecycle events",
  ["event", "reason"],
)
AGENT_CONTROL_DATABASE_STATE = Gauge(
  "quantx_agent_control_database_state",
  "QMT Agent control-session database health",
  ["device_id", "measure"],
)
T_TRADE_V3_RUNTIME_VALUE = Gauge(
  "quantx_t_trade_v3_runtime_value",
  "Engine-observed T-trade V3 cumulative value since the current Engine start",
  ["engine_instance", "metric", "path", "health", "detail"],
)
T_TRADE_V3_ACTIVE_STREAMS = Gauge(
  "quantx_t_trade_v3_active_streams",
  "Number of T-trade V3 run/instrument streams observed by the Engine",
  ["engine_instance"],
)
T_TRADE_V3_ACCUMULATOR_STATE = Gauge(
  "quantx_t_trade_v3_accumulator_state",
  "Bounded T-trade V3 accumulator capacity, loss accounting, and export state",
  ["engine_instance", "measure"],
)
T_TRADE_V3_PROJECTION_VALUE = Gauge(
  "quantx_t_trade_v3_projection_value",
  "Engine projection and subscription coalescer cumulative values",
  ["engine_instance", "metric"],
)
T_TRADE_CLIENT_EVENTS = Counter(
  "quantx_t_trade_client_events_total",
  "Bounded client-side T-trade V3 refresh and subscription recovery events",
  ["surface", "platform", "event"],
)

_T_TRADE_CLIENT_SURFACES = frozenset({"T_TRADE_SIGNAL_V3"})
_T_TRADE_CLIENT_PLATFORMS = frozenset({"WEB", "IOS"})
_T_TRADE_CLIENT_EVENT_CODES = frozenset(
  {"REFRESH_SUCCESS", "REFRESH_FAILURE", "SUBSCRIPTION_RECONNECTED"}
)
_T_TRADE_V3_RUNTIME_SCHEMA_VERSION = 2
_T_TRADE_V3_MAX_RUNTIME_SERIES = 1_024
_T_TRADE_V3_MAX_ACTIVE_STREAMS = 4_096
_T_TRADE_V3_MAX_EXACT_COUNTER = (1 << 53) - 1
_T_TRADE_V3_PATHS = frozenset({"NONE", "PULLBACK_REBOUND", "MOMENTUM_ACCELERATION"})
_T_TRADE_V3_HEALTH = frozenset(
  {
    "WARMING",
    "READY",
    "DEGRADED",
    "STALE",
    "CONTINUITY_LOST",
    "INSUFFICIENT",
    "UNKNOWN",
  }
)


def record_t_trade_client_event(
  *,
  surface: str,
  platform: str,
  event: str,
) -> None:
  """Increment only the fixed low-cardinality T-trade client label set."""

  if surface not in _T_TRADE_CLIENT_SURFACES:
    raise ValueError("unsupported T-trade client telemetry surface")
  if platform not in _T_TRADE_CLIENT_PLATFORMS:
    raise ValueError("unsupported T-trade client telemetry platform")
  if event not in _T_TRADE_CLIENT_EVENT_CODES:
    raise ValueError("unsupported T-trade client telemetry event")
  T_TRADE_CLIENT_EVENTS.labels(
    surface=surface,
    platform=platform,
    event=event,
  ).inc()


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


def update_database_pool_metrics() -> None:
  from quantx_infrastructure.database.relational_connection import (
    database_pool_snapshot,
  )

  snapshot = database_pool_snapshot()
  role = str(snapshot["role"])
  for state in ("size", "checked_in", "checked_out", "overflow", "maximum"):
    DATABASE_POOL_CONNECTIONS.labels(role=role, state=state).set(
      int(snapshot[state])
    )


def _set_latency(phase: str, values: list[float]) -> None:
  values = [max(0.0, value) for value in values]
  DELIVERY_LATENCY.labels(phase=phase, statistic="average").set(
    sum(values) / len(values) if values else 0
  )
  DELIVERY_LATENCY.labels(phase=phase, statistic="maximum").set(max(values, default=0))


def _bounded_metric_label(value: object, *, fallback: str = "UNKNOWN") -> str:
  normalized = str(value or "").strip().upper()
  if not normalized:
    return fallback
  if len(normalized) > 80 or any(
    not (char.isalnum() or char in {"_", "-", ".", ":", ">"}) for char in normalized
  ):
    return "OTHER"
  return normalized


def _t_trade_v3_count(value: object) -> int | None:
  if not isinstance(value, int) or isinstance(value, bool):
    return None
  if value < 0 or value > _T_TRADE_V3_MAX_EXACT_COUNTER:
    return None
  return value


def _validated_t_trade_v3_runtime(
  runtime: dict[object, object],
) -> tuple[dict[str, int], list[tuple[str, str, str, str, float]]] | None:
  """Validate the complete bounded snapshot before creating Prometheus children."""

  if runtime.get("schemaVersion") != _T_TRADE_V3_RUNTIME_SCHEMA_VERSION:
    return None
  counts: dict[str, int] = {}
  for key in (
    "activeStreamCount",
    "streamCapacity",
    "streamEvictionsTotal",
    "seriesCount",
    "seriesCapacity",
    "seriesOverflowUpdatesTotal",
  ):
    value = _t_trade_v3_count(runtime.get(key))
    if value is None:
      return None
    counts[key] = value
  if not 0 < counts["seriesCapacity"] <= _T_TRADE_V3_MAX_RUNTIME_SERIES:
    return None
  if not 0 < counts["streamCapacity"] <= _T_TRADE_V3_MAX_ACTIVE_STREAMS:
    return None
  if counts["activeStreamCount"] > counts["streamCapacity"]:
    return None

  raw_series = runtime.get("series")
  if not isinstance(raw_series, list):
    return None
  if (
    len(raw_series) != counts["seriesCount"]
    or len(raw_series) > counts["seriesCapacity"]
  ):
    return None

  parsed: list[tuple[str, str, str, str, float]] = []
  seen: set[tuple[str, str, str, str]] = set()
  for item in raw_series:
    if not isinstance(item, dict) or "policyVersion" in item:
      return None
    try:
      value = float(item.get("value", 0.0) or 0.0)
    except (TypeError, ValueError, OverflowError):
      return None
    if not isfinite(value) or value < 0:
      return None
    metric = _bounded_metric_label(item.get("metric"))
    path = _bounded_metric_label(item.get("path"), fallback="NONE")
    health = _bounded_metric_label(item.get("health"))
    detail = _bounded_metric_label(item.get("detail"), fallback="TOTAL")
    if path not in _T_TRADE_V3_PATHS or health not in _T_TRADE_V3_HEALTH:
      return None
    labels = (metric, path, health, detail)
    if labels in seen:
      return None
    seen.add(labels)
    parsed.append((*labels, value))
  return counts, parsed


_T_TRADE_PROJECTION_COUNTERS = frozenset(
  {
    "received_total",
    "immediate_total",
    "pending_cancelled_by_material_total",
    "coalesced_replacements_total",
    "coalesced_windows_total",
    "flush_published_total",
    "projection_failures_total",
    "projection_missing_total",
    "published_total",
    "publish_failures_total",
  }
)


def _validated_t_trade_projection(
  projection: dict[object, object],
) -> list[tuple[str, float]] | None:
  """Validate the complete fixed-schema projection metrics snapshot."""

  if projection.get("schemaVersion") != 1:
    return None
  counters = projection.get("counters")
  if not isinstance(counters, dict) or any(
    not isinstance(metric, str) or metric not in _T_TRADE_PROJECTION_COUNTERS
    for metric in counters
  ):
    return None
  parsed: list[tuple[str, float]] = []
  for metric, raw_value in counters.items():
    value = _t_trade_v3_count(raw_value)
    if value is None:
      return None
    parsed.append((metric.upper(), float(value)))
  for metric, key in (
    ("PENDING_NOTICE_COUNT", "pendingNoticeCount"),
    ("ACTIVE_NOTICE_TASK_COUNT", "activeNoticeTaskCount"),
  ):
    value = _t_trade_v3_count(projection.get(key))
    if value is None:
      return None
    parsed.append((metric, float(value)))
  return parsed


def _set_t_trade_v3_engine_metrics(heartbeat: object | None) -> None:
  """Project the separate Engine process's bounded heartbeat snapshot."""

  T_TRADE_V3_RUNTIME_VALUE.clear()
  T_TRADE_V3_ACTIVE_STREAMS.clear()
  T_TRADE_V3_ACCUMULATOR_STATE.clear()
  T_TRADE_V3_PROJECTION_VALUE.clear()
  if heartbeat is None:
    return
  instance_id = _bounded_metric_label(getattr(heartbeat, "instance_id", None))
  details = dict(getattr(heartbeat, "details", None) or {})
  runtime = details.get("tTradeV3")
  projection = details.get("tTradeProjection")
  # The two sections are one heartbeat contract.  Exporting a valid-looking
  # half when the other half is missing or malformed would silently present a
  # partial, potentially stale picture to Prometheus consumers.
  validated_runtime = (
    _validated_t_trade_v3_runtime(runtime) if isinstance(runtime, dict) else None
  )
  validated_projection = (
    _validated_t_trade_projection(projection) if isinstance(projection, dict) else None
  )
  rejected = validated_runtime is None or validated_projection is None
  T_TRADE_V3_ACCUMULATOR_STATE.labels(
    engine_instance=instance_id,
    measure="SNAPSHOT_REJECTED",
  ).set(1 if rejected else 0)
  if rejected:
    return

  counts, series = validated_runtime
  T_TRADE_V3_ACTIVE_STREAMS.labels(engine_instance=instance_id).set(
    counts["activeStreamCount"]
  )
  for measure, key in (
    ("SERIES_COUNT", "seriesCount"),
    ("SERIES_CAPACITY", "seriesCapacity"),
    ("SERIES_OVERFLOW_UPDATES_TOTAL", "seriesOverflowUpdatesTotal"),
    ("STREAM_CAPACITY", "streamCapacity"),
    ("STREAM_EVICTIONS_TOTAL", "streamEvictionsTotal"),
  ):
    T_TRADE_V3_ACCUMULATOR_STATE.labels(
      engine_instance=instance_id,
      measure=measure,
    ).set(counts[key])
  for metric, path, health, detail, value in series:
    T_TRADE_V3_RUNTIME_VALUE.labels(
      engine_instance=instance_id,
      metric=metric,
      path=path,
      health=health,
      detail=detail,
    ).set(value)
  for metric, value in validated_projection:
    T_TRADE_V3_PROJECTION_VALUE.labels(
      engine_instance=instance_id,
      metric=metric,
    ).set(value)


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
    AccountExecutionControl,
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
        _age_seconds(created_at, delivered_at) for created_at, delivered_at in delivered
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

    rollouts = (await db.execute(select(AccountExecutionControl))).scalars().all()
    for rollout in rollouts:
      account_id = str(rollout.account_id)
      RECONCILIATION_AGE.labels(account_id=account_id).set(
        _age_seconds(rollout.last_snapshot_at, now)
      )
      BACKUP_AGE.labels(account_id=account_id).set(
        _age_seconds(rollout.last_backup_at, now)
      )
      KILL_SWITCH_STATE.labels(account_id=account_id).set(
        1 if str(rollout.authorization_state).upper() == "KILLED" else 0
      )

    heartbeats = (
      (
        await db.execute(
          select(RuntimeComponentHeartbeat).where(
            RuntimeComponentHeartbeat.component.like("qmt-agent:%")
          )
        )
      )
      .scalars()
      .all()
    )
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

    engine_heartbeat = await db.get(RuntimeComponentHeartbeat, "engine")
    _set_t_trade_v3_engine_metrics(engine_heartbeat)

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
      update_database_pool_metrics()
    except Exception as exc:
      METRICS_COLLECTION_FAILURES.labels(collector="database_pool").inc()
      logger.warning(
        "Database pool metric collection degraded: %s",
        exc.__class__.__name__,
      )
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
