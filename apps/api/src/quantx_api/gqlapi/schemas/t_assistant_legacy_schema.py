"""Native maintenance review and confirmation for a bound legacy T run."""

from dataclasses import asdict
from datetime import UTC, datetime

import strawberry
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from strawberry.scalars import JSON

from ..security import principal_from_context
from ..t_assistant_legacy_drain_confirmation import (
  consume_drain_confirmation,
  enqueue_legacy_inventory,
  issue_drain_confirmation,
  read_legacy_confirmation_status,
  read_legacy_maintenance_operation,
  read_legacy_maintenance_source,
)
from ..trade_approval import TradeApprovalChallengeError


@strawberry.input
class TAssistantLegacyInventoryRequest:
  account_id: str
  config_id: str
  run_id: str
  expected_head_version: int


@strawberry.input
class TAssistantLegacyDrainRequest(TAssistantLegacyInventoryRequest):
  inventory_operation_id: str
  expected_inventory_hash: str
  window_start: datetime
  window_end: datetime


@strawberry.type
class TAssistantLegacyDrainPreview:
  challenge_id: str
  confirmation_token: str
  expires_at: datetime
  request: JSON


@strawberry.type
class TAssistantLegacyMaintenanceResult:
  success: bool
  code: str
  message: str
  engine_command_id: str | None = None
  preview: TAssistantLegacyDrainPreview | None = None


@strawberry.type
class TAssistantLegacyMaintenanceOperation:
  command_id: str
  status: str
  evidence: JSON | None = None


@strawberry.type
class TAssistantLegacyConfirmationStatus:
  challenge_id: str
  request: JSON
  status: str
  engine_command_id: str | None = None


@strawberry.type
class TAssistantLegacyMaintenanceSource:
  account_id: str
  config_id: str
  run_id: str
  head_version: int
  draining: bool


def _failed(exc):
  if isinstance(exc, TradeApprovalChallengeError):
    return TAssistantLegacyMaintenanceResult(
      success=False, code=exc.code, message=exc.message
    )
  return TAssistantLegacyMaintenanceResult(
    success=False,
    code="LEGACY_MAINTENANCE_UNAVAILABLE",
    message="维护范围、凭据或义务清单已变化，请重新核对",
  )


@strawberry.type
class TAssistantLegacyQuery:
  @strawberry.field(
    description="读取当前账户绑定的旧做 T 来源和精确状态版本，不授权切换"
  )
  async def t_assistant_legacy_maintenance_source(
    self, info: strawberry.types.Info, account_id: str
  ) -> TAssistantLegacyMaintenanceSource | None:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db, db.begin():
      result = await read_legacy_maintenance_source(
        db, principal=principal, account_id=account_id
      )
    return TAssistantLegacyMaintenanceSource(**result) if result else None

  @strawberry.field(
    description="原设备锁定后按确认 ID 找回处理状态，不返回或重新签发令牌"
  )
  async def t_assistant_legacy_confirmation_status(
    self, info: strawberry.types.Info, challenge_id: str
  ) -> TAssistantLegacyConfirmationStatus:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db, db.begin():
      result = await read_legacy_confirmation_status(
        db, principal=principal, challenge_id=challenge_id, now=datetime.now(UTC)
      )
    return TAssistantLegacyConfirmationStatus(**result)

  @strawberry.field(description="读取维护命令及持久化审计；入队不代表排空完成")
  async def t_assistant_legacy_maintenance_operation(
    self, info: strawberry.types.Info, account_id: str, command_id: str
  ) -> TAssistantLegacyMaintenanceOperation:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db, db.begin():
      value = await read_legacy_maintenance_operation(
        db, principal=principal, account_id=account_id, command_id=command_id
      )
    return TAssistantLegacyMaintenanceOperation(**value)


@strawberry.type
class TAssistantLegacyMutation:
  @strawberry.mutation(description="准备旧做 T 义务清单供复核，不停止入场、不解除绑定")
  async def prepare_t_assistant_legacy_inventory(
    self,
    info: strawberry.types.Info,
    request_id: str,
    request: TAssistantLegacyInventoryRequest,
  ) -> TAssistantLegacyMaintenanceResult:
    principal = principal_from_context(info.context)
    try:
      async with AsyncSessionLocal() as db, db.begin():
        identity = await enqueue_legacy_inventory(
          db,
          principal=principal,
          request_id=request_id,
          request=asdict(request),
          now=datetime.now(UTC),
        )
      return TAssistantLegacyMaintenanceResult(
        success=True,
        code="INVENTORY_QUEUED",
        message="清单准备已入队，请等待并复核结果",
        engine_command_id=identity,
      )
    except ValueError as exc:
      return _failed(exc)

  @strawberry.mutation(description="预览绑定已复核义务清单和维护窗口的排空确认")
  async def preview_t_assistant_legacy_drain(
    self, info: strawberry.types.Info, request: TAssistantLegacyDrainRequest
  ) -> TAssistantLegacyMaintenanceResult:
    principal = principal_from_context(info.context)
    try:
      async with AsyncSessionLocal() as db, db.begin():
        issued = await issue_drain_confirmation(
          db, principal=principal, request=asdict(request), now=datetime.now(UTC)
        )
      return TAssistantLegacyMaintenanceResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对义务清单和维护窗口；确认后停止新的买入",
        preview=TAssistantLegacyDrainPreview(**issued),
      )
    except ValueError as exc:
      return _failed(exc)

  @strawberry.mutation(description="消费原设备维护凭据，交由 Engine 停止旧执行的新入场")
  async def confirm_t_assistant_legacy_drain(
    self, info: strawberry.types.Info, challenge_id: str, confirmation_token: str
  ) -> TAssistantLegacyMaintenanceResult:
    principal = principal_from_context(info.context)
    try:
      async with AsyncSessionLocal() as db, db.begin():
        identity = await consume_drain_confirmation(
          db,
          principal=principal,
          challenge_id=challenge_id,
          confirmation_token=confirmation_token,
          now=datetime.now(UTC),
        )
      return TAssistantLegacyMaintenanceResult(
        success=True,
        code="DRAIN_QUEUED",
        message="排空已入队，原订单和退出义务仍由原执行处理",
        engine_command_id=identity,
      )
    except ValueError as exc:
      return _failed(exc)
