"""Authenticated global history download configuration."""

from datetime import datetime

import strawberry
from graphql import GraphQLError
from pydantic import ValidationError
from quantx_contracts.history_download_settings import (
  HistoryDownloadMode,
  HistoryDownloadPolicy,
)
from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.repositories.history_download_settings_repository import (
  HistoryDownloadSettingsConflict,
  HistoryDownloadSettingsRepository,
)

from ..security import principal_from_context

HistoryDownloadModeType = strawberry.enum(HistoryDownloadMode)


@strawberry.type
class HistoryDownloadTimeWindow:
  start: str
  end: str


@strawberry.input
class HistoryDownloadTimeWindowInput:
  start: str
  end: str


@strawberry.type
class HistoryDownloadSettingsView:
  version: int
  mode: HistoryDownloadModeType
  non_trading_days_allowed: bool
  windows: list[HistoryDownloadTimeWindow]
  updated_at: datetime | None


@strawberry.input
class UpdateHistoryDownloadSettingsInput:
  expected_version: int
  mode: HistoryDownloadModeType
  non_trading_days_allowed: bool
  windows: list[HistoryDownloadTimeWindowInput]


def view(config):
  return HistoryDownloadSettingsView(
    version=config.version,
    mode=config.policy.mode,
    non_trading_days_allowed=config.policy.non_trading_days_allowed,
    windows=[
      HistoryDownloadTimeWindow(start=w.start, end=w.end) for w in config.policy.windows
    ],
    updated_at=config.updated_at,
  )


@strawberry.type
class HistoryDownloadSettingsQuery:
  @strawberry.field(description="读取历史行情补采策略、允许时段和配置版本")
  async def history_download_settings(
    self, info: strawberry.types.Info
  ) -> HistoryDownloadSettingsView:
    principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      return view(await HistoryDownloadSettingsRepository(db).get())


@strawberry.type
class HistoryDownloadSettingsMutation:
  @strawberry.mutation(description="按预期版本更新历史行情补采策略和允许时段")
  async def update_history_download_settings(
    self,
    info: strawberry.types.Info,
    input: UpdateHistoryDownloadSettingsInput,
  ) -> HistoryDownloadSettingsView:
    principal = principal_from_context(info.context)
    try:
      policy = HistoryDownloadPolicy(
        mode=input.mode,
        non_trading_days_allowed=input.non_trading_days_allowed,
        windows=[{"start": w.start, "end": w.end} for w in input.windows],
      )
    except ValidationError as exc:
      raise GraphQLError(
        "补采时段无效：" + exc.errors()[0]["msg"],
        extensions={"code": "HISTORY_DOWNLOAD_INVALID_SETTINGS"},
      ) from None
    async with AsyncSessionLocal() as db:
      try:
        result = await HistoryDownloadSettingsRepository(db).update(
          policy=policy,
          expected_version=input.expected_version,
          user_id=principal.user_id,
        )
      except HistoryDownloadSettingsConflict as exc:
        raise GraphQLError(
          str(exc), extensions={"code": "HISTORY_DOWNLOAD_SETTINGS_CONFLICT"}
        ) from None
      return view(result)
