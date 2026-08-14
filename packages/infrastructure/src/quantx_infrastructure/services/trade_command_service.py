"""Persist user trade intent and enqueue delivery to a registered QMT agent."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from quantx_domain.clock import to_naive_utc, utcnow
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRollout,
  AgentDevice,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.exit_plan_authorization_service import (
  validate_exact_auto_exit_authorization,
)


class AgentUnavailableError(RuntimeError):
  pass


@dataclass(frozen=True)
class QueuedTradeCommand:
  client_order_id: str
  message_id: str
  status: str


class TradeCommandService:
  MANUAL_RECONCILIATION_MAX_AGE_SECONDS = 90

  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  @staticmethod
  def order_idempotency_digest(
    *, user_id: str, account_id: str, idempotency_key: str
  ) -> str:
    """Return the persisted business key used to recover queued order results."""

    return hashlib.sha256(
      f"order:{user_id}:{account_id}:{idempotency_key.strip()}".encode("utf-8")
    ).hexdigest()

  @staticmethod
  def _heartbeat_fresh(heartbeat: RuntimeComponentHeartbeat) -> bool:
    updated_at = to_naive_utc(heartbeat.updated_at)
    return (utcnow() - updated_at).total_seconds() <= 90

  async def _require_live_authorization(
    self,
    account_id: str,
    *,
    risk_reducing: bool = False,
  ) -> None:
    if not settings.enable_real_trading or not settings.t_trade_live_enabled:
      raise AgentUnavailableError("服务端真实交易或做 T 实盘开关未启用")
    if account_id not in set(settings.real_trading_account_allowlist or []):
      raise AgentUnavailableError("账户不在服务端真实交易白名单")
    rollout = await self.db.get(AccountTradingRollout, account_id)
    if rollout is None:
      raise AgentUnavailableError("账户尚未配置做 T 灰度策略")
    if rollout.kill_switch or rollout.stage == "KILL_SWITCHED":
      raise AgentUnavailableError("账户做 T kill switch 已触发")
    if not rollout.enabled or rollout.stage not in {"CANARY", "LIVE"}:
      paused_exit_allowed = (
        risk_reducing
        and rollout.stage == "PAUSED"
        and rollout.reconcile_status == "READY"
      )
      if not paused_exit_allowed:
        raise AgentUnavailableError("账户尚未进入 CANARY/LIVE 阶段")
    if rollout.reconcile_status != "READY":
      raise AgentUnavailableError("账户快照或仓位对账未就绪")
    if rollout.acknowledged_policy_version < rollout.policy_version:
      raise AgentUnavailableError("当前自动退出策略版本尚未确认")

  async def _require_manual_live_authorization(
    self,
    account_id: str,
    *,
    risk_reducing: bool,
  ) -> AccountTradingRollout:
    """Lock and validate the account gate for a confirmed manual live order.

    The rollout row is the first mutable trading row locked by both this path
    and ``TTradeOperationsService.kill``.  If enqueue wins, kill subsequently
    scans and cancels the new pending command; if kill wins, a BUY observes the
    kill state here and is rejected before any outbox row is created.

    Risk-reducing SELL orders deliberately do not require an active controlled
    window, CANARY/LIVE enablement, or policy acknowledgement.  This preserves
    an escape path while paused or killed, but still requires a current,
    authoritative reconciliation snapshot and a ready live device.
    """

    if not settings.enable_real_trading:
      raise AgentUnavailableError("服务端真实交易总开关未启用")
    if account_id not in set(settings.real_trading_account_allowlist or []):
      raise AgentUnavailableError("账户不在服务端真实交易白名单")
    rollout = await self.db.get(
      AccountTradingRollout,
      account_id,
      with_for_update=True,
    )
    if rollout is None:
      raise AgentUnavailableError("账户尚未配置实盘灰度与对账状态")

    if not risk_reducing and (
      bool(rollout.kill_switch)
      or str(rollout.stage or "").upper() == "KILL_SWITCHED"
    ):
      raise AgentUnavailableError("账户交易 kill switch 已触发，禁止新增风险")
    if str(rollout.reconcile_status or "").upper() != "READY":
      raise AgentUnavailableError("账户资金、持仓、委托和成交快照尚未完成对账")

    snapshot_id = str(rollout.last_snapshot_id or "")
    snapshot_hash = str(rollout.last_snapshot_hash or "")
    snapshot_at = (
      to_naive_utc(rollout.last_snapshot_at)
      if rollout.last_snapshot_at is not None
      else None
    )
    snapshot_age = (
      (utcnow() - snapshot_at).total_seconds()
      if snapshot_at is not None
      else None
    )
    if (
      not snapshot_id
      or not snapshot_hash
      or snapshot_age is None
      or snapshot_age < 0
      or snapshot_age > self.MANUAL_RECONCILIATION_MAX_AGE_SECONDS
    ):
      raise AgentUnavailableError("账户完整对账快照缺失或已超过 90 秒")

    if risk_reducing:
      return rollout

    if not bool(rollout.controlled_window_active):
      raise AgentUnavailableError("手动买入需要基于最新快照建立受控交易窗口")
    controlled_snapshot_id = str(rollout.controlled_window_snapshot_id or "")
    controlled_snapshot_hash = str(rollout.controlled_window_snapshot_hash or "")
    if (
      controlled_snapshot_id != snapshot_id
      or controlled_snapshot_hash != snapshot_hash
    ):
      raise AgentUnavailableError("受控窗口快照与最新完整快照不一致")
    if not bool(rollout.enabled) or str(rollout.stage or "").upper() not in {
      "CANARY",
      "LIVE",
    }:
      raise AgentUnavailableError("手动买入要求账户处于已启用的 CANARY/LIVE 阶段")
    if int(rollout.acknowledged_policy_version or 0) < int(
      rollout.policy_version or 0
    ):
      raise AgentUnavailableError("当前交易策略版本尚未确认")
    return rollout

  async def _device_for(
    self,
    *,
    user_id: str,
    account_id: str,
    execution_mode: str,
    allow_degraded_cancel: bool = False,
  ) -> AgentDevice:
    result = await self.db.execute(
      select(AgentDevice).where(
        AgentDevice.user_id == user_id,
        AgentDevice.revoked_at.is_(None),
      )
    )
    devices = result.scalars().all()
    eligible: list[AgentDevice] = []
    for device in devices:
      allowed = list(device.authorized_account_ids or [])
      capabilities = {
        str(capability).lower()
        for capability in list(device.capabilities or [])
      }
      if account_id not in allowed or execution_mode not in capabilities:
        continue
      if execution_mode == "live":
        heartbeat = await self.db.get(
          RuntimeComponentHeartbeat,
          f"qmt-agent:{device.id}",
        )
        acceptable_statuses = (
          {"READY", "EMERGENCY_STOP", "RECONCILE_REQUIRED"}
          if allow_degraded_cancel
          else {"READY"}
        )
        if (
          heartbeat is None
          or str(heartbeat.status).upper() not in acceptable_statuses
        ):
          continue
        if not self._heartbeat_fresh(heartbeat):
          continue
      eligible.append(device)
    if execution_mode == "live" and len(eligible) > 1:
      raise AgentUnavailableError(
        "同一账户检测到多个就绪 live QMT Agent，已拒绝路由交易命令"
      )
    if eligible:
      return eligible[0]
    raise AgentUnavailableError(
      f"没有已登记、就绪且具备交易能力（{execution_mode}）的 QMT Agent"
    )

  async def _device_for_account(
    self,
    account_id: str,
    execution_mode: str,
  ) -> AgentDevice:
    result = await self.db.execute(
      select(AgentDevice).where(AgentDevice.revoked_at.is_(None))
    )
    for device in result.scalars().all():
      capabilities = {
        str(capability).lower()
        for capability in list(device.capabilities or [])
      }
      if (
        account_id in list(device.authorized_account_ids or [])
        and execution_mode in capabilities
      ):
        if execution_mode == "live":
          heartbeat = await self.db.get(
            RuntimeComponentHeartbeat,
            f"qmt-agent:{device.id}",
          )
          if (
            heartbeat is None
            or str(heartbeat.status).upper() != "READY"
            or not self._heartbeat_fresh(heartbeat)
          ):
            continue
        return device
    raise AgentUnavailableError(
      f"没有已登记、就绪且具备交易能力（{execution_mode}）的 QMT Agent"
    )

  async def enqueue_order(
    self,
    *,
    user_id: str,
    account_id: str,
    instrument_code: str,
    side: str,
    order_type: str,
    limit_price: Decimal,
    volume: int,
    strategy_name: str = "",
    order_remark: str = "",
    trace_id: str = "",
    idempotency_key: str = "",
    execution_mode: str = "paper",
    strategy_run_id: str = "",
    strategy_order_id: str = "",
    intent_id: str = "",
    batch_id: str = "",
    bucket: str = "manual",
    t_trade_role: str = "",
    risk_decision_id: str = "",
    substitution_plan: dict[str, Any] | None = None,
    policy_version: int = 0,
    request_metadata: dict[str, Any] | None = None,
    manual_live: bool = False,
    reason_tags: list[str] | None = None,
    commit_transaction: bool = True,
  ) -> QueuedTradeCommand:
    if volume <= 0:
      raise ValueError("委托数量必须大于 0")
    normalized_mode = execution_mode.strip().lower()
    if normalized_mode not in {"paper", "live"}:
      raise ValueError("交易命令 execution_mode 必须是 paper 或 live")
    normalized_role = t_trade_role.strip().upper()
    if normalized_role not in {"", "ENTRY", "EXIT"}:
      raise ValueError("做 T 订单角色必须是 ENTRY 或 EXIT")
    immutable_metadata = dict(request_metadata or {})
    if manual_live and normalized_mode != "live":
      raise ValueError("手动实盘授权只能用于 live 交易命令")
    risk_reducing = normalized_role == "EXIT" or side.upper() == "SELL"
    if manual_live:
      # This lock must precede the outbox lookup/insert to match kill()'s
      # rollout -> pending/outbox lock order.
      await self._require_manual_live_authorization(
        account_id,
        risk_reducing=risk_reducing,
      )

    raw_idempotency_key = idempotency_key.strip() or trace_id.strip()
    if raw_idempotency_key:
      business_idempotency_key = self.order_idempotency_digest(
        user_id=user_id,
        account_id=account_id,
        idempotency_key=raw_idempotency_key,
      )
      existing = (
        await self.db.execute(
          select(TradeCommandOutbox).where(
            TradeCommandOutbox.idempotency_key == business_idempotency_key
          )
        )
      ).scalar_one_or_none()
      if existing is not None:
        return QueuedTradeCommand(
          existing.client_order_id,
          existing.message_id,
          existing.delivery_status,
        )
    else:
      business_idempotency_key = f"generated:{uuid.uuid4()}"

    if normalized_mode == "live" and not manual_live:
      await self._require_live_authorization(
        account_id,
        risk_reducing=risk_reducing,
      )
    device = await self._device_for(
      user_id=user_id,
      account_id=account_id,
      execution_mode=normalized_mode,
    )
    now = utcnow()
    client_order_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=2)
    payload: dict[str, Any] = {
      "command_kind": "PLACE_ORDER",
      "client_order_id": client_order_id,
      "account_id": account_id,
      "execution_mode": normalized_mode,
      "instance_id": strategy_name or "manual",
      "instrument_code": instrument_code,
      "side": side,
      "order_type": order_type,
      "limit_price": str(limit_price),
      "volume": volume,
      "strategy_name": strategy_name,
      "bucket": bucket or "manual",
      "order_remark": order_remark,
      "trace_id": trace_id or message_id,
      "risk_decision_id": risk_decision_id or trace_id or message_id,
      "reason_tags": sorted(
        {
          str(value).strip()
          for value in (
            list(reason_tags)
            if reason_tags is not None
            else ["queued-command"]
          )
          if str(value).strip()
        }
      ),
      "substitution_plan": substitution_plan,
      "strategy_run_id": strategy_run_id,
      "strategy_order_id": strategy_order_id,
      "intent_id": intent_id,
      "batch_id": batch_id,
      "t_trade_role": normalized_role,
      "policy_version": max(0, int(policy_version or 0)),
      "request_metadata": immutable_metadata,
      "expires_at": expires_at.isoformat() + "Z",
    }
    self.db.add(
      PendingTradeOrder(
        client_order_id=client_order_id,
        user_id=user_id,
        account_id=account_id,
        instrument_code=instrument_code,
        side=side,
        order_type=order_type,
        limit_price=str(limit_price),
        volume=volume,
        status="QUEUED",
        execution_mode=normalized_mode,
        strategy_run_id=strategy_run_id or None,
        strategy_order_id=strategy_order_id or None,
        intent_id=intent_id or None,
        batch_id=batch_id or None,
        bucket=bucket or "manual",
        t_trade_role=normalized_role or None,
        risk_decision_id=risk_decision_id or None,
        trace_id=trace_id or message_id,
        substitution_plan=substitution_plan,
        request_metadata=immutable_metadata,
      )
    )
    if strategy_run_id and strategy_order_id and intent_id:
      self.db.add(
        StrategyOrderCorrelation(
          id=str(uuid.uuid4()),
          client_order_id=client_order_id,
          account_id=account_id,
          strategy_run_id=strategy_run_id,
          strategy_order_id=strategy_order_id,
          intent_id=intent_id,
          batch_id=batch_id or None,
          bucket=bucket or "manual",
          t_trade_role=normalized_role or None,
          execution_mode=normalized_mode,
          risk_decision_id=risk_decision_id or None,
          trace_id=trace_id or message_id,
          substitution_plan=substitution_plan,
          request_metadata=immutable_metadata,
        )
      )
      if batch_id:
        batch = await self.db.get(TTradeBatch, batch_id)
        if batch is None:
          batch = TTradeBatch(
            batch_id=batch_id,
            account_id=account_id,
            instrument_code=instrument_code,
            strategy_run_id=strategy_run_id,
            target_volume=volume,
            policy_version=max(0, int(policy_version or 0)),
          )
          self.db.add(batch)
        if normalized_role == "ENTRY":
          batch.entry_intent_id = intent_id
          batch.entry_client_order_id = client_order_id
          batch.status = "ENTRY_QUEUED"
        elif normalized_role == "EXIT":
          batch.exit_intent_id = intent_id
          batch.exit_client_order_id = client_order_id
          batch.status = "EXIT_TRIGGERED"
    self.db.add(
      TradeCommandOutbox(
        message_id=message_id,
        client_order_id=client_order_id,
        idempotency_key=business_idempotency_key,
        device_id=device.id,
        account_id=account_id,
        payload=payload,
        delivery_status="QUEUED",
        expires_at=expires_at,
        attempts=0,
      )
    )
    if not commit_transaction:
      # The caller owns one atomic transaction spanning its authorization
      # record and the pending/outbox rows.  Integrity failures propagate so
      # the caller can roll back the entire unit rather than half-commit it.
      await self.db.flush()
      return QueuedTradeCommand(client_order_id, message_id, "QUEUED")
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = (
        await self.db.execute(
          select(TradeCommandOutbox).where(
            TradeCommandOutbox.idempotency_key == business_idempotency_key
          )
        )
      ).scalar_one_or_none()
      if existing is None:
        raise
      return QueuedTradeCommand(
        existing.client_order_id,
        existing.message_id,
        existing.delivery_status,
      )
    return QueuedTradeCommand(client_order_id, message_id, "QUEUED")

  async def enqueue_order_for_account(
    self,
    *,
    account_id: str,
    instrument_code: str,
    side: str,
    order_type: str,
    limit_price: Decimal,
    volume: int,
    strategy_name: str = "",
    order_remark: str = "",
    trace_id: str = "",
    idempotency_key: str = "",
    execution_mode: str = "paper",
    strategy_run_id: str = "",
    strategy_order_id: str = "",
    intent_id: str = "",
    batch_id: str = "",
    bucket: str = "manual",
    t_trade_role: str = "",
    risk_decision_id: str = "",
    substitution_plan: dict[str, Any] | None = None,
    policy_version: int = 0,
    request_metadata: dict[str, Any] | None = None,
    require_risk_reducing_live_authorization: bool = False,
    authorization_user_id: str = "",
  ) -> QueuedTradeCommand:
    normalized_execution_mode = str(execution_mode or "paper").lower()
    if require_risk_reducing_live_authorization:
      metadata = dict(request_metadata or {})
      authorization_plan_id = str(metadata.get("exit_plan_id") or "").strip()
      authorization_fingerprint = str(
        metadata.get("auto_exit_authorization_fingerprint") or ""
      ).strip()
      if normalized_execution_mode != "live" or str(side or "").upper() != "SELL":
        raise AgentUnavailableError(
          "精确自动退出门禁只能用于 LIVE 风险降低卖单"
        )
      if not str(authorization_user_id or "").strip():
        raise AgentUnavailableError("自动退出授权缺少确认用户绑定")
      if not authorization_plan_id or not authorization_fingerprint:
        raise AgentUnavailableError("自动退出命令缺少精确计划授权绑定")
      plan = (
        await self.db.execute(
          select(AutoExitPlanRecord)
          .where(AutoExitPlanRecord.plan_id == authorization_plan_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if (
        plan is None
        or str(plan.account_id) != str(account_id)
        or str(plan.instrument_code) != str(instrument_code)
        or int(plan.config_version or 0) != int(policy_version or 0)
        or str(plan.auto_exit_authorization_user_id or "")
        != str(authorization_user_id)
        or str(plan.auto_exit_authorization_fingerprint or "")
        != authorization_fingerprint
      ):
        raise AgentUnavailableError("自动退出计划、版本、标的或授权人绑定不匹配")
      validation = await validate_exact_auto_exit_authorization(
        self.db,
        plan,
        lock_mutable_rows=True,
      )
      if not validation.valid:
        raise AgentUnavailableError(
          f"自动退出授权已失效：{validation.code}"
        )
      intent = await self.db.get(
        TradeIntentRecord,
        str(intent_id or ""),
        with_for_update=True,
      )
      intent_metadata = (
        dict(intent.intent_metadata or {}) if intent is not None else {}
      )
      plan_state = dict(plan.plan_state or {})
      if (
        intent is None
        or str(intent.owner_type or "") != "EXIT_PLAN"
        or str(intent.owner_id or "") != authorization_plan_id
        or str(intent.account_id or "") != str(account_id)
        or str(intent.instrument_code or "") != str(instrument_code)
        or str(intent.direction or "").upper() != "SELL"
        or str(intent.status or "").upper() != "PENDING"
        or str(plan_state.get("pending_intent_id") or "") != str(intent_id or "")
        or not bool(intent_metadata.get("exact_auto_exit_authorized"))
        or str(intent_metadata.get("auto_exit_authorization_fingerprint") or "")
        != authorization_fingerprint
        or str(intent_metadata.get("auto_exit_authorization_user_id") or "")
        != str(authorization_user_id)
        or int(intent.target_volume or 0) < int(volume)
      ):
        raise AgentUnavailableError("自动退出意图与精确计划授权不匹配")
      position = await self.db.scalar(
        select(Position)
        .where(
          Position.account_id == account_id,
          Position.stock_code == instrument_code,
        )
        .with_for_update()
      )
      if (
        int(volume) > int(plan.remaining_volume or 0)
        or position is None
        or int(volume) > int(position.can_use_volume or 0)
      ):
        raise AgentUnavailableError("自动退出委托超过当前计划剩余量或实时可卖量")
      await self._require_manual_live_authorization(
        account_id,
        risk_reducing=True,
      )
      device = await self._device_for(
        user_id=str(authorization_user_id),
        account_id=account_id,
        execution_mode="live",
      )
      heartbeat = await self.db.get(
        RuntimeComponentHeartbeat,
        f"qmt-agent:{device.id}",
      )
      details = dict(heartbeat.details or {}) if heartbeat is not None else {}
      capabilities = {
        str(value).strip().lower()
        for value in list(details.get("capabilities") or [])
        if str(value).strip()
      }
      if (
        heartbeat is None
        or str(heartbeat.status or "").upper() != "READY"
        or "live" not in capabilities
        or str(details.get("protocolVersion") or "") != "1.1"
      ):
        raise AgentUnavailableError(
          "自动退出要求唯一 READY、live、协议 1.1 的 QMT Agent"
        )
    else:
      device = await self._device_for_account(account_id, normalized_execution_mode)
    return await self.enqueue_order(
      user_id=device.user_id,
      account_id=account_id,
      instrument_code=instrument_code,
      side=side,
      order_type=order_type,
      limit_price=limit_price,
      volume=volume,
      strategy_name=strategy_name,
      order_remark=order_remark,
      trace_id=trace_id,
      idempotency_key=idempotency_key,
      execution_mode=normalized_execution_mode,
      strategy_run_id=strategy_run_id,
      strategy_order_id=strategy_order_id,
      intent_id=intent_id,
      batch_id=batch_id,
      bucket=bucket,
      t_trade_role=t_trade_role,
      risk_decision_id=risk_decision_id,
      substitution_plan=substitution_plan,
      policy_version=policy_version,
      request_metadata=request_metadata,
    )

  async def enqueue_cancel(
    self,
    *,
    user_id: str,
    account_id: str,
    broker_order_id: str,
    idempotency_key: str = "",
    execution_mode: str = "paper",
    commit_transaction: bool = True,
  ) -> QueuedTradeCommand:
    business_idempotency_key = hashlib.sha256(
      (
        f"cancel:{user_id}:{account_id}:"
        f"{idempotency_key.strip() or broker_order_id}"
      ).encode("utf-8")
    ).hexdigest()
    existing = (
      await self.db.execute(
        select(TradeCommandOutbox).where(
          TradeCommandOutbox.idempotency_key == business_idempotency_key
        )
      )
    ).scalar_one_or_none()
    if existing is not None:
      return QueuedTradeCommand(
        existing.client_order_id,
        existing.message_id,
        existing.delivery_status,
      )
    device = await self._device_for(
      user_id=user_id,
      account_id=account_id,
      execution_mode=execution_mode,
      allow_degraded_cancel=True,
    )
    now = utcnow()
    client_order_id = f"cancel:{uuid.uuid4()}"
    message_id = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=2)
    payload = {
      "command_kind": "CANCEL_ORDER",
      "client_order_id": client_order_id,
      "account_id": account_id,
      "execution_mode": execution_mode,
      "broker_order_id": str(broker_order_id),
      "trace_id": message_id,
      "expires_at": expires_at.isoformat() + "Z",
    }
    self.db.add(
      TradeCommandOutbox(
        message_id=message_id,
        client_order_id=client_order_id,
        idempotency_key=business_idempotency_key,
        device_id=device.id,
        account_id=account_id,
        payload=payload,
        delivery_status="QUEUED",
        expires_at=expires_at,
        attempts=0,
      )
    )
    if commit_transaction:
      try:
        await self.db.commit()
      except IntegrityError:
        await self.db.rollback()
        existing = (
          await self.db.execute(
            select(TradeCommandOutbox).where(
              TradeCommandOutbox.idempotency_key == business_idempotency_key
            )
          )
        ).scalar_one_or_none()
        if existing is None:
          raise
        return QueuedTradeCommand(
          existing.client_order_id,
          existing.message_id,
          existing.delivery_status,
        )
    else:
      await self.db.flush()
    return QueuedTradeCommand(client_order_id, message_id, "QUEUED")

  async def enqueue_cancel_for_account(
    self,
    *,
    account_id: str,
    broker_order_id: str,
    idempotency_key: str = "",
    execution_mode: str = "paper",
  ) -> QueuedTradeCommand:
    device = await self._device_for_account(account_id, execution_mode)
    return await self.enqueue_cancel(
      user_id=device.user_id,
      account_id=account_id,
      broker_order_id=broker_order_id,
      idempotency_key=idempotency_key,
      execution_mode=execution_mode,
    )
