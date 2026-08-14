from __future__ import annotations

import pytest
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.schemas.ai_assistant_schema import (
  _context_refs,
)
from quantx_api.gqlapi.types.ai_assistant_types import (
  AiAssistantContextRefInput,
  AiAssistantRouteContextInput,
  SendAiAssistantMessageInput,
)


def test_ai_assistant_schema_is_typed_and_exposes_durable_operations() -> None:
  sdl = schema.as_str()

  for field in (
    "aiAssistantCapabilities",
    "aiAssistantThreads",
    "sendAiAssistantMessage",
    "resolveAiAssistantApproval",
    "aiAssistantEvents",
  ):
    assert field in sdl
  assert "union AiAssistantContentBlock" in sdl
  assert "AiAssistantCitationBlock" in sdl


def test_context_refs_normalize_route_and_explicit_object_references() -> None:
  input_value = SendAiAssistantMessageInput(
    thread_id="thread-1",
    text="分析当前策略",
    client_message_id="client-1",
    route_context=AiAssistantRouteContextInput(
      path="/strategies/run-1",
      object_type="STRATEGY_RUN_PAGE",
    ),
    context_refs=[
      AiAssistantContextRefInput(
        kind="strategy_run",
        object_id="run-1",
        label="当前策略",
      )
    ],
  )

  assert _context_refs(input_value) == [
    {
      "kind": "ROUTE",
      "objectId": "/strategies/run-1",
      "label": "STRATEGY_RUN_PAGE",
    },
    {"kind": "STRATEGY_RUN", "objectId": "run-1", "label": "当前策略"},
  ]


def test_context_refs_reject_unknown_kinds() -> None:
  input_value = SendAiAssistantMessageInput(
    thread_id="thread-1",
    text="test",
    client_message_id="client-1",
    context_refs=[AiAssistantContextRefInput(kind="ORDER", object_id="order-1")],
  )

  with pytest.raises(Exception, match="页面上下文引用无效"):
    _context_refs(input_value)


def test_context_ref_limit_includes_the_automatic_route_reference() -> None:
  input_value = SendAiAssistantMessageInput(
    thread_id="thread-1",
    text="test",
    client_message_id="client-1",
    route_context=AiAssistantRouteContextInput(path="/screening"),
    context_refs=[
      AiAssistantContextRefInput(kind="INSTRUMENT", object_id=f"stock-{index}")
      for index in range(8)
    ],
  )

  with pytest.raises(Exception, match="一次最多附加 8 个页面上下文"):
    _context_refs(input_value)
