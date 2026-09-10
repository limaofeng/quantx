"""Registered full binding -> safe self-test -> CPU scores; synthetic data only."""
# ruff: noqa: F811


import pytest
from quantx_engine.t_registry_model_batch_runtime import TRegistryModelBatchRuntime
from quantx_infrastructure.repositories.t_model_registry_repository import (
  TModelRegistryRepository,
)

from tests.infrastructure.test_t_model_registry_repository import (  # noqa: F401
  NOW,
  registration,
  sessions,
)
from tests.research.test_t_assistant_model_data import bar
from tests.research.test_t_model_runtime_self_test import fixture, rebind


async def register(sessions, binding, gate, *, evidence=None):
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    await repo.append_candidate(**(registration() | dict(
      model_id=binding.model_id, model_version=binding.model_version,
      artifact_sha256=binding.artifact_manifest_sha256,
      policy_compatibility_hash=binding.portfolio_policy_compatibility_hash,
      gate_conclusion=gate, evidence=evidence if evidence is not None else {
        "synthetic": True, "runtime_self_test_manifest_hash": binding.runtime_self_test_manifest_hash,
        "self_test_tolerance_policy_version": binding.self_test_tolerance_policy_version,
      },
    )))
    for revision, stage in enumerate(["SHADOW", "ACTIVE"] if binding.registry_stage == "ACTIVE" else ["SHADOW"], 1):
      await repo.set_stage(model_id=binding.model_id, model_version=binding.model_version,
        expected_revision=revision, stage=stage, actor_id="reviewer", reason="fixture", now=NOW)


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
async def test_full_binding_loads_self_tests_and_identifies_scores(sessions, tmp_path, mode):
  args = fixture(tmp_path)
  rebind(args, registry_stage=mode, registry_authorization_revision=3 if mode == "ACTIVE" else 2)
  await register(sessions, args["binding"], f"{mode}_ELIGIBLE")
  feature = bar()
  runtime = await TRegistryModelBatchRuntime.load(**args, session_factory=sessions,
    clock_ms=lambda: feature.available_at_ms + 10, max_age_ms=120000, inference_budget_ms=10000)
  assert runtime.latest.reason == "COLD" and not runtime.latest.active_scores
  result = await runtime.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=("rule",))
  scores = result.active_scores + result.shadow_scores
  assert result.reason == "VALID" and len(scores) == 1
  assert scores[0].model_runtime_binding_hash == args["binding"].binding_hash
  assert scores[0].probabilities == pytest.approx((0.5, 0.25, 0.25), abs=1e-12)
  frozen = await runtime.freeze_for_snapshot(as_of_ms=feature.available_at_ms + 10,
    instrument_codes=(feature.instrument_code,), rule_order=("current",))
  assert (frozen.active_scores + frozen.shadow_scores)[0].model_runtime_binding_hash == args["binding"].binding_hash


@pytest.mark.parametrize("failure", ["unregistered", "evidence", "self_test", "revoke_during_load"])
async def test_loading_cannot_return_runtime_without_authority_and_self_test(sessions, tmp_path, monkeypatch, failure):
  import quantx_engine.t_registry_model_batch_runtime as module

  args = fixture(tmp_path)
  if failure != "unregistered":
    await register(sessions, args["binding"], "SHADOW_ELIGIBLE",
      evidence={"synthetic": True} if failure == "evidence" else None)
  if failure == "self_test":
    args["self_test_manifest"]["cases"][0]["expected_probabilities"] = [0.1, 0.2, 0.7]
  original = module.asyncio.to_thread
  calls = []

  async def intercepted(function, **kwargs):
    calls.append(True)
    artifact = await original(function, **kwargs)
    if failure == "revoke_during_load":
      binding = args["binding"]
      async with sessions() as db, db.begin():
        await TModelRegistryRepository(db).set_stage(model_id=binding.model_id,
          model_version=binding.model_version, expected_revision=binding.registry_authorization_revision,
          stage="SUSPENDED", actor_id="reviewer", reason="revoke during self-test", now=NOW)
    return artifact

  monkeypatch.setattr(module.asyncio, "to_thread", intercepted)
  with pytest.raises(ValueError, match="T_MODEL_"):
    await TRegistryModelBatchRuntime.load(**args, session_factory=sessions,
      clock_ms=lambda: bar().available_at_ms + 10, max_age_ms=120000, inference_budget_ms=10000)
  assert bool(calls) == (failure in {"self_test", "revoke_during_load"})
