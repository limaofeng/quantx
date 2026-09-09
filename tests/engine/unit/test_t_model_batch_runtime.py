"""Mode isolation and atomic model failure handling without a trading runtime."""

from dataclasses import replace

import pytest
from quantx_engine.t_model_batch_runtime import TModelAuthorization, TModelBatchRuntime
from quantx_research.t_model_export import publish_immutable_cpu_artifact

from tests.research.test_t_assistant_model_data import bar
from tests.research.test_t_model_cpu_artifact import payload


def runtime(tmp_path, mode):
  artifact = publish_immutable_cpu_artifact(tmp_path / "model.json", payload())
  auth = TModelAuthorization(artifact.sha256, "a" * 64, mode, 1, f"{mode}_ELIGIBLE")
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
    model.artifact = replace(model.artifact, feature_bounds=((0, 0),) * 6)
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
