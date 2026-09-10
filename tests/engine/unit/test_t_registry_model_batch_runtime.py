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


async def registered(sessions, tmp_path, mode, *, budget=10000):
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
  material = model.authorization.runtime_binding.to_dict()
  material.pop("binding_hash")
  material["registry_authorization_revision"] = revision
  frozen = type(model.authorization.runtime_binding).create(**material)
  model = type(model)(mode=mode, artifact=artifact,
    authorization=replace(model.authorization, registry_authorization_revision=revision, runtime_binding=frozen),
    policy_hash=model.policy_hash, max_age_ms=model.max_age_ms, inference_budget_ms=budget)
  return model, TRegistryModelBatchRuntime(model=model, session_factory=sessions, clock_ms=lambda: bar().available_at_ms + 10)


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
async def test_typed_snapshot_validates_frozen_score_identity(sessions, tmp_path, mode):
  model, scorer = await registered(sessions, tmp_path, mode)
  feature = bar()
  await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=())
  view = await scorer.snapshot_view(as_of_ms=feature.available_at_ms + 10,
    instrument_codes=(feature.instrument_code,), rule_order=())
  scope = dict(mode=mode, binding_hash=model.authorization.runtime_binding.binding_hash,
    feature_schema_version=feature.feature_schema_version, as_of_ms=feature.available_at_ms + 10,
    instrument_codes=(feature.instrument_code,))
  view.validate_for_snapshot(**scope)
  assert view.status == "VALID" and len(view.scores) == 1
  score = view.scores[0]
  for changed in (
    replace(view, revision=view.revision + 1),
    replace(view, scores=()),
    replace(view, model_as_of_ms=scope["as_of_ms"] + 1),
    replace(view, max_age_ms=None),
    replace(view, scores=(replace(score, model_authorization_revision=99),)),
    replace(view, scores=(replace(score, artifact_manifest_sha256="f" * 64),)),
    replace(view, scores=(replace(score, model_version="wrong"),)),
  ):
    with pytest.raises(ValueError, match="T_MODEL_SNAPSHOT_"):
      changed.validate_for_snapshot(**scope)
  for changed_scope in (scope | {"binding_hash": "f" * 64},
    scope | {"as_of_ms": scope["as_of_ms"] + model.max_age_ms + 1}):
    with pytest.raises(ValueError, match="T_MODEL_SNAPSHOT_"):
      view.validate_for_snapshot(**changed_scope)


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
  original = TModelRegistryRepository.read_snapshot_authorization

  async def blocked(repo, **kwargs):
    if asyncio.current_task().get_name() == "snapshot-read":
      entered.set()
      await release.wait()
    return await original(repo, **kwargs)

  monkeypatch.setattr(TModelRegistryRepository, "read_snapshot_authorization", blocked)
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


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
@pytest.mark.parametrize("newer", ["missing", "backend_failure"])
async def test_old_minute_cannot_reappear_after_newer_unavailable(sessions, tmp_path, monkeypatch, mode, newer):
  from quantx_application.t_trade_v3.model_minute_batch import TModelMinuteBatchRuntime

  from tests.engine.unit.test_t_model_minute_batch import CODES, close, config, feed
  from tests.research.test_t_assistant_model_data import START

  model, scorer = await registered(sessions, tmp_path, mode)
  minutes = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  for code in CODES:
    feed(minutes, code)
  old = close(minutes)
  assert (await scorer.evaluate_minute(old, rule_order=())).revision == 1
  minutes.advance(instrument_codes=CODES, **config(START + 60000))
  if newer == "backend_failure":
    for code in CODES:
      feed(minutes, code, 60000)
  new = close(minutes, START + 60000)
  scorer._clock_ms = lambda: new.available_at_ms + 10
  original = type(model.artifact).score
  if newer == "backend_failure":
    def fail(*args, **kwargs):
      raise RuntimeError("synthetic inference failure")
    monkeypatch.setattr(type(model.artifact), "score", fail)
  second = await scorer.evaluate_minute(new, rule_order=())
  assert not second.active_scores and not second.shadow_scores
  monkeypatch.setattr(type(model.artifact), "score", original)
  for _ in range(2):
    replay = await scorer.evaluate_minute(old, rule_order=("current-rule",))
    assert replay.reason == "MODEL_BATCH_UNAVAILABLE" and replay.revision == second.revision
    assert replay.entry_blocked == (mode == "ACTIVE")
    assert not replay.active_scores and not replay.shadow_scores
  recovered = await scorer.evaluate_minute(new, rule_order=())
  assert recovered.reason == "VALID" and recovered.revision == second.revision + 1


@pytest.mark.parametrize("damage", ["repaired", "bare_bars"])
async def test_sealed_minute_cannot_be_repaired_or_bypass_manifest(sessions, tmp_path, damage):
  from quantx_application.t_trade_v3.model_minute_batch import TModelMinuteBatchRuntime

  from tests.engine.unit.test_t_model_minute_batch import CODES, close, config, feed

  _, scorer = await registered(sessions, tmp_path, "ACTIVE")
  empty = close(TModelMinuteBatchRuntime(instrument_codes=CODES, **config()))
  first = await scorer.evaluate_minute(empty, rule_order=())
  assert first.reason == "VALID" and len(first.unavailable) == 3
  repaired = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  for code in CODES:
    feed(repaired, code)
  complete = close(repaired)
  if damage == "repaired":
    result = await scorer.evaluate_minute(complete, rule_order=())
  else:
    result = await scorer.evaluate(complete.complete_bars, model_as_of_ms=complete.available_at_ms, rule_order=())
  assert result.reason == "MODEL_BATCH_UNAVAILABLE" and result.revision == first.revision
  assert result.entry_blocked and not result.active_scores


@pytest.mark.parametrize("damage", ["scope", "future", "bar_mismatch"])
async def test_first_minute_rejects_rehashed_context_corruption(sessions, tmp_path, damage):
  from dataclasses import asdict

  from quantx_application.t_trade_v3.model_minute_batch import TModelMinuteBatchRuntime
  from quantx_domain.trading.t_assistant_execution import stable_manifest_hash

  from tests.engine.unit.test_t_model_minute_batch import CODES, close, config, feed

  _, scorer = await registered(sessions, tmp_path, "ACTIVE")
  minutes = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  feed(minutes, CODES[0])
  batch = close(minutes)
  contexts = dict(batch.contexts)
  if damage == "scope":
    contexts.pop(CODES[0])
  else:
    context = contexts[CODES[0]]
    contexts[CODES[0]] = replace(context, sector_context_as_of_ms=(
      batch.interval_start_ms + 1 if damage == "future" else batch.interval_start_ms - 1))
  broken = replace(batch, contexts=tuple(contexts.items()))
  material = asdict(broken)
  material.pop("manifest_hash")
  broken = replace(broken, manifest_hash=stable_manifest_hash(material))
  result = await scorer.evaluate_minute(broken, rule_order=())
  assert result.reason == "MODEL_BATCH_UNAVAILABLE" and result.revision == 0
  assert not result.active_scores and scorer._minute_input_fence is None


async def test_accepted_minute_boundary_reaches_registered_cpu_and_snapshot(sessions, tmp_path):
  from quantx_application.t_trade_v3.model_minute_batch import TModelMinuteBatchRuntime

  from tests.engine.unit.test_t_model_minute_batch import (
    CODES,
    boundaries,
    config,
    feed,
  )
  from tests.research.test_t_assistant_model_data import START

  _, scorer = await registered(sessions, tmp_path, "SHADOW")
  scorer._clock_ms = lambda: START + 60020
  minutes = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  for code in CODES:
    feed(minutes, code)
  batch = minutes.seal_from_accepted_ticks(boundaries())
  result = await scorer.evaluate_minute(batch, rule_order=("unchanged",))
  assert result.reason == "VALID" and len(result.shadow_scores) == 3
  view = await scorer.snapshot_view(as_of_ms=START + 60020, instrument_codes=CODES, rule_order=())
  assert view.status == "VALID" and view.revision == result.revision
  assert {score.instrument_code for score in view.scores} == set(CODES)
  assert all(score.model_as_of_ms >= batch.available_at_ms for score in view.scores)


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
async def test_cpu_call_does_not_block_loop_or_overlap_after_abandonment(sessions, tmp_path, monkeypatch, failure):
  import threading

  model, scorer = await registered(sessions, tmp_path, "ACTIVE", budget=100 if failure == "timeout" else 10000)
  feature = bar()
  args = dict(model_as_of_ms=feature.available_at_ms, rule_order=())
  first = await scorer.evaluate((feature,), **args)
  assert first.reason == "VALID"
  entered = asyncio.Event()
  release = threading.Event()
  loop = asyncio.get_running_loop()
  original = type(model.artifact).score
  calls = []

  def blocked(artifact, bar, **kwargs):
    calls.append(bar.instrument_code)
    loop.call_soon_threadsafe(entered.set)
    assert release.wait(5), "test failed to release CPU worker"
    return original(artifact, bar, **kwargs)

  monkeypatch.setattr(type(model.artifact), "score", blocked)
  args["model_as_of_ms"] += 1
  evaluation = asyncio.create_task(scorer.evaluate((feature,), **args))
  try:
    await asyncio.wait_for(entered.wait(), timeout=2)
    # This coroutine can run while native inference remains blocked.
    assert not release.is_set() and not scorer._inference_task.done()
    if failure == "cancel":
      evaluation.cancel()
      with pytest.raises(asyncio.CancelledError):
        await evaluation
    else:
      result = await asyncio.wait_for(evaluation, timeout=2)
      assert result.reason == "MODEL_BATCH_UNAVAILABLE"
    assert not scorer.latest.active_scores and scorer.latest.revision == first.revision
    old_task = scorer._inference_task
    rejected = await scorer.evaluate((feature,), **args)
    assert rejected.reason == "MODEL_BATCH_UNAVAILABLE" and len(calls) == 1
    assert scorer._inference_task is old_task and not old_task.done()
    release.set()
    await asyncio.wait_for(asyncio.shield(old_task), timeout=2)
    assert not scorer.latest.active_scores  # A late worker cannot publish itself.
    retry = await scorer.evaluate((feature,), **args)
    assert retry.reason == "VALID" and retry.revision == first.revision + 1
    assert len(calls) == 2
  finally:
    release.set()
    if not evaluation.done():
      evaluation.cancel()
    await asyncio.gather(evaluation, return_exceptions=True)
    if scorer._inference_task is not None:
      await asyncio.shield(scorer._inference_task)


async def test_busy_worker_still_records_newer_minute_input_fence(sessions, tmp_path):
  from quantx_application.t_trade_v3.model_minute_batch import TModelMinuteBatchRuntime

  from tests.engine.unit.test_t_model_minute_batch import CODES, close, config
  from tests.research.test_t_assistant_model_data import START

  _, scorer = await registered(sessions, tmp_path, "ACTIVE")
  minute = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  older = close(minute)
  minute.advance(instrument_codes=CODES, **config(START + 60000))
  newer = close(minute, START + 60000)
  pending = asyncio.get_running_loop().create_future()
  scorer._inference_task = pending
  try:
    assert (await scorer.evaluate_minute(newer, rule_order=())).reason == "MODEL_BATCH_UNAVAILABLE"
    assert scorer._minute_input_fence == (newer.interval_start_ms, newer.manifest_hash)
  finally:
    pending.set_result(None)
  assert (await scorer.evaluate_minute(older, rule_order=())).reason == "MODEL_BATCH_UNAVAILABLE"
  scorer._clock_ms = lambda: newer.available_at_ms + 1
  assert (await scorer.evaluate_minute(newer, rule_order=())).reason == "VALID"


async def test_snapshot_reads_previous_batch_while_cpu_is_running(sessions, tmp_path, monkeypatch):
  import threading

  model, scorer = await registered(sessions, tmp_path, "SHADOW")
  feature = bar()
  first = await scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=())
  entered = asyncio.Event()
  release = threading.Event()
  loop = asyncio.get_running_loop()
  original = type(model.artifact).score
  def slow(artifact, value, **kwargs):
    loop.call_soon_threadsafe(entered.set)
    assert release.wait(5)
    return original(artifact, value, **kwargs)
  monkeypatch.setattr(type(model.artifact), "score", slow)
  pending = asyncio.create_task(scorer.evaluate((feature,), model_as_of_ms=feature.available_at_ms + 1, rule_order=()))
  try:
    await asyncio.wait_for(entered.wait(), 2)
    frozen = await asyncio.wait_for(scorer.snapshot_view(as_of_ms=feature.available_at_ms + 10,
      instrument_codes=(feature.instrument_code,), rule_order=()), 2)
    assert frozen.status == "VALID" and frozen.revision == first.revision
    assert not pending.done() and not release.is_set()
  finally:
    release.set()
    await pending
  assert scorer.latest.revision == first.revision + 1
  assert frozen.revision == first.revision
