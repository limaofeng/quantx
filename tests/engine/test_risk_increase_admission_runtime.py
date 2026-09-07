from unittest.mock import AsyncMock

import pytest
import quantx_engine.risk_increase_admission_runtime as runtime_module
from quantx_engine.risk_increase_admission_runtime import (
  RiskIncreaseAdmissionRuntime,
)


class _Scalars:
  def __init__(self, values):
    self._values = values

  def all(self):
    return list(self._values)


class _Session:
  def __init__(self):
    self._results = iter(
      (
        _Scalars(["account-ready", "account-both"]),
        _Scalars(["account-prepared", "account-both"]),
      )
    )
    self.rollback = AsyncMock()

  async def scalars(self, _statement):
    return next(self._results)


class _SessionContext:
  def __init__(self, session):
    self.session = session

  async def __aenter__(self):
    return self.session

  async def __aexit__(self, _exc_type, _exc, _traceback):
    return None


@pytest.mark.asyncio
async def test_recovery_scan_dispatches_ready_and_prepared_accounts(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session = _Session()
  dispatched: list[tuple[str, str]] = []

  class CommandService:
    def __init__(self, db):
      assert db is session

    async def dispatch_ready_risk_increase_orders(
      self,
      *,
      account_id: str,
      processing_owner: str,
    ):
      dispatched.append((account_id, processing_owner))
      return {f"intent-{account_id}": object()}

  monkeypatch.setattr(
    runtime_module,
    "AsyncSessionLocal",
    lambda: _SessionContext(session),
  )
  monkeypatch.setattr(runtime_module, "TradeCommandService", CommandService)
  runtime = RiskIncreaseAdmissionRuntime()

  result = await runtime.recover_once()

  assert [account for account, _owner in dispatched] == [
    "account-both",
    "account-prepared",
    "account-ready",
  ]
  assert all(owner.startswith("engine-admission:") for _, owner in dispatched)
  assert result == {"accounts": 3, "dispatched": 3}
  session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_failure_isolated_without_logging_account_or_raw_error(
  monkeypatch, caplog,
) -> None:
  session = _Session()
  dispatched = []

  class CommandService:
    def __init__(self, _db):
      pass

    async def dispatch_ready_risk_increase_orders(self, *, account_id, processing_owner):
      dispatched.append(account_id)
      if account_id == "account-both":
        raise RuntimeError("broker credential sentinel account-both")
      return {account_id: object()}

  monkeypatch.setattr(runtime_module, "AsyncSessionLocal", lambda: _SessionContext(session))
  monkeypatch.setattr(runtime_module, "TradeCommandService", CommandService)
  result = await RiskIncreaseAdmissionRuntime().recover_once()

  assert result == {"accounts": 3, "dispatched": 2}
  assert dispatched == ["account-both", "account-prepared", "account-ready"]
  session.rollback.assert_awaited_once()
  assert "RuntimeError" in caplog.text
  assert "account-both" not in caplog.text
  assert "credential sentinel" not in caplog.text


@pytest.mark.asyncio
async def test_start_runs_recovery_barrier_before_background_loop() -> None:
  runtime = RiskIncreaseAdmissionRuntime(interval_seconds=3)
  recovered = False

  async def recover_once():
    nonlocal recovered
    recovered = True
    return {"accounts": 0, "dispatched": 0}

  runtime.recover_once = AsyncMock(side_effect=recover_once)

  await runtime.start()
  try:
    assert recovered is True
    assert runtime.is_running is True
  finally:
    await runtime.stop()
