"""Application lifecycle boundary for independent T-assistant executions."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecution,
  TAssistantExecutionEvent,
  TAssistantExecutionStatus,
)


class TAssistantExecutionTransitionPort(Protocol):
  async def save_transition_with_event(
    self,
    execution: TAssistantExecution,
    *,
    expected_state_version: int,
    event: TAssistantExecutionEvent,
  ) -> Any: ...


class TAssistantExecutionLifecycle:
  """Persist each lifecycle CAS together with its append-only audit event."""

  def __init__(self, port: TAssistantExecutionTransitionPort) -> None:
    self._port = port

  async def revise_universe(
    self,
    execution: TAssistantExecution,
    *,
    universe_revision: int,
    at,
    payload: Mapping[str, Any],
  ) -> TAssistantExecution:
    revised = execution.with_universe_revision(universe_revision)
    if revised is execution:
      return execution
    await self._port.save_transition_with_event(
      revised,
      expected_state_version=execution.state_version,
      event=TAssistantExecutionEvent(
        execution_id=execution.execution_id,
        event_key=f"universe-revision:{universe_revision}:{revised.state_version}",
        event_type="EXECUTION_UNIVERSE_REVISED",
        occurred_at=at,
        payload=dict(payload),
      ),
    )
    return revised

  async def activate_ready(
    self,
    execution: TAssistantExecution,
    *,
    at,
    payload: Mapping[str, Any],
  ) -> TAssistantExecution:
    activated = execution.activate_ready(at=at)
    await self._port.save_transition_with_event(
      activated,
      expected_state_version=execution.state_version,
      event=TAssistantExecutionEvent(
        execution_id=execution.execution_id,
        event_key=f"execution-ready:{activated.state_version}",
        event_type="EXECUTION_ENTRY_READY",
        occurred_at=at,
        payload=dict(payload),
      ),
    )
    return activated

  async def transition(
    self,
    execution: TAssistantExecution,
    *,
    target: TAssistantExecutionStatus,
    at,
    has_unsettled_buy_work: bool,
    event_type: str,
    payload: Mapping[str, Any],
  ) -> TAssistantExecution:
    transitioned = execution.transition(
      target,
      at=at,
      has_unsettled_buy_work=has_unsettled_buy_work,
    )
    await self._port.save_transition_with_event(
      transitioned,
      expected_state_version=execution.state_version,
      event=TAssistantExecutionEvent(
        execution_id=execution.execution_id,
        event_key=(
          f"execution-{target.value.lower()}:{transitioned.state_version}"
        ),
        event_type=event_type,
        occurred_at=at,
        payload=dict(payload),
      ),
    )
    return transitioned


__all__ = [
  "TAssistantExecutionLifecycle",
  "TAssistantExecutionTransitionPort",
]
