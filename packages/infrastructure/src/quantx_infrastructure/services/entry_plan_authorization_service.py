"""Exact, revocable and amount-bounded authorization for automatic BUY entry."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Optional

from quantx_domain.trading.entry_plan import ManagedEntryPlanConfig
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.entry_plan_authorization import (
  EntryAutomationGate,
  EntryPlanAuthorizationConsumption,
  EntryPlanAuthorizationGrant,
)
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.repositories.entry_plan_authorization_repository import (
  EntryPlanAuthorizationRepository,
)

ENTRY_PLAN_AUTHORIZATION_ACTION = "ENTRY_PLAN_AUTHORIZATION"
ENTRY_PLAN_AUTHORIZATION_LIFETIME = timedelta(days=7)
ENTRY_PLAN_CHALLENGE_LIFETIME = timedelta(seconds=60)
REQUIRED_ENTRY_AUTHORIZATION_SCOPES = frozenset({"strategy:control", "trade:approve"})
_MONEY_QUANTUM = Decimal("0.0001")
_PRICE_QUANTUM = Decimal("0.000001")
MAX_UNBOUNDED_ENTRY_PLAN_VALID_UNTIL = datetime(9999, 12, 31, 23, 59, 59, 999999)


class EntryPlanAuthorizationError(ValueError):
  def __init__(self, code: str, message: str):
    super().__init__(code)
    self.code = code
    self.message = message


@dataclass(frozen=True)
class EntryPlanAuthorizationScope:
  """All risk-increasing facts covered by one authorization grant."""

  plan_id: str
  run_id: str
  config_version: int
  plan_fingerprint: str
  rule_fingerprint: str
  instrument_code: str
  bucket: str
  account_snapshot_version: str
  max_total_amount_cny: Decimal
  max_single_amount_cny: Decimal
  max_daily_amount_cny: Decimal
  max_position_pct: Decimal
  max_buy_price: Decimal
  max_slippage_bps: int
  max_price_deviation_bps: int
  plan_valid_until: datetime


@dataclass(frozen=True)
class EntryPlanAuthorizationPreview:
  challenge_id: str
  confirmation_token: str
  authorization_fingerprint: str
  challenge_expires_at: datetime
  authorization_expires_at: datetime


@dataclass(frozen=True)
class EntryPlanAuthorizationBalance:
  grant_id: str
  consumed_total_amount_cny: Decimal
  consumed_today_amount_cny: Decimal
  remaining_total_amount_cny: Decimal
  remaining_daily_amount_cny: Decimal
  max_single_amount_cny: Decimal


@dataclass(frozen=True)
class EntryPlanAuthorizationValidation:
  valid: bool
  code: str
  message: str
  balance: Optional[EntryPlanAuthorizationBalance] = None


@dataclass(frozen=True)
class EntryAutomationGateState:
  paused: bool
  reason: Optional[str]
  actor_user_id: Optional[str]
  changed_at: Optional[datetime]


def account_fingerprint(account_id: str) -> str:
  normalized = str(account_id or "").strip()
  if not normalized:
    raise EntryPlanAuthorizationError(
      "ACCOUNT_REQUIRED", "自动买入授权必须绑定唯一实盘账户"
    )
  return hashlib.sha256(
    f"quantx:entry-account:v1:{normalized}".encode("utf-8")
  ).hexdigest()


def _decimal(
  value: Any, *, code: str, quantum: Decimal, upper: Decimal | None = None
) -> Decimal:
  try:
    normalized = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
  except (InvalidOperation, TypeError, ValueError) as exc:
    raise EntryPlanAuthorizationError(code, "授权风险参数不是有效数值") from exc
  if not normalized.is_finite() or normalized <= 0:
    raise EntryPlanAuthorizationError(code, "授权风险参数必须大于零")
  if upper is not None and normalized > upper:
    raise EntryPlanAuthorizationError(code, "授权风险参数超过允许范围")
  return normalized


def normalize_scope(scope: EntryPlanAuthorizationScope) -> EntryPlanAuthorizationScope:
  plan_id = str(scope.plan_id or "").strip()
  run_id = str(scope.run_id or "").strip()
  if not plan_id or not run_id:
    raise EntryPlanAuthorizationError(
      "PLAN_RUN_REQUIRED", "自动买入授权必须同时绑定计划与当前 StrategyRun"
    )
  if int(scope.config_version or 0) <= 0:
    raise EntryPlanAuthorizationError("INVALID_CONFIG_VERSION", "配置版本必须大于零")
  for value, code in (
    (scope.plan_fingerprint, "INVALID_PLAN_FINGERPRINT"),
    (scope.rule_fingerprint, "INVALID_RULE_FINGERPRINT"),
  ):
    token = str(value or "").lower()
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
      raise EntryPlanAuthorizationError(code, "授权指纹无效")
  instrument_code = str(scope.instrument_code or "").strip().upper()
  if not instrument_code or len(instrument_code) > 20:
    raise EntryPlanAuthorizationError("INVALID_SYMBOL", "交易标的无效")
  bucket = str(scope.bucket or "").strip().lower()
  if bucket not in {"core", "swing"}:
    raise EntryPlanAuthorizationError("INVALID_BUCKET", "自动买入只允许核心仓或活跃仓")
  slippage = int(scope.max_slippage_bps)
  if slippage < 0 or slippage > 10_000:
    raise EntryPlanAuthorizationError("INVALID_SLIPPAGE_LIMIT", "最大滑点边界无效")
  price_deviation = int(scope.max_price_deviation_bps)
  if price_deviation < 0 or price_deviation > 10_000:
    raise EntryPlanAuthorizationError(
      "INVALID_PRICE_DEVIATION_LIMIT", "最大价格偏离边界无效"
    )
  account_snapshot_version = str(scope.account_snapshot_version or "").strip()
  if not account_snapshot_version or len(account_snapshot_version) > 64:
    raise EntryPlanAuthorizationError(
      "ACCOUNT_SNAPSHOT_REQUIRED", "自动买入授权要求明确的账户快照版本"
    )
  if not isinstance(scope.plan_valid_until, datetime):
    raise EntryPlanAuthorizationError("INVALID_PLAN_VALIDITY", "计划有效期无效")
  plan_valid_until = time_utils.to_shanghai(scope.plan_valid_until)
  return EntryPlanAuthorizationScope(
    plan_id=plan_id,
    run_id=run_id,
    config_version=int(scope.config_version),
    plan_fingerprint=str(scope.plan_fingerprint).lower(),
    rule_fingerprint=str(scope.rule_fingerprint).lower(),
    instrument_code=instrument_code,
    bucket=bucket,
    account_snapshot_version=account_snapshot_version,
    max_total_amount_cny=_decimal(
      scope.max_total_amount_cny,
      code="INVALID_TOTAL_LIMIT",
      quantum=_MONEY_QUANTUM,
    ),
    max_single_amount_cny=_decimal(
      scope.max_single_amount_cny,
      code="INVALID_SINGLE_LIMIT",
      quantum=_MONEY_QUANTUM,
    ),
    max_daily_amount_cny=_decimal(
      scope.max_daily_amount_cny,
      code="INVALID_DAILY_LIMIT",
      quantum=_MONEY_QUANTUM,
    ),
    max_position_pct=_decimal(
      scope.max_position_pct,
      code="INVALID_POSITION_LIMIT",
      quantum=Decimal("0.00000001"),
      upper=Decimal("1"),
    ),
    max_buy_price=_decimal(
      scope.max_buy_price,
      code="INVALID_MAX_BUY_PRICE",
      quantum=_PRICE_QUANTUM,
    ),
    max_slippage_bps=slippage,
    max_price_deviation_bps=price_deviation,
    plan_valid_until=plan_valid_until,
  )


def _canonical(value: dict[str, Any]) -> str:
  encoded = json.dumps(
    value,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _scope_payload(scope: EntryPlanAuthorizationScope) -> dict[str, Any]:
  value = asdict(normalize_scope(scope))
  return {
    key: (
      str(item)
      if isinstance(item, Decimal)
      else item.isoformat(timespec="microseconds")
      if isinstance(item, datetime)
      else item
    )
    for key, item in value.items()
  }


def _token_digest(token: str) -> str:
  return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def scope_from_managed_entry_config(
  *,
  plan_id: str,
  run_id: Optional[str] = None,
  config: Mapping[str, Any] | ManagedEntryPlanConfig,
) -> EntryPlanAuthorizationScope:
  """Build the one canonical authorization scope used by every execution gate.

  A plan without ``expire_at_ms`` has no product-level expiry.  It maps to the
  fixed maximum naive timestamp below; the independently bounded seven-day
  grant lifetime still applies.  This rule keeps API, Engine and command-gate
  fingerprints identical without consulting a clock.
  """

  if isinstance(config, ManagedEntryPlanConfig):
    normalized_config = config
  else:
    raw = dict(config or {})
    selected = raw.get("managed_entry_plan")
    if isinstance(selected, Mapping):
      raw = dict(selected)
    try:
      normalized_config = ManagedEntryPlanConfig.from_dict(raw)
    except (TypeError, ValueError) as exc:
      raise EntryPlanAuthorizationError(
        "INVALID_MANAGED_ENTRY_CONFIG",
        "建仓计划配置无法构造精确授权范围",
      ) from exc

  canonical_config = normalized_config.to_dict()
  expire_at_ms = normalized_config.completion_policy.expire_at_ms
  if expire_at_ms is None:
    valid_until = MAX_UNBOUNDED_ENTRY_PLAN_VALID_UNTIL
  else:
    try:
      valid_until = time_utils.to_shanghai(
        datetime.fromtimestamp(expire_at_ms / 1000, tz=timezone.utc)
      )
    except (OSError, OverflowError, ValueError) as exc:
      raise EntryPlanAuthorizationError(
        "INVALID_PLAN_VALIDITY", "计划有效期超出可授权范围"
      ) from exc
  target = normalized_config.target_policy
  pacing = normalized_config.pacing_policy
  execution = normalized_config.execution_policy
  completion = normalized_config.completion_policy
  return normalize_scope(
    EntryPlanAuthorizationScope(
      plan_id=str(plan_id),
      run_id=str(run_id or plan_id),
      config_version=normalized_config.config_version,
      plan_fingerprint=_canonical(canonical_config),
      rule_fingerprint=_canonical(
        {"trigger_rules": canonical_config.get("trigger_rules", [])}
      ),
      instrument_code=normalized_config.instrument_code,
      bucket=normalized_config.bucket,
      account_snapshot_version=(target.baseline_snapshot.account_snapshot_version),
      max_total_amount_cny=Decimal(str(target.max_total_amount_cny)),
      max_single_amount_cny=Decimal(str(pacing.max_single_intent_amount_cny)),
      max_daily_amount_cny=Decimal(str(pacing.max_daily_filled_amount_cny)),
      max_position_pct=Decimal(str(target.max_position_pct)),
      max_buy_price=Decimal(str(completion.max_buy_price)),
      max_slippage_bps=int(execution.max_slippage_bps),
      max_price_deviation_bps=int(execution.max_price_deviation_bps),
      plan_valid_until=valid_until,
    )
  )


class EntryPlanAuthorizationService:
  def __init__(self, db: AsyncSession):
    self.db = db
    self.repo = EntryPlanAuthorizationRepository(db)

  async def _require_actor(
    self,
    *,
    user_id: str,
    device_session_id: str,
    account_id: str,
    now: datetime,
    lock: bool,
  ) -> None:
    stmt = (
      select(AuthDeviceSession, AuthUser)
      .join(AuthUser, AuthUser.id == AuthDeviceSession.user_id)
      .where(
        AuthDeviceSession.id == device_session_id,
        AuthDeviceSession.user_id == user_id,
      )
    )
    if lock:
      stmt = stmt.with_for_update()
    row = (await self.db.execute(stmt)).one_or_none()
    if row is None:
      raise EntryPlanAuthorizationError(
        "DEVICE_SESSION_REQUIRED", "自动买入授权要求当前原生设备会话"
      )
    session, user = row
    if (
      session.revoked_at is not None
      or time_utils.to_shanghai(session.expires_at) <= now
      or not bool(user.is_active)
    ):
      raise EntryPlanAuthorizationError(
        "DEVICE_SESSION_REVOKED", "授权设备会话或用户已失效"
      )
    session_scopes = {str(value) for value in list(session.granted_permissions or [])}
    user_scopes = {str(value) for value in list(user.permissions or [])}
    if not REQUIRED_ENTRY_AUTHORIZATION_SCOPES <= (session_scopes & user_scopes):
      raise EntryPlanAuthorizationError(
        "ENTRY_AUTHORIZATION_FORBIDDEN", "当前主体缺少自动买入授权权限"
      )
    access_stmt = select(AuthUserAccountAccess).where(
      AuthUserAccountAccess.user_id == user_id
    )
    if lock:
      access_stmt = access_stmt.with_for_update()
    accesses = list((await self.db.execute(access_stmt)).scalars().all())
    if len(accesses) != 1 or str(accesses[0].account_id) != str(account_id):
      raise EntryPlanAuthorizationError(
        "UNIQUE_PRIMARY_ACCOUNT_REQUIRED",
        "自动买入授权要求唯一且精确匹配的主账户",
      )

  async def preview(
    self,
    *,
    scope: EntryPlanAuthorizationScope,
    user_id: str,
    device_session_id: str,
    account_id: str,
    idempotency_key: str,
    now: Optional[datetime] = None,
  ) -> EntryPlanAuthorizationPreview:
    checked_at = now or time_utils.now()
    normalized = normalize_scope(scope)
    if normalized.plan_valid_until <= checked_at:
      raise EntryPlanAuthorizationError("ENTRY_PLAN_EXPIRED", "建仓计划有效期已结束")
    if not str(idempotency_key or "").strip() or len(idempotency_key) > 128:
      raise EntryPlanAuthorizationError("INVALID_IDEMPOTENCY_KEY", "授权预览幂等键无效")
    await self._require_actor(
      user_id=user_id,
      device_session_id=device_session_id,
      account_id=account_id,
      now=checked_at,
      lock=False,
    )
    fingerprint = account_fingerprint(account_id)
    gate = await self.repo.find_gate(fingerprint)
    if gate is not None and bool(gate.paused):
      raise EntryPlanAuthorizationError(
        "ENTRY_AUTOMATION_PAUSED", "全局自动买入当前已暂停"
      )
    challenge_expires_at = checked_at + ENTRY_PLAN_CHALLENGE_LIFETIME
    authorization_expires_at = min(
      checked_at + ENTRY_PLAN_AUTHORIZATION_LIFETIME,
      normalized.plan_valid_until,
    )
    payload = {
      "action": ENTRY_PLAN_AUTHORIZATION_ACTION,
      "scope": _scope_payload(normalized),
      "user_id": user_id,
      "device_session_id": device_session_id,
      "account_fingerprint": fingerprint,
      "authorization_expires_at": authorization_expires_at.isoformat(
        timespec="microseconds"
      ),
    }
    authorization_fingerprint = _canonical(payload)
    payload["authorization_fingerprint"] = authorization_fingerprint
    token = secrets.token_urlsafe(48)
    challenge = TradeConfirmationChallenge(
      id=str(uuid.uuid4()),
      action=ENTRY_PLAN_AUTHORIZATION_ACTION,
      user_id=user_id,
      device_session_id=device_session_id,
      account_id=str(account_id),
      idempotency_key=str(idempotency_key).strip(),
      payload=payload,
      payload_fingerprint=_canonical(payload),
      token_digest=_token_digest(token),
      expires_at=challenge_expires_at,
      consumed_at=None,
    )
    self.db.add(challenge)
    try:
      await self.db.commit()
    except IntegrityError as exc:
      await self.db.rollback()
      raise EntryPlanAuthorizationError(
        "IDEMPOTENCY_KEY_ALREADY_USED",
        "该幂等键已用于自动买入授权预览",
      ) from exc
    return EntryPlanAuthorizationPreview(
      challenge_id=challenge.id,
      confirmation_token=token,
      authorization_fingerprint=authorization_fingerprint,
      challenge_expires_at=challenge_expires_at,
      authorization_expires_at=authorization_expires_at,
    )

  async def confirm(
    self,
    *,
    scope: EntryPlanAuthorizationScope,
    user_id: str,
    device_session_id: str,
    account_id: str,
    challenge_id: str,
    confirmation_token: str,
    now: Optional[datetime] = None,
  ) -> EntryPlanAuthorizationGrant:
    checked_at = now or time_utils.now()
    normalized = normalize_scope(scope)
    challenge = (
      await self.db.execute(
        select(TradeConfirmationChallenge)
        .where(TradeConfirmationChallenge.id == str(challenge_id))
        .with_for_update()
      )
    ).scalar_one_or_none()
    if challenge is None or challenge.action != ENTRY_PLAN_AUTHORIZATION_ACTION:
      raise EntryPlanAuthorizationError(
        "CONFIRMATION_NOT_FOUND", "自动买入授权挑战不存在"
      )
    if not hmac.compare_digest(
      str(challenge.token_digest), _token_digest(confirmation_token)
    ):
      raise EntryPlanAuthorizationError(
        "INVALID_CONFIRMATION_TOKEN", "自动买入授权确认凭据无效"
      )
    await self._require_actor(
      user_id=user_id,
      device_session_id=device_session_id,
      account_id=account_id,
      now=checked_at,
      lock=True,
    )
    payload = dict(challenge.payload or {})
    expected = {
      "action": ENTRY_PLAN_AUTHORIZATION_ACTION,
      "scope": _scope_payload(normalized),
      "user_id": user_id,
      "device_session_id": device_session_id,
      "account_fingerprint": account_fingerprint(account_id),
      "authorization_expires_at": payload.get("authorization_expires_at"),
    }
    expected_fingerprint = _canonical(expected)
    if (
      expected_fingerprint != payload.get("authorization_fingerprint")
      or _canonical(payload) != challenge.payload_fingerprint
      or str(challenge.user_id) != user_id
      or str(challenge.device_session_id) != device_session_id
      or str(challenge.account_id) != str(account_id)
    ):
      raise EntryPlanAuthorizationError(
        "AUTHORIZATION_SCOPE_CHANGED",
        "主体、设备、账户、计划、规则或风险边界已变化",
      )
    if challenge.consumed_at is not None:
      grant_id = str(dict(challenge.result_reference or {}).get("grant_id") or "")
      existing = await self.repo.find_grant(grant_id)
      if existing is None:
        raise EntryPlanAuthorizationError(
          "CONFIRMATION_RESULT_MISSING", "已确认授权结果不可用"
        )
      return existing
    if time_utils.to_shanghai(challenge.expires_at) <= checked_at:
      raise EntryPlanAuthorizationError(
        "CONFIRMATION_EXPIRED", "自动买入授权挑战已过期"
      )
    gate = await self.repo.find_gate(account_fingerprint(account_id), for_update=True)
    if gate is not None and bool(gate.paused):
      raise EntryPlanAuthorizationError(
        "ENTRY_AUTOMATION_PAUSED", "全局自动买入当前已暂停"
      )
    expires_at = datetime.fromisoformat(str(payload["authorization_expires_at"]))
    if expires_at <= checked_at:
      raise EntryPlanAuthorizationError(
        "AUTHORIZATION_EXPIRED", "自动买入授权有效期已结束"
      )
    prior = await self.repo.find_current_for_plan(normalized.plan_id, for_update=True)
    if prior is not None:
      prior.revoked_at = checked_at
      prior.revoked_reason = "SUPERSEDED"
    consumed_plan_amount = await self.repo.consumed_for_plan(normalized.plan_id)
    consumed_plan_volume = await self.repo.consumed_volume_for_plan(normalized.plan_id)
    if consumed_plan_amount >= normalized.max_total_amount_cny:
      raise EntryPlanAuthorizationError(
        "AUTHORIZATION_CAPACITY_EXHAUSTED", "计划真实成交已用尽授权总额度"
      )
    grant = EntryPlanAuthorizationGrant(
      grant_id=str(uuid.uuid4()),
      plan_id=normalized.plan_id,
      run_id=normalized.run_id,
      config_version=normalized.config_version,
      plan_fingerprint=normalized.plan_fingerprint,
      rule_fingerprint=normalized.rule_fingerprint,
      authorization_fingerprint=expected_fingerprint,
      subject_user_id=user_id,
      device_session_id=device_session_id,
      account_fingerprint=account_fingerprint(account_id),
      account_snapshot_version=normalized.account_snapshot_version,
      challenge_id=str(challenge.id),
      instrument_code=normalized.instrument_code,
      bucket=normalized.bucket,
      max_total_amount_cny=normalized.max_total_amount_cny,
      max_single_amount_cny=normalized.max_single_amount_cny,
      max_daily_amount_cny=normalized.max_daily_amount_cny,
      max_position_pct=normalized.max_position_pct,
      max_buy_price=normalized.max_buy_price,
      max_slippage_bps=normalized.max_slippage_bps,
      max_price_deviation_bps=normalized.max_price_deviation_bps,
      plan_valid_until=normalized.plan_valid_until,
      authorized_at=checked_at,
      expires_at=expires_at,
      consumed_total_amount_cny=consumed_plan_amount,
      consumed_total_volume=consumed_plan_volume,
    )
    self.repo.add_grant(grant)
    challenge.consumed_at = checked_at
    challenge.result_reference = {"grant_id": grant.grant_id}
    await self.repo.add_event_once(
      event_id=str(uuid.uuid4()),
      business_key=f"entry-authorized:{normalized.plan_id}:{challenge.id}",
      plan_id=normalized.plan_id,
      grant_id=grant.grant_id,
      event_type="ENTRY_AUTO_AUTHORIZED",
      reason_code=None,
      subject_fingerprint=expected_fingerprint,
      created_at=checked_at,
    )
    try:
      await self.db.commit()
    except IntegrityError as exc:
      await self.db.rollback()
      raise EntryPlanAuthorizationError(
        "CONCURRENT_AUTHORIZATION_CONFLICT",
        "同一计划已有并发创建的自动买入授权，请刷新后重试",
      ) from exc
    await self.db.refresh(grant)
    return grant

  async def _balance(
    self, grant: EntryPlanAuthorizationGrant, checked_at: datetime
  ) -> EntryPlanAuthorizationBalance:
    consumed_total = await self.repo.consumed_for_plan(grant.plan_id)
    consumed_today = await self.repo.consumed_on_date(
      grant.plan_id, time_utils.to_shanghai(checked_at).date()
    )
    return EntryPlanAuthorizationBalance(
      grant_id=grant.grant_id,
      consumed_total_amount_cny=consumed_total,
      consumed_today_amount_cny=consumed_today,
      remaining_total_amount_cny=max(
        Decimal("0"), Decimal(str(grant.max_total_amount_cny)) - consumed_total
      ),
      remaining_daily_amount_cny=max(
        Decimal("0"), Decimal(str(grant.max_daily_amount_cny)) - consumed_today
      ),
      max_single_amount_cny=Decimal(str(grant.max_single_amount_cny)),
    )

  async def _invalidate(
    self,
    grant: EntryPlanAuthorizationGrant,
    *,
    reason: str,
    now: datetime,
  ) -> None:
    if grant.invalidated_at is None:
      grant.invalidated_at = now
      grant.invalidation_reason = reason
      await self.repo.add_event_once(
        event_id=str(uuid.uuid4()),
        business_key=f"entry-auth-invalidated:{grant.grant_id}:{reason}",
        plan_id=grant.plan_id,
        grant_id=grant.grant_id,
        event_type="ENTRY_AUTO_AUTHORIZATION_INVALIDATED",
        reason_code=reason,
        subject_fingerprint=grant.authorization_fingerprint,
        created_at=now,
      )

  async def validate_or_invalidate(
    self,
    *,
    plan_id: str,
    current_scope: EntryPlanAuthorizationScope,
    account_id: str,
    proposed_amount_cny: Decimal | None = None,
    proposed_buy_price: Decimal | None = None,
    proposed_slippage_bps: int | None = None,
    proposed_price_deviation_bps: int | None = None,
    resulting_position_pct: Decimal | None = None,
    now: Optional[datetime] = None,
    commit: bool = True,
  ) -> EntryPlanAuthorizationValidation:
    checked_at = now or time_utils.now()
    grant = await self.repo.find_current_for_plan(plan_id, for_update=True)
    if grant is None:
      return EntryPlanAuthorizationValidation(
        False, "ENTRY_AUTO_NOT_AUTHORIZED", "建仓计划没有有效精确授权"
      )
    reason: Optional[str] = None
    try:
      normalized = normalize_scope(current_scope)
    except EntryPlanAuthorizationError:
      await self._invalidate(
        grant, reason="ENTRY_AUTHORIZATION_SCOPE_CHANGED", now=checked_at
      )
      if commit:
        await self.db.commit()
      else:
        await self.db.flush()
      return EntryPlanAuthorizationValidation(
        False,
        "ENTRY_AUTHORIZATION_SCOPE_CHANGED",
        "计划授权范围无效或已变化",
      )
    gate = await self.repo.find_gate(account_fingerprint(account_id), for_update=True)
    if gate is not None and bool(gate.paused):
      return EntryPlanAuthorizationValidation(
        False, "ENTRY_AUTOMATION_PAUSED", "全局自动买入当前已暂停"
      )
    if time_utils.to_shanghai(grant.expires_at) <= checked_at:
      reason = "ENTRY_AUTHORIZATION_EXPIRED"
    elif grant.account_fingerprint != account_fingerprint(account_id):
      reason = "ENTRY_ACCOUNT_SCOPE_CHANGED"
    elif self._grant_scope(grant) != normalized:
      reason = "ENTRY_AUTHORIZATION_SCOPE_CHANGED"
    else:
      try:
        await self._require_actor(
          user_id=grant.subject_user_id,
          device_session_id=grant.device_session_id,
          account_id=account_id,
          now=checked_at,
          lock=True,
        )
      except EntryPlanAuthorizationError:
        reason = "ENTRY_AUTHORIZATION_SUBJECT_REVOKED"
    if reason is not None:
      await self._invalidate(grant, reason=reason, now=checked_at)
      if commit:
        await self.db.commit()
      else:
        await self.db.flush()
      return EntryPlanAuthorizationValidation(False, reason, "自动买入授权已失效")

    balance = await self._balance(grant, checked_at)
    try:
      amount = Decimal(str(proposed_amount_cny or 0))
    except (InvalidOperation, TypeError, ValueError):
      amount = Decimal("NaN")
    if not amount.is_finite() or amount < 0:
      return EntryPlanAuthorizationValidation(
        False, "INVALID_BUY_AMOUNT", "买入金额无效", balance
      )
    if amount > 0 and amount > balance.max_single_amount_cny:
      return EntryPlanAuthorizationValidation(
        False, "SINGLE_AMOUNT_LIMIT", "超过授权单笔额度", balance
      )
    if amount > balance.remaining_total_amount_cny:
      return EntryPlanAuthorizationValidation(
        False, "TOTAL_AMOUNT_LIMIT", "超过授权剩余总额度", balance
      )
    if amount > balance.remaining_daily_amount_cny:
      return EntryPlanAuthorizationValidation(
        False, "DAILY_AMOUNT_LIMIT", "超过授权当日剩余额度", balance
      )
    if proposed_buy_price is not None:
      try:
        buy_price = Decimal(str(proposed_buy_price))
      except (InvalidOperation, TypeError, ValueError):
        buy_price = Decimal("NaN")
      if not buy_price.is_finite() or buy_price <= 0:
        return EntryPlanAuthorizationValidation(
          False, "INVALID_BUY_PRICE", "买入价格无效", balance
        )
      if buy_price > Decimal(str(grant.max_buy_price)):
        return EntryPlanAuthorizationValidation(
          False, "MAX_BUY_PRICE", "买入价格超过授权上限", balance
        )
    if proposed_slippage_bps is not None:
      try:
        slippage_bps = int(proposed_slippage_bps)
      except (TypeError, ValueError):
        slippage_bps = -1
      if slippage_bps < 0:
        return EntryPlanAuthorizationValidation(
          False, "INVALID_SLIPPAGE", "买入滑点无效", balance
        )
      if slippage_bps > int(grant.max_slippage_bps):
        return EntryPlanAuthorizationValidation(
          False, "MAX_SLIPPAGE", "买入滑点超过授权上限", balance
        )
    if proposed_price_deviation_bps is not None:
      try:
        price_deviation_bps = int(proposed_price_deviation_bps)
      except (TypeError, ValueError):
        price_deviation_bps = -1
      if price_deviation_bps < 0:
        return EntryPlanAuthorizationValidation(
          False, "INVALID_PRICE_DEVIATION", "买入价格偏离无效", balance
        )
      if price_deviation_bps > int(grant.max_price_deviation_bps):
        return EntryPlanAuthorizationValidation(
          False, "MAX_PRICE_DEVIATION", "买入价格偏离超过授权上限", balance
        )
    if resulting_position_pct is not None:
      try:
        position_pct = Decimal(str(resulting_position_pct))
      except (InvalidOperation, TypeError, ValueError):
        position_pct = Decimal("NaN")
      if not position_pct.is_finite() or position_pct < 0:
        return EntryPlanAuthorizationValidation(
          False, "INVALID_POSITION", "成交后仓位无效", balance
        )
      if position_pct > Decimal(str(grant.max_position_pct)):
        return EntryPlanAuthorizationValidation(
          False, "MAX_POSITION", "成交后仓位超过授权上限", balance
        )
    if (
      balance.remaining_total_amount_cny <= 0 or balance.remaining_daily_amount_cny <= 0
    ):
      return EntryPlanAuthorizationValidation(
        False, "AUTHORIZATION_CAPACITY_EXHAUSTED", "自动买入授权额度已用尽", balance
      )
    return EntryPlanAuthorizationValidation(
      True, "AUTHORIZED", "精确自动买入授权有效", balance
    )

  @staticmethod
  def _grant_scope(grant: EntryPlanAuthorizationGrant) -> EntryPlanAuthorizationScope:
    return normalize_scope(
      EntryPlanAuthorizationScope(
        plan_id=grant.plan_id,
        run_id=grant.run_id,
        config_version=grant.config_version,
        plan_fingerprint=grant.plan_fingerprint,
        rule_fingerprint=grant.rule_fingerprint,
        instrument_code=grant.instrument_code,
        bucket=grant.bucket,
        account_snapshot_version=grant.account_snapshot_version,
        max_total_amount_cny=Decimal(str(grant.max_total_amount_cny)),
        max_single_amount_cny=Decimal(str(grant.max_single_amount_cny)),
        max_daily_amount_cny=Decimal(str(grant.max_daily_amount_cny)),
        max_position_pct=Decimal(str(grant.max_position_pct)),
        max_buy_price=Decimal(str(grant.max_buy_price)),
        max_slippage_bps=grant.max_slippage_bps,
        max_price_deviation_bps=grant.max_price_deviation_bps,
        plan_valid_until=grant.plan_valid_until,
      )
    )

  async def revoke(
    self,
    *,
    plan_id: str,
    reason: str,
    actor_user_id: str,
    now: Optional[datetime] = None,
  ) -> bool:
    checked_at = now or time_utils.now()
    grant = await self.repo.find_current_for_plan(plan_id, for_update=True)
    if grant is None:
      return False
    grant.revoked_at = checked_at
    grant.revoked_reason = str(reason or "USER_REVOKED")[:64]
    await self.repo.add_event_once(
      event_id=str(uuid.uuid4()),
      business_key=f"entry-auth-revoked:{grant.grant_id}",
      plan_id=grant.plan_id,
      grant_id=grant.grant_id,
      event_type="ENTRY_AUTO_AUTHORIZATION_REVOKED",
      reason_code=grant.revoked_reason,
      subject_fingerprint=_canonical({"actor_user_id": actor_user_id}),
      created_at=checked_at,
    )
    await self.db.commit()
    return True

  async def invalidate(
    self,
    *,
    plan_id: str,
    reason: str,
    now: Optional[datetime] = None,
    commit: bool = True,
  ) -> bool:
    """Invalidate the current grant for an unexplained authoritative change."""

    checked_at = now or time_utils.now()
    grant = await self.repo.find_current_for_plan(plan_id, for_update=True)
    if grant is None:
      return False
    await self._invalidate(
      grant,
      reason=str(reason or "ENTRY_AUTHORIZATION_INVALID")[:64],
      now=checked_at,
    )
    if commit:
      await self.db.commit()
    else:
      await self.db.flush()
    return True

  async def consume_real_fill(
    self,
    *,
    grant_id: str,
    trade_business_key: str,
    filled_amount_cny: Decimal,
    filled_volume: int,
    fill_price: Decimal,
    filled_at: datetime,
  ) -> EntryPlanAuthorizationBalance:
    amount = _decimal(
      filled_amount_cny, code="INVALID_FILL_AMOUNT", quantum=_MONEY_QUANTUM
    )
    price = _decimal(fill_price, code="INVALID_FILL_PRICE", quantum=_PRICE_QUANTUM)
    volume = int(filled_volume)
    if volume <= 0 or not str(trade_business_key or "").strip():
      raise EntryPlanAuthorizationError("INVALID_REAL_FILL", "真实成交事实无效")
    existing = await self.repo.find_consumption(str(trade_business_key))
    if existing is not None:
      if (
        str(existing.grant_id) != str(grant_id)
        or Decimal(str(existing.filled_amount_cny)) != amount
        or int(existing.filled_volume) != volume
        or Decimal(str(existing.fill_price)) != price
        or time_utils.to_shanghai(existing.filled_at)
        != time_utils.to_shanghai(filled_at)
      ):
        raise EntryPlanAuthorizationError(
          "REAL_FILL_REPLAY_MISMATCH",
          "相同成交业务键对应了不同的真实成交事实",
        )
      grant = await self.repo.find_grant(existing.grant_id)
      if grant is None:
        raise EntryPlanAuthorizationError("GRANT_NOT_FOUND", "成交对应授权不存在")
      return await self._balance(grant, filled_at)
    grant = await self.repo.find_grant(grant_id, for_update=True)
    if grant is None:
      raise EntryPlanAuthorizationError("GRANT_NOT_FOUND", "成交对应授权不存在")
    daily_before = await self.repo.consumed_on_date(
      grant.plan_id, time_utils.to_shanghai(filled_at).date()
    )
    prior_total = await self.repo.consumed_for_plan(grant.plan_id)
    prior_volume = await self.repo.consumed_volume_for_plan(grant.plan_id)
    consumption = EntryPlanAuthorizationConsumption(
      consumption_id=str(uuid.uuid4()),
      grant_id=grant.grant_id,
      plan_id=grant.plan_id,
      trade_business_key=str(trade_business_key),
      trade_date=time_utils.to_shanghai(filled_at).date(),
      filled_at=time_utils.to_shanghai(filled_at),
      filled_amount_cny=amount,
      filled_volume=volume,
      fill_price=price,
      created_at=time_utils.now(),
    )
    self.repo.add_consumption(consumption)
    grant.consumed_total_amount_cny = prior_total + amount
    grant.consumed_total_volume = prior_volume + volume
    breach: Optional[str] = None
    if Decimal(str(grant.consumed_total_amount_cny)) > Decimal(
      str(grant.max_total_amount_cny)
    ):
      breach = "REAL_FILL_EXCEEDED_TOTAL_LIMIT"
    elif daily_before + amount > Decimal(str(grant.max_daily_amount_cny)):
      breach = "REAL_FILL_EXCEEDED_DAILY_LIMIT"
    elif amount > Decimal(str(grant.max_single_amount_cny)):
      breach = "REAL_FILL_EXCEEDED_SINGLE_LIMIT"
    elif price > Decimal(str(grant.max_buy_price)):
      breach = "REAL_FILL_EXCEEDED_MAX_PRICE"
    if breach is not None:
      await self._invalidate(
        grant, reason=breach, now=time_utils.to_shanghai(filled_at)
      )
    await self.repo.add_event_once(
      event_id=str(uuid.uuid4()),
      business_key=f"entry-auth-fill:{trade_business_key}",
      plan_id=grant.plan_id,
      grant_id=grant.grant_id,
      event_type="ENTRY_AUTHORIZATION_REAL_FILL_CONSUMED",
      reason_code=breach,
      subject_fingerprint=_canonical({"trade_business_key": trade_business_key}),
      created_at=time_utils.to_shanghai(filled_at),
    )
    await self.db.commit()
    return await self._balance(grant, filled_at)

  async def get_gate(self, account_id: str) -> EntryAutomationGateState:
    gate = await self.repo.find_gate(account_fingerprint(account_id))
    if gate is None:
      return EntryAutomationGateState(False, None, None, None)
    return EntryAutomationGateState(
      bool(gate.paused), gate.reason, gate.actor_user_id, gate.changed_at
    )

  async def set_paused(
    self,
    *,
    account_id: str,
    paused: bool,
    reason: str,
    actor_user_id: str,
    now: Optional[datetime] = None,
  ) -> EntryAutomationGateState:
    checked_at = now or time_utils.now()
    fingerprint = account_fingerprint(account_id)
    gate = await self.repo.find_gate(fingerprint, for_update=True)
    if gate is None:
      gate = EntryAutomationGate(
        account_fingerprint=fingerprint,
        paused=bool(paused),
        reason=str(reason or "")[:160] or None,
        actor_user_id=str(actor_user_id or "") or None,
        changed_at=checked_at,
      )
      self.repo.add_gate(gate)
    else:
      gate.paused = bool(paused)
      gate.reason = str(reason or "")[:160] or None
      gate.actor_user_id = str(actor_user_id or "") or None
      gate.changed_at = checked_at
    await self.repo.add_event_once(
      event_id=str(uuid.uuid4()),
      business_key=f"entry-automation-gate:{fingerprint}:{checked_at.isoformat(timespec='microseconds')}",
      plan_id="*",
      grant_id=None,
      event_type="ENTRY_AUTOMATION_PAUSED" if paused else "ENTRY_AUTOMATION_RESUMED",
      reason_code=str(reason or "")[:64] or None,
      subject_fingerprint=_canonical({"actor_user_id": actor_user_id}),
      created_at=checked_at,
    )
    await self.db.commit()
    return await self.get_gate(account_id)


__all__ = [
  "ENTRY_PLAN_AUTHORIZATION_ACTION",
  "ENTRY_PLAN_AUTHORIZATION_LIFETIME",
  "MAX_UNBOUNDED_ENTRY_PLAN_VALID_UNTIL",
  "EntryAutomationGateState",
  "EntryPlanAuthorizationBalance",
  "EntryPlanAuthorizationError",
  "EntryPlanAuthorizationPreview",
  "EntryPlanAuthorizationScope",
  "EntryPlanAuthorizationService",
  "EntryPlanAuthorizationValidation",
  "account_fingerprint",
  "normalize_scope",
  "scope_from_managed_entry_config",
]
