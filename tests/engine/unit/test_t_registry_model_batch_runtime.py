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
  return model, TRegistryModelBatchRuntime(model=model, session_factory=sessions, clock_ms=lambda: bar().available_at_ms + 10)


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
  scorer = TRegistryModelBatchRuntime(model=model, session_factory=unavailable, clock_ms=lambda: bar().available_at_ms + 10)
  assert (await scorer.evaluate((), model_as_of_ms=0, rule_order=("a",))).reason == "MODEL_OFF"


async def test_offline_cached_scores_are_not_initial_registry_authority(tmp_path):
  model, feature = runtime(tmp_path, "ACTIVE"), bar()
  assert model.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=()).reason == "VALID"

  def unavailable():
    raise RuntimeError("registry unavailable")

  scorer = TRegistryModelBatchRuntime(model=model, session_factory=unavailable, clock_ms=lambda: bar().available_at_ms + 10)
  assert scorer.latest.reason == "COLD" and not scorer.latest.active_scores
  result = await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=())
  assert result.entry_blocked and result.reason == "MODEL_BATCH_UNAVAILABLE" and result.revision == 0
  assert not result.active_scores
  assert model.latest.reason == "VALID"  # No mutation of the caller's offline state.


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
async def test_publication_time_is_after_commit_and_replay_does_not_rejuvenate(sessions, tmp_path, mode):
  _, scorer = await registered(sessions, tmp_path, mode)
  feature = bar()
  now = feature.available_at_ms
  scorer._clock_ms = lambda: now

  @asynccontextmanager
  async def delayed_commit():
    nonlocal now
    async with sessions() as db:
      class Session:
        @asynccontextmanager
        async def begin(self):
          nonlocal now
          async with db.begin():
            yield
            now += 25
        def __getattr__(self, key):
          return getattr(db, key)
      yield Session()

  scorer._sessions = delayed_commit
  first = await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=())
  scores = first.active_scores + first.shadow_scores
  assert first.reason == "VALID" and scores[0].model_as_of_ms == feature.available_at_ms + 25
  replay = await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=("new-rule-order",))
  assert replay.revision == first.revision and replay.manifest_hash == first.manifest_hash
  assert replay.active_scores + replay.shadow_scores == scores
  assert now == feature.available_at_ms + 50
  assert replay.execution_rule_order == ("new-rule-order",)


@pytest.mark.parametrize("clock_value", ["backward", "expired", "invalid"])
@pytest.mark.parametrize("recompute", [False, True])
async def test_publication_clock_failure_clears_previous_cache(sessions, tmp_path, clock_value, recompute):
  _, scorer = await registered(sessions, tmp_path, "ACTIVE")
  feature = bar()
  args = dict(model_as_of_ms=feature.available_at_ms, rule_order=())
  first = await scorer.evaluate((feature,), **args)
  assert first.revision == 1
  if clock_value == "backward":
    # Still later than the request, but earlier than the prior visible outcome.
    value = feature.available_at_ms + 5
  elif clock_value == "expired":
    value = feature.interval_end_ms + 120001
  else:
    value = True
  scorer._clock_ms = lambda: value
  if recompute:
    args["model_as_of_ms"] += 1
  result = await scorer.evaluate((feature,), **args)
  assert result.reason == "MODEL_BATCH_UNAVAILABLE" and result.revision == 1
  assert result.entry_blocked and not result.active_scores


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
@pytest.mark.parametrize("damage", ["future", "stale", "universe", "revoke"])
async def test_snapshot_rechecks_visibility_and_registry(sessions, tmp_path, mode, damage):
  model, scorer = await registered(sessions, tmp_path, mode)
  feature = bar()
  batch = await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=())
  as_of = feature.available_at_ms + 10
  codes = (feature.instrument_code,)
  args = dict(as_of_ms=as_of, instrument_codes=codes, rule_order=("current-rule-order",))
  frozen = await scorer.freeze_for_snapshot(**args)
  assert frozen.manifest_hash == batch.manifest_hash and frozen.reason == "VALID"
  assert frozen.execution_rule_order == ("current-rule-order",)
  if damage == "future":
    args["as_of_ms"] -= 1
  elif damage == "stale":
    args["as_of_ms"] += model.max_age_ms + 1
  elif damage == "universe":
    args["instrument_codes"] += ("000001.SZ",)
  else:
    async with sessions() as db, db.begin():
      await TModelRegistryRepository(db).set_stage(
        model_id=model.artifact.model_id, model_version=model.artifact.model_version,
        expected_revision=model.authorization.registry_authorization_revision,
        stage="SUSPENDED", actor_id="reviewer", reason="snapshot revoke", now=NOW,
      )
  unavailable = await scorer.freeze_for_snapshot(**args)
  assert unavailable.reason == "MODEL_SNAPSHOT_UNAVAILABLE"
  assert unavailable.entry_blocked == (mode == "ACTIVE")
  assert not unavailable.active_scores and not unavailable.shadow_scores
  assert frozen.reason == "VALID" and frozen.manifest_hash == batch.manifest_hash


async def test_snapshot_does_not_adopt_batch_published_during_registry_read(sessions, tmp_path, monkeypatch):
  _, scorer = await registered(sessions, tmp_path, "ACTIVE")
  feature = bar()
  first = await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=())
  entered, release = asyncio.Event(), asyncio.Event()
  original = TModelRegistryRepository.authorize

  async def blocked(repo, **kwargs):
    if asyncio.current_task().get_name() == "snapshot-read":
      entered.set()
      await release.wait()
    return await original(repo, **kwargs)

  monkeypatch.setattr(TModelRegistryRepository, "authorize", blocked)
  pending = asyncio.create_task(scorer.freeze_for_snapshot(
    as_of_ms=feature.available_at_ms + 10, instrument_codes=(feature.instrument_code,), rule_order=(),
  ), name="snapshot-read")
  try:
    await asyncio.wait_for(entered.wait(), 2)
    scorer._clock_ms = lambda: feature.available_at_ms + 20
    second = await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms + 1, rule_order=())
    assert second.revision == 2
    release.set()
    frozen = await asyncio.wait_for(pending, 2)
    assert frozen.revision == 1 and frozen.manifest_hash == first.manifest_hash
    assert scorer.latest.revision == 2
  finally:
    if not pending.done():
      pending.cancel()
      await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
async def test_real_minute_batch_publishes_missing_outcomes_without_old_score_reuse(sessions, tmp_path, monkeypatch, mode):
  from quantx_application.t_trade_v3.model_minute_batch import TModelMinuteBatchRuntime

  from tests.engine.unit.test_t_model_minute_batch import CODES, close, config, feed
  from tests.research.test_t_assistant_model_data import START

  model, scorer = await registered(sessions, tmp_path, mode)
  minutes = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  for code in CODES:
    feed(minutes, code)
  first_batch = close(minutes)
  first = await scorer.evaluate_minute(first_batch, rule_order=("rule-1",))
  assert first.reason == "VALID" and first.revision == 1 and not first.unavailable
  assert len(first.active_scores + first.shadow_scores) == 3
  minutes.advance(instrument_codes=CODES, **config(START + 60000))
  for code in CODES[:2]:
    feed(minutes, code, 60000)
  second_batch = close(minutes, START + 60000)
  scorer._clock_ms = lambda: second_batch.available_at_ms + 10
  second = await scorer.evaluate_minute(second_batch, rule_order=("rule-2",))
  assert second.reason == "VALID" and second.revision == 2
  assert [item.instrument_code for item in second.unavailable] == [CODES[2]]
  assert len(second.active_scores + second.shadow_scores) == 2
  replay = await scorer.evaluate_minute(second_batch, rule_order=("current-rule",))
  assert replay.revision == 2 and replay.manifest_hash == second.manifest_hash
  frozen = await scorer.freeze_for_snapshot(as_of_ms=second_batch.available_at_ms + 10,
    instrument_codes=CODES, rule_order=("current-rule",))
  assert frozen.reason == "VALID" and frozen.unavailable == second.unavailable
  assert frozen.execution_rule_order == ("current-rule",)
  minutes.advance(instrument_codes=CODES, **config(START + 120000))
  third_batch = close(minutes, START + 120000)
  scorer._clock_ms = lambda: third_batch.available_at_ms + 10

  def forbidden(*args, **kwargs):
    raise AssertionError("no feature should enter inference")

  monkeypatch.setattr(type(model.artifact), "score", forbidden)
  third = await scorer.evaluate_minute(third_batch, rule_order=())
  assert third.reason == "VALID" and third.revision == 3 and len(third.unavailable) == 3
  assert not third.active_scores and not third.shadow_scores
  assert third.entry_blocked == (mode == "ACTIVE")
  assert third.model_as_of_ms == third_batch.available_at_ms + 10
  frozen = await scorer.freeze_for_snapshot(as_of_ms=third.model_as_of_ms,
    instrument_codes=CODES, rule_order=())
  assert frozen.revision == 3 and len(frozen.unavailable) == 3 and frozen.reason == "VALID"


@pytest.mark.parametrize("damage", ["manifest", "missing", "bar_coordinate", "watermark", "supplied_bars"])
async def test_minute_batch_validation_rejects_corruption(sessions, tmp_path, damage):
  from dataclasses import asdict

  from quantx_application.t_trade_v3.model_minute_batch import TModelMinuteBatchRuntime
  from quantx_domain.trading.t_assistant_execution import stable_manifest_hash

  from tests.engine.unit.test_t_model_minute_batch import CODES, close, config, feed

  _, scorer = await registered(sessions, tmp_path, "ACTIVE")
  minutes = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  feed(minutes, CODES[0])
  batch = close(minutes)
  first = await scorer.evaluate_minute(batch, rule_order=())
  assert first.reason == "VALID" and first.revision == 1
  if damage == "supplied_bars":
    broken = batch
  elif damage == "manifest":
    broken = replace(batch, manifest_hash="f" * 64)
  else:
    if damage == "missing":
      broken = replace(batch, outcomes=batch.outcomes[:-1])
    elif damage == "watermark":
      broken = replace(batch, watermark_ms=batch.interval_end_ms - 1)
    else:
      outcomes = tuple(replace(item, feature_bar=replace(item.feature_bar, stream_id="other")) if item.feature_bar else item for item in batch.outcomes)
      broken = replace(batch, outcomes=outcomes)
    material = asdict(broken)
    material.pop("manifest_hash")
    broken = replace(broken, manifest_hash=stable_manifest_hash(material))
  if damage == "supplied_bars":
    result = await scorer.evaluate((), model_as_of_ms=batch.available_at_ms, rule_order=(), minute_batch=batch)
  else:
    result = await scorer.evaluate_minute(broken, rule_order=())
  assert result.reason == "MODEL_BATCH_UNAVAILABLE" and result.revision == 1
  assert result.entry_blocked and not result.active_scores and not result.unavailable
