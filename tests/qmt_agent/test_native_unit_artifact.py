"""Native result durability without loading or calling the Windows broker SDK."""

from uuid import uuid4

import pytest
from quantx_contracts.collection_permit import CollectionUnit
from quantx_qmt_agent.native_unit_artifact import NativeUnitArtifacts


@pytest.fixture
def artifacts(tmp_path):
  return NativeUnitArtifacts(
    tmp_path, max_bytes=10000, max_record_bytes=1000, max_records=10
  )


@pytest.fixture
def unit():
  return CollectionUnit.from_payload(
    str(uuid4()), 0, {"stock_list": ["000001.SZ"], "periods": ["tick"]}
  )


class Budget:
  def __init__(self, limit=10000):
    self.used = 0
    self.limit = limit

  def reserve(self, size):
    if self.used + size > self.limit:
      raise ValueError("physical quota exhausted")
    self.used += size

  def release(self, size):
    self.used -= size


def seal(artifacts, unit, records, budget):
  return artifacts.seal(unit, records, reserve=budget.reserve, release=budget.release)


@pytest.mark.parametrize(
  "records",
  [[], [{"code": "000001.SZ", "time": 1, "price": 12.5}], [{"reason": "无数据"}]],
)
def test_sealed_unit_survives_reopen_and_replays_exactly(artifacts, unit, records):
  budget = Budget()
  artifact = seal(artifacts, unit, records, budget)
  assert artifact.byte_count == budget.used == artifact.path.stat().st_size
  reopened = NativeUnitArtifacts(
    artifacts.root, max_bytes=10000, max_record_bytes=1000, max_records=10
  )
  assert reopened.inspect(unit, expected_sha256=artifact.sha256) == artifact
  assert list(reopened.replay(artifact)) == records


def test_native_failure_leaves_no_completion_and_releases_temporary_space(
  artifacts, unit
):
  budget = Budget()

  def failing_records():
    yield {"value": 1}
    raise RuntimeError("native call failed")

  with pytest.raises(RuntimeError, match="native call failed"):
    seal(artifacts, unit, failing_records(), budget)
  assert budget.used == 0
  assert list(artifacts.root.iterdir()) == []
  with pytest.raises(FileNotFoundError):
    artifacts.inspect(unit)


def test_duplicate_is_idempotent_but_changed_native_output_never_overwrites(
  artifacts, unit
):
  budget = Budget()
  original = seal(artifacts, unit, [{"value": 1}], budget)
  assert seal(artifacts, unit, [{"value": 1}], budget) == original
  assert budget.used == original.byte_count
  with pytest.raises(ValueError, match="journal digest"):
    seal(artifacts, unit, [{"value": 2}], budget)
  assert list(artifacts.replay(original)) == [{"value": 1}]
  assert budget.used == original.byte_count


@pytest.mark.parametrize(
  "corruption", [b"", b"{broken}\n", b'{"record":{"value":2}}\n']
)
def test_corruption_is_rejected_before_any_replay(artifacts, unit, corruption):
  artifact = seal(artifacts, unit, [{"value": 1}], Budget())
  artifact.path.write_bytes(corruption)
  iterator = artifacts.replay(artifact)
  with pytest.raises(ValueError):
    next(iterator)


def test_partial_file_cannot_be_mistaken_for_empty_completed_unit(artifacts, unit):
  artifact = seal(artifacts, unit, [], Budget())
  artifact.path.write_bytes(artifact.path.read_bytes().splitlines(keepends=True)[0])
  with pytest.raises(ValueError, match="incomplete"):
    artifacts.inspect(unit)


@pytest.mark.parametrize(
  "records",
  [
    [{"x": "x" * 1000}],
    [{"x": n} for n in range(11)],
    [{"x": float("nan")}],
    ["invalid"],
  ],
)
def test_record_limits_release_space_and_publish_nothing(artifacts, unit, records):
  budget = Budget()
  with pytest.raises(ValueError):
    seal(artifacts, unit, records, budget)
  assert budget.used == 0 and not list(artifacts.root.iterdir())


def test_physical_quota_failure_cannot_publish_completion(artifacts, unit):
  budget = Budget(400)
  with pytest.raises(ValueError):
    seal(artifacts, unit, [{"value": "x" * 300}], budget)
  assert budget.used == 0 and not list(artifacts.root.iterdir())


def test_wrong_identity_and_symlink_never_replay(artifacts, unit, tmp_path):
  artifact = seal(artifacts, unit, [{"value": 1}], Budget())
  other = unit.model_copy(update={"unit_index": 1})
  other_path = artifacts.root / f"{other.unit_id}.jsonl"
  other_path.write_bytes(artifact.path.read_bytes())
  with pytest.raises(ValueError, match="identity"):
    artifacts.inspect(other)
  other_path.unlink()
  other_path.symlink_to(artifact.path)
  with pytest.raises(ValueError, match="linked"):
    artifacts.inspect(other)


def test_trailing_data_and_wrong_journal_digest_are_rejected(artifacts, unit):
  artifact = seal(artifacts, unit, [{"value": 1}], Budget())
  with pytest.raises(ValueError, match="journal digest"):
    artifacts.inspect(unit, expected_sha256="f" * 64)
  with artifact.path.open("ab") as stream:
    stream.write(b"extra")
  with pytest.raises(ValueError, match="trailing"):
    artifacts.inspect(unit)


def test_journal_binds_verified_bytes_to_permit_and_reopens_without_native_call(
  artifacts, unit, tmp_path
):
  from quantx_qmt_agent.journal import LocalJournal

  from tests.qmt_agent.test_collection_permit import NOW, permit

  authorization = permit(unit=unit)
  journal_path = tmp_path / "journal.sqlite"
  journal = LocalJournal(journal_path)
  journal.accept_collection_permit(
    authorization, device_id=str(authorization.device_id), unit=unit, now=NOW
  )
  assert (
    journal.load_collection_artifact(
      device_id=str(authorization.device_id), unit=unit, artifacts=artifacts
    )
    is None
  )
  artifact = seal(artifacts, unit, [{"value": 1}], Budget())
  assert journal.record_collection_artifact(
    permit_id=str(authorization.permit_id), artifact=artifact, artifacts=artifacts
  )
  assert not journal.record_collection_artifact(
    permit_id=str(authorization.permit_id), artifact=artifact, artifacts=artifacts
  )
  journal.connection.close()
  reopened = LocalJournal(journal_path)
  try:
    restored = reopened.load_collection_artifact(
      device_id=str(authorization.device_id), unit=unit, artifacts=artifacts
    )
    assert restored == artifact
    assert list(artifacts.replay(restored)) == [{"value": 1}]
    assert (
      reopened.load_collection_artifact(
        device_id=str(uuid4()), unit=unit, artifacts=artifacts
      )
      is None
    )
    artifact.path.unlink()
    with pytest.raises(FileNotFoundError):
      reopened.load_collection_artifact(
        device_id=str(authorization.device_id), unit=unit, artifacts=artifacts
      )
  finally:
    reopened.connection.close()


def test_artifact_without_a_permit_cannot_create_journal_completion(
  artifacts, unit, tmp_path
):
  from quantx_qmt_agent.journal import LocalJournal

  artifact = seal(artifacts, unit, [], Budget())
  journal = LocalJournal(tmp_path / "journal.sqlite")
  try:
    with pytest.raises(ValueError, match="matching permit receipt"):
      journal.record_collection_artifact(
        permit_id=str(uuid4()), artifact=artifact, artifacts=artifacts
      )
    assert (
      journal.connection.execute(
        "SELECT count(*) FROM history_collection_artifacts"
      ).fetchone()[0]
      == 0
    )
  finally:
    journal.connection.close()


def test_changed_record_with_original_footer_cannot_yield_partial_results(
  artifacts, unit
):
  artifact = seal(artifacts, unit, [{"value": 1}, {"value": 2}], Budget())
  artifact.path.write_bytes(
    artifact.path.read_bytes().replace(b'"value":2', b'"value":3')
  )
  with pytest.raises(ValueError, match="completion proof"):
    next(artifacts.replay(artifact))


@pytest.mark.parametrize(
  "old,new",
  [
    (b'"unit_index":0', b'"unit_index":false'),
    (b'"record_count":0', b'"record_count":false'),
  ],
)
def test_identity_and_completion_types_are_strict(artifacts, unit, old, new):
  artifact = seal(artifacts, unit, [], Budget())
  artifact.path.write_bytes(artifact.path.read_bytes().replace(old, new))
  with pytest.raises(ValueError):
    artifacts.inspect(unit)
