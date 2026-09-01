from datetime import date

import pytest
from quantx_infrastructure.services.snapshot_fencing import (
  SNAPSHOT_FENCE_NAMESPACE,
  SNAPSHOT_SESSION_LOCK_NAMESPACE,
  SnapshotFenceLost,
  acquire_snapshot_publish_guard,
  assert_snapshot_run_owner,
)
from sqlalchemy.dialects import postgresql


class _RowsResult:
  def __init__(self, rows=()):
    self._rows = list(rows)

  def all(self):
    return list(self._rows)


class _FenceSession:
  def __init__(self, rows):
    self.rows = rows
    self.statements = []

  async def execute(self, statement):
    self.statements.append(statement)
    if len(self.statements) <= 2:
      return _RowsResult()
    return _RowsResult(self.rows)


class _ScalarRowsResult(_RowsResult):
  def scalars(self):
    return self


class _PublishSession:
  def __init__(self, *, factor_owner=True, date_keys=()):
    self.factor_owner = factor_owner
    self.date_keys = list(date_keys)
    self.calls = []

  async def scalar(self, statement, parameters=None):
    self.calls.append(("scalar", statement, parameters))
    return self.factor_owner

  async def execute(self, statement, parameters=None):
    self.calls.append(("execute", statement, parameters))
    if "FROM pg_locks" in str(statement):
      return _ScalarRowsResult(self.date_keys)
    return _RowsResult()


def _sql(statement) -> str:
  return str(
    statement.compile(
      dialect=postgresql.dialect(),
      compile_kwargs={"literal_binds": True},
    )
  )


@pytest.mark.asyncio
async def test_snapshot_fence_locks_dates_before_validating_latest_run_generation():
  first = date(2026, 8, 28)
  second = date(2026, 8, 31)
  session = _FenceSession([(first, 11), (second, 12)])

  await assert_snapshot_run_owner(session, {second: 12, first: 11})

  assert len(session.statements) == 3
  first_lock, second_lock, owner_query = map(_sql, session.statements)
  assert "pg_advisory_xact_lock" in first_lock
  assert str(SNAPSHOT_FENCE_NAMESPACE) in first_lock
  assert str(first.toordinal()) in first_lock
  assert str(second.toordinal()) in second_lock
  assert "max(daily_signal_runs.id)" in owner_query
  assert "daily_signal_runs.snapshot_date IN" in owner_query
  assert "daily_signal_runs.signal_version = 'daily-indicator-v1'" in owner_query


@pytest.mark.asyncio
async def test_snapshot_fence_rejects_superseded_or_incomplete_generation():
  first = date(2026, 8, 31)
  session = _FenceSession([(first, 13)])

  with pytest.raises(SnapshotFenceLost, match="已被更新任务替代"):
    await assert_snapshot_run_owner(session, {first: 12})

  with pytest.raises(SnapshotFenceLost, match="缺少运行代次"):
    await assert_snapshot_run_owner(_FenceSession([]), {})


@pytest.mark.asyncio
async def test_snapshot_publish_guard_holds_factor_tx_lock_and_verifies_exact_dates():
  first = date(2026, 8, 28)
  second = date(2026, 8, 31)
  session = _PublishSession(
    date_keys=[second.toordinal(), first.toordinal()],
  )

  await acquire_snapshot_publish_guard(
    session,
    lock_backend_pid=202,
    snapshot_dates=[second, first],
  )

  assert len(session.calls) == 3
  acquire_call, factor_call, dates_call = session.calls
  assert "pg_advisory_xact_lock_shared" in _sql(acquire_call[1])
  assert factor_call[2]["backend_pid"] == 202
  assert dates_call[2] == {
    "backend_pid": 202,
    "namespace": SNAPSHOT_SESSION_LOCK_NAMESPACE,
  }


@pytest.mark.parametrize(
  ("factor_owner", "date_keys"),
  [
    (False, [date(2026, 8, 31).toordinal()]),
    (True, []),
    (True, [date(2026, 8, 28).toordinal(), date(2026, 8, 31).toordinal()]),
  ],
)
@pytest.mark.asyncio
async def test_snapshot_publish_guard_rejects_lost_or_extra_session_locks(
  factor_owner,
  date_keys,
):
  session = _PublishSession(factor_owner=factor_owner, date_keys=date_keys)

  with pytest.raises(SnapshotFenceLost, match="所有权已丢失"):
    await acquire_snapshot_publish_guard(
      session,
      lock_backend_pid=202,
      snapshot_dates=[date(2026, 8, 31)],
    )
