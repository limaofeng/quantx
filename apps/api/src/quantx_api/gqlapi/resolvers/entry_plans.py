"""GraphQL resolver bridge for Engine-owned managed entry plans."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from dataclasses import fields as dataclass_fields
from typing import Any, Iterable, Optional, TypeVar

from quantx_domain.trading.entry_plan import (
  EntryAuthorizationMode,
  EntryEnvironment,
  ManagedEntryPlanConfig,
)
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.managed_plan_repository import (
  ManagedPlanRepository,
)
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.engine_command_service import engine_command_service
from quantx_infrastructure.services.entry_plan_authorization_service import (
  EntryPlanAuthorizationError,
  EntryPlanAuthorizationScope,
  EntryPlanAuthorizationService,
  scope_from_managed_entry_config,
)
from quantx_infrastructure.services.entry_plan_projection_service import (
  entry_plan_projection_service,
)

from quantx_api.gqlapi.types.entry_plan_types import (
  CreateEntryPlanInput,
  EntryAutomationStatus,
  EntryExitProtection,
  EntryIntent,
  EntryIntentPreview,
  EntryPlan,
  EntryPlanAuthorizationConfirmationInput,
  EntryPlanAuthorizationPreview,
  EntryPlanAuthorizationPreviewInput,
  EntryPlanAuthorizationResult,
  EntryPlanCapabilities,
  EntryPlanCompletion,
  EntryPlanEvent,
  EntryPlanExecution,
  EntryPlanMutationResult,
  EntryPlanPacing,
  EntryPlanRule,
  EntryPriceLadderLevel,
  EntryRuleCapability,
  EntryRuleFieldCapability,
  EntryRulePreset,
  EntryTargetModeCapability,
  UpdateEntryPlanInput,
)

T = TypeVar("T")


class EntryPlanResolver:
  @staticmethod
  async def _engine_request(
    command_type: str,
    payload: dict[str, Any],
    *,
    aggregate_id: str,
    idempotency_key: str = "",
  ) -> dict[str, Any]:
    receipt = await engine_command_service.request(
      command_type,
      payload,
      aggregate_id=aggregate_id,
      idempotency_key=(
        idempotency_key
        or f"{command_type.lower()}:{aggregate_id}:{uuid.uuid4()}"
      ),
    )
    if receipt.status == "FAILED":
      raise ValueError(receipt.error or f"Engine command failed: {command_type}")
    if receipt.status != "SUCCEEDED":
      raise ValueError(f"Engine 命令已排队但尚未确认: {receipt.message_id}")
    return dict(receipt.result or {})

  @staticmethod
  def _typed(type_: type[T], payload: dict[str, Any]) -> T:
    known = {item.name for item in dataclass_fields(type_)}
    return type_(**{key: value for key, value in payload.items() if key in known})

  @classmethod
  def _plan_type(cls, payload: dict[str, Any]) -> EntryPlan:
    data = dict(payload)
    data["trigger_rules"] = [
      EntryPlanRule(
        **{
          **{
            key: value
            for key, value in rule.items()
            if key != "ladder_levels"
          },
          "ladder_levels": [
            EntryPriceLadderLevel(**level)
            for level in list(rule.get("ladder_levels") or [])
          ],
        }
      )
      for rule in list(data.get("trigger_rules") or [])
    ]
    data["pacing_policy"] = EntryPlanPacing(**data["pacing_policy"])
    data["execution_policy"] = EntryPlanExecution(**data["execution_policy"])
    data["completion_policy"] = EntryPlanCompletion(**data["completion_policy"])
    data["exit_protection"] = EntryExitProtection(**data["exit_protection"])
    return cls._typed(EntryPlan, data)

  @staticmethod
  async def _authorization_scope(
    account_id: str,
    plan_id: str,
    config_version: int,
  ) -> tuple[ManagedEntryPlanConfig, EntryPlanAuthorizationScope]:
    async with AsyncSessionLocal() as db:
      run_repo = StrategyRunRepository(db)
      run = await run_repo.find_run_by_id(str(plan_id))
      if run is None:
        plan = await ManagedPlanRepository(db).find(str(plan_id))
        run = (
          await run_repo.find_run_by_id(str(plan.current_run_id))
          if plan is not None and plan.current_run_id
          else None
        )
      if run is None or getattr(run, "strategy", None) is None:
        raise ValueError("建仓/加仓计划不存在")
      if str(run.strategy.class_name) != "AshareManagedEntryPlanStrategy":
        raise ValueError("指定运行不是建仓/加仓托管计划")
      parameters = dict(run.parameters or {})
      if str(parameters.get("account_id") or "") != str(account_id):
        raise ValueError("建仓/加仓计划不属于当前账户")
      raw_config = parameters.get("managed_entry_plan")
      if not isinstance(raw_config, dict):
        raise ValueError("建仓计划配置缺失")
      config = ManagedEntryPlanConfig.from_dict(raw_config)
      if config.config_version != int(config_version):
        raise ValueError("计划版本已变化，请刷新后重新授权")
      if (
        config.execution_policy.environment != EntryEnvironment.LIVE
        or config.execution_policy.authorization_mode
        != EntryAuthorizationMode.AUTO
      ):
        raise ValueError("只有 LIVE 自动托管计划可以申请精确授权")
      return config, scope_from_managed_entry_config(
        plan_id=str(plan_id),
        run_id=str(run.id),
        config=config,
      )

  @classmethod
  async def list(
    cls,
    account_id: str,
    *,
    instrument_code: str = "",
    statuses: Optional[Iterable[str]] = None,
  ) -> list[EntryPlan]:
    rows = await entry_plan_projection_service.list(
      account_id,
      instrument_code=instrument_code,
      statuses=statuses,
    )
    return [cls._plan_type(row) for row in rows]

  @classmethod
  async def get(cls, account_id: str, plan_id: str) -> Optional[EntryPlan]:
    row = await entry_plan_projection_service.get(plan_id, account_id=account_id)
    return cls._plan_type(row) if row else None

  @staticmethod
  async def capabilities() -> EntryPlanCapabilities:
    payload = entry_plan_projection_service.capabilities()
    target_modes = [
      EntryTargetModeCapability(**item) for item in payload["target_modes"]
    ]
    rule_types = [
      EntryRuleCapability(
        **{
          **{
            key: value
            for key, value in item.items()
            if key not in {"fields", "presets"}
          },
          "fields": [
            EntryRuleFieldCapability(**definition)
            for definition in item.get("fields", [])
          ],
          "presets": [EntryRulePreset(**preset) for preset in item["presets"]],
        }
      )
      for item in payload["rule_types"]
    ]
    return EntryPlanCapabilities(
      version=payload["version"],
      target_modes=target_modes,
      rule_types=rule_types,
      allowed_buckets=payload["allowed_buckets"],
      environments=payload["environments"],
      authorization_modes=payload["authorization_modes"],
      max_open_orders=payload["max_open_orders"],
    )

  @classmethod
  async def events(
    cls, account_id: str, plan_id: str, limit: int
  ) -> list[EntryPlanEvent]:
    rows = await entry_plan_projection_service.events(
      plan_id,
      account_id=account_id,
      limit=limit,
    )
    return [cls._typed(EntryPlanEvent, row) for row in rows]

  @classmethod
  async def pending_intents(
    cls, account_id: str, instrument_code: str = ""
  ) -> list[EntryIntent]:
    rows = await entry_plan_projection_service.pending_intents(
      account_id,
      instrument_code=instrument_code,
    )
    return [cls._typed(EntryIntent, row) for row in rows]

  @classmethod
  async def automation_status(cls, account_id: str) -> EntryAutomationStatus:
    return cls._typed(
      EntryAutomationStatus,
      await entry_plan_projection_service.automation_status(account_id),
    )

  @classmethod
  async def create(
    cls,
    account_id: str,
    actor_user_id: str,
    input: CreateEntryPlanInput,
  ) -> EntryPlanMutationResult:
    payload = asdict(input)
    idempotency_key = str(payload.pop("idempotency_key", "") or "")
    try:
      result = await cls._engine_request(
        "ENTRY_PLAN_CREATE",
        {
          "account_id": account_id,
          "actor_user_id": actor_user_id,
          "input": payload,
        },
        aggregate_id=f"entry-plan:{account_id}:{input.instrument_code.upper()}",
        idempotency_key=idempotency_key,
      )
      plan_id = str(result.get("plan_id") or "")
      plan = await cls.get(account_id, plan_id)
      return EntryPlanMutationResult(
        success=plan is not None,
        code="ENTRY_PLAN_CREATED" if plan else "ENTRY_PLAN_PROJECTION_MISSING",
        message="买入托管计划已创建" if plan else "计划已创建但投影尚未就绪",
        plan=plan,
      )
    except ValueError as exc:
      return cls._mutation_error(exc)

  @classmethod
  async def update(
    cls,
    account_id: str,
    actor_user_id: str,
    input: UpdateEntryPlanInput,
  ) -> EntryPlanMutationResult:
    payload = asdict(input)
    idempotency_key = str(payload.pop("idempotency_key", "") or "")
    try:
      await cls._require_plan(account_id, str(input.plan_id))
      await cls._engine_request(
        "ENTRY_PLAN_UPDATE",
        {
          "account_id": account_id,
          "actor_user_id": actor_user_id,
          "input": payload,
        },
        aggregate_id=str(input.plan_id),
        idempotency_key=idempotency_key,
      )
      return EntryPlanMutationResult(
        success=True,
        code="ENTRY_PLAN_UPDATED",
        message="买入托管计划已更新并重新检查授权",
        plan=await cls.get(account_id, str(input.plan_id)),
      )
    except ValueError as exc:
      return cls._mutation_error(exc)

  @classmethod
  async def set_enabled(
    cls,
    account_id: str,
    actor_user_id: str,
    plan_id: str,
    enabled: bool,
    config_version: int,
  ) -> EntryPlanMutationResult:
    try:
      await cls._require_plan(account_id, plan_id)
      await cls._engine_request(
        "ENTRY_PLAN_SET_ENABLED",
        {
          "account_id": account_id,
          "actor_user_id": actor_user_id,
          "plan_id": plan_id,
          "enabled": enabled,
          "config_version": config_version,
        },
        aggregate_id=plan_id,
      )
      return EntryPlanMutationResult(
        success=True,
        code="ENTRY_PLAN_ARMED" if enabled else "ENTRY_PLAN_PAUSED",
        message="计划已开始监控" if enabled else "计划已暂停新触发",
        plan=await cls.get(account_id, plan_id),
      )
    except ValueError as exc:
      return cls._mutation_error(exc)

  @classmethod
  async def cancel(
    cls,
    account_id: str,
    actor_user_id: str,
    plan_id: str,
    config_version: int,
    cancel_working_order: bool,
  ) -> EntryPlanMutationResult:
    try:
      await cls._require_plan(account_id, plan_id)
      await cls._engine_request(
        "ENTRY_PLAN_CANCEL",
        {
          "account_id": account_id,
          "actor_user_id": actor_user_id,
          "plan_id": plan_id,
          "config_version": config_version,
          "cancel_working_order": cancel_working_order,
        },
        aggregate_id=plan_id,
      )
      return EntryPlanMutationResult(
        success=True,
        code="ENTRY_PLAN_CANCEL_REQUESTED",
        message="计划已停止新买入，工作中委托将按选择继续收敛",
        plan=await cls.get(account_id, plan_id),
      )
    except ValueError as exc:
      return cls._mutation_error(exc)

  @classmethod
  async def evaluate_now(
    cls, account_id: str, plan_id: str
  ) -> EntryPlanMutationResult:
    try:
      await cls._require_plan(account_id, plan_id)
      await cls._engine_request(
        "ENTRY_PLAN_EVALUATE_NOW",
        {"account_id": account_id, "plan_id": plan_id},
        aggregate_id=plan_id,
      )
      return EntryPlanMutationResult(
        success=True,
        code="ENTRY_PLAN_EVALUATED",
        message="已使用最新可用行情重新检查计划",
        plan=await cls.get(account_id, plan_id),
      )
    except ValueError as exc:
      return cls._mutation_error(exc)

  @classmethod
  async def trigger_manual_rule(
    cls,
    account_id: str,
    actor_user_id: str,
    plan_id: str,
    rule_id: str,
  ) -> EntryPlanMutationResult:
    try:
      await cls._require_plan(account_id, plan_id)
      await cls._engine_request(
        "ENTRY_PLAN_TRIGGER_MANUAL",
        {
          "account_id": account_id,
          "actor_user_id": actor_user_id,
          "plan_id": plan_id,
          "rule_id": rule_id,
        },
        aggregate_id=plan_id,
        idempotency_key=(
          f"entry-plan-manual-trigger:{plan_id}:{rule_id}:{uuid.uuid4()}"
        ),
      )
      return EntryPlanMutationResult(
        success=True,
        code="ENTRY_PLAN_MANUAL_TRIGGERED",
        message="人工规则已触发，系统正按最新行情和风控检查本批买入",
        plan=await cls.get(account_id, plan_id),
      )
    except ValueError as exc:
      return cls._mutation_error(exc)

  @classmethod
  async def set_automation_paused(
    cls,
    account_id: str,
    actor_user_id: str,
    paused: bool,
    reason: str,
  ) -> EntryAutomationStatus:
    await cls._engine_request(
      "ENTRY_AUTOMATION_SET_PAUSED",
      {
        "account_id": account_id,
        "actor_user_id": actor_user_id,
        "paused": paused,
        "reason": str(reason or "USER_REQUESTED")[:200],
      },
      aggregate_id=f"entry-automation:{account_id}",
    )
    return await cls.automation_status(account_id)

  @classmethod
  async def preview_authorization(
    cls,
    account_id: str,
    user_id: str,
    device_session_id: str,
    input: EntryPlanAuthorizationPreviewInput,
  ) -> EntryPlanAuthorizationPreview:
    config, scope = await cls._authorization_scope(
      account_id,
      str(input.plan_id),
      input.config_version,
    )
    try:
      async with AsyncSessionLocal() as db:
        preview = await EntryPlanAuthorizationService(db).preview(
          scope=scope,
          user_id=user_id,
          device_session_id=device_session_id,
          account_id=account_id,
          idempotency_key=input.idempotency_key,
        )
    except EntryPlanAuthorizationError as exc:
      raise ValueError(exc.message) from exc
    target = config.target_policy
    pacing = config.pacing_policy
    completion = config.completion_policy
    return EntryPlanAuthorizationPreview(
      challenge_id=preview.challenge_id,
      confirmation_token=preview.confirmation_token,
      authorization_fingerprint=preview.authorization_fingerprint,
      challenge_expires_at=preview.challenge_expires_at.isoformat(),
      authorization_expires_at=preview.authorization_expires_at.isoformat(),
      summary=(
        f"授权 {config.instrument_code} 在总预算 ¥{target.max_total_amount_cny:,.2f}、"
        f"单笔 ¥{pacing.max_single_intent_amount_cny:,.2f} 和最高买价 "
        f"¥{completion.max_buy_price:,.3f} 内自动分批买入，至少保留 "
        f"{pacing.cash_buffer_pct:.1%} 现金"
      ),
      risk_envelope={
        "instrument_code": config.instrument_code,
        "bucket": config.bucket,
        "config_version": config.config_version,
        "max_total_amount_cny": target.max_total_amount_cny,
        "max_single_amount_cny": pacing.max_single_intent_amount_cny,
        "max_daily_amount_cny": pacing.max_daily_filled_amount_cny,
        "cash_buffer_pct": pacing.cash_buffer_pct,
        "max_position_pct": target.max_position_pct,
        "max_buy_price": completion.max_buy_price,
        "max_slippage_bps": config.execution_policy.max_slippage_bps,
        "max_price_deviation_bps": (
          config.execution_policy.max_price_deviation_bps
        ),
        "account_snapshot_version": (
          target.baseline_snapshot.account_snapshot_version
        ),
      },
    )

  @classmethod
  async def confirm_authorization(
    cls,
    account_id: str,
    user_id: str,
    device_session_id: str,
    input: EntryPlanAuthorizationConfirmationInput,
  ) -> EntryPlanAuthorizationResult:
    try:
      _config, scope = await cls._authorization_scope(
        account_id,
        str(input.plan_id),
        input.config_version,
      )
      async with AsyncSessionLocal() as db:
        service = EntryPlanAuthorizationService(db)
        grant = await service.confirm(
          scope=scope,
          user_id=user_id,
          device_session_id=device_session_id,
          account_id=account_id,
          challenge_id=str(input.challenge_id),
          confirmation_token=input.confirmation_token,
        )
      try:
        await cls._engine_request(
          "ENTRY_PLAN_SET_ENABLED",
          {
            "account_id": account_id,
            "actor_user_id": user_id,
            "plan_id": str(input.plan_id),
            "enabled": True,
            "config_version": input.config_version,
          },
          aggregate_id=str(input.plan_id),
          idempotency_key=(
            f"entry-plan-authorized-start:{input.plan_id}:{input.config_version}:"
            f"{input.challenge_id}"
          ),
        )
      except ValueError:
        async with AsyncSessionLocal() as db:
          await EntryPlanAuthorizationService(db).revoke(
            plan_id=str(input.plan_id),
            reason="ACTIVATION_FAILED",
            actor_user_id=user_id,
          )
        raise
      return EntryPlanAuthorizationResult(
        success=True,
        code="ENTRY_PLAN_AUTHORIZED",
        message="实盘自动建仓授权已生效，计划已开始监控",
        authorization_state="AUTHORIZED",
        grant_id=str(grant.grant_id),
        expires_at=grant.expires_at.isoformat(),
        plan=await cls.get(account_id, str(input.plan_id)),
      )
    except (EntryPlanAuthorizationError, ValueError) as exc:
      message = (
        exc.message if isinstance(exc, EntryPlanAuthorizationError) else str(exc)
      )
      return EntryPlanAuthorizationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=message,
        authorization_state="REQUIRED",
      )

  @classmethod
  async def preview_intent(
    cls, account_id: str, plan_id: str, intent_id: str
  ) -> EntryIntentPreview:
    plan = await cls._require_plan(account_id, plan_id)
    payload = await cls._engine_request(
      "ENTRY_PLAN_PREVIEW_INTENT",
      {
        "account_id": account_id,
        "plan_id": plan_id,
        "run_id": str(plan.run_id),
        "intent_id": intent_id,
      },
      aggregate_id=plan_id,
      idempotency_key=f"entry-intent-preview:{plan_id}:{intent_id}:{uuid.uuid4()}",
    )
    return cls._typed(
      EntryIntentPreview,
      {
        **payload,
        "challenge_id": "",
        "confirmation_token": "",
        "challenge_expires_at": "",
        "warnings": [],
      },
    )

  @classmethod
  async def confirm_intent(
    cls,
    account_id: str,
    plan_id: str,
    intent_id: str,
    *,
    actor_user_id: str,
    device_session_id: str,
    challenge_id: str,
  ) -> EntryPlanMutationResult:
    try:
      plan = await cls._require_plan(account_id, plan_id)
      result = await cls._engine_request(
        "STRATEGY_APPROVE_TRADE_INTENT",
        {
          "run_id": str(plan.run_id),
          "intent_id": intent_id,
          "approval_audit": {
            "actor_id": str(actor_user_id or "")[:64],
            "device_session_id": str(device_session_id or "")[:64],
            "challenge_id": str(challenge_id or "")[:64],
            "channel": "ENTRY_PLAN_DEVICE_CHALLENGE",
          },
        },
        aggregate_id=plan_id,
        idempotency_key=f"entry-intent-confirm:{plan_id}:{intent_id}",
      )
      success = bool(result.get("success"))
      return EntryPlanMutationResult(
        success=success,
        code=str(result.get("code") or ("APPROVED" if success else "REJECTED")),
        message=str(result.get("message") or "买入意图已处理"),
        plan=await cls.get(account_id, plan_id),
      )
    except ValueError as exc:
      return cls._mutation_error(exc)

  @classmethod
  async def reject_intent(
    cls, account_id: str, plan_id: str, intent_id: str
  ) -> EntryPlanMutationResult:
    try:
      plan = await cls._require_plan(account_id, plan_id)
      result = await cls._engine_request(
        "STRATEGY_REJECT_TRADE_INTENT",
        {
          "run_id": str(plan.run_id),
          "intent_id": intent_id,
          "reason": "USER_REJECTED",
        },
        aggregate_id=plan_id,
        idempotency_key=f"entry-intent-reject:{plan_id}:{intent_id}",
      )
      success = bool(result.get("success"))
      return EntryPlanMutationResult(
        success=success,
        code=str(result.get("code") or ("REJECTED" if success else "FAILED")),
        message=str(result.get("message") or "买入意图已忽略"),
        plan=await cls.get(account_id, plan_id),
      )
    except ValueError as exc:
      return cls._mutation_error(exc)

  @classmethod
  async def _require_plan(cls, account_id: str, plan_id: str) -> EntryPlan:
    plan = await cls.get(account_id, plan_id)
    if plan is None:
      raise ValueError("建仓/加仓计划不存在或不属于当前账户")
    return plan

  @staticmethod
  def _mutation_error(exc: Exception) -> EntryPlanMutationResult:
    message = str(exc)
    code = "VERSION_CONFLICT" if "版本" in message else "VALIDATION_FAILED"
    return EntryPlanMutationResult(
      success=False,
      code=code,
      message=message,
    )
