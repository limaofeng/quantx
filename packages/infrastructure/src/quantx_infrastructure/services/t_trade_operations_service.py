"""Production readiness, rollout, and operational projections for positive T."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from quantx_domain.clock import to_naive_utc, utcnow
from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.orm import aliased

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRollout,
  AccountTradingRolloutEvent,
  AgentDevice,
  AgentReportInbox,
  OperationalAlert,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.services.operational_alert_service import (
  OperationalAlertService,
)
from quantx_infrastructure.services.trading_service import TradingService


class TTradeOperationsService:
  TERMINAL_BATCH_STATUSES = {
    "CLOSED",
    "ENTRY_EXPIRED",
    "ENTRY_REJECTED",
  }
  ACTIVE_BROKER_ORDER_STATUSES = {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}

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
      aliases = {
        "REPORTED": "SUBMITTED",
        "ACCEPTED": "SUBMITTED",
        "PARTIALLY_FILLED": "PARTIAL_FILLED",
        "CANCELED": "CANCELLED",
      }
      return aliases.get(text, text or "PENDING")

  @staticmethod
  def _invalidate_controlled_window(rollout: AccountTradingRollout) -> None:
    rollout.controlled_window_active = False
    rollout.controlled_window_snapshot_id = None
    rollout.controlled_window_snapshot_hash = None
    rollout.controlled_window_started_at = None
    rollout.controlled_window_started_by_user_id = None
    rollout.controlled_window_external_order_ids = []
    rollout.controlled_window_external_trade_ids = []

  @staticmethod
  def _append_rollout_event(
    db,
    *,
    account_id: str,
    event_type: str,
    actor_user_id: str | None = None,
    previous_stage: str | None = None,
    next_stage: str | None = None,
    snapshot_id: str | None = None,
    details: dict[str, Any] | None = None,
  ) -> None:
    db.add(
      AccountTradingRolloutEvent(
        event_id=str(uuid.uuid4()),
        account_id=account_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        previous_stage=previous_stage,
        next_stage=next_stage,
        snapshot_id=snapshot_id,
        details=dict(details or {}),
        created_at=utcnow(),
      )
    )

  async def record_event(
    self,
    account_id: str,
    event_type: str,
    *,
    actor_user_id: str | None = None,
    details: dict[str, Any] | None = None,
  ) -> None:
    async with AsyncSessionLocal() as db:
      self._append_rollout_event(
        db,
        account_id=account_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        details=details,
      )
      await db.commit()

  async def _latest_full_snapshot(
    self,
    db,
    *,
    account_id: str,
    snapshot_id: str,
  ) -> dict[str, Any]:
    reports = (
      await db.execute(
        select(AgentReportInbox)
        .where(
          AgentReportInbox.message_type == "delta_report",
          AgentReportInbox.protocol_version == "1.1",
        )
        .order_by(AgentReportInbox.received_at.desc())
        .limit(100)
      )
    ).scalars().all()
    for report in reports:
      payload = dict(report.payload or {})
      if (
        bool(payload.get("is_complete"))
        and str(payload.get("snapshot_id") or "") == snapshot_id
        and account_id
        in {
          str(item.get("account_id") or "")
          for item in payload.get("accounts") or []
        }
      ):
        return payload
    raise ValueError("最新权威账户快照原文不可用，请等待 Agent 再次完整上报")

  async def _external_snapshot_activity(
    self,
    db,
    *,
    account_id: str,
    payload: dict[str, Any],
  ) -> dict[str, list[dict[str, str]]]:
    pending = (
      await db.execute(
        select(PendingTradeOrder).where(PendingTradeOrder.account_id == account_id)
      )
    ).scalars().all()
    by_client = {str(item.client_order_id) for item in pending}
    by_broker = {
      str(item.broker_order_id) for item in pending if item.broker_order_id
    }
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
          "raw_status": self._normalized_broker_order_status(
            item.get("order_status", item.get("status"))
          ),
          "status_reason": str(item.get("effective_status_reason") or ""),
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

  @staticmethod
  def _fresh(heartbeat: RuntimeComponentHeartbeat | None) -> bool:
    if heartbeat is None:
      return False
    updated_at = to_naive_utc(heartbeat.updated_at)
    return bool(
      str(heartbeat.status).upper() == "READY"
      and updated_at >= utcnow() - timedelta(seconds=90)
    )

  @classmethod
  def _agent_candidate_rank(
    cls,
    heartbeat: RuntimeComponentHeartbeat | None,
  ) -> tuple[bool, datetime]:
    """Prefer a current READY session over stale registrations."""
    updated_at = (
      to_naive_utc(heartbeat.updated_at)
      if heartbeat is not None
      else datetime.min
    )
    return cls._fresh(heartbeat), updated_at

  async def _readiness_snapshot(self, db, account_id: str):
    """Load rollout, heartbeats, devices, and counters in one round trip."""
    anchor = select(literal(account_id).label("account_id")).subquery()
    engine_heartbeat = aliased(RuntimeComponentHeartbeat)
    agent_heartbeat = aliased(RuntimeComponentHeartbeat)
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
        (
          (OperationalAlert.account_id == account_id)
          | (OperationalAlert.account_id.is_(None))
        ),
      )
      .scalar_subquery()
    )
    unresolved_critical_alert_count = (
      select(func.count(OperationalAlert.id))
      .where(
        OperationalAlert.status != "RESOLVED",
        OperationalAlert.severity.in_(("SEV1", "SEV2")),
        OperationalAlert.code != "AGENT_REPORT_DEAD_LETTER",
        (
          (OperationalAlert.account_id == account_id)
          | (OperationalAlert.account_id.is_(None))
        ),
      )
      .scalar_subquery()
    )
    return (
      await db.execute(
        select(
          AccountTradingRollout,
          engine_heartbeat,
          AgentDevice,
          agent_heartbeat,
          queued_count,
          oldest_queued_at,
          dead_letter_count,
          unresolved_critical_alert_count,
        )
        .select_from(anchor)
        .outerjoin(
          AccountTradingRollout,
          AccountTradingRollout.account_id == anchor.c.account_id,
        )
        .outerjoin(
          engine_heartbeat,
          engine_heartbeat.component == "engine",
        )
        .outerjoin(
          AgentDevice,
          AgentDevice.revoked_at.is_(None),
        )
        .outerjoin(
          agent_heartbeat,
          agent_heartbeat.component
          == literal("qmt-agent:").concat(AgentDevice.id),
        )
      )
    ).all()

  async def readiness(self, account_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
      rows = await self._readiness_snapshot(db, account_id)
      if not rows:
        raise RuntimeError("实盘安全状态数据库查询未返回结果")
      (
        rollout,
        engine,
        _,
        _,
        queued_count,
        oldest_queued_at,
        dead_letter_count,
        unresolved_critical_alert_count,
      ) = rows[0]
      device = None
      agent = None
      for row in rows:
        candidate_device = row[2]
        if candidate_device is None:
          continue
        if (
          account_id in list(candidate_device.authorized_account_ids or [])
          and "live"
          in {
            str(value).lower()
            for value in list(candidate_device.capabilities or [])
          }
        ):
          candidate_agent = row[3]
          if device is None or self._agent_candidate_rank(
            candidate_agent
          ) > self._agent_candidate_rank(agent):
            device = candidate_device
            agent = candidate_agent
      agent_details = dict(agent.details or {}) if agent else {}
      capabilities = {
        str(value).lower()
        for value in list(agent_details.get("capabilities") or [])
      }
      reported_agent_mode = next(
        (
          value
          for value in ("live", "paper", "data-only")
          if value in capabilities
        ),
        "offline",
      )
      reported_protocol_version = str(
        agent_details.get("protocolVersion") or ""
      )
      account_reconciliation = dict(
        agent_details.get("accountReconciliation") or {}
      )
      reconciliation_summary = dict(
        account_reconciliation.get(account_id) or {}
      )
      current_snapshot_id = str(
        rollout.last_snapshot_id if rollout and rollout.last_snapshot_id else ""
      )
      activity_classification_current = bool(
        current_snapshot_id
        and str(reconciliation_summary.get("snapshotId") or "")
        == current_snapshot_id
      )
      external_order_count = max(
        0,
        int(reconciliation_summary.get("externalOrderCount") or 0),
      )
      external_trade_count = max(
        0,
        int(reconciliation_summary.get("externalTradeCount") or 0),
      )
      controlled_window_active = bool(
        rollout and getattr(rollout, "controlled_window_active", False)
      )
      controlled_window_snapshot_id = str(
        getattr(rollout, "controlled_window_snapshot_id", None)
        if rollout and getattr(rollout, "controlled_window_snapshot_id", None)
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
            reconciliation_summary.get(
              "newExternalOrderCount", external_order_count
            )
            or 0
          ),
        )
        new_external_trade_count = max(
          0,
          int(
            reconciliation_summary.get(
              "newExternalTradeCount", external_trade_count
            )
            or 0
          ),
        )
        working_external_order_count = max(
          0,
          int(reconciliation_summary.get("workingExternalOrderCount") or 0),
        )
      manual_coexistence = bool(
        reconciliation_summary.get("manualCoexistence")
        if activity_classification_current
        else (
          rollout
          and not rollout.enabled
          and str(rollout.stage).upper() in {"SHADOW", "PAUSED"}
        )
      )
      snapshot_at = (
        to_naive_utc(rollout.last_snapshot_at)
        if rollout and rollout.last_snapshot_at
        else None
      )
      backup_at = (
        to_naive_utc(rollout.last_backup_at)
        if rollout and rollout.last_backup_at
        else None
      )
      now = utcnow()
      agent_heartbeat_fresh = bool(
        agent
        and to_naive_utc(agent.updated_at)
        >= now - timedelta(seconds=90)
      )
      live_agent_ready = bool(
        agent_heartbeat_fresh
        and str(agent.status).upper() == "READY"
      )
      if device is None:
        live_agent_blocked_reason = (
          "没有绑定该账户且具备 live 能力的已登记 QMT Agent"
        )
      elif agent is None:
        live_agent_blocked_reason = "对应账户的 live Agent 尚未上报心跳"
      elif not agent_heartbeat_fresh:
        live_agent_blocked_reason = (
          "对应账户的 live Agent 已离线或心跳超过 90 秒"
        )
      else:
        live_agent_blocked_reason = (
          "对应账户的 live Agent 当前未就绪"
        )
      agent_mode = reported_agent_mode if agent_heartbeat_fresh else "offline"
      protocol_version = (
        reported_protocol_version if agent_heartbeat_fresh else ""
      )
      agent_status = (
        str(agent.status) if agent_heartbeat_fresh and agent else "OFFLINE"
      )
      snapshot_age = (
        max(0.0, (now - snapshot_at).total_seconds())
        if snapshot_at
        else None
      )
      backup_age = (
        max(0.0, (now - backup_at).total_seconds())
        if backup_at
        else None
      )
      dead_letter_count = int(dead_letter_count or 0)
      unresolved_critical_alert_count = int(
        unresolved_critical_alert_count or 0
      )
      queue_delay = (
        max(
          0.0,
          (
            now - to_naive_utc(oldest_queued_at)
          ).total_seconds(),
        )
        if oldest_queued_at
        else 0.0
      )
      checks = [
        (
          "SERVER_REAL_TRADING_ENABLED",
          bool(settings.enable_real_trading),
          "服务端 ENABLE_REAL_TRADING 未启用",
          "AUTOMATION",
        ),
        (
          "T_TRADE_LIVE_ENABLED",
          bool(settings.t_trade_live_enabled),
          "服务端 T_TRADE_LIVE_ENABLED 未启用",
          "AUTOMATION",
        ),
        (
          "ACCOUNT_ALLOWLISTED",
          account_id in set(settings.real_trading_account_allowlist or []),
          "账户不在 REAL_TRADING_ACCOUNT_ALLOWLIST",
          "AUTOMATION",
        ),
        (
          "ENGINE_READY",
          self._fresh(engine),
          "Engine 心跳缺失或已过期",
          "PREPARATION",
        ),
        (
          "LIVE_AGENT_READY",
          live_agent_ready,
          live_agent_blocked_reason,
          "PREPARATION",
        ),
        (
          "AGENT_MODE_LIVE",
          reported_agent_mode == "live",
          "QMT Agent 尚未明确切换到 live 模式",
          "PREPARATION",
        ),
        (
          "PROTOCOL_1_1",
          reported_protocol_version == "1.1",
          "账户观察与真实下单均要求 Agent 协议 1.1",
          "PREPARATION",
        ),
        (
          "ROLLOUT_CONFIGURED",
          rollout is not None,
          "账户尚未创建实盘灰度配置",
          "PREPARATION",
        ),
        (
          "SNAPSHOT_RECONCILED",
          bool(rollout and rollout.reconcile_status == "READY"),
          "资金、持仓、委托和成交快照尚未完成对账",
          "PREPARATION",
        ),
        (
          "SNAPSHOT_FRESH",
          snapshot_age is not None and snapshot_age <= 90,
          "账户完整快照缺失或已超过 90 秒",
          "PREPARATION",
        ),
        (
          "SNAPSHOT_ACTIVITY_CLASSIFIED",
          activity_classification_current,
          "最新完整快照尚未完成手工/外部交易分类",
          "PREPARATION",
        ),
        (
          "RECENT_BACKUP",
          backup_age is not None and backup_age < 24 * 60 * 60,
          "最近成功备份缺失或已超过 24 小时",
          "AUTOMATION",
        ),
        (
          "NO_CRITICAL_ALERTS",
          unresolved_critical_alert_count == 0,
          "存在尚未解决的 Sev-1/Sev-2 运行告警",
          "PREPARATION",
        ),
        (
          "NO_DEAD_LETTERS",
          dead_letter_count == 0,
          "存在尚未被后续权威快照取代的 Agent 报告死信",
          "PREPARATION",
        ),
        (
          "CONTROLLED_WINDOW_ACTIVE",
          controlled_window_active,
          "尚未基于最新完整快照建立受控交易窗口",
          "AUTOMATION",
        ),
        (
          "NO_EXTERNAL_BROKER_ACTIVITY",
          new_external_order_count == 0
          and new_external_trade_count == 0
          and working_external_order_count == 0,
          "受控窗口后出现新的 QMT 手工/外部交易或仍有活动委托",
          "AUTOMATION",
        ),
        (
          "KILL_SWITCH_CLEAR",
          bool(rollout and not rollout.kill_switch),
          "账户 kill switch 已触发",
          "PREPARATION",
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
      preparation_blocked = [
        item["message"]
        for item in items
        if item["scope"] == "PREPARATION" and not item["passed"]
      ]
      blocked = [item["message"] for item in items if not item["passed"]]
      preparation_ready = not preparation_blocked
      automation_ready = not blocked
      status = (
        "HARD_KILL"
        if rollout and rollout.kill_switch
        else (
          "READY"
          if automation_ready
          else "PREPARING"
          if preparation_ready
          else "BLOCKED"
        )
      )
      return {
        "account_id": account_id,
        # Backward-compatible alias: ``ready`` still means order automation.
        "ready": automation_ready,
        "status": status,
        "preparation_ready": preparation_ready,
        "automation_ready": automation_ready,
        "stage": str(rollout.stage if rollout else "SHADOW"),
        "engine_status": str(engine.status if engine else "OFFLINE"),
        "agent_status": agent_status,
        "agent_device_id": str(device.id) if device else None,
        "agent_mode": agent_mode,
        "protocol_version": protocol_version,
        "reconcile_status": str(
          rollout.reconcile_status if rollout else "UNKNOWN"
        ),
        "kill_switch": bool(rollout and rollout.kill_switch),
        "policy_version": int(rollout.policy_version if rollout else 1),
        "can_approve": bool(
          rollout
          and rollout.enabled
          and rollout.stage in {"CANARY", "LIVE"}
          and automation_ready
        ),
        "can_activate_live": automation_ready,
        "blocked_reasons": blocked,
        "preparation_blocked_reasons": preparation_blocked,
        "checks": items,
        "snapshot_id": rollout.last_snapshot_id if rollout else None,
        "snapshot_hash": rollout.last_snapshot_hash if rollout else None,
        "snapshot_at": snapshot_at,
        "reconciliation_age_seconds": snapshot_age,
        "queued_command_count": int(queued_count or 0),
        "queue_delay_seconds": queue_delay,
        "dead_letter_count": dead_letter_count,
        "unresolved_critical_alert_count": unresolved_critical_alert_count,
        "manual_coexistence": manual_coexistence,
        "external_order_count": external_order_count,
        "external_trade_count": external_trade_count,
        "controlled_window_active": controlled_window_active,
        "controlled_window_snapshot_id": (
          controlled_window_snapshot_id or None
        ),
        "controlled_window_started_at": (
          to_naive_utc(getattr(rollout, "controlled_window_started_at"))
          if rollout and getattr(rollout, "controlled_window_started_at", None)
          else None
        ),
        "new_external_order_count": new_external_order_count,
        "new_external_trade_count": new_external_trade_count,
        "working_external_order_count": working_external_order_count,
        "journal_integrity": str(
          agent_details.get("journalIntegrity") or "unknown"
        ),
        "journal_size_bytes": int(
          agent_details.get("journalSizeBytes") or 0
        ),
        "journal_pending_reports": int(
          agent_details.get("journalPendingReports") or 0
        ),
        "last_backup_at": backup_at,
        "checked_at": now,
      }

  async def ensure_rollout(self, account_id: str) -> AccountTradingRollout:
    async with AsyncSessionLocal() as db:
      rollout = await db.get(AccountTradingRollout, account_id)
      if rollout is None:
        rollout = AccountTradingRollout(account_id=account_id)
        db.add(rollout)
        await db.commit()
        await db.refresh(rollout)
      return rollout

  async def mark_reconciled(
    self,
    account_id: str,
    *,
    ready: bool,
    reason: str = "",
  ) -> None:
    async with AsyncSessionLocal() as db:
      rollout = await db.get(
        AccountTradingRollout,
        account_id,
        with_for_update=True,
      )
      if rollout is None:
        rollout = AccountTradingRollout(account_id=account_id)
        db.add(rollout)
      rollout.reconcile_status = "READY" if ready else "RECONCILE_REQUIRED"
      rollout.paused_reason = None if ready else reason[:2000]
      await db.commit()

  async def begin_controlled_window(
    self,
    account_id: str,
    *,
    user_id: str,
    snapshot_id: str,
  ) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    readiness = await self.readiness(account_id)
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
      raise ValueError("完整快照已经更新，请刷新页面后重新确认受控窗口")

    async with AsyncSessionLocal() as db:
      rollout = await db.get(
        AccountTradingRollout,
        account_id,
        with_for_update=True,
      )
      if rollout is None:
        raise ValueError("账户灰度配置不存在")
      if str(rollout.stage).upper() not in {"SHADOW", "PAUSED"}:
        raise ValueError("只能在 SHADOW 或 PAUSED 阶段建立受控交易窗口")
      if rollout.enabled:
        raise ValueError("自动执行已启用，必须先暂停后再建立受控交易窗口")
      if rollout.kill_switch:
        raise ValueError("账户 kill switch 已触发")
      if str(rollout.reconcile_status).upper() != "READY":
        raise ValueError("资金、持仓、委托和成交快照尚未完成对账")
      if str(rollout.last_snapshot_id or "") != snapshot_id:
        raise ValueError("完整快照已经更新，请刷新页面后重新确认受控窗口")
      payload = await self._latest_full_snapshot(
        db,
        account_id=account_id,
        snapshot_id=snapshot_id,
      )
      activity = await self._external_snapshot_activity(
        db,
        account_id=account_id,
        payload=payload,
      )
      active_orders = [
        item
        for item in activity["orders"]
        if item["status"] in self.ACTIVE_BROKER_ORDER_STATUSES
      ]
      if active_orders:
        raise ValueError(
          f"当前仍有 {len(active_orders)} 笔 QMT 手工委托可能成交，请先在 MiniQMT 撤单"
        )
      previous_stage = str(rollout.stage)
      rollout.controlled_window_active = True
      rollout.controlled_window_snapshot_id = snapshot_id
      rollout.controlled_window_snapshot_hash = str(
        readiness.get("snapshot_hash") or ""
      ) or None
      rollout.controlled_window_started_at = utcnow()
      rollout.controlled_window_started_by_user_id = user_id
      rollout.controlled_window_external_order_ids = sorted(
        {item["business_id"] for item in activity["orders"]}
      )
      rollout.controlled_window_external_trade_ids = sorted(
        {item["business_id"] for item in activity["trades"]}
      )
      if previous_stage == "PAUSED" and not rollout.kill_switch:
        rollout.stage = "SHADOW"
        rollout.enabled = False
        rollout.paused_reason = None
      self._append_rollout_event(
        db,
        account_id=account_id,
        event_type="CONTROLLED_WINDOW_STARTED",
        actor_user_id=user_id,
        previous_stage=previous_stage,
        next_stage=str(rollout.stage),
        snapshot_id=snapshot_id,
        details={
          "acknowledgedExternalOrderCount": len(activity["orders"]),
          "acknowledgedExternalTradeCount": len(activity["trades"]),
        },
      )
      await db.commit()
    return await self.readiness(account_id)

  async def activate_rollout(
    self,
    account_id: str,
    *,
    user_id: str,
    acknowledged_policy_version: int,
    target_stage: str = "CANARY",
    confirmation: str = "",
  ) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    target = str(target_stage or "CANARY").strip().upper()
    if target not in {"CANARY", "LIVE"}:
      raise ValueError("目标灰度阶段必须是 CANARY 或 LIVE")
    readiness = await self.readiness(account_id)
    if not readiness["automation_ready"]:
      raise ValueError("；".join(readiness["blocked_reasons"]))
    if target == "LIVE":
      if not settings.is_development:
        raise ValueError("生产环境禁止从 SHADOW 直接进入 LIVE")
      if confirmation != f"LIVE:{account_id}":
        raise ValueError(f"正式 LIVE 需要精确确认 LIVE:{account_id}")
      if not readiness.get("controlled_window_active"):
        raise ValueError("正式 LIVE 需要先建立受控交易窗口")
    async with AsyncSessionLocal() as db:
      rollout = await db.get(
        AccountTradingRollout,
        account_id,
        with_for_update=True,
      )
      if rollout is None:
        raise ValueError("账户灰度配置不存在")
      if rollout.kill_switch:
        raise ValueError("账户 kill switch 已触发")
      if str(rollout.reconcile_status).upper() != "READY":
        raise ValueError("资金、持仓、委托和成交快照尚未完成对账")
      if not rollout.controlled_window_active:
        raise ValueError("实盘启用需要先建立受控交易窗口")
      if (
        str(readiness.get("snapshot_id") or "")
        and str(rollout.last_snapshot_id or "")
        != str(readiness.get("snapshot_id") or "")
      ):
        raise ValueError("完整快照已经更新，请刷新后重新检查实盘门禁")
      if acknowledged_policy_version != rollout.policy_version:
        raise ValueError("确认的自动退出策略版本已过期")
      previous_stage = str(rollout.stage)
      if target == "LIVE" and previous_stage.upper() != "SHADOW":
        raise ValueError("开发环境正式 LIVE 只允许从 SHADOW 直接启用")
      next_stage = (
        "LIVE"
        if target == "CANARY" and previous_stage.upper() == "LIVE"
        else target
      )
      rollout.stage = next_stage
      rollout.enabled = True
      rollout.kill_switch = False
      rollout.acknowledged_policy_version = acknowledged_policy_version
      rollout.activated_by_user_id = user_id
      rollout.activated_at = utcnow()
      rollout.paused_reason = None
      self._append_rollout_event(
        db,
        account_id=account_id,
        event_type=f"{next_stage}_ACTIVATED",
        actor_user_id=user_id,
        previous_stage=previous_stage,
        next_stage=next_stage,
        snapshot_id=str(rollout.controlled_window_snapshot_id or "") or None,
        details={"policyVersion": acknowledged_policy_version},
      )
      await db.commit()
    return await self.readiness(account_id)

  async def activate_canary(
    self,
    account_id: str,
    *,
    user_id: str,
    acknowledged_policy_version: int,
  ) -> dict[str, Any]:
    return await self.activate_rollout(
      account_id,
      user_id=user_id,
      acknowledged_policy_version=acknowledged_policy_version,
      target_stage="CANARY",
    )

  async def pause(
    self,
    account_id: str,
    reason: str,
    *,
    user_id: str | None = None,
  ) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    async with AsyncSessionLocal() as db:
      rollout = await db.get(
        AccountTradingRollout,
        account_id,
        with_for_update=True,
      )
      previous_stage = str(rollout.stage)
      rollout.enabled = False
      rollout.stage = "PAUSED"
      rollout.paused_reason = reason[:2000] or "manual pause"
      self._invalidate_controlled_window(rollout)
      self._append_rollout_event(
        db,
        account_id=account_id,
        event_type="ENTRIES_PAUSED",
        actor_user_id=user_id,
        previous_stage=previous_stage,
        next_stage="PAUSED",
        details={"reason": rollout.paused_reason},
      )
      await db.commit()
    return await self.readiness(account_id)

  async def kill(
    self,
    account_id: str,
    reason: str,
    *,
    user_id: str | None = None,
  ) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    async with AsyncSessionLocal() as db:
      rollout = await db.get(
        AccountTradingRollout,
        account_id,
        with_for_update=True,
      )
      was_killed = bool(rollout.kill_switch)
      previous_stage = str(rollout.stage)
      kill_event_id = str(uuid.uuid4())
      now = utcnow()
      rollout.enabled = False
      rollout.kill_switch = True
      rollout.stage = "KILL_SWITCHED"
      rollout.paused_reason = reason[:2000] or "manual kill switch"
      self._invalidate_controlled_window(rollout)
      self._append_rollout_event(
        db,
        account_id=account_id,
        event_type="KILL_SWITCHED",
        actor_user_id=user_id,
        previous_stage=previous_stage,
        next_stage="KILL_SWITCHED",
        details={"reason": rollout.paused_reason},
      )
      rows = (
        await db.execute(
          select(TTradeBatch).where(
            TTradeBatch.account_id == account_id,
            TTradeBatch.status.notin_(self.TERMINAL_BATCH_STATUSES),
          )
        )
      ).scalars().all()
      for batch in rows:
        batch.status = "KILL_SWITCHED"
        batch.exception_reason = rollout.paused_reason
      pending_orders = (
        await db.execute(
          select(PendingTradeOrder).where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.status.in_(
              ("QUEUED", "PENDING", "SUBMITTED", "PARTIAL_FILLED")
            ),
          )
        )
      ).scalars().all()
      source_commands = (
        await db.execute(
          select(TradeCommandOutbox).where(
            TradeCommandOutbox.account_id == account_id
          )
        )
      ).scalars().all()
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
          command.last_error = "hard_kill_before_broker_confirmation"
        device_ids.add(str(command.device_id))

      if not was_killed:
        for device_id in sorted(device_ids):
          client_order_id = f"emergency:{uuid.uuid4()}"
          expires_at = now + timedelta(minutes=10)
          db.add(
            TradeCommandOutbox(
              message_id=str(uuid.uuid4()),
              client_order_id=client_order_id,
              idempotency_key=hashlib.sha256(
                (
                  f"hard-kill-stop:{account_id}:{device_id}:{kill_event_id}"
                ).encode("utf-8")
              ).hexdigest(),
              device_id=device_id,
              account_id=account_id,
              payload={
                "command_kind": "EMERGENCY_STOP",
                "client_order_id": client_order_id,
                "account_id": account_id,
                "reason": rollout.paused_reason,
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
            pending.status_reason = "hard kill before broker order id"
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
              (
                f"hard-kill-cancel:{account_id}:{cancel_key}:{kill_event_id}"
              ).encode("utf-8")
            ).hexdigest(),
            device_id=source.device_id,
            account_id=account_id,
            payload={
              "command_kind": "CANCEL_ORDER",
              "client_order_id": client_order_id,
              "account_id": account_id,
              "execution_mode": pending.execution_mode,
              "broker_order_id": str(pending.broker_order_id),
              "trace_id": kill_event_id,
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
        business_id=kill_event_id,
        message=f"账户 {account_id} 已触发 hard kill",
        details={
          "reason": rollout.paused_reason,
          "cancel_commands": len(cancellation_keys),
          "device_commands": 0 if was_killed else len(device_ids),
        },
        commit=False,
      )
      await db.commit()
    return await self.readiness(account_id)

  async def list_batches(
    self,
    account_id: str,
    *,
    status_group: str | None = None,
    offset: int = 0,
    limit: int = 100,
  ) -> list[dict[str, Any]]:
    groups = {
      "OPEN": {"ENTRY_PARTIAL", "OPEN"},
      "EXITING": {"EXIT_TRIGGERED", "EXIT_SUBMITTED", "EXIT_PARTIAL"},
      "CLOSED": {"CLOSED"},
      "EXCEPTION": {
        "ENTRY_EXPIRED",
        "ENTRY_REJECTED",
        "EXIT_REJECTED",
        "RECONCILE_REQUIRED",
        "KILL_SWITCHED",
      },
    }
    async with AsyncSessionLocal() as db:
      query = select(TTradeBatch).where(TTradeBatch.account_id == account_id)
      selected = groups.get(str(status_group or "").upper())
      if selected:
        query = query.where(TTradeBatch.status.in_(selected))
      rows = (
        await db.execute(
          query.order_by(TTradeBatch.updated_at.desc())
          .offset(max(0, offset))
          .limit(min(max(1, limit), 200))
        )
      ).scalars().all()
      return [self._batch_row(row) for row in rows]

  async def list_batches_page(
    self,
    account_id: str,
    *,
    status_group: str | None = None,
    cursor_updated_at: datetime | None = None,
    cursor_id: str | None = None,
    first: int = 30,
  ) -> tuple[list[dict[str, Any]], bool]:
    groups = {
      "OPEN": {"ENTRY_PARTIAL", "OPEN"},
      "EXITING": {"EXIT_TRIGGERED", "EXIT_SUBMITTED", "EXIT_PARTIAL"},
      "CLOSED": {"CLOSED"},
      "EXCEPTION": {
        "ENTRY_EXPIRED",
        "ENTRY_REJECTED",
        "EXIT_REJECTED",
        "RECONCILE_REQUIRED",
        "KILL_SWITCHED",
      },
    }
    safe_first = max(1, min(int(first or 30), 100))
    async with AsyncSessionLocal() as db:
      query = select(TTradeBatch).where(TTradeBatch.account_id == account_id)
      selected = groups.get(str(status_group or "").upper())
      if selected:
        query = query.where(TTradeBatch.status.in_(selected))
      if cursor_updated_at is not None and cursor_id:
        query = query.where(
          or_(
            TTradeBatch.updated_at < cursor_updated_at,
            and_(
              TTradeBatch.updated_at == cursor_updated_at,
              TTradeBatch.batch_id < cursor_id,
            ),
          )
        )
      rows = list(
        (
          await db.execute(
            query.order_by(
              TTradeBatch.updated_at.desc(),
              TTradeBatch.batch_id.desc(),
            ).limit(safe_first + 1)
          )
        ).scalars().all()
      )
      return (
        [self._batch_row(row) for row in rows[:safe_first]],
        len(rows) > safe_first,
      )

  @staticmethod
  def _batch_row(row: TTradeBatch) -> dict[str, Any]:
    return {
      "batch_id": row.batch_id,
      "account_id": row.account_id,
      "stock_code": row.instrument_code,
      "strategy_run_id": row.strategy_run_id,
      "status": row.status,
      "entry_intent_id": row.entry_intent_id,
      "exit_intent_id": row.exit_intent_id,
      "entry_client_order_id": row.entry_client_order_id,
      "exit_client_order_id": row.exit_client_order_id,
      "entry_broker_order_id": row.entry_broker_order_id,
      "exit_broker_order_id": row.exit_broker_order_id,
      "target_volume": row.target_volume,
      "entry_filled_volume": row.entry_filled_volume,
      "entry_avg_price": row.entry_avg_price,
      "exit_filled_volume": row.exit_filled_volume,
      "exit_avg_price": row.exit_avg_price,
      "active_volume": max(
        0,
        int(row.entry_filled_volume or 0) - int(row.exit_filled_volume or 0),
      ),
      "last_price": row.last_price,
      "last_net_profit_pct": row.last_net_profit_pct,
      "peak_net_profit_pct": row.peak_net_profit_pct,
      "trailing_floor_pct": row.trailing_floor_pct,
      "exit_reason": row.exit_reason,
      "exception_reason": row.exception_reason,
      "policy_version": row.policy_version,
      "version": row.version,
      "created_at": row.created_at,
      "updated_at": row.updated_at,
    }

  async def list_events(
    self,
    account_id: str,
    *,
    batch_id: str | None = None,
    limit: int = 100,
  ) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
      query = (
        select(StrategyRuntimeEvent)
        .join(
          PendingTradeOrder,
          PendingTradeOrder.client_order_id
          == StrategyRuntimeEvent.client_order_id,
        )
        .where(PendingTradeOrder.account_id == account_id)
      )
      if batch_id:
        query = query.where(PendingTradeOrder.batch_id == batch_id)
      rows = (
        await db.execute(
          query.order_by(StrategyRuntimeEvent.created_at.desc()).limit(
            min(max(1, limit), 200)
          )
        )
      ).scalars().all()
      return [self._event_row(row) for row in rows]

  async def list_events_page(
    self,
    account_id: str,
    *,
    batch_id: str | None = None,
    cursor_created_at: datetime | None = None,
    cursor_id: str | None = None,
    first: int = 30,
  ) -> tuple[list[dict[str, Any]], bool]:
    safe_first = max(1, min(int(first or 30), 100))
    async with AsyncSessionLocal() as db:
      query = (
        select(StrategyRuntimeEvent)
        .join(
          PendingTradeOrder,
          PendingTradeOrder.client_order_id
          == StrategyRuntimeEvent.client_order_id,
        )
        .where(PendingTradeOrder.account_id == account_id)
      )
      if batch_id:
        query = query.where(PendingTradeOrder.batch_id == batch_id)
      if cursor_created_at is not None and cursor_id:
        query = query.where(
          or_(
            StrategyRuntimeEvent.created_at < cursor_created_at,
            and_(
              StrategyRuntimeEvent.created_at == cursor_created_at,
              StrategyRuntimeEvent.event_id < cursor_id,
            ),
          )
        )
      rows = list(
        (
          await db.execute(
            query.order_by(
              StrategyRuntimeEvent.created_at.desc(),
              StrategyRuntimeEvent.event_id.desc(),
            ).limit(safe_first + 1)
          )
        ).scalars().all()
      )
      return (
        [self._event_row(row) for row in rows[:safe_first]],
        len(rows) > safe_first,
      )

  @staticmethod
  def _event_row(row: StrategyRuntimeEvent) -> dict[str, Any]:
    return {
      "event_id": row.event_id,
      "batch_id": str(
        (row.payload or {}).get("metadata", {}).get("t_batch_id") or ""
      ),
      "event_type": row.event_type,
      "status": row.application_status,
      "client_order_id": row.client_order_id,
      "broker_order_id": row.broker_order_id,
      "payload": row.payload,
      "created_at": row.created_at,
      "applied_at": row.applied_at,
      "error": row.application_error,
    }

  async def cancel_order(
    self,
    account_id: str,
    client_order_id: str,
  ) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
      pending = await db.get(PendingTradeOrder, client_order_id)
      if pending is None or pending.account_id != account_id:
        raise ValueError("找不到对应账户的做 T 委托")
      if pending.status.upper() in {
        "FILLED",
        "SUCCEEDED",
        "CANCELLED",
        "CANCELED",
        "REJECTED",
      }:
        raise ValueError("当前委托状态不可撤")
      mode = pending.execution_mode
    return await TradingService(
      account_id=account_id,
      execution_mode=mode,
    ).cancel_pending_order(client_order_id=client_order_id)
