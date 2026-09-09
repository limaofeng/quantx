"""Persist user trade intent and enqueue delivery to a registered QMT agent."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from time import monotonic
from typing import Any, Mapping

from quantx_contracts import (
  PROTOCOL_VERSION,
  CancelCommandPayload,
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
  TradeCommandPayload,
)
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.clock import to_naive_utc, utcnow
from quantx_domain.strategies.ashare_managed_entry_plan import (
  ENTRY_PLAN_ENABLED_KEY,
)
from quantx_domain.strategies.base import ExitPlanIntentOrigin, TradeIntent
from quantx_domain.trading.entry_plan import (
  EntryAuthorizationMode,
  EntryEnvironment,
  EntryTargetMode,
  ManagedEntryPlanConfig,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import ContextRiskLayer, TradingRiskChecker
from quantx_domain.trading.t_order_policy import (
  TEntryOrderPolicy,
  TExitOrderPolicy,
  TOrderPolicyResult,
)
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentDevice,
  OrderCorrelation,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.entry_plan_authorization import (
  EntryPlanAuthorizationGrant,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.enums import OrderStatus as PersistedOrderStatus
from quantx_infrastructure.models.enums import OrderType as PersistedOrderType
from quantx_infrastructure.models.execution_owner import validate_owner_environment
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationStatus,
)
from quantx_infrastructure.models.order import Order as PersistedOrder
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_capacity_service import (
  AccountCapacityService,
  buy_cash_required,
)
from quantx_infrastructure.services.account_risk_increase_admission import (
  ADMISSION_LEASE_SECONDS,
  ADMISSION_RENEW_INTERVAL_SECONDS,
  AccountRiskIncreaseAdmissionSequencer,
  AdmissionBatchClaim,
)
from quantx_infrastructure.services.agent_session_guard import (
  evaluate_agent_session,
)
from quantx_infrastructure.services.entry_plan_authorization_service import (
  EntryPlanAuthorizationService,
  scope_from_managed_entry_config,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  T_TRADE_ENTRY_APPROVAL_ACTION,
  T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY,
  build_t_trade_entry_exit_authorization_envelope,
  trade_confirmation_payload_fingerprint,
  validate_consumed_exit_plan_sell_challenge,
  validate_exact_auto_exit_authorization,
)
from quantx_infrastructure.services.market_stream_readiness import (
  authoritative_market_stream_tradable,
)
from quantx_infrastructure.services.t_order_lifecycle_state import (
  t_order_lifecycle_active,
)
from quantx_infrastructure.services.t_trade_batch_metrics import (
  extract_t_trade_cost_snapshot,
)


class AgentUnavailableError(RuntimeError):
  pass


MAX_STABLE_COMMAND_KEY_LENGTH = 128
RISK_ADMISSION_COLLECTION_WINDOW_SECONDS = 0.05


def require_stable_command_key(
  idempotency_key: str | None,
  trace_id: str | None = None,
) -> tuple[str, str, str]:
  """Require a caller-owned key before creating a durable order command.

  ``trace_id`` is accepted only as the explicit correlation key when
  no idempotency key was supplied.  Neither value may be synthesized from a
  request payload or a transport-generated order id.
  """

  if idempotency_key is not None and not isinstance(idempotency_key, str):
    raise AgentUnavailableError("IDEMPOTENCY_KEY_INVALID:稳定幂等键必须是字符串")
  if trace_id is not None and not isinstance(trace_id, str):
    raise AgentUnavailableError("TRACE_ID_INVALID:稳定追踪键必须是字符串")
  normalized_idempotency_key = (idempotency_key or "").strip()
  normalized_trace_id = (trace_id or "").strip()
  for field_name, value in (
    ("IDEMPOTENCY_KEY", normalized_idempotency_key),
    ("TRACE_ID", normalized_trace_id),
  ):
    if len(value) > MAX_STABLE_COMMAND_KEY_LENGTH:
      raise AgentUnavailableError(
        f"{field_name}_INVALID:稳定键不得超过 "
        f"{MAX_STABLE_COMMAND_KEY_LENGTH} 个字符"
      )
  stable_key = normalized_idempotency_key or normalized_trace_id
  if not stable_key:
    raise AgentUnavailableError(
      "IDEMPOTENCY_KEY_REQUIRED:必须提供稳定 idempotency_key 或 trace_id"
    )
  return normalized_idempotency_key, normalized_trace_id, stable_key


_AUTHORITATIVE_ENTRY_WORKING_STATUSES = (
  PersistedOrderStatus.UNREPORTED,
  PersistedOrderStatus.WAIT_REPORTING,
  PersistedOrderStatus.REPORTED,
  PersistedOrderStatus.REPORTED_CANCEL,
  PersistedOrderStatus.PARTSUCC_CANCEL,
  PersistedOrderStatus.PART_SUCC,
  PersistedOrderStatus.UNKNOWN,
)

_AUTHORITATIVE_ENTRY_TERMINAL_STATUSES = {
  int(PersistedOrderStatus.PART_CANCEL),
  int(PersistedOrderStatus.CANCELED),
  int(PersistedOrderStatus.SUCCEEDED),
  int(PersistedOrderStatus.JUNK),
}

_ROUTABLE_EXIT_PLAN_STATUSES = frozenset(
  {"ACTIVE", "PARTIALLY_EXITED", "EXIT_PENDING"}
)
_REGISTERED_RUNTIME_OWNER_TYPES = frozenset(
  {
    ExecutionOwnerType.STRATEGY_RUN.value,
    ExecutionOwnerType.T_ASSISTANT_EXECUTION.value,
    ExecutionOwnerType.EXIT_PLAN.value,
    ExecutionOwnerType.MANUAL_COMMAND.value,
  }
)

# ``request_metadata`` is an audit/evidence projection, not a second command
# identity.  Identity fields belong to the typed columns/arguments below and
# are rejected at this boundary instead of being copied into JSON and later
# used as a routing fallback.
_REQUEST_METADATA_IDENTITY_KEYS = frozenset(
  {
    "owner_type",
    "owner_id",
    "source_execution_owner_type",
    "source_execution_owner_id",
    "source_execution_environment",
    "environment",
    "execution_environment",
    "execution_mode",
    "strategy_run_id",
    "strategy_order_id",
    "intent_id",
    "batch_id",
    "t_batch_id",
    "idempotency_key",
    "client_order_id",
    "broker_order_id",
    "account_id",
    "instrument_code",
    "exit_plan_id",
    "order_remark",
    "strategy_name",
  }
)
_REQUEST_METADATA_ALLOWLIST = frozenset(
  {
    "origin",
    "challenge_id",
    "payload_fingerprint",
    "quote_timestamp",
    "quote_fingerprint",
    "live_entry_review_event_key",
    "portfolio_input_fingerprint",
    "reference_price",
    "requested_volume",
    "final_volume",
    "requested_entry_volume",
    "rollout_snapshot_id",
    "rollout_snapshot_hash",
    "rollout_snapshot_at",
    "account_updated_at",
    "position_updated_at",
    "instrument_updated_at",
    "position_snapshot_sequence",
    "position_snapshot_source",
    "position_snapshot_reported_at",
    "position_snapshot_received_at",
    "position_snapshot_count",
    "total_asset_cny",
    "position_volume",
    "market_value_cny",
    "account_snapshot_version",
    "risk_action",
    "risk_decision_id",
    "risk_reason_code",
    "risk_reason_detail",
    "risk_tags",
    "reason_tags",
    "substitution_plan",
    "config_version",
    "entry_config_version",
    "entry_plan_id",
    "entry_plan_fingerprint",
    "entry_rule_fingerprint",
    "auto_entry_authorization_grant_id",
    "auto_entry_plan_fingerprint",
    "auto_entry_rule_fingerprint",
    "exact_auto_entry_authorized",
    "protected_limit_price",
    "price_type",
    "price_reference",
    "protected_limit",
    "max_exit_slippage_bps",
    "exit_plan_template",
    "exit_reason",
    "exit_rule_id",
    "exit_policy_version",
    "exit_policy_fingerprint",
    "exit_rule_fingerprint",
    "exit_plan_source_type",
    "exit_plan_source_id",
    "auto_exit_authorization_code",
    "auto_exit_authorization_fingerprint",
    "auto_exit_authorization_challenge_id",
    "auto_exit_authorization_user_id",
    "auto_exit_authorization_device_session_id",
    "auto_exit_authorized_at",
    "auto_exit_authorization_expires_at",
    "exit_plan_approval_challenge_id",
    "exit_plan_approval_user_id",
    "exit_plan_approval_device_session_id",
    "exit_plan_approval_channel",
    "exact_auto_exit_authorized",
    "policy_version",
    "account_capacity",
    "execution_terminal_source",
    "execution_terminal_reason",
    "reconciled_zero_fill_intent_id",
    "managed_runtime_command_id",
    "conditional_order_id",
    "runtime_event_key",
    "entry_stage_id",
    "entry_plan_note",
    "commission_rate",
    "minimum_commission",
    "min_commission",
    "stamp_tax_rate",
    "transfer_fee_rate",
    "t_entry_order_policy_version",
    "t_exit_order_policy_version",
    "t_exit_order_ttl_seconds",
    "t_exit_total_ttl_seconds",
    "t_exit_max_replace_count",
    "t_exit_max_slippage_bps",
    "t_order_reference_price",
    "t_order_price_tick",
    "t_order_limit_up",
    "t_order_limit_down",
    "t_order_cage_upper",
    "t_order_cage_lower",
    "t_order_original_created_at",
    "t_order_replace_count",
  }
)
_GENERATED_ORDER_METADATA_KEYS = frozenset(
  {
    "account_capacity",
    "admission_batch_id",
    "admission_rank",
    "admission_policy_version",
    "admission_input_fingerprint",
    "t_order_original_created_at",
    "t_order_replace_count",
  }
)


@dataclass(frozen=True)
class QueuedTradeCommand:
  client_order_id: str
  message_id: str
  status: str


@dataclass(frozen=True)
class StrategyOrderCancelRequest:
  client_order_id: str
  strategy_order_id: str
  intent_id: str
  broker_order_id: str
  status: str
  request_metadata: dict[str, Any]
  local_terminal: bool = False


class TradeCommandService:
  MANUAL_RECONCILIATION_MAX_AGE_SECONDS = 90

  def __init__(self, db: AsyncSession, *, live_entry_review=None) -> None:
    self.db = db
    self.live_entry_review = live_entry_review

  @staticmethod
  def _require_execution_identity(
    execution_ref: ExecutionOwnerRef,
    environment: ExecutionEnvironment,
  ) -> tuple[ExecutionOwnerRef, ExecutionEnvironment, str, str, str]:
    """Validate the one explicit identity accepted by command creation.

    Owner and environment are deliberately not reconstructed from request
    metadata, legacy run fields, traces, buckets, or caller flags.  The
    command boundary receives the already validated value objects and only
    projects them to their canonical durable/wire representations here.
    """

    if not isinstance(execution_ref, ExecutionOwnerRef):
      raise ValueError("EXECUTION_OWNER_REQUIRED")
    if not isinstance(environment, ExecutionEnvironment):
      raise ValueError("EXECUTION_ENVIRONMENT_REQUIRED")
    owner_type, owner_id, canonical_environment = validate_owner_environment(
      execution_ref.owner_type,
      execution_ref.owner_id,
      environment,
    )
    # The value-object and persistence validators intentionally share the same
    # closed enum.  Keep this assertion explicit so a future enum change cannot
    # silently create a mixed identity representation.
    if owner_type is None or owner_id is None or canonical_environment is None:
      raise ValueError("EXECUTION_OWNER_REQUIRED")
    canonical_ref = ExecutionOwnerRef(owner_type, owner_id)
    canonical_env = ExecutionEnvironment(canonical_environment)
    return (
      canonical_ref,
      canonical_env,
      canonical_ref.owner_type.value,
      canonical_ref.owner_id,
      canonical_env.value,
    )

  @staticmethod
  def _wire_execution_mode(environment: ExecutionEnvironment) -> str:
    """Map canonical durable environment to the lower-case wire value."""

    if environment not in {
      ExecutionEnvironment.PAPER,
      ExecutionEnvironment.LIVE,
    }:
      raise ValueError("交易命令仅支持 PAPER 或 LIVE execution environment")
    return environment.value.lower()

  @staticmethod
  def _wire_price_type(order_type: Any) -> str:
    """Validate the sole current-protocol order price type."""

    value = str(getattr(order_type, "value", order_type) or "").strip().upper()
    if value != "FIX_PRICE":
      raise AgentUnavailableError(
        f"协议 {PROTOCOL_VERSION} PLACE_ORDER 仅支持 FIX_PRICE 限价委托"
      )
    return value

  @staticmethod
  def _normalize_strategy_identity(
    owner_type: str,
    owner_id: str,
    strategy_run_id: str | None,
    strategy_order_id: str | None,
  ) -> tuple[str, str]:
    """Normalize the legacy strategy witness columns for one owner.

    ``strategy_run_id`` and ``strategy_order_id`` are a pair of denormalized
    witnesses for ``STRATEGY_RUN`` only.  EXIT_PLAN and MANUAL_COMMAND use
    their typed owner (and, for EXIT_PLAN, ``intent_id``) as the durable
    identity; accepting either strategy witness for those owners would create
    a row that cannot satisfy the database identity constraint.
    """

    normalized_run_id = str(strategy_run_id or "").strip()
    normalized_order_id = str(strategy_order_id or "").strip()
    if owner_type == ExecutionOwnerType.STRATEGY_RUN.value:
      if normalized_run_id and normalized_run_id != owner_id:
        raise AgentUnavailableError(
          "TRADE_COMMAND_OWNER_CONFLICT:显式 owner 与 strategy_run_id 不一致"
        )
      if not normalized_order_id:
        raise AgentUnavailableError(
          "TRADE_COMMAND_OWNER_CONFLICT:STRATEGY_RUN 命令必须绑定 strategy_order_id"
        )
      return owner_id, normalized_order_id
    if normalized_run_id or normalized_order_id:
      raise AgentUnavailableError(
        "TRADE_COMMAND_OWNER_CONFLICT:非 STRATEGY_RUN 命令不得携带 strategy_run_id/strategy_order_id"
      )
    return "", ""

  @staticmethod
  def _sanitize_request_metadata(
    metadata: Mapping[str, Any] | None,
  ) -> dict[str, Any]:
    """Validate the non-identity JSON projection at the command boundary."""

    if metadata is None:
      return {}
    if not isinstance(metadata, Mapping):
      raise AgentUnavailableError("TRADE_COMMAND_METADATA_INVALID:必须是对象")
    rejected = sorted(
      {
        str(key)
        for key in metadata
        if not isinstance(key, str)
        or key in _REQUEST_METADATA_IDENTITY_KEYS
        or key in _GENERATED_ORDER_METADATA_KEYS
        or key not in _REQUEST_METADATA_ALLOWLIST
      }
    )
    if rejected:
      raise AgentUnavailableError(
        "TRADE_COMMAND_METADATA_KEY_REJECTED:" + ",".join(rejected)
      )
    return dict(metadata)

  @staticmethod
  def order_idempotency_digest(
    *,
    user_id: str,
    account_id: str,
    idempotency_key: str,
    execution_ref: ExecutionOwnerRef,
    environment: ExecutionEnvironment,
  ) -> str:
    """Return the owner-scoped key used to recover queued order results."""

    _, _, normalized_key = require_stable_command_key(idempotency_key)
    _canonical_ref, _canonical_env, owner_type, owner_id, environment_value = (
      TradeCommandService._require_execution_identity(execution_ref, environment)
    )

    return hashlib.sha256(
      (
        f"order:{user_id}:{account_id}:{environment_value}:"
        f"{owner_type}:{owner_id}:{normalized_key}"
      ).encode("utf-8")
    ).hexdigest()

  @staticmethod
  def _idempotent_order_matches_request(
    existing: TradeCommandOutbox,
    *,
    account_id: str,
    owner_type: str,
    owner_id: str,
    environment: str,
    instrument_code: str,
    side: str,
    order_type: str,
    limit_price: Decimal,
    volume: int,
  ) -> bool:
    """Prove that an idempotent retry is the same immutable order request."""

    if (
      str(getattr(existing, "account_id", "") or "") != account_id
      or str(getattr(existing, "owner_type", "") or "") != owner_type
      or str(getattr(existing, "owner_id", "") or "") != owner_id
      or str(getattr(existing, "environment", "") or "") != environment
    ):
      return False
    payload = dict(getattr(existing, "payload", None) or {})
    if set(payload) != {
      "command_kind",
      "client_order_id",
      "account_id",
      "execution_mode",
      "instrument_code",
      "side",
      "price_type",
      "limit_price",
      "volume",
      "expires_at",
    }:
      return False
    if (
      str(payload.get("command_kind") or "").strip().upper()
      != "PLACE_ORDER"
      or str(payload.get("client_order_id") or "")
      != str(getattr(existing, "client_order_id", "") or "")
      or str(payload.get("account_id") or "") != account_id
      or str(payload.get("execution_mode") or "").strip().upper()
      != environment
      or str(payload.get("instrument_code") or "").strip().upper()
      != instrument_code
      or str(payload.get("side") or "").strip().upper() != side
      or str(payload.get("price_type") or "").strip().upper() != order_type
    ):
      return False
    try:
      validated_payload = TradeCommandPayload.model_validate(payload)
      existing_limit_price = Decimal(str(payload.get("limit_price")))
      existing_volume = int(payload.get("volume"))
      payload_expires_at = validated_payload.expires_at
      durable_expires_at = existing.expires_at
      if durable_expires_at.tzinfo is None:
        durable_expires_at = durable_expires_at.replace(tzinfo=timezone.utc)
      else:
        durable_expires_at = durable_expires_at.astimezone(timezone.utc)
      if payload_expires_at.tzinfo is None:
        payload_expires_at = payload_expires_at.replace(tzinfo=timezone.utc)
      else:
        payload_expires_at = payload_expires_at.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, ArithmeticError):
      return False
    return (
      existing_limit_price == limit_price
      and existing_volume == volume
      and payload_expires_at == durable_expires_at
    )

  async def _idempotent_order_chain_matches_request(
    self,
    existing: TradeCommandOutbox,
    *,
    user_id: str,
    account_id: str,
    owner_type: str,
    owner_id: str,
    environment: str,
    instrument_code: str,
    side: str,
    order_type: str,
    limit_price: Decimal,
    volume: int,
    strategy_run_id: str,
    strategy_order_id: str,
    intent_id: str,
    batch_id: str,
    bucket: str,
    t_trade_role: str,
    risk_decision_id: str,
    trace_id: str,
    substitution_plan: Mapping[str, Any] | None,
    request_metadata: Mapping[str, Any],
  ) -> bool:
    """Prove an idempotent retry is the same complete durable command chain."""

    try:
      normalized_strategy_run_id, normalized_strategy_order_id = (
        self._normalize_strategy_identity(
          owner_type,
          owner_id,
          strategy_run_id,
          strategy_order_id,
        )
      )
    except AgentUnavailableError:
      return False

    if not self._idempotent_order_matches_request(
      existing,
      account_id=account_id,
      owner_type=owner_type,
      owner_id=owner_id,
      environment=environment,
      instrument_code=instrument_code,
      side=side,
      order_type=order_type,
      limit_price=limit_price,
      volume=volume,
    ):
      return False
    pending_rows = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(PendingTradeOrder.client_order_id == existing.client_order_id)
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    correlation_rows = list(
      (
        await self.db.execute(
          select(OrderCorrelation)
          .where(OrderCorrelation.client_order_id == existing.client_order_id)
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    if len(pending_rows) != 1 or len(correlation_rows) != 1:
      return False
    pending = pending_rows[0]
    correlation = correlation_rows[0]
    normalized_intent_id = str(intent_id or "").strip()
    normalized_batch_id = str(batch_id or "").strip()
    normalized_bucket = str(bucket or "manual").strip()
    normalized_role = str(t_trade_role or "").strip().upper()
    normalized_risk_decision_id = str(risk_decision_id or "").strip()
    if (
      str(pending.user_id or "") != str(user_id or "")
      or str(pending.account_id or "") != account_id
      or str(pending.owner_type or "").strip().upper() != owner_type
      or str(pending.owner_id or "").strip() != owner_id
      or str(pending.environment or "").strip().upper() != environment
      or str(pending.instrument_code or "").strip().upper() != instrument_code
      or str(pending.side or "").strip().upper() != side
      or str(pending.order_type or "").strip().upper() != order_type
      or Decimal(str(pending.limit_price)) != limit_price
      or int(pending.volume or 0) != volume
      or str(pending.strategy_run_id or "").strip() != normalized_strategy_run_id
      or str(pending.strategy_order_id or "").strip() != normalized_strategy_order_id
      or str(pending.intent_id or "").strip() != normalized_intent_id
      or str(pending.batch_id or "").strip() != normalized_batch_id
      or str(pending.bucket or "manual").strip() != normalized_bucket
      or str(pending.t_trade_role or "").strip().upper() != normalized_role
      or str(pending.risk_decision_id or "").strip() != normalized_risk_decision_id
      or dict(pending.substitution_plan or {}) != dict(substitution_plan or {})
    ):
      return False
    if (
      str(correlation.client_order_id or "") != str(pending.client_order_id or "")
      or str(correlation.account_id or "") != account_id
      or str(correlation.owner_type or "").strip().upper() != owner_type
      or str(correlation.owner_id or "").strip() != owner_id
      or str(correlation.environment or "").strip().upper() != environment
      or str(correlation.strategy_run_id or "").strip()
      != normalized_strategy_run_id
      or str(correlation.strategy_order_id or "").strip()
      != normalized_strategy_order_id
      or str(correlation.intent_id or "").strip() != normalized_intent_id
      or str(correlation.batch_id or "").strip() != normalized_batch_id
      or str(correlation.bucket or "manual").strip() != normalized_bucket
      or str(correlation.t_trade_role or "").strip().upper() != normalized_role
      or str(correlation.risk_decision_id or "").strip()
      != normalized_risk_decision_id
      or str(correlation.trace_id or "").strip()
      != str(pending.trace_id or "").strip()
      or dict(correlation.substitution_plan or {})
      != dict(pending.substitution_plan or {})
      or dict(correlation.request_metadata or {})
      != dict(pending.request_metadata or {})
    ):
      return False
    if trace_id and str(pending.trace_id or "").strip() != str(trace_id).strip():
      return False
    expected_metadata = dict(request_metadata or {})
    stored_metadata = dict(pending.request_metadata or {})
    for key in set(expected_metadata) | set(stored_metadata):
      if key in _GENERATED_ORDER_METADATA_KEYS:
        continue
      if stored_metadata.get(key) != expected_metadata.get(key):
        return False
    if normalized_intent_id:
      intent = await self.db.get(
        TradeIntentRecord,
        normalized_intent_id,
        with_for_update=True,
        populate_existing=True,
      )
      if intent is None:
        return False
      intent_strategy_run_id = str(intent.strategy_run_id or "").strip()
      if intent_strategy_run_id and (
        owner_type != ExecutionOwnerType.STRATEGY_RUN.value
        or intent_strategy_run_id != owner_id
      ):
        return False
      if (
        str(intent.id or "") != normalized_intent_id
        or str(intent.owner_type or "").strip().upper() != owner_type
        or str(intent.owner_id or "").strip() != owner_id
        or str(intent.environment or "").strip().upper() != environment
        or str(intent.account_id or "") != account_id
        or str(intent.instrument_code or "").strip().upper() != instrument_code
        or str(intent.direction or "").strip().upper() != side
        or str(intent.bucket or "").strip() != normalized_bucket
      ):
        return False
    elif owner_type != ExecutionOwnerType.MANUAL_COMMAND.value:
      return False
    return True

  @staticmethod
  def _cancel_attempt_matches_request(
    attempt: TradeCommandOutbox,
    *,
    account_id: str,
    owner_type: str,
    owner_id: str,
    environment: str,
    broker_order_id: str,
  ) -> bool:
    """Prove one immutable CANCEL attempt targets the requested order."""

    payload = dict(getattr(attempt, "payload", None) or {})
    if set(payload) != {
      "command_kind",
      "client_order_id",
      "account_id",
      "execution_mode",
      "broker_order_id",
      "expires_at",
    }:
      return False
    if (
      str(getattr(attempt, "account_id", "") or "") != account_id
      or str(getattr(attempt, "owner_type", "") or "").strip().upper()
      != owner_type
      or str(getattr(attempt, "owner_id", "") or "").strip() != owner_id
      or str(getattr(attempt, "environment", "") or "").strip().upper()
      != environment
      or str(payload.get("command_kind") or "").strip().upper()
      != "CANCEL_ORDER"
      or str(payload.get("client_order_id") or "")
      != str(getattr(attempt, "client_order_id", "") or "")
      or str(payload.get("account_id") or "") != account_id
      or str(payload.get("execution_mode") or "").strip().lower()
      != environment.lower()
      or str(payload.get("broker_order_id") or "").strip() != broker_order_id
    ):
      return False
    try:
      payload_expires_at = datetime.fromisoformat(
        str(payload.get("expires_at") or "").strip().replace("Z", "+00:00")
      )
      if payload_expires_at.tzinfo is None:
        payload_expires_at = payload_expires_at.replace(tzinfo=timezone.utc)
      else:
        payload_expires_at = payload_expires_at.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
      return False
    durable_expires_at = attempt.expires_at
    if durable_expires_at.tzinfo is None:
      durable_expires_at = durable_expires_at.replace(tzinfo=timezone.utc)
    else:
      durable_expires_at = durable_expires_at.astimezone(timezone.utc)
    return payload_expires_at == durable_expires_at

  @staticmethod
  def _heartbeat_fresh(
    heartbeat: RuntimeComponentHeartbeat,
    *,
    acceptable_statuses: set[str] | None = None,
  ) -> bool:
    return evaluate_agent_session(
      heartbeat,
      now=utcnow(),
      acceptable_statuses=acceptable_statuses or {"READY"},
    ).current

  async def _require_live_authorization(
    self,
    account_id: str,
    *,
    risk_reducing: bool = False,
  ) -> AccountExecutionControl:
    return await self._require_manual_live_authorization(
      account_id,
      risk_reducing=risk_reducing,
      require_controlled_window=False,
    )

  async def _require_live_market_stream_ready(self, device: AgentDevice) -> None:
    heartbeat = await self.db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device.id}",
    )
    details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    if (
      str(details.get("marketStreamStatus") or "").upper() != "READY"
      or not await authoritative_market_stream_tradable()
    ):
      raise AgentUnavailableError("当前不具备交易时段内的新鲜权威全市场行情")

  async def _preview_live_authorization(
    self,
    account_id: str,
    *,
    risk_reducing: bool,
    require_controlled_window: bool = False,
  ) -> AccountExecutionControl:
    """Validate non-locking gates before a BUY enters durable admission."""

    from quantx_contracts.runtime_environment import live_runtime_allowed

    if not live_runtime_allowed(settings.environment):
      raise AgentUnavailableError("当前平台或应用环境禁止真实交易")
    if not settings.enable_real_trading:
      raise AgentUnavailableError("服务端真实交易总开关未启用")
    if account_id not in set(settings.real_trading_account_allowlist or []):
      raise AgentUnavailableError("账户不在服务端真实交易白名单")
    control = await self.db.get(
      AccountExecutionControl,
      account_id,
      populate_existing=True,
    )
    if control is None:
      raise AgentUnavailableError("账户尚未配置独立执行控制与对账状态")
    state = str(control.authorization_state or "DISABLED").upper()
    if not risk_reducing and state == "KILLED":
      raise AgentUnavailableError("账户交易 kill switch 已触发，禁止买入或加仓")
    if not risk_reducing and state != "ENABLED":
      raise AgentUnavailableError("账户买入权限未启用")
    if str(control.reconcile_status or "").upper() != "READY":
      raise AgentUnavailableError("账户资金、持仓、委托和成交快照尚未完成对账")
    snapshot_id = str(control.last_snapshot_id or "")
    snapshot_hash = str(control.last_snapshot_hash or "")
    snapshot_at = (
      to_naive_utc(control.last_snapshot_at)
      if control.last_snapshot_at is not None
      else None
    )
    snapshot_age = (
      (utcnow() - snapshot_at).total_seconds() if snapshot_at is not None else None
    )
    if (
      not snapshot_id
      or len(snapshot_hash) != 64
      or snapshot_age is None
      or snapshot_age < 0
      or snapshot_age > self.MANUAL_RECONCILIATION_MAX_AGE_SECONDS
    ):
      raise AgentUnavailableError("账户完整对账快照缺失或已超过 90 秒")
    if require_controlled_window and not risk_reducing:
      if not bool(control.controlled_window_active):
        raise AgentUnavailableError("手动买入需要基于最新快照建立账户实盘窗口")
      if (
        str(control.controlled_window_snapshot_id or "") != snapshot_id
        or str(control.controlled_window_snapshot_hash or "") != snapshot_hash
      ):
        raise AgentUnavailableError("账户实盘窗口快照与最新完整快照不一致")
    return control

  async def _require_manual_live_authorization(
    self,
    account_id: str,
    *,
    risk_reducing: bool,
    require_controlled_window: bool = True,
  ) -> AccountExecutionControl:
    """Lock and validate the account gate for a confirmed manual live order.

    The account control row is the first mutable trading row locked by both
    this path and ``AccountExecutionSafetyService.set_authorization_state``.
    If enqueue wins, a hard kill subsequently scans and cancels the new pending
    command; if the hard kill wins, a BUY observes the killed state here and is
    rejected before any outbox row is created.

    Risk-reducing SELL orders deliberately do not require an active controlled
    window, CANARY/LIVE enablement, or policy acknowledgement.  This preserves
    an escape path while paused or killed, but still requires a current,
    authoritative reconciliation snapshot and a ready live device.
    """

    from quantx_contracts.runtime_environment import live_runtime_allowed

    if not live_runtime_allowed(settings.environment):
      raise AgentUnavailableError("当前平台或应用环境禁止真实交易")
    if not settings.enable_real_trading:
      raise AgentUnavailableError("服务端真实交易总开关未启用")
    if account_id not in set(settings.real_trading_account_allowlist or []):
      raise AgentUnavailableError("账户不在服务端真实交易白名单")
    control = await self.db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
      populate_existing=True,
    )
    if control is None:
      raise AgentUnavailableError("账户尚未配置独立执行控制与对账状态")

    state = str(control.authorization_state or "DISABLED").upper()
    if not risk_reducing and state == "KILLED":
      raise AgentUnavailableError("账户交易 kill switch 已触发，禁止买入或加仓")
    if not risk_reducing and state != "ENABLED":
      raise AgentUnavailableError("账户买入权限未启用")
    if str(control.reconcile_status or "").upper() != "READY":
      raise AgentUnavailableError("账户资金、持仓、委托和成交快照尚未完成对账")

    snapshot_id = str(control.last_snapshot_id or "")
    snapshot_hash = str(control.last_snapshot_hash or "")
    snapshot_at = (
      to_naive_utc(control.last_snapshot_at)
      if control.last_snapshot_at is not None
      else None
    )
    snapshot_age = (
      (utcnow() - snapshot_at).total_seconds() if snapshot_at is not None else None
    )
    if (
      not snapshot_id
      or len(snapshot_hash) != 64
      or snapshot_age is None
      or snapshot_age < 0
      or snapshot_age > self.MANUAL_RECONCILIATION_MAX_AGE_SECONDS
    ):
      raise AgentUnavailableError("账户完整对账快照缺失或已超过 90 秒")

    if risk_reducing or not require_controlled_window:
      return control

    if not bool(control.controlled_window_active):
      raise AgentUnavailableError("手动买入需要基于最新快照建立账户实盘窗口")
    controlled_snapshot_id = str(control.controlled_window_snapshot_id or "")
    controlled_snapshot_hash = str(control.controlled_window_snapshot_hash or "")
    if (
      controlled_snapshot_id != snapshot_id or controlled_snapshot_hash != snapshot_hash
    ):
      raise AgentUnavailableError("账户实盘窗口快照与最新完整快照不一致")
    return control

  @staticmethod
  def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()

  async def _lock_and_validate_live_exit_plan_sell(
    self,
    *,
    locked_intent: TradeIntentRecord,
    plan_id: str,
    intent_id: str,
    execution_ref: ExecutionOwnerRef,
    environment: ExecutionEnvironment,
    account_id: str,
    instrument_code: str,
    volume: int,
    request_metadata: Mapping[str, Any],
    allow_queued_intent_projection: bool = False,
  ) -> tuple[
    AutoExitPlanRecord,
    TradeIntentRecord,
    Position,
    dict[str, Any],
  ]:
    """Lock the final durable authority for one live EXIT_PLAN SELL.

    A consumed device challenge or exact-auto grant authorizes only a bound
    plan intent.  It is not a reservation against later plan/position state.
    These three rows therefore stay locked in the enqueue transaction until
    the PendingTradeOrder and TradeCommandOutbox rows commit.
    """

    normalized_plan_id = str(plan_id or "").strip()
    normalized_intent_id = str(intent_id or "").strip()
    normalized_account_id = str(account_id or "").strip()
    normalized_instrument = str(instrument_code or "").strip().upper()
    canonical_ref, canonical_environment, owner_type, owner_id, _ = (
      self._require_execution_identity(execution_ref, environment)
    )
    if not normalized_plan_id or not normalized_intent_id:
      raise AgentUnavailableError("退出计划卖单缺少精确计划或意图绑定")

    if (
      canonical_ref.owner_type is not ExecutionOwnerType.EXIT_PLAN
      or owner_id != normalized_plan_id
      or canonical_environment is not ExecutionEnvironment.LIVE
    ):
      raise AgentUnavailableError("退出计划卖单必须使用 LIVE EXIT_PLAN 所有权")

    if str(locked_intent.id or "") != normalized_intent_id:
      raise AgentUnavailableError("退出计划卖单意图锁与请求绑定不匹配")

    plan = await self.db.scalar(
      select(AutoExitPlanRecord)
      .where(AutoExitPlanRecord.plan_id == normalized_plan_id)
      .with_for_update()
      .execution_options(populate_existing=True)
    )
    intent = locked_intent
    position = await self.db.scalar(
      select(Position)
      .where(
        Position.account_id == normalized_account_id,
        Position.stock_code == normalized_instrument,
      )
      .with_for_update()
      .execution_options(populate_existing=True)
    )
    if plan is None or intent is None or position is None:
      raise AgentUnavailableError("退出计划卖单缺少计划、意图或最新持仓")

    intent_metadata = dict(intent.intent_metadata or {})
    plan_state = dict(plan.plan_state or {})
    exact_auto = bool(intent_metadata.get("exact_auto_exit_authorized"))
    expected_intent_status = "PENDING" if exact_auto else "APPROVED"
    allowed_intent_statuses = {expected_intent_status}
    if allow_queued_intent_projection:
      # TradingService returns only after the exact Pending/outbox rows commit.
      # TradeIntentProcessor then projects that accepted command as QUEUED in a
      # separate transaction, which may win the race with the physical writer.
      # The physical gate has already re-locked and matched those durable rows,
      # so QUEUED is the only legitimate post-enqueue status accepted here.
      allowed_intent_statuses.add("QUEUED")
    plan_status = str(plan.status or "").strip().upper()
    target_volume = int(intent.target_volume or 0)
    remaining_volume = max(0, int(plan.remaining_volume or 0))
    available_volume = max(0, int(position.can_use_volume or 0))
    position_volume = max(0, int(position.volume or 0))
    owner_binding_invalid = (
      owner_type != "EXIT_PLAN"
      or owner_id != normalized_plan_id
      or str(intent.owner_type or "").strip().upper() != "EXIT_PLAN"
      or str(intent.owner_id or "").strip() != normalized_plan_id
      or str(getattr(intent, "environment", "")).strip().upper()
      != canonical_environment.value
    )
    identity_invalid = (
      str(plan.account_id or "") != normalized_account_id
      or str(plan.instrument_code or "").strip().upper()
      != normalized_instrument
      or str(plan.environment or "").strip().upper() != "LIVE"
      or str(intent.account_id or "") != normalized_account_id
      or str(intent.instrument_code or "").strip().upper()
      != normalized_instrument
      or str(intent.direction or "").strip().upper() != "SELL"
      or str(intent.status or "").strip().upper()
      not in allowed_intent_statuses
    )
    plan_not_routable = (
      not bool(plan.enabled)
      or plan_status not in _ROUTABLE_EXIT_PLAN_STATUSES
      or str(plan_state.get("pending_intent_id") or "").strip()
      != normalized_intent_id
    )
    volume_invalid = (
      int(volume) <= 0
      or target_volume <= 0
      or int(volume) > target_volume
      or int(volume) > remaining_volume
      or int(volume) > available_volume
      or int(volume) > position_volume
    )
    if owner_binding_invalid:
      raise AgentUnavailableError("退出计划卖单所有权绑定已变化")
    if identity_invalid:
      raise AgentUnavailableError("退出计划卖单账户、标的、方向或状态已变化")
    if plan_not_routable:
      raise AgentUnavailableError("退出计划已暂停、终止或 pending 意图已变化")
    if volume_invalid:
      raise AgentUnavailableError("退出计划委托量超过意图目标、计划剩余量或最新可卖量")
    return plan, intent, position, intent_metadata

  async def validate_locked_live_exit_plan_place_for_delivery(
    self,
    command: TradeCommandOutbox,
  ) -> None:
    """Revalidate a claimed EXIT_PLAN SELL at the physical-send boundary.

    The caller already owns AccountExecutionControl -> PLACE outbox.  This
    method continues the one lifecycle order with Pending -> Intent -> Plan ->
    Position and rejects a cached envelope whose durable quantity or owner has
    changed since enqueue.
    """

    payload = dict(command.payload or {})
    if not (
      str(payload.get("command_kind") or "").strip().upper() == "PLACE_ORDER"
      and str(payload.get("execution_mode") or "").strip().lower() == "live"
      and str(payload.get("side") or "").strip().upper() == "SELL"
    ):
      raise AgentUnavailableError("物理投递门禁仅接受 LIVE PLACE SELL")
    client_order_id = str(payload.get("client_order_id") or "").strip()
    pending = await self.db.get(
      PendingTradeOrder,
      client_order_id,
      with_for_update=True,
      populate_existing=True,
    )
    if pending is None:
      raise AgentUnavailableError("卖单物理投递缺少 Pending 投影")
    command_environment = str(getattr(command, "environment", "") or "").upper()
    command_owner_type = str(getattr(command, "owner_type", "") or "").upper()
    command_owner_id = str(getattr(command, "owner_id", "") or "")
    pending_environment = str(getattr(pending, "environment", "") or "").upper()
    pending_owner_type = str(getattr(pending, "owner_type", "") or "").upper()
    pending_owner_id = str(getattr(pending, "owner_id", "") or "")
    intent_id = str(getattr(pending, "intent_id", "") or "").strip()
    if (
      command_environment != "LIVE"
      or pending_environment != command_environment
      or pending_owner_type != command_owner_type
      or pending_owner_id != command_owner_id
      or not command_owner_type
      or not command_owner_id
    ):
      raise AgentUnavailableError("卖单物理投递 owner/environment 绑定已变化")
    try:
      requested_volume = int(payload.get("volume") or 0)
    except (TypeError, ValueError, OverflowError) as exc:
      raise AgentUnavailableError("卖单物理投递数量无效") from exc
    if not (
      str(command.client_order_id or "") == client_order_id
      and str(command.account_id or "") == str(payload.get("account_id") or "")
      and str(pending.client_order_id or "") == client_order_id
      and str(pending.account_id or "") == str(command.account_id or "")
      and str(pending.environment or "").strip().upper() == "LIVE"
      and str(pending.side or "").strip().upper() == "SELL"
      and str(pending.intent_id or "") == intent_id
      and str(pending.instrument_code or "").strip().upper()
      == str(payload.get("instrument_code") or "").strip().upper()
      and int(pending.volume or 0) == requested_volume
      and str(pending.status or "").strip().upper()
      not in {
        "FILLED",
        "CANCELLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
        "CANCEL_REQUESTED",
        "RECONCILE_REQUIRED",
        "RECONCILED_ZERO_FILL",
      }
    ):
      raise AgentUnavailableError("卖单 Pending 绑定或状态已变化")

    intent = (
      await self.db.get(
        TradeIntentRecord,
        intent_id,
        with_for_update=True,
        populate_existing=True,
      )
      if intent_id
      else None
    )
    persisted_exit_plan_sell = bool(
      command_owner_type == ExecutionOwnerType.EXIT_PLAN.value
    )
    if not persisted_exit_plan_sell:
      return
    if intent is None:
      raise AgentUnavailableError("退出计划物理投递缺少持久化意图")

    await self._lock_and_validate_live_exit_plan_sell(
      locked_intent=intent,
      plan_id=command_owner_id,
      intent_id=intent_id,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        command_owner_id,
      ),
      environment=ExecutionEnvironment.LIVE,
      account_id=str(command.account_id or ""),
      instrument_code=str(payload.get("instrument_code") or ""),
      volume=requested_volume,
      request_metadata=dict(getattr(pending, "request_metadata", None) or {}),
      allow_queued_intent_projection=True,
    )

  @staticmethod
  def _managed_entry_state(
    plan_state: StrategyRunState | None,
    *,
    intent_id: str,
  ) -> dict[str, Any]:
    if plan_state is None:
      raise AgentUnavailableError("建仓计划权威状态快照缺失")
    managed_state = dict(
      dict(plan_state.custom_state or {}).get("managed_entry_plan") or {}
    )
    phase = str(managed_state.get("phase") or "").upper()
    pending_intent_id = str(managed_state.get("pending_intent_id") or "")
    if (
      not managed_state
      or phase not in {"AWAITING_APPROVAL", "ENTRY_PENDING"}
      or pending_intent_id != str(intent_id or "")
    ):
      raise AgentUnavailableError("建仓计划当前状态不允许买入")
    return managed_state

  @staticmethod
  def _require_managed_entry_capacity(
    *,
    plan_id: str,
    intent_id: str,
    config: ManagedEntryPlanConfig,
    managed_state: dict[str, Any],
    account: Account,
    position: Position | None,
    active_pending: list[PendingTradeOrder],
    executed_volumes: dict[str, int],
    requested_price: Decimal,
    requested_volume: int,
  ) -> None:
    """Recompute the exact remaining EntryPlan target at the outbox boundary."""

    total_asset = Decimal(str(account.total_asset or 0))
    requested_amount = requested_price * int(requested_volume)
    if total_asset <= 0 or requested_amount <= 0:
      raise AgentUnavailableError("建仓计划目标缺口所需账户数据无效")

    instrument_code = config.instrument_code
    other_orders = [
      order
      for order in active_pending
      if str(order.intent_id or "") != str(intent_id or "")
      and str(order.instrument_code or "") == instrument_code
    ]
    instrument_pending_amount = sum(
      (
        Decimal(str(order.limit_price or 0))
        * TradeCommandService._remaining_order_volume(order, executed_volumes)
        for order in other_orders
      ),
      Decimal("0"),
    )
    instrument_pending_volume = sum(
      TradeCommandService._remaining_order_volume(order, executed_volumes)
      for order in other_orders
    )
    plan_pending_orders = [
      order
      for order in other_orders
      if str(order.strategy_run_id or "") == plan_id
    ]
    plan_pending_amount = sum(
      (
        Decimal(str(order.limit_price or 0))
        * TradeCommandService._remaining_order_volume(order, executed_volumes)
        for order in plan_pending_orders
      ),
      Decimal("0"),
    )

    try:
      filled_amount = max(
        Decimal("0"),
        Decimal(str(managed_state.get("filled_amount_cny", 0) or 0)),
      )
      filled_volume = max(
        0,
        int(managed_state.get("filled_volume", 0) or 0),
      )
    except (TypeError, ValueError, ArithmeticError) as exc:
      raise AgentUnavailableError("建仓计划累计成交状态无效") from exc
    if not filled_amount.is_finite():
      raise AgentUnavailableError("建仓计划累计成交状态无效")

    current_volume = max(0, int(getattr(position, "volume", 0) or 0))
    current_market_value = Decimal(str(getattr(position, "market_value", 0) or 0))
    if current_market_value <= 0 and current_volume > 0:
      current_market_value = requested_price * current_volume
    current_market_value = max(Decimal("0"), current_market_value)

    policy = config.target_policy
    baseline = policy.baseline_snapshot
    external_or_plan_amount = max(
      filled_amount,
      Decimal(max(0, current_volume - int(baseline.position_volume))) * requested_price,
      max(
        Decimal("0"),
        current_market_value - Decimal(str(baseline.market_value_cny)),
      ),
    )
    external_or_plan_volume = max(
      filled_volume,
      max(0, current_volume - int(baseline.position_volume)),
    )
    plan_budget_remaining = max(
      Decimal("0"),
      Decimal(str(policy.max_total_amount_cny)) - filled_amount - plan_pending_amount,
    )
    position_cap_remaining = max(
      Decimal("0"),
      total_asset * Decimal(str(policy.max_position_pct))
      - current_market_value
      - instrument_pending_amount,
    )

    if policy.mode == EntryTargetMode.TARGET_POSITION_PCT:
      target_remaining_amount = max(
        Decimal("0"),
        total_asset * Decimal(str(policy.target_position_pct or 0))
        - current_market_value
        - instrument_pending_amount,
      )
      target_remaining_volume: int | None = None
    elif policy.mode == EntryTargetMode.INCREMENTAL_AMOUNT_CNY:
      target_remaining_amount = max(
        Decimal("0"),
        Decimal(str(policy.incremental_amount_cny or 0))
        - external_or_plan_amount
        - instrument_pending_amount,
      )
      target_remaining_volume = None
    else:
      target_remaining_volume = max(
        0,
        int(policy.additional_volume or 0)
        - external_or_plan_volume
        - instrument_pending_volume,
      )
      target_remaining_amount = requested_price * target_remaining_volume

    amount_capacity = min(
      plan_budget_remaining,
      position_cap_remaining,
      target_remaining_amount,
    )
    if requested_amount > amount_capacity or (
      target_remaining_volume is not None
      and int(requested_volume) > target_remaining_volume
    ):
      raise AgentUnavailableError("买入委托超过建仓计划当前剩余目标或总预算")

  @staticmethod
  def _remaining_order_volume(
    order: PendingTradeOrder,
    executed_volumes: dict[str, int],
  ) -> int:
    order_key = str(
      getattr(order, "client_order_id", "") or getattr(order, "intent_id", "") or ""
    )
    return max(
      0,
      int(order.volume or 0) - max(0, int(executed_volumes.get(order_key, 0) or 0)),
    )

  async def _executed_volumes_for_orders(
    self,
    orders: list[PendingTradeOrder],
  ) -> dict[str, int]:
    broker_to_client: dict[int, str] = {}
    for order in orders:
      broker_order_id = str(order.broker_order_id or "").strip()
      if not broker_order_id:
        continue
      try:
        normalized_broker_id = int(broker_order_id)
      except (TypeError, ValueError):
        # QMT A-share broker order ids are integers.  Unknown ids cannot be
        # proven filled and therefore retain the conservative full reserve.
        continue
      broker_to_client[normalized_broker_id] = str(order.client_order_id)
    if not broker_to_client:
      return {}
    trades = list(
      (
        await self.db.execute(
          select(Trade).where(Trade.order_id.in_(tuple(broker_to_client)))
        )
      )
      .scalars()
      .all()
    )
    executed: dict[str, int] = {}
    for trade in trades:
      client_order_id = broker_to_client.get(int(trade.order_id))
      if not client_order_id:
        continue
      executed[client_order_id] = executed.get(client_order_id, 0) + max(
        0,
        int(trade.volume or 0),
      )
    return executed

  async def _require_no_conflicting_entry_exit(
    self,
    *,
    plan_id: str,
    strategy_run_id: str = "",
    account_id: str,
    instrument_code: str,
    working_orders: list[PendingTradeOrder],
    invalidate_auto_external_buy: bool = False,
  ) -> None:
    strategy_run_id = str(strategy_run_id or plan_id)
    instrument_orders = [
      order
      for order in working_orders
      if str(order.instrument_code or "") == instrument_code
    ]
    for order in instrument_orders:
      side = str(order.side or "").upper()
      status = str(order.status or "").upper()
      belongs_to_plan = (
        str(getattr(order, "strategy_run_id", "") or "") == strategy_run_id
      )
      if side == "SELL" or status == "RECONCILE_REQUIRED":
        raise AgentUnavailableError("同标的存在卖单或待对账委托，禁止继续买入")
      if side != "BUY":
        raise AgentUnavailableError("同标的存在方向不明的工作委托，禁止继续买入")
      if not belongs_to_plan:
        if invalidate_auto_external_buy:
          await EntryPlanAuthorizationService(self.db).invalidate(
            plan_id=plan_id,
            reason="ENTRY_EXTERNAL_WORKING_BUY",
            commit=True,
          )
          raise AgentUnavailableError(
            "同标的存在外部或其他策略工作买单，自动授权已失效"
          )
        raise AgentUnavailableError(
          "同标的存在外部或其他策略工作买单，请先完成对账后再买入"
        )
    reconcile_intent = await self.db.scalar(
      select(TradeIntentRecord.id)
      .where(
        TradeIntentRecord.strategy_run_id == strategy_run_id,
        TradeIntentRecord.instrument_code == instrument_code,
        TradeIntentRecord.status == "RECONCILE_REQUIRED",
      )
      .with_for_update()
      .limit(1)
    )
    if reconcile_intent is not None:
      raise AgentUnavailableError("建仓计划存在未收敛成交意图，禁止继续买入")
    liquidation = await self.db.scalar(
      select(ConditionalLiquidationOrder.id)
      .where(
        ConditionalLiquidationOrder.account_id == account_id,
        ConditionalLiquidationOrder.stock_code == instrument_code,
        ConditionalLiquidationOrder.execution_mode == "live",
        ConditionalLiquidationOrder.status.in_(
          (
            ConditionalLiquidationStatus.SUBMITTED,
            ConditionalLiquidationStatus.PARTIALLY_EXITED,
          )
        ),
      )
      .with_for_update()
      .limit(1)
    )
    exit_plan = await self.db.scalar(
      select(AutoExitPlanRecord.plan_id)
      .where(
        AutoExitPlanRecord.account_id == account_id,
        AutoExitPlanRecord.instrument_code == instrument_code,
        AutoExitPlanRecord.environment == "LIVE",
        or_(
          AutoExitPlanRecord.status == "EXIT_PENDING",
          AutoExitPlanRecord.pending_client_order_id.is_not(None),
        ),
      )
      .with_for_update()
      .limit(1)
    )
    if liquidation is not None or exit_plan is not None:
      raise AgentUnavailableError("同标的正在持续清仓，禁止继续买入")

  @staticmethod
  def _persisted_order_enum_int(value: Any) -> int | None:
    try:
      return int(getattr(value, "value", value))
    except (TypeError, ValueError):
      return None

  async def _require_no_authoritative_entry_order_conflict(
    self,
    *,
    plan_id: str,
    strategy_run_id: str = "",
    account_id: str,
    instrument_code: str,
    working_orders: list[PendingTradeOrder],
    invalidate_auto_external_buy: bool,
  ) -> None:
    """Fail closed on broker working orders absent from the command ledger.

    ``orders`` is the latest authoritative QMT snapshot.  A normal working row
    already represented by an active ``PendingTradeOrder`` is evaluated by the
    durable command-ledger checks and must not be counted or rejected twice.
    An UNKNOWN broker status is never trusted, even when represented locally.
    Any uncovered row requires a unique correlation to this exact EntryPlan;
    because the active pending row is missing, even that case is held for
    reconciliation rather than allowing a second broker order.
    """

    strategy_run_id = str(strategy_run_id or plan_id)
    trading_day_start = datetime.combine(time_utils.today(), datetime.min.time())
    trading_day_end = trading_day_start + timedelta(days=1)
    authoritative_orders = list(
      (
        await self.db.execute(
          select(PersistedOrder)
          .where(
            PersistedOrder.account_id == account_id,
            PersistedOrder.time >= trading_day_start,
            PersistedOrder.time < trading_day_end,
            PersistedOrder.status.in_(_AUTHORITATIVE_ENTRY_WORKING_STATUSES),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    if not authoritative_orders:
      return

    pending_by_broker_id = {
      str(order.broker_order_id).strip(): order
      for order in working_orders
      if str(getattr(order, "broker_order_id", "") or "").strip()
    }
    uncovered_orders: list[PersistedOrder] = []
    represented_unknown: list[tuple[PersistedOrder, PendingTradeOrder]] = []
    for order in authoritative_orders:
      order_time = getattr(order, "time", None)
      if isinstance(order_time, datetime):
        normalized_order_time = time_utils.to_shanghai(order_time)
        if not trading_day_start <= normalized_order_time < trading_day_end:
          continue
      status_code = self._persisted_order_enum_int(order.status)
      # Defensive filtering keeps stale/incorrect repository fakes and future
      # enum additions from turning a terminal broker row into a false block.
      if status_code in _AUTHORITATIVE_ENTRY_TERMINAL_STATUSES:
        continue
      broker_order_id = str(order.id)
      pending = pending_by_broker_id.get(broker_order_id)
      if pending is not None:
        if status_code == int(PersistedOrderStatus.UNKNOWN):
          order_type = self._persisted_order_enum_int(order.type)
          if str(order.stock_code or "") == instrument_code or order_type == int(
            PersistedOrderType.BUY
          ):
            represented_unknown.append((order, pending))
        continue
      order_type = self._persisted_order_enum_int(order.type)
      if str(order.stock_code or "") == instrument_code or order_type == int(
        PersistedOrderType.BUY
      ):
        uncovered_orders.append(order)

    if not uncovered_orders and not represented_unknown:
      return

    correlations_by_broker_id: dict[str, list[OrderCorrelation]] = {}
    if uncovered_orders:
      broker_order_ids = tuple(str(order.id) for order in uncovered_orders)
      correlations = list(
        (
          await self.db.execute(
            select(OrderCorrelation)
            .where(
              OrderCorrelation.account_id == account_id,
              OrderCorrelation.broker_order_id.in_(broker_order_ids),
            )
            .with_for_update()
          )
        )
        .scalars()
        .all()
      )
      for correlation in correlations:
        correlations_by_broker_id.setdefault(
          str(correlation.broker_order_id), []
        ).append(correlation)

    conflicts: list[tuple[PersistedOrder, bool]] = []
    for order, pending in represented_unknown:
      belongs_to_plan = (
        str(getattr(pending, "strategy_run_id", "") or "") == strategy_run_id
      )
      conflicts.append((order, not belongs_to_plan))
    for order in uncovered_orders:
      correlations = correlations_by_broker_id.get(str(order.id), [])
      belongs_to_plan = False
      if len(correlations) == 1:
        correlation = correlations[0]
        belongs_to_plan = (
          str(order.stock_code or "") == instrument_code
          and str(correlation.strategy_run_id or "") == strategy_run_id
        )
      conflicts.append((order, not belongs_to_plan))

    if not conflicts:
      return
    conflict_order, external = next(
      (
        (order, is_external)
        for order, is_external in conflicts
        if invalidate_auto_external_buy
        and is_external
        and self._persisted_order_enum_int(order.type) == int(PersistedOrderType.BUY)
      ),
      next(
        ((order, is_external) for order, is_external in conflicts if is_external),
        conflicts[0],
      ),
    )
    order_type = self._persisted_order_enum_int(conflict_order.type)
    is_buy = order_type == int(PersistedOrderType.BUY)
    if invalidate_auto_external_buy and external and is_buy:
      await EntryPlanAuthorizationService(self.db).invalidate(
        plan_id=plan_id,
        reason="ENTRY_EXTERNAL_WORKING_BUY",
        commit=True,
      )
      raise AgentUnavailableError("检测到外部或其他策略工作买单，自动授权已失效")
    if external:
      raise AgentUnavailableError(
        "检测到外部、其他策略或方向不明的工作委托，请处理后重新确认"
      )
    raise AgentUnavailableError(
      "本计划存在仅见于 Broker 的未完成委托，需先对账收敛后再买入"
    )

  async def _require_no_unattributed_buy_trades(
    self,
    *,
    plan_id: str,
    strategy_run_id: str = "",
    account_id: str,
    instrument_code: str,
    grant: EntryPlanAuthorizationGrant,
  ) -> None:
    strategy_run_id = str(strategy_run_id or plan_id)
    authorized_at = getattr(grant, "authorized_at", None)
    if authorized_at is None:
      raise AgentUnavailableError("自动买入授权缺少生效时间")
    buy_trades = list(
      (
        await self.db.execute(
          select(Trade).where(
            Trade.account_id == account_id,
            Trade.stock_code == instrument_code,
            Trade.order_type == int(PersistedOrderType.BUY),
            Trade.time >= authorized_at,
          )
        )
      )
      .scalars()
      .all()
    )
    if not buy_trades:
      return
    broker_order_ids = {str(trade.order_id) for trade in buy_trades}
    correlations = list(
      (
        await self.db.execute(
          select(OrderCorrelation).where(
            OrderCorrelation.broker_order_id.in_(broker_order_ids)
          )
        )
      )
      .scalars()
      .all()
    )
    attributed_order_ids = {
      str(item.broker_order_id)
      for item in correlations
      if str(item.strategy_run_id or "") == strategy_run_id
    }
    if broker_order_ids - attributed_order_ids:
      await EntryPlanAuthorizationService(self.db).invalidate(
        plan_id=plan_id,
        reason="ENTRY_UNATTRIBUTED_REAL_BUY",
        commit=True,
      )
      raise AgentUnavailableError("检测到授权后的未归因真实买入，自动授权已失效")

  async def _require_auto_entry_live_snapshot(
    self,
    account_id: str,
  ) -> AccountExecutionControl:
    return await self._require_live_authorization(account_id)

  async def _exact_auto_entry_device(
    self,
    *,
    account_id: str,
    instrument_code: str,
    side: str,
    limit_price: Decimal,
    volume: int,
    strategy_run_id: str,
    intent_id: str,
    bucket: str,
    policy_version: int,
    request_metadata: dict[str, Any],
  ) -> AgentDevice:
    """Perform the atomic, authoritative second gate for one managed BUY."""

    if str(side or "").upper() != "BUY":
      raise AgentUnavailableError("精确自动建仓门禁只能用于 LIVE BUY")
    control = await self._require_auto_entry_live_snapshot(account_id)
    grant_id = str(
      request_metadata.get("auto_entry_authorization_grant_id") or ""
    ).strip()
    if not str(strategy_run_id or "").strip() or not grant_id:
      raise AgentUnavailableError("自动买入命令缺少精确计划与 grant 绑定")

    run_row = (
      await self.db.execute(
        select(StrategyRun, Strategy)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(StrategyRun.id == strategy_run_id)
        .with_for_update()
      )
    ).one_or_none()
    if run_row is None:
      raise AgentUnavailableError("自动买入对应建仓计划不存在")
    run, strategy = run_row
    parameters = dict(run.parameters or {})
    bound_plan_id = str(getattr(run, "plan_id", "") or "").strip()
    if not bound_plan_id:
      binding = parameters.get("_managed_plan_binding")
      if isinstance(binding, Mapping):
        bound_plan_id = str(binding.get("plan_id") or "").strip()
    bound_plan_id = bound_plan_id or str(getattr(run, "id", strategy_run_id))
    plan_id = bound_plan_id
    if (
      strategy.class_name != "AshareManagedEntryPlanStrategy"
      or self._enum_value(run.mode) != "live"
      or self._enum_value(run.status) != "running"
      or parameters.get(ENTRY_PLAN_ENABLED_KEY) is not True
      or list(run.instruments or []) != [instrument_code]
    ):
      raise AgentUnavailableError("建仓计划已暂停、终止或不再绑定当前标的")
    try:
      config = ManagedEntryPlanConfig.from_dict(
        dict(parameters.get("managed_entry_plan") or {})
      )
      scope = scope_from_managed_entry_config(
        plan_id=plan_id,
        config=config,
        run_id=strategy_run_id,
      )
    except (TypeError, ValueError) as exc:
      raise AgentUnavailableError("建仓计划权威配置无效") from exc
    if (
      config.execution_policy.environment != EntryEnvironment.LIVE
      or config.execution_policy.authorization_mode != EntryAuthorizationMode.AUTO
      or config.instrument_code != instrument_code
      or config.bucket != str(bucket or "").lower()
      or int(config.config_version) != int(policy_version or 0)
    ):
      raise AgentUnavailableError("建仓计划环境、授权模式、仓位桶或版本不匹配")
    intent = await self.db.get(
      TradeIntentRecord,
      str(intent_id or ""),
      with_for_update=True,
    )
    intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
    if (
      intent is None
      or str(intent.strategy_run_id or "") != strategy_run_id
      or str(intent.instrument_code or "") != instrument_code
      or str(intent.direction or "").upper() != "BUY"
      or str(intent.bucket or "").lower() != config.bucket
      or str(intent.status or "").upper() not in {"PENDING", "EXECUTION_READY"}
      or str(intent_metadata.get("execution_mode") or "").upper() != "AUTO"
      or str(intent_metadata.get("entry_plan_id") or "") != plan_id
      or int(intent_metadata.get("entry_config_version") or 0) != config.config_version
      or str(intent_metadata.get("auto_entry_authorization_grant_id") or "") != grant_id
      or not bool(intent_metadata.get("exact_auto_entry_authorized"))
      or str(intent_metadata.get("auto_entry_plan_fingerprint") or "")
      != scope.plan_fingerprint
      or str(intent_metadata.get("auto_entry_rule_fingerprint") or "")
      != scope.rule_fingerprint
      or str(request_metadata.get("auto_entry_plan_fingerprint") or "")
      != scope.plan_fingerprint
      or str(request_metadata.get("auto_entry_rule_fingerprint") or "")
      != scope.rule_fingerprint
    ):
      raise AgentUnavailableError("自动买入意图与当前计划或精确授权不匹配")

    plan_state = (
      await self.db.execute(
        select(StrategyRunState)
        .where(StrategyRunState.run_id == strategy_run_id)
        .with_for_update()
      )
    ).scalar_one_or_none()
    managed_state = self._managed_entry_state(
      plan_state,
      intent_id=intent_id,
    )

    price = Decimal(str(limit_price))
    requested_amount = price * int(volume)
    if not price.is_finite() or price <= 0 or requested_amount <= 0:
      raise AgentUnavailableError("自动买入委托价格或金额无效")
    if intent.target_volume is not None and int(volume) > int(intent.target_volume):
      raise AgentUnavailableError("自动买入委托超过意图目标数量")
    if intent.target_amount is not None and requested_amount > Decimal(
      str(intent.target_amount)
    ):
      raise AgentUnavailableError("自动买入委托超过意图目标金额")

    account = (
      await self.db.execute(
        select(Account)
        .where(
          Account.account_id == account_id,
          Account.account_type == AccountType.STOCK,
        )
        .with_for_update()
      )
    ).scalar_one_or_none()
    if account is None or Decimal(str(account.total_asset or 0)) <= 0:
      raise AgentUnavailableError("账户资产快照不可用")
    position = await self.db.scalar(
      select(Position)
      .where(
        Position.account_id == account_id,
        Position.stock_code == instrument_code,
      )
      .with_for_update()
    )
    position_market_value = Decimal(str(getattr(position, "market_value", 0) or 0))
    if position is not None and position_market_value <= 0:
      position_price = Decimal(str(getattr(position, "last_price", 0) or limit_price))
      position_market_value = position_price * int(position.volume or 0)
    active_pending = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.environment == "LIVE",
            PendingTradeOrder.status.in_(
              (
                "QUEUED",
                "PENDING",
                "DELIVERED",
                "SUBMITTED",
                "ACCEPTED",
                "PARTIAL_FILLED",
                "PARTIALLY_FILLED",
                "RECONCILE_REQUIRED",
                "CANCEL_REQUESTED",
              )
            ),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    grant = await self.db.get(
      EntryPlanAuthorizationGrant,
      grant_id,
      with_for_update=True,
    )
    if grant is None:
      raise AgentUnavailableError("自动买入精确授权不存在")
    await self._require_no_conflicting_entry_exit(
      plan_id=plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      working_orders=active_pending,
      invalidate_auto_external_buy=True,
    )
    await self._require_no_authoritative_entry_order_conflict(
      plan_id=plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      working_orders=active_pending,
      invalidate_auto_external_buy=True,
    )
    executed_volumes = await self._executed_volumes_for_orders(active_pending)
    self._require_managed_entry_capacity(
      plan_id=plan_id,
      intent_id=intent_id,
      config=config,
      managed_state=managed_state,
      account=account,
      position=position,
      active_pending=active_pending,
      executed_volumes=executed_volumes,
      requested_price=price,
      requested_volume=volume,
    )
    if any(
      str(item.strategy_run_id or "") == str(strategy_run_id)
      and str(item.intent_id or "") != str(intent_id)
      and str(item.side or "").upper() == "BUY"
      for item in active_pending
    ):
      raise AgentUnavailableError("建仓计划已有未完成买单，禁止并发路由")
    pending_amount = sum(
      (
        Decimal(str(item.limit_price))
        * self._remaining_order_volume(item, executed_volumes)
        for item in active_pending
        if str(item.intent_id or "") != str(intent_id)
        and str(item.instrument_code or "") == instrument_code
        and str(item.side or "").upper() == "BUY"
      ),
      Decimal("0"),
    )
    try:
      capacity = await AccountCapacityService(self.db).read(control, instrument_code=instrument_code)
    except ValueError as exc:
      raise AgentUnavailableError(str(exc)) from exc
    available_cash = capacity.available_cash
    if requested_amount > available_cash:
      raise AgentUnavailableError("自动买入超过当前权威可用资金")
    cash_buffer = Decimal(str(account.total_asset)) * Decimal(
      str(config.pacing_policy.cash_buffer_pct)
    )
    if available_cash - requested_amount < cash_buffer:
      raise AgentUnavailableError("自动买入会突破计划绑定的最低现金缓冲")
    resulting_position_pct = (
      position_market_value + pending_amount + requested_amount
    ) / Decimal(str(account.total_asset))

    protected_price = Decimal(str(intent_metadata.get("protected_limit_price") or 0))
    if not protected_price.is_finite() or protected_price <= 0:
      raise AgentUnavailableError("自动买入意图缺少受保护的决策价格")
    proposed_slippage_bps = int(
      max(
        Decimal("0"),
        (price - protected_price) / protected_price * Decimal("10000"),
      ).to_integral_value(rounding=ROUND_CEILING)
    )
    proposed_price_deviation_bps = int(
      (
        abs(price - protected_price) / protected_price * Decimal("10000")
      ).to_integral_value(rounding=ROUND_CEILING)
    )
    validation = await EntryPlanAuthorizationService(self.db).validate_or_invalidate(
      plan_id=plan_id,
      current_scope=scope,
      account_id=account_id,
      proposed_amount_cny=requested_amount,
      proposed_buy_price=price,
      proposed_slippage_bps=proposed_slippage_bps,
      proposed_price_deviation_bps=proposed_price_deviation_bps,
      resulting_position_pct=resulting_position_pct,
      commit=False,
    )
    if (
      not validation.valid
      or validation.balance is None
      or validation.balance.grant_id != grant_id
    ):
      raise AgentUnavailableError(f"自动买入精确授权已失效：{validation.code}")
    await self._require_no_unattributed_buy_trades(
      plan_id=plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      grant=grant,
    )
    current_position_volume = max(0, int(getattr(position, "volume", 0) or 0))
    explained_position_volume = max(
      0,
      int(config.target_policy.baseline_snapshot.position_volume)
      + int(getattr(grant, "consumed_total_volume", 0) or 0),
    )
    if current_position_volume > explained_position_volume:
      await EntryPlanAuthorizationService(self.db).invalidate(
        plan_id=plan_id,
        reason="ENTRY_UNEXPLAINED_POSITION_INCREASE",
        commit=True,
      )
      raise AgentUnavailableError("检测到未归属于本计划的外部增仓，自动授权已失效")
    device = await self._device_for(
      user_id=str(grant.subject_user_id),
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
      or str(details.get("protocolVersion") or "") != PROTOCOL_VERSION
    ):
      raise AgentUnavailableError(
        f"自动买入要求唯一 READY、live、协议 {PROTOCOL_VERSION} 的 QMT Agent"
      )
    return device

  async def _managed_manual_entry_device(
    self,
    *,
    account_id: str,
    instrument_code: str,
    limit_price: Decimal,
    volume: int,
    strategy_run_id: str,
    intent_id: str,
    bucket: str,
    policy_version: int,
    intent: TradeIntentRecord,
  ) -> AgentDevice:
    """Atomically recheck a device-confirmed managed BUY before outbox insert."""

    control = await self._require_manual_live_authorization(
      account_id,
      risk_reducing=False,
    )
    locked_intent = await self.db.get(
      TradeIntentRecord,
      str(intent_id or ""),
      with_for_update=True,
    )
    if locked_intent is None or str(locked_intent.id) != str(intent.id):
      raise AgentUnavailableError("逐笔确认意图不存在或已变化")
    intent = locked_intent
    row = (
      await self.db.execute(
        select(StrategyRun, Strategy)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(StrategyRun.id == strategy_run_id)
        .with_for_update()
      )
    ).one_or_none()
    if row is None:
      raise AgentUnavailableError("逐笔确认对应建仓计划不存在")
    run, strategy = row
    parameters = dict(run.parameters or {})
    bound_plan_id = str(getattr(run, "plan_id", "") or "").strip()
    if not bound_plan_id:
      binding = parameters.get("_managed_plan_binding")
      if isinstance(binding, Mapping):
        bound_plan_id = str(binding.get("plan_id") or "").strip()
    bound_plan_id = bound_plan_id or str(getattr(run, "id", strategy_run_id))
    if (
      strategy.class_name != "AshareManagedEntryPlanStrategy"
      or self._enum_value(run.mode) != "live"
      or self._enum_value(run.status) != "running"
      or parameters.get("entry_plan_enabled") is not True
      or list(run.instruments or []) != [instrument_code]
    ):
      raise AgentUnavailableError("建仓计划已暂停、终止或不再绑定当前标的")
    try:
      config = ManagedEntryPlanConfig.from_dict(
        dict(parameters.get("managed_entry_plan") or {})
      )
    except (TypeError, ValueError) as exc:
      raise AgentUnavailableError("建仓计划权威配置无效") from exc
    intent_metadata = dict(intent.intent_metadata or {})
    if (
      config.execution_policy.environment != EntryEnvironment.LIVE
      or config.instrument_code != instrument_code
      or config.bucket != str(bucket or "").lower()
      or config.config_version != int(policy_version or 0)
      or str(intent.strategy_run_id or "") != strategy_run_id
      or str(intent.instrument_code or "") != instrument_code
      or str(intent.direction or "").upper() != "BUY"
      or str(intent.status or "").upper() not in {"APPROVED", "EXECUTION_READY"}
      or str(intent_metadata.get("entry_plan_id") or "") != bound_plan_id
      or int(intent_metadata.get("entry_config_version") or 0) != config.config_version
      or str(intent_metadata.get("execution_mode") or "").upper() != "MANUAL_CONFIRM"
    ):
      raise AgentUnavailableError("逐笔确认意图与当前建仓计划不匹配")
    plan_state = (
      await self.db.execute(
        select(StrategyRunState)
        .where(StrategyRunState.run_id == strategy_run_id)
        .with_for_update()
      )
    ).scalar_one_or_none()
    managed_state = self._managed_entry_state(
      plan_state,
      intent_id=intent_id,
    )

    price = Decimal(str(limit_price))
    requested_amount = price * int(volume)
    if (
      not price.is_finite()
      or price <= 0
      or int(volume) <= 0
      or price > Decimal(str(config.completion_policy.max_buy_price))
      or requested_amount
      > Decimal(str(config.pacing_policy.max_single_intent_amount_cny))
    ):
      raise AgentUnavailableError("逐笔确认买单超过价格或单笔风险上限")
    if intent.target_volume is not None and int(volume) > int(intent.target_volume):
      raise AgentUnavailableError("逐笔确认买单超过已确认意图数量")
    if intent.target_amount is not None and requested_amount > Decimal(
      str(intent.target_amount)
    ):
      raise AgentUnavailableError("逐笔确认买单超过已确认意图金额")
    account = (
      await self.db.execute(
        select(Account)
        .where(
          Account.account_id == account_id,
          Account.account_type == AccountType.STOCK,
        )
        .with_for_update()
      )
    ).scalar_one_or_none()
    if account is None or Decimal(str(account.total_asset or 0)) <= 0:
      raise AgentUnavailableError("账户资产快照不可用")
    pending_orders = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.environment == "LIVE",
            PendingTradeOrder.status.in_(
              (
                "QUEUED",
                "PENDING",
                "DELIVERED",
                "SUBMITTED",
                "ACCEPTED",
                "PARTIAL_FILLED",
                "PARTIALLY_FILLED",
                "RECONCILE_REQUIRED",
                "CANCEL_REQUESTED",
              )
            ),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    await self._require_no_conflicting_entry_exit(
      plan_id=bound_plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      working_orders=pending_orders,
    )
    await self._require_no_authoritative_entry_order_conflict(
      plan_id=bound_plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      working_orders=pending_orders,
      invalidate_auto_external_buy=False,
    )
    executed_volumes = await self._executed_volumes_for_orders(pending_orders)
    try:
      capacity = await AccountCapacityService(self.db).read(control, instrument_code=instrument_code)
    except ValueError as exc:
      raise AgentUnavailableError(str(exc)) from exc
    available_cash = capacity.available_cash
    cash_buffer = Decimal(str(account.total_asset)) * Decimal(
      str(config.pacing_policy.cash_buffer_pct)
    )
    if available_cash - requested_amount < cash_buffer:
      raise AgentUnavailableError("逐笔确认买入会突破计划绑定的最低现金缓冲")

    position = await self.db.scalar(
      select(Position)
      .where(
        Position.account_id == account_id,
        Position.stock_code == instrument_code,
      )
      .with_for_update()
    )
    current_market_value = Decimal(str(getattr(position, "market_value", 0) or 0))
    if position is not None and current_market_value <= 0:
      current_market_value = price * int(position.volume or 0)
    self._require_managed_entry_capacity(
      plan_id=bound_plan_id,
      intent_id=intent_id,
      config=config,
      managed_state=managed_state,
      account=account,
      position=position,
      active_pending=pending_orders,
      executed_volumes=executed_volumes,
      requested_price=price,
      requested_volume=volume,
    )
    instrument_pending = sum(
      (
        Decimal(str(order.limit_price))
        * self._remaining_order_volume(order, executed_volumes)
        for order in pending_orders
        if str(order.instrument_code or "") == instrument_code
        and str(order.intent_id or "") != intent_id
        and str(order.side or "").upper() == "BUY"
      ),
      Decimal("0"),
    )
    resulting_position_pct = (
      current_market_value + instrument_pending + requested_amount
    ) / Decimal(str(account.total_asset))
    if resulting_position_pct > Decimal(str(config.target_policy.max_position_pct)):
      raise AgentUnavailableError("逐笔确认买入会突破计划仓位上限")

    device = await self._device_for_account(account_id, "live")
    heartbeat = await self.db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device.id}",
    )
    details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    if str(details.get("protocolVersion") or "") != PROTOCOL_VERSION:
      raise AgentUnavailableError(
        f"逐笔确认买入要求协议 {PROTOCOL_VERSION} 的就绪 QMT Agent"
      )
    return device

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
        str(capability).lower() for capability in list(device.capabilities or [])
      }
      if account_id not in allowed or execution_mode not in capabilities:
        continue
      if execution_mode == "live":
        heartbeat = await self.db.get(
          RuntimeComponentHeartbeat,
          f"qmt-agent:{device.id}",
        )
        acceptable_statuses = (
          {"READY", "RECONCILING", "EMERGENCY_STOP", "RECONCILE_REQUIRED"}
          if allow_degraded_cancel
          else {"READY"}
        )
        if (
          heartbeat is None or str(heartbeat.status).upper() not in acceptable_statuses
        ):
          continue
        if not self._heartbeat_fresh(
          heartbeat,
          acceptable_statuses=acceptable_statuses,
        ):
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
    eligible: list[AgentDevice] = []
    for device in result.scalars().all():
      capabilities = {
        str(capability).lower() for capability in list(device.capabilities or [])
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

  async def _require_durable_order_intent(
    self,
    *,
    account_id: str,
    instrument_code: str,
    side: str,
    execution_mode: str,
    execution_ref: ExecutionOwnerRef,
    environment: ExecutionEnvironment,
    strategy_run_id: str,
    strategy_order_id: str,
    intent_id: str,
    batch_id: str,
    bucket: str,
    t_order_parent_client_id: str = "",
  ) -> TradeIntentRecord | None:
    canonical_ref, canonical_environment, owner_type, owner_id, _ = (
      self._require_execution_identity(execution_ref, environment)
    )
    normalized_strategy_run_id, normalized_strategy_order_id = (
      self._normalize_strategy_identity(
        owner_type,
        owner_id,
        strategy_run_id,
        strategy_order_id,
      )
    )
    strategy_run_id = normalized_strategy_run_id
    strategy_order_id = normalized_strategy_order_id
    normalized_intent_id = str(intent_id or "").strip()
    if owner_type == ExecutionOwnerType.STRATEGY_RUN.value and not normalized_intent_id:
      raise AgentUnavailableError(
        "TRADE_INTENT_REQUIRED:STRATEGY_RUN 委托必须绑定已持久化意图"
      )
    if owner_type == ExecutionOwnerType.EXIT_PLAN.value and not normalized_intent_id:
      raise AgentUnavailableError(
        "TRADE_INTENT_REQUIRED:EXIT_PLAN 委托必须绑定已持久化意图"
      )
    intent_id = normalized_intent_id

    # A command without a persisted intent is valid for an explicitly scoped
    # manual/direct command.  Any supplied intent linkage, however, must be
    # proved against the same owner/environment; it is never used to infer
    # either value.
    if not any((strategy_order_id, intent_id, batch_id)):
      return None
    if not intent_id:
      raise AgentUnavailableError("TRADE_INTENT_REQUIRED:策略委托必须绑定已持久化意图")
    intent = await self.db.get(
      TradeIntentRecord,
      intent_id,
      with_for_update=True,
      populate_existing=True,
    )
    if intent is None:
      raise AgentUnavailableError("TRADE_INTENT_NOT_ACCEPTED:策略意图尚未持久化受理")
    if (
      str(intent.owner_type or "").upper() != owner_type
      or str(intent.owner_id or "") != owner_id
      or str(getattr(intent, "environment", "")).upper()
      != canonical_environment.value
      or str(intent.instrument_code or "") != instrument_code
      or str(intent.direction).upper() != side.upper()
      or str(intent.bucket) != bucket
      or (intent.account_id and str(intent.account_id) != account_id)
    ):
      raise AgentUnavailableError(
        "TRADE_INTENT_SCOPE_MISMATCH:委托与持久化意图归属不一致"
      )
    exit_plan: AutoExitPlanRecord | None = None
    if owner_type == ExecutionOwnerType.STRATEGY_RUN.value:
      if strategy_run_id and strategy_run_id != owner_id:
        raise AgentUnavailableError(
          "TRADE_INTENT_SCOPE_MISMATCH:策略运行与 owner 不一致"
        )
      if intent.strategy_run_id and str(intent.strategy_run_id) != owner_id:
        raise AgentUnavailableError(
          "TRADE_INTENT_SCOPE_MISMATCH:策略意图缺少匹配 strategy_run_id"
        )
    elif intent.strategy_run_id is not None:
      raise AgentUnavailableError(
        "TRADE_INTENT_OWNER_CONFLICT:非 STRATEGY_RUN 意图不得携带 strategy_run_id"
      )
    if str(intent.status).upper() in {
      "FILLED",
      "CANCELLED",
      "CANCELED",
      "REJECTED",
      "EXPIRED",
      "RECONCILED_ZERO_FILL",
    }:
      raise AgentUnavailableError("TRADE_INTENT_TERMINAL:终态意图不可再次下单")
    if owner_type == ExecutionOwnerType.STRATEGY_RUN.value:
      run = await self.db.get(StrategyRun, owner_id)
      if (
        not strategy_order_id
        or run is None
        or self._enum_value(run.mode) != execution_mode
        or str(dict(run.parameters or {}).get("account_id") or "") != account_id
      ):
        raise AgentUnavailableError(
          "TRADE_INTENT_SCOPE_MISMATCH:策略运行与委托执行环境不一致"
        )
    elif owner_type == ExecutionOwnerType.EXIT_PLAN.value:
      exit_plan = await self.db.get(AutoExitPlanRecord, owner_id)
      if (
        exit_plan is None
        or str(exit_plan.account_id) != account_id
        or str(exit_plan.environment or "").strip().upper()
        != canonical_environment.value
        or str(exit_plan.instrument_code) != instrument_code
        or str(
          getattr(exit_plan, "source_execution_environment", "")
        ).upper()
        != canonical_environment.value
      ):
        raise AgentUnavailableError(
          "TRADE_INTENT_SCOPE_MISMATCH:退出计划与委托执行环境不一致"
        )
      if batch_id and (
        str(exit_plan.source_type or "").strip().upper() != "T_TRADE_BATCH"
        or str(exit_plan.source_id or "") != str(batch_id)
      ):
        raise AgentUnavailableError(
          "TRADE_INTENT_SCOPE_MISMATCH:公共退出计划与做T批次来源不一致"
        )
    existing = await self.db.scalar(
      select(PendingTradeOrder.client_order_id)
      .where(
        PendingTradeOrder.intent_id == intent_id,
      )
      .order_by(PendingTradeOrder.t_order_attempt.desc())
      .limit(1)
    )
    if existing is not None:
      parent = await self.db.get(PendingTradeOrder, t_order_parent_client_id) if t_order_parent_client_id else None
      if not (
        parent is not None and existing == t_order_parent_client_id
        and parent.intent_id == intent_id and parent.account_id == account_id
        and parent.owner_type == owner_type and parent.owner_id == owner_id
        and parent.environment == canonical_environment.value
        and parent.batch_id == batch_id and parent.instrument_code == instrument_code
        and parent.side == side and t_order_lifecycle_active(parent, utcnow())
      ):
        raise AgentUnavailableError(
          "TRADE_INTENT_ALREADY_ROUTED:意图已有委托，请使用原幂等键重试"
        )
    return intent

  async def _require_account_capacity(
    self,
    control: AccountExecutionControl,
    *,
    instrument_code: str,
    side: str,
    limit_price: Decimal,
    volume: int,
    intent: TradeIntentRecord | None,
    batch_id: str,
    t_trade_role: str,
  ) -> dict[str, Any]:
    try:
      intent_metadata = dict(getattr(intent, "intent_metadata", None) or {})
      envelope = intent_metadata.get("t_trading_envelope")
      envelope = dict(envelope) if isinstance(envelope, Mapping) else {}
      if intent is not None and intent.owner_type == "T_ASSISTANT_EXECUTION":
        from quantx_infrastructure.services.live_entry_dispatch_review import (
          revalidate_live_entry_dispatch,
        )

        reviewed = await revalidate_live_entry_dispatch(
          self.db, intent=intent, volume=volume, limit_price=limit_price,
          now=datetime.now(timezone.utc), fresh_review=self.live_entry_review,
        )
        envelope = dict(reviewed.request.metadata.get("t_trading_envelope") or {})
      bucket_inventory = envelope.get("observed_position_projection")
      bucket_inventory = (
        dict(bucket_inventory) if isinstance(bucket_inventory, Mapping) else None
      )
      if (
        side.upper() == "BUY"
        and t_trade_role == "ENTRY"
        and intent is not None
        and str(intent.owner_type or "").upper()
        == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value
        and bucket_inventory is None
      ):
        raise ValueError(
          "T_TRADING_ENVELOPE_REQUIRED:新做T ENTRY 缺少冻结桶级容量证据"
        )
      protected_old_floor = max(
        0,
        int(envelope.get("protected_old_position_floor", 0) or 0),
      )
      locked_available = 0
      if bucket_inventory is not None:
        locked_value = bucket_inventory.get("locked_core")
        locked_available = max(
          0,
          int(
            (
              locked_value.get("available_volume", 0)
              if isinstance(locked_value, Mapping)
              else locked_value
            )
            or 0
          ),
        )
      capacity = await AccountCapacityService(self.db).read(
        control,
        instrument_code=instrument_code,
        own_plan_id=str(intent.owner_id)
        if intent is not None and intent.owner_type == "EXIT_PLAN"
        else "",
        own_batch_id=batch_id if t_trade_role == "EXIT" else "",
        bucket_inventory=bucket_inventory,
        protected_core_floor=max(0, protected_old_floor - locked_available),
        allow_core_claim=bool(envelope.get("allow_core_claim", False)),
      )
      if side.upper() == "BUY":
        required = buy_cash_required(limit_price, volume)
        if required > capacity.available_cash:
          raise ValueError(
            "ACCOUNT_CASH_CAPACITY_EXCEEDED:账户资金已被其他委托占用或不足"
          )
        if t_trade_role == "ENTRY" and volume > capacity.unclaimed_volume:
          raise ValueError(
            "T_TRADE_EXIT_CAPACITY_EXCEEDED:正T买入量超过未占用的旧仓可卖量"
          )
      elif volume > capacity.unclaimed_volume:
        raise ValueError(
          "ACCOUNT_SELL_CAPACITY_EXCEEDED:可卖库存已被其他委托或退出义务占用"
        )
    except ValueError as exc:
      raise AgentUnavailableError(str(exc)) from exc
    return {
      "snapshot_id": capacity.snapshot_id,
      "available_cash": str(capacity.available_cash),
      "available_volume": capacity.available_volume,
      "unclaimed_volume": capacity.unclaimed_volume,
      "obligation_watermark": capacity.obligation_watermark,
      "available_by_bucket": dict(capacity.available_by_bucket),
      "unclaimed_by_bucket": dict(capacity.unclaimed_by_bucket),
      "protected_old_position_floor": capacity.protected_old_position_floor,
      "old_inventory_claim_allocation": dict(
        capacity.old_inventory_claim_allocation
      ),
    }

  @staticmethod
  def _require_t_order_new_policy(
    *,
    role: str,
    order_type: str,
    limit_price: Decimal,
    intent: TradeIntentRecord | None,
    request_metadata: Mapping[str, Any],
  ) -> dict[str, Any]:
    """Re-evaluate the frozen v1 policy at the final LIVE command boundary."""

    normalized_role = str(role or "").strip().upper()
    if not normalized_role:
      return {}
    if str(order_type or "").strip().upper() != "FIX_PRICE":
      raise AgentUnavailableError("T_ORDER_FIX_PRICE_REQUIRED")
    policy = TEntryOrderPolicy() if normalized_role == "ENTRY" else TExitOrderPolicy()
    evidence = {
      **dict(getattr(intent, "intent_metadata", None) or {}),
      **dict(request_metadata or {}),
    }
    version_key = (
      "t_entry_order_policy_version"
      if normalized_role == "ENTRY"
      else "t_exit_order_policy_version"
    )
    if str(evidence.get(version_key) or "") != policy.version:
      raise AgentUnavailableError("T_ORDER_POLICY_VERSION_REQUIRED")
    try:
      result = policy.decide_new(
        now=time_utils.now(),
        reference_price=evidence.get("t_order_reference_price"),
        price_tick=evidence.get("t_order_price_tick"),
        limit_up=evidence.get("t_order_limit_up"),
        limit_down=evidence.get("t_order_limit_down"),
        cage_upper=evidence.get("t_order_cage_upper"),
        cage_lower=evidence.get("t_order_cage_lower"),
      )
    except (ArithmeticError, TypeError, ValueError) as exc:
      raise AgentUnavailableError("T_ORDER_POLICY_EVIDENCE_INVALID") from exc
    if not result.allowed:
      raise AgentUnavailableError(result.reason_code)
    protected_limit = result.limit_price
    if protected_limit is None or (
      normalized_role == "ENTRY" and limit_price > protected_limit
    ) or (
      normalized_role == "EXIT" and limit_price < protected_limit
    ):
      raise AgentUnavailableError("T_ORDER_PROTECTED_LIMIT_VIOLATED")
    return {
      version_key: policy.version,
      "t_order_reference_price": str(evidence["t_order_reference_price"]),
      "t_order_price_tick": str(evidence["t_order_price_tick"]),
      "protected_limit_price": str(protected_limit),
    }

  @staticmethod
  def _place_order_expires_at(now: datetime, *, t_trade_role: str) -> datetime:
    role = str(t_trade_role or "").strip().upper()
    if role == "ENTRY":
      return now + timedelta(seconds=TEntryOrderPolicy().order_ttl_seconds)
    if role == "EXIT":
      return now + timedelta(seconds=TExitOrderPolicy().order_ttl_seconds)
    return now + timedelta(minutes=2)

  async def evaluate_t_order_replacement(
    self,
    *,
    client_order_id: str,
    now: datetime,
    reference_price: Decimal,
    price_tick: Decimal,
    limit_up: Decimal | None = None,
    limit_down: Decimal | None = None,
    cage_upper: Decimal | None = None,
    cage_lower: Decimal | None = None,
  ) -> TOrderPolicyResult:
    """Authorize cancel-replace only from converged durable order facts.

    An UNKNOWN result or an unconfirmed cancellation can only reconcile; it
    can never produce a replacement authorization.
    """

    pending = await self.db.get(
      PendingTradeOrder,
      str(client_order_id or ""),
      with_for_update=True,
    )
    if pending is None or str(pending.t_trade_role or "").upper() not in {
      "ENTRY",
      "EXIT",
    }:
      raise AgentUnavailableError("T_ORDER_REPLACE_SOURCE_INVALID")
    metadata = dict(pending.request_metadata or {})
    policy = (
      TEntryOrderPolicy()
      if str(pending.t_trade_role).upper() == "ENTRY"
      else TExitOrderPolicy()
    )
    version_key = (
      "t_entry_order_policy_version"
      if str(pending.t_trade_role).upper() == "ENTRY"
      else "t_exit_order_policy_version"
    )
    if str(metadata.get(version_key) or "") != policy.version:
      raise AgentUnavailableError("T_ORDER_POLICY_VERSION_REQUIRED")
    status = str(pending.status or "").strip().upper()
    broker_order_id = str(pending.broker_order_id or "").strip()
    result_unknown = status in {"UNKNOWN", "RECONCILE_REQUIRED"}
    authoritative_terminal = False
    filled_volume = 0
    if broker_order_id and broker_order_id.isdecimal():
      broker_order = await self.db.get(PersistedOrder, int(broker_order_id))
      correlation = await self.db.scalar(select(OrderCorrelation).where(
        OrderCorrelation.client_order_id == pending.client_order_id,
      ))
      unapplied = await self.db.scalar(select(StrategyRuntimeEvent.event_id).where(
        StrategyRuntimeEvent.client_order_id == pending.client_order_id,
        StrategyRuntimeEvent.application_status != "APPLIED",
      ).limit(1))
      trades = list(
        (
          await self.db.scalars(
            select(Trade).where(
              Trade.order_id == int(broker_order_id),
              Trade.account_id == pending.account_id,
              Trade.stock_code == pending.instrument_code,
            )
          )
        ).all()
      )
      filled_volume = sum(max(0, int(trade.volume or 0)) for trade in trades)
      authoritative_terminal = bool(
        broker_order is not None
        and correlation is not None
        and str(correlation.broker_order_id or "") == broker_order_id
        and all(
          getattr(correlation, field) == getattr(pending, field)
          for field in ("account_id", "owner_type", "owner_id", "environment", "intent_id", "batch_id", "t_trade_role", "bucket", "strategy_run_id")
        )
        and broker_order.account_id == pending.account_id
        and broker_order.stock_code == pending.instrument_code
        and int(broker_order.type) == int(
          PersistedOrderType.BUY if pending.side == "BUY" else PersistedOrderType.SELL
        )
        and int(broker_order.volume or 0) == int(pending.volume or 0)
        and 0 <= filled_volume <= int(pending.volume or 0)
        and all(
          int(trade.volume or 0) > 0
          and int(trade.order_type) == int(broker_order.type)
          for trade in trades
        )
        and int(broker_order.status) in _AUTHORITATIVE_ENTRY_TERMINAL_STATUSES
        and int(broker_order.traded_volume or 0) == filled_volume
        and not unapplied
        and (filled_volume > 0 or status == "RECONCILED_ZERO_FILL")
      )
    original_created_at = getattr(pending, "t_order_original_created_at", None) or pending.created_at
    raw_original = metadata.get("t_order_original_created_at")
    if raw_original and getattr(pending, "t_order_original_created_at", None) is None:
      try:
        original_created_at = datetime.fromisoformat(str(raw_original))
      except ValueError as exc:
        raise AgentUnavailableError("T_ORDER_POLICY_EVIDENCE_INVALID") from exc
    if original_created_at is None:
      raise AgentUnavailableError("T_ORDER_POLICY_EVIDENCE_INVALID")
    if getattr(pending, "t_order_original_created_at", None) is not None:
      original_created_at = to_naive_utc(original_created_at).replace(tzinfo=timezone.utc)
    prior_created_at = pending.created_at
    if getattr(pending, "t_order_original_created_at", None) is not None:
      prior_created_at = to_naive_utc(prior_created_at).replace(tzinfo=timezone.utc)
    return policy.decide_replace(
      now=now,
      original_created_at=original_created_at,
      prior_order_created_at=prior_created_at,
      replace_count=max(0, int(getattr(pending, "t_order_attempt", 0) or 0)),
      requested_volume=int(pending.volume or 0),
      authoritative_filled_volume=filled_volume,
      prior_order_authoritative_terminal=authoritative_terminal,
      result_unknown=result_unknown,
      cancel_unconfirmed=not authoritative_terminal,
      reference_price=reference_price,
      price_tick=price_tick,
      limit_up=limit_up,
      limit_down=limit_down,
      cage_upper=cage_upper,
      cage_lower=cage_lower,
    )

  async def retire_t_order_staged_request(self, pending: PendingTradeOrder) -> None:
    """Close an unsent replacement READY request before finalizing its intent.

    Caller has decided the original lifecycle is over and owns the account
    coordinator. Preserve its request and admission items as audit evidence;
    supersede a PREPARED batch so an already claimed old dispatcher cannot send.
    """
    intent = await self.db.get(TradeIntentRecord, pending.intent_id, with_for_update=True)
    if intent is None or intent.status not in {"PENDING", "APPROVED", "EXECUTION_READY"}:
      return
    metadata = dict(intent.intent_metadata or {})
    staged = dict(metadata.get("risk_increase_order_request") or {})
    if staged.get("t_order_parent_client_id") != pending.client_order_id:
      return
    successor = await self.db.scalar(select(PendingTradeOrder.client_order_id).where(
      PendingTradeOrder.t_order_parent_client_id == pending.client_order_id,
    ))
    if successor is not None:
      return
    batch_id = str(intent.admission_batch_id or "")
    if batch_id:
      batch = await self.db.get(AccountRiskIncreaseAdmissionBatch, batch_id, with_for_update=True)
      if batch is None or batch.status == "COMMITTED":
        raise AgentUnavailableError("T_ORDER_STAGED_ADMISSION_RECONCILE_REQUIRED")
      if batch.status == "PREPARED":
        batch.status = "SUPERSEDED"
        batch.terminal_reason = "T_ORDER_LIFECYCLE_ENDED_BEFORE_DISPATCH"
    metadata["t_order_staged_request_terminal_reason"] = "T_ORDER_LIFECYCLE_ENDED_BEFORE_DISPATCH"
    intent.intent_metadata = metadata
    intent.status = "EXECUTION_PENDING"

  async def replace_t_order(
    self,
    *,
    client_order_id: str,
    quote_at: datetime,
    reference_price: Decimal,
    price_tick: Decimal,
    limit_up: Decimal,
    limit_down: Decimal,
    market_data: MarketDataSnapshot,
  ) -> QueuedTradeCommand:
    """Continue one active intent through its proved next order attempt.

    Reuses the complete public authorization/capacity/admission boundary. The
    old Pending/Correlation/Outbox remain immutable evidence of that attempt.
    """
    now = utcnow()
    if not 0 <= (now - to_naive_utc(quote_at)).total_seconds() <= 2:
      raise AgentUnavailableError("T_ORDER_QUOTE_STALE")
    pending = await self.db.get(PendingTradeOrder, client_order_id, with_for_update=True)
    if pending is None or not t_order_lifecycle_active(pending, now):
      raise AgentUnavailableError("T_ORDER_LIFECYCLE_INACTIVE")
    successor = await self.db.scalar(select(PendingTradeOrder).where(
      PendingTradeOrder.t_order_parent_client_id == client_order_id,
    ))
    if successor is not None:
      outbox = await self.db.scalar(select(TradeCommandOutbox).where(
        TradeCommandOutbox.client_order_id == successor.client_order_id,
      ))
      if outbox is None:
        raise AgentUnavailableError("T_ORDER_REPLACE_CHAIN_INCOMPLETE")
      return QueuedTradeCommand(outbox.client_order_id, outbox.message_id, outbox.delivery_status)
    if (
      market_data.instrument_code != pending.instrument_code
      or market_data.timestamp is None
      or to_naive_utc(market_data.timestamp) != to_naive_utc(quote_at)
      or not market_data.is_trading or market_data.suspended
    ):
      raise AgentUnavailableError("T_ORDER_MARKET_EVIDENCE_INVALID")
    result = await self.evaluate_t_order_replacement(
      client_order_id=client_order_id, now=now.replace(tzinfo=timezone.utc),
      reference_price=reference_price, price_tick=price_tick,
      limit_up=limit_up, limit_down=limit_down,
    )
    if not result.allowed:
      raise AgentUnavailableError(result.reason_code)
    intent = await self.db.get(TradeIntentRecord, pending.intent_id, with_for_update=True)
    if intent is None or str(intent.status).upper() in {
      "FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "RECONCILED_ZERO_FILL",
    }:
      raise AgentUnavailableError("T_ORDER_INTENT_RELEASED")
    metadata = {
      key: value for key, value in dict(pending.request_metadata or {}).items()
      if key in _REQUEST_METADATA_ALLOWLIST and key not in _GENERATED_ORDER_METADATA_KEYS
    }
    metadata.update({
      "quote_timestamp": to_naive_utc(quote_at).replace(tzinfo=timezone.utc).isoformat(),
      "t_order_reference_price": str(reference_price),
      "t_order_price_tick": str(price_tick),
      "t_order_limit_up": str(limit_up),
      "t_order_limit_down": str(limit_down),
      "protected_limit_price": str(result.limit_price),
    })
    intent_metadata = dict(intent.intent_metadata or {})
    account = await self.db.scalar(select(Account).where(Account.account_id == pending.account_id))
    position = await self.db.scalar(select(Position).where(
      Position.account_id == pending.account_id,
      Position.stock_code == pending.instrument_code,
    ))
    if account is None or position is None:
      raise AgentUnavailableError("T_ORDER_REPLACE_SNAPSHOT_MISSING")
    account_state = account.to_dict()
    position_state = {
      **position.to_dict(), "available_volume": int(position.can_use_volume or 0),
      "total_volume": int(position.volume or 0),
    }
    run = await self.db.get(StrategyRun, pending.owner_id) if pending.owner_type == "STRATEGY_RUN" else None
    if pending.t_trade_role == "ENTRY" and (
      run is None or self._enum_value(run.status) != "running"
    ):
      raise AgentUnavailableError("T_ORDER_ENTRY_SOURCE_STOPPED")
    caps = ContextRiskLayer().build_caps(
      portfolio_state={"account": account_state},
      parameters=dict(run.parameters or {}) if run is not None else {},
      instrument_code=pending.instrument_code,
    )
    proposal = TradeIntent(
      strategy_id=str(intent.strategy_id or ""), run_id=str(pending.strategy_run_id or ""),
      instrument_code=pending.instrument_code, direction=pending.side,
      bucket=pending.bucket, reason=str(intent.reason or "T_ORDER_REPLACE"),
      target_volume=result.remaining_volume, intent_id=pending.intent_id,
      execution_ref=ExecutionOwnerRef(pending.owner_type, pending.owner_id),
      origin=ExitPlanIntentOrigin(pending.owner_id) if pending.owner_type == "EXIT_PLAN" else None,
      metadata={"bucket": pending.bucket},
    )
    draft = OrderSizer().draft_intent(
      proposal, OrderType(pending.side), float(result.limit_price), account_state, position_state,
    )
    request = OrderRequest(
      instrument_code=pending.instrument_code, order_type=OrderType(pending.side),
      price_type=PriceType.LIMIT, volume=draft.sized_volume, price=float(result.limit_price),
      execution_ref=proposal.execution_ref, environment=ExecutionEnvironment(pending.environment),
      metadata={"bucket": pending.bucket},
    )
    risk = await TradingRiskChecker(strict_market_data=True, strict_limit_data=True).evaluate_order(
      request, account=account_state, position=position_state, market_data=market_data,
      current_time=now.replace(tzinfo=timezone.utc), risk_caps=caps.to_dict(),
    )
    if not risk.allowed or not 0 < risk.final_volume <= result.remaining_volume:
      raise AgentUnavailableError(f"T_ORDER_REPLACE_RISK:{risk.reason_code}")
    metadata.update({
      "risk_decision_id": risk.risk_decision_id,
      "risk_action": risk.action.value, "risk_reason_code": risk.reason_code,
      "risk_reason_detail": risk.reason_detail,
    })
    staged = dict(intent_metadata.get("risk_increase_order_request") or {})
    already_staged = staged.get("t_order_parent_client_id") == client_order_id
    if not already_staged:
      previous_admission_id = str(getattr(intent, "admission_batch_id", "") or "")
      if previous_admission_id:
        previous_admission = await self.db.get(
          AccountRiskIncreaseAdmissionBatch, previous_admission_id, with_for_update=True,
        )
        if previous_admission is None or str(previous_admission.status) not in {
          "COMMITTED", "SUPERSEDED", "EXPIRED", "FAILED",
        }:
          raise AgentUnavailableError("T_ORDER_REPLACE_ADMISSION_UNFINISHED")
      # Preserve completed admission batches/items as audit facts; only the
      # intent's current admission projection advances to the fresh attempt.
      intent_metadata.pop("risk_increase_order_request", None)
      previous_zero_fill = intent_metadata.pop("qmt_zero_fill_reconciliation", None)
      if previous_zero_fill is not None:
        pending.request_metadata = {
          **dict(pending.request_metadata or {}),
          "qmt_zero_fill_reconciliation": previous_zero_fill,
        }
      intent.intent_metadata = intent_metadata
      intent.admission_batch_id = None
      intent.admission_rank = None
      intent.admission_policy_version = None
      intent.admission_input_fingerprint = None
      exact_auto = bool(intent_metadata.get("exact_auto_exit_authorized") or intent_metadata.get("exact_auto_entry_authorized"))
      intent.status = "PENDING" if exact_auto else "APPROVED"
    attempt = int(pending.t_order_attempt) + 1
    key = (
      f"strategy-exit:{pending.owner_id}:{pending.intent_id}:replace:{attempt}"
      if pending.t_trade_role == "EXIT"
      else f"t-order:{pending.intent_id}:replace:{attempt}"
    )
    return await self.enqueue_order_for_account(
      account_id=pending.account_id, instrument_code=pending.instrument_code,
      side=pending.side, order_type="FIX_PRICE", limit_price=result.limit_price,
      volume=risk.final_volume,
      execution_ref=ExecutionOwnerRef(pending.owner_type, pending.owner_id),
      environment=ExecutionEnvironment(pending.environment),
      idempotency_key=key, trace_id=str(pending.trace_id or ""),
      strategy_run_id=str(pending.strategy_run_id or ""),
      strategy_order_id=str(pending.strategy_order_id or ""),
      intent_id=pending.intent_id, batch_id=pending.batch_id,
      bucket=pending.bucket, t_trade_role=pending.t_trade_role,
      risk_decision_id=risk.risk_decision_id,
      substitution_plan=pending.substitution_plan,
      policy_version=int(metadata.get("config_version") or metadata.get("policy_version") or 0),
      request_metadata=metadata,
      authorization_user_id=str(metadata.get("auto_exit_authorization_user_id") or ""),
      _t_order_parent_client_id=client_order_id,
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
    execution_ref: ExecutionOwnerRef,
    environment: ExecutionEnvironment,
    idempotency_key: str,
    trace_id: str = "",
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
    _locked_live_control: AccountExecutionControl | None = None,
    _admission_batch_id: str = "",
    _admission_rank: int = 0,
    _admission_fence_token: str = "",
    _t_order_parent_client_id: str = "",
  ) -> QueuedTradeCommand:
    idempotency_key, trace_id, raw_idempotency_key = require_stable_command_key(
      idempotency_key,
      trace_id,
    )
    (
      canonical_ref,
      canonical_environment,
      owner_type,
      owner_id,
      canonical_environment_value,
    ) = self._require_execution_identity(execution_ref, environment)
    if owner_type not in _REGISTERED_RUNTIME_OWNER_TYPES:
      raise AgentUnavailableError(
        "TRADE_COMMAND_OWNER_UNREGISTERED:当前 owner 尚未注册 runtime handler"
      )
    normalized_mode = self._wire_execution_mode(canonical_environment)
    normalized_side = str(side or "").strip().upper()
    normalized_instrument = str(instrument_code or "").strip().upper()
    normalized_order_type = self._wire_price_type(order_type)
    if volume <= 0:
      raise ValueError("委托数量必须大于 0")
    if not normalized_side:
      raise ValueError("委托方向不能为空")
    if not normalized_instrument:
      raise ValueError("委托标的不能为空")
    try:
      normalized_limit_price = Decimal(str(limit_price))
    except (TypeError, ValueError, ArithmeticError) as exc:
      raise ValueError("限价委托价格无效") from exc
    if not normalized_limit_price.is_finite() or normalized_limit_price <= 0:
      raise ValueError(
        f"协议 {PROTOCOL_VERSION} PLACE_ORDER 必须使用正数限价"
      )
    normalized_role = t_trade_role.strip().upper()
    if normalized_role not in {"", "ENTRY", "EXIT"}:
      raise ValueError("做 T 订单角色必须是 ENTRY 或 EXIT")
    if (
      normalized_role
      and normalized_side != {"ENTRY": "BUY", "EXIT": "SELL"}[normalized_role]
    ):
      raise AgentUnavailableError("TRADE_INTENT_SCOPE_MISMATCH:做T角色与委托方向不一致")
    if owner_type == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value and (
      normalized_role != "ENTRY" or normalized_side != "BUY"
      or canonical_environment is not ExecutionEnvironment.LIVE or not batch_id or not intent_id
    ):
      raise AgentUnavailableError("T_ENTRY_COMMAND_SCOPE_INVALID")
    if batch_id and not (
      owner_type == ExecutionOwnerType.STRATEGY_RUN.value
      or (owner_type == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value and normalized_role == "ENTRY")
      or (
        owner_type == ExecutionOwnerType.EXIT_PLAN.value
        and normalized_role == "EXIT"
      )
    ):
      raise AgentUnavailableError(
        "TRADE_COMMAND_OWNER_CONFLICT:TTrade 批次必须归属 ENTRY 来源或公共 EXIT_PLAN"
      )
    immutable_metadata = self._sanitize_request_metadata(request_metadata)
    strategy_run_id, strategy_order_id = self._normalize_strategy_identity(
      owner_type,
      owner_id,
      strategy_run_id,
      strategy_order_id,
    )
    if manual_live and owner_type != ExecutionOwnerType.MANUAL_COMMAND.value:
      raise AgentUnavailableError(
        "TRADE_COMMAND_OWNER_CONFLICT:manual_live 只能用于 MANUAL_COMMAND"
      )
    if manual_live and normalized_mode != "live":
      raise ValueError("手动实盘授权只能用于 live 交易命令")
    risk_reducing = normalized_role == "EXIT" or normalized_side == "SELL"
    if risk_decision_id:
      immutable_metadata.setdefault("risk_decision_id", str(risk_decision_id))
    if reason_tags:
      immutable_metadata.setdefault(
        "reason_tags",
        sorted({str(value).strip() for value in reason_tags if str(value).strip()}),
      )
    if substitution_plan is not None:
      immutable_metadata.setdefault("substitution_plan", substitution_plan)
    admission_required = (
      normalized_mode == "live"
      and normalized_side == "BUY"
      and not _admission_batch_id
    )
    if admission_required:
      await self._preview_live_authorization(
        account_id,
        risk_reducing=False,
        require_controlled_window=manual_live,
      )
    if _locked_live_control is not None:
      if normalized_mode != "live" or str(_locked_live_control.account_id or "") != str(
        account_id or ""
      ):
        raise ValueError("预锁账户控制与 LIVE 命令不匹配")
    elif manual_live and not admission_required:
      # This lock must precede the outbox lookup/insert to match the account
      # hard-kill control -> pending/outbox lock order.
      _locked_live_control = await self._require_manual_live_authorization(
        account_id,
        risk_reducing=risk_reducing,
      )
    elif normalized_mode == "live" and not admission_required:
      _locked_live_control = await self._require_live_authorization(
        account_id,
        risk_reducing=risk_reducing,
      )

    business_idempotency_key = self.order_idempotency_digest(
      user_id=user_id,
      account_id=account_id,
      idempotency_key=raw_idempotency_key,
      execution_ref=canonical_ref,
      environment=canonical_environment,
    )
    existing = (
      await self.db.execute(
        select(TradeCommandOutbox).where(
          TradeCommandOutbox.idempotency_key == business_idempotency_key
        ).with_for_update()
      )
    ).scalar_one_or_none()
    if existing is not None:
      retry_intent_id = intent_id
      if (
        normalized_mode == "live"
        and normalized_side == "BUY"
        and owner_type == ExecutionOwnerType.MANUAL_COMMAND.value
        and not str(retry_intent_id or "").strip()
      ):
        retry_intent_id = str(
          uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"quantx:risk-increase:{business_idempotency_key}",
          )
        )
      if not await self._idempotent_order_chain_matches_request(
        existing,
        user_id=user_id,
        account_id=account_id,
        owner_type=owner_type,
        owner_id=owner_id,
        environment=canonical_environment_value,
        instrument_code=normalized_instrument,
        side=normalized_side,
        order_type=normalized_order_type,
        limit_price=normalized_limit_price,
        volume=volume,
        strategy_run_id=strategy_run_id,
        strategy_order_id=strategy_order_id,
        intent_id=retry_intent_id,
        batch_id=batch_id,
        bucket=bucket,
        t_trade_role=normalized_role,
        risk_decision_id=risk_decision_id,
        trace_id=trace_id,
        substitution_plan=substitution_plan,
        request_metadata=immutable_metadata,
      ):
        raise AgentUnavailableError(
          "IDEMPOTENCY_KEY_CONFLICT:同一幂等键对应不同交易请求"
        )
      return QueuedTradeCommand(
        existing.client_order_id,
        existing.message_id,
        existing.delivery_status,
      )

    if (
      normalized_mode == "live" and normalized_side == "BUY" and normalized_order_type != "FIX_PRICE"
    ):
      raise AgentUnavailableError(
        "ACCOUNT_CAPACITY_LIMIT_PRICE_REQUIRED:实盘买入必须使用有价格上限的限价委托"
      )
    accepted_intent = await self._require_durable_order_intent(
      account_id=account_id,
      instrument_code=normalized_instrument,
      side=normalized_side,
      execution_mode=normalized_mode,
      execution_ref=canonical_ref,
      environment=canonical_environment,
      strategy_run_id=strategy_run_id,
      strategy_order_id=strategy_order_id,
      intent_id=intent_id,
      batch_id=batch_id,
      bucket=bucket,
      t_order_parent_client_id=_t_order_parent_client_id,
    )
    if normalized_role and accepted_intent is None:
      raise AgentUnavailableError(
        "TRADE_INTENT_REQUIRED:做T委托必须绑定已持久化意图与批次"
      )
    if accepted_intent is not None:
      accepted_metadata = dict(accepted_intent.intent_metadata or {})
      accepted_role = str(accepted_metadata.get("t_trade_role") or "").upper()
      if accepted_role != normalized_role or (
        accepted_role and str(accepted_metadata.get("t_batch_id") or "") != batch_id
      ):
        raise AgentUnavailableError(
          "TRADE_INTENT_SCOPE_MISMATCH:做T角色或批次与持久化意图不一致"
        )
      if normalized_role:
        prior_t_order = await self.db.scalar(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.environment == canonical_environment_value,
            PendingTradeOrder.batch_id == batch_id,
            PendingTradeOrder.t_trade_role == normalized_role,
          )
          .order_by(PendingTradeOrder.created_at.desc())
          .limit(1)
        )
        if prior_t_order is not None and not _t_order_parent_client_id:
          raise AgentUnavailableError(
            "T_ORDER_REPLACE_PROOF_REQUIRED:已有同批次同角色订单，禁止用新意图重置生命周期"
          )
        if _t_order_parent_client_id:
          try:
            quote_at = datetime.fromisoformat(str(immutable_metadata["quote_timestamp"]))
          except (KeyError, TypeError, ValueError) as exc:
            raise AgentUnavailableError("T_ORDER_QUOTE_STALE") from exc
          if not 0 <= (utcnow() - to_naive_utc(quote_at)).total_seconds() <= 2:
            raise AgentUnavailableError("T_ORDER_QUOTE_STALE")
          if (
            prior_t_order is None
            or prior_t_order.client_order_id != _t_order_parent_client_id
            or prior_t_order.intent_id != intent_id
            or prior_t_order.owner_type != owner_type
            or prior_t_order.owner_id != owner_id
            or not t_order_lifecycle_active(prior_t_order, utcnow())
          ):
            raise AgentUnavailableError("T_ORDER_REPLACE_SOURCE_INVALID")
          replacement = await self.evaluate_t_order_replacement(
            client_order_id=_t_order_parent_client_id,
            now=utcnow().replace(tzinfo=timezone.utc),
            reference_price=Decimal(str(immutable_metadata.get("t_order_reference_price") or 0)),
            price_tick=Decimal(str(immutable_metadata.get("t_order_price_tick") or 0)),
            limit_up=immutable_metadata.get("t_order_limit_up"),
            limit_down=immutable_metadata.get("t_order_limit_down"),
            cage_upper=immutable_metadata.get("t_order_cage_upper"),
            cage_lower=immutable_metadata.get("t_order_cage_lower"),
          )
          if not replacement.allowed:
            raise AgentUnavailableError(replacement.reason_code)
          if not 0 < volume <= replacement.remaining_volume or normalized_limit_price != replacement.limit_price:
            raise AgentUnavailableError("T_ORDER_REPLACE_REQUEST_MISMATCH")
        batch = await self.db.get(TTradeBatch, batch_id, with_for_update=True)
        if owner_type == ExecutionOwnerType.EXIT_PLAN.value:
          exit_plan = await self.db.get(AutoExitPlanRecord, owner_id)
          expected_source_owner_type = str(
            getattr(exit_plan, "source_execution_owner_type", "") or ""
          )
          expected_source_owner_id = str(
            getattr(exit_plan, "source_execution_owner_id", "") or ""
          )
          expected_source_environment = str(
            getattr(exit_plan, "source_execution_environment", "") or ""
          )
        else:
          expected_source_owner_type = owner_type
          expected_source_owner_id = owner_id
          expected_source_environment = canonical_environment_value
        if (
          not batch_id
          or (normalized_role == "EXIT" and batch is None)
          or (
            batch is not None
            and (
              batch.account_id != account_id
              or batch.instrument_code != normalized_instrument
              or str(batch.environment or "").strip().upper()
              != canonical_environment_value
              or str(getattr(batch, "source_execution_owner_type", "") or "")
              != expected_source_owner_type
              or str(getattr(batch, "source_execution_owner_id", "") or "")
              != expected_source_owner_id
              or str(getattr(batch, "source_execution_environment", "") or "")
              != expected_source_environment
              or (
                owner_type == ExecutionOwnerType.STRATEGY_RUN.value
                and batch.strategy_run_id != owner_id
              )
            )
          )
        ):
          raise AgentUnavailableError(
            "TRADE_INTENT_SCOPE_MISMATCH:做T批次与委托归属不一致"
          )
    if admission_required:
      staged_intent_id = await self._stage_risk_increase_order_request(
        accepted_intent=accepted_intent,
        business_idempotency_key=business_idempotency_key,
        user_id=user_id,
        account_id=account_id,
        instrument_code=normalized_instrument,
        owner_type=owner_type,
        owner_id=owner_id,
        environment=canonical_environment_value,
        idempotency_key=raw_idempotency_key,
        trace_id=trace_id,
        strategy_run_id=strategy_run_id,
        strategy_order_id=strategy_order_id,
        batch_id=batch_id,
        bucket=bucket,
        t_trade_role=normalized_role,
        risk_decision_id=risk_decision_id,
        substitution_plan=substitution_plan,
        policy_version=policy_version,
        order_type=normalized_order_type,
        limit_price=normalized_limit_price,
        volume=volume,
        request_metadata=immutable_metadata,
        manual_live=manual_live,
        t_order_parent_client_id=_t_order_parent_client_id,
      )
      await asyncio.sleep(RISK_ADMISSION_COLLECTION_WINDOW_SECONDS)
      try:
        dispatched = await self.dispatch_ready_risk_increase_orders(
          account_id=account_id,
          processing_owner=f"trade-command:{owner_type}:{owner_id}",
        )
      except Exception as exc:
        await self.db.rollback()
        contention_codes = (
          "RISK_ADMISSION_LEASE_HELD",
          "RISK_ADMISSION_READY_SET_CHANGED",
          "RISK_ADMISSION_BATCH_ALREADY_COMMITTED",
          "RISK_ADMISSION_EMPTY_BATCH",
          "RISK_ADMISSION_INTENT_ALREADY_ASSIGNED",
        )
        if isinstance(exc, IntegrityError) or any(
          code in str(exc) for code in contention_codes
        ):
          queued = await self._await_queued_risk_increase_order(staged_intent_id)
          if queued is not None:
            return queued
        raise
      queued = dispatched.get(staged_intent_id)
      if queued is None:
        queued = await self._queued_risk_increase_order(staged_intent_id)
      if queued is None:
        raise AgentUnavailableError("RISK_ADMISSION_DISPATCH_INCOMPLETE")
      return queued
    admission_capacity: dict[str, Any] | None = None
    if _locked_live_control is not None:
      admission_capacity = await self._require_account_capacity(
        _locked_live_control,
        instrument_code=instrument_code,
        side=normalized_side,
        limit_price=normalized_limit_price,
        volume=volume,
        intent=accepted_intent,
        batch_id=batch_id,
        t_trade_role=normalized_role,
      )
      immutable_metadata["account_capacity"] = admission_capacity
    if normalized_mode == "live" and normalized_side == "BUY":
      await self._require_risk_increase_admission(
        intent=accepted_intent,
        admission_batch_id=_admission_batch_id,
        admission_rank=_admission_rank,
        admission_fence_token=_admission_fence_token,
      )
      immutable_metadata.update(
        {
          "admission_batch_id": _admission_batch_id,
          "admission_rank": int(_admission_rank),
          "admission_policy_version": str(
            getattr(accepted_intent, "admission_policy_version", "") or ""
          ),
          "admission_input_fingerprint": str(
            getattr(accepted_intent, "admission_input_fingerprint", "") or ""
          ),
        }
      )
    if normalized_mode == "live" and normalized_role:
      immutable_metadata.update(
        self._require_t_order_new_policy(
          role=normalized_role,
          order_type=normalized_order_type,
          limit_price=normalized_limit_price,
          intent=accepted_intent,
          request_metadata=immutable_metadata,
        )
      )
      immutable_metadata.setdefault(
        "t_order_original_created_at",
        time_utils.now().isoformat(),
      )
      immutable_metadata.setdefault("t_order_replace_count", 0)
      if _t_order_parent_client_id:
        immutable_metadata["t_order_original_created_at"] = (
          prior_t_order.t_order_original_created_at.replace(tzinfo=timezone.utc).isoformat()
        )
        immutable_metadata["t_order_replace_count"] = prior_t_order.t_order_attempt + 1
    if owner_type == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value:
      device = await self._t_entry_device(
        intent=accepted_intent, account_id=account_id,
        instrument_code=normalized_instrument, volume=volume,
        limit_price=normalized_limit_price,
      )
      if device.user_id != user_id:
        raise AgentUnavailableError("T_ENTRY_COMMAND_ACTOR_MISMATCH")
    else:
      device = await self._device_for(
        user_id=user_id,
        account_id=account_id,
        execution_mode=normalized_mode,
      )
    if normalized_mode == "live" and not risk_reducing:
      await self._require_live_market_stream_ready(device)
    now = utcnow()
    client_order_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    expires_at = self._place_order_expires_at(
      now,
      t_trade_role=normalized_role,
    )
    if _t_order_parent_client_id:
      policy = TEntryOrderPolicy() if normalized_role == "ENTRY" else TExitOrderPolicy()
      expires_at = min(expires_at, prior_t_order.t_order_original_created_at + timedelta(seconds=policy.total_ttl_seconds))
    wire_expires_at = expires_at.replace(tzinfo=timezone.utc)
    payload = TradeCommandPayload(
      command_kind="PLACE_ORDER",
      client_order_id=client_order_id,
      account_id=account_id,
      execution_mode=normalized_mode,
      instrument_code=normalized_instrument,
      side=normalized_side,
      price_type=normalized_order_type,
      limit_price=str(normalized_limit_price),
      volume=volume,
      expires_at=wire_expires_at,
    ).model_dump(mode="json")
    projected_strategy_run_id = (
      owner_id if owner_type == ExecutionOwnerType.STRATEGY_RUN.value else None
    )
    # Every PLACE gets one durable correlation.  Non-strategy commands keep
    # strategy identities nullable; the explicit owner is their durable
    # identity and no synthetic strategy projection is allowed.
    projected_strategy_order_id = (
      strategy_order_id if owner_type == ExecutionOwnerType.STRATEGY_RUN.value else None
    )
    projected_intent_id = str(intent_id or "").strip() or None
    self.db.add(
      PendingTradeOrder(
        client_order_id=client_order_id,
        user_id=user_id,
        account_id=account_id,
        owner_type=owner_type,
        owner_id=owner_id,
        environment=canonical_environment_value,
        instrument_code=normalized_instrument,
        side=normalized_side,
        order_type=normalized_order_type,
        limit_price=str(normalized_limit_price),
        volume=volume,
        status="QUEUED",
        strategy_run_id=projected_strategy_run_id,
        strategy_order_id=projected_strategy_order_id,
        intent_id=projected_intent_id,
        batch_id=batch_id or None,
        bucket=bucket or "manual",
        t_trade_role=normalized_role or None,
        t_order_attempt=(prior_t_order.t_order_attempt + 1 if _t_order_parent_client_id else 0),
        t_order_parent_client_id=_t_order_parent_client_id or None,
        t_order_original_created_at=(
          prior_t_order.t_order_original_created_at if _t_order_parent_client_id
          else now if normalized_role and normalized_mode == "live" else None
        ),
        risk_decision_id=risk_decision_id or None,
        trace_id=trace_id or message_id,
        substitution_plan=substitution_plan,
        request_metadata=immutable_metadata,
      )
    )
    self.db.add(
      OrderCorrelation(
        id=str(uuid.uuid4()),
        client_order_id=client_order_id,
        account_id=account_id,
        owner_type=owner_type,
        owner_id=owner_id,
        environment=canonical_environment_value,
        strategy_run_id=projected_strategy_run_id,
        strategy_order_id=projected_strategy_order_id,
        intent_id=projected_intent_id,
        batch_id=batch_id or None,
        bucket=bucket or "manual",
        t_trade_role=normalized_role or None,
        risk_decision_id=risk_decision_id or None,
        trace_id=trace_id or message_id,
        substitution_plan=substitution_plan,
        request_metadata=immutable_metadata,
      )
    )
    if batch_id:
      batch = await self.db.get(TTradeBatch, batch_id)
      if batch is None:
        if normalized_role != "ENTRY" or owner_type not in {"STRATEGY_RUN", "T_ASSISTANT_EXECUTION"}:
          raise AgentUnavailableError(
            "TRADE_INTENT_SCOPE_MISMATCH:公共退出计划缺少持久化做T批次"
          )
        cost_snapshot = extract_t_trade_cost_snapshot(immutable_metadata)
        batch = TTradeBatch(
          batch_id=batch_id,
          account_id=account_id,
          instrument_code=normalized_instrument,
          strategy_run_id=projected_strategy_run_id,
          source_execution_owner_type=owner_type,
          source_execution_owner_id=owner_id,
          source_execution_environment=canonical_environment_value,
          target_volume=volume,
          environment=canonical_environment_value,
          metrics_origin="RULE_ESTIMATE",
          commission_rate=(
            cost_snapshot.commission_rate if cost_snapshot is not None else None
          ),
          minimum_commission=(
            cost_snapshot.minimum_commission if cost_snapshot is not None else None
          ),
          stamp_tax_rate=(
            cost_snapshot.stamp_tax_rate if cost_snapshot is not None else None
          ),
          transfer_fee_rate=(
            cost_snapshot.transfer_fee_rate if cost_snapshot is not None else None
          ),
          policy_version=max(0, int(policy_version or 0)),
        )
        self.db.add(batch)
      if normalized_role == "ENTRY":
        # Entry creation freezes the execution and cost model.  Exit orders
        # must not rewrite a batch using later global settings.
        if str(batch.environment or "").strip().upper() != canonical_environment_value:
          raise AgentUnavailableError(
            "TRADE_INTENT_SCOPE_MISMATCH:TTrade 批次执行环境不可变"
          )
        batch.metrics_origin = batch.metrics_origin or "RULE_ESTIMATE"
        if batch.commission_rate is None:
          cost_snapshot = extract_t_trade_cost_snapshot(immutable_metadata)
          if cost_snapshot is not None:
            batch.commission_rate = cost_snapshot.commission_rate
            batch.minimum_commission = cost_snapshot.minimum_commission
            batch.stamp_tax_rate = cost_snapshot.stamp_tax_rate
            batch.transfer_fee_rate = cost_snapshot.transfer_fee_rate
        batch.entry_intent_id = intent_id or None
        batch.entry_client_order_id = client_order_id
        batch.status = "ENTRY_QUEUED"
      elif normalized_role == "EXIT":
        batch.exit_intent_id = intent_id or None
        batch.exit_client_order_id = client_order_id
        batch.exit_reason = batch.exit_reason or (
          str(immutable_metadata.get("exit_reason") or "").strip() or None
        )
        batch.status = "EXIT_TRIGGERED"
    self.db.add(
      TradeCommandOutbox(
        message_id=message_id,
        client_order_id=client_order_id,
        idempotency_key=business_idempotency_key,
        device_id=device.id,
        account_id=account_id,
        owner_type=owner_type,
        owner_id=owner_id,
        environment=canonical_environment_value,
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
          ).with_for_update()
        )
      ).scalar_one_or_none()
      if existing is None:
        raise
      if not await self._idempotent_order_chain_matches_request(
        existing,
        user_id=user_id,
        account_id=account_id,
        owner_type=owner_type,
        owner_id=owner_id,
        environment=canonical_environment_value,
        instrument_code=normalized_instrument,
        side=normalized_side,
        order_type=normalized_order_type,
        limit_price=normalized_limit_price,
        volume=volume,
        strategy_run_id=strategy_run_id,
        strategy_order_id=strategy_order_id,
        intent_id=intent_id,
        batch_id=batch_id,
        bucket=bucket,
        t_trade_role=normalized_role,
        risk_decision_id=risk_decision_id,
        trace_id=trace_id,
        substitution_plan=substitution_plan,
        request_metadata=immutable_metadata,
      ):
        raise AgentUnavailableError(
          "IDEMPOTENCY_KEY_CONFLICT:同一幂等键对应不同交易请求"
        )
      return QueuedTradeCommand(
        existing.client_order_id,
        existing.message_id,
        existing.delivery_status,
      )
    return QueuedTradeCommand(client_order_id, message_id, "QUEUED")

  async def _require_risk_increase_admission(
    self,
    *,
    intent: TradeIntentRecord | None,
    admission_batch_id: str,
    admission_rank: int,
    admission_fence_token: str,
  ) -> None:
    """Require the public durable admission claim for every LIVE BUY."""

    if intent is None:
      raise AgentUnavailableError("RISK_ADMISSION_INTENT_REQUIRED")
    if str(intent.status or "").upper() != "EXECUTION_READY":
      raise AgentUnavailableError("RISK_ADMISSION_INTENT_NOT_READY")
    if not admission_batch_id or admission_rank <= 0 or not admission_fence_token:
      raise AgentUnavailableError("RISK_ADMISSION_REQUIRED")
    if (
      str(intent.admission_batch_id or "") != admission_batch_id
      or int(intent.admission_rank or 0) != int(admission_rank)
      or not str(intent.admission_policy_version or "")
      or not str(intent.admission_input_fingerprint or "")
    ):
      raise AgentUnavailableError("RISK_ADMISSION_INTENT_CONFLICT")
    batch = await self.db.get(
      AccountRiskIncreaseAdmissionBatch,
      admission_batch_id,
      with_for_update=True,
    )
    item = await self.db.scalar(
      select(AccountRiskIncreaseAdmissionItem).where(
        AccountRiskIncreaseAdmissionItem.admission_batch_id == admission_batch_id,
        AccountRiskIncreaseAdmissionItem.intent_id == str(intent.id),
      )
    )
    if (
      batch is None
      or item is None
      or str(batch.status or "") != "PREPARED"
      or str(batch.processing_fence_token or "") != admission_fence_token
      or int(item.admission_rank or 0) != int(admission_rank)
      or str(item.owner_type or "") != str(intent.owner_type or "")
      or str(item.owner_id or "") != str(intent.owner_id or "")
    ):
      raise AgentUnavailableError("RISK_ADMISSION_CLAIM_CONFLICT")

  async def _stage_risk_increase_order_request(
    self,
    *,
    accepted_intent: TradeIntentRecord | None,
    business_idempotency_key: str,
    user_id: str,
    account_id: str,
    instrument_code: str,
    owner_type: str,
    owner_id: str,
    environment: str,
    idempotency_key: str,
    trace_id: str,
    strategy_run_id: str,
    strategy_order_id: str,
    batch_id: str,
    bucket: str,
    t_trade_role: str,
    risk_decision_id: str,
    substitution_plan: Mapping[str, Any] | None,
    policy_version: int,
    order_type: str,
    limit_price: Decimal,
    volume: int,
    request_metadata: Mapping[str, Any],
    manual_live: bool = False,
    t_order_parent_client_id: str = "",
    commit: bool = True,
  ) -> str:
    """Persist one complete READY request before entering the account queue."""

    intent = accepted_intent
    if intent is None:
      if owner_type != ExecutionOwnerType.MANUAL_COMMAND.value:
        raise AgentUnavailableError("RISK_ADMISSION_INTENT_REQUIRED")
      intent_id = str(
        uuid.uuid5(
          uuid.NAMESPACE_URL,
          f"quantx:risk-increase:{business_idempotency_key}",
        )
      )
      intent = await self.db.get(
        TradeIntentRecord,
        intent_id,
        with_for_update=True,
        populate_existing=True,
      )
      if intent is None:
        intent = TradeIntentRecord(
          id=intent_id,
          strategy_run_id=None,
          owner_type=owner_type,
          owner_id=owner_id,
          environment=environment,
          idempotency_key=f"risk-increase:{business_idempotency_key}",
          account_id=account_id,
          strategy_id=None,
          instrument_code=instrument_code,
          direction="BUY",
          bucket=bucket,
          reason="MANUAL_RISK_INCREASE",
          priority="URGENT",
          target_volume=int(volume),
          limit_price_hint=float(limit_price),
          trace_id=trace_id or None,
          status="EXECUTION_READY",
          intent_metadata={},
        )
        self.db.add(intent)
    if (
      str(intent.account_id or "") != account_id
      or str(intent.instrument_code or "").upper() != instrument_code
      or str(intent.direction or "").upper() != "BUY"
      or str(intent.owner_type or "").upper() != owner_type
      or str(intent.owner_id or "") != owner_id
      or str(intent.environment or "").upper() != environment
    ):
      raise AgentUnavailableError("RISK_ADMISSION_INTENT_CONFLICT")
    if str(intent.status or "").upper() not in {
      "PENDING",
      "APPROVED",
      "EXECUTION_READY",
    }:
      raise AgentUnavailableError("RISK_ADMISSION_INTENT_NOT_READY")
    durable_request = {
      "version": "risk-increase-order-request.v1",
      "user_id": str(user_id),
      "account_id": account_id,
      "instrument_code": instrument_code,
      "owner_type": owner_type,
      "owner_id": owner_id,
      "environment": environment,
      "idempotency_key": idempotency_key,
      "trace_id": trace_id,
      "strategy_run_id": strategy_run_id,
      "strategy_order_id": strategy_order_id,
      "intent_id": str(intent.id),
      "batch_id": batch_id,
      "bucket": bucket,
      "t_trade_role": t_trade_role,
      "risk_decision_id": risk_decision_id,
      "substitution_plan": dict(substitution_plan or {}),
      "policy_version": int(policy_version or 0),
      "order_type": order_type,
      "limit_price": str(limit_price),
      "volume": int(volume),
      "request_metadata": dict(request_metadata or {}),
      "manual_live": bool(manual_live),
      "t_order_parent_client_id": t_order_parent_client_id,
    }
    metadata = dict(intent.intent_metadata or {})
    existing_request = metadata.get("risk_increase_order_request")
    if existing_request is not None and dict(existing_request) != durable_request:
      previous = dict(existing_request)
      # This request has not crossed the Pending/Outbox boundary. The caller
      # has re-proved the same terminal parent and rerun sizing/risk/authority.
      # Keep the current admission pointer until prepare_batch atomically
      # supersedes its stale fingerprint and ranks the fresh complete READY set.
      stable_fields = (
        "user_id", "account_id", "instrument_code", "owner_type", "owner_id",
        "environment", "idempotency_key", "trace_id", "strategy_run_id",
        "strategy_order_id", "intent_id", "batch_id", "bucket", "t_trade_role",
        "t_order_parent_client_id",
      )
      if not t_order_parent_client_id or any(
        previous.get(field) != durable_request.get(field) for field in stable_fields
      ):
        raise AgentUnavailableError("RISK_ADMISSION_ORDER_REQUEST_CONFLICT")
    metadata["risk_increase_order_request"] = durable_request
    intent.intent_metadata = metadata
    intent.status = "EXECUTION_READY"
    if commit:
      await self.db.commit()
    else:
      await self.db.flush()
    return str(intent.id)

  @staticmethod
  def _ready_order_request(intent: TradeIntentRecord) -> dict[str, Any]:
    intent_metadata = dict(intent.intent_metadata or {})
    raw = intent_metadata.pop("risk_increase_order_request", None)
    request = dict(raw) if isinstance(raw, Mapping) else {}
    if request.get("version") != "risk-increase-order-request.v1":
      raise AgentUnavailableError("RISK_ADMISSION_ORDER_REQUEST_MISSING")
    expected = {
      "account_id": str(intent.account_id or ""),
      "instrument_code": str(intent.instrument_code or "").upper(),
      "owner_type": str(intent.owner_type or "").upper(),
      "owner_id": str(intent.owner_id or ""),
      "environment": str(intent.environment or "").upper(),
      "intent_id": str(intent.id),
      "bucket": str(intent.bucket or ""),
    }
    if any(str(request.get(key) or "") != value for key, value in expected.items()):
      raise AgentUnavailableError("RISK_ADMISSION_ORDER_REQUEST_CONFLICT")
    return {
      "user_id": str(request.get("user_id") or ""),
      "account_id": expected["account_id"],
      "instrument_code": expected["instrument_code"],
      "order_type": str(request.get("order_type") or ""),
      "limit_price": Decimal(str(request.get("limit_price") or "0")),
      "volume": int(request.get("volume") or 0),
      "execution_ref": ExecutionOwnerRef(
        ExecutionOwnerType(expected["owner_type"]),
        expected["owner_id"],
      ),
      "idempotency_key": str(request.get("idempotency_key") or ""),
      "trace_id": str(request.get("trace_id") or ""),
      "strategy_run_id": str(request.get("strategy_run_id") or ""),
      "strategy_order_id": str(request.get("strategy_order_id") or ""),
      "intent_id": expected["intent_id"],
      "batch_id": str(request.get("batch_id") or ""),
      "bucket": expected["bucket"],
      "t_trade_role": str(request.get("t_trade_role") or ""),
      "risk_decision_id": str(request.get("risk_decision_id") or ""),
      "substitution_plan": dict(request.get("substitution_plan") or {}),
      "policy_version": int(request.get("policy_version") or 0),
      "request_metadata": {
        **{
          key: value for key, value in intent_metadata.items()
          if key in _REQUEST_METADATA_ALLOWLIST and key not in _GENERATED_ORDER_METADATA_KEYS
        },
        **dict(request.get("request_metadata") or {}),
      },
      "manual_live": bool(request.get("manual_live")),
      "_t_order_parent_client_id": str(request.get("t_order_parent_client_id") or ""),
    }

  async def _t_entry_device(
    self, *, intent: TradeIntentRecord, account_id: str,
    instrument_code: str, volume: int, limit_price: Decimal,
  ) -> AgentDevice:
    """Revalidate T's own consumed confirmation or LIVE_AUTO rollout authority."""
    if intent.owner_type == "T_ASSISTANT_EXECUTION":
      from quantx_infrastructure.services.live_entry_dispatch_review import (
        revalidate_live_entry_dispatch,
      )
      from quantx_infrastructure.services.t_live_entry_authorization import (
        authorize_live_entry,
      )

      try:
        await revalidate_live_entry_dispatch(
          self.db, intent=intent, volume=volume, limit_price=limit_price,
          now=datetime.now(timezone.utc), fresh_review=self.live_entry_review,
        )
        actor_id = await authorize_live_entry(
          self.db, intent=intent, account_id=account_id,
          instrument_code=instrument_code, volume=volume, limit_price=limit_price,
          now=datetime.now(timezone.utc),
        )
      except (TypeError, ValueError) as exc:
        raise AgentUnavailableError(str(exc)) from exc
      return await self._device_for(
        user_id=actor_id, account_id=account_id, execution_mode="live",
      )
    metadata = dict(intent.intent_metadata or {})
    if (
      intent.owner_type != "STRATEGY_RUN" or intent.environment != "LIVE"
      or intent.direction != "BUY" or intent.account_id != account_id
      or intent.instrument_code != instrument_code
      or str(metadata.get("t_trade_role") or "").upper() != "ENTRY"
      or not str(metadata.get("t_batch_id") or "")
      or intent.status not in {"APPROVED", "EXECUTION_READY"}
    ):
      raise AgentUnavailableError("T_ENTRY_AUTHORIZATION_SCOPE_INVALID")
    run = await self.db.get(StrategyRun, intent.owner_id)
    if (
      run is None or self._enum_value(run.status) != "running"
      or self._enum_value(run.mode) != "live"
      or str(dict(run.parameters or {}).get("account_id") or "") != account_id
    ):
      raise AgentUnavailableError("T_ENTRY_SOURCE_NOT_RUNNING")
    try:
      envelope = build_t_trade_entry_exit_authorization_envelope(intent)
    except (TypeError, ValueError) as exc:
      raise AgentUnavailableError("T_ENTRY_AUTHORIZATION_SCOPE_INVALID") from exc
    subject = envelope.subject
    actor_id = ""
    if str(metadata.get("approval_mode") or "").upper() == "LIVE_AUTO":
      from quantx_infrastructure.services.t_trade_operations_service import (
        TTradeOperationsService,
      )

      readiness = await TTradeOperationsService().readiness(account_id)
      if not (
        str(readiness.get("stage") or "").upper() == "LIVE"
        and readiness.get("rollout_enabled") and readiness.get("automation_ready")
        and readiness.get("can_approve") and not readiness.get("kill_switch")
      ):
        raise AgentUnavailableError("LIVE_AUTO_AUTHORITY_NOT_READY")
    else:
      actor_id = str(metadata.get("t_trade_entry_approval_user_id") or "")
      session_id = str(metadata.get("t_trade_entry_approval_device_session_id") or "")
      challenge_id = str(metadata.get("t_trade_entry_approval_challenge_id") or "")
      challenge = await self.db.get(TradeConfirmationChallenge, challenge_id) if challenge_id else None
      if challenge is None or not actor_id or not session_id:
        raise AgentUnavailableError("T_TRADE_ENTRY_DEVICE_CHALLENGE_REQUIRED")
      payload = dict(challenge.payload or {})
      binding = dict(payload.get(T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY) or {})
      expected = {
        "action": T_TRADE_ENTRY_APPROVAL_ACTION, "user_id": actor_id,
        "device_session_id": session_id, "account_id": account_id,
        "owner_type": "STRATEGY_RUN", "owner_id": str(intent.owner_id),
        "environment": "LIVE", "intent_id": str(intent.id),
      }
      try:
        payload_fingerprint = trade_confirmation_payload_fingerprint(payload)
        bound_subject = dict(binding.get("subject") or {})
        envelope = build_t_trade_entry_exit_authorization_envelope(
          intent, max_protected_volume=int(bound_subject.get("max_protected_volume") or 0),
        )
      except (TypeError, ValueError) as exc:
        raise AgentUnavailableError("T_ENTRY_CHALLENGE_INVALID") from exc
      if (
        challenge.consumed_at is None or challenge.expires_at is None
        or to_naive_utc(challenge.expires_at) <= utcnow()
        or any(str(getattr(challenge, key, "") or "") != expected[key]
               for key in ("action", "user_id", "device_session_id", "account_id", "owner_type", "owner_id", "environment"))
        or any(str(payload.get(key) or "") != value for key, value in expected.items())
        or not hmac.compare_digest(str(challenge.payload_fingerprint or ""), payload_fingerprint)
        or envelope.subject != bound_subject
        or not hmac.compare_digest(envelope.fingerprint, str(binding.get("fingerprint") or ""))
      ):
        raise AgentUnavailableError("T_ENTRY_CHALLENGE_INVALID")
      subject = envelope.subject
    filled = max(0, int(intent.executed_volume or 0))
    if volume <= 0 or filled + int(volume) > int(subject["max_protected_volume"]):
      raise AgentUnavailableError("T_ENTRY_AUTHORIZED_VOLUME_EXCEEDED")
    reference = Decimal(str(subject.get("entry_reference_price") or 0))
    deviation = Decimal(str(subject.get("entry_max_price_deviation_bps") or 0))
    if reference <= 0 or limit_price > reference * (1 + deviation / Decimal(10000)):
      raise AgentUnavailableError("T_ENTRY_AUTHORIZED_PRICE_EXCEEDED")
    return (
      await self._device_for(user_id=actor_id, account_id=account_id, execution_mode="live")
      if actor_id else await self._device_for_account(account_id, "live")
    )

  async def _revalidate_ready_order_request(
    self,
    *,
    intent: TradeIntentRecord,
    request: Mapping[str, Any],
  ) -> None:
    """Repeat producer-specific mutable gates inside the admission lock."""

    execution_ref = request.get("execution_ref")
    if (
      not isinstance(execution_ref, ExecutionOwnerRef)
      or execution_ref.owner_type not in {
        ExecutionOwnerType.STRATEGY_RUN, ExecutionOwnerType.T_ASSISTANT_EXECUTION,
      }
    ):
      return
    metadata = dict(request.get("request_metadata") or {})
    if str(dict(intent.intent_metadata or {}).get("t_trade_role") or "").upper() == "ENTRY":
      device = await self._t_entry_device(
        intent=intent, account_id=str(request.get("account_id") or ""),
        instrument_code=str(request.get("instrument_code") or ""),
        volume=int(request.get("volume") or 0),
        limit_price=Decimal(str(request.get("limit_price") or 0)),
      )
      if str(device.user_id or "") != str(request.get("user_id") or ""):
        raise AgentUnavailableError("RISK_ADMISSION_ENTRY_DEVICE_CHANGED")
      return
    if execution_ref.owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION:
      raise AgentUnavailableError("T_ENTRY_AUTHORIZATION_SCOPE_INVALID")
    execution_mode = str(metadata.get("execution_mode") or "").upper()
    if not str(metadata.get("entry_plan_id") or ""):
      return
    common = {
      "account_id": str(request.get("account_id") or ""),
      "instrument_code": str(request.get("instrument_code") or ""),
      "limit_price": Decimal(str(request.get("limit_price") or "0")),
      "volume": int(request.get("volume") or 0),
      "strategy_run_id": execution_ref.owner_id,
      "intent_id": str(intent.id),
      "bucket": str(request.get("bucket") or ""),
      "policy_version": int(request.get("policy_version") or 0),
    }
    if execution_mode == "AUTO":
      device = await self._exact_auto_entry_device(
        **common,
        side="BUY",
        request_metadata=metadata,
      )
    elif execution_mode == "MANUAL_CONFIRM":
      device = await self._managed_manual_entry_device(
        **common,
        intent=intent,
      )
    else:
      raise AgentUnavailableError("RISK_ADMISSION_ENTRY_MODE_INVALID")
    if str(device.user_id or "") != str(request.get("user_id") or ""):
      raise AgentUnavailableError("RISK_ADMISSION_ENTRY_DEVICE_CHANGED")

  async def dispatch_ready_risk_increase_orders(
    self,
    *,
    account_id: str,
    processing_owner: str,
  ) -> dict[str, QueuedTradeCommand]:
    """Collect the whole READY set and enqueue it in deterministic rank order."""

    ready = list(
      (
        await self.db.scalars(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.account_id == str(account_id),
            TradeIntentRecord.environment == ExecutionEnvironment.LIVE.value,
            TradeIntentRecord.direction == "BUY",
            TradeIntentRecord.status == "EXECUTION_READY",
          )
          .order_by(
            TradeIntentRecord.created_at,
            TradeIntentRecord.owner_type,
            TradeIntentRecord.owner_id,
            TradeIntentRecord.id,
          )
        )
      ).all()
    )
    if not ready:
      return {}
    requests = [self._ready_order_request(intent) for intent in ready]
    # This is a non-locking preview.  The account execution-control row is
    # locked and revalidated only after the READY set has a committed batch
    # and processing fence.
    control = await self._preview_live_authorization(
      str(account_id),
      risk_reducing=False,
      require_controlled_window=any(
        bool(request.get("manual_live")) for request in requests
      ),
    )
    for intent, request in zip(ready, requests, strict=True):
      await self._revalidate_ready_order_request(
        intent=intent,
        request=request,
      )
    watermarks = {
      (
        await AccountCapacityService(self.db).read(
          control,
          instrument_code=str(request["instrument_code"]),
          lock_rows=False,
        )
      ).obligation_watermark
      for request in requests
    }
    if len(watermarks) != 1:
      raise AgentUnavailableError("RISK_ADMISSION_INPUT_CHANGED")
    obligation_watermark = next(iter(watermarks))
    sequencer = AccountRiskIncreaseAdmissionSequencer(self.db)
    batch = await sequencer.prepare_batch(
      account_id=str(account_id),
      account_snapshot_id=str(control.last_snapshot_id or ""),
      account_snapshot_hash=str(control.last_snapshot_hash or ""),
      obligation_watermark=obligation_watermark,
      intent_ids=[str(intent.id) for intent in ready],
      commit=True,
    )
    claim = await sequencer.claim_batch(
      admission_batch_id=str(batch.admission_batch_id),
      processing_owner=str(processing_owner or "risk-admission-dispatcher"),
      commit=True,
    )
    queued = await self.enqueue_risk_increase_admission_batch(
      claim=claim,
      order_requests=requests,
      account_snapshot_id=str(control.last_snapshot_id or ""),
      account_snapshot_hash=str(control.last_snapshot_hash or ""),
      obligation_watermark=obligation_watermark,
    )
    ranked_items = await self.db.scalars(
      select(AccountRiskIncreaseAdmissionItem)
      .where(
        AccountRiskIncreaseAdmissionItem.admission_batch_id
        == claim.admission_batch_id
      )
      .order_by(AccountRiskIncreaseAdmissionItem.admission_rank)
    )
    return {
      str(item.intent_id): result
      for item, result in zip(ranked_items.all(), queued, strict=True)
    }

  async def _queued_risk_increase_order(
    self,
    intent_id: str,
  ) -> QueuedTradeCommand | None:
    row = (
      await self.db.execute(
        select(PendingTradeOrder, TradeCommandOutbox)
        .join(
          TradeCommandOutbox,
          TradeCommandOutbox.client_order_id == PendingTradeOrder.client_order_id,
        )
        .where(PendingTradeOrder.intent_id == str(intent_id))
        .where(PendingTradeOrder.status.in_(("QUEUED", "PENDING", "DELIVERED", "SUBMITTED", "ACCEPTED", "PARTIAL_FILLED", "PARTIALLY_FILLED")))
        .order_by(PendingTradeOrder.t_order_attempt.desc())
        .limit(1)
      )
    ).one_or_none()
    if row is None:
      return None
    pending, outbox = row
    return QueuedTradeCommand(
      str(pending.client_order_id),
      str(outbox.message_id),
      str(outbox.delivery_status),
    )

  async def _await_queued_risk_increase_order(
    self,
    intent_id: str,
  ) -> QueuedTradeCommand | None:
    """Let a concurrent dispatcher winner publish the durable result.

    A held 10-second lease is not an error for the producer that staged the
    same intent.  Polling is bounded by that frozen lease: after it expires a
    background or caller dispatcher may take over on the next attempt.
    """

    deadline = monotonic() + ADMISSION_LEASE_SECONDS
    while True:
      queued = await self._queued_risk_increase_order(intent_id)
      if queued is not None:
        return queued
      if monotonic() >= deadline:
        return None
      await asyncio.sleep(RISK_ADMISSION_COLLECTION_WINDOW_SECONDS)

  async def enqueue_risk_increase_admission_batch(
    self,
    *,
    claim: AdmissionBatchClaim,
    order_requests: list[Mapping[str, Any]],
    account_snapshot_id: str,
    account_snapshot_hash: str,
    obligation_watermark: str,
  ) -> list[QueuedTradeCommand]:
    """Atomically enqueue one ranked LIVE BUY batch under its fence token."""

    # The committed claim may still be in the identity map; db.get() alone
    # does not necessarily autobegin the transaction needed by source locks.
    if not self.db.in_transaction():
      await self.db.begin()

    batch_identity = await self.db.get(
      AccountRiskIncreaseAdmissionBatch,
      claim.admission_batch_id,
    )
    if batch_identity is None:
      raise AgentUnavailableError("RISK_ADMISSION_BATCH_NOT_FOUND")
    from quantx_infrastructure.services.live_entry_source_locks import (
      lock_live_entry_sources,
    )

    try:
      await lock_live_entry_sources(
        self.db, account_id=str(batch_identity.account_id), order_requests=order_requests,
      )
    except ValueError as exc:
      raise AgentUnavailableError(str(exc)) from exc
    requires_manual_window = any(
      bool(request.get("manual_live")) for request in order_requests
    )
    control = (
      await self._require_manual_live_authorization(
        str(batch_identity.account_id),
        risk_reducing=False,
      )
      if requires_manual_window
      else await self._require_live_authorization(
        str(batch_identity.account_id),
        risk_reducing=False,
      )
    )
    batch = await self.db.get(
      AccountRiskIncreaseAdmissionBatch,
      claim.admission_batch_id,
      with_for_update=True,
      populate_existing=True,
    )
    if batch is None or str(batch.account_id) != str(control.account_id):
      raise AgentUnavailableError("RISK_ADMISSION_BATCH_ACCOUNT_CONFLICT")
    items = list(
      (
        await self.db.scalars(
          select(AccountRiskIncreaseAdmissionItem)
          .where(
            AccountRiskIncreaseAdmissionItem.admission_batch_id
            == claim.admission_batch_id
          )
          .order_by(AccountRiskIncreaseAdmissionItem.admission_rank)
          .with_for_update()
        )
      ).all()
    )
    requests_by_intent = {
      str(request.get("intent_id") or ""): dict(request)
      for request in order_requests
    }
    if (
      len(requests_by_intent) != len(order_requests)
      or set(requests_by_intent) != {str(item.intent_id) for item in items}
    ):
      raise AgentUnavailableError("RISK_ADMISSION_ORDER_MANIFEST_CONFLICT")
    if not items:
      raise AgentUnavailableError("RISK_ADMISSION_EMPTY_BATCH")
    instrument_codes = sorted(
      {
        str(request.get("instrument_code") or "").strip().upper()
        for request in requests_by_intent.values()
      }
    )
    if not instrument_codes or any(not code for code in instrument_codes):
      raise AgentUnavailableError("RISK_ADMISSION_INSTRUMENT_REQUIRED")
    capacity_watermarks = {
      (
        await AccountCapacityService(self.db).read(
          control,
          instrument_code=instrument_code,
        )
      ).obligation_watermark
      for instrument_code in instrument_codes
    }
    if (
      str(control.last_snapshot_id or "") != str(account_snapshot_id or "")
      or str(control.last_snapshot_hash or "").lower()
      != str(account_snapshot_hash or "").lower()
      or capacity_watermarks != {str(obligation_watermark or "").lower()}
    ):
      raise AgentUnavailableError("RISK_ADMISSION_INPUT_CHANGED")
    results: list[QueuedTradeCommand] = []
    try:
      last_renewed_at = monotonic()
      for item in items:
        current_monotonic = monotonic()
        if (
          current_monotonic - last_renewed_at
          >= ADMISSION_RENEW_INTERVAL_SECONDS
        ):
          await AccountRiskIncreaseAdmissionSequencer(self.db).renew_claim(
            admission_batch_id=claim.admission_batch_id,
            fence_token=claim.fence_token,
            commit=False,
          )
          last_renewed_at = current_monotonic
        request = requests_by_intent[str(item.intent_id)]
        request.update(
          {
            "environment": ExecutionEnvironment.LIVE,
            "side": "BUY",
            "commit_transaction": False,
            "_locked_live_control": control,
            "_admission_batch_id": claim.admission_batch_id,
            "_admission_rank": int(item.admission_rank),
            "_admission_fence_token": claim.fence_token,
          }
        )
        results.append(await self.enqueue_order(**request))
      await AccountRiskIncreaseAdmissionSequencer(self.db).commit_batch(
        admission_batch_id=claim.admission_batch_id,
        fence_token=claim.fence_token,
        account_snapshot_id=account_snapshot_id,
        account_snapshot_hash=account_snapshot_hash,
        obligation_watermark=obligation_watermark,
        commit=False,
      )
      for item in items:
        intent = await self.db.get(TradeIntentRecord, item.intent_id)
        if intent is None:
          raise AgentUnavailableError("RISK_ADMISSION_INTENT_NOT_FOUND")
        intent.status = "EXECUTION_PENDING"
      await self.db.commit()
      return results
    except Exception:
      await self.db.rollback()
      raise

  async def enqueue_order_for_account(
    self,
    *,
    account_id: str,
    instrument_code: str,
    side: str,
    order_type: str,
    limit_price: Decimal,
    volume: int,
    execution_ref: ExecutionOwnerRef,
    environment: ExecutionEnvironment,
    idempotency_key: str,
    trace_id: str = "",
    strategy_run_id: str = "",
    strategy_order_id: str = "",
    intent_id: str = "",
    batch_id: str = "",
    bucket: str = "manual",
    t_trade_role: str = "",
    _t_order_parent_client_id: str = "",
    risk_decision_id: str = "",
    substitution_plan: dict[str, Any] | None = None,
    policy_version: int = 0,
    request_metadata: dict[str, Any] | None = None,
    require_risk_reducing_live_authorization: bool = False,
    authorization_user_id: str = "",
  ) -> QueuedTradeCommand:
    idempotency_key, trace_id, raw_idempotency_key = require_stable_command_key(
      idempotency_key,
      trace_id,
    )
    metadata = self._sanitize_request_metadata(request_metadata)
    (
      canonical_ref,
      canonical_environment,
      owner_type,
      owner_id,
      _canonical_environment_value,
    ) = self._require_execution_identity(execution_ref, environment)
    normalized_execution_mode = self._wire_execution_mode(canonical_environment)
    strategy_run_id, strategy_order_id = self._normalize_strategy_identity(
      owner_type,
      owner_id,
      strategy_run_id,
      strategy_order_id,
    )
    normalized_side = str(side or "").strip().upper()
    normalized_instrument = str(instrument_code or "").strip().upper()
    normalized_order_type = self._wire_price_type(order_type)
    normalized_role = str(t_trade_role or "").strip().upper()
    if owner_type == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value and (
      normalized_role != "ENTRY" or normalized_side != "BUY"
      or canonical_environment is not ExecutionEnvironment.LIVE or not batch_id or not intent_id
    ):
      raise AgentUnavailableError("T_ENTRY_COMMAND_SCOPE_INVALID")
    if batch_id and not (
      owner_type == ExecutionOwnerType.STRATEGY_RUN.value
      or (owner_type == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value and normalized_role == "ENTRY")
      or (
        owner_type == ExecutionOwnerType.EXIT_PLAN.value
        and normalized_role == "EXIT"
      )
    ):
      raise AgentUnavailableError(
        "TRADE_COMMAND_OWNER_CONFLICT:TTrade 批次必须归属 ENTRY 来源或公共 EXIT_PLAN"
      )
    stage_before_account_lock = (
      normalized_execution_mode == "live" and normalized_side == "BUY"
    )
    locked_live_control = (
      await self._require_live_authorization(
        str(account_id),
        risk_reducing=normalized_side == "SELL" or normalized_role == "EXIT",
      )
      if normalized_execution_mode == "live" and not stage_before_account_lock
      else None
    )
    # Recover an accepted command before checking a grant or capacity again.
    # Its own reservation (or a subsequently consumed grant) is not a new buy.
    if intent_id and raw_idempotency_key:
      prior = (
        await self.db.execute(
          select(PendingTradeOrder, TradeCommandOutbox)
          .join(
            TradeCommandOutbox,
            TradeCommandOutbox.client_order_id == PendingTradeOrder.client_order_id,
          )
          .where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.intent_id == intent_id,
            PendingTradeOrder.t_order_attempt == 0,
          )
          .with_for_update()
        )
      ).one_or_none()
      if prior is not None and not _t_order_parent_client_id:
        pending, outbox = prior
        if outbox.idempotency_key != self.order_idempotency_digest(
          user_id=pending.user_id,
          account_id=account_id,
          idempotency_key=raw_idempotency_key,
          execution_ref=canonical_ref,
          environment=canonical_environment,
        ):
          raise AgentUnavailableError(
            "TRADE_INTENT_ALREADY_ROUTED:意图已有委托，请使用原幂等键重试"
          )
        if not await self._idempotent_order_chain_matches_request(
          outbox,
          user_id=str(pending.user_id or ""),
          account_id=account_id,
          owner_type=owner_type,
          owner_id=owner_id,
          environment=canonical_environment.value,
          instrument_code=normalized_instrument,
          side=normalized_side,
          order_type=normalized_order_type,
          limit_price=Decimal(str(limit_price)),
          volume=volume,
          strategy_run_id=strategy_run_id,
          strategy_order_id=strategy_order_id,
          intent_id=intent_id,
          batch_id=batch_id,
          bucket=bucket,
          t_trade_role=t_trade_role,
          risk_decision_id=risk_decision_id,
          trace_id=trace_id,
          substitution_plan=substitution_plan,
          request_metadata=metadata,
        ):
          raise AgentUnavailableError(
            "TRADE_COMMAND_RETRY_MISMATCH:重试必须保留已受理委托的数量和归属"
          )
        return QueuedTradeCommand(
          pending.client_order_id, outbox.message_id, outbox.delivery_status
        )
    live_sell_intent = (
      await self.db.get(
        TradeIntentRecord,
        str(intent_id or ""),
        with_for_update=True,
        populate_existing=True,
      )
      if normalized_execution_mode == "live"
      and str(side or "").upper() == "SELL"
      and str(intent_id or "")
      else None
    )
    live_sell_metadata = (
      dict(live_sell_intent.intent_metadata or {})
      if live_sell_intent is not None
      else {}
    )
    caller_claims_exit_plan = owner_type == ExecutionOwnerType.EXIT_PLAN.value
    persisted_exit_plan_sell = bool(
      live_sell_intent is not None
      and str(live_sell_intent.owner_type or "").upper() == "EXIT_PLAN"
    )
    if (
      normalized_execution_mode == "live"
      and caller_claims_exit_plan
      and not persisted_exit_plan_sell
    ):
      raise AgentUnavailableError("自动退出卖单缺少持久化 EXIT_PLAN 意图所有权")
    locked_exit_plan: AutoExitPlanRecord | None = None
    if persisted_exit_plan_sell:
      (
        locked_exit_plan,
        live_sell_intent,
        _position,
        live_sell_metadata,
      ) = await self._lock_and_validate_live_exit_plan_sell(
        locked_intent=live_sell_intent,
        plan_id=owner_id,
        intent_id=str(intent_id or ""),
        execution_ref=canonical_ref,
        environment=canonical_environment,
        account_id=str(account_id),
        instrument_code=normalized_instrument,
        volume=int(volume),
        request_metadata=metadata,
      )
    requires_exact_exit_authorization = bool(
      persisted_exit_plan_sell and live_sell_metadata.get("exact_auto_exit_authorized")
    )
    if requires_exact_exit_authorization:
      authorization_plan_id = owner_id
      authorization_fingerprint = str(
        metadata.get("auto_exit_authorization_fingerprint") or ""
      ).strip()
      if normalized_execution_mode != "live" or str(side or "").upper() != "SELL":
        raise AgentUnavailableError("精确自动退出门禁只能用于 LIVE 风险降低卖单")
      if not str(authorization_user_id or "").strip():
        raise AgentUnavailableError("自动退出授权缺少确认用户绑定")
      if (
        not authorization_plan_id
        or authorization_plan_id != str(locked_exit_plan.plan_id if locked_exit_plan else "")
        or not authorization_fingerprint
      ):
        raise AgentUnavailableError("自动退出命令缺少精确计划授权绑定")
      plan = locked_exit_plan
      if (
        plan is None
        or str(plan.account_id) != str(account_id)
        or str(plan.instrument_code) != str(instrument_code)
        or int(plan.config_version or 0) != int(policy_version or 0)
        or str(plan.auto_exit_authorization_user_id or "") != str(authorization_user_id)
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
        raise AgentUnavailableError(f"自动退出授权已失效：{validation.code}")
      intent = live_sell_intent
      intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
      if (
        intent is None
        or not bool(metadata.get("exact_auto_exit_authorized"))
        or not bool(intent_metadata.get("exact_auto_exit_authorized"))
        or str(intent_metadata.get("auto_exit_authorization_fingerprint") or "")
        != authorization_fingerprint
        or str(intent_metadata.get("auto_exit_authorization_user_id") or "")
        != str(authorization_user_id)
      ):
        raise AgentUnavailableError("自动退出意图与精确计划授权不匹配")
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
      or str(details.get("protocolVersion") or "") != PROTOCOL_VERSION
      ):
        raise AgentUnavailableError(
          f"自动退出要求唯一 READY、live、协议 {PROTOCOL_VERSION} 的 QMT Agent"
        )
    else:
      if persisted_exit_plan_sell:
        manual_plan_id = owner_id
        try:
          await validate_consumed_exit_plan_sell_challenge(
            self.db,
            plan_id=manual_plan_id,
            intent_id=str(intent_id or ""),
            account_id=str(account_id),
            approval_audit={
              "challenge_id": live_sell_metadata.get("exit_plan_approval_challenge_id"),
              "actor_id": live_sell_metadata.get("exit_plan_approval_user_id"),
              "device_session_id": live_sell_metadata.get(
                "exit_plan_approval_device_session_id"
              ),
              "channel": live_sell_metadata.get("exit_plan_approval_channel"),
            },
          )
        except ValueError as exc:
          raise AgentUnavailableError(f"退出计划人工确认挑战无效：{exc}") from exc
      persisted_intent = (
        await self.db.get(TradeIntentRecord, str(intent_id or ""))
        if normalized_execution_mode == "live"
        and str(side or "").upper() == "BUY"
        and str(intent_id or "")
        else None
      )
      persisted_metadata = (
        dict(persisted_intent.intent_metadata or {})
        if persisted_intent is not None
        else {}
      )
      is_managed_auto_entry = bool(
        persisted_intent is not None
        and str(persisted_intent.direction or "").upper() == "BUY"
        and str(persisted_intent.owner_type or "").upper()
        == ExecutionOwnerType.STRATEGY_RUN.value
        and str(persisted_intent.owner_id or "") == str(strategy_run_id or "")
        and str(persisted_metadata.get("execution_mode") or "").upper() == "AUTO"
        and str(persisted_metadata.get("t_trade_role") or "").upper() != "ENTRY"
      )
      is_managed_manual_entry = bool(
        persisted_intent is not None
        and str(persisted_intent.direction or "").upper() == "BUY"
        and str(persisted_intent.owner_type or "").upper()
        == ExecutionOwnerType.STRATEGY_RUN.value
        and str(persisted_intent.owner_id or "") == str(strategy_run_id or "")
        and str(persisted_metadata.get("execution_mode") or "").upper()
        == "MANUAL_CONFIRM"
        and str(persisted_metadata.get("t_trade_role") or "").upper() != "ENTRY"
      )
      if persisted_intent is not None and str(persisted_metadata.get("t_trade_role") or "").upper() == "ENTRY":
        device = await self._t_entry_device(
          intent=persisted_intent, account_id=account_id,
          instrument_code=instrument_code, limit_price=Decimal(str(limit_price)), volume=volume,
        )
      elif is_managed_auto_entry:
        device = await self._exact_auto_entry_device(
          account_id=account_id,
          instrument_code=instrument_code,
          side=side,
          limit_price=limit_price,
          volume=volume,
          strategy_run_id=strategy_run_id,
          intent_id=intent_id,
          bucket=bucket,
          policy_version=policy_version,
          request_metadata=metadata,
        )
      elif is_managed_manual_entry:
        device = await self._managed_manual_entry_device(
          account_id=account_id,
          instrument_code=instrument_code,
          limit_price=limit_price,
          volume=volume,
          strategy_run_id=strategy_run_id,
          intent_id=intent_id,
          bucket=bucket,
          policy_version=policy_version,
          intent=persisted_intent,
        )
      else:
        device = await self._device_for_account(account_id, normalized_execution_mode)
    return await self.enqueue_order(
      user_id=device.user_id,
      account_id=account_id,
      instrument_code=normalized_instrument,
      side=normalized_side,
      order_type=normalized_order_type,
      limit_price=limit_price,
      volume=volume,
      execution_ref=canonical_ref,
      environment=canonical_environment,
      trace_id=trace_id,
      idempotency_key=idempotency_key,
      strategy_run_id=strategy_run_id,
      strategy_order_id=strategy_order_id,
      intent_id=intent_id,
      batch_id=batch_id,
      bucket=bucket,
      t_trade_role=t_trade_role,
      risk_decision_id=risk_decision_id,
      substitution_plan=substitution_plan,
      policy_version=policy_version,
      request_metadata=metadata,
      _locked_live_control=locked_live_control,
      _t_order_parent_client_id=_t_order_parent_client_id,
    )

  async def enqueue_cancel(
    self,
    *,
    user_id: str,
    account_id: str,
    broker_order_id: str,
    execution_ref: ExecutionOwnerRef,
    environment: ExecutionEnvironment,
    idempotency_key: str = "",
    commit_transaction: bool = True,
  ) -> QueuedTradeCommand:
    canonical_ref, canonical_environment, owner_type, owner_id, environment_value = (
      self._require_execution_identity(execution_ref, environment)
    )
    normalized_broker_order_id = str(broker_order_id or "").strip()
    if not normalized_broker_order_id:
      raise AgentUnavailableError("TRADE_COMMAND_TARGET_MISSING:撤单目标不能为空")
    pending_candidates = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.broker_order_id == normalized_broker_order_id,
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    if len(pending_candidates) != 1:
      raise AgentUnavailableError(
        "TRADE_COMMAND_TARGET_UNPROVEN:撤单目标必须唯一命中持久化委托"
      )
    target_pending = pending_candidates[0]
    pending_owner_type = str(target_pending.owner_type or "").strip().upper()
    pending_owner_id = str(target_pending.owner_id or "").strip()
    pending_environment = str(target_pending.environment or "").strip().upper()
    pending_strategy_run_id = str(target_pending.strategy_run_id or "").strip()
    if pending_strategy_run_id and (
      pending_owner_type != ExecutionOwnerType.STRATEGY_RUN.value
      or pending_strategy_run_id != pending_owner_id
    ):
      raise AgentUnavailableError(
        "TRADE_COMMAND_TARGET_UNPROVEN:撤单目标 strategy_run 见证不一致"
      )
    if (
      str(target_pending.user_id or "") != str(user_id or "")
      or pending_owner_type != owner_type
      or pending_owner_id != owner_id
      or pending_environment != environment_value
    ):
      raise AgentUnavailableError("TRADE_COMMAND_OWNER_CONFLICT:撤单目标归属不一致")
    correlations = list(
      (
        await self.db.execute(
          select(OrderCorrelation)
          .where(
            OrderCorrelation.account_id == account_id,
            OrderCorrelation.broker_order_id == normalized_broker_order_id,
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    if len(correlations) != 1:
      raise AgentUnavailableError(
        "TRADE_COMMAND_TARGET_UNPROVEN:撤单目标 correlation 不唯一"
      )
    correlation = correlations[0]
    if (
      str(correlation.client_order_id or "")
      != str(target_pending.client_order_id or "")
      or str(correlation.account_id or "") != str(target_pending.account_id or "")
      or str(correlation.broker_order_id or "") != normalized_broker_order_id
      or str(correlation.owner_type or "").strip().upper() != pending_owner_type
      or str(correlation.owner_id or "").strip() != pending_owner_id
      or str(correlation.environment or "").strip().upper()
      != pending_environment
    ):
      raise AgentUnavailableError(
        "TRADE_COMMAND_TARGET_UNPROVEN:撤单目标 correlation 归属不一致"
      )
    normalized_execution_mode = self._wire_execution_mode(canonical_environment)
    cancel_business_identity = hashlib.sha256(
      (
        f"cancel:{user_id}:{account_id}:{environment_value}:{owner_type}:{owner_id}:"
        f"{idempotency_key.strip() or normalized_broker_order_id}"
      ).encode("utf-8")
    ).hexdigest()
    cancel_retry_prefix = f"{cancel_business_identity}:attempt:"
    cancel_attempt_selector = or_(
      TradeCommandOutbox.idempotency_key == cancel_business_identity,
      TradeCommandOutbox.idempotency_key.like(f"{cancel_retry_prefix}%"),
    )

    def cancel_attempt_number(attempt: TradeCommandOutbox) -> int:
      attempt_key = str(attempt.idempotency_key or "")
      if attempt_key == cancel_business_identity:
        return 1
      if attempt_key.startswith(cancel_retry_prefix):
        try:
          return max(1, int(attempt_key.removeprefix(cancel_retry_prefix)))
        except ValueError:
          return 1
      return 1

    attempts = list(
      (
        await self.db.execute(
          select(TradeCommandOutbox)
          .where(cancel_attempt_selector)
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    for attempt in attempts:
      if (
        str(getattr(attempt, "owner_type", "") or "") != owner_type
        or str(getattr(attempt, "owner_id", "") or "") != owner_id
        or str(getattr(attempt, "environment", "") or "").upper()
        != environment_value
      ):
        raise AgentUnavailableError("TRADE_COMMAND_OWNER_CONFLICT:取消重试归属不一致")
      if not self._cancel_attempt_matches_request(
        attempt,
        account_id=account_id,
        owner_type=owner_type,
        owner_id=owner_id,
        environment=environment_value,
        broker_order_id=normalized_broker_order_id,
      ):
        raise AgentUnavailableError(
          "IDEMPOTENCY_KEY_CONFLICT:同一取消幂等键对应不同目标"
        )
    now = utcnow()
    active_attempt = next(
      (
        attempt
        for attempt in attempts
        if str(attempt.delivery_status or "").upper()
        in {"QUEUED", "DELIVERED", "ACKNOWLEDGED"}
        and attempt.expires_at > now
      ),
      None,
    )
    if active_attempt is not None:
      return QueuedTradeCommand(
        active_attempt.client_order_id,
        active_attempt.message_id,
        active_attempt.delivery_status,
      )

    # An expired cancel that never left this process is safe to revive in
    # place.  Do not change its command identity: the Agent may still have a
    # durable journal for that exact message if the delivery evidence was
    # incomplete, which is why every other expired/old attempt is retired and
    # gets a distinct next-attempt identity below.
    safely_reusable = next(
      (
        attempt
        for attempt in attempts
        if str(attempt.delivery_status or "").upper() in {"QUEUED", "EXPIRED"}
        and attempt.expires_at <= now
        and attempt.delivered_at is None
        and attempt.acknowledged_at is None
        and int(attempt.attempts or 0) == 0
      ),
      None,
    )
    if safely_reusable is not None:
      expires_at = now + timedelta(minutes=2)
      safely_reusable.payload = CancelCommandPayload(
        command_kind="CANCEL_ORDER",
        client_order_id=str(safely_reusable.client_order_id or ""),
        account_id=str(safely_reusable.account_id or ""),
        execution_mode=normalized_execution_mode,
        broker_order_id=normalized_broker_order_id,
        expires_at=expires_at.replace(tzinfo=timezone.utc),
      ).model_dump(mode="json")
      safely_reusable.delivery_status = "QUEUED"
      safely_reusable.expires_at = expires_at
      safely_reusable.last_error = None
      if commit_transaction:
        await self.db.commit()
      else:
        await self.db.flush()
      return QueuedTradeCommand(
        safely_reusable.client_order_id,
        safely_reusable.message_id,
        "QUEUED",
      )

    # A delivered, acknowledged, reconciliation-required, or otherwise
    # uncertain attempt must never become deliverable again.  Its immutable
    # evidence remains in the outbox while a fresh attempt receives new Agent
    # identities.  A stale QUEUED row with any delivery evidence is uncertain
    # too, so retire it explicitly before adding the replacement.
    for attempt in attempts:
      if str(attempt.delivery_status or "").upper() == "QUEUED":
        attempt.delivery_status = "RECONCILE_REQUIRED"
        attempt.last_error = "cancel_retry_requires_new_command_identity"

    attempt_numbers = [cancel_attempt_number(attempt) for attempt in attempts]
    cancel_attempt = max(attempt_numbers, default=0) + 1
    business_idempotency_key = (
      cancel_business_identity
      if cancel_attempt == 1
      else f"{cancel_retry_prefix}{cancel_attempt}"
    )
    device = await self._device_for(
      user_id=user_id,
      account_id=account_id,
      execution_mode=normalized_execution_mode,
      allow_degraded_cancel=True,
    )
    client_order_id = f"cancel:{uuid.uuid4()}"
    message_id = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=2)
    payload = CancelCommandPayload(
      command_kind="CANCEL_ORDER",
      client_order_id=client_order_id,
      account_id=account_id,
      execution_mode=normalized_execution_mode,
      broker_order_id=normalized_broker_order_id,
      expires_at=expires_at.replace(tzinfo=timezone.utc),
    ).model_dump(mode="json")
    command = TradeCommandOutbox(
      message_id=message_id,
      client_order_id=client_order_id,
      idempotency_key=business_idempotency_key,
      device_id=device.id,
      account_id=account_id,
      owner_type=owner_type,
      owner_id=owner_id,
      environment=environment_value,
      payload=payload,
      delivery_status="QUEUED",
      expires_at=expires_at,
      attempts=0,
    )
    if commit_transaction:
      self.db.add(command)
      try:
        await self.db.commit()
      except IntegrityError:
        await self.db.rollback()
        existing_attempts = list(
          (
            await self.db.execute(
              select(TradeCommandOutbox)
              .where(cancel_attempt_selector)
              .with_for_update()
            )
          )
          .scalars()
          .all()
        )
        for attempt in existing_attempts:
          if not self._cancel_attempt_matches_request(
            attempt,
            account_id=account_id,
            owner_type=owner_type,
            owner_id=owner_id,
            environment=environment_value,
            broker_order_id=normalized_broker_order_id,
          ):
            raise AgentUnavailableError(
              "IDEMPOTENCY_KEY_CONFLICT:同一取消幂等键对应不同目标"
            )
        existing = next(
          (
            attempt
            for attempt in existing_attempts
            if str(attempt.delivery_status or "").upper()
            in {"QUEUED", "DELIVERED", "ACKNOWLEDGED"}
            and attempt.expires_at > utcnow()
          ),
          None,
        )
        if existing is None:
          raise
        return QueuedTradeCommand(
          existing.client_order_id,
          existing.message_id,
          existing.delivery_status,
        )
    else:
      # Isolate a concurrent uniqueness collision in a savepoint so callers
      # that own a larger authorization/report transaction remain usable.
      # The competing active QUEUED attempt is then returned below.
      try:
        async with self.db.begin_nested():
          self.db.add(command)
          await self.db.flush()
      except IntegrityError:
        existing_attempts = list(
          (
            await self.db.execute(
              select(TradeCommandOutbox)
              .where(cancel_attempt_selector)
              .with_for_update()
            )
          )
          .scalars()
          .all()
        )
        for attempt in existing_attempts:
          if not self._cancel_attempt_matches_request(
            attempt,
            account_id=account_id,
            owner_type=owner_type,
            owner_id=owner_id,
            environment=environment_value,
            broker_order_id=normalized_broker_order_id,
          ):
            raise AgentUnavailableError(
              "IDEMPOTENCY_KEY_CONFLICT:同一取消幂等键对应不同目标"
            )
        existing = next(
          (
            attempt
            for attempt in existing_attempts
            if str(attempt.delivery_status or "").upper()
            in {"QUEUED", "DELIVERED", "ACKNOWLEDGED"}
            and attempt.expires_at > utcnow()
          ),
          None,
        )
        if existing is None:
          raise
        return QueuedTradeCommand(
          existing.client_order_id,
          existing.message_id,
          existing.delivery_status,
        )
    return QueuedTradeCommand(client_order_id, message_id, "QUEUED")

  async def request_strategy_buy_cancellations(
    self,
    *,
    strategy_run_id: str,
    reason: str,
  ) -> list[StrategyOrderCancelRequest]:
    """Persist cancel intent without treating command delivery as terminal.

    A command that has never left the durable outbox can be cancelled locally.
    Once delivery may have happened, the pending order stays
    ``CANCEL_REQUESTED`` until an authoritative broker terminal report arrives.
    """

    orders = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.strategy_run_id == strategy_run_id,
            PendingTradeOrder.side.in_(("BUY", "BUY_TO_COVER")),
            PendingTradeOrder.status.in_(
              (
                "QUEUED",
                "PENDING",
                "DELIVERED",
                "SUBMITTED",
                "ACCEPTED",
                "PARTIAL_FILLED",
                "PARTIALLY_FILLED",
                "RECONCILE_REQUIRED",
                "CANCEL_REQUESTED",
              )
            ),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    if not orders:
      return []

    client_order_ids = [str(order.client_order_id) for order in orders]
    outboxes = list(
      (
        await self.db.execute(
          select(TradeCommandOutbox)
          .where(TradeCommandOutbox.client_order_id.in_(client_order_ids))
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    outbox_by_client = {str(row.client_order_id): row for row in outboxes}
    local_candidate_ids = [
      str(order.client_order_id)
      for order in orders
      if not str(order.broker_order_id or "").strip()
      and (
        (outbox := outbox_by_client.get(str(order.client_order_id))) is not None
        and str(outbox.delivery_status or "") == "QUEUED"
      )
    ]
    runtime_event_client_ids: set[str] = set()
    if local_candidate_ids:
      runtime_event_client_ids = {
        str(client_order_id)
        for client_order_id in (
          await self.db.execute(
            select(StrategyRuntimeEvent.client_order_id)
            .where(
              StrategyRuntimeEvent.client_order_id.in_(local_candidate_ids),
              StrategyRuntimeEvent.event_type.in_(("ORDER", "TRADE")),
            )
            .with_for_update()
          )
        )
        .scalars()
        .all()
      }
    results: list[StrategyOrderCancelRequest] = []
    for order in orders:
      client_order_id = str(order.client_order_id)
      broker_order_id = str(order.broker_order_id or "")
      local_terminal = False
      result_request_metadata = dict(order.request_metadata or {})
      if broker_order_id:
        order.status = "CANCEL_REQUESTED"
        order.status_reason = str(reason or "entry plan cancellation requested")[:256]
        await self.enqueue_cancel(
          user_id=str(order.user_id),
          account_id=str(order.account_id),
          broker_order_id=broker_order_id,
          idempotency_key=(f"entry-plan-cancel:{client_order_id}:{broker_order_id}"),
          execution_ref=ExecutionOwnerRef(
            str(order.owner_type or ""),
            str(order.owner_id or ""),
          ),
          environment=ExecutionEnvironment(str(order.environment or "").upper()),
          commit_transaction=False,
        )
      else:
        outbox = outbox_by_client.get(client_order_id)
        if (
          outbox is not None
          and str(outbox.delivery_status or "") == "QUEUED"
          and client_order_id not in runtime_event_client_ids
        ):
          intent_id = str(order.intent_id or "").strip()
          intent = None
          intent_metadata: dict[str, Any] = {}
          if intent_id:
            intent = await self.db.get(
              TradeIntentRecord,
              intent_id,
              with_for_update=True,
            )
            intent_metadata = (
              dict(intent.intent_metadata or {}) if intent is not None else {}
            )
            if intent is not None:
              result_request_metadata = intent_metadata
          has_managed_entry_marker = bool(
            str(order.strategy_run_id or "").strip()
            or (
              intent is not None
              and str(intent.owner_type or "").upper()
              == ExecutionOwnerType.STRATEGY_RUN.value
            )
          )
          is_bound_managed_entry = bool(
            intent is not None
            and str(intent.strategy_run_id or "") == strategy_run_id
            and str(intent.owner_type or "").upper()
            == ExecutionOwnerType.STRATEGY_RUN.value
            and str(intent.owner_id or "") == strategy_run_id
            and str(intent.direction or "").upper() == "BUY"
            and str(order.strategy_run_id or "") == strategy_run_id
          )
          try:
            executed_volume = int(intent.executed_volume or 0) if intent else 0
            last_source_sequence = int(getattr(order, "last_source_sequence", 0) or 0)
            executed_price = (
              Decimal(str(intent.executed_price or 0))
              if intent is not None
              else Decimal("0")
            )
          except (TypeError, ValueError, ArithmeticError):
            executed_volume = -1
            last_source_sequence = -1
            executed_price = Decimal("NaN")
          proven_zero_fill = bool(
            (intent is not None or not has_managed_entry_marker)
            and executed_volume == 0
            and (intent is None or intent.executed_time is None)
            and executed_price.is_finite()
            and executed_price <= 0
            and last_source_sequence == 0
            and getattr(order, "last_source_event_at", None) is None
            and (
              intent is None
              or str(intent.status or "").upper()
              not in {"FILLED", "PARTIAL_FILLED", "PARTIALLY_FILLED"}
            )
          )
          if not proven_zero_fill or (
            has_managed_entry_marker and not is_bound_managed_entry
          ):
            order.status = "RECONCILE_REQUIRED"
            order.status_reason = "local cancel could not prove zero broker execution"
          else:
            outbox.delivery_status = "CANCELLED"
            order.status = "CANCELLED"
            order.status_reason = "cancelled before Agent delivery"
            local_terminal = True
            if is_bound_managed_entry:
              reason_code = "ENTRY_PLAN_CANCELLED_BEFORE_AGENT_DELIVERY"
              intent.status = "RECONCILED_ZERO_FILL"
              intent.notes = reason_code
              intent_metadata["execution_terminal_reason"] = reason_code
              intent_metadata["execution_terminal_source"] = "LOCAL_OUTBOX_CANCEL"
              order.request_metadata = {
                **dict(order.request_metadata or {}),
                "execution_terminal_source": "LOCAL_OUTBOX_CANCEL",
                "execution_terminal_reason": reason_code,
                "command_lifecycle_message_id": outbox.message_id,
              }
              intent.intent_metadata = intent_metadata
              result_request_metadata = intent_metadata
        elif client_order_id in runtime_event_client_ids:
          order.status = "RECONCILE_REQUIRED"
          order.status_reason = "durable broker runtime event blocks local cancel"
        else:
          order.status = "CANCEL_REQUESTED"
          order.status_reason = str(
            reason or "waiting for broker order id before cancellation"
          )[:256]
      results.append(
        StrategyOrderCancelRequest(
          client_order_id=client_order_id,
          strategy_order_id=str(order.strategy_order_id or ""),
          intent_id=str(order.intent_id or ""),
          broker_order_id=broker_order_id,
          status=str(order.status or ""),
          request_metadata=result_request_metadata,
          local_terminal=local_terminal,
        )
      )
    await self.db.commit()
    return results

  async def enqueue_cancel_for_account(
    self,
    *,
    account_id: str,
    broker_order_id: str,
    execution_ref: ExecutionOwnerRef,
    environment: ExecutionEnvironment,
    idempotency_key: str = "",
  ) -> QueuedTradeCommand:
    _canonical_ref, canonical_environment, _owner_type, _owner_id, _ = (
      self._require_execution_identity(execution_ref, environment)
    )
    execution_mode = self._wire_execution_mode(canonical_environment)
    device = await self._device_for_account(account_id, execution_mode)
    return await self.enqueue_cancel(
      user_id=device.user_id,
      account_id=account_id,
      broker_order_id=broker_order_id,
      execution_ref=execution_ref,
      environment=canonical_environment,
      idempotency_key=idempotency_key,
    )
