"""GraphQL contract for non-secret AI Runtime settings."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

import strawberry


@strawberry.enum
class AiRuntimeSettingsSource(Enum):
  ENVIRONMENT = "ENVIRONMENT"
  DATABASE_OVERRIDE = "DATABASE_OVERRIDE"


@strawberry.enum
class AiRuntimeStatus(Enum):
  READY = "READY"
  DISABLED = "DISABLED"
  UNCONFIGURED = "UNCONFIGURED"
  UNAVAILABLE = "UNAVAILABLE"
  OFFLINE = "OFFLINE"


@strawberry.enum
class AiRuntimeApplyState(Enum):
  APPLIED = "APPLIED"
  PENDING = "PENDING"
  OFFLINE = "OFFLINE"


@strawberry.type
class AiRuntimeSettings:
  version: int
  source: AiRuntimeSettingsSource
  enabled: bool
  api_key_configured: bool
  model: str
  max_concurrent_runs: int
  max_turns: int
  max_tool_calls: int
  run_timeout_seconds: int
  tracing_enabled: bool
  lease_seconds: int
  runtime_status: AiRuntimeStatus
  applied_version: Optional[int]
  apply_state: AiRuntimeApplyState
  updated_at: Optional[datetime]


@strawberry.input
class UpdateAiRuntimeSettingsInput:
  expected_version: int
  enabled: bool
  model: str
  max_concurrent_runs: int
  max_turns: int
  max_tool_calls: int
  run_timeout_seconds: int
