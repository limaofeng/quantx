"""Mode isolation and atomic model failure handling without a trading runtime."""

from dataclasses import replace

import pytest
from quantx_engine.t_model_batch_runtime import TModelAuthorization, TModelBatchRuntime
from quantx_research.t_model_export import publish_immutable_cpu_artifact

from tests.research.test_t_assistant_model_data import bar
from tests.research.test_t_model_cpu_artifact import payload


def runtime(tmp_path, mode):
  artifact = publish_immutable_cpu_artifact(tmp_path / "model.json", payload())
  from tests.domain.test_t_model_runtime_binding import binding

  frozen = binding(mode, model_id=artifact.model_id, model_version=artifact.model_version,
    artifact_manifest_sha256=artifact.sha256, portfolio_policy_compatibility_hash="a" * 64,
    label_spec_version=artifact.label_spec_version, calibration_version=artifact.calibration_version)
  auth = TModelAuthorization(artifact.sha256, "a" * 64, mode, 1, f"{mode}_ELIGIBLE", frozen)
  return TModelBatchRuntime(
    mode=mode,
    artifact=artifact,
    authorization=auth,
    policy_hash="a" * 64,
    max_age_ms=120000,
    inference_budget_ms=10000,
  )


def test_rule_only_does_not_require_features_or_load_model():
  model = TModelBatchRuntime(
    mode="RULE_ONLY",
    artifact=None,
    authorization=None,
    policy_hash="a" * 64,
    max_age_ms=1,
    inference_budget_ms=1,
  )
  assert model.evaluate(
    (), model_as_of_ms=0, rule_order=("b", "a")
  ).execution_rule_order == ("b", "a")
  assert model.latest.reason == "MODEL_OFF" and not model.latest.entry_blocked


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
def test_identical_batch_reuses_revision_but_keeps_current_rule_order(tmp_path, monkeypatch, mode):
  model, feature = runtime(tmp_path, mode), bar()
  original = type(model.artifact).score
  calls = []

  def score(artifact, feature, **kwargs):
    calls.append(feature.instrument_code)
    return original(artifact, feature, **kwargs)

  monkeypatch.setattr(type(model.artifact), "score", score)
  first = model.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=("a", "b"))
  replay = model.evaluate((feature,), model_as_of_ms=feature.available_at_ms, rule_order=("b", "a"))
  assert first.reason == replay.reason == "VALID"
  assert first.revision == replay.revision == 1 and first.manifest_hash == replay.manifest_hash
  assert replay.execution_rule_order == ("b", "a") and len(calls) == 1
  fresh = model.evaluate((feature,), model_as_of_ms=feature.available_at_ms + 1, rule_order=("a",))
  assert fresh.revision == 2 and len(calls) == 2
  model.authorization = replace(model.authorization, gate_conclusion="BLOCKED")
  failed = model.evaluate((feature,), model_as_of_ms=feature.available_at_ms + 1, rule_order=("a",))
  assert failed.reason == "MODEL_BATCH_UNAVAILABLE" and not failed.active_scores and not failed.shadow_scores
  model.authorization = replace(model.authorization, gate_conclusion=f"{mode}_ELIGIBLE")
  restored = model.evaluate((feature,), model_as_of_ms=feature.available_at_ms + 1, rule_order=("a",))
  assert restored.revision == 3 and len(calls) == 3


@pytest.mark.parametrize("field,value", [
  ("interval_start_ms", 0), ("stream_id", "other-stream"),
  ("continuity_generation", "other-generation"), ("market_session", "other-session"),
])
def test_batch_rejects_mixed_minute_or_stream_coordinates(tmp_path, field, value):
  model, feature = runtime(tmp_path, "ACTIVE"), bar()
  other = replace(feature, instrument_code="000001.SZ", feature_bar_id="other-bar", **{field: value})
  result = model.evaluate((feature, other), model_as_of_ms=feature.available_at_ms, rule_order=())
  assert result.entry_blocked and result.revision == 0 and not result.active_scores


@pytest.mark.parametrize("damage", ["artifact", "features", "future", "budget"])
def test_cache_never_hides_changed_artifact_features_or_budget(tmp_path, monkeypatch, damage):
  model, feature = runtime(tmp_path, "ACTIVE"), bar()
  at = feature.available_at_ms
  assert model.evaluate((feature,), model_as_of_ms=at, rule_order=()).reason == "VALID"
  if damage == "artifact":
    model.artifact = replace(model.artifact, feature_bounds=((0, 0),) * 6)
  elif damage == "features":
    feature = replace(feature, feature_values=(float("nan"),) * 6)
  elif damage == "future":
    feature = replace(feature, available_at_ms=at + 1)
  else:
    times = iter((0, 100_000_000_000))
    monkeypatch.setattr("quantx_engine.t_model_batch_runtime.perf_counter_ns", lambda: next(times))
  result = model.evaluate((feature,), model_as_of_ms=at, rule_order=())
  assert result.entry_blocked and result.revision == 1 and not result.active_scores


def test_duplicate_feature_identity_cannot_publish_two_score_owners(tmp_path):
  model, feature = runtime(tmp_path, "ACTIVE"), bar()
  result = model.evaluate(
    (feature, replace(feature, instrument_code="000001.SZ")),
    model_as_of_ms=feature.available_at_ms, rule_order=(),
  )
  assert result.entry_blocked and not result.active_scores and result.revision == 0


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
def test_mode_batch_is_atomic_and_never_reuses_old_scores(tmp_path, mode):
  model, feature = runtime(tmp_path, mode), bar()
  first = model.evaluate(
    (feature,), model_as_of_ms=feature.available_at_ms, rule_order=("b", "a")
  )
  assert first.revision == 1 and first.execution_rule_order == ("b", "a")
  assert bool(first.active_scores) == (mode == "ACTIVE")
  assert bool(first.shadow_scores) == (mode == "SHADOW")
  broken = replace(
    feature, instrument_code="000001.SZ", feature_values=(float("nan"),) * 6
  )
  second = model.evaluate(
    (feature, broken), model_as_of_ms=feature.available_at_ms, rule_order=("b", "a")
  )
  assert second.revision == 1 and second.entry_blocked == (mode == "ACTIVE")
  assert not second.active_scores and not second.shadow_scores


@pytest.mark.parametrize(
  "damage", ["authorization", "missing", "stale", "ood", "latency"]
)
def test_active_failure_blocks_only_model_entry_result(tmp_path, monkeypatch, damage):
  model, feature = runtime(tmp_path, "ACTIVE"), bar()
  at = feature.available_at_ms
  if damage == "authorization":
    model.authorization = replace(model.authorization, gate_conclusion="BLOCKED")
  if damage == "missing":
    model.artifact = None
  if damage == "stale":
    at += 120001
  if damage == "ood":
    model = TModelBatchRuntime(
      mode="ACTIVE", artifact=replace(model.artifact, feature_bounds=((0, 0),) * 6),
      authorization=model.authorization, policy_hash=model.policy_hash,
      max_age_ms=model.max_age_ms, inference_budget_ms=model.budget_ms,
    )
  if damage == "latency":
    times = iter((0, 100_000_000_000))
    monkeypatch.setattr(
      "quantx_engine.t_model_batch_runtime.perf_counter_ns", lambda: next(times)
    )
  result = model.evaluate((feature,), model_as_of_ms=at, rule_order=("intent-1",))
  assert result.entry_blocked and not result.active_scores and result.revision == 0


def test_shadow_missing_model_does_not_block_rule_entries():
  model = TModelBatchRuntime(
    mode="SHADOW",
    artifact=None,
    authorization=None,
    policy_hash="a" * 64,
    max_age_ms=1,
    inference_budget_ms=1,
  )
  result = model.evaluate((), model_as_of_ms=0, rule_order=("intent-1",))
  assert not result.entry_blocked and result.execution_rule_order == ("intent-1",)


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
def test_unexpected_scorer_failure_stays_inside_model_boundary(
  tmp_path, monkeypatch, mode
):
  model, feature = runtime(tmp_path, mode), bar()

  def fail(*args, **kwargs):
    raise RuntimeError("synthetic backend failure")

  monkeypatch.setattr(type(model.artifact), "score", fail)
  result = model.evaluate(
    (feature,), model_as_of_ms=feature.available_at_ms, rule_order=("intent-1",)
  )
  assert result.entry_blocked == (mode == "ACTIVE")
  assert not result.active_scores and result.revision == 0


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
@pytest.mark.parametrize("change", ["revision", "revoke", "artifact", "mode", "switch_mode", "policy", "budget"])
def test_inflight_binding_change_never_publishes_old_batch(tmp_path, monkeypatch, mode, change):
  model, feature = runtime(tmp_path, mode), bar()
  at = feature.available_at_ms
  first = model.evaluate((feature,), model_as_of_ms=at, rule_order=("b", "a"))
  assert first.revision == 1 and first.reason == "VALID"
  original = type(model.artifact).score

  def score(artifact, feature, **kwargs):
    result = original(artifact, feature, **kwargs)
    if change == "revision":
      model.authorization = replace(model.authorization, registry_authorization_revision=2)
    elif change == "revoke":
      model.authorization = replace(model.authorization, registry_stage="SUSPENDED")
    elif change == "artifact":
      model.artifact = replace(artifact, model_version="replaced")
    elif change == "mode":
      model.mode = "RULE_ONLY"
    elif change == "switch_mode":
      model.mode = "ACTIVE" if mode == "SHADOW" else "SHADOW"
    elif change == "policy":
      model.policy_hash = "b" * 64
    else:
      model.budget_ms += 1
    return result

  monkeypatch.setattr(type(model.artifact), "score", score)
  result = model.evaluate((feature,), model_as_of_ms=at + 1, rule_order=("a", "b"))
  assert result.revision == 1 and result.reason == "MODEL_BATCH_UNAVAILABLE"
  assert result.entry_blocked == (mode == "ACTIVE")
  assert result.execution_rule_order == ("a", "b")
  assert not result.active_scores and not result.shadow_scores and not result.manifest_hash
  assert model._cache_key is None and model._cached_artifact is None

  # A following batch cannot silently adopt a new mode, artifact or revision.
  again = model.evaluate((feature,), model_as_of_ms=at + 2, rule_order=("a", "b"))
  assert again.reason == "MODEL_BATCH_UNAVAILABLE" and again.revision == 1
  assert again.entry_blocked == (mode == "ACTIVE")
  assert not again.active_scores and not again.shadow_scores
