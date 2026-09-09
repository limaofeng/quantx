from datetime import timedelta

import pytest
import strawberry
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.schemas import history_download_settings_schema as module
from quantx_api.gqlapi.security import AuthorizationExtension
from quantx_infrastructure.auth.tokens import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.history_download_settings import (
  HistoryDownloadSettingsRecord,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_settings_graphql_auth_validation_and_persistence(monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as conn:
    await conn.run_sync(
      lambda c: Base.metadata.create_all(
        c, tables=[HistoryDownloadSettingsRecord.__table__]
      )
    )
  monkeypatch.setattr(
    module, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False)
  )
  schema = strawberry.Schema(
    query=module.HistoryDownloadSettingsQuery,
    mutation=module.HistoryDownloadSettingsMutation,
    extensions=[AuthorizationExtension],
  )
  query = "{ historyDownloadSettings { version mode windows { start end } } }"
  mutation = """mutation($input:UpdateHistoryDownloadSettingsInput!) {
    updateHistoryDownloadSettings(input:$input) { version mode windows { start end } }
  }"""
  principal = Principal(
    user_id="test-user",
    username="test",
    display_name="test",
    device_session_id="test",
    access_token_expires_at=utcnow() + timedelta(minutes=10),
    permissions=frozenset({"system-status:read", "system-config:write"}),
    authorized_account_ids=(),
  )
  context = {"principal": principal}
  values = {
    "expectedVersion": 0,
    "mode": "CUSTOM",
    "nonTradingDaysAllowed": True,
    "windows": [{"start": "11:30", "end": "13:00"}, {"start": "16:00", "end": "08:30"}],
  }
  try:
    denied = await schema.execute(query, context_value={})
    assert denied.errors
    initial = await schema.execute(query, context_value=context)
    assert initial.data["historyDownloadSettings"]["mode"] == "ALWAYS"
    bad = await schema.execute(
      mutation,
      variable_values={"input": {**values, "windows": []}},
      context_value=context,
    )
    assert bad.errors[0].extensions["code"] == "HISTORY_DOWNLOAD_INVALID_SETTINGS"
    updated = await schema.execute(
      mutation, variable_values={"input": values}, context_value=context
    )
    assert not updated.errors
    assert updated.data["updateHistoryDownloadSettings"]["version"] == 1
    stale = await schema.execute(
      mutation, variable_values={"input": values}, context_value=context
    )
    assert stale.errors[0].extensions["code"] == "HISTORY_DOWNLOAD_SETTINGS_CONFLICT"
    from dataclasses import replace

    forbidden = await schema.execute(
      mutation,
      variable_values={"input": {**values, "expectedVersion": 1}},
      context_value={
        "principal": replace(principal, permissions=frozenset({"system-status:read"}))
      },
    )
    assert forbidden.errors[0].extensions["code"] == "FORBIDDEN"
  finally:
    await engine.dispose()
