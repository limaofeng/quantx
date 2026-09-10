"""Safe display evidence for the independent Trainer; never execution authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TrainerDispatchStatus(BaseModel):
  model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
  state: Literal["FRESH", "STALE", "UNKNOWN"] = "UNKNOWN"
  observed_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
  status: Literal["IDLE", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "OWNERSHIP_LOST"] | None = None
  reason: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,79}$")


class TrainerRuntimeStatus(BaseModel):
  model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
  service: Literal["ALIVE", "OFFLINE", "STALE", "UNKNOWN"] = "UNKNOWN"
  phase: Literal["PREFLIGHT", "REGISTERING", "WORKER_LOOP", "STOPPING", "EXITING"] | None = None
  admission: Literal["OPEN", "DRAINING", "UNKNOWN"] = "UNKNOWN"
  resource_reason: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,79}$")
  training: TrainerDispatchStatus = Field(default_factory=TrainerDispatchStatus)
  preparation: TrainerDispatchStatus = Field(default_factory=TrainerDispatchStatus)
