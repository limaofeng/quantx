import asyncio
from types import SimpleNamespace

import pytest
from quantx_infrastructure.services import exit_plan_scope_lock as scope_module


@pytest.mark.asyncio
async def test_concurrent_plan_writers_share_position_first_lock_order(
  monkeypatch,
):
  records = {
    "plan-a": SimpleNamespace(
      plan_id="plan-a",
      account_id="account-a",
      instrument_code="600000.SH",
    ),
    "plan-b": SimpleNamespace(
      plan_id="plan-b",
      account_id="account-a",
      instrument_code="600000.SH",
    ),
  }
  position = SimpleNamespace(
    account_id="account-a",
    stock_code="600000.SH",
  )
  position_lock = asyncio.Lock()
  plan_locks = {plan_id: asyncio.Lock() for plan_id in records}

  class Session:
    def __init__(self):
      self.acquired = []
      self.trace = []

    async def scalar(self, _statement):
      self.trace.append("position")
      await position_lock.acquire()
      self.acquired.append(position_lock)
      return position

    def release(self):
      for lock in reversed(self.acquired):
        lock.release()

  class Repository:
    def __init__(self, db):
      self.db = db

    async def find_by_id(self, plan_id, *, for_update=False):
      if for_update:
        self.db.trace.append(plan_id)
        await plan_locks[plan_id].acquire()
        self.db.acquired.append(plan_locks[plan_id])
      return records.get(plan_id)

    async def find_reserving(self, **_kwargs):
      result = []
      for plan_id in sorted(records):
        self.db.trace.append(plan_id)
        await plan_locks[plan_id].acquire()
        self.db.acquired.append(plan_locks[plan_id])
        result.append(records[plan_id])
      return result

  monkeypatch.setattr(scope_module, "AutoExitPlanRepository", Repository)

  async def lock_as(plan_id):
    session = Session()
    try:
      scope = await scope_module.lock_exit_plan_scope_for_plan(session, plan_id)
      assert scope.plan(plan_id) is records[plan_id]
      await asyncio.sleep(0)
      return session.trace
    finally:
      session.release()

  traces = await asyncio.wait_for(
    asyncio.gather(lock_as("plan-a"), lock_as("plan-b")),
    timeout=1,
  )

  assert traces == [
    ["position", "plan-a", "plan-b"],
    ["position", "plan-a", "plan-b"],
  ]
