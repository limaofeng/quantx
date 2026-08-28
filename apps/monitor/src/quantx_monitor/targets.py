"""Known targets; the public API never accepts arbitrary probe URLs."""

from __future__ import annotations

from .models import ProbeKind, TargetDefinition, TargetGroup

TARGETS: tuple[TargetDefinition, ...] = (
  TargetDefinition("postgresql", "PostgreSQL", TargetGroup.EXTERNAL),
  TargetDefinition("redis", "Redis", TargetGroup.EXTERNAL),
  TargetDefinition("influxdb", "InfluxDB", TargetGroup.EXTERNAL),
  TargetDefinition("prefect-server", "Prefect Server", TargetGroup.EXTERNAL),
  TargetDefinition("web-entry", "Web / Caddy", TargetGroup.RUNTIME),
  TargetDefinition("docs", "开发文档", TargetGroup.RUNTIME),
  TargetDefinition("api-public", "API 公共链路", TargetGroup.RUNTIME),
  TargetDefinition("api-process", "API 进程", TargetGroup.RUNTIME),
  TargetDefinition("market-gateway", "Market Gateway", TargetGroup.RUNTIME),
  TargetDefinition(
    "engine",
    "策略引擎",
    TargetGroup.RUNTIME,
    probe_kind=ProbeKind.DERIVED,
  ),
  TargetDefinition(
    "worker",
    "Prefect Worker",
    TargetGroup.RUNTIME,
    probe_kind=ProbeKind.DERIVED,
  ),
  TargetDefinition(
    "qmt-agent",
    "QMT Agent",
    TargetGroup.RUNTIME,
    probe_kind=ProbeKind.COMPOSITE,
  ),
  TargetDefinition(
    "market-data",
    "行情服务",
    TargetGroup.RUNTIME,
    probe_kind=ProbeKind.DERIVED,
  ),
  TargetDefinition(
    "ai-runtime",
    "AI Runtime",
    TargetGroup.RUNTIME,
    optional=True,
    probe_kind=ProbeKind.DERIVED,
  ),
)

TARGET_BY_ID = {target.target_id: target for target in TARGETS}
