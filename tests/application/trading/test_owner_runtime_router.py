from dataclasses import FrozenInstanceError, is_dataclass
from types import MappingProxyType

import pytest
from quantx_application.trading.owner_runtime_router import (
  OWNER_ENVIRONMENT_CONFLICT,
  OWNER_EVENT_ID_MISSING,
  OWNER_EVENT_INVALID,
  OWNER_EVENT_KIND_INVALID,
  OWNER_HANDLER_DUPLICATE,
  OWNER_HANDLER_INVALID,
  OWNER_HANDLER_UNREGISTERED,
  OWNER_TARGET_CONFLICT,
  OWNER_TARGET_NOT_FOUND,
  OwnerRuntimeEvent,
  OwnerRuntimeEventKind,
  OwnerRuntimeRegistry,
  OwnerRuntimeRouter,
  OwnerRuntimeRouteResult,
  OwnerRuntimeRoutingError,
  OwnerRuntimeTarget,
)
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType


class RecordingHandler:
  def __init__(self, target=None, error=None) -> None:
    self.target = target
    self.error = error
    self.resolve_calls = []
    self.apply_calls = []

  async def resolve(self, execution_ref):
    self.resolve_calls.append(execution_ref)
    if self.error is not None:
      raise self.error
    return self.target

  async def apply(self, target, event):
    self.apply_calls.append((target, event))


def _event(owner_type=ExecutionOwnerType.STRATEGY_RUN, owner_id="owner-1", **overrides):
  values = {
    "event_id": "event-1",
    "event_kind": OwnerRuntimeEventKind.ORDER,
    "execution_ref": ExecutionOwnerRef(owner_type, owner_id),
    "environment": ExecutionEnvironment.PAPER,
    "payload": {"owner_type": "MANUAL_COMMAND", "owner_id": "not-the-owner"},
  }
  values.update(overrides)
  return OwnerRuntimeEvent(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "owner_type",
  (
    ExecutionOwnerType.STRATEGY_RUN,
    ExecutionOwnerType.EXIT_PLAN,
    ExecutionOwnerType.MANUAL_COMMAND,
  ),
)
async def test_registered_owner_types_route_to_their_registered_handler(owner_type):
  owner = ExecutionOwnerRef(owner_type, "owner-1")
  target = OwnerRuntimeTarget(owner, ExecutionEnvironment.LIVE)
  handler = RecordingHandler(target=target)
  registry = OwnerRuntimeRegistry()
  registry.register(owner_type, handler)
  event = _event(
    owner_type=owner_type,
    environment=ExecutionEnvironment.LIVE,
    event_kind="TRADE",
  )

  result = await OwnerRuntimeRouter(registry).route(event)

  assert result == OwnerRuntimeRouteResult(
    event_id="event-1",
    execution_ref=owner,
    environment=ExecutionEnvironment.LIVE,
    event_kind=OwnerRuntimeEventKind.TRADE,
    handler_owner_type=owner_type,
  )
  assert result.owner_type is owner_type
  assert handler.resolve_calls == [owner]
  assert handler.apply_calls == [(target, event)]


@pytest.mark.asyncio
async def test_unregistered_owner_fails_closed_without_apply():
  handler = RecordingHandler(
    target=OwnerRuntimeTarget(
      ExecutionOwnerRef.strategy_run("owner-1"), ExecutionEnvironment.PAPER
    )
  )

  with pytest.raises(OwnerRuntimeRoutingError) as captured:
    await OwnerRuntimeRouter(OwnerRuntimeRegistry()).route(_event())

  assert captured.value.code == OWNER_HANDLER_UNREGISTERED
  assert handler.apply_calls == []


def test_registry_rejects_invalid_and_duplicate_handlers():
  registry = OwnerRuntimeRegistry()
  owner_type = ExecutionOwnerType.STRATEGY_RUN

  with pytest.raises(OwnerRuntimeRoutingError) as captured:
    registry.register("ENTRY_PLAN", RecordingHandler())
  assert captured.value.code == OWNER_HANDLER_INVALID

  with pytest.raises(OwnerRuntimeRoutingError) as captured:
    registry.register(owner_type, object())
  assert captured.value.code == OWNER_HANDLER_INVALID

  handler = RecordingHandler()
  registry.register(owner_type, handler)
  with pytest.raises(OwnerRuntimeRoutingError) as captured:
    registry.register(owner_type, handler)
  assert captured.value.code == OWNER_HANDLER_DUPLICATE


@pytest.mark.parametrize(
  "owner_type",
  (
    ExecutionOwnerType.ENTRY_PLAN,
    ExecutionOwnerType.BOARD_ASSISTANT_EXECUTION,
  ),
)
def test_registry_rejects_future_owner_types(owner_type):
  registry = OwnerRuntimeRegistry()

  with pytest.raises(OwnerRuntimeRoutingError) as captured:
    registry.register(owner_type, RecordingHandler())

  assert captured.value.code == OWNER_HANDLER_INVALID
  assert registry.registered_owner_types == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("target", "environment", "code"),
  [
    (None, ExecutionEnvironment.PAPER, OWNER_TARGET_NOT_FOUND),
    (
      OwnerRuntimeTarget(
        ExecutionOwnerRef.strategy_run("other-owner"), ExecutionEnvironment.PAPER
      ),
      ExecutionEnvironment.PAPER,
      OWNER_TARGET_CONFLICT,
    ),
    (
      OwnerRuntimeTarget(
        ExecutionOwnerRef(ExecutionOwnerType.MANUAL_COMMAND, "owner-1"),
        ExecutionEnvironment.PAPER,
      ),
      ExecutionEnvironment.PAPER,
      OWNER_TARGET_CONFLICT,
    ),
    (
      OwnerRuntimeTarget(
        ExecutionOwnerRef.strategy_run("owner-1"), ExecutionEnvironment.LIVE
      ),
      ExecutionEnvironment.PAPER,
      OWNER_ENVIRONMENT_CONFLICT,
    ),
  ],
)
async def test_target_proof_precedes_apply(target, environment, code):
  handler = RecordingHandler(target=target)
  registry = OwnerRuntimeRegistry()
  registry.register(ExecutionOwnerType.STRATEGY_RUN, handler)

  with pytest.raises(OwnerRuntimeRoutingError) as captured:
    await OwnerRuntimeRouter(registry).route(_event(environment=environment))

  assert captured.value.code == code
  assert handler.apply_calls == []


@pytest.mark.asyncio
async def test_payload_owner_fields_never_override_event_owner():
  owner = ExecutionOwnerRef.strategy_run("owner-1")
  target = OwnerRuntimeTarget(owner, ExecutionEnvironment.PAPER)
  handler = RecordingHandler(target=target)
  registry = OwnerRuntimeRegistry()
  registry.register(ExecutionOwnerType.STRATEGY_RUN, handler)

  await OwnerRuntimeRouter(registry).route(_event(payload={
    "owner_type": "MANUAL_COMMAND",
    "owner_id": "manual-1",
  }))

  assert handler.resolve_calls == [owner]
  assert handler.apply_calls[0][1].execution_ref == owner


def test_events_and_payload_are_frozen_and_detached():
  payload = {"nested": {"items": [1]}}
  event = _event(payload=payload)
  payload["nested"]["items"].append(2)

  assert event.payload == MappingProxyType(
    {"nested": MappingProxyType({"items": (1,)})}
  )
  with pytest.raises(TypeError):
    event.payload["new"] = "value"
  with pytest.raises(TypeError):
    event.payload["nested"]["items"] += (2,)
  with pytest.raises(FrozenInstanceError):
    event.event_id = "other-event"

  target = OwnerRuntimeTarget(event.execution_ref, event.environment)
  with pytest.raises(FrozenInstanceError):
    target.environment = ExecutionEnvironment.LIVE
  assert is_dataclass(event)
  assert is_dataclass(target)


@pytest.mark.asyncio
async def test_handler_exception_propagates_without_fallback():
  failure = RuntimeError("handler failed")
  first = RecordingHandler(
    target=OwnerRuntimeTarget(
      ExecutionOwnerRef.strategy_run("owner-1"), ExecutionEnvironment.PAPER
    ),
    error=failure,
  )
  second = RecordingHandler(
    target=OwnerRuntimeTarget(
      ExecutionOwnerRef.strategy_run("owner-1"), ExecutionEnvironment.PAPER
    )
  )
  registry = OwnerRuntimeRegistry()
  registry.register(ExecutionOwnerType.STRATEGY_RUN, first)
  # A registry has one handler per owner type; this second handler cannot be
  # selected as a fallback for the same owner.
  with pytest.raises(OwnerRuntimeRoutingError) as captured:
    registry.register(ExecutionOwnerType.STRATEGY_RUN, second)
  assert captured.value.code == OWNER_HANDLER_DUPLICATE

  with pytest.raises(RuntimeError, match="^handler failed$"):
    await OwnerRuntimeRouter(registry).route(_event())
  assert first.apply_calls == []
  assert second.apply_calls == []


@pytest.mark.parametrize(
  ("kwargs", "code"),
  [
    ({"event_id": None}, OWNER_EVENT_ID_MISSING),
    ({"event_id": ""}, OWNER_EVENT_ID_MISSING),
    ({"event_kind": "UNKNOWN"}, OWNER_EVENT_KIND_INVALID),
    (
      {"execution_ref": {"owner_type": "STRATEGY_RUN", "owner_id": "owner-1"}},
      OWNER_EVENT_INVALID,
    ),
  ],
)
def test_event_validation_is_stable(kwargs, code):
  with pytest.raises(OwnerRuntimeRoutingError) as captured:
    _event(**kwargs)
  assert captured.value.code == code
  assert str(captured.value) == code
