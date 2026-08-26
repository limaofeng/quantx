"""Production readiness, rollout, and operational projections for positive T."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from quantx_domain.clock import utcnow
from sqlalchemy import and_, or_, select

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
  TTradeRollout,
  TTradeRolloutEvent,
)
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)
from quantx_infrastructure.services.trading_service import TradingService


class TTradeOperationIdempotencyError(ValueError):
  code = "IDEMPOTENCY_KEY_REUSED"


class TTradeOperationsService:
  TERMINAL_BATCH_STATUSES = {
    "CLOSED",
    "ENTRY_EXPIRED",
    "ENTRY_REJECTED",
  }
  ACTIVE_BROKER_ORDER_STATUSES = {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}

  @staticmethod
  def _assert_operation_event_binding(
    event: TTradeRolloutEvent,
    *,
    account_id: str,
    operation_id: str,
    event_types: set[str],
    actor_user_id: str | None,
    snapshot_id: str | None = None,
    target_stage: str | None = None,
    policy_version: int | None = None,
    confirmation: str | None = None,
    reason: str | None = None,
  ) -> None:
    """Reject reuse of an operation marker with a different binding.

    This helper is deliberately called both before and after taking the
    rollout row lock.  The first lookup makes a completed operation stable
    even when mutable readiness gates have changed; the second lookup closes
    the race with a concurrent first submission.
    """
    details = dict(event.details or {})
    if (
      str(event.account_id) != account_id
      or str(event.event_type) not in event_types
      or str(details.get("operationId") or "") != operation_id
      or str(event.actor_user_id or "") != str(actor_user_id or "")
      or (snapshot_id is not None and str(event.snapshot_id or "") != str(snapshot_id))
      or (
        target_stage is not None
        and str(details.get("targetStage") or "") != str(target_stage)
      )
      or (
        policy_version is not None
        and str(details.get("policyVersion") or "") != str(policy_version)
      )
      or (
        confirmation is not None
        and str(details.get("confirmation") or "") != str(confirmation)
      )
      or (reason is not None and str(details.get("reason") or "") != str(reason))
    ):
      raise TTradeOperationIdempotencyError("做 T 控制幂等标识已绑定其他操作")

  async def operation_marker_exists(
    self,
    account_id: str,
    operation_id: str,
    *,
    event_types: set[str],
    actor_user_id: str | None,
    snapshot_id: str | None = None,
    target_stage: str | None = None,
    policy_version: int | None = None,
    confirmation: str | None = None,
    reason: str | None = None,
  ) -> bool:
    """Check a committed rollout marker without consulting mutable readiness.

    Mutation resolvers use this after a post-commit readiness/readback error.
    The marker is the durable operation result; a missing marker is the only
    case that remains an ordinary validation failure.
    """
    async with AsyncSessionLocal() as db:
      event = await db.get(TTradeRolloutEvent, operation_id)
      if event is None:
        await db.rollback()
        return False
      self._assert_operation_event_binding(
        event,
        account_id=account_id,
        operation_id=operation_id,
        event_types=event_types,
        actor_user_id=actor_user_id,
        snapshot_id=snapshot_id,
        target_stage=target_stage,
        policy_version=policy_version,
        confirmation=confirmation,
        reason=reason,
      )
      await db.rollback()
      return True

  @staticmethod
  def _append_rollout_event(
    db,
    *,
    event_id: str | None = None,
    account_id: str,
    event_type: str,
    actor_user_id: str | None = None,
    previous_stage: str | None = None,
    next_stage: str | None = None,
    snapshot_id: str | None = None,
    details: dict[str, Any] | None = None,
  ) -> None:
    db.add(
      TTradeRolloutEvent(
        event_id=event_id or str(uuid.uuid4()),
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

  @staticmethod
  def _activation_readiness_failures(
    readiness: dict[str, Any],
  ) -> list[str]:
    """Return current account and feature blockers for automatic execution."""

    if bool(readiness.get("automation_ready")):
      return []
    failed = [
      item
      for item in list(readiness.get("checks") or [])
      if not bool(item.get("passed"))
    ]
    if failed:
      return [
        str(item.get("message") or item.get("code") or "做 T 实盘未就绪")
        for item in failed
      ]
    return [
      str(item or "做 T 实盘未就绪")
      for item in list(readiness.get("blocked_reasons") or [])
    ] or ["当前账户执行安全状态不完整，拒绝启用"]

  @staticmethod
  def _rollout_limits_check(rollout: TTradeRollout | None) -> dict[str, Any]:
    passed = bool(
      rollout is not None
      and int(getattr(rollout, "max_active_batches", 0) or 0) == 1
      and int(getattr(rollout, "max_batch_volume", 0) or 0) > 0
      and float(getattr(rollout, "max_order_amount", 0) or 0) > 0
      and 0 < float(getattr(rollout, "max_total_exposure_pct", 0) or 0) <= 0.02
    )
    return {
      "code": "T_TRADE_ROLLOUT_LIMITS_CONFIGURED",
      "passed": passed,
      "message": ""
      if passed
      else "做 T 执行限制必须为单批次、正订单上限和不超过 2% 总敞口",
      "scope": "AUTOMATION",
    }

  @classmethod
  async def readiness(self, account_id: str) -> dict[str, Any]:
    account_safety = await AccountExecutionSafetyService().status(account_id)
    async with AsyncSessionLocal() as db:
      rollout = await db.get(TTradeRollout, account_id)
    feature_checks = [
      {
        "code": "T_TRADE_LIVE_ENABLED",
        "passed": bool(settings.t_trade_live_enabled),
        "message": ""
        if settings.t_trade_live_enabled
        else "服务端 T_TRADE_LIVE_ENABLED 未启用",
        "scope": "AUTOMATION",
      },
      {
        "code": "T_TRADE_ROLLOUT_CONFIGURED",
        "passed": rollout is not None,
        "message": "" if rollout is not None else "做 T 尚未创建独立灰度配置",
        "scope": "AUTOMATION",
      },
      self._rollout_limits_check(rollout),
    ]
    account_checks = [dict(item) for item in list(account_safety.get("checks") or [])]
    checks = account_checks + feature_checks
    blocked = [
      str(item.get("message") or item.get("code"))
      for item in checks
      if not bool(item.get("passed"))
    ]
    feature_ready = not any(not bool(item.get("passed")) for item in feature_checks)
    account_ready = bool(account_safety.get("can_increase_risk"))
    automation_ready = account_ready and feature_ready
    stage = str(rollout.stage if rollout else "SHADOW")
    feature_enabled = bool(rollout and rollout.enabled and stage in {"CANARY", "LIVE"})
    status = (
      "READY"
      if automation_ready
      else "PREPARING"
      if account_safety.get("health_status") == "HEALTHY"
      else "BLOCKED"
    )
    if bool(account_safety.get("kill_switch")):
      status = "HARD_KILL"
    return {
      "account_id": account_id,
      "ready": automation_ready,
      "status": status,
      "preparation_ready": bool(account_safety.get("can_reduce_risk")),
      "automation_ready": automation_ready,
      "rollout_enabled": feature_enabled,
      "stage": stage,
      "engine_status": account_safety.get("engine_status", "OFFLINE"),
      "agent_status": account_safety.get("agent_status", "OFFLINE"),
      "agent_device_id": account_safety.get("agent_device_id"),
      "ready_live_agent_count": int(
        account_safety.get("ready_live_agent_count") or 0
      ),
      "agent_mode": account_safety.get("agent_mode", "offline"),
      "requested_agent_mode": account_safety.get("requested_agent_mode", "unknown"),
      "qmt_launch_reason_code": account_safety.get("qmt_launch_reason_code", ""),
      "protocol_version": account_safety.get("protocol_version", ""),
      "reconcile_status": account_safety.get("reconcile_status", "UNKNOWN"),
      "kill_switch": bool(account_safety.get("kill_switch")),
      "policy_version": int(rollout.policy_version if rollout else 1),
      "can_approve": feature_enabled and automation_ready,
      "can_activate_live": automation_ready,
      "blocked_reasons": blocked,
      "preparation_blocked_reasons": list(account_safety.get("blocked_reasons") or []),
      "checks": checks,
      "feature_checks": feature_checks,
      "account_safety": account_safety,
      "snapshot_id": account_safety.get("snapshot_id"),
      "snapshot_hash": account_safety.get("snapshot_hash"),
      "snapshot_at": account_safety.get("snapshot_at"),
      "reconciliation_age_seconds": account_safety.get("reconciliation_age_seconds"),
      "queued_command_count": int(account_safety.get("queued_command_count") or 0),
      "queue_delay_seconds": float(account_safety.get("queue_delay_seconds") or 0),
      "dead_letter_count": int(account_safety.get("dead_letter_count") or 0),
      "unresolved_critical_alert_count": int(
        account_safety.get("unresolved_critical_alert_count") or 0
      ),
      "manual_coexistence": bool(account_safety.get("manual_coexistence")),
      "external_order_count": int(account_safety.get("external_order_count") or 0),
      "external_trade_count": int(account_safety.get("external_trade_count") or 0),
      "controlled_window_active": bool(account_safety.get("execution_window_active")),
      "controlled_window_snapshot_id": account_safety.get(
        "controlled_window_snapshot_id"
      ),
      "controlled_window_started_at": account_safety.get(
        "controlled_window_started_at"
      ),
      "new_external_order_count": int(
        account_safety.get("new_external_order_count") or 0
      ),
      "new_external_trade_count": int(
        account_safety.get("new_external_trade_count") or 0
      ),
      "working_external_order_count": int(
        account_safety.get("working_external_order_count") or 0
      ),
      "journal_integrity": account_safety.get("journal_integrity", "unknown"),
      "journal_size_bytes": int(account_safety.get("journal_size_bytes") or 0),
      "journal_pending_reports": int(
        account_safety.get("journal_pending_reports") or 0
      ),
      "last_backup_at": account_safety.get("last_backup_at"),
      "checked_at": account_safety.get("checked_at") or utcnow(),
    }

  async def ensure_rollout(self, account_id: str) -> TTradeRollout:
    async with AsyncSessionLocal() as db:
      rollout = await db.get(TTradeRollout, account_id)
      if rollout is None:
        rollout = TTradeRollout(account_id=account_id)
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
    await AccountExecutionSafetyService().mark_reconciled(
      account_id,
      ready=ready,
      reason=reason,
    )

  async def activate_rollout(
    self,
    account_id: str,
    *,
    user_id: str,
    acknowledged_policy_version: int,
    target_stage: str = "CANARY",
    confirmation: str = "",
    expected_snapshot_id: str | None = None,
    operation_id: str | None = None,
  ) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    target = str(target_stage or "CANARY").strip().upper()
    if target not in {"CANARY", "LIVE"}:
      raise ValueError("目标灰度阶段必须是 CANARY 或 LIVE")

    expected_event_types = (
      {"LIVE_ACTIVATED"} if target == "LIVE" else {"CANARY_ACTIVATED", "LIVE_ACTIVATED"}
    )
    # A: resolve an already committed operation before evaluating mutable
    # readiness gates.  This is intentionally a separate short transaction.
    if operation_id:
      replay = False
      async with AsyncSessionLocal() as db:
        applied = await db.get(TTradeRolloutEvent, operation_id)
        if applied is not None:
          self._assert_operation_event_binding(
            applied,
            account_id=account_id,
            operation_id=operation_id,
            event_types=expected_event_types,
            actor_user_id=user_id,
            snapshot_id=expected_snapshot_id,
            target_stage=target,
            policy_version=acknowledged_policy_version,
            confirmation=confirmation,
          )
          replay = True
        await db.rollback()
      if replay:
        return await self.readiness(account_id)

    # B: evaluate mutable gates while no rollout row lock is held.
    readiness = await self.readiness(account_id)
    preflight_failures = self._activation_readiness_failures(readiness)
    if preflight_failures:
      raise ValueError("；".join(preflight_failures))
    if target == "LIVE":
      if confirmation != f"LIVE:{account_id}":
        raise ValueError(f"正式 LIVE 需要精确确认 LIVE:{account_id}")
      if not readiness.get("controlled_window_active"):
        raise ValueError("正式 LIVE 需要先建立账户实盘窗口")
    if not str(operation_id or "").strip():
      raise ValueError("启用实盘阶段必须携带受鉴权的幂等操作标识")
    bound_snapshot_id = expected_snapshot_id or str(readiness.get("snapshot_id") or "")
    if not bound_snapshot_id or str(readiness.get("snapshot_id") or "") != (
      bound_snapshot_id
    ):
      raise ValueError("完整快照已经更新，请刷新页面后重新检查实盘门禁")

    # C: take the rollout lock only for the final, serialized transition and
    # recheck the marker to close the A/B -> C race.
    replay = False
    async with AsyncSessionLocal() as db:
      account_control = await db.get(
        AccountExecutionControl,
        account_id,
        with_for_update=True,
      )
      rollout = await db.get(
        TTradeRollout,
        account_id,
        with_for_update=True,
      )
      if rollout is None:
        raise ValueError("做 T 助手灰度配置不存在")
      if operation_id:
        applied = await db.get(TTradeRolloutEvent, operation_id)
        if applied is not None:
          self._assert_operation_event_binding(
            applied,
            account_id=account_id,
            operation_id=operation_id,
            event_types=expected_event_types,
            actor_user_id=user_id,
            snapshot_id=expected_snapshot_id,
            target_stage=target,
            policy_version=acknowledged_policy_version,
            confirmation=confirmation,
          )
          replay = True

      if replay:
        await db.rollback()
      else:
        preflight_failures = self._activation_readiness_failures(readiness)
        if preflight_failures:
          raise ValueError("；".join(preflight_failures))
        if target == "LIVE":
          if confirmation != f"LIVE:{account_id}":
            raise ValueError(f"正式 LIVE 需要精确确认 LIVE:{account_id}")
          if not readiness.get("controlled_window_active"):
            raise ValueError("正式 LIVE 需要先建立账户实盘窗口")
        if account_control is None:
          raise ValueError("账户执行控制配置不存在")
        if str(account_control.authorization_state).upper() != "ENABLED":
          raise ValueError("账户买入权限未启用")
        if not account_control.controlled_window_active:
          raise ValueError("账户实盘窗口尚未建立")
        if str(account_control.last_snapshot_id or "") != str(bound_snapshot_id):
          raise ValueError("完整快照已经更新，请刷新后重新检查实盘门禁")
        if str(readiness.get("snapshot_hash") or "") != str(
          account_control.last_snapshot_hash or ""
        ):
          raise ValueError("完整快照已经更新，请刷新后重新检查实盘门禁")
        if acknowledged_policy_version != rollout.policy_version:
          raise ValueError("确认的自动退出策略版本已过期")
        limits_check = self._rollout_limits_check(rollout)
        if not limits_check["passed"]:
          raise ValueError(str(limits_check["message"]))
        previous_stage = str(rollout.stage).upper()
        if previous_stage not in {"SHADOW", "PAUSED"}:
          raise ValueError("当前做 T 阶段不允许重新启用")
        next_stage = target
        rollout.stage = next_stage
        rollout.enabled = True
        rollout.acknowledged_policy_version = acknowledged_policy_version
        rollout.activated_by_user_id = user_id
        rollout.activated_at = utcnow()
        rollout.paused_reason = None
        self._append_rollout_event(
          db,
          event_id=operation_id,
          account_id=account_id,
          event_type=f"{next_stage}_ACTIVATED",
          actor_user_id=user_id,
          previous_stage=previous_stage,
          next_stage=next_stage,
          snapshot_id=bound_snapshot_id,
          details={
            "operationId": operation_id,
            "policyVersion": acknowledged_policy_version,
            "confirmation": confirmation,
            "targetStage": target,
          },
        )
        await db.commit()
    return await self.readiness(account_id)

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
        TTradeRollout,
        account_id,
        with_for_update=True,
      )
      previous_stage = str(rollout.stage)
      rollout.enabled = False
      rollout.stage = "PAUSED"
      rollout.paused_reason = reason[:2000] or "manual pause"
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
    operation_id: str | None = None,
  ) -> dict[str, Any]:
    await self.ensure_rollout(account_id)
    normalized_reason = reason[:2000] or "manual T-trade stop"
    async with AsyncSessionLocal() as db:
      rollout = await db.get(TTradeRollout, account_id, with_for_update=True)
      if rollout is None:
        raise ValueError("做 T 灰度配置不存在")
      if operation_id:
        applied = await db.get(TTradeRolloutEvent, operation_id)
        if applied is not None:
          details = dict(applied.details or {})
          if (
            str(applied.account_id) != account_id
            or str(applied.event_type) != "T_TRADE_STOPPED"
            or str(details.get("operationId") or "") != operation_id
            or str(details.get("reason") or "") != normalized_reason
            or str(applied.actor_user_id or "") != str(user_id or "")
          ):
            raise TTradeOperationIdempotencyError("做 T 控制幂等标识已绑定其他操作")
          await db.rollback()
          return await self.readiness(account_id)
      previous_stage = str(rollout.stage)
      stop_event_id = operation_id or str(uuid.uuid4())
      rollout.enabled = False
      rollout.stage = "PAUSED"
      rollout.paused_reason = normalized_reason
      self._append_rollout_event(
        db,
        event_id=operation_id,
        account_id=account_id,
        event_type="T_TRADE_STOPPED",
        actor_user_id=user_id,
        previous_stage=previous_stage,
        next_stage="PAUSED",
        details={
          **({"operationId": operation_id} if operation_id else {}),
          "reason": normalized_reason,
        },
      )
      batches = (
        (
          await db.execute(
            select(TTradeBatch).where(
              TTradeBatch.account_id == account_id,
              TTradeBatch.status.notin_(self.TERMINAL_BATCH_STATUSES),
            )
          )
        )
        .scalars()
        .all()
      )
      t_client_order_ids = {
        value
        for batch in batches
        for value in (batch.entry_client_order_id, batch.exit_client_order_id)
        if value
      }
      pending_orders = []
      if t_client_order_ids:
        pending_orders = (
          (
            await db.execute(
              select(PendingTradeOrder).where(
                PendingTradeOrder.account_id == account_id,
                PendingTradeOrder.client_order_id.in_(t_client_order_ids),
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
            select(TradeCommandOutbox).where(
              TradeCommandOutbox.account_id == account_id,
              TradeCommandOutbox.client_order_id.in_(t_client_order_ids),
            )
          )
        )
        .scalars()
        .all()
        if t_client_order_ids
        else []
      )
      command_by_client = {
        row.client_order_id: row
        for row in source_commands
        if row.payload.get("command_kind") == "PLACE_ORDER"
      }
      for command in command_by_client.values():
        if command.delivery_status == "QUEUED":
          command.delivery_status = "CANCELLED_KILL"
          command.last_error = "T-trade stopped before command delivery"
      now = utcnow()
      cancellation_keys: set[str] = set()
      for pending in pending_orders:
        if not pending.broker_order_id:
          if pending.status in {"QUEUED", "PENDING"}:
            pending.status = "CANCELLED"
            pending.status_reason = "T-trade stopped before broker order id"
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
              (f"t-trade-stop-cancel:{account_id}:{cancel_key}:{stop_event_id}").encode(
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
              "trace_id": stop_event_id,
              "expires_at": expires_at.isoformat() + "Z",
            },
            delivery_status="QUEUED",
            expires_at=expires_at,
            attempts=0,
          )
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
        (
          await db.execute(
            query.order_by(TTradeBatch.updated_at.desc())
            .offset(max(0, offset))
            .limit(min(max(1, limit), 200))
          )
        )
        .scalars()
        .all()
      )
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
        )
        .scalars()
        .all()
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
          PendingTradeOrder.client_order_id == StrategyRuntimeEvent.client_order_id,
        )
        .where(PendingTradeOrder.account_id == account_id)
      )
      if batch_id:
        query = query.where(PendingTradeOrder.batch_id == batch_id)
      rows = (
        (
          await db.execute(
            query.order_by(StrategyRuntimeEvent.created_at.desc()).limit(
              min(max(1, limit), 200)
            )
          )
        )
        .scalars()
        .all()
      )
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
          PendingTradeOrder.client_order_id == StrategyRuntimeEvent.client_order_id,
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
        )
        .scalars()
        .all()
      )
      return (
        [self._event_row(row) for row in rows[:safe_first]],
        len(rows) > safe_first,
      )

  @staticmethod
  def _event_row(row: StrategyRuntimeEvent) -> dict[str, Any]:
    return {
      "event_id": row.event_id,
      "batch_id": str((row.payload or {}).get("metadata", {}).get("t_batch_id") or ""),
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
