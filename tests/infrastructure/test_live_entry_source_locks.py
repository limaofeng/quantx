"""Public account admission acquires all T source fences before account locks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionOwnerRef
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.services.live_entry_source_locks import (
  lock_live_entry_sources,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)


def fixture_db(fault=None):
  locked = []
  sources = {
    "execution-a": SimpleNamespace(
      config_id="config-b",
      account_id="account",
      environment="LIVE",
      config_version_id="version-b",
      status="RUNNING",
      entry_readiness="READY",
    ),
    "execution-b": SimpleNamespace(
      config_id="config-a",
      account_id="account",
      environment="LIVE",
      config_version_id="version-a",
      status="RUNNING",
      entry_readiness="READY",
    ),
  }
  heads = {
    key: SimpleNamespace(
      account_id="account",
      enabled=True,
      strategy_run_id=None,
      desired_environment="LIVE",
      active_config_version_id="version-" + key[-1],
    )
    for key in ("config-a", "config-b")
  }
  if fault == "readiness":
    sources["execution-a"].entry_readiness = "DEGRADED"
  elif fault == "head":
    heads["config-b"].active_config_version_id = "changed"
  elif fault == "account":
    sources["execution-a"].account_id = "another-account"

  async def get(model, key, **kwargs):
    if kwargs.get("with_for_update"):
      assert kwargs.get("populate_existing") is True
      locked.append((model.__name__, key))
    if model is AccountRiskIncreaseAdmissionBatch:
      return SimpleNamespace(account_id="account")
    if model is TAssistantExecutionRecord:
      return sources.get(key)
    if model is TTradeGlobalConfig:
      return heads.get(key)
    raise AssertionError(model)

  return SimpleNamespace(
    get=AsyncMock(side_effect=get), in_transaction=lambda: True
  ), locked


def requests():
  return [
    {
      "execution_ref": ExecutionOwnerRef("T_ASSISTANT_EXECUTION", key),
      "account_id": "account",
      "t_trade_role": "ENTRY",
    }
    for key in ("execution-b", "execution-a", "execution-b")
  ]


async def test_source_fences_are_deduplicated_and_ordered():
  db, locked = fixture_db()
  await lock_live_entry_sources(db, account_id="account", order_requests=requests())
  assert locked == [
    ("TTradeGlobalConfig", "config-a"),
    ("TTradeGlobalConfig", "config-b"),
    ("TAssistantExecutionRecord", "execution-a"),
    ("TAssistantExecutionRecord", "execution-b"),
  ]


@pytest.mark.parametrize("fault", [None, "readiness", "head", "account"])
async def test_public_batch_fences_sources_before_account_authorization(
  monkeypatch, fault
):
  db, locked = fixture_db(fault)
  service = TradeCommandService(db)

  async def account_lock(*args, **kwargs):
    assert locked[-1] == ("TAssistantExecutionRecord", "execution-b")
    raise RuntimeError("reached-account-lock")

  authorization = AsyncMock(side_effect=account_lock)
  monkeypatch.setattr(service, "_require_live_authorization", authorization)
  with pytest.raises(
    AgentUnavailableError if fault else RuntimeError,
    match="LIVE_ENTRY_SOURCE_" if fault else "reached-account-lock",
  ):
    await service.enqueue_risk_increase_admission_batch(
      claim=SimpleNamespace(admission_batch_id="batch"),
      order_requests=requests(),
      account_snapshot_id="snapshot",
      account_snapshot_hash="a" * 64,
      obligation_watermark="b" * 64,
    )
  if fault:
    authorization.assert_not_awaited()
  else:
    authorization.assert_awaited_once()


async def test_other_owner_does_not_acquire_t_source_locks():
  db, locked = fixture_db()
  await lock_live_entry_sources(
    db,
    account_id="account",
    order_requests=[
      {
        "execution_ref": ExecutionOwnerRef("STRATEGY_RUN", "run"),
        "account_id": "account",
      }
    ],
  )
  db.get.assert_not_awaited()
  assert locked == []
