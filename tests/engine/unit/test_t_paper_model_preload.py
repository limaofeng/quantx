"""Actual PAPER reconcile + registry + artifact self-test; no model orders."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from quantx_engine.t_registry_model_batch_runtime import TRegistryModelBatchRuntime
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
)
from quantx_infrastructure.models.t_model_registry import (
  TModelRegistryEventRecord,
  TModelVersionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_model_registry_repository import (
  TModelRegistryRepository,
)
from sqlalchemy import select

from tests.engine.unit.test_t_assistant_paper_shadow_runtime import (
  _reconcile_cursor_probe,
)
from tests.engine.unit.test_t_assistant_paper_shadow_runtime import (
  sessions as paper_sessions,
)
from tests.engine.unit.test_t_model_runtime_loading import register
from tests.infrastructure.test_t_model_registry_repository import NOW
from tests.research.test_t_model_runtime_self_test import fixture

_FIXTURE = paper_sessions


@pytest.fixture
async def sessions(paper_sessions):
  async with paper_sessions() as db, db.begin():
    connection = await db.connection()
    for table in (TModelVersionRecord.__table__, TModelRegistryEventRecord.__table__):
      await connection.run_sync(table.create)
  yield paper_sessions


async def configure(sessions, config, args, *, policy=True):
  async with sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, config.id)
    settings = dict(head.settings)
    settings.update(scorer_mode="SHADOW", model_runtime_binding=args["binding"].to_dict())
    if policy:
      settings["model_runtime_policy"] = {"score_max_age_ms": 120000, "inference_budget_ms": 10000}
    head.settings = settings
    head.config_version += 1
    return head


def evidence(args):
  binding = args["binding"]
  return dict(synthetic=True, runtime_self_test_manifest_hash=binding.runtime_self_test_manifest_hash,
    self_test_tolerance_policy_version=binding.self_test_tolerance_policy_version,
    cpu_artifact_entry=args["entry"], runtime_self_test_manifest=args["self_test_manifest"])


async def test_paper_reconcile_loads_once_and_rechecks_registry_on_refresh(sessions, tmp_path, monkeypatch):
  config, _, supervisor, universe, predecessor = await _reconcile_cursor_probe(sessions)
  args = fixture(tmp_path)
  supervisor._model_artifact_root = tmp_path
  try:
    await register(sessions, args["binding"], "SHADOW_ELIGIBLE", evidence=evidence(args))
    config = await configure(sessions, config, args)
    key = await supervisor.reconcile(config=config, universe=universe)
    model = supervisor._bindings[key].model_runtime
    assert key != predecessor and predecessor not in supervisor._bindings
    assert isinstance(model, TRegistryModelBatchRuntime) and model.latest.reason == "COLD"
    monkeypatch.setattr(TRegistryModelBatchRuntime, "load", AsyncMock(side_effect=AssertionError("unexpected artifact reload")))
    assert await supervisor.reconcile(config=config, universe=universe) == key
    assert supervisor._bindings[key].model_runtime is model
    binding = args["binding"]
    async with sessions() as db, db.begin():
      await TModelRegistryRepository(db).set_stage(model_id=binding.model_id,
        model_version=binding.model_version, expected_revision=2, stage="SUSPENDED",
        actor_id="reviewer", reason="fixture revoke", now=NOW)
    with pytest.raises(ValueError, match="AUTHORIZATION_REVOKED"):
      await supervisor.reconcile(config=config, universe=universe)
    assert key not in supervisor._bindings
  finally:
    await supervisor.stop()


async def test_paper_quote_persists_cold_model_observation(sessions, tmp_path):
  config, hub, supervisor, universe, _ = await _reconcile_cursor_probe(sessions)
  args = fixture(tmp_path)
  supervisor._model_artifact_root = tmp_path
  try:
    await register(sessions, args["binding"], "SHADOW_ELIGIBLE", evidence=evidence(args))
    config = await configure(sessions, config, args)
    key = await supervisor.reconcile(config=config, universe=universe)
    await hub.emit(5)
    async with sessions() as db:
      cycle = await db.scalar(select(TAssistantDecisionCycleRecord).where(
        TAssistantDecisionCycleRecord.execution_id == key))
      assert cycle is not None and cycle.status == "PROPOSALS_COMMITTED"
      manifest = cycle.input_manifest
      view = manifest["model_view"]
      assert manifest["model_runtime_binding_hash"] == args["binding"].binding_hash
      assert view["binding"] == args["binding"].to_dict()
      assert view["status"] == "UNAVAILABLE" and view["revision"] == 0
      assert view["reason"] == "MODEL_SNAPSHOT_UNAVAILABLE"
      assert view["scores"] == [] and view["model_as_of_ms"] is None
      assert view["unavailable"] == [["600000.SH", "MODEL_SNAPSHOT_UNAVAILABLE"]]
    dispatch = await supervisor._dispatch_entries(supervisor._bindings[key])
    assert dispatch.status == "BLOCKED"
  finally:
    await supervisor.stop()


@pytest.mark.parametrize("failure", ["root", "policy", "unregistered", "artifact", "cancel"])
async def test_failed_preload_unbinds_previous_source(sessions, tmp_path, monkeypatch, failure):
  config, _, supervisor, universe, old_key = await _reconcile_cursor_probe(sessions)
  args = fixture(tmp_path)
  supervisor._model_artifact_root = None if failure == "root" else tmp_path
  try:
    if failure != "unregistered":
      frozen_evidence = evidence(args)
      if failure == "artifact":
        frozen_evidence["cpu_artifact_entry"] = args["entry"] | {"relative_path": "../model.json"}
      await register(sessions, args["binding"], "SHADOW_ELIGIBLE", evidence=frozen_evidence)
    config = await configure(sessions, config, args, policy=failure != "policy")
    if failure == "cancel":
      monkeypatch.setattr(TRegistryModelBatchRuntime, "load", AsyncMock(side_effect=asyncio.CancelledError("T_MODEL_TEST_CANCEL")))
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ValueError, match="T_MODEL_"):
      await supervisor.reconcile(config=config, universe=universe)
    assert old_key not in supervisor._bindings
    assert config.account_id not in supervisor._account_execution_ids
  finally:
    await supervisor.stop()
