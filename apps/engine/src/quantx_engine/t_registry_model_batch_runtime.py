"""Registry-authorized scoring; no model promotion or order execution.

The synchronous scorer evaluates a private copy. Only a successful registry
transaction may publish that copy. Snapshot/order consumers still need their
own current-authorization check at the point of use.
"""

import asyncio
import copy
from dataclasses import asdict, replace

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
    self._minute_input_fence = None

  @property
  def latest(self):
    return self._model.latest

  async def evaluate_minute(self, batch, *, rule_order):
    return await self.evaluate(batch.complete_bars, model_as_of_ms=batch.available_at_ms,
      rule_order=rule_order, minute_batch=batch)

  async def evaluate(self, bars, *, model_as_of_ms, rule_order, minute_batch=None):
    async with self._lock:
      candidate = copy.copy(self._model)
      if candidate.mode == "RULE_ONLY":
        result = candidate.evaluate(bars, model_as_of_ms=model_as_of_ms, rule_order=rule_order)
        self._model = candidate
        return result
      try:
        unavailable = ()
        if minute_batch is not None:
          material = asdict(minute_batch)
          digest = material.pop("manifest_hash")
          outcomes = minute_batch.outcomes
          if (
            stable_manifest_hash(material) != digest
            or type(minute_batch.interval_start_ms) is not int
            or minute_batch.interval_start_ms < 0 or minute_batch.interval_start_ms % 60000
            or minute_batch.interval_end_ms != minute_batch.interval_start_ms + 60000
            or not minute_batch.universe
            or any(not isinstance(code, str) or not code or code != code.strip().upper() for code in minute_batch.universe)
            or tuple(bars) != minute_batch.complete_bars
            or type(minute_batch.watermark_ms) is not int
            or type(minute_batch.available_at_ms) is not int
            or not minute_batch.interval_end_ms <= minute_batch.watermark_ms <= minute_batch.available_at_ms
            or tuple(item.instrument_code for item in outcomes) != minute_batch.universe
            or len(set(minute_batch.universe)) != len(minute_batch.universe)
            or any((item.interval_start_ms, item.interval_end_ms) != (minute_batch.interval_start_ms, minute_batch.interval_end_ms) for item in outcomes)
            or any((item.status == "COMPLETE") != (item.feature_bar is not None) for item in outcomes)
            or any(item.status not in {"COMPLETE", "UNAVAILABLE"} for item in outcomes)
            or any(item.feature_bar is not None and (
              item.feature_bar.instrument_code != item.instrument_code
              or item.feature_bar.interval_start_ms != minute_batch.interval_start_ms
              or item.feature_bar.interval_end_ms != minute_batch.interval_end_ms
              or item.feature_bar.available_at_ms != minute_batch.available_at_ms
              or (item.feature_bar.stream_id, item.feature_bar.continuity_generation)
              != (minute_batch.stream_id, minute_batch.continuity_generation)
              or (minute_batch.watermark_stream_id, minute_batch.watermark_continuity_generation)
              != (minute_batch.stream_id, minute_batch.continuity_generation)
            ) for item in outcomes)
          ):
            raise ValueError("T_MODEL_MINUTE_MANIFEST_INVALID")
          unavailable = tuple(item for item in outcomes if item.status == "UNAVAILABLE")
          identity = (minute_batch.interval_start_ms, minute_batch.manifest_hash)
          previous = self._minute_input_fence
          if previous is not None and (
            identity[0] < previous[0] or (identity[0] == previous[0] and identity != previous)
          ):
            raise ValueError("T_MODEL_MINUTE_PUBLICATION_ORDER_INVALID")
          # Preserve the accepted input fence even if inference/authorization or
          # commit fails. Only this same sealed input or a later minute may retry.
          self._minute_input_fence = identity
        elif self._minute_input_fence is not None:
          raise ValueError("T_MODEL_MINUTE_MANIFEST_REQUIRED")
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
          result = candidate.evaluate(bars, model_as_of_ms=model_as_of_ms, rule_order=rule_order,
            unavailable=unavailable, input_manifest_hash=minute_batch.manifest_hash if minute_batch else None)
        # Input model_as_of_ms is a lower bound. Visibility starts only after
        # the registry transaction has exited, never at minute end/request time.
        visible_at = self._clock_ms()
        if (
          type(visible_at) is not int or type(model_as_of_ms) is not int
          or visible_at < model_as_of_ms
          or (self._last_visibility_ms is not None and visible_at < self._last_visibility_ms)
          or any(visible_at - bar.interval_end_ms > candidate.max_age_ms for bar in tuple(bars) + unavailable)
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
          result = replace(result, active_scores=active, shadow_scores=shadow, model_as_of_ms=visible_at,
            manifest_hash=stable_manifest_hash({
              "revision": result.revision, "artifact": artifact.sha256,
              "authorization_revision": auth.registry_authorization_revision,
              "features": [score.source_feature_bar_id for score in scores],
              "model_as_of_ms": visible_at,
              "probabilities": [score.probabilities for score in scores],
              "unavailable": [asdict(item) for item in result.unavailable],
              "input_manifest_hash": minute_batch.manifest_hash if minute_batch else None,
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


  async def freeze_for_snapshot(self, *, as_of_ms, instrument_codes, rule_order):
    """Freeze a value before registry IO; never await the scorer's publication lock.

    This view is only a snapshot input, not permission to create an order. The
    order transaction must independently validate the current registry revision.
    """
    model = self._model
    frozen = model.latest
    mode = model._bound_config[0]
    if mode == "RULE_ONLY":
      return TModelBatchResult(frozen.revision, "", False, "MODEL_OFF", rule_order, (), ())
    try:
      codes = tuple(instrument_codes)
      scores = frozen.active_scores + frozen.shadow_scores
      if (
        type(as_of_ms) is not int or as_of_ms < 0
        or not codes or any(not isinstance(code, str) or not code for code in codes)
        or len(set(codes)) != len(codes)
        or frozen.reason != "VALID"
        or {item.instrument_code for item in scores + frozen.unavailable} != set(codes)
        or frozen.model_as_of_ms is None
        or not 0 <= as_of_ms - frozen.model_as_of_ms <= model.max_age_ms
        or any(
          not 0 <= as_of_ms - score.model_as_of_ms <= model.max_age_ms
          or score.source_bar_end_ms > as_of_ms
          for score in scores
        )
      ):
        raise ValueError("T_MODEL_SNAPSHOT_UNAVAILABLE")
      artifact, auth = model.artifact, model.authorization
      if artifact is None or auth is None:
        raise ValueError("T_MODEL_BINDING_MISSING")
      async with self._sessions() as db, db.begin():
        current = await TModelRegistryRepository(db).authorize(
          model_id=artifact.model_id, model_version=artifact.model_version,
          expected_revision=auth.registry_authorization_revision, mode=mode,
          artifact_sha256=auth.artifact_sha256,
          policy_compatibility_hash=auth.policy_compatibility_hash,
        )
        if current.gate_conclusion != auth.gate_conclusion:
          raise ValueError("T_MODEL_AUTHORIZATION_CHANGED")
      return replace(frozen, execution_rule_order=rule_order)
    except Exception:
      return TModelBatchResult(
        frozen.revision, "", mode == "ACTIVE", "MODEL_SNAPSHOT_UNAVAILABLE", rule_order, (), (),
      )
