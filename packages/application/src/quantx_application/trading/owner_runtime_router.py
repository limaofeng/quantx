"""Fail-closed routing for owner-scoped execution runtime events.

This module intentionally contains only application-layer value objects and
ports.  A concrete handler owns the lookup and application of its own runtime
state; the router never derives ownership from an event payload or metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from inspect import iscoroutinefunction
from types import MappingProxyType
from typing import Protocol, TypeVar, runtime_checkable

from quantx_contracts import (
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
)

OWNER_EVENT_INVALID = "OWNER_EVENT_INVALID"
OWNER_EVENT_ID_MISSING = "OWNER_EVENT_ID_MISSING"
OWNER_EVENT_KIND_INVALID = "OWNER_EVENT_KIND_INVALID"
OWNER_HANDLER_INVALID = "OWNER_HANDLER_INVALID"
OWNER_HANDLER_DUPLICATE = "OWNER_HANDLER_DUPLICATE"
OWNER_HANDLER_UNREGISTERED = "OWNER_HANDLER_UNREGISTERED"
OWNER_TARGET_NOT_FOUND = "OWNER_TARGET_NOT_FOUND"
OWNER_TARGET_CONFLICT = "OWNER_TARGET_CONFLICT"
OWNER_ENVIRONMENT_CONFLICT = "OWNER_ENVIRONMENT_CONFLICT"


class OwnerRuntimeRoutingError(Exception):
  """Stable, machine-readable failure from owner runtime routing."""

  def __init__(self, code: str) -> None:
    if not isinstance(code, str) or not code:
      raise ValueError("routing error code must be a non-empty string")
    self.code = code
    super().__init__(code)

  def __str__(self) -> str:
    return self.code


class OwnerRuntimeEventKind(StrEnum):
  """The closed set of events accepted by :class:`OwnerRuntimeRouter`."""

  ORDER = "ORDER"
  TRADE = "TRADE"
  RECONCILE = "RECONCILE"


_PayloadValue = TypeVar("_PayloadValue")


def _freeze_payload(value: _PayloadValue, active: set[int]) -> object:
  """Copy and recursively freeze common mutable payload containers.

  Mapping proxies protect the event's top-level facts, while recursively
  freezing nested mappings and containers prevents a caller from retaining a
  mutable alias through a nested value.  Cyclic payloads are rejected because
  they cannot be represented as a finite immutable fact snapshot.
  """

  value_id = id(value)
  if isinstance(value, Mapping):
    if value_id in active:
      raise ValueError("cyclic payload")
    active.add(value_id)
    try:
      frozen = {
        deepcopy(key): _freeze_payload(item, active)
        for key, item in value.items()
      }
    finally:
      active.remove(value_id)
    return MappingProxyType(frozen)

  if isinstance(value, list):
    if value_id in active:
      raise ValueError("cyclic payload")
    active.add(value_id)
    try:
      return tuple(_freeze_payload(item, active) for item in value)
    finally:
      active.remove(value_id)

  if isinstance(value, tuple):
    if value_id in active:
      raise ValueError("cyclic payload")
    active.add(value_id)
    try:
      return tuple(_freeze_payload(item, active) for item in value)
    finally:
      active.remove(value_id)

  if isinstance(value, (set, frozenset)):
    if value_id in active:
      raise ValueError("cyclic payload")
    active.add(value_id)
    try:
      frozen_items = tuple(_freeze_payload(item, active) for item in value)
    finally:
      active.remove(value_id)
    try:
      return frozenset(frozen_items)
    except TypeError:
      # A set may contain a value that becomes an immutable mapping proxy,
      # which is still intentionally represented without mutability.
      return frozen_items

  if isinstance(value, bytearray):
    return bytes(value)
  if isinstance(value, memoryview):
    return bytes(value)

  # Detach arbitrary fact values from caller-owned objects.  Values that
  # cannot be copied are not safe to retain in an immutable event snapshot.
  return deepcopy(value)


@dataclass(frozen=True, slots=True)
class OwnerRuntimeEvent:
  """An immutable owner-scoped event supplied to a runtime handler."""

  event_id: str
  event_kind: OwnerRuntimeEventKind | str
  execution_ref: ExecutionOwnerRef
  environment: ExecutionEnvironment
  payload: Mapping[str, object] = field(default_factory=dict)

  def __post_init__(self) -> None:
    if self.event_id is None:
      raise OwnerRuntimeRoutingError(OWNER_EVENT_ID_MISSING)
    if not isinstance(self.event_id, str):
      raise OwnerRuntimeRoutingError(OWNER_EVENT_INVALID)
    if not self.event_id.strip():
      raise OwnerRuntimeRoutingError(OWNER_EVENT_ID_MISSING)
    if self.event_id != self.event_id.strip():
      raise OwnerRuntimeRoutingError(OWNER_EVENT_INVALID)
    if any(ord(character) <= 31 or ord(character) == 127 for character in self.event_id):
      raise OwnerRuntimeRoutingError(OWNER_EVENT_INVALID)

    try:
      event_kind = OwnerRuntimeEventKind(self.event_kind)
    except (TypeError, ValueError) as exc:
      raise OwnerRuntimeRoutingError(OWNER_EVENT_KIND_INVALID) from exc

    if not isinstance(self.execution_ref, ExecutionOwnerRef):
      raise OwnerRuntimeRoutingError(OWNER_EVENT_INVALID)
    if not isinstance(self.environment, ExecutionEnvironment):
      raise OwnerRuntimeRoutingError(OWNER_EVENT_INVALID)
    if not isinstance(self.payload, Mapping):
      raise OwnerRuntimeRoutingError(OWNER_EVENT_INVALID)

    try:
      payload = _freeze_payload(self.payload, set())
    except Exception as exc:
      raise OwnerRuntimeRoutingError(OWNER_EVENT_INVALID) from exc

    object.__setattr__(self, "event_kind", event_kind)
    object.__setattr__(self, "payload", payload)


@dataclass(frozen=True, slots=True)
class OwnerRuntimeTarget:
  """The durable target proven by an owner handler lookup."""

  execution_ref: ExecutionOwnerRef
  environment: ExecutionEnvironment

  def __post_init__(self) -> None:
    if not isinstance(self.execution_ref, ExecutionOwnerRef):
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    if not isinstance(self.environment, ExecutionEnvironment):
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)


@runtime_checkable
class OwnerRuntimeHandler(Protocol):
  """Async port implemented by one concrete owner runtime."""

  async def resolve(
    self,
    execution_ref: ExecutionOwnerRef,
  ) -> OwnerRuntimeTarget | None:
    ...

  async def apply(
    self,
    target: OwnerRuntimeTarget,
    event: OwnerRuntimeEvent,
  ) -> None:
    ...


def _is_handler(value: object) -> bool:
  """Check the runtime shape required by the async handler protocol."""

  try:
    resolve = getattr(value, "resolve")
    apply = getattr(value, "apply")
  except Exception:
    return False
  return (
    callable(resolve)
    and callable(apply)
    and iscoroutinefunction(resolve)
    and iscoroutinefunction(apply)
  )


class OwnerRuntimeRegistry:
  """Explicit mapping from each registered owner type to one handler."""

  def __init__(self) -> None:
    self._handlers: dict[ExecutionOwnerType, OwnerRuntimeHandler] = {}

  def register(
    self,
    owner_type: ExecutionOwnerType,
    handler: OwnerRuntimeHandler,
  ) -> None:
    if not isinstance(owner_type, ExecutionOwnerType):
      raise OwnerRuntimeRoutingError(OWNER_HANDLER_INVALID)
    if not _is_handler(handler):
      raise OwnerRuntimeRoutingError(OWNER_HANDLER_INVALID)
    if owner_type in self._handlers:
      raise OwnerRuntimeRoutingError(OWNER_HANDLER_DUPLICATE)
    self._handlers[owner_type] = handler

  @property
  def registered_owner_types(self) -> tuple[ExecutionOwnerType, ...]:
    """Return registered keys without exposing mutable registry state."""

    return tuple(self._handlers)

  def handler_for(self, owner_type: ExecutionOwnerType) -> OwnerRuntimeHandler:
    if not isinstance(owner_type, ExecutionOwnerType):
      raise OwnerRuntimeRoutingError(OWNER_HANDLER_INVALID)
    try:
      return self._handlers[owner_type]
    except KeyError as exc:
      raise OwnerRuntimeRoutingError(OWNER_HANDLER_UNREGISTERED) from exc


@dataclass(frozen=True, slots=True)
class OwnerRuntimeRouteResult:
  """Immutable acknowledgement that one event was applied to one owner."""

  event_id: str
  execution_ref: ExecutionOwnerRef
  environment: ExecutionEnvironment
  event_kind: OwnerRuntimeEventKind
  handler_owner_type: ExecutionOwnerType

  @property
  def owner_type(self) -> ExecutionOwnerType:
    """Return the selected handler owner type."""

    return self.handler_owner_type


class OwnerRuntimeRouter:
  """Resolve and apply events only after exact owner/environment proof."""

  def __init__(self, registry: OwnerRuntimeRegistry) -> None:
    if not isinstance(registry, OwnerRuntimeRegistry):
      raise OwnerRuntimeRoutingError(OWNER_HANDLER_INVALID)
    self._registry = registry

  async def route(self, event: OwnerRuntimeEvent) -> OwnerRuntimeRouteResult:
    if type(event) is not OwnerRuntimeEvent:
      raise OwnerRuntimeRoutingError(OWNER_EVENT_INVALID)

    owner_type = event.execution_ref.owner_type
    handler = self._registry.handler_for(owner_type)
    target = await handler.resolve(event.execution_ref)
    if target is None:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_NOT_FOUND)
    if type(target) is not OwnerRuntimeTarget:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)

    try:
      event.execution_ref.require_matches(
        target.execution_ref.owner_type,
        target.execution_ref.owner_id,
      )
    except (TypeError, ValueError) as exc:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT) from exc

    if target.environment is not event.environment:
      raise OwnerRuntimeRoutingError(OWNER_ENVIRONMENT_CONFLICT)

    await handler.apply(target, event)
    return OwnerRuntimeRouteResult(
      event_id=event.event_id,
      execution_ref=event.execution_ref,
      environment=event.environment,
      event_kind=event.event_kind,
      handler_owner_type=owner_type,
    )


__all__ = [
  "OWNER_ENVIRONMENT_CONFLICT",
  "OWNER_EVENT_ID_MISSING",
  "OWNER_EVENT_INVALID",
  "OWNER_EVENT_KIND_INVALID",
  "OWNER_HANDLER_DUPLICATE",
  "OWNER_HANDLER_INVALID",
  "OWNER_HANDLER_UNREGISTERED",
  "OWNER_TARGET_CONFLICT",
  "OWNER_TARGET_NOT_FOUND",
  "OwnerRuntimeEvent",
  "OwnerRuntimeEventKind",
  "OwnerRuntimeHandler",
  "OwnerRuntimeRegistry",
  "OwnerRuntimeRouteResult",
  "OwnerRuntimeRouter",
  "OwnerRuntimeRoutingError",
  "OwnerRuntimeTarget",
]
