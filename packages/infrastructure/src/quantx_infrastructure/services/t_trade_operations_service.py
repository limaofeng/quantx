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

  @staticmethod
  def _fresh(heartbeat: RuntimeComponentHeartbeat | None) -> bool:
    if heartbeat is None:
      return False
    updated_at = to_naive_utc(heartbeat.updated_at)
    return bool(
      str(heartbeat.status).upper() == "READY"
      and updated_at >= utcnow() - timedelta(seconds=90)
    )

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
      select(func.count(AgentReportInbox.message_id))
      .where(AgentReportInbox.processing_status == "FAILED")
      .scalar_subquery()
    )
    unresolved_critical_alert_count = (
      select(func.count(OperationalAlert.id))
      .where(
        OperationalAlert.status != "RESOLVED",
        OperationalAlert.severity.in_(("SEV1", "SEV2")),
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
          device = candidate_device
          agent = row[3]
          break
      agent_details = dict(agent.details or {}) if agent else {}
      capabilities = {
        str(value).lower()
        for value in list(agent_details.get("capabilities") or [])
      }
      agent_mode = next(
        (
          value
          for value in ("live", "paper", "data-only")
          if value in capabilities
        ),
        "offline",
      )
      protocol_version = str(
        agent_details.get("protocolVersion") or ""
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
        ),
        (
          "T_TRADE_LIVE_ENABLED",
          bool(settings.t_trade_live_enabled),
          "服务端 T_TRADE_LIVE_ENABLED 未启用",
        ),
        (
          "ACCOUNT_ALLOWLISTED",
          account_id in set(settings.real_trading_account_allowlist or []),
          "账户不在 REAL_TRADING_ACCOUNT_ALLOWLIST",
        ),
        (
          "ENGINE_READY",
          self._fresh(engine),
          "Engine 心跳缺失或已过期",
        ),
        (
          "LIVE_AGENT_READY",
          device is not None and self._fresh(agent),
          "没有对应账户且处于 READY 的 live Agent",
        ),
        (
          "AGENT_MODE_LIVE",
          agent_mode == "live",
          "QMT Agent 尚未明确切换到 live 模式",
        ),
        (
          "PROTOCOL_1_1",
          protocol_version == "1.1",
          "真实下单仅允许 Agent 协议 1.1",
        ),
        (
          "ROLLOUT_CONFIGURED",
          rollout is not None,
          "账户尚未创建实盘灰度配置",
        ),
        (
          "SNAPSHOT_RECONCILED",
          bool(rollout and rollout.reconcile_status == "READY"),
          "资金、持仓、委托和成交快照尚未完成对账",
        ),
        (
          "SNAPSHOT_FRESH",
          snapshot_age is not None and snapshot_age <= 90,
          "账户完整快照缺失或已超过 90 秒",
        ),
        (
          "RECENT_BACKUP",
          backup_age is not None and backup_age < 24 * 60 * 60,
          "最近成功备份缺失或已超过 24 小时",
        ),
        (
          "NO_CRITICAL_ALERTS",
          unresolved_critical_alert_count == 0,
          "存在尚未解决的 Sev-1/Sev-2 运行告警",
        ),
        (
          "NO_DEAD_LETTERS",
          dead_letter_count == 0,
          "Agent 报告死信尚未清零",
        ),
        (
          "KILL_SWITCH_CLEAR",
          bool(rollout and not rollout.kill_switch),
          "账户 kill switch 已触发",
        ),
      ]
      items = [
        {"code": code, "passed": passed, "message": "" if passed else message}
        for code, passed, message in checks
      ]
      blocked = [item["message"] for item in items if not item["passed"]]
      return {
        "account_id": account_id,
        "ready": not blocked,
        "stage": str(rollout.stage if rollout else "SHADOW"),
        "engine_status": str(engine.status if engine else "OFFLINE"),
        "agent_status": str(agent.status if agent else "OFFLINE"),
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
          and not blocked
        ),
        "can_activate_live": not blocked,
        "blocked_reasons": blocked,
        "checks": items,
        "snapshot_id": rollout.last_snapshot_id if rollout else None,
        "snapshot_hash": rollout.last_snapshot_hash if rollout else None,
        "snapshot_at": snapshot_at,
        "reconciliation_age_seconds": snapshot_age,
        "queued_command_count": int(queued_count or 0),
        "queue_delay_seconds": queue_delay,
        "dead_letter_count": dead_letter_count,
        "unresolved_critical_alert_count": unresolved_critical_alert_count,
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
      rollout = await db.get(AccountTradingRollout, account_id)
      if rollout is None:
        rollout = AccountTradingRollout(account_id=account_id)
        db.add(rollout)
      rollout.reconcile_status = "READY" if ready else "RECONCILE_REQUIRED"
      rollout.paused_reason = None if ready else reason[:2000]
      await db.commit()

  async def activate_canary(
    self,
    account_id: str,
    *,
    user_id: str,
    acknowledged_policy_version: int,
  ) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    readiness = await self.readiness(account_id)
    if not readiness["ready"]:
      raise ValueError("；".join(readiness["blocked_reasons"]))
    async with AsyncSessionLocal() as db:
      rollout = await db.get(AccountTradingRollout, account_id)
      if rollout is None:
        raise ValueError("账户灰度配置不存在")
      if acknowledged_policy_version != rollout.policy_version:
        raise ValueError("确认的自动退出策略版本已过期")
      rollout.stage = "LIVE" if rollout.stage == "LIVE" else "CANARY"
      rollout.enabled = True
      rollout.kill_switch = False
      rollout.acknowledged_policy_version = acknowledged_policy_version
      rollout.activated_by_user_id = user_id
      rollout.activated_at = utcnow()
      rollout.paused_reason = None
      await db.commit()
    return await self.readiness(account_id)

  async def pause(self, account_id: str, reason: str) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    async with AsyncSessionLocal() as db:
      rollout = await db.get(AccountTradingRollout, account_id)
      rollout.enabled = False
      rollout.stage = "PAUSED"
      rollout.paused_reason = reason[:2000] or "manual pause"
      await db.commit()
    return await self.readiness(account_id)

  async def kill(self, account_id: str, reason: str) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    async with AsyncSessionLocal() as db:
      rollout = await db.get(AccountTradingRollout, account_id)
      was_killed = bool(rollout.kill_switch)
      kill_event_id = str(uuid.uuid4())
      now = utcnow()
      rollout.enabled = False
      rollout.kill_switch = True
      rollout.stage = "KILL_SWITCHED"
      rollout.paused_reason = reason[:2000] or "manual kill switch"
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
