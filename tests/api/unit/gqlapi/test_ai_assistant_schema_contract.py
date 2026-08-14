from __future__ import annotations

import pytest
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.schemas import ai_assistant_schema
from quantx_api.gqlapi.schemas.ai_assistant_schema import (
  _context_refs,
)
from quantx_api.gqlapi.types.ai_assistant_types import (
  AiAssistantContextRefInput,
  AiAssistantRouteContextInput,
  SendAiAssistantMessageInput,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.models.ai_runtime_settings import (
  AiRuntimeSettingsAudit,
  AiRuntimeSettingsRecord,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_ai_assistant_schema_is_typed_and_exposes_durable_operations() -> None:
  sdl = schema.as_str()

  for field in (
    "aiRuntimeSettings",
    "updateAiRuntimeSettings",
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


@pytest.mark.asyncio
async def test_ai_runtime_settings_query_update_and_version_conflict(
  monkeypatch: pytest.MonkeyPatch,
  authorized_graphql_context,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AiRuntimeSettingsRecord.__table__,
          AiRuntimeSettingsAudit.__table__,
          RuntimeComponentHeartbeat.__table__,
        ],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(ai_assistant_schema, "AsyncSessionLocal", sessions)

  async def ignore_notify(_version: int) -> None:
    return None

  monkeypatch.setattr(
    ai_assistant_schema,
    "_safe_notify_runtime_settings",
    ignore_notify,
  )

  query_result = await schema.execute(
    """
    query RuntimeSettings {
      aiRuntimeSettings {
        version
        source
        apiKeyConfigured
        runtimeStatus
        applyState
      }
    }
    """,
    context_value=authorized_graphql_context,
  )
  assert query_result.errors is None
  assert query_result.data["aiRuntimeSettings"]["version"] == 0
  assert query_result.data["aiRuntimeSettings"]["source"] == "ENVIRONMENT"
  assert query_result.data["aiRuntimeSettings"]["runtimeStatus"] == "OFFLINE"

  mutation = """
    mutation UpdateRuntime($input: UpdateAiRuntimeSettingsInput!) {
      updateAiRuntimeSettings(input: $input) {
        version
        source
        model
        applyState
      }
    }
  """
  variables = {
    "input": {
      "expectedVersion": 0,
      "enabled": True,
      "model": "  gpt-runtime-test  ",
      "maxConcurrentRuns": 4,
      "maxTurns": 20,
      "maxToolCalls": 10,
      "runTimeoutSeconds": 600,
    }
  }
  update_result = await schema.execute(
    mutation,
    variable_values=variables,
    context_value=authorized_graphql_context,
  )
  assert update_result.errors is None
  assert update_result.data["updateAiRuntimeSettings"] == {
    "version": 1,
    "source": "DATABASE_OVERRIDE",
    "model": "gpt-runtime-test",
    "applyState": "OFFLINE",
  }

  conflict_result = await schema.execute(
    mutation,
    variable_values=variables,
    context_value=authorized_graphql_context,
  )
  assert conflict_result.errors
  assert (
    conflict_result.errors[0].extensions["code"]
    == "AI_RUNTIME_SETTINGS_VERSION_CONFLICT"
  )
  await engine.dispose()
