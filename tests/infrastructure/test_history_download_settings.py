from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from quantx_contracts.history_download_settings import HistoryDownloadPolicy
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.history_download_settings import (
  HistoryDownloadSettingsRecord,
)
from quantx_infrastructure.repositories.history_download_settings_repository import (
  HistoryDownloadSettingsConflict,
  HistoryDownloadSettingsRepository,
)
from quantx_infrastructure.services import development_history_window as window
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def custom(**overrides):
  return HistoryDownloadPolicy.model_validate(
    {
      "mode": "CUSTOM",
      "non_trading_days_allowed": False,
      "windows": [
        {"start": "11:30", "end": "13:00"},
        {"start": "16:00", "end": "08:30"},
      ],
      **overrides,
    }
  )


def now(clock, day=9):
  return datetime.fromisoformat(f"2026-09-{day:02d}T{clock}:00").replace(
    tzinfo=ZoneInfo("Asia/Shanghai")
  )


@pytest.mark.parametrize(
  "clock,expected",
  [
    ("00:00", True),
    ("08:29", True),
    ("08:30", False),
    ("11:29", False),
    ("11:30", True),
    ("12:59", True),
    ("13:00", False),
    ("15:59", False),
    ("16:00", True),
  ],
)
async def test_multiple_windows_and_boundaries(clock, expected):
  assert await window.policy_window_open(custom(), now(clock)) is expected


async def test_always_default_does_not_require_calendar(monkeypatch):
  calendar = AsyncMock(side_effect=RuntimeError("unavailable"))
  monkeypatch.setattr(window.HolidayService, "get_holidays", calendar)
  assert await window.policy_window_open(HistoryDownloadPolicy(), now("10:00"))
  calendar.assert_not_awaited()


async def test_non_trading_days_and_unknown_calendar(monkeypatch):
  monkeypatch.setattr(window.HolidayService, "get_holidays", AsyncMock(return_value=[]))
  policy = custom(non_trading_days_allowed=True)
  assert not await window.policy_window_open(policy, now("10:00"))
  assert await window.policy_window_open(policy, now("12:00"))
  assert await window.policy_window_open(policy, now("10:00", 12))
  assert not await window.policy_window_open(custom(), now("10:00", 12))
  monkeypatch.setattr(
    window.HolidayService,
    "get_holidays",
    AsyncMock(return_value=[SimpleNamespace(date=now("10:00").date())]),
  )
  assert await window.policy_window_open(policy, now("10:00"))
  assert await window.policy_window_open(
    custom(), now("12:00").astimezone(ZoneInfo("UTC"))
  )


@pytest.mark.parametrize(
  "windows",
  [
    [],
    [{"start": "12:00", "end": "12:00"}],
    [{"start": "24:00", "end": "13:00"}],
    [{"start": "9:00", "end": "13:00"}],
    [{"start": "16:00", "end": "08:30"}, {"start": "08:00", "end": "09:00"}],
    [{"start": "11:30", "end": "13:00"}, {"start": "12:00", "end": "14:00"}],
  ],
)
def test_invalid_windows(windows):
  with pytest.raises(ValidationError):
    custom(windows=windows)


async def test_persistence_hot_read_and_stale_version(monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as conn:
    await conn.run_sync(
      lambda c: Base.metadata.create_all(
        c, tables=[HistoryDownloadSettingsRecord.__table__]
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(window, "AsyncSessionLocal", sessions)
  try:
    assert await window.history_window_open(now("10:00"))
    async with sessions() as db:
      repo = HistoryDownloadSettingsRepository(db)
      assert (await repo.get()).version == 0
      result = await repo.update(
        policy=custom(), expected_version=0, user_id="test-user"
      )
      assert result.version == 1
    assert not await window.history_window_open(now("10:00"))
    assert await window.history_window_open(now("12:00"))
    async with sessions() as db:
      with pytest.raises(HistoryDownloadSettingsConflict):
        await HistoryDownloadSettingsRepository(db).update(
          policy=HistoryDownloadPolicy(), expected_version=0, user_id="test-user"
        )
    assert not await window.history_window_open(now("10:00"))
    async with sessions() as db:
      await HistoryDownloadSettingsRepository(db).update(
        policy=HistoryDownloadPolicy(), expected_version=1, user_id="test-user"
      )
    assert await window.history_window_open(now("10:00"))
    monkeypatch.setattr(
      HistoryDownloadSettingsRepository,
      "get",
      AsyncMock(side_effect=RuntimeError("unavailable")),
    )
    assert not await window.history_window_open(now("12:00"))
  finally:
    await engine.dispose()
