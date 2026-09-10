"""Registry-authorized scoring; no model promotion or order execution.

The synchronous scorer evaluates a private copy. Only a successful registry
transaction may publish that copy. Snapshot/order consumers still need their
own current-authorization check at the point of use.
"""

import asyncio
import copy

from quantx_infrastructure.repositories.t_model_registry_repository import (
  TModelRegistryRepository,
)

from .t_model_batch_runtime import TModelBatchResult, TModelBatchRuntime


class TRegistryModelBatchRuntime:
  def __init__(self, *, model: TModelBatchRuntime, session_factory):
    # Own the publication state; the caller's offline scorer cannot mutate it.
    self._model = copy.copy(model)
    self._model.revision = 0
    self._model._cache_key = self._model._cached_artifact = None
    self._model.latest = TModelBatchResult(
      0, "", model._bound_config[0] == "ACTIVE", "COLD", (), (), (),
    )
    self._sessions = session_factory
    self._lock = asyncio.Lock()

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
