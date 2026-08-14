"""Authenticated GraphQL boundary for iOS push device management."""

from __future__ import annotations

from typing import Optional

import strawberry
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from sqlalchemy.exc import SQLAlchemyError

from quantx_api.auth.errors import AuthError
from quantx_api.auth.service import AuthService
from quantx_api.auth.tokens import require_signing_key
from quantx_api.gqlapi.security import authorized_account_id, principal_from_context
from quantx_api.gqlapi.types.notification_types import (
  NotificationEventRoute,
  PushDeviceRegistration,
  RegisterPushDeviceInput,
  UnregisterPushDeviceInput,
  UnregisterPushDeviceResult,
  UpdatePushPreferencesInput,
)
from quantx_api.notifications.service import PushNotificationService

_PERMISSION = "notification:manage"


async def _lock_push_write(db, info):
  principal = principal_from_context(info.context)
  request_account_id = authorized_account_id(info)
  current = await AuthService(db).lock_and_validate_session(
    principal,
    account_id=request_account_id,
  )
  # The top-level security extension preserves existing Web administration via
  # mutation:write. Native sessions never receive that broad permission and
  # must hold the dedicated notification scope.
  legacy_web_compatible = (
    current.active_account_id is None and "mutation:write" in current.permissions
  )
  if not legacy_web_compatible:
    current.require_permission(_PERMISSION)
  return current, _current_account_id(current, request_account_id)


async def _lock_push_read(db, info):
  principal = principal_from_context(info.context)
  request_account_id = authorized_account_id(info)
  current = await AuthService(db).lock_and_validate_session(
    principal,
    required_permission=_PERMISSION,
    account_id=request_account_id,
  )
  return current, _current_account_id(current, request_account_id)


def _current_account_id(current, request_account_id: str) -> str:
  if current.active_account_id is None:
    return current.require_account(request_account_id)
  if current.active_account_id != request_account_id:
    raise AuthError(
      "ACCOUNT_SCOPE_MISMATCH",
      "当前设备会话的主账户已变化",
      status_code=403,
    )
  return current.active_account_id


def _service(db) -> PushNotificationService:
  return PushNotificationService(db, signing_key=require_signing_key(settings))


def _persistence_error() -> AuthError:
  return AuthError(
    "PUSH_PERSISTENCE_FAILED",
    "推送设备状态暂时无法保存",
    status_code=503,
    retryable=True,
  )


@strawberry.type(description="iOS 通知路由查询")
class NotificationQuery:
  @strawberry.field(description="按随机事件标识解析当前会话可访问的非敏感路由")
  async def notification_event_route(
    self,
    info: strawberry.types.Info,
    event_id: strawberry.ID,
  ) -> Optional[NotificationEventRoute]:
    async with AsyncSessionLocal() as db:
      principal, account_id = await _lock_push_read(db, info)
      value = await _service(db).resolve_event(
        event_id=str(event_id),
        user_id=principal.user_id,
        device_session_id=principal.device_session_id,
        account_id=account_id,
      )
    return NotificationEventRoute.from_snapshot(value) if value else None


@strawberry.type(description="当前 iOS 安装的普通 APNs 通知管理")
class NotificationMutation:
  @strawberry.mutation(description="注册或幂等轮换当前安装的 APNs Token")
  async def register_push_device(
    self,
    info: strawberry.types.Info,
    input: RegisterPushDeviceInput,
  ) -> PushDeviceRegistration:
    async with AsyncSessionLocal() as db:
      try:
        principal, account_id = await _lock_push_write(db, info)
        value = await _service(db).register(
          user_id=principal.user_id,
          device_session_id=principal.device_session_id,
          account_id=account_id,
          device_install_id=input.device_install_id,
          app_bundle_id=input.app_bundle_id,
          app_version=input.app_version,
          apns_environment=input.environment.value,
          device_token=input.device_token,
        )
        await db.commit()
      except AuthError:
        await db.rollback()
        raise
      except SQLAlchemyError:
        await db.rollback()
        raise _persistence_error() from None
    return PushDeviceRegistration.from_snapshot(value)

  @strawberry.mutation(description="更新当前安装的普通通知类别偏好")
  async def update_push_preferences(
    self,
    info: strawberry.types.Info,
    input: UpdatePushPreferencesInput,
  ) -> PushDeviceRegistration:
    async with AsyncSessionLocal() as db:
      try:
        principal, account_id = await _lock_push_write(db, info)
        value = await _service(db).update_preferences(
          user_id=principal.user_id,
          device_session_id=principal.device_session_id,
          account_id=account_id,
          device_install_id=input.device_install_id,
          app_bundle_id=input.app_bundle_id,
          apns_environment=input.environment.value,
          preferences=(
            (preference.category.value, preference.enabled)
            for preference in input.preferences
          ),
        )
        await db.commit()
      except AuthError:
        await db.rollback()
        raise
      except SQLAlchemyError:
        await db.rollback()
        raise _persistence_error() from None
    return PushDeviceRegistration.from_snapshot(value)

  @strawberry.mutation(description="注销当前安装并丢弃尚未发送的推送意图")
  async def unregister_push_device(
    self,
    info: strawberry.types.Info,
    input: UnregisterPushDeviceInput,
  ) -> UnregisterPushDeviceResult:
    async with AsyncSessionLocal() as db:
      try:
        principal, account_id = await _lock_push_write(db, info)
        success = await _service(db).unregister(
          user_id=principal.user_id,
          device_session_id=principal.device_session_id,
          account_id=account_id,
          device_install_id=input.device_install_id,
          app_bundle_id=input.app_bundle_id,
          apns_environment=input.environment.value,
        )
        await db.commit()
      except AuthError:
        await db.rollback()
        raise
      except SQLAlchemyError:
        await db.rollback()
        raise _persistence_error() from None
    return UnregisterPushDeviceResult(success=success)


__all__ = ["NotificationMutation", "NotificationQuery"]
