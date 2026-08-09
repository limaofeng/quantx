from datetime import date
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.models.holidays import Holiday
from quantx_infrastructure.services import holiday_service as holiday_module
from quantx_infrastructure.services.holiday_service import HolidayService


def _install_repository(monkeypatch, repository) -> None:
  async def fake_get_async_db():
    yield object()

  monkeypatch.setattr(holiday_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    holiday_module,
    "HolidayRepository",
    lambda db: repository,
  )


@pytest.mark.asyncio
async def test_get_holidays_uses_repository(monkeypatch) -> None:
  expected = [
    Holiday(
      market="SH",
      year=2026,
      date=date(2026, 1, 1),
      description="元旦",
    )
  ]
  repository = AsyncMock()
  repository.find_all_by_market_and_year.return_value = expected
  _install_repository(monkeypatch, repository)

  result = await HolidayService().get_holidays("SH", 2026)

  assert result == expected
  repository.find_all_by_market_and_year.assert_awaited_once_with("SH", 2026)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_holiday", [True, False])
async def test_is_holiday_uses_repository(monkeypatch, is_holiday) -> None:
  repository = AsyncMock()
  repository.exists_by_market_and_date.return_value = is_holiday
  _install_repository(monkeypatch, repository)
  check_date = date(2026, 10, 1)

  result = await HolidayService().is_holiday("SH", check_date)

  assert result is is_holiday
  repository.exists_by_market_and_date.assert_awaited_once_with(
    "SH", check_date
  )


@pytest.mark.asyncio
async def test_bulk_save_replaces_one_market_year(monkeypatch) -> None:
  repository = AsyncMock()
  repository.create.side_effect = lambda holiday: holiday
  _install_repository(monkeypatch, repository)
  values = [
    {"date": date(2026, 1, 1), "description": "元旦"},
    {"date": date(2026, 10, 1), "description": "国庆节"},
  ]

  result = await HolidayService().bulk_save_holidays("SH", 2026, values)

  repository.delete_by_market_and_year.assert_awaited_once_with("SH", 2026)
  assert repository.create.await_count == 2
  assert [item.description for item in result] == ["元旦", "国庆节"]


@pytest.mark.asyncio
async def test_bulk_save_empty_list_still_clears_existing_year(monkeypatch) -> None:
  repository = AsyncMock()
  _install_repository(monkeypatch, repository)

  result = await HolidayService().bulk_save_holidays("SH", 2026, [])

  assert result == []
  repository.delete_by_market_and_year.assert_awaited_once_with("SH", 2026)
  repository.create.assert_not_awaited()
