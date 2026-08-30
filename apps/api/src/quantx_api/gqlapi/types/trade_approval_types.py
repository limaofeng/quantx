"""GraphQL types for two-phase manual trade approval."""

from datetime import datetime
from typing import List, Optional

import strawberry
from strawberry.scalars import JSON

from ..trade_approval import (
  TradeApprovalPreviewData,
  TTradeAutoExitAuthorizationPreviewData,
)


@strawberry.type(description="做 T 买入确认同时覆盖的精确自动退出范围")
class TTradeAutoExitAuthorizationPreview:
  plan_id: str
  config_version: int
  max_protected_volume: int
  rules: JSON
  t1_policy: str
  execution_policy: JSON
  execution_semantics: str
  authorization_expires_at: datetime

  @staticmethod
  def from_data(
    data: TTradeAutoExitAuthorizationPreviewData,
  ) -> "TTradeAutoExitAuthorizationPreview":
    return TTradeAutoExitAuthorizationPreview(**vars(data))


@strawberry.type(description="服务器生成的单笔交易确认预览")
class TradeApprovalPreview:
  challenge_id: str
  confirmation_token: str
  action: str
  account_id: str
  run_id: str
  intent_id: str
  instrument_code: str
  side: str
  bucket: str
  reason: str
  target_volume: Optional[int]
  reference_price: Optional[float]
  estimated_amount: Optional[float]
  signal_expires_at: Optional[datetime]
  challenge_expires_at: datetime
  warnings: List[str]
  t_trade_auto_exit_authorization: Optional[
    TTradeAutoExitAuthorizationPreview
  ] = None

  @staticmethod
  def from_data(data: TradeApprovalPreviewData) -> "TradeApprovalPreview":
    values = vars(data).copy()
    authorization = values.pop("t_trade_auto_exit_authorization", None)
    return TradeApprovalPreview(
      **values,
      t_trade_auto_exit_authorization=(
        TTradeAutoExitAuthorizationPreview.from_data(authorization)
        if authorization is not None
        else None
      ),
    )


@strawberry.type(description="交易确认预览结果")
class TradeApprovalPreviewResult:
  success: bool
  code: str
  message: str
  preview: Optional[TradeApprovalPreview] = None


@strawberry.type(description="交易确认提交结果；成功仅表示已进入统一执行链路")
class TradeApprovalConfirmationResult:
  success: bool
  code: str
  message: str
  challenge_id: Optional[str] = None
