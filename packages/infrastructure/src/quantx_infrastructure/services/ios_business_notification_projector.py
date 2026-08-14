"""Project durable account business events into opaque iOS notification rows."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from quantx_domain.clock import utcnow
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRolloutEvent,
  EngineCommandOutbox,
  OperationalAlert,
  PendingTradeOrder,
  StrategyRuntimeEvent,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.models.ios_notifications import (
  IosBusinessNotificationReceipt,
)
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.ios_notification_enqueue_service import (
  IosNotificationEnqueueService,
)

_SOURCE_HMAC_DOMAIN = b"quantx:ios-business-notification-source:v1\0"
_ACTION_DEFAULT_TTL = timedelta(minutes=15)
_ACTION_MAX_TTL = timedelta(hours=1)
_ORDER_TTL = timedelta(hours=24)
_RISK_TTL = timedelta(hours=24)
_AUTOMATION_ERROR_TTL = timedelta(hours=24)
_CONNECTION_DATA_TTL = timedelta(hours=6)

_ROLLOUT_RISK_EVENTS = frozenset(
  {
    "CONTROLLED_WINDOW_INVALIDATED",
    "ENTRIES_PAUSED",
    "KILL_SWITCHED",
  }
)
_EXIT_PLAN_RISK_EVENTS = frozenset(
  {
    "AUTO_EXIT_AUTHORIZATION_INVALIDATED",
  }
)
_AUTOMATION_COMMAND_PREFIXES = (
  "EXIT_PLAN_",
  "FIRST_BOARD_",
  "LIMIT_UP_BOARD_",
  "STRATEGY_",
  "T_TRADE_",
)
_CONNECTION_ALERT_CODES = frozenset({"AGENT_REPORT_DEAD_LETTER"})
_SOURCE_TRADE_INTENT = "TRADE_INTENT"
_SOURCE_RUNTIME_EVENT = "STRATEGY_RUNTIME_EVENT"
_SOURCE_ROLLOUT_EVENT = "ACCOUNT_ROLLOUT_EVENT"
_SOURCE_EXIT_PLAN_EVENT = "AUTO_EXIT_PLAN_EVENT"
_SOURCE_ENGINE_COMMAND = "ENGINE_COMMAND"
_SOURCE_OPERATIONAL_ALERT = "OPERATIONAL_ALERT"


@dataclass(frozen=True)
class BusinessNotificationCandidate:
  source_kind: str
  source_event_id: str
  account_id: str
  category: str
  route_type: str
  occurred_at: datetime
  expires_at: datetime


@dataclass(frozen=True)
class BusinessNotificationProjectionSummary:
  discovered: int = 0
  projected: int = 0
  already_projected: int = 0
  queued: int = 0


def _normalized_text(value: Any) -> str:
  return str(value or "").strip()


def _enum_text(value: Any) -> str:
  return _normalized_text(getattr(value, "value", value)).upper()


def _source_event_hmac(signing_key: bytes, source_key: str) -> str:
  if len(signing_key) < 32:
    raise ValueError("iOS notification projection signing key is unavailable")
  normalized = _normalized_text(source_key)
  if not normalized or len(normalized) > 1024:
    raise ValueError("iOS notification projection source key is invalid")
  return hmac.new(
    signing_key,
    _SOURCE_HMAC_DOMAIN + normalized.encode("utf-8"),
    hashlib.sha256,
  ).hexdigest()


def _not_projected(source_kind: str, source_event_id_column):
  return ~select(IosBusinessNotificationReceipt.source_event_key_hash).where(
    IosBusinessNotificationReceipt.source_kind == source_kind,
    IosBusinessNotificationReceipt.source_event_id == source_event_id_column,
  ).exists()


def _action_expiry(intent: TradeIntentRecord) -> datetime:
  metadata = dict(intent.intent_metadata or {})
  try:
    ttl_ms = int(metadata.get("approval_ttl_ms") or 0)
  except (TypeError, ValueError):
    ttl_ms = 0
  ttl = timedelta(milliseconds=ttl_ms) if ttl_ms > 0 else _ACTION_DEFAULT_TTL
  ttl = min(ttl, _ACTION_MAX_TTL)
  return intent.created_at + ttl


class IosBusinessNotificationProjector:
  """Consume durable sources once and enqueue for preferences active now.

  The global receipt and all per-session event/outbox rows are flushed in the
  caller's transaction. A disabled preference or zero eligible registrations
  still consumes the source, preventing retroactive delivery after opt-in.
  """

  def __init__(
    self,
    db: AsyncSession,
    *,
    signing_key: bytes,
    source_batch_limit: int = 100,
  ) -> None:
    if len(signing_key) < 32:
      raise ValueError("iOS notification projection signing key is unavailable")
    self.db = db
    self._signing_key = signing_key
    self._source_batch_limit = max(1, min(int(source_batch_limit), 500))

  async def project_once(
    self,
    *,
    now: datetime | None = None,
  ) -> BusinessNotificationProjectionSummary:
    projected_at = now or utcnow()
    candidates = await self._load_candidates(projected_at)
    projected = 0
    already_projected = 0
    queued_count = 0
    enqueuer = IosNotificationEnqueueService(self.db)

    for candidate in candidates:
      source_hash = _source_event_hmac(
        self._signing_key,
        f"{candidate.source_kind}:{candidate.source_event_id}",
      )
      if await self.db.get(IosBusinessNotificationReceipt, source_hash):
        already_projected += 1
        continue

      receipt = IosBusinessNotificationReceipt(
        source_event_key_hash=source_hash,
        source_kind=candidate.source_kind,
        source_event_id=candidate.source_event_id,
        account_id=candidate.account_id,
        category=candidate.category,
        occurred_at=candidate.occurred_at,
        expires_at=candidate.expires_at,
        projected_at=projected_at,
        queued_event_count=0,
      )
      try:
        async with self.db.begin_nested():
          self.db.add(receipt)
          await self.db.flush([receipt])
      except IntegrityError:
        already_projected += 1
        continue

      queued = await enqueuer.enqueue_event(
        account_id=candidate.account_id,
        category=candidate.category,
        route_type=candidate.route_type,
        occurred_at=candidate.occurred_at,
        expires_at=candidate.expires_at,
      )
      receipt.queued_event_count = len(queued)
      await self.db.flush()
      projected += 1
      queued_count += len(queued)

    return BusinessNotificationProjectionSummary(
      discovered=len(candidates),
      projected=projected,
      already_projected=already_projected,
      queued=queued_count,
    )

  async def _load_candidates(
    self,
    now: datetime,
  ) -> list[BusinessNotificationCandidate]:
    candidates = [
      *(await self._action_required(now)),
      *(await self._order_updates(now)),
      *(await self._risk_safety(now)),
      *(await self._automation_errors(now)),
      *(await self._connection_data(now)),
    ]
    return sorted(
      candidates,
      key=lambda item: (
        item.occurred_at,
        item.source_kind,
        item.source_event_id,
      ),
    )

  async def _action_required(
    self,
    now: datetime,
  ) -> list[BusinessNotificationCandidate]:
    live_or_paper_plan = select(AutoExitPlanRecord.plan_id).where(
      AutoExitPlanRecord.plan_id == TradeIntentRecord.owner_id,
      AutoExitPlanRecord.account_id == TradeIntentRecord.account_id,
      func.upper(AutoExitPlanRecord.execution_mode).in_(("PAPER", "LIVE")),
    ).exists()
    live_or_paper_run = select(StrategyRun.id).where(
      StrategyRun.id == TradeIntentRecord.strategy_run_id,
      StrategyRun.mode.in_((StrategyRunMode.PAPER, StrategyRunMode.LIVE)),
    ).exists()
    intents = list(
      (
        await self.db.execute(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.status == "AWAITING_APPROVAL",
            TradeIntentRecord.account_id.is_not(None),
            TradeIntentRecord.created_at >= now - _ACTION_MAX_TTL,
            or_(
              and_(
                func.upper(TradeIntentRecord.owner_type) == "EXIT_PLAN",
                live_or_paper_plan,
              ),
              and_(
                func.upper(TradeIntentRecord.owner_type) != "EXIT_PLAN",
                live_or_paper_run,
              ),
            ),
            _not_projected(_SOURCE_TRADE_INTENT, TradeIntentRecord.id),
          )
          .order_by(TradeIntentRecord.created_at.desc(), TradeIntentRecord.id)
          .limit(self._source_batch_limit)
          .with_for_update(skip_locked=True)
        )
      ).scalars()
    )
    candidates: list[BusinessNotificationCandidate] = []
    for intent in intents:
      account_id = _normalized_text(intent.account_id)
      if not account_id:
        continue
      expires_at = _action_expiry(intent)
      if expires_at <= now:
        continue
      candidates.append(
        BusinessNotificationCandidate(
          source_kind=_SOURCE_TRADE_INTENT,
          source_event_id=_normalized_text(intent.id),
          account_id=account_id,
          category="ACTION_REQUIRED",
          route_type="today.action",
          occurred_at=intent.created_at,
          expires_at=expires_at,
        )
      )
    return candidates

  async def _order_updates(
    self,
    now: datetime,
  ) -> list[BusinessNotificationCandidate]:
    rows = (
      await self.db.execute(
        select(StrategyRuntimeEvent, PendingTradeOrder)
        .join(
          PendingTradeOrder,
          PendingTradeOrder.client_order_id
          == StrategyRuntimeEvent.client_order_id,
        )
        .where(
          StrategyRuntimeEvent.application_status == "APPLIED",
          StrategyRuntimeEvent.event_type.in_(("ORDER", "TRADE")),
          StrategyRuntimeEvent.applied_at.is_not(None),
          StrategyRuntimeEvent.applied_at >= now - _ORDER_TTL,
          func.upper(PendingTradeOrder.execution_mode).in_(("PAPER", "LIVE")),
          _not_projected(
            _SOURCE_RUNTIME_EVENT,
            StrategyRuntimeEvent.event_id,
          ),
        )
        .order_by(
          StrategyRuntimeEvent.applied_at,
          StrategyRuntimeEvent.event_id,
        )
        .limit(self._source_batch_limit)
        .with_for_update(skip_locked=True)
      )
    ).all()
    return [
      BusinessNotificationCandidate(
        source_kind=_SOURCE_RUNTIME_EVENT,
        source_event_id=_normalized_text(event.event_id),
        account_id=_normalized_text(order.account_id),
        category="ORDER_UPDATE",
        route_type="trading.orders",
        occurred_at=event.applied_at,
        expires_at=event.applied_at + _ORDER_TTL,
      )
      for event, order in rows
      if _normalized_text(order.account_id)
      and event.applied_at + _ORDER_TTL > now
    ]

  async def _risk_safety(
    self,
    now: datetime,
  ) -> list[BusinessNotificationCandidate]:
    rollout_events = list(
      (
        await self.db.execute(
          select(AccountTradingRolloutEvent)
          .where(
            AccountTradingRolloutEvent.event_type.in_(_ROLLOUT_RISK_EVENTS),
            AccountTradingRolloutEvent.created_at >= now - _RISK_TTL,
            _not_projected(
              _SOURCE_ROLLOUT_EVENT,
              AccountTradingRolloutEvent.event_id,
            ),
          )
          .order_by(
            AccountTradingRolloutEvent.created_at,
            AccountTradingRolloutEvent.event_id,
          )
          .limit(self._source_batch_limit)
          .with_for_update(skip_locked=True)
        )
      ).scalars()
    )
    exit_plan_rows = (
      await self.db.execute(
        select(AutoExitPlanEvent, AutoExitPlanRecord)
        .join(
          AutoExitPlanRecord,
          AutoExitPlanRecord.plan_id == AutoExitPlanEvent.plan_id,
        )
        .where(
          AutoExitPlanEvent.event_type.in_(_EXIT_PLAN_RISK_EVENTS),
          AutoExitPlanEvent.created_at >= now - _RISK_TTL,
          func.upper(AutoExitPlanRecord.execution_mode).in_(("PAPER", "LIVE")),
          _not_projected(
            _SOURCE_EXIT_PLAN_EVENT,
            AutoExitPlanEvent.event_id,
          ),
        )
        .order_by(AutoExitPlanEvent.created_at, AutoExitPlanEvent.event_id)
        .limit(self._source_batch_limit)
        .with_for_update(skip_locked=True)
      )
    ).all()
    candidates = [
      BusinessNotificationCandidate(
        source_kind=_SOURCE_ROLLOUT_EVENT,
        source_event_id=_normalized_text(event.event_id),
        account_id=_normalized_text(event.account_id),
        category="RISK_SAFETY",
        route_type="trading.safety",
        occurred_at=event.created_at,
        expires_at=event.created_at + _RISK_TTL,
      )
      for event in rollout_events
      if _normalized_text(event.account_id)
      and event.created_at + _RISK_TTL > now
    ]
    candidates.extend(
      BusinessNotificationCandidate(
        source_kind=_SOURCE_EXIT_PLAN_EVENT,
        source_event_id=_normalized_text(event.event_id),
        account_id=_normalized_text(plan.account_id),
        category="RISK_SAFETY",
        route_type="trading.safety",
        occurred_at=event.created_at,
        expires_at=event.created_at + _RISK_TTL,
      )
      for event, plan in exit_plan_rows
      if _normalized_text(plan.account_id)
      and event.created_at + _RISK_TTL > now
    )
    return candidates

  async def _automation_errors(
    self,
    now: datetime,
  ) -> list[BusinessNotificationCandidate]:
    account_id_expression = EngineCommandOutbox.payload["account_id"].as_string()
    commands = list(
      (
        await self.db.execute(
          select(EngineCommandOutbox)
          .where(
            EngineCommandOutbox.processing_status == "FAILED",
            EngineCommandOutbox.processed_at.is_not(None),
            EngineCommandOutbox.processed_at >= now - _AUTOMATION_ERROR_TTL,
            or_(
              *(
                EngineCommandOutbox.command_type.startswith(prefix)
                for prefix in _AUTOMATION_COMMAND_PREFIXES
              )
            ),
            account_id_expression.is_not(None),
            func.length(func.trim(account_id_expression)) > 0,
            _not_projected(
              _SOURCE_ENGINE_COMMAND,
              EngineCommandOutbox.message_id,
            ),
          )
          .order_by(
            EngineCommandOutbox.processed_at,
            EngineCommandOutbox.message_id,
          )
          .limit(self._source_batch_limit)
          .with_for_update(skip_locked=True)
        )
      ).scalars()
    )
    candidates: list[BusinessNotificationCandidate] = []
    for command in commands:
      command_type = _enum_text(command.command_type)
      if not command_type.startswith(_AUTOMATION_COMMAND_PREFIXES):
        continue
      account_id = _normalized_text(dict(command.payload or {}).get("account_id"))
      if not account_id or command.processed_at + _AUTOMATION_ERROR_TTL <= now:
        continue
      candidates.append(
        BusinessNotificationCandidate(
          source_kind=_SOURCE_ENGINE_COMMAND,
          source_event_id=_normalized_text(command.message_id),
          account_id=account_id,
          category="AUTOMATION_ERROR",
          route_type="quant.workspace",
          occurred_at=command.processed_at,
          expires_at=command.processed_at + _AUTOMATION_ERROR_TTL,
        )
      )
    return candidates

  async def _connection_data(
    self,
    now: datetime,
  ) -> list[BusinessNotificationCandidate]:
    alerts = list(
      (
        await self.db.execute(
          select(OperationalAlert)
          .where(
            OperationalAlert.code.in_(_CONNECTION_ALERT_CODES),
            OperationalAlert.account_id.is_not(None),
            OperationalAlert.status.in_(("OPEN", "ACKNOWLEDGED")),
            OperationalAlert.last_seen_at >= now - _CONNECTION_DATA_TTL,
            _not_projected(
              _SOURCE_OPERATIONAL_ALERT,
              OperationalAlert.id,
            ),
          )
          .order_by(OperationalAlert.last_seen_at, OperationalAlert.id)
          .limit(self._source_batch_limit)
          .with_for_update(skip_locked=True)
        )
      ).scalars()
    )
    return [
      BusinessNotificationCandidate(
        source_kind=_SOURCE_OPERATIONAL_ALERT,
        source_event_id=_normalized_text(alert.id),
        account_id=_normalized_text(alert.account_id),
        category="CONNECTION_DATA",
        route_type="system.status",
        occurred_at=alert.last_seen_at,
        expires_at=alert.last_seen_at + _CONNECTION_DATA_TTL,
      )
      for alert in alerts
      if _normalized_text(alert.account_id)
      and alert.last_seen_at + _CONNECTION_DATA_TTL > now
    ]


__all__ = [
  "BusinessNotificationCandidate",
  "BusinessNotificationProjectionSummary",
  "IosBusinessNotificationProjector",
]
