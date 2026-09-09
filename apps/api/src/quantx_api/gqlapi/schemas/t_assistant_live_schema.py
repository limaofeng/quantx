"""Explicit LIVE owner queue and device-bound two-phase entry confirmation."""

from dataclasses import asdict
from datetime import UTC, datetime

import strawberry
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import case, select

from ..security import authorized_account_id, principal_from_context
from ..t_assistant_release_confirmation import (
  consume_release_confirmation,
  issue_release_confirmation,
)
from ..trade_approval import (
  T_TRADE_ENTRY_APPROVAL,
  TradeApprovalChallengeError,
  TradeApprovalChallengeService,
  _aware_shanghai,
  _intent_expiry,
)
from ..types.trade_approval_types import (
  TradeApprovalConfirmationResult,
  TradeApprovalPreview,
  TradeApprovalPreviewResult,
)


@strawberry.input
class TAssistantReleaseRequest:
  account_id: str
  source_execution_id: str
  config_version_id: str
  expected_config_hash: str
  expected_head_version: int
  evaluation_id: str
  expected_report_hash: str
  expected_policy_hash: str
  window_start: datetime
  window_end: datetime


@strawberry.type
class TAssistantReleasePreview:
  challenge_id: str
  confirmation_token: str
  expires_at: datetime
  account_id: str
  source_execution_id: str
  config_version_id: str
  config_snapshot_hash: str
  report_hash: str
  policy_hash: str
  window_start: datetime
  window_end: datetime


@strawberry.type
class TAssistantReleaseResult:
  success: bool
  code: str
  message: str
  preview: TAssistantReleasePreview | None = None
  engine_command_id: str | None = None


@strawberry.type
class TAssistantLiveEntry:
  intent_id: str
  instrument_code: str
  status: str
  requested_amount: float | None
  reason: str
  expires_at: datetime | None
  can_preview: bool
  confirmation_status: str


@strawberry.type
class TAssistantLiveApprovalQueue:
  execution_id: str | None
  status: str | None
  entry_authorization: str | None
  entry_readiness: str | None
  reason_codes: list[str]
  entries: list[TAssistantLiveEntry]
  truncated: bool


@strawberry.type
class TAssistantLiveQuery:
  @strawberry.field(description="当前独立 LIVE 做 T 执行的人工确认队列")
  async def t_assistant_live_approval_queue(
    self, info: strawberry.types.Info, account_id: str
  ) -> TAssistantLiveApprovalQueue:
    principal = principal_from_context(info.context)
    principal.require_permission("strategy:read")
    account_id = authorized_account_id(info, account_id)
    async with AsyncSessionLocal() as db:
      heads = list(
        (
          await db.scalars(
            select(TTradeGlobalConfig)
            .where(TTradeGlobalConfig.account_id == account_id)
            .limit(2)
          )
        ).all()
      )
      if len(heads) > 1:
        raise ValueError("T_ASSISTANT_HEAD_AMBIGUOUS")
      head = heads[0] if heads else None
      source = (
        await db.scalar(
          select(TAssistantExecutionRecord)
          .where(
            TAssistantExecutionRecord.account_id == account_id,
            TAssistantExecutionRecord.config_id == head.id,
            TAssistantExecutionRecord.environment == "LIVE",
            TAssistantExecutionRecord.config_version_id
            == head.active_config_version_id,
            TAssistantExecutionRecord.status.in_(
              ["WARMING", "RUNNING", "DRAINING", "RECONCILE_REQUIRED"]
            ),
          )
          .order_by(
            case(
              (TAssistantExecutionRecord.status.in_(["RUNNING", "WARMING"]), 0), else_=1
            ),
            TAssistantExecutionRecord.created_at.desc(),
            TAssistantExecutionRecord.execution_id,
          )
          .limit(1)
        )
        if head
        else None
      )
      if source is None:
        return TAssistantLiveApprovalQueue(
          execution_id=None,
          status=None,
          entry_authorization=None,
          entry_readiness=None,
          reason_codes=[],
          entries=[],
          truncated=False,
        )
      intents = list(
        (
          await db.scalars(
            select(TradeIntentRecord)
            .where(
              TradeIntentRecord.account_id == account_id,
              TradeIntentRecord.owner_type == "T_ASSISTANT_EXECUTION",
              TradeIntentRecord.owner_id == source.execution_id,
              TradeIntentRecord.environment == "LIVE",
              TradeIntentRecord.direction == "BUY",
              TradeIntentRecord.status.in_(
                ["AWAITING_APPROVAL", "ALLOCATION_PENDING", "EXECUTION_READY"]
              ),
            )
            .order_by(TradeIntentRecord.created_at.desc(), TradeIntentRecord.id)
            .limit(51)
          )
        ).all()
      )
      ready = (
        {"t-trade:control", "liquidation:control", "trade:approve"}.issubset(
          principal.permissions
        )
        and head.enabled is True
        and head.desired_environment == "LIVE"
        and source.status == "RUNNING"
        and source.entry_readiness == "READY"
        and source.entry_authorization == "MANUAL_CONFIRM"
        and source.scorer_mode == "RULE_ONLY"
      )
      operations = {}
      references = await db.execute(
        select(TradeConfirmationChallenge, EngineCommandOutbox)
        .outerjoin(
          EngineCommandOutbox,
          EngineCommandOutbox.message_id
          == TradeConfirmationChallenge.result_reference["engine_command"][
            "message_id"
          ].as_string(),
        )
        .where(
          TradeConfirmationChallenge.account_id == account_id,
          TradeConfirmationChallenge.owner_type == "T_ASSISTANT_EXECUTION",
          TradeConfirmationChallenge.owner_id == source.execution_id,
          TradeConfirmationChallenge.environment == "LIVE",
          TradeConfirmationChallenge.action == T_TRADE_ENTRY_APPROVAL,
          TradeConfirmationChallenge.consumed_at.is_not(None),
          TradeConfirmationChallenge.payload["intent_id"]
          .as_string()
          .in_([intent.id for intent in intents[:50]]),
        )
        .order_by(
          TradeConfirmationChallenge.created_at.desc(), TradeConfirmationChallenge.id
        )
      )
      for challenge, command in references:
        identity = challenge.payload.get("intent_id")
        if identity in operations:
          continue
        if (
          command is None
          or command.command_type != "T_ASSISTANT_APPROVE_ENTRY"
          or command.aggregate_id != source.execution_id
          or command.payload.get("intent_id") != identity
          or command.payload.get("account_id") != account_id
        ):
          operations[identity] = "UNKNOWN"
        elif TradeApprovalChallengeService._terminal_rejection(command):
          operations[identity] = "FAILED"
        elif command.processing_status == "SUCCEEDED":
          operations[identity] = "SUCCEEDED"
        else:
          operations[identity] = "PENDING"
      now = datetime.now(UTC)
      entries = []
      for intent in intents[:50]:
        expires = _intent_expiry(intent)
        expires = _aware_shanghai(expires) if expires else None
        entries.append(
          TAssistantLiveEntry(
            intent_id=intent.id,
            instrument_code=intent.instrument_code,
            status=intent.status,
            requested_amount=intent.target_amount,
            reason=intent.reason or "",
            expires_at=expires,
            can_preview=ready
            and intent.status == "AWAITING_APPROVAL"
            and expires is not None
            and expires > now
            and operations.get(intent.id, "NONE") in {"NONE", "FAILED"},
            confirmation_status=operations.get(intent.id, "NONE"),
          )
        )
      return TAssistantLiveApprovalQueue(
        execution_id=source.execution_id,
        status=source.status,
        entry_authorization=source.entry_authorization,
        entry_readiness=source.entry_readiness,
        reason_codes=list(source.entry_readiness_reasons or []),
        entries=entries,
        truncated=len(intents) > 50,
      )


def _approval_scope(info, account_id):
  principal = principal_from_context(info.context)
  for permission in ("t-trade:control", "liquidation:control", "trade:approve"):
    principal.require_permission(permission)
  return principal, authorized_account_id(info, account_id)


@strawberry.type
class TAssistantLiveMutation:
  @strawberry.mutation(
    description="原生设备预览 CANARY 发布确认；正式证据由 Engine 执行时复核"
  )
  async def preview_t_assistant_live_release(
    self, info: strawberry.types.Info, request: TAssistantReleaseRequest
  ) -> TAssistantReleaseResult:
    principal = principal_from_context(info.context)
    try:
      async with AsyncSessionLocal() as db, db.begin():
        issued = await issue_release_confirmation(
          db, principal=principal, request=asdict(request), now=datetime.now(UTC)
        )
      normalized = issued["request"]
      return TAssistantReleaseResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对目标配置、已审核报告和维护窗口；确认后交由 Engine 复核发布",
        preview=TAssistantReleasePreview(
          challenge_id=issued["challenge_id"],
          confirmation_token=issued["confirmation_token"],
          expires_at=issued["expires_at"],
          account_id=normalized["account_id"],
          source_execution_id=normalized["source_execution_id"],
          config_version_id=normalized["config_version_id"],
          config_snapshot_hash=normalized["expected_config_hash"],
          report_hash=normalized["expected_report_hash"],
          policy_hash=normalized["expected_policy_hash"],
          window_start=datetime.fromisoformat(normalized["window_start"]),
          window_end=datetime.fromisoformat(normalized["window_end"]),
        ),
      )
    except TradeApprovalChallengeError as exc:
      return TAssistantReleaseResult(success=False, code=exc.code, message=exc.message)
    except ValueError:
      return TAssistantReleaseResult(
        success=False,
        code="RELEASE_PREVIEW_UNAVAILABLE",
        message="发布范围、配置或窗口已变化，请核对后重试",
      )

  @strawberry.mutation(
    description="消费原设备发布凭据并入队；入队不表示发布成功或允许交易"
  )
  async def confirm_t_assistant_live_release(
    self, info: strawberry.types.Info, challenge_id: str, confirmation_token: str
  ) -> TAssistantReleaseResult:
    principal = principal_from_context(info.context)
    try:
      async with AsyncSessionLocal() as db, db.begin():
        command_id = await consume_release_confirmation(
          db,
          principal=principal,
          challenge_id=challenge_id,
          confirmation_token=confirmation_token,
          now=datetime.now(UTC),
        )
      return TAssistantReleaseResult(
        success=True,
        code="RELEASE_QUEUED",
        message="发布请求已入队，等待 Engine 复核结果",
        engine_command_id=command_id,
      )
    except TradeApprovalChallengeError as exc:
      return TAssistantReleaseResult(success=False, code=exc.code, message=exc.message)
    except ValueError:
      return TAssistantReleaseResult(
        success=False,
        code="RELEASE_CONFIRMATION_UNAVAILABLE",
        message="发布凭据或配置已变化，请重新预览",
      )

  @strawberry.mutation(description="预览独立 LIVE ENTRY 及其绑定的自动退出保护")
  async def preview_t_assistant_live_entry(
    self,
    info: strawberry.types.Info,
    account_id: str,
    execution_id: str,
    intent_id: str,
  ) -> TradeApprovalPreviewResult:
    principal, account_id = _approval_scope(info, account_id)
    try:
      preview = await TradeApprovalChallengeService.issue(
        principal=principal,
        action=T_TRADE_ENTRY_APPROVAL,
        account_id=account_id,
        execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id),
        environment=ExecutionEnvironment.LIVE,
        intent_id=intent_id,
      )
      return TradeApprovalPreviewResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对买入信息及自动退出保护",
        preview=TradeApprovalPreview.from_data(preview),
      )
    except TradeApprovalChallengeError as exc:
      return TradeApprovalPreviewResult(
        success=False, code=exc.code, message=exc.message
      )
    except ValueError:
      return TradeApprovalPreviewResult(
        success=False,
        code="T_ASSISTANT_PREVIEW_UNAVAILABLE",
        message="执行状态或确认材料已变化，请刷新后重试",
      )

  @strawberry.mutation(
    description="消费原设备凭据，将同一 ENTRY 交给 Engine 重新分配；不表示成交"
  )
  async def confirm_t_assistant_live_entry(
    self,
    info: strawberry.types.Info,
    account_id: str,
    execution_id: str,
    intent_id: str,
    confirmation_token: str,
  ) -> TradeApprovalConfirmationResult:
    principal, account_id = _approval_scope(info, account_id)
    try:
      dispatch = await TradeApprovalChallengeService.consume(
        principal=principal,
        action=T_TRADE_ENTRY_APPROVAL,
        account_id=account_id,
        execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id),
        environment=ExecutionEnvironment.LIVE,
        intent_id=intent_id,
        confirmation_token=confirmation_token,
        command_type="T_ASSISTANT_APPROVE_ENTRY",
        command_aggregate_id=execution_id,
        command_idempotency_key_factory=lambda challenge: (
          f"t-assistant-confirm:{challenge}"
        ),
        command_payload={
          "execution_id": execution_id,
          "intent_id": intent_id,
          "account_id": account_id,
        },
        return_command_reference=True,
      )
      return TradeApprovalConfirmationResult(
        success=True,
        code="T_ASSISTANT_CONFIRMATION_QUEUED",
        message="确认已提交，等待重新分配；尚未成交",
        challenge_id=dispatch.challenge_id,
      )
    except TradeApprovalChallengeError as exc:
      return TradeApprovalConfirmationResult(
        success=False, code=exc.code, message=exc.message
      )
    except Exception:
      # The client must retain the exact token for retry after uncertain transport
      # or database outcomes. Never infer a rejection or generate another operation.
      return TradeApprovalConfirmationResult(
        success=False,
        code="T_ASSISTANT_CONFIRMATION_OUTCOME_UNKNOWN",
        message="确认结果暂未明确，请重试原确认请求",
      )
