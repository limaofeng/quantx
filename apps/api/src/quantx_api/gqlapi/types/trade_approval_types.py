"""GraphQL types for two-phase manual trade approval."""

from datetime import datetime
from typing import List, Optional

import strawberry

from ..trade_approval import TradeApprovalPreviewData


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

  @staticmethod
  def from_data(data: TradeApprovalPreviewData) -> "TradeApprovalPreview":
    return TradeApprovalPreview(**vars(data))


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
