"""Registry-authorized scoring; no model promotion or order execution.

The synchronous scorer evaluates a private copy. Only a successful registry
transaction may publish that copy. Snapshot/order consumers still need their
own current-authorization check at the point of use.
"""

import asyncio
import copy
from dataclasses import replace

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.repositories.t_model_registry_repository import (
  TModelRegistryRepository,
)

from .t_model_batch_runtime import TModelBatchResult, TModelBatchRuntime


class TRegistryModelBatchRuntime:
  def __init__(self, *, model: TModelBatchRuntime, session_factory, clock_ms):
    # Own the publication state; the caller's offline scorer cannot mutate it.
    self._model = copy.copy(model)
    self._model.revision = 0
    self._model._cache_key = self._model._cached_artifact = None
    self._model.latest = TModelBatchResult(
      0, "", model._bound_config[0] == "ACTIVE", "COLD", (), (), (),
    )
    self._clock_ms = clock_ms
    self._sessions = session_factory
    self._lock = asyncio.Lock()
    self._last_visibility_ms = None

  @property
  def latest(self):
    return self._model.latest

  async def evaluate(self, bars, *, model_as_of_ms, rule_order):
    async with self._lock:
      candidate = copy.copy(self._model)
      if candidate.mode == "RULE_ONLY":
        result = candidate.evaluate(bars, model_as_of_ms=model_as_of_ms, rule_order=rule_order)
        self._model = candidate
        return result
      try:
        artifact, auth = candidate.artifact, candidate.authorization
        if artifact is None or auth is None:
          raise ValueError("T_MODEL_BINDING_MISSING")
        async with self._sessions() as db, db.begin():
          current = await TModelRegistryRepository(db).authorize(
            model_id=artifact.model_id, model_version=artifact.model_version,
            expected_revision=auth.registry_authorization_revision, mode=candidate.mode,
            artifact_sha256=auth.artifact_sha256,
            policy_compatibility_hash=auth.policy_compatibility_hash,
          )
          if current.gate_conclusion != auth.gate_conclusion:
            raise ValueError("T_MODEL_AUTHORIZATION_CHANGED")
          # authorize holds the registry row through this transaction. Registry
          # stage changes use CAS on that same row and cannot pass the held lock.
          result = candidate.evaluate(bars, model_as_of_ms=model_as_of_ms, rule_order=rule_order)
        # Input model_as_of_ms is a lower bound. Visibility starts only after
        # the registry transaction has exited, never at minute end/request time.
        visible_at = self._clock_ms()
        if (
          type(visible_at) is not int or type(model_as_of_ms) is not int
          or visible_at < model_as_of_ms
          or (self._last_visibility_ms is not None and visible_at < self._last_visibility_ms)
          or any(visible_at - bar.interval_end_ms > candidate.max_age_ms for bar in bars)
          or any(visible_at < score.model_as_of_ms for score in result.active_scores + result.shadow_scores)
        ):
          raise ValueError("T_MODEL_PUBLICATION_TIME_INVALID")
        if result.reason == "VALID" and result.revision > self._model.revision:
          def stamp(score):
            return replace(score, model_as_of_ms=visible_at, score_id=stable_manifest_hash({
              "binding": score.model_runtime_binding_hash, "bar": score.source_feature_bar_id,
              "revision": score.score_cache_revision, "as_of_ms": visible_at,
            }))
          active = tuple(stamp(score) for score in result.active_scores)
          shadow = tuple(stamp(score) for score in result.shadow_scores)
          scores = active + shadow
          result = replace(result, active_scores=active, shadow_scores=shadow,
            manifest_hash=stable_manifest_hash({
              "revision": result.revision, "artifact": artifact.sha256,
              "authorization_revision": auth.registry_authorization_revision,
              "features": [score.source_feature_bar_id for score in scores],
              "model_as_of_ms": visible_at,
              "probabilities": [score.probabilities for score in scores],
            }))
          candidate.latest = result
        if result.reason == "VALID":
          self._last_visibility_ms = visible_at
        self._model = candidate
        return result
      except BaseException as exc:
        # A cancelled/failed transaction must not expose even a prior valid cache.
        self._model._cache_key = None
        self._model._cached_artifact = None
        self._model.latest = TModelBatchResult(
          self._model.revision, "", self._model._bound_config[0] == "ACTIVE",
          "MODEL_BATCH_UNAVAILABLE", rule_order, (), (),
        )
        if not isinstance(exc, Exception):
          raise
        return self.latest
