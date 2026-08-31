"""Market supply health, independent of trading and downstream consumers."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

# One deadline covers all readiness dependencies; HTTP callers retain transport slack.
MARKET_GATEWAY_READINESS_TIMEOUT_SECONDS = 2.0
MARKET_GATEWAY_HTTP_TIMEOUT_SECONDS = MARKET_GATEWAY_READINESS_TIMEOUT_SECONDS + 1.0


class MarketHealthReason(StrEnum):
  STREAM_OFFLINE = "MARKET_STREAM_OFFLINE"
  STREAM_SYNCING = "MARKET_STREAM_SYNCING"
  STREAM_STALE = "MARKET_STREAM_STALE"
  SNAPSHOT_INCOMPLETE = "MARKET_SNAPSHOT_INCOMPLETE"
  REDIS_UNAVAILABLE = "MARKET_REDIS_UNAVAILABLE"
  CALENDAR_UNAVAILABLE = "MARKET_CALENDAR_UNAVAILABLE"


class MarketGatewayHealth(BaseModel):
  model_config = ConfigDict(
    extra="forbid",
    alias_generator=to_camel,
    populate_by_name=True,
    allow_inf_nan=False,
  )

  component: Literal["market-gateway"]
  protocol: Literal["quantx.market.v2"]
  status: Literal["ready", "not_ready"]
  reason_code: MarketHealthReason | None
  connected_devices: int = Field(ge=0, le=1)
  sequence: int = Field(ge=0)
  instrument_count: int = Field(ge=0)
  universe_count: int = Field(ge=0)
  stream_age_seconds: float | None = Field(ge=0)
  trading_session: bool | None

  @model_validator(mode="after")
  def consistent_readiness(self) -> "MarketGatewayHealth":
    if (self.status == "ready") != (self.reason_code is None):
      raise ValueError("health status and reason disagree")
    if self.status == "ready" and (
      self.connected_devices != 1
      or self.sequence < 3
      or self.universe_count <= 0
      or not 0.99 * self.universe_count <= self.instrument_count <= self.universe_count
      or self.stream_age_seconds is None
      or self.trading_session is None
    ):
      raise ValueError("ready supply requires an active, complete market stream")
    return self
