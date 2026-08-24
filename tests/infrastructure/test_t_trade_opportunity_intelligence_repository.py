from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
  TTradeInstrumentProfile,
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeInstrumentProfileRepository,
  TTradeOpportunityEvaluationRepository,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SHANGHAI = timezone(timedelta(hours=8))


async def _create_repositories():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(TTradeOpportunityEvaluation.__table__.create)
    await connection.run_sync(TTradeInstrumentProfile.__table__.create)
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  return engine, sessions


def _evaluation_arguments(
  at: datetime,
  *,
  event_key: str,
  account_id: str = "account-1",
  instrument_code: str = "600000.SH",
  payload: dict | None = None,
) -> dict:
  return {
    "event_key": event_key,
    "account_id": account_id,
    "strategy_run_id": "run-1",
    "instrument_code": instrument_code,
    "evaluated_at": at,
    "event_type": "STATE_TRANSITION",
    "policy_version": "policy-v3",
    "schema_version": "evaluation-v1",
    "payload": payload or {"state": "CANDIDATE", "opportunity_score": 72.5},
    "metrics": {"tick_count": 1},
  }


def _diagnostic_batch_record(
  at: datetime,
  *,
  event_key: str,
  instrument_code: str = "600000.SH",
  payload: dict | None = None,
) -> dict:
  return {
    **_evaluation_arguments(
      at,
      event_key=event_key,
      instrument_code=instrument_code,
      payload=payload,
    ),
    "record_kind": T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
    "window_started_at": at - timedelta(seconds=2),
    "window_ended_at": at,
    "coalesced_count": 1,
  }


def _profile_arguments(
  at: datetime,
  *,
  fingerprint_character: str,
  profile: dict | None = None,
) -> dict:
  return {
    "instrument_code": "600000.sh",
    "as_of": at,
    "profile": profile or {"liquidity_bucket": "HIGH"},
    "schema_version": "profile-v1",
    "version": "2026.08",
    "fingerprint": fingerprint_character * 64,
    "metrics": {"median_turnover": 123_000_000.0},
    "data_manifest": {"source_max_at": at.isoformat(), "sources": ["kline"]},
  }


@pytest.mark.asyncio
async def test_material_evaluation_is_append_only_idempotent_and_collision_safe() -> (
  None
):
  engine, sessions = await _create_repositories()
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  try:
    async with sessions() as db:
      repository = TTradeOpportunityEvaluationRepository(db)
      first = await repository.append_material(
        **_evaluation_arguments(at, event_key="material-1")
      )
      repeated = await repository.append_material(
        **_evaluation_arguments(at, event_key="material-1")
      )

      assert repeated.id == first.id
      assert first.instrument_code == "600000.SH"
      assert first.candidate_id is None
      assert first.content_fingerprint
      count = await db.scalar(select(func.count(TTradeOpportunityEvaluation.id)))
      assert count == 1

      with pytest.raises(ValueError, match="事件键碰撞"):
        await repository.append_material(
          **_evaluation_arguments(
            at,
            event_key="material-1",
            payload={"state": "BLOCKED"},
          )
        )

      with pytest.raises(ValueError, match="不是有效的 JSON"):
        await repository.append_material(
          **_evaluation_arguments(
            at,
            event_key="material-invalid-json",
            payload={"evaluated_at": at},
          )
        )

      assert not hasattr(repository, "update")
      assert not hasattr(repository, "delete")
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_id_is_denormalized_only_for_material_evidence() -> None:
  engine, sessions = await _create_repositories()
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  try:
    async with sessions() as db:
      repository = TTradeOpportunityEvaluationRepository(db)
      payload = {
        "signal_snapshot": {
          "candidate_id": "  candidate-1  ",
          "candidate_status": "ACTIVE",
        }
      }
      material = await repository.append_material(
        **_evaluation_arguments(
          at,
          event_key="candidate-material",
          payload=payload,
        )
      )
      diagnostic = await repository.append_coalesced_diagnostic(
        **_evaluation_arguments(
          at + timedelta(seconds=10),
          event_key="candidate-diagnostic",
          payload=payload,
        ),
        window_started_at=at,
        window_ended_at=at + timedelta(seconds=10),
        coalesced_count=10,
      )

      assert material.candidate_id == "candidate-1"
      assert diagnostic.candidate_id is None

      oversized_payload = {
        "signal_snapshot": {"candidate_id": "x" * 129},
      }
      with pytest.raises(ValueError, match="候选标识长度不能超过 128"):
        await repository.append_material(
          **_evaluation_arguments(
            at + timedelta(seconds=20),
            event_key="candidate-too-long",
            payload=oversized_payload,
          )
        )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_require_a_closed_point_in_time_window() -> None:
  engine, sessions = await _create_repositories()
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  try:
    async with sessions() as db:
      repository = TTradeOpportunityEvaluationRepository(db)

      with pytest.raises(ValueError, match="合并数量"):
        await repository.append_coalesced_diagnostic(
          **_evaluation_arguments(at, event_key="diagnostic-invalid"),
          window_started_at=at - timedelta(seconds=10),
          window_ended_at=at,
          coalesced_count=0,
        )

      with pytest.raises(ValueError, match="不能晚于评估时间"):
        await repository.append_coalesced_diagnostic(
          **_evaluation_arguments(at, event_key="diagnostic-future"),
          window_started_at=at - timedelta(seconds=10),
          window_ended_at=at + timedelta(microseconds=1),
          coalesced_count=10,
        )

      diagnostic = await repository.append_coalesced_diagnostic(
        **_evaluation_arguments(at, event_key="diagnostic-1"),
        window_started_at=at - timedelta(seconds=10),
        window_ended_at=at,
        coalesced_count=25,
      )

      assert diagnostic.record_kind == T_TRADE_EVALUATION_KIND_DIAGNOSTIC
      assert diagnostic.coalesced_count == 25
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_batch_append_600_rows_uses_three_multi_values_executes_and_one_commit() -> None:
  class CountingAsyncSession(AsyncSession):
    execute_calls = 0
    commit_calls = 0
    execute_parameter_batches: list[object] = []

    async def execute(self, *args, **kwargs):
      type(self).execute_calls += 1
      type(self).execute_parameter_batches.append(
        args[1] if len(args) > 1 else kwargs.get("params")
      )
      return await super().execute(*args, **kwargs)

    async def commit(self) -> None:
      type(self).commit_calls += 1
      await super().commit()

  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(TTradeOpportunityEvaluation.__table__.create)
  sessions = async_sessionmaker(
    engine,
    class_=CountingAsyncSession,
    expire_on_commit=False,
  )
  reader_sessions = async_sessionmaker(engine, expire_on_commit=False)
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  records = [
    _diagnostic_batch_record(
      at + timedelta(seconds=index * 2),
      event_key=f"diagnostic-batch-{index}",
      instrument_code=f"{600000 + index:06d}.SH",
    )
    for index in range(600)
  ]
  try:
    async with sessions() as db:
      rows = await TTradeOpportunityEvaluationRepository(db).append_many(records)

    assert len(rows) == len(records)
    assert [row.event_key for row in rows] == [
      record["event_key"] for record in records
    ]
    assert CountingAsyncSession.execute_calls == 3
    assert CountingAsyncSession.execute_parameter_batches == [None, None, None]
    assert CountingAsyncSession.commit_calls == 1
    async with reader_sessions() as db:
      assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 600
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_batch_append_replay_is_idempotent_and_collision_fails_closed() -> None:
  engine, sessions = await _create_repositories()
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  records = [
    _diagnostic_batch_record(
      at + timedelta(seconds=index * 2),
      event_key=f"diagnostic-replay-{index}",
      instrument_code=f"6001{index:02d}.SH",
    )
    for index in range(3)
  ]
  try:
    async with sessions() as db:
      repository = TTradeOpportunityEvaluationRepository(db)
      first = await repository.append_many(records)
      replayed = await repository.append_many(records)

      assert [row.id for row in replayed] == [row.id for row in first]
      assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 3

      conflicting = [dict(record) for record in records]
      conflicting[1]["payload"] = {"state": "BLOCKED"}
      with pytest.raises(ValueError, match="事件键碰撞"):
        await repository.append_many(conflicting)
      assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 3
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_batch_append_reconciles_a_commit_unknown_without_duplicates() -> None:
  class CommitUnknownSession(AsyncSession):
    raise_after_first_commit = True

    async def commit(self) -> None:
      await super().commit()
      if type(self).raise_after_first_commit:
        type(self).raise_after_first_commit = False
        raise ConnectionError("commit result unavailable")

  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(TTradeOpportunityEvaluation.__table__.create)
  sessions = async_sessionmaker(
    engine,
    class_=CommitUnknownSession,
    expire_on_commit=False,
  )
  reader_sessions = async_sessionmaker(engine, expire_on_commit=False)
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  records = [
    _diagnostic_batch_record(
      at + timedelta(seconds=index * 2),
      event_key=f"diagnostic-commit-unknown-{index}",
    )
    for index in range(2)
  ]
  try:
    async with sessions() as db:
      rows = await TTradeOpportunityEvaluationRepository(db).append_many(records)
      assert {row.event_key for row in rows} == {
        "diagnostic-commit-unknown-0",
        "diagnostic-commit-unknown-1",
      }

    async with reader_sessions() as db:
      assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 2
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_batch_append_conflict_rolls_back_other_new_rows() -> None:
  engine, sessions = await _create_repositories()
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  original = _diagnostic_batch_record(at, event_key="diagnostic-existing")
  new_record = _diagnostic_batch_record(
    at + timedelta(seconds=2),
    event_key="diagnostic-must-roll-back",
  )
  try:
    async with sessions() as db:
      repository = TTradeOpportunityEvaluationRepository(db)
      await repository.append_many([original])
      conflicting = dict(original)
      conflicting["payload"] = {"state": "CONFLICT"}

      with pytest.raises(ValueError, match="事件键碰撞"):
        await repository.append_many([conflicting, new_record])

      rows = await repository.list_evaluations(account_id="account-1", limit=10)
      assert [row.event_key for row in rows] == ["diagnostic-existing"]
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_evaluation_time_cursor_is_stable_and_account_scoped() -> None:
  engine, sessions = await _create_repositories()
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  try:
    async with sessions() as db:
      repository = TTradeOpportunityEvaluationRepository(db)
      for index in range(4):
        await repository.append_material(
          **_evaluation_arguments(
            at if index < 3 else at - timedelta(seconds=1),
            event_key=f"material-{index}",
          )
        )
      await repository.append_material(
        **_evaluation_arguments(
          at,
          event_key="other-account",
          account_id="account-2",
        )
      )

      all_rows = await repository.list_evaluations(
        account_id="account-1",
        limit=10,
      )
      first_page = await repository.list_evaluations(
        account_id="account-1",
        limit=2,
      )
      cursor = first_page[-1]
      second_page = await repository.list_evaluations(
        account_id="account-1",
        limit=2,
        cursor_evaluated_at=cursor.evaluated_at,
        cursor_id=cursor.id,
      )

      assert [row.id for row in first_page + second_page] == [
        row.id for row in all_rows
      ]
      assert len({row.id for row in first_page + second_page}) == 4
      assert {row.account_id for row in all_rows} == {"account-1"}

      with pytest.raises(ValueError, match="游标必须同时"):
        await repository.list_evaluations(
          account_id="account-1",
          cursor_evaluated_at=at,
        )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_evaluation_and_profile_can_join_a_caller_owned_unit_of_work() -> None:
  engine, sessions = await _create_repositories()
  at = datetime(2026, 8, 23, 10, 0, tzinfo=SHANGHAI)
  try:
    async with sessions() as db:
      evaluations = TTradeOpportunityEvaluationRepository(db)
      profiles = TTradeInstrumentProfileRepository(db)
      await evaluations.append_material(
        **_evaluation_arguments(at, event_key="rolled-back-evaluation"),
        commit=False,
      )
      await profiles.save_profile(
        **_profile_arguments(at, fingerprint_character="a"),
        commit=False,
      )
      await db.rollback()

    async with sessions() as db:
      evaluation_count = await db.scalar(
        select(func.count(TTradeOpportunityEvaluation.id))
      )
      profile_count = await db.scalar(select(func.count(TTradeInstrumentProfile.id)))
      assert evaluation_count == 0
      assert profile_count == 0

      evaluations = TTradeOpportunityEvaluationRepository(db)
      profiles = TTradeInstrumentProfileRepository(db)
      await evaluations.append_material(
        **_evaluation_arguments(at, event_key="committed-evaluation"),
        commit=False,
      )
      await profiles.save_profile(
        **_profile_arguments(at, fingerprint_character="b"),
        commit=False,
      )
      await db.commit()

    async with sessions() as db:
      evaluation_count = await db.scalar(
        select(func.count(TTradeOpportunityEvaluation.id))
      )
      profile_count = await db.scalar(select(func.count(TTradeInstrumentProfile.id)))
      assert evaluation_count == 1
      assert profile_count == 1
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_profile_is_immutable_idempotent_and_rejects_future_data() -> None:
  engine, sessions = await _create_repositories()
  at = datetime(2026, 8, 23, 9, 30, tzinfo=SHANGHAI)
  try:
    async with sessions() as db:
      repository = TTradeInstrumentProfileRepository(db)
      first = await repository.save_profile(
        **_profile_arguments(at, fingerprint_character="a")
      )
      repeated = await repository.save_profile(
        **_profile_arguments(at, fingerprint_character="a")
      )

      assert repeated.id == first.id
      assert first.instrument_code == "600000.SH"

      with pytest.raises(ValueError, match="指纹碰撞"):
        await repository.save_profile(
          **_profile_arguments(
            at,
            fingerprint_character="a",
            profile={"liquidity_bucket": "LOW"},
          )
        )

      with pytest.raises(ValueError, match="时点版本碰撞"):
        await repository.save_profile(
          **_profile_arguments(at, fingerprint_character="b")
        )

      future = _profile_arguments(
        at + timedelta(minutes=1),
        fingerprint_character="c",
      )
      future["data_manifest"] = {
        "source_max_at": (at + timedelta(minutes=2)).isoformat()
      }
      with pytest.raises(ValueError, match="不能晚于画像时点"):
        await repository.save_profile(**future)

      with pytest.raises(ValueError, match="64 位 SHA-256"):
        invalid = _profile_arguments(
          at + timedelta(minutes=2),
          fingerprint_character="d",
        )
        invalid["fingerprint"] = "not-a-fingerprint"
        await repository.save_profile(**invalid)
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_profile_lookup_never_reads_beyond_requested_as_of() -> None:
  engine, sessions = await _create_repositories()
  first_at = datetime(2026, 8, 23, 9, 30, tzinfo=SHANGHAI)
  second_at = first_at + timedelta(minutes=30)
  try:
    async with sessions() as db:
      repository = TTradeInstrumentProfileRepository(db)
      first = await repository.save_profile(
        **_profile_arguments(first_at, fingerprint_character="a")
      )
      second_arguments = _profile_arguments(
        second_at,
        fingerprint_character="b",
        profile={"liquidity_bucket": "MEDIUM"},
      )
      second_arguments["version"] = "2026.09"
      second = await repository.save_profile(**second_arguments)

      before_first = await repository.latest_at_or_before(
        instrument_code="600000.SH",
        as_of=first_at - timedelta(microseconds=1),
        schema_version="profile-v1",
        version="2026.08",
      )
      at_first = await repository.latest_at_or_before(
        instrument_code="600000.SH",
        as_of=second_at - timedelta(microseconds=1),
        schema_version="profile-v1",
        version="2026.08",
      )
      at_second = await repository.latest_at_or_before(
        instrument_code="600000.SH",
        as_of=second_at,
        schema_version="profile-v1",
      )
      old_version_at_second = await repository.latest_at_or_before(
        instrument_code="600000.SH",
        as_of=second_at,
        schema_version="profile-v1",
        version="2026.08",
      )

      assert before_first is None
      assert at_first is not None and at_first.id == first.id
      assert at_second is not None and at_second.id == second.id
      assert old_version_at_second is not None
      assert old_version_at_second.id == first.id
  finally:
    await engine.dispose()
