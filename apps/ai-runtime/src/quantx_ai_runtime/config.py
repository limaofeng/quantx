"""Validated runtime configuration projected from shared QuantX settings."""

from __future__ import annotations

from dataclasses import dataclass

from quantx_infrastructure.config.settings import settings


@dataclass(frozen=True)
class AiRuntimeConfig:
  enabled: bool
  api_key: str
  model: str
  max_concurrent_runs: int
  max_turns: int
  max_tool_calls: int
  run_timeout_seconds: int
  lease_seconds: int
  tracing_enabled: bool

  @property
  def configured(self) -> bool:
    return self.enabled and bool(self.api_key.strip())


def load_config() -> AiRuntimeConfig:
  return AiRuntimeConfig(
    enabled=bool(settings.ai_assistant_enabled),
    api_key=settings.openai_api_key.strip(),
    model=settings.quantx_ai_model.strip() or "gpt-5.6",
    max_concurrent_runs=settings.ai_assistant_max_concurrent_runs,
    max_turns=settings.ai_assistant_max_turns,
    max_tool_calls=settings.ai_assistant_max_tool_calls,
    run_timeout_seconds=settings.ai_assistant_run_timeout_seconds,
    lease_seconds=settings.ai_assistant_lease_seconds,
    tracing_enabled=settings.ai_assistant_tracing_enabled,
  )
