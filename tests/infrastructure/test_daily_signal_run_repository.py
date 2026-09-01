"""A rerun must revoke an earlier whole-day indicator readiness certificate."""

from datetime import date, datetime

import pytest
import pytest_asyncio
from quantx_domain.indicators import INDICATOR_VERSION
from quantx_infrastructure.models.daily_signal_run import DailySignalRun
from quantx_infrastructure.repositories.daily_signal_run_repository import (
  DailySignalRunRepository,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TARGET = date(2026, 8, 31)
PREVIOUS = date(2026, 8, 28)


@pytest_asyncio.fixture
async def session():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  try:
    async with engine.begin() as connection:
      await connection.run_sync(DailySignalRun.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
      yield db
  finally:
    await engine.dispose()


def _run(run_id, status, *, target=TARGET, version=INDICATOR_VERSION, clock=None):
  stamp = clock or datetime(2026, 8, 31, 16)
  return DailySignalRun(
    id=run_id,
    snapshot_date=target,
    signal_version=version,
    status=status,
    started_at=stamp,
    completed_at=stamp if status == "success" else None,
    created_at=stamp,
    updated_at=stamp,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "new_status", ["running", "failed", "partial_failure", "scoped_success"]
)
async def test_newer_attempt_hides_historical_success_for_both_queries(
  session, new_status
):
  # Even an older run with later caller timestamps cannot regain authority.
  older = _run(10, "success", clock=datetime(2026, 9, 1, 18))
  session.add_all(
    [
      older,
      _run(11, new_status),
      _run(12, "success", target=PREVIOUS),
    ]
  )
  await session.commit()
  older.updated_at = older.completed_at = datetime(2026, 9, 2, 18)
  await session.commit()
  repository = DailySignalRunRepository(session)

  assert await repository.find_latest_completed(TARGET) is None
  assert (await repository.find_latest_completed()).snapshot_date == PREVIOUS
  assert await repository.find_completed_dates(PREVIOUS, TARGET) == [PREVIOUS]
  assert await repository.find_completed_dates(TARGET, TARGET) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("old_status", ["success", "running", "failed"])
@pytest.mark.parametrize("current_status", ["success", "failed"])
async def test_later_old_version_run_neither_revokes_nor_restores_current_readiness(
  session,
  old_status,
  current_status,
):
  session.add_all(
    [
      _run(1, "success"),
      _run(2, current_status),
      _run(3, old_status, version="daily-old"),
    ]
  )
  await session.commit()
  repository = DailySignalRunRepository(session)
  completed = await repository.find_latest_completed(TARGET)
  if current_status == "success":
    assert completed.id == 2
    assert await repository.find_completed_dates(TARGET, TARGET) == [TARGET]
  else:
    assert completed is None
    assert await repository.find_completed_dates(TARGET, TARGET) == []


@pytest.mark.asyncio
async def test_new_whole_market_success_restores_readiness_without_duplicate_dates(
  session,
):
  session.add_all(
    [
      _run(1, "success"),
      _run(2, "failed"),
      _run(3, "running"),
      _run(4, "scoped_success"),
      _run(5, "success"),
    ]
  )
  await session.commit()
  repository = DailySignalRunRepository(session)

  assert (await repository.find_latest_completed(TARGET)).id == 5
  assert (await repository.find_latest_completed()).id == 5
  assert await repository.find_completed_dates(TARGET, TARGET) == [TARGET]


@pytest.mark.asyncio
async def test_old_version_only_or_empty_interval_is_not_complete(session):
  session.add(_run(1, "success", version="daily-old"))
  await session.commit()
  repository = DailySignalRunRepository(session)

  assert await repository.find_latest_completed() is None
  assert await repository.find_completed_dates(PREVIOUS, TARGET) == []
