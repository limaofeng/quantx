"""Real registry + CPU scorer in isolated SQLite, without model release gates."""
# ruff: noqa: F811

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from quantx_engine.t_registry_model_batch_runtime import TRegistryModelBatchRuntime
from quantx_infrastructure.repositories.t_model_registry_repository import (
  TModelRegistryRepository,
)

from tests.engine.unit.test_t_model_batch_runtime import runtime
from tests.infrastructure.test_t_model_registry_repository import (  # noqa: F401
  NOW,
  registration,
  sessions,
)
from tests.research.test_t_assistant_model_data import bar


async def registered(sessions, tmp_path, mode):
  model = runtime(tmp_path, mode)
  artifact = model.artifact
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    await repo.append_candidate(**(registration() | dict(
      model_id=artifact.model_id, model_version=artifact.model_version,
      artifact_sha256=artifact.sha256, policy_compatibility_hash=model.policy_hash,
      gate_conclusion=f"{mode}_ELIGIBLE",
    )))
    revision = 1
    for target in (["SHADOW", "ACTIVE"] if mode == "ACTIVE" else ["SHADOW"]):
      await repo.set_stage(model_id=artifact.model_id, model_version=artifact.model_version,
        expected_revision=revision, stage=target, actor_id="reviewer", reason="fixture", now=NOW)
      revision += 1
  # A new frozen execution binding, not a hot replacement on an existing runtime.
  model = type(model)(mode=mode, artifact=artifact,
    authorization=replace(model.authorization, registry_authorization_revision=revision),
    policy_hash=model.policy_hash, max_age_ms=model.max_age_ms, inference_budget_ms=model.budget_ms)
  return model, TRegistryModelBatchRuntime(model=model, session_factory=sessions)


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
async def test_registry_revocation_invalidates_even_exact_replay(sessions, tmp_path, mode):
  model, scorer = await registered(sessions, tmp_path, mode)
  feature = bar()
  args = dict(model_as_of_ms=feature.available_at_ms, rule_order=("b", "a"))
  first = await scorer.evaluate((feature,), **args)
  assert first.reason == "VALID" and first.revision == 1
  assert (await scorer.evaluate((feature,), **args)).manifest_hash == first.manifest_hash
  async with sessions() as db, db.begin():
    await TModelRegistryRepository(db).set_stage(
      model_id=model.artifact.model_id, model_version=model.artifact.model_version,
      expected_revision=model.authorization.registry_authorization_revision,
      stage="SUSPENDED", actor_id="reviewer", reason="fixture revoke", now=NOW,
    )
  result = await scorer.evaluate((feature,), **args)
  assert result.reason == "MODEL_BATCH_UNAVAILABLE" and result.revision == 1
  assert result.entry_blocked == (mode == "ACTIVE") and result.execution_rule_order == ("b", "a")
  assert not result.active_scores and not result.shadow_scores


@pytest.mark.parametrize("failure", ["commit", "cancel"])
@pytest.mark.parametrize("primed", [False, True])
async def test_uncommitted_score_is_never_visible(sessions, tmp_path, failure, primed):
  _, scorer = await registered(sessions, tmp_path, "ACTIVE")
  feature = bar()
  if primed:
    assert (await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=())).revision == 1
  evaluated = asyncio.Event()
  finish = asyncio.Event()

  @asynccontextmanager
  async def failing_session():
    async with sessions() as db:
      class Session:
        @asynccontextmanager
        async def begin(self):
          async with db.begin():
            yield
            evaluated.set()
            await finish.wait()
            raise RuntimeError("synthetic commit failure")

        def __getattr__(self, key):
          return getattr(db, key)
      yield Session()

  scorer._sessions = failing_session
  task = asyncio.create_task(scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms + 1, rule_order=()))
  await asyncio.wait_for(evaluated.wait(), 2)
  assert scorer.latest.revision == int(primed)
  assert bool(scorer.latest.active_scores) == primed
  if failure == "cancel":
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task
  else:
    finish.set()
    await task
  assert scorer.latest.reason == "MODEL_BATCH_UNAVAILABLE" and scorer.latest.revision == int(primed)
  assert scorer.latest.entry_blocked and not scorer.latest.active_scores


async def test_concurrent_batches_publish_monotonic_revisions(sessions, tmp_path):
  _, scorer = await registered(sessions, tmp_path, "ACTIVE")
  feature = bar()
  results = await asyncio.gather(*[
    scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms + index, rule_order=())
    for index in range(3)
  ])
  assert [result.revision for result in results] == [1, 2, 3]
  assert all(result.reason == "VALID" for result in results)
  assert scorer.latest.revision == 3


async def test_rule_only_never_opens_registry(tmp_path):
  from quantx_engine.t_model_batch_runtime import TModelBatchRuntime

  def unavailable():
    raise AssertionError("RULE_ONLY accessed registry")

  model = TModelBatchRuntime(mode="RULE_ONLY", artifact=None, authorization=None,
    policy_hash="a" * 64, max_age_ms=1, inference_budget_ms=1)
  scorer = TRegistryModelBatchRuntime(model=model, session_factory=unavailable)
  assert (await scorer.evaluate((), model_as_of_ms=0, rule_order=("a",))).reason == "MODEL_OFF"


async def test_offline_cached_scores_are_not_initial_registry_authority(tmp_path):
  model, feature = runtime(tmp_path, "ACTIVE"), bar()
  assert model.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=()).reason == "VALID"

  def unavailable():
    raise RuntimeError("registry unavailable")

  scorer = TRegistryModelBatchRuntime(model=model, session_factory=unavailable)
  assert scorer.latest.reason == "COLD" and not scorer.latest.active_scores
  result = await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=())
  assert result.entry_blocked and result.reason == "MODEL_BATCH_UNAVAILABLE" and result.revision == 0
  assert not result.active_scores
  assert model.latest.reason == "VALID"  # No mutation of the caller's offline state.
