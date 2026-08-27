"""Account-wide live execution safety and authorization controls."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from quantx_domain.clock import to_naive_utc, utcnow
from sqlalchemy import func, literal, select
from sqlalchemy.orm import aliased

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AccountExecutionControlEvent,
  AgentDevice,
  AgentReportInbox,
  OperationalAlert,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  TradeCommandOutbox,
)
from quantx_infrastructure.services.agent_session_guard import (
  API_HEARTBEAT_COMPONENT,
  REMOTE_AGENT_NOT_RECONCILED,
  evaluate_agent_session,
)
from quantx_infrastructure.services.operational_alert_service import (
  OperationalAlertService,
)

ACCOUNT_EXECUTION_ALERT_CODES = frozenset(
  {
    "ACCOUNT_EXECUTION_CONTROL_FAILED",
    "ACCOUNT_RECONCILIATION_FAILED",
    "HARD_KILL_ACTIVATED",
  }
)
ACCOUNT_EXECUTION_CHECK_CODES = frozenset(
  {
    "SERVER_REAL_TRADING_ENABLED",
    "ACCOUNT_ALLOWLISTED",
    "ENGINE_READY",
    "LIVE_AGENT_READY",
    "AGENT_MODE_LIVE",
    "MARKET_STREAM_READY",
    "PROTOCOL_1_1",
    "EXECUTION_CONTROL_CONFIGURED",
    "SNAPSHOT_RECONCILED",
    "SNAPSHOT_FRESH",
    "SNAPSHOT_ACTIVITY_CLASSIFIED",
    "RECENT_BACKUP",
    "NO_CRITICAL_ALERTS",
    "NO_DEAD_LETTERS",
    "CONTROLLED_WINDOW_ACTIVE",
    "NO_EXTERNAL_BROKER_ACTIVITY",
    "KILL_SWITCH_CLEAR",
    "ACCOUNT_RISK_INCREASE_AUTHORIZED",
  }
)
_RISK_REDUCTION_CHECKS = frozenset(
  {
    "SERVER_REAL_TRADING_ENABLED",
    "ACCOUNT_ALLOWLISTED",
    "ENGINE_READY",
    "LIVE_AGENT_READY",
    "AGENT_MODE_LIVE",
    "PROTOCOL_1_1",
    "EXECUTION_CONTROL_CONFIGURED",
    "SNAPSHOT_RECONCILED",
    "SNAPSHOT_FRESH",
  }
)
_AUTHORIZATION_CHECK = "ACCOUNT_RISK_INCREASE_AUTHORIZED"


class AccountExecutionControlIdempotencyError(ValueError):
  code = "IDEMPOTENCY_KEY_REUSED"


def _unique_messages(values: list[str]) -> list[str]:
  return list(dict.fromkeys(value for value in values if value))


def project_account_execution_safety(readiness: dict[str, Any]) -> dict[str, Any]:
  """Project capabilities from account-owned checks only.

  The allowlist is intentional: a feature may compose this projection, but it
  cannot inject one of its own rollout checks into account-wide authorization.
  """

  checks = [
    dict(item)
    for item in list(readiness.get("checks") or [])
    if str(item.get("code") or "") in ACCOUNT_EXECUTION_CHECK_CODES
  ]
  failed_checks = [item for item in checks if not bool(item.get("passed"))]
  authorization_state = str(readiness.get("authorization_state") or "DISABLED").upper()
  activation_failures = [
    item for item in failed_checks if item.get("code") != _AUTHORIZATION_CHECK
  ]
  reduction_failures = [
    item
    for item in failed_checks
    if str(item.get("code") or "") in _RISK_REDUCTION_CHECKS
  ]
  observation_failures = [
    item for item in failed_checks if str(item.get("scope") or "") == "OBSERVATION"
  ]
  can_reduce_risk = not reduction_failures
  can_activate_automation = not activation_failures
  can_increase_risk = not failed_checks

  if authorization_state == "KILLED":
    health_status, execution_mode = "KILLED", "KILLED"
  elif observation_failures:
    health_status = "BLOCKED"
    execution_mode = "REDUCE_ONLY" if can_reduce_risk else "OBSERVE_ONLY"
  elif can_increase_risk:
    health_status, execution_mode = "HEALTHY", "TRADING"
  else:
    health_status = "HEALTHY"
    execution_mode = "REDUCE_ONLY" if can_reduce_risk else "OBSERVE_ONLY"

  blocked_reasons = _unique_messages(
    [
      str(item.get("message") or item.get("code") or "账户实盘门禁未通过")
      for item in failed_checks
    ]
  )
  if authorization_state == "KILLED":
    summary = (
      "账户紧急停止已触发；仍仅允许风险降低型卖出"
      if can_reduce_risk
      else "账户紧急停止已触发；实盘执行已关闭"
    )
  elif can_increase_risk:
    summary = "账户状态与买入条件均已通过"
  elif can_reduce_risk:
    summary = "账户事实已收敛；当前仅允许减仓"
  elif not observation_failures:
    summary = "账户观察与对账正常；买入权限保持关闭"
  else:
    summary = blocked_reasons[0] if blocked_reasons else "账户安全状态未就绪"

  return {
    "authorization_state": authorization_state,
    "health_status": health_status,
    "execution_mode": execution_mode,
    "can_increase_risk": can_increase_risk,
    "can_reduce_risk": can_reduce_risk,
    "can_activate_automation": can_activate_automation,
    "summary": summary,
    "blocked_reasons": blocked_reasons,
    "checks": checks,
  }


class AccountExecutionSafetyService:
  """Own generic broker facts and account-wide risk-increase authorization."""

  @staticmethod
  def _fresh(heartbeat: RuntimeComponentHeartbeat | None) -> bool:
    if heartbeat is None:
      return False
    return bool(
      str(heartbeat.status).upper() == "READY"
      and to_naive_utc(heartbeat.updated_at) >= utcnow() - timedelta(seconds=90)
    )

  @staticmethod
  def _agent_fresh(
    heartbeat: RuntimeComponentHeartbeat | None,
    api_heartbeat: RuntimeComponentHeartbeat | None,
  ) -> bool:
    return evaluate_agent_session(
      heartbeat,
      api_heartbeat,
      now=utcnow(),
      acceptable_statuses={"READY"},
    ).current

  @classmethod
  def _agent_candidate_rank(
    cls,
    heartbeat: RuntimeComponentHeartbeat | None,
    api_heartbeat: RuntimeComponentHeartbeat | None,
  ) -> tuple[bool, datetime]:
    updated_at = (
      to_naive_utc(heartbeat.updated_at) if heartbeat is not None else datetime.min
    )
    return cls._agent_fresh(heartbeat, api_heartbeat), updated_at

  @staticmethod
  def _normalized_broker_order_status(value: Any) -> str:
    names = {
      48: "PENDING",
      49: "SUBMITTED",
      50: "SUBMITTED",
      51: "SUBMITTED",
      52: "PARTIAL_FILLED",
      53: "CANCELLED",
      54: "CANCELLED",
      55: "PARTIAL_FILLED",
      56: "FILLED",
      57: "REJECTED",
      255: "PENDING",
    }
    try:
      return names[int(value)]
    except (TypeError, ValueError, KeyError):
      text = str(value or "").strip().upper()
      return {
        "REPORTED": "SUBMITTED",
        "ACCEPTED": "SUBMITTED",
        "PARTIALLY_FILLED": "PARTIAL_FILLED",
        "CANCELED": "CANCELLED",
      }.get(text, text or "PENDING")

  @staticmethod
  def _invalidate_controlled_window(control: AccountExecutionControl) -> None:
    control.controlled_window_active = False
    control.controlled_window_snapshot_id = None
    control.controlled_window_snapshot_hash = None
    control.controlled_window_started_at = None
    control.controlled_window_started_by_user_id = None
    control.controlled_window_external_order_ids = []
    control.controlled_window_external_trade_ids = []

  @staticmethod
  def _append_event(
    db,
    *,
    account_id: str,
    event_type: str,
    event_id: str | None = None,
    actor_user_id: str | None = None,
    previous_state: str | None = None,
    next_state: str | None = None,
    snapshot_id: str | None = None,
    details: dict[str, Any] | None = None,
  ) -> None:
    db.add(
      AccountExecutionControlEvent(
        event_id=event_id or str(uuid.uuid4()),
        account_id=account_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        previous_state=previous_state,
        next_state=next_state,
        snapshot_id=snapshot_id,
        details=dict(details or {}),
        created_at=utcnow(),
      )
    )

  @staticmethod
  def _assert_event_binding(
    event: AccountExecutionControlEvent,
    *,
    account_id: str,
    operation_id: str,
    event_type: str,
    actor_user_id: str | None,
    snapshot_id: str | None = None,
    expected_state_version: int | None = None,
    reason: str | None = None,
  ) -> None:
    details = dict(event.details or {})
    if (
      str(event.account_id) != account_id
      or str(event.event_type) != event_type
      or str(details.get("operationId") or "") != operation_id
      or str(event.actor_user_id or "") != str(actor_user_id or "")
      or (snapshot_id is not None and str(event.snapshot_id or "") != snapshot_id)
      or (
        expected_state_version is not None
        and int(details.get("expectedStateVersion") or 0) != expected_state_version
      )
      or (reason is not None and str(details.get("reason") or "") != reason)
    ):
      raise AccountExecutionControlIdempotencyError(
        "账户执行控制幂等标识已绑定其他操作"
      )

  async def operation_marker_exists(
    self,
    account_id: str,
    operation_id: str,
    *,
    event_type: str,
    actor_user_id: str | None,
    snapshot_id: str | None = None,
    expected_state_version: int | None = None,
    reason: str | None = None,
  ) -> bool:
    async with AsyncSessionLocal() as db:
      event = await db.get(AccountExecutionControlEvent, operation_id)
      if event is None:
        return False
      self._assert_event_binding(
        event,
        account_id=account_id,
        operation_id=operation_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        snapshot_id=snapshot_id,
        expected_state_version=expected_state_version,
        reason=reason,
      )
      return True

  async def ensure_control(self, account_id: str) -> AccountExecutionControl:
    async with AsyncSessionLocal() as db:
      control = await db.get(AccountExecutionControl, account_id)
      if control is None:
        control = AccountExecutionControl(account_id=account_id)
        db.add(control)
        await db.commit()
        await db.refresh(control)
      return control

  async def _readiness_snapshot(self, db, account_id: str):
    anchor = select(literal(account_id).label("account_id")).subquery()
    engine_heartbeat = aliased(RuntimeComponentHeartbeat)
    agent_heartbeat = aliased(RuntimeComponentHeartbeat)
    api_heartbeat = aliased(RuntimeComponentHeartbeat)
    queued_count = (
      select(func.count(TradeCommandOutbox.message_id))
      .where(
        TradeCommandOutbox.account_id == account_id,
        TradeCommandOutbox.delivery_status.in_(("QUEUED", "DELIVERED")),
      )
      .scalar_subquery()
    )
    oldest_queued_at = (
      select(func.min(TradeCommandOutbox.created_at))
      .where(
        TradeCommandOutbox.account_id == account_id,
        TradeCommandOutbox.delivery_status.in_(("QUEUED", "DELIVERED")),
      )
      .scalar_subquery()
    )
    dead_letter_count = (
      select(func.count(OperationalAlert.id))
      .where(
        OperationalAlert.status != "RESOLVED",
        OperationalAlert.code == "AGENT_REPORT_DEAD_LETTER",
        (OperationalAlert.account_id == account_id)
        | (OperationalAlert.account_id.is_(None)),
      )
      .scalar_subquery()
    )
    unresolved_critical_alert_count = (
      select(func.count(OperationalAlert.id))
      .where(
        OperationalAlert.status != "RESOLVED",
        OperationalAlert.severity.in_(("SEV1", "SEV2")),
        OperationalAlert.code.in_(ACCOUNT_EXECUTION_ALERT_CODES),
        (OperationalAlert.account_id == account_id)
        | (OperationalAlert.account_id.is_(None)),
      )
      .scalar_subquery()
    )
    return (
      await db.execute(
        select(
          AccountExecutionControl,
          engine_heartbeat,
          AgentDevice,
          agent_heartbeat,
          api_heartbeat,
          queued_count,
          oldest_queued_at,
          dead_letter_count,
          unresolved_critical_alert_count,
        )
        .select_from(anchor)
        .outerjoin(
          AccountExecutionControl,
          AccountExecutionControl.account_id == anchor.c.account_id,
        )
        .outerjoin(engine_heartbeat, engine_heartbeat.component == "engine")
        .outerjoin(api_heartbeat, api_heartbeat.component == API_HEARTBEAT_COMPONENT)
        .outerjoin(AgentDevice, AgentDevice.revoked_at.is_(None))
        .outerjoin(
          agent_heartbeat,
          agent_heartbeat.component == literal("qmt-agent:").concat(AgentDevice.id),
        )
      )
    ).all()

  async def status(self, account_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
      rows = await self._readiness_snapshot(db, account_id)
      if not rows:
        raise RuntimeError("账户执行安全状态数据库查询未返回结果")
      (
        control,
        engine,
        _,
        _,
        api_heartbeat,
        queued_count,
        oldest_queued_at,
        dead_letter_count,
        unresolved_critical_alert_count,
      ) = rows[0]
      device = None
      agent = None
      live_agent_candidates = []
      for row in rows:
        candidate_device = row[2]
        if candidate_device is None:
          continue
        if account_id in list(candidate_device.authorized_account_ids or []) and (
          "live"
          in {str(value).lower() for value in list(candidate_device.capabilities or [])}
        ):
          candidate_agent = row[3]
          live_agent_candidates.append((candidate_device, candidate_agent))
          if device is None or self._agent_candidate_rank(
            candidate_agent,
            api_heartbeat,
          ) > self._agent_candidate_rank(agent, api_heartbeat):
            device = candidate_device
            agent = candidate_agent

      now = utcnow()
      ready_live_agents = [
        (candidate_device, candidate_agent)
        for candidate_device, candidate_agent in live_agent_candidates
        if self._agent_fresh(candidate_agent, api_heartbeat)
      ]
      multiple_ready_live_agents = len(ready_live_agents) > 1
      if len(ready_live_agents) == 1:
        device, agent = ready_live_agents[0]
      agent_details = dict(agent.details or {}) if agent else {}
      capabilities = {
        str(value).lower() for value in list(agent_details.get("capabilities") or [])
      }
      reported_agent_mode = next(
        (value for value in ("live", "paper", "data-only") if value in capabilities),
        "offline",
      )
      reported_protocol_version = str(agent_details.get("protocolVersion") or "")
      agent_session = evaluate_agent_session(
        agent,
        api_heartbeat,
        now=now,
        acceptable_statuses={"READY"},
      )
      agent_heartbeat_fresh = agent_session.current
      live_agent_ready = bool(
        not multiple_ready_live_agents
        and len(ready_live_agents) == 1
        and agent_heartbeat_fresh
        and str(agent.status).upper() == "READY"
      )
      if multiple_ready_live_agents:
        live_agent_blocked_reason = (
          "同一账户检测到多个就绪 live QMT Agent，必须先恢复唯一会话"
        )
      elif device is None:
        live_agent_blocked_reason = "没有绑定该账户且具备 live 能力的已登记 QMT Agent"
      elif agent is None:
        live_agent_blocked_reason = "对应账户的 live Agent 尚未上报心跳"
      elif not agent_heartbeat_fresh:
        live_agent_blocked_reason = "对应账户的 live Agent 已离线或心跳超过 90 秒"
      else:
        live_agent_blocked_reason = "对应账户的 live Agent 当前未就绪"

      agent_reason_code = agent_session.reason_code or (
        "" if live_agent_ready else REMOTE_AGENT_NOT_RECONCILED
      )
      if not multiple_ready_live_agents and not live_agent_ready and agent_reason_code:
        live_agent_blocked_reason = (
          f"远程 QMT Agent 会话不可用于实盘（{agent_reason_code}）"
        )

      account_reconciliation = dict(agent_details.get("accountReconciliation") or {})
      reconciliation_summary = dict(account_reconciliation.get(account_id) or {})
      current_snapshot_id = str(control.last_snapshot_id if control else "")
      activity_classification_current = bool(
        current_snapshot_id
        and str(reconciliation_summary.get("snapshotId") or "") == current_snapshot_id
      )
      external_order_count = max(
        0, int(reconciliation_summary.get("externalOrderCount") or 0)
      )
      external_trade_count = max(
        0, int(reconciliation_summary.get("externalTradeCount") or 0)
      )
      controlled_window_active = bool(control and control.controlled_window_active)
      controlled_window_snapshot_id = str(
        control.controlled_window_snapshot_id
        if control and control.controlled_window_snapshot_id
        else ""
      )
      current_reconciliation_snapshot_id = str(
        reconciliation_summary.get("snapshotId") or ""
      )
      if (
        controlled_window_active
        and controlled_window_snapshot_id
        and current_reconciliation_snapshot_id == controlled_window_snapshot_id
      ):
        new_external_order_count = 0
        new_external_trade_count = 0
        working_external_order_count = 0
      else:
        new_external_order_count = max(
          0,
          int(
            reconciliation_summary.get("newExternalOrderCount", external_order_count)
            or 0
          ),
        )
        new_external_trade_count = max(
          0,
          int(
            reconciliation_summary.get("newExternalTradeCount", external_trade_count)
            or 0
          ),
        )
        working_external_order_count = max(
          0, int(reconciliation_summary.get("workingExternalOrderCount") or 0)
        )

      snapshot_at = (
        to_naive_utc(control.last_snapshot_at)
        if control and control.last_snapshot_at
        else None
      )
      backup_at = (
        to_naive_utc(control.last_backup_at)
        if control and control.last_backup_at
        else None
      )
      snapshot_age = (
        max(0.0, (now - snapshot_at).total_seconds()) if snapshot_at else None
      )
      backup_age = max(0.0, (now - backup_at).total_seconds()) if backup_at else None
      queue_delay = (
        max(0.0, (now - to_naive_utc(oldest_queued_at)).total_seconds())
        if oldest_queued_at
        else 0.0
      )
      dead_letter_count = int(dead_letter_count or 0)
      unresolved_critical_alert_count = int(unresolved_critical_alert_count or 0)
      authorization_state = str(
        control.authorization_state if control else "DISABLED"
      ).upper()

      checks = [
        (
          "SERVER_REAL_TRADING_ENABLED",
          bool(settings.enable_real_trading),
          "服务端 ENABLE_REAL_TRADING 未启用",
          "INCREASE_RISK",
        ),
        (
          "ACCOUNT_ALLOWLISTED",
          account_id in set(settings.real_trading_account_allowlist or []),
          "账户不在 REAL_TRADING_ACCOUNT_ALLOWLIST",
          "INCREASE_RISK",
        ),
        ("ENGINE_READY", self._fresh(engine), "Engine 心跳缺失或已过期", "OBSERVATION"),
        (
          "LIVE_AGENT_READY",
          live_agent_ready,
          live_agent_blocked_reason,
          "OBSERVATION",
        ),
        (
          "AGENT_MODE_LIVE",
          reported_agent_mode == "live",
          "QMT Agent 尚未明确切换到 live 模式",
          "OBSERVATION",
        ),
        (
          "MARKET_STREAM_READY",
          str(agent_details.get("marketStreamStatus") or "").upper() == "READY",
          "全市场行情尚未完成远程三阶段同步",
          "INCREASE_RISK",
        ),
        (
          "PROTOCOL_1_1",
          reported_protocol_version == "1.1",
          "账户观察与真实下单均要求 Agent 协议 1.1",
          "OBSERVATION",
        ),
        (
          "EXECUTION_CONTROL_CONFIGURED",
          control is not None,
          "账户尚未创建独立执行控制配置",
          "OBSERVATION",
        ),
        (
          "SNAPSHOT_RECONCILED",
          bool(control and control.reconcile_status == "READY"),
          "资金、持仓、委托和成交快照尚未完成对账",
          "OBSERVATION",
        ),
        (
          "SNAPSHOT_FRESH",
          snapshot_age is not None and snapshot_age <= 90,
          "账户完整快照缺失或已超过 90 秒",
          "OBSERVATION",
        ),
        (
          "SNAPSHOT_ACTIVITY_CLASSIFIED",
          activity_classification_current,
          "最新完整快照尚未完成手工/外部交易分类",
          "OBSERVATION",
        ),
        (
          "RECENT_BACKUP",
          backup_age is not None and backup_age < 24 * 60 * 60,
          "最近成功备份缺失或已超过 24 小时",
          "INCREASE_RISK",
        ),
        (
          "NO_CRITICAL_ALERTS",
          unresolved_critical_alert_count == 0,
          "存在尚未解决的账户执行 Sev-1/Sev-2 运行告警",
          "OBSERVATION",
        ),
        (
          "NO_DEAD_LETTERS",
          dead_letter_count == 0,
          "存在尚未被后续权威快照取代的 Agent 报告死信",
          "OBSERVATION",
        ),
        (
          "CONTROLLED_WINDOW_ACTIVE",
          controlled_window_active,
          "尚未基于最新完整快照建立账户实盘窗口",
          "INCREASE_RISK",
        ),
        (
          "NO_EXTERNAL_BROKER_ACTIVITY",
          new_external_order_count == 0
          and new_external_trade_count == 0
          and working_external_order_count == 0,
          "账户实盘窗口后出现新的 QMT 手工/外部交易或仍有活动委托",
          "INCREASE_RISK",
        ),
        (
          "KILL_SWITCH_CLEAR",
          authorization_state != "KILLED",
          "账户紧急停止已触发",
          "OBSERVATION",
        ),
        (
          _AUTHORIZATION_CHECK,
          authorization_state == "ENABLED",
          "账户买入权限未启用",
          "INCREASE_RISK",
        ),
      ]
      items = [
        {
          "code": code,
          "passed": passed,
          "message": "" if passed else message,
          "scope": scope,
        }
        for code, passed, message, scope in checks
      ]
      projection = project_account_execution_safety(
        {"authorization_state": authorization_state, "checks": items}
      )

      return {
        "account_id": account_id,
        "authorization_state": authorization_state,
        "state_version": int(control.state_version if control else 0),
        "health_status": projection["health_status"],
        "execution_mode": projection["execution_mode"],
        "can_increase_risk": projection["can_increase_risk"],
        "can_reduce_risk": projection["can_reduce_risk"],
        "can_activate_automation": projection["can_activate_automation"],
        "summary": projection["summary"],
        "blocked_reasons": projection["blocked_reasons"],
        "checks": projection["checks"],
        "engine_status": str(engine.status if engine else "OFFLINE"),
        "agent_status": str(agent.status)
        if agent_heartbeat_fresh and agent
        else "OFFLINE",
        "agent_device_id": str(device.id) if device else None,
        "ready_live_agent_count": len(ready_live_agents),
        "agent_mode": reported_agent_mode if agent_heartbeat_fresh else "offline",
        "requested_agent_mode": reported_agent_mode or "unknown",
        "qmt_launch_reason_code": agent_reason_code,
        "protocol_version": reported_protocol_version if agent_heartbeat_fresh else "",
        "reconcile_status": str(control.reconcile_status if control else "UNKNOWN"),
        "kill_switch": authorization_state == "KILLED",
        "execution_window_active": controlled_window_active,
        "snapshot_id": control.last_snapshot_id if control else None,
        "snapshot_hash": control.last_snapshot_hash if control else None,
        "snapshot_at": snapshot_at,
        "reconciliation_age_seconds": snapshot_age,
        "queued_command_count": int(queued_count or 0),
        "queue_delay_seconds": queue_delay,
        "dead_letter_count": dead_letter_count,
        "unresolved_critical_alert_count": unresolved_critical_alert_count,
        "manual_coexistence": bool(
          reconciliation_summary.get("manualCoexistence")
          if activity_classification_current
          else authorization_state != "ENABLED"
        ),
        "external_order_count": external_order_count,
        "external_trade_count": external_trade_count,
        "controlled_window_snapshot_id": controlled_window_snapshot_id or None,
        "controlled_window_started_at": to_naive_utc(
          control.controlled_window_started_at
        )
        if control and control.controlled_window_started_at
        else None,
        "new_external_order_count": new_external_order_count,
        "new_external_trade_count": new_external_trade_count,
        "working_external_order_count": working_external_order_count,
        "journal_integrity": str(agent_details.get("journalIntegrity") or "unknown"),
        "journal_size_bytes": int(agent_details.get("journalSizeBytes") or 0),
        "journal_pending_reports": int(agent_details.get("journalPendingReports") or 0),
        "last_backup_at": backup_at,
        "checked_at": now,
      }

  async def _latest_full_snapshot(
    self, db, *, account_id: str, snapshot_id: str
  ) -> dict[str, Any]:
    reports = (
      (
        await db.execute(
          select(AgentReportInbox)
          .where(
            AgentReportInbox.message_type == "delta_report",
            AgentReportInbox.protocol_version == "1.1",
          )
          .order_by(AgentReportInbox.received_at.desc())
          .limit(100)
        )
      )
      .scalars()
      .all()
    )
    for report in reports:
      payload = dict(report.payload or {})
      if (
        bool(payload.get("is_complete"))
        and str(payload.get("snapshot_id") or "") == snapshot_id
        and account_id
        in {str(item.get("account_id") or "") for item in payload.get("accounts") or []}
      ):
        return payload
    raise ValueError("最新权威账户快照原文不可用，请等待 Agent 再次完整上报")

  async def _external_snapshot_activity(
    self, db, *, account_id: str, payload: dict[str, Any]
  ) -> dict[str, list[dict[str, str]]]:
    pending = (
      (
        await db.execute(
          select(PendingTradeOrder).where(PendingTradeOrder.account_id == account_id)
        )
      )
      .scalars()
      .all()
    )
    by_client = {str(item.client_order_id) for item in pending}
    by_broker = {str(item.broker_order_id) for item in pending if item.broker_order_id}
    external_orders: list[dict[str, str]] = []
    for raw in payload.get("orders") or []:
      item = dict(raw)
      if str(item.get("account_id") or "") != account_id:
        continue
      client_id = str(item.get("client_order_id") or "")
      broker_id = str(item.get("order_id") or item.get("broker_order_id") or "")
      if client_id in by_client or broker_id in by_broker:
        continue
      business_id = broker_id or client_id
      if not business_id:
        raise ValueError("最新快照包含无法建立审计身份的外部委托")
      external_orders.append(
        {
          "business_id": business_id,
          "status": self._normalized_broker_order_status(
            item.get("effective_order_status")
            or item.get("order_status", item.get("status"))
          ),
        }
      )
    external_trades: list[dict[str, str]] = []
    for raw in payload.get("trades") or []:
      item = dict(raw)
      if str(item.get("account_id") or "") != account_id:
        continue
      client_id = str(item.get("client_order_id") or "")
      broker_id = str(item.get("order_id") or item.get("broker_order_id") or "")
      if client_id in by_client or broker_id in by_broker:
        continue
      business_id = str(
        item.get("execution_id")
        or item.get("traded_id")
        or item.get("trade_id")
        or broker_id
        or client_id
        or ""
      )
      if not business_id:
        raise ValueError("最新快照包含无法建立审计身份的外部成交")
      external_trades.append({"business_id": business_id, "status": "FILLED"})
    return {"orders": external_orders, "trades": external_trades}

  async def begin_controlled_window(
    self,
    account_id: str,
    *,
    user_id: str,
    snapshot_id: str,
    expected_state_version: int,
    operation_id: str,
  ) -> dict[str, Any]:
    await self.ensure_control(account_id)
    if await self.operation_marker_exists(
      account_id,
      operation_id,
      event_type="CONTROLLED_WINDOW_STARTED",
      actor_user_id=user_id,
      snapshot_id=snapshot_id,
      expected_state_version=expected_state_version,
    ):
      return await self.status(account_id)
    readiness = await self.status(account_id)
    required_codes = {
      "ENGINE_READY",
      "LIVE_AGENT_READY",
      "AGENT_MODE_LIVE",
      "PROTOCOL_1_1",
      "SNAPSHOT_RECONCILED",
      "SNAPSHOT_FRESH",
      "SNAPSHOT_ACTIVITY_CLASSIFIED",
      "KILL_SWITCH_CLEAR",
    }
    failures = [
      item["message"]
      for item in readiness["checks"]
      if item["code"] in required_codes and not item["passed"]
    ]
    if failures:
      raise ValueError("；".join(failures))
    if str(readiness.get("snapshot_id") or "") != snapshot_id:
      raise ValueError("完整快照已经更新，请刷新页面后重新确认账户实盘窗口")
    async with AsyncSessionLocal() as db:
      control = await db.get(AccountExecutionControl, account_id, with_for_update=True)
      if control is None:
        raise ValueError("账户执行控制配置不存在")
      existing = await db.get(AccountExecutionControlEvent, operation_id)
      if existing is not None:
        self._assert_event_binding(
          existing,
          account_id=account_id,
          operation_id=operation_id,
          event_type="CONTROLLED_WINDOW_STARTED",
          actor_user_id=user_id,
          snapshot_id=snapshot_id,
          expected_state_version=expected_state_version,
        )
        await db.rollback()
        return await self.status(account_id)
      if int(control.state_version) != expected_state_version:
        raise ValueError("账户执行控制状态已变化，请刷新后重试")
      if str(control.last_snapshot_id or "") != snapshot_id:
        raise ValueError("完整快照已经更新，请刷新页面后重新确认账户实盘窗口")
      payload = await self._latest_full_snapshot(
        db, account_id=account_id, snapshot_id=snapshot_id
      )
      activity = await self._external_snapshot_activity(
        db, account_id=account_id, payload=payload
      )
      control.controlled_window_active = True
      control.controlled_window_snapshot_id = snapshot_id
      control.controlled_window_snapshot_hash = control.last_snapshot_hash
      control.controlled_window_started_at = utcnow()
      control.controlled_window_started_by_user_id = user_id
      control.controlled_window_external_order_ids = [
        item["business_id"] for item in activity["orders"]
      ]
      control.controlled_window_external_trade_ids = [
        item["business_id"] for item in activity["trades"]
      ]
      control.state_version = int(control.state_version) + 1
      self._append_event(
        db,
        account_id=account_id,
        event_id=operation_id,
        event_type="CONTROLLED_WINDOW_STARTED",
        actor_user_id=user_id,
        previous_state=str(control.authorization_state),
        next_state=str(control.authorization_state),
        snapshot_id=snapshot_id,
        details={
          "operationId": operation_id,
          "expectedStateVersion": expected_state_version,
          "acknowledgedExternalOrderCount": len(activity["orders"]),
          "acknowledgedExternalTradeCount": len(activity["trades"]),
        },
      )
      await db.commit()
    return await self.status(account_id)

  async def set_authorization_state(
    self,
    account_id: str,
    *,
    target_state: str,
    user_id: str,
    expected_state_version: int,
    operation_id: str,
    reason: str = "",
  ) -> dict[str, Any]:
    target = str(target_state or "").upper()
    if target not in {"ENABLED", "PAUSED", "KILLED", "DISABLED"}:
      raise ValueError("账户执行授权目标状态无效")
    event_type = {
      "ENABLED": "RISK_INCREASE_ENABLED",
      "PAUSED": "RISK_INCREASE_PAUSED",
      "KILLED": "HARD_KILL_ACTIVATED",
      "DISABLED": "KILL_SWITCH_CLEARED",
    }[target]
    normalized_reason = str(reason or "")[:2000]
    await self.ensure_control(account_id)
    if await self.operation_marker_exists(
      account_id,
      operation_id,
      event_type=event_type,
      actor_user_id=user_id,
      expected_state_version=expected_state_version,
      reason=normalized_reason,
    ):
      return await self.status(account_id)
    if target == "ENABLED":
      readiness = await self.status(account_id)
      failures = [
        item["message"]
        for item in readiness["checks"]
        if item["code"] != _AUTHORIZATION_CHECK and not item["passed"]
      ]
      if failures:
        raise ValueError("；".join(failures))
    async with AsyncSessionLocal() as db:
      control = await db.get(AccountExecutionControl, account_id, with_for_update=True)
      if control is None:
        raise ValueError("账户执行控制配置不存在")
      existing = await db.get(AccountExecutionControlEvent, operation_id)
      if existing is not None:
        self._assert_event_binding(
          existing,
          account_id=account_id,
          operation_id=operation_id,
          event_type=event_type,
          actor_user_id=user_id,
          expected_state_version=expected_state_version,
          reason=normalized_reason,
        )
        await db.rollback()
        return await self.status(account_id)
      if int(control.state_version) != expected_state_version:
        raise ValueError("账户执行控制状态已变化，请刷新后重试")
      previous_state = str(control.authorization_state)
      if previous_state == "KILLED" and target not in {"KILLED", "DISABLED"}:
        raise ValueError("账户紧急停止只能通过清除 kill switch 操作解除")
      if target == "DISABLED" and previous_state != "KILLED":
        raise ValueError("只有已触发紧急停止的账户才能清除 kill switch")
      control.authorization_state = target
      control.authorized_by_user_id = user_id if target == "ENABLED" else None
      control.authorized_at = utcnow() if target == "ENABLED" else None
      control.paused_reason = normalized_reason or None
      if target != "ENABLED":
        self._invalidate_controlled_window(control)
      control.state_version = int(control.state_version) + 1
      self._append_event(
        db,
        account_id=account_id,
        event_id=operation_id,
        event_type=event_type,
        actor_user_id=user_id,
        previous_state=previous_state,
        next_state=target,
        snapshot_id=control.last_snapshot_id,
        details={
          "operationId": operation_id,
          "expectedStateVersion": expected_state_version,
          "reason": normalized_reason,
        },
      )
      if target == "KILLED":
        await self._enqueue_hard_kill(
          db,
          account_id=account_id,
          event_id=operation_id,
          reason=normalized_reason or "manual account kill switch",
          first_activation=previous_state != "KILLED",
        )
      await db.commit()
    return await self.status(account_id)

  async def _enqueue_hard_kill(
    self,
    db,
    *,
    account_id: str,
    event_id: str,
    reason: str,
    first_activation: bool,
  ) -> None:
    now = utcnow()
    pending_orders = (
      (
        await db.execute(
          select(PendingTradeOrder).where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.status.in_(
              ("QUEUED", "PENDING", "SUBMITTED", "PARTIAL_FILLED")
            ),
          )
        )
      )
      .scalars()
      .all()
    )
    source_commands = (
      (
        await db.execute(
          select(TradeCommandOutbox).where(TradeCommandOutbox.account_id == account_id)
        )
      )
      .scalars()
      .all()
    )
    command_by_client = {
      row.client_order_id: row
      for row in source_commands
      if row.payload.get("command_kind") == "PLACE_ORDER"
    }
    device_ids: set[str] = set()
    for command in source_commands:
      if command.payload.get("command_kind") != "PLACE_ORDER":
        continue
      if command.delivery_status in {"QUEUED", "DELIVERED"}:
        command.delivery_status = "CANCELLED_KILL"
        command.last_error = "account_hard_kill_before_broker_confirmation"
      device_ids.add(str(command.device_id))

    if first_activation:
      for device_id in sorted(device_ids):
        client_order_id = f"emergency:{uuid.uuid4()}"
        expires_at = now + timedelta(minutes=10)
        db.add(
          TradeCommandOutbox(
            message_id=str(uuid.uuid4()),
            client_order_id=client_order_id,
            idempotency_key=hashlib.sha256(
              f"account-hard-kill-stop:{account_id}:{device_id}:{event_id}".encode(
                "utf-8"
              )
            ).hexdigest(),
            device_id=device_id,
            account_id=account_id,
            payload={
              "command_kind": "EMERGENCY_STOP",
              "client_order_id": client_order_id,
              "account_id": account_id,
              "reason": reason,
              "expires_at": expires_at.isoformat() + "Z",
            },
            delivery_status="QUEUED",
            expires_at=expires_at,
            attempts=0,
          )
        )

    cancellation_keys: set[str] = set()
    for pending in pending_orders:
      if not pending.broker_order_id:
        if pending.status in {"QUEUED", "PENDING"}:
          pending.status = "KILL_SWITCHED"
          pending.status_reason = "account hard kill before broker order id"
        continue
      source = command_by_client.get(pending.client_order_id)
      if source is None:
        continue
      cancel_key = f"{source.device_id}:{pending.broker_order_id}"
      if cancel_key in cancellation_keys:
        continue
      cancellation_keys.add(cancel_key)
      client_order_id = f"cancel:{uuid.uuid4()}"
      expires_at = now + timedelta(minutes=10)
      db.add(
        TradeCommandOutbox(
          message_id=str(uuid.uuid4()),
          client_order_id=client_order_id,
          idempotency_key=hashlib.sha256(
            f"account-hard-kill-cancel:{account_id}:{cancel_key}:{event_id}".encode(
              "utf-8"
            )
          ).hexdigest(),
          device_id=source.device_id,
          account_id=account_id,
          payload={
            "command_kind": "CANCEL_ORDER",
            "client_order_id": client_order_id,
            "account_id": account_id,
            "execution_mode": pending.execution_mode,
            "broker_order_id": str(pending.broker_order_id),
            "trace_id": event_id,
            "expires_at": expires_at.isoformat() + "Z",
          },
          delivery_status="QUEUED",
          expires_at=expires_at,
          attempts=0,
        )
      )
    await OperationalAlertService(db).raise_alert(
      severity="SEV1",
      source="API",
      code="HARD_KILL_ACTIVATED",
      account_id=account_id,
      business_id=event_id,
      message=f"账户 {account_id} 已触发 hard kill",
      details={
        "reason": reason,
        "cancel_commands": len(cancellation_keys),
        "device_commands": len(device_ids) if first_activation else 0,
      },
      commit=False,
    )

  async def mark_reconciled(
    self, account_id: str, *, ready: bool, reason: str = ""
  ) -> None:
    async with AsyncSessionLocal() as db:
      control = await db.get(AccountExecutionControl, account_id, with_for_update=True)
      if control is None:
        control = AccountExecutionControl(account_id=account_id)
        db.add(control)
      control.reconcile_status = "READY" if ready else "RECONCILE_REQUIRED"
      if not ready:
        if control.authorization_state != "KILLED":
          control.authorization_state = "PAUSED"
        control.paused_reason = reason[:2000]
        self._invalidate_controlled_window(control)
        control.state_version = int(control.state_version or 0) + 1
      await db.commit()


__all__ = [
  "ACCOUNT_EXECUTION_ALERT_CODES",
  "AccountExecutionControlIdempotencyError",
  "AccountExecutionSafetyService",
]
