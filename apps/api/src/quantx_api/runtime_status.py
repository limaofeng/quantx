"""Read-only health aggregation for independently supervised components."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from quantx_contracts.market_health import (
  MARKET_GATEWAY_HTTP_TIMEOUT_SECONDS,
  MarketGatewayHealth,
)
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.data.market_stream_transport import (
  market_stream_store,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.services.agent_session_guard import (
  QMT_ACCOUNT_MISMATCH,
  QMT_AGENT_NOT_RECONCILED,
  QMT_AGENT_OFFLINE,
  QMT_AGENT_STALE,
  QMT_CONTROL_DEPENDENCY_UNAVAILABLE,
  agent_unready_reason_code,
  evaluate_agent_session,
)
from quantx_infrastructure.services.market_stream_readiness import (
  MarketStreamReadinessStatus,
  classify_authoritative_market_stream_readiness,
)
from quantx_infrastructure.services.qmt_launch_guard import (
  qmt_agent_launch_block_reason,
)
from quantx_infrastructure.services.trading_time_service import TradingTimeService
from sqlalchemy import select, text

from quantx_api.agent_hub import agent_connection_hub
from quantx_api.auth.tokens import utcnow

HEARTBEAT_TTL = timedelta(seconds=90)
RECONCILING_AGENT_STATUSES = frozenset({"RECONCILING", "RECONCILE_REQUIRED"})
CONNECTED_AGENT_STATUSES = frozenset(
  {
    "READY",
    *RECONCILING_AGENT_STATUSES,
    "TRADING_UNAVAILABLE",
    "XTDATA_UNAVAILABLE",
    "EMERGENCY_STOP",
  }
)


def _masked_account_id(value: str) -> str:
  normalized = str(value or "").strip()
  if len(normalized) <= 4:
    return "*" * len(normalized)
  return f"***{normalized[-4:]}"


def _snapshot_age_seconds(value: Any, now: datetime) -> float | None:
  if not value:
    return None
  try:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
      parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return max(0.0, (now - parsed).total_seconds())
  except (TypeError, ValueError):
    return None


def _aware_state_age(value: datetime | None, now: datetime) -> float | None:
  if value is None:
    return None
  normalized = value.astimezone(timezone.utc).replace(tzinfo=None)
  return max(0.0, (now - normalized).total_seconds())


def _iso_utc_timestamp(value: datetime | None) -> str | None:
  if value is None:
    return None
  normalized = (
    value.replace(tzinfo=timezone.utc)
    if value.tzinfo is None
    else value.astimezone(timezone.utc)
  )
  return normalized.isoformat().replace("+00:00", "Z")


async def _database_status() -> dict[str, Any]:
  try:
    async with AsyncSessionLocal() as db:
      version = (
        await db.execute(text("SELECT current_setting('server_version')"))
      ).scalar_one()
    return {"status": "ready", "version": str(version)}
  except Exception as exc:
    return {"status": "unavailable", "error": exc.__class__.__name__}


async def _component_heartbeats() -> dict[str, dict[str, Any]]:
  now = utcnow()
  control_snapshots = await agent_connection_hub.health_snapshots()
  control_by_device = {
    snapshot.device_id: snapshot for snapshot in control_snapshots
  }
  try:
    async with AsyncSessionLocal() as db:
      result = await db.execute(select(RuntimeComponentHeartbeat))
      heartbeats = result.scalars().all()
      agent_result = await db.execute(
        select(AgentDevice).where(AgentDevice.revoked_at.is_(None))
      )
      agents = agent_result.scalars().all()
  except Exception as exc:
    unavailable = {
      "status": "unavailable",
      "error": exc.__class__.__name__,
    }
    live_transports = [
      snapshot
      for snapshot in control_snapshots
      if snapshot.heartbeat_age_seconds <= HEARTBEAT_TTL.total_seconds()
    ]
    return {
      "qmt-agent": {
        **unavailable,
        "status": "degraded" if live_transports else "unavailable",
        "connectedDevices": len(live_transports),
        "onlineDevices": len(live_transports),
        "readyDevices": 0,
        "reasonCode": QMT_CONTROL_DEPENDENCY_UNAVAILABLE,
      },
      "engine": unavailable,
    }

  components: dict[str, dict[str, Any]] = {}
  heartbeat_by_component = {heartbeat.component: heartbeat for heartbeat in heartbeats}
  for heartbeat in heartbeats:
    if heartbeat.component.startswith("qmt-agent:"):
      # Per-device details contain account-scoped reconciliation metadata.
      # Only the masked aggregate below is part of the public status contract.
      continue
    age = max(0.0, (now - heartbeat.updated_at).total_seconds())
    components[heartbeat.component] = {
      "status": (
        str(heartbeat.status or "").lower()
        if age <= HEARTBEAT_TTL.total_seconds()
        else "stale"
      ),
      "instanceId": heartbeat.instance_id,
      "ageSeconds": round(age, 3),
      "details": heartbeat.details or {},
    }

  launch_block_reason = qmt_agent_launch_block_reason()
  online_agents: list[AgentDevice] = []
  connected_agents: list[tuple[AgentDevice, str]] = []
  agent_reason_codes: list[str] = []
  for agent in agents:
    heartbeat = heartbeat_by_component.get(f"qmt-agent:{agent.id}")
    heartbeat_status = str(heartbeat.status or "").upper() if heartbeat else ""
    control = control_by_device.get(str(agent.id))
    evaluation = evaluate_agent_session(
      heartbeat,
      now=now,
      acceptable_statuses=CONNECTED_AGENT_STATUSES,
    )
    transport_alive = bool(
      control is not None
      and control.heartbeat_age_seconds <= HEARTBEAT_TTL.total_seconds()
    )
    if control is not None and not transport_alive:
      agent_reason_codes.append(QMT_AGENT_STALE)
    else:
      agent_reason_codes.append(evaluation.reason_code)
    projection_matches = bool(
      transport_alive
      and evaluation.current
      and control is not None
      and evaluation.api_instance_id == control.api_instance_id
      and evaluation.agent_session_id == control.agent_session_id
      and control.dependency_ready
    )
    if transport_alive:
      online_agents.append(agent)
      connected_agents.append(
        (
          agent,
          heartbeat_status
          if projection_matches
          else QMT_CONTROL_DEPENDENCY_UNAVAILABLE,
        )
      )
  require_live_agent = bool(settings.enable_real_trading)

  def capabilities_for(agent: AgentDevice) -> set[str]:
    heartbeat = heartbeat_by_component.get(f"qmt-agent:{agent.id}")
    details = dict(heartbeat.details or {}) if heartbeat else {}
    return {
      str(value).lower()
      for value in [
        *list(agent.capabilities or []),
        *list(details.get("capabilities") or []),
      ]
    }

  ready_agents = [
    agent
    for agent, status in connected_agents
    if status == "READY"
    and (not require_live_agent or "live" in capabilities_for(agent))
  ]
  ready_agent_ids = {str(agent.id) for agent in ready_agents}
  latest_ready_heartbeat_at = max(
    (
      heartbeat_by_component[f"qmt-agent:{agent.id}"].updated_at
      for agent, _ in connected_agents
      if str(agent.id) in ready_agent_ids
      and f"qmt-agent:{agent.id}" in heartbeat_by_component
    ),
    default=None,
  )
  agent_modes: set[str] = set()
  protocol_versions: set[str] = set()
  account_ids: set[str] = set()
  snapshot_ages: list[float] = []
  for agent, _ in connected_agents:
    heartbeat = heartbeat_by_component.get(f"qmt-agent:{agent.id}")
    details = dict(heartbeat.details or {}) if heartbeat else {}
    capabilities = capabilities_for(agent)
    agent_modes.update(
      value for value in ("live", "paper", "data-only") if value in capabilities
    )
    protocol_version = str(details.get("protocolVersion") or "").strip()
    if protocol_version:
      protocol_versions.add(protocol_version)
    account_ids.update(
      _masked_account_id(str(value))
      for value in list(agent.authorized_account_ids or [])
    )
    direct_age = _snapshot_age_seconds(details.get("snapshotAt"), now)
    if direct_age is not None:
      snapshot_ages.append(direct_age)
    for summary in dict(details.get("accountReconciliation") or {}).values():
      age = _snapshot_age_seconds(dict(summary or {}).get("snapshotAt"), now)
      if age is not None:
        snapshot_ages.append(age)
  reconciling_agents = [
    agent for agent, status in connected_agents if status in RECONCILING_AGENT_STATUSES
  ]
  connected_agent_reasons = {
    (
      QMT_CONTROL_DEPENDENCY_UNAVAILABLE
      if status == QMT_CONTROL_DEPENDENCY_UNAVAILABLE
      else agent_unready_reason_code(
        heartbeat_by_component.get(f"qmt-agent:{agent.id}")
      )
    )
    for agent, status in connected_agents
    if status != "READY"
  }
  degraded_reason = next(
    (
      reason
      for reason in (
        QMT_CONTROL_DEPENDENCY_UNAVAILABLE,
        "XTDATA_UNAVAILABLE",
        "XTTRADING_UNAVAILABLE",
        "EMERGENCY_STOP",
        QMT_AGENT_NOT_RECONCILED,
      )
      if reason in connected_agent_reasons
    ),
    QMT_AGENT_NOT_RECONCILED,
  )
  components["qmt-agent"] = {
    "status": (
      "blocked"
      if launch_block_reason
      else "ready"
      if ready_agents
      else "degraded"
      if connected_agents
      else "offline"
    ),
    "connectedDevices": len(connected_agents),
    "readyDevices": len(ready_agents),
    "onlineDevices": len(online_agents),
    "reconcilingDevices": len(reconciling_agents),
    "degradedDevices": len(connected_agents)
    - len(ready_agents)
    - len(reconciling_agents),
    "registeredDevices": len(agents),
    "modes": sorted(agent_modes),
    "protocolVersions": sorted(protocol_versions),
    "accountIds": sorted(account_ids),
    "latestSnapshotAgeSeconds": (
      round(min(snapshot_ages), 3) if snapshot_ages else None
    ),
    "latestReadyHeartbeatAt": _iso_utc_timestamp(latest_ready_heartbeat_at),
    "reasonCode": (
      launch_block_reason
      if launch_block_reason
      else ""
      if ready_agents
      else (
        degraded_reason
        if connected_agents
        else next(
          (
            reason
            for reason in (
              QMT_ACCOUNT_MISMATCH,
              QMT_AGENT_STALE,
              QMT_AGENT_OFFLINE,
            )
            if reason in agent_reason_codes
          ),
          QMT_AGENT_OFFLINE,
        )
      )
    ),
  }
  market_data_agents = [
    agent
    for agent, status in connected_agents
    if "market-data" in list(agent.capabilities or [])
    and status != "XTDATA_UNAVAILABLE"
  ]
  market_stream_agents = []
  ready_market_stream_agents = []
  for agent in market_data_agents:
    heartbeat = heartbeat_by_component.get(f"qmt-agent:{agent.id}")
    details = dict(heartbeat.details or {}) if heartbeat else {}
    market_stream_status = str(details.get("marketStreamStatus") or "OFFLINE").upper()
    if market_stream_status != "OFFLINE":
      market_stream_agents.append(agent)
    if market_stream_status == "READY":
      ready_market_stream_agents.append(agent)
  try:
    stream_authority, trading_session = await asyncio.gather(
      market_stream_store.readiness_snapshot(),
      TradingTimeService().is_trading_hours(
        "SH",
        time_utils.now(),
      ),
    )
    stream_state, freshness_lease, engine_state = stream_authority
    stream_age = _aware_state_age(
      stream_state.updated_at if stream_state is not None else None,
      now,
    )
    engine_age = _aware_state_age(
      engine_state.updated_at if engine_state is not None else None,
      now,
    )
    market_readiness = classify_authoritative_market_stream_readiness(
      stream_state=stream_state,
      freshness_lease=freshness_lease,
      engine_state=engine_state,
      trading_session=trading_session,
    )
    ready = bool(
      ready_market_stream_agents
      and market_readiness.status
      in {
        MarketStreamReadinessStatus.PASSED,
        MarketStreamReadinessStatus.STANDBY,
      }
    )
    if ready:
      effective_status = "ready"
    elif not market_stream_agents:
      effective_status = "offline"
    elif stream_state is None or engine_state is None:
      effective_status = "syncing"
    elif stream_state.status != "READY":
      effective_status = str(stream_state.status).lower()
    elif engine_state.status != "READY" or (
      trading_session and not market_readiness.freshness_current
    ):
      effective_status = "stale"
    elif not market_readiness.converged:
      effective_status = "syncing"
    else:
      effective_status = "syncing"
    market_consumption = {
      "status": effective_status,
      "connectedDevices": (
        len(market_stream_agents)
        if stream_state is not None and stream_state.status != "OFFLINE"
        else 0
      ),
      "protocol": "quantx.market.v2",
      "streamId": stream_state.stream_id if stream_state is not None else "",
      "sequence": stream_state.sequence if stream_state is not None else 0,
      "engineSequence": engine_state.sequence if engine_state is not None else 0,
      "instrumentCount": (
        engine_state.instrument_count if engine_state is not None else 0
      ),
      "universeCount": (stream_state.universe_count if stream_state is not None else 0),
      "missingCount": (
        max(0, stream_state.universe_count - stream_state.instrument_count)
        if stream_state is not None
        else 0
      ),
      "commitPhase": (
        stream_state.commit_phase if stream_state is not None else "IDLE"
      ),
      "readinessStatus": market_readiness.status.value.lower(),
      "readinessMessage": market_readiness.message,
      "streamAgeSeconds": round(stream_age, 3) if stream_age is not None else None,
      "engineAgeSeconds": round(engine_age, 3) if engine_age is not None else None,
      "tradingSession": trading_session,
    }
  except Exception as exc:
    market_consumption = {
      "status": "unavailable",
      "connectedDevices": 0,
      "protocol": "quantx.market.v2",
      "error": exc.__class__.__name__,
    }
  engine = components.setdefault("engine", {"status": "offline"})
  engine["marketConsumption"] = market_consumption
  if engine["status"] == "ready" and market_consumption["status"] != "ready":
    engine["status"] = "degraded"
    engine["reasonCode"] = "ENGINE_MARKET_NOT_READY"
  return components


async def _prefect_status() -> dict[str, Any]:
  if not settings.prefect_enabled:
    return {"status": "disabled"}
  api_url = settings.prefect_api_url.rstrip("/")
  if not api_url.endswith("/api"):
    api_url += "/api"
  health_url = f"{api_url}/health"
  worker_pool = settings.prefect_worker_pool.strip() or "quantx-pool"
  workers_url = f"{api_url}/work_pools/{worker_pool}/workers/filter"
  try:
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
      response = await client.get(health_url)
      workers_response = await client.post(
        workers_url,
        json={},
      )
    workers = workers_response.json() if workers_response.is_success else []
    online_workers = [
      worker for worker in workers if str(worker.get("status", "")).upper() == "ONLINE"
    ]
    return {
      "status": "ready" if response.is_success else "unavailable",
      "statusCode": response.status_code,
      "workersStatusCode": workers_response.status_code,
      "workerStatus": "ready" if online_workers else "offline",
      "onlineWorkers": len(online_workers),
      "registeredWorkers": len(workers),
      "offlineWorkers": len(workers) - len(online_workers),
      "workers": [
        {
          "name": worker.get("name"),
          "status": worker.get("status"),
          "lastHeartbeatTime": worker.get("last_heartbeat_time"),
        }
        for worker in online_workers
      ],
    }
  except Exception as exc:
    return {"status": "unavailable", "error": exc.__class__.__name__}


async def _market_gateway_status() -> dict[str, Any]:
  try:
    async with httpx.AsyncClient(
      timeout=MARKET_GATEWAY_HTTP_TIMEOUT_SECONDS, trust_env=False
    ) as client:
      response = await client.get(
        f"{settings.market_gateway_url.rstrip('/')}/health/ready"
      )
    payload = MarketGatewayHealth.model_validate(response.json())
    if response.status_code != (200 if payload.status == "ready" else 503):
      raise ValueError("market gateway HTTP status disagrees with its health")
    return {
      **payload.model_dump(mode="json", by_alias=True),
      "status": "ready" if payload.status == "ready" else "unavailable",
      "statusCode": response.status_code,
    }
  except Exception as exc:
    return {
      "status": "unavailable",
      "reasonCode": "MARKET_GATEWAY_UNAVAILABLE",
      "error": exc.__class__.__name__,
    }


async def component_status() -> dict[str, dict[str, Any]]:
  database, heartbeats, prefect, market_gateway = await asyncio.gather(
    _database_status(),
    _component_heartbeats(),
    _prefect_status(),
    _market_gateway_status(),
  )
  raw_ai_runtime = heartbeats.get("ai-runtime", {"status": "offline"})
  ai_runtime = {
    "status": raw_ai_runtime.get("status", "offline"),
    "ageSeconds": raw_ai_runtime.get("ageSeconds"),
  }
  details = dict(raw_ai_runtime.get("details") or {})
  if details.get("configVersion") is not None:
    ai_runtime["configVersion"] = details.get("configVersion")
  return {
    "api": heartbeats.get("api", {"status": "offline"}),
    "database": database,
    "engine": heartbeats.get("engine", {"status": "offline"}),
    "worker": {
      "status": prefect.get("workerStatus", "offline"),
      "onlineWorkers": prefect.get("onlineWorkers", 0),
      "registeredWorkers": prefect.get("registeredWorkers", 0),
      "offlineWorkers": prefect.get("offlineWorkers", 0),
      "workers": prefect.get("workers", []),
    },
    "qmtAgent": {"status": "disabled", "reason": "DEVELOPMENT_NO_QMT"} if settings.environment == "development" else heartbeats["qmt-agent"],
    "aiRuntime": ai_runtime,
    "marketData": market_gateway,
    "prefect": prefect,
  }


async def market_data_runtime_status() -> dict[str, Any]:
  """Project the gateway's supply health without probing trading or consumers."""
  return await _market_gateway_status()


def required_components() -> tuple[str, ...]:
  profile = getattr(settings, "runtime_profile", "web").lower()
  if settings.environment == "development":
    import os

    required = ("api", "database", "engine")
    if profile == "full":
      required += ("prefect", "worker")
    if os.environ.get("QUANTX_MARKET_DATA_URL"):
      required += ("marketData",)
    return required
  if profile == "full":
    return (
      "api",
      "database",
      "engine",
      "prefect",
      "worker",
      "qmtAgent",
      "marketData",
    )
  return ("api", "database", "engine")


async def readiness_status() -> tuple[bool, dict[str, Any]]:
  components = await component_status()
  required = required_components()
  ready = all(components[name]["status"] == "ready" for name in required)
  return ready, {
    "status": "ready" if ready else "not_ready",
    "profile": getattr(settings, "runtime_profile", "web"),
    "requiredComponents": list(required),
    "components": components,
  }
