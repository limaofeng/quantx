from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from quantx_infrastructure.models.limit_up_board_replay import (
  LimitUpBoardUniverseSnapshot,
)
from quantx_infrastructure.repositories.limit_up_board_replay_repository import (
  LimitUpBoardReplayRepository,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SHANGHAI = timezone(timedelta(hours=8))


def _payload(at: datetime, *, eligible: bool = True) -> dict:
  return {
    "scanner_running": True,
    "candidates": [
      {
        "code": "600000.SH",
        "updated_at": at.isoformat(),
        "promotion_eligible": eligible,
      }
    ],
  }


async def _create_repository():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(LimitUpBoardUniverseSnapshot.__table__.create)
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  return engine, sessions


@pytest.mark.asyncio
async def test_universe_snapshot_is_idempotent_and_chronologically_streamed() -> None:
  engine, sessions = await _create_repository()
  first_at = datetime(2026, 8, 20, 9, 30, tzinfo=SHANGHAI)
  second_at = first_at + timedelta(seconds=5)
  try:
    async with sessions() as db:
      repo = LimitUpBoardReplayRepository(db)
      first = await repo.save_universe_snapshot(
        snapshot_key="key-1",
        trade_date=first_at.date(),
        observed_at=first_at,
        source_max_at=first_at,
        snapshot_version="version-1",
        score_version="score-v1",
        feature_version="feature-v1",
        model_version="model-v1",
        exit_policy_version="exit-v1",
        candidate_count=1,
        eligible_count=1,
        payload=_payload(first_at),
      )
      repeated = await repo.save_universe_snapshot(
        snapshot_key="key-1",
        trade_date=first_at.date(),
        observed_at=first_at,
        source_max_at=first_at,
        snapshot_version="version-1",
        score_version="score-v1",
        feature_version="feature-v1",
        model_version="model-v1",
        exit_policy_version="exit-v1",
        candidate_count=1,
        eligible_count=1,
        payload=_payload(first_at),
      )
      second = await repo.save_universe_snapshot(
        snapshot_key="key-2",
        trade_date=second_at.date(),
        observed_at=second_at,
        source_max_at=second_at,
        snapshot_version="version-2",
        score_version="score-v1",
        feature_version="feature-v1",
        model_version="model-v1",
        exit_policy_version="exit-v1",
        candidate_count=1,
        eligible_count=1,
        payload=_payload(second_at),
      )

      streamed = [
        row
        async for row in repo.iter_universe_snapshots(
          first_at.replace(tzinfo=None),
          second_at.replace(tzinfo=None),
        )
      ]

    assert repeated.id == first.id
    assert [row.id for row in streamed] == [first.id, second.id]
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_universe_snapshot_rejects_collision_future_data_and_bad_counts() -> None:
  engine, sessions = await _create_repository()
  at = datetime(2026, 8, 20, 9, 30, tzinfo=SHANGHAI)
  try:
    async with sessions() as db:
      repo = LimitUpBoardReplayRepository(db)
      await repo.save_universe_snapshot(
        snapshot_key="key-1",
        trade_date=at.date(),
        observed_at=at,
        source_max_at=at,
        snapshot_version="version-1",
        score_version="score-v1",
        feature_version="feature-v1",
        model_version="model-v1",
        exit_policy_version="exit-v1",
        candidate_count=1,
        eligible_count=1,
        payload=_payload(at),
      )

      with pytest.raises(ValueError, match="键碰撞"):
        await repo.save_universe_snapshot(
          snapshot_key="key-1",
          trade_date=at.date(),
          observed_at=at,
          source_max_at=at,
          snapshot_version="version-other",
          score_version="score-v1",
          feature_version="feature-v1",
          model_version="model-v1",
          exit_policy_version="exit-v1",
          candidate_count=1,
          eligible_count=1,
          payload=_payload(at),
        )

      with pytest.raises(ValueError, match="截止时间"):
        await repo.save_universe_snapshot(
          snapshot_key="key-future",
          trade_date=at.date(),
          observed_at=at,
          source_max_at=at + timedelta(milliseconds=1),
          snapshot_version="version-future",
          score_version="score-v1",
          feature_version="feature-v1",
          model_version="model-v1",
          exit_policy_version="exit-v1",
          candidate_count=1,
          eligible_count=1,
          payload=_payload(at),
        )

      with pytest.raises(ValueError, match="候选数量"):
        await repo.save_universe_snapshot(
          snapshot_key="key-count",
          trade_date=at.date(),
          observed_at=at,
          source_max_at=at,
          snapshot_version="version-count",
          score_version="score-v1",
          feature_version="feature-v1",
          model_version="model-v1",
          exit_policy_version="exit-v1",
          candidate_count=0,
          eligible_count=1,
          payload=_payload(at),
        )
  finally:
    await engine.dispose()
