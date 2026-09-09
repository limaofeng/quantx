"""Dedicated Agent history connection messages, independent of trade sessions."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .collection_permit import CollectionPermit

HISTORY_SESSION_SUBPROTOCOL = "quantx.history.v1"


class HistoryCapabilities(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  capabilities: list[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]] = (
    Field(min_length=1, max_length=16)
  )


class HistoryAuthentication(HistoryCapabilities):
  device_id: str = Field(min_length=1, max_length=36)
  access_token: str = Field(min_length=1, max_length=2048, repr=False)


class HistoryHeartbeat(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  type: Literal["HEARTBEAT"] = "HEARTBEAT"
  xtdata_ready: bool = Field(strict=True)
  qos_reason: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")


class HistoryAuthResult(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  type: Literal["AUTH_RESULT"] = "AUTH_RESULT"
  accepted: Literal[True] = True
  session_id: UUID
  heartbeat_seconds: Literal[5] = 5


class HistoryHeartbeatAck(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  type: Literal["HEARTBEAT_ACK"] = "HEARTBEAT_ACK"
  session_id: UUID


class HistoryRequest(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  type: Literal["REQUEST"] = "REQUEST"
  request_id: UUID
  payload: dict[str, Any]


class HistoryGrant(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  type: Literal["GRANT"] = "GRANT"
  permit: CollectionPermit
  state: Literal["ISSUED", "STARTED"]
  unit_payload: dict[str, Any]
