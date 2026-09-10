"""Transactional storage, not a model approval or FINAL evaluation service.

The caller verifies training evidence and human release approval before writing,
and owns the transaction. This repository never commits or creates executions.
"""

import copy
import re
from datetime import datetime

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from sqlalchemy import select, update

from quantx_infrastructure.models.t_model_registry import (
  TModelRegistryEventRecord,
  TModelVersionRecord,
)


class TModelRegistryConflict(ValueError):
  pass


class TModelRegistryRepository:
  def __init__(self, db):
    self.db = db

  async def get(self, model_id, model_version):
    return await self.db.get(TModelVersionRecord, (model_id, model_version), populate_existing=True)

  @staticmethod
  def _audit_input(actor_id, reason, now):
    if (
      not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 64
      or not isinstance(reason, str) or not reason.strip() or len(reason) > 256
      or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
    ):
      raise TModelRegistryConflict("T_MODEL_AUDIT_INVALID")

  async def append_candidate(self, *, model_id, model_version, run_key, artifact_sha256,
    policy_compatibility_hash, gate_conclusion, evidence, actor_id, now):
    self._audit_input(actor_id, "REGISTER", now)
    for value, limit in ((model_id, 80), (model_version, 80), (run_key, 160)):
      if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise TModelRegistryConflict("T_MODEL_REGISTRATION_INVALID")
    if (
      any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in (artifact_sha256, policy_compatibility_hash))
      or gate_conclusion not in {"SHADOW_ELIGIBLE", "ACTIVE_ELIGIBLE"}
      or not isinstance(evidence, dict) or not evidence
    ):
      raise TModelRegistryConflict("T_MODEL_REGISTRATION_INVALID")
    values = dict(model_id=model_id, model_version=model_version, run_key=run_key,
      artifact_sha256=artifact_sha256, policy_compatibility_hash=policy_compatibility_hash,
      gate_conclusion=gate_conclusion, evidence=copy.deepcopy(evidence))
    digest = stable_manifest_hash(values)
    existing = await self.get(model_id, model_version)
    if existing is not None:
      if existing.registration_hash != digest or any(getattr(existing, key) != value for key, value in values.items()):
        raise TModelRegistryConflict("T_MODEL_REGISTRATION_CONFLICT")
      return existing
    record = TModelVersionRecord(**values, registration_hash=digest,
      registry_stage="CANDIDATE", authorization_revision=1)
    self.db.add(record)
    await self.db.flush()
    self.db.add(TModelRegistryEventRecord(model_id=model_id, model_version=model_version,
      authorization_revision=1, previous_stage=None, registry_stage="CANDIDATE",
      actor_id=actor_id, reason="REGISTER", occurred_at=now, registration_hash=digest))
    await self.db.flush()
    return record

  @staticmethod
  def _verify_registration(record):
    values = {key: getattr(record, key) for key in (
      "model_id", "model_version", "run_key", "artifact_sha256",
      "policy_compatibility_hash", "gate_conclusion", "evidence",
    )}
    if stable_manifest_hash(values) != record.registration_hash:
      raise TModelRegistryConflict("T_MODEL_REGISTRATION_CORRUPT")

  async def set_stage(self, *, model_id, model_version, expected_revision, stage, actor_id, reason, now):
    self._audit_input(actor_id, reason, now)
    if type(expected_revision) is not int or expected_revision < 1:
      raise TModelRegistryConflict("T_MODEL_REVISION_INVALID")
    record = await self.get(model_id, model_version)
    if record is None or record.authorization_revision != expected_revision:
      raise TModelRegistryConflict("T_MODEL_REVISION_CONFLICT")
    self._verify_registration(record)
    allowed = {
      "CANDIDATE": {"SHADOW", "SUSPENDED", "RETIRED"},
      "SHADOW": {"ACTIVE", "SUSPENDED", "RETIRED"},
      "ACTIVE": {"SUSPENDED", "RETIRED"},
      "SUSPENDED": {"SHADOW", "RETIRED"},
      "RETIRED": set(),
    }
    if stage not in allowed[record.registry_stage] or (stage == "ACTIVE" and record.gate_conclusion != "ACTIVE_ELIGIBLE"):
      raise TModelRegistryConflict("T_MODEL_STAGE_FORBIDDEN")
    previous = record.registry_stage
    result = await self.db.execute(update(TModelVersionRecord).where(
      TModelVersionRecord.model_id == model_id, TModelVersionRecord.model_version == model_version,
      TModelVersionRecord.authorization_revision == expected_revision,
      TModelVersionRecord.registry_stage == previous,
    ).values(registry_stage=stage, authorization_revision=expected_revision + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
      raise TModelRegistryConflict("T_MODEL_REVISION_CONFLICT")
    self.db.add(TModelRegistryEventRecord(model_id=model_id, model_version=model_version,
      authorization_revision=expected_revision + 1, previous_stage=previous, registry_stage=stage,
      actor_id=actor_id, reason=reason, occurred_at=now, registration_hash=record.registration_hash))
    await self.db.flush()
    return await self.get(model_id, model_version)

  async def authorize(self, *, model_id, model_version, expected_revision, mode,
    artifact_sha256, policy_compatibility_hash):
    """Hold current authorization through the caller's write transaction."""
    return await self._check_authorization(model_id=model_id, model_version=model_version,
      expected_revision=expected_revision, mode=mode, artifact_sha256=artifact_sha256,
      policy_compatibility_hash=policy_compatibility_hash, for_update=True)

  async def read_snapshot_authorization(self, **identity):
    """Read committed registration for an observation, without taking a row lock.

    This grants no order authority. Pending/outbox writes must use authorize in
    their own transaction, even if the observation was authorized earlier.
    """
    return await self._check_authorization(**identity, for_update=False)

  async def _check_authorization(self, *, model_id, model_version, expected_revision, mode,
    artifact_sha256, policy_compatibility_hash, for_update):
    record = await self.db.get(TModelVersionRecord, (model_id, model_version),
      populate_existing=True, with_for_update=True if for_update else None)
    if (
      mode not in {"SHADOW", "ACTIVE"} or type(expected_revision) is not int
      or record is None or record.registry_stage != mode
      or record.authorization_revision != expected_revision
      or record.artifact_sha256 != artifact_sha256
      or record.policy_compatibility_hash != policy_compatibility_hash
      or record.gate_conclusion not in ({"ACTIVE_ELIGIBLE"} if mode == "ACTIVE" else {"SHADOW_ELIGIBLE", "ACTIVE_ELIGIBLE"})
    ):
      raise TModelRegistryConflict("T_MODEL_AUTHORIZATION_REVOKED")
    self._verify_registration(record)
    # Re-read through SQL rather than returning a cached registry identity.
    active = await self.db.scalars(select(TModelVersionRecord).where(TModelVersionRecord.registry_stage == "ACTIVE"))
    if mode == "ACTIVE" and [(item.model_id, item.model_version) for item in active] != [(model_id, model_version)]:
      raise TModelRegistryConflict("T_MODEL_AUTHORIZATION_REVOKED")
    return record
