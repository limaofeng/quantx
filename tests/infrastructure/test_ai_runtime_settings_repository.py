from __future__ import annotations

import pytest
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.ai_runtime_settings import (
  AiRuntimeSettingsAudit,
  AiRuntimeSettingsRecord,
)
from quantx_infrastructure.repositories.ai_runtime_settings_repository import (
  AiRuntimeEditableValues,
  AiRuntimeSettingsRepository,
  AiRuntimeSettingsVersionConflict,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_ai_runtime_settings_fall_back_to_environment_then_audit_override(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(settings, "ai_assistant_enabled", True)
  monkeypatch.setattr(settings, "openai_api_key", "secret-never-persisted")
  monkeypatch.setattr(settings, "quantx_ai_model", "gpt-env")
  monkeypatch.setattr(settings, "ai_assistant_max_concurrent_runs", 2)
  monkeypatch.setattr(settings, "ai_assistant_max_turns", 12)
  monkeypatch.setattr(settings, "ai_assistant_max_tool_calls", 8)
  monkeypatch.setattr(settings, "ai_assistant_run_timeout_seconds", 300)
  monkeypatch.setattr(settings, "ai_assistant_lease_seconds", 60)
  monkeypatch.setattr(settings, "ai_assistant_tracing_enabled", False)
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AiRuntimeSettingsRecord.__table__,
          AiRuntimeSettingsAudit.__table__,
        ],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)

  async with sessions() as db:
    repository = AiRuntimeSettingsRepository(db)
    initial = await repository.get_effective()
    assert initial.version == 0
    assert initial.source == "ENVIRONMENT"
    assert initial.values.model == "gpt-env"
    assert initial.api_key_configured is True

    updated = await repository.update(
      expected_version=0,
      values=AiRuntimeEditableValues(
        enabled=False,
        model="gpt-database",
        max_concurrent_runs=4,
        max_turns=20,
        max_tool_calls=10,
        run_timeout_seconds=600,
      ),
      user_id="user-1",
      request_id="request-1",
    )
    assert updated.version == 1
    assert updated.source == "DATABASE_OVERRIDE"
    assert updated.values.model == "gpt-database"
    assert updated.values.enabled is False
    audit_count = await db.scalar(select(func.count(AiRuntimeSettingsAudit.id)))
    assert audit_count == 1
    audit = await db.scalar(select(AiRuntimeSettingsAudit))
    assert audit is not None
    assert audit.previous_values["model"] == "gpt-env"
    assert audit.next_values["model"] == "gpt-database"
    assert "secret-never-persisted" not in str(audit.previous_values)
    assert "secret-never-persisted" not in str(audit.next_values)

  await engine.dispose()


@pytest.mark.asyncio
async def test_ai_runtime_settings_reject_stale_versions() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AiRuntimeSettingsRecord.__table__,
          AiRuntimeSettingsAudit.__table__,
        ],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  values = AiRuntimeEditableValues(
    enabled=True,
    model="gpt-test",
    max_concurrent_runs=2,
    max_turns=12,
    max_tool_calls=8,
    run_timeout_seconds=300,
  )
  async with sessions() as db:
    repository = AiRuntimeSettingsRepository(db)
    await repository.update(
      expected_version=0,
      values=values,
      user_id="user-1",
      request_id="request-1",
    )
    with pytest.raises(AiRuntimeSettingsVersionConflict) as error:
      await repository.update(
        expected_version=0,
        values=values,
        user_id="user-2",
        request_id="request-2",
      )
    assert error.value.current_version == 1

  await engine.dispose()
