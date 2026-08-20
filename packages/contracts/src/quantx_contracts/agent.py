"""Wire contracts for the outbound-only QMT agent connection."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Type
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = "1.1"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"1.0", PROTOCOL_VERSION})
HISTORICAL_TICK_ORDINAL_FIELD = "tick_ordinal"
HISTORICAL_TICK_SOURCE_TIME_FIELD = "source_time_ms"
HISTORICAL_TICK_ORDINALS_PER_MILLISECOND = 1000


def utcnow() -> datetime:
  return datetime.now(timezone.utc)


class AgentMessageType(str, Enum):
  AUTH = "auth"
  AUTH_RESULT = "auth_result"
  HEARTBEAT = "heartbeat"
  HEARTBEAT_ACK = "heartbeat_ack"
  COMMAND = "command"
  COMMAND_ACK = "command_ack"
  CANCEL_COMMAND = "cancel_command"
  ORDER_REPORT = "order_report"
  EXECUTION_REPORT = "execution_report"
  DELTA_REPORT = "delta_report"
  REPORT_ACK = "report_ack"
  MARKET_DATA_REQUEST = "market_data_request"
  MARKET_DATA_CHUNK = "market_data_chunk"
  MARKET_SUBSCRIBE = "market_subscribe"
  MARKET_UNSUBSCRIBE = "market_unsubscribe"
  MARKET_RESET = "market_reset"
  MARKET_EVENT = "market_event"


class AgentEnvelope(BaseModel):
  model_config = ConfigDict(extra="forbid")

  protocol_version: str = PROTOCOL_VERSION
  message_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
  message_type: AgentMessageType
  sent_at: datetime = Field(default_factory=utcnow)
  payload: Dict[str, Any] = Field(default_factory=dict)

  @field_validator("protocol_version")
  @classmethod
  def require_supported_protocol(cls, value: str) -> str:
    if value not in SUPPORTED_PROTOCOL_VERSIONS:
      raise ValueError(f"unsupported protocol version: {value}")
    return value

  @field_validator("sent_at")
  @classmethod
  def require_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
      raise ValueError("sent_at must be timezone-aware")
    return value

  def validate_payload(self) -> BaseModel | Dict[str, Any]:
    """Validate typed trading reports while preserving extensible control data."""
    payload_type: Optional[Type[BaseModel]] = {
      AgentMessageType.ORDER_REPORT: OrderReportPayload,
      AgentMessageType.EXECUTION_REPORT: ExecutionReportPayload,
      AgentMessageType.DELTA_REPORT: AccountSnapshotPayload,
    }.get(self.message_type)
    return (
      payload_type.model_validate(self.payload)
      if payload_type is not None
      else self.payload
    )


class TradeCommandPayload(BaseModel):
  model_config = ConfigDict(extra="forbid")

  command_kind: Literal["PLACE_ORDER"] = "PLACE_ORDER"
  client_order_id: str = Field(min_length=1)
  instance_id: str = Field(min_length=1)
  account_id: str = Field(min_length=1)
  execution_mode: Literal["paper", "live"] = "paper"
  instrument_code: str = Field(min_length=1)
  side: str
  order_type: str = "LIMIT"
  limit_price: str
  volume: int = Field(gt=0)
  bucket: str
  risk_decision_id: str
  trace_id: str
  expires_at: datetime
  reason_tags: List[str] = Field(default_factory=list)
  substitution_plan: Optional[Dict[str, Any]] = None
  strategy_name: str = ""
  strategy_run_id: str = ""
  strategy_order_id: str = ""
  intent_id: str = ""
  batch_id: str = ""
  t_trade_role: Literal["", "ENTRY", "EXIT"] = ""
  policy_version: int = Field(default=0, ge=0)
  request_metadata: Dict[str, Any] = Field(default_factory=dict)
  order_remark: str = ""

  @field_validator("expires_at")
  @classmethod
  def require_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None:
      raise ValueError("expires_at must be timezone-aware")
    return value


class CancelCommandPayload(BaseModel):
  model_config = ConfigDict(extra="forbid")

  command_kind: Literal["CANCEL_ORDER"] = "CANCEL_ORDER"
  client_order_id: str = Field(min_length=1)
  account_id: str = Field(min_length=1)
  execution_mode: Literal["paper", "live"] = "paper"
  broker_order_id: str = Field(min_length=1)
  trace_id: str = Field(min_length=1)
  expires_at: datetime

  @field_validator("expires_at")
  @classmethod
  def require_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None:
      raise ValueError("expires_at must be timezone-aware")
    return value


class CommandAckPayload(BaseModel):
  model_config = ConfigDict(extra="forbid")

  command_message_id: str = Field(min_length=1)
  client_order_id: str = Field(min_length=1)
  accepted: bool
  reason: str = ""


class HeartbeatPayload(BaseModel):
  model_config = ConfigDict(extra="forbid")

  device_id: str
  agent_version: str
  protocol_version: str = PROTOCOL_VERSION
  capabilities: List[str] = Field(default_factory=list)
  status: str = "READY"
  journal_integrity: str = ""
  journal_size_bytes: int = Field(default=0, ge=0)
  journal_pending_reports: int = Field(default=0, ge=0)
  journal_processing_commands: int = Field(default=0, ge=0)
  market_stream_status: str = "OFFLINE"
  market_stream_sequence: int = Field(default=0, ge=0)
  market_stream_queue_depth: int = Field(default=0, ge=0)
  market_stream_resyncs: int = Field(default=0, ge=0)
  market_stream_ack_latency_ms: float = Field(default=0.0, ge=0)


class ReportAckPayload(BaseModel):
  model_config = ConfigDict(extra="forbid")

  report_message_id: str
  accepted: bool
  duplicate: bool = False
  reason: str = ""


class BrokerOrderPayload(BaseModel):
  """Typed broker order identity with vendor fields retained for audit."""

  model_config = ConfigDict(extra="allow")

  account_id: str = Field(min_length=1)
  order_id: Optional[Any] = None
  broker_order_id: Optional[Any] = None
  stock_code: str = ""
  order_status: Optional[Any] = None
  status: Optional[Any] = None
  effective_order_status: Optional[str] = None
  can_cancel: Optional[bool] = None
  session_expired: bool = False
  effective_status_reason: str = ""
  order_session_date: Optional[str] = None
  traded_volume: int = Field(default=0, ge=0)
  traded_price: float = Field(default=0.0, ge=0)

  @model_validator(mode="after")
  def require_broker_order_id(self):
    if self.order_id is None and self.broker_order_id is None:
      raise ValueError("order report requires broker order id")
    return self


class BrokerExecutionPayload(BaseModel):
  """Typed broker execution identity with vendor fields retained for audit."""

  model_config = ConfigDict(extra="allow")

  account_id: str = Field(min_length=1)
  order_id: Optional[Any] = None
  broker_order_id: Optional[Any] = None
  execution_id: Optional[str] = None
  traded_id: Optional[str] = None
  stock_code: str = ""
  traded_time: Optional[Any] = None
  traded_price: float = Field(ge=0)
  traded_volume: int = Field(gt=0)
  traded_amount: float = Field(default=0.0, ge=0)

  @model_validator(mode="after")
  def require_execution_identity(self):
    if self.order_id is None and self.broker_order_id is None:
      raise ValueError("execution report requires broker order id")
    if not self.execution_id and not self.traded_id:
      raise ValueError("execution report requires execution id")
    return self


class OrderReportPayload(BaseModel):
  model_config = ConfigDict(extra="forbid")

  account_id: Optional[str] = None
  client_order_id: Optional[str] = None
  source_sequence: int = Field(default=0, ge=0)
  source_event_at: datetime = Field(default_factory=utcnow)
  order: BrokerOrderPayload

  @field_validator("source_event_at")
  @classmethod
  def require_source_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
      raise ValueError("source_event_at must be timezone-aware")
    return value


class ExecutionReportPayload(BaseModel):
  model_config = ConfigDict(extra="forbid")

  account_id: Optional[str] = None
  client_order_id: Optional[str] = None
  order_status: str = "PARTIAL_FILLED"
  source_sequence: int = Field(default=0, ge=0)
  source_event_at: datetime = Field(default_factory=utcnow)
  execution: BrokerExecutionPayload

  @field_validator("source_event_at")
  @classmethod
  def require_source_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
      raise ValueError("source_event_at must be timezone-aware")
    return value


class AccountSnapshotPayload(BaseModel):
  model_config = ConfigDict(extra="allow")

  account_id: Optional[str] = None
  source_sequence: int = Field(default=0, ge=0)
  source_event_at: datetime = Field(default_factory=utcnow)
  snapshot_id: str = ""
  snapshot_hash: str = ""
  is_complete: bool
  accounts: List[Dict[str, Any]] = Field(default_factory=list)
  positions_by_account: Dict[str, List[Dict[str, Any]]] = Field(
    default_factory=dict
  )
  positions: List[Dict[str, Any]] = Field(default_factory=list)
  position_deltas: List[Dict[str, Any]] = Field(default_factory=list)
  orders: List[Dict[str, Any]] = Field(default_factory=list)
  trades: List[Dict[str, Any]] = Field(default_factory=list)
  section_completeness_by_account: Dict[str, Dict[str, bool]] = Field(
    default_factory=dict
  )
  unavailable_accounts: List[str] = Field(default_factory=list)
  order_errors: List[Dict[str, Any]] = Field(default_factory=list)
  cancel_errors: List[Dict[str, Any]] = Field(default_factory=list)

  @field_validator("source_event_at")
  @classmethod
  def require_source_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
      raise ValueError("source_event_at must be timezone-aware")
    return value

  @model_validator(mode="after")
  def require_complete_snapshot_identity(self):
    if self.is_complete and (
      not self.snapshot_id
      or len(self.snapshot_hash) != 64
      or any(character not in "0123456789abcdefABCDEF" for character in self.snapshot_hash)
    ):
      raise ValueError("complete snapshot requires id and hash")
    if self.is_complete:
      if self.unavailable_accounts:
        raise ValueError("complete snapshot cannot contain unavailable accounts")
      covered_accounts = {
        str(self.account_id or "").strip(),
        *(
          str(item.get("account_id") or "").strip()
          for item in self.accounts
        ),
        *(str(account_id).strip() for account_id in self.positions_by_account),
        *(
          str(item.get("account_id") or "").strip()
          for item in (*self.orders, *self.trades)
        ),
        *(
          str(account_id).strip()
          for account_id in self.section_completeness_by_account
        ),
      }
      covered_accounts.discard("")
      if not covered_accounts:
        raise ValueError("complete snapshot requires a covered account")
      account_record_ids = {
        str(item.get("account_id") or "").strip()
        for item in self.accounts
        if str(item.get("account_id") or "").strip()
      }
      position_account_ids = {
        str(account_id).strip()
        for account_id in self.positions_by_account
        if str(account_id).strip()
      }
      section_account_ids = {
        str(account_id).strip()
        for account_id in self.section_completeness_by_account
        if str(account_id).strip()
      }
      if (
        account_record_ids != covered_accounts
        or position_account_ids != covered_accounts
        or section_account_ids != covered_accounts
      ):
        raise ValueError("complete snapshot requires every account section")
      required_sections = ("account", "positions", "orders", "trades")
      if any(
        not all(
          self.section_completeness_by_account[account_id].get(section)
          is True
          for section in required_sections
        )
        for account_id in covered_accounts
      ):
        raise ValueError("complete snapshot contains an incomplete account section")
    return self


class ReconciliationResultPayload(BaseModel):
  model_config = ConfigDict(extra="forbid")

  account_id: str = Field(min_length=1)
  snapshot_id: str = Field(min_length=1)
  snapshot_hash: str = Field(min_length=16)
  reconciled_at: datetime
  ready: bool
  discrepancies: List[Dict[str, Any]] = Field(default_factory=list)

  @field_validator("reconciled_at")
  @classmethod
  def require_reconciled_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
      raise ValueError("reconciled_at must be timezone-aware")
    return value
