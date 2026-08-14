"""Authenticated user identity passed to API resolvers."""

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Optional, Tuple

from .errors import forbidden


@dataclass(frozen=True)
class Principal:
  user_id: str
  username: str
  display_name: str
  device_session_id: str
  access_token_expires_at: datetime
  permissions: FrozenSet[str]
  authorized_account_ids: Tuple[str, ...]
  active_account_id: Optional[str] = None

  def require_permission(self, permission: str) -> None:
    if permission not in self.permissions:
      raise forbidden(f"缺少权限：{permission}")

  def require_account(self, requested_account_id: Optional[str] = None) -> str:
    normalized = (requested_account_id or "").strip()
    if normalized:
      if normalized not in self.authorized_account_ids:
        raise forbidden("无权访问该资金账户")
      return normalized
    if self.active_account_id is not None:
      if self.active_account_id not in self.authorized_account_ids:
        raise forbidden("当前设备会话的主账户授权已失效")
      return self.active_account_id
    if not self.authorized_account_ids:
      raise forbidden("当前用户未授权任何资金账户")
    return self.authorized_account_ids[0]
