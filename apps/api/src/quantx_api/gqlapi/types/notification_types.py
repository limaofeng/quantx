"""GraphQL types for session-bound APNs registration and opaque routing."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List

import strawberry

from quantx_api.notifications.service import (
  NotificationRouteSnapshot,
  PushRegistrationSnapshot,
)


@strawberry.enum(description="APNs 服务环境")
class PushEnvironment(Enum):
  SANDBOX = "SANDBOX"
  PRODUCTION = "PRODUCTION"


@strawberry.enum(description="可配置的普通通知类别")
class PushCategory(Enum):
  ACTION_REQUIRED = "ACTION_REQUIRED"
  ORDER_UPDATE = "ORDER_UPDATE"
  RISK_SAFETY = "RISK_SAFETY"
  AUTOMATION_ERROR = "AUTOMATION_ERROR"
  CONNECTION_DATA = "CONNECTION_DATA"


@strawberry.enum(description="解锁后允许导航的非敏感路由类型")
class NotificationRouteType(Enum):
  TODAY_ACTION = "today.action"
  TRADING_ORDERS = "trading.orders"
  TRADING_SAFETY = "trading.safety"
  QUANT_WORKSPACE = "quant.workspace"
  SYSTEM_STATUS = "system.status"


@strawberry.input(description="注册或轮换当前安装的 APNs Token")
class RegisterPushDeviceInput:
  device_token: str
  environment: PushEnvironment
  app_bundle_id: str
  app_version: str
  device_install_id: str


@strawberry.input(description="单个普通通知类别偏好")
class PushCategoryPreferenceInput:
  category: PushCategory
  enabled: bool


@strawberry.input(description="更新当前安装的普通通知类别偏好")
class UpdatePushPreferencesInput:
  environment: PushEnvironment
  app_bundle_id: str
  device_install_id: str
  preferences: List[PushCategoryPreferenceInput]


@strawberry.input(description="注销当前安装的 APNs Token")
class UnregisterPushDeviceInput:
  environment: PushEnvironment
  app_bundle_id: str
  device_install_id: str


@strawberry.type(description="普通通知类别偏好")
class PushCategoryPreference:
  category: PushCategory
  enabled: bool


@strawberry.type(description="当前会话绑定的 APNs 注册；不返回设备 Token")
class PushDeviceRegistration:
  id: strawberry.ID
  device_install_id: str
  app_bundle_id: str
  app_version: str
  environment: PushEnvironment
  registered_at: datetime
  updated_at: datetime
  preferences: List[PushCategoryPreference]

  @staticmethod
  def from_snapshot(value: PushRegistrationSnapshot) -> "PushDeviceRegistration":
    return PushDeviceRegistration(
      id=strawberry.ID(value.id),
      device_install_id=value.device_install_id,
      app_bundle_id=value.app_bundle_id,
      app_version=value.app_version,
      environment=PushEnvironment(value.apns_environment),
      registered_at=value.registered_at,
      updated_at=value.updated_at,
      preferences=[
        PushCategoryPreference(
          category=PushCategory(preference.category),
          enabled=preference.enabled,
        )
        for preference in value.preferences
      ],
    )


@strawberry.type(description="APNs 安装注销结果")
class UnregisterPushDeviceResult:
  success: bool


@strawberry.type(
  description="解锁后解析的非敏感路由；业务终态仍由目标 Query 重新读取"
)
class NotificationEventRoute:
  event_id: strawberry.ID
  category: PushCategory
  route_type: NotificationRouteType
  occurred_at: datetime
  expires_at: datetime
  expired: bool

  @staticmethod
  def from_snapshot(value: NotificationRouteSnapshot) -> "NotificationEventRoute":
    return NotificationEventRoute(
      event_id=strawberry.ID(value.event_id),
      category=PushCategory(value.category),
      route_type=NotificationRouteType(value.route_type),
      occurred_at=value.occurred_at,
      expires_at=value.expires_at,
      expired=value.expired,
    )


__all__ = [
  "NotificationEventRoute",
  "NotificationRouteType",
  "PushCategory",
  "PushCategoryPreference",
  "PushCategoryPreferenceInput",
  "PushDeviceRegistration",
  "PushEnvironment",
  "RegisterPushDeviceInput",
  "UnregisterPushDeviceInput",
  "UnregisterPushDeviceResult",
  "UpdatePushPreferencesInput",
]
