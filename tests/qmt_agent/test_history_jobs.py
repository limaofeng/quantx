"""Durable unit ownership survives the existing upload reset and sweeper."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from quantx_contracts.history_session import HistoryRequest
from quantx_qmt_agent.history_jobs import HistoryJobs, retained_history_bytes
from quantx_qmt_agent.native_unit_artifact import NativeUnitArtifacts
from quantx_qmt_agent.runtime import (
  _managed_market_data_spool_bytes,
  _reset_market_data_spool_directory,
  _sweep_market_data_spool_cleanup,
)

PAYLOAD = {
  "operation": "bars",
  "stock_list": ["000001.SZ"],
  "periods": ["1d"],
  "start_time": "20250102",
  "end_time": "20250102",
}


@pytest.fixture
def job(tmp_path):
  request = HistoryRequest(
    request_id=uuid4(), payload=PAYLOAD, unit_count=1, completed_units=0
  )
  jobs = HistoryJobs(tmp_path, device_id=str(uuid4()))
  reserve, release = Mock(), Mock()
  retained = jobs.retain(request, reserve=reserve, release=release)
  return jobs, retained, reserve, release


def test_reopen_keeps_identity_and_progress_is_server_owned(job):
  jobs, retained, reserve, release = job
  original = (retained.directory / "request.json").read_bytes()
  reopened = HistoryJobs(jobs.root, device_id=jobs.device_id)
  current = reopened.retain(
    retained.request.model_copy(update={"completed_units": 1}),
    reserve=reserve,
    release=release,
  )
  assert current.units == retained.units
  assert current.request.completed_units == 1
  assert (current.directory / "request.json").read_bytes() == original
  reserve.assert_called_once_with(len(original))
  release.assert_not_called()


def test_old_reset_and_sweep_keep_units_and_count_their_bytes(job):
  jobs, retained, _, _ = job
  artifacts = NativeUnitArtifacts(
    retained.artifacts_directory, max_bytes=10000, max_record_bytes=1000, max_records=10
  )
  result = artifacts.seal(
    retained.units[0], [{"value": 1}], reserve=Mock(), release=Mock()
  )
  manifest_size = (retained.directory / "request.json").stat().st_size
  assert (
    _managed_market_data_spool_bytes(jobs.root) == manifest_size + result.byte_count
  )
  _reset_market_data_spool_directory(jobs.root, str(retained.request.request_id))
  assert not _sweep_market_data_spool_cleanup(jobs.root)
  assert artifacts.inspect(retained.units[0]) == result
  assert (
    _managed_market_data_spool_bytes(jobs.root) == manifest_size + result.byte_count
  )


def test_identity_change_is_rejected_without_overwriting(job):
  jobs, retained, _, _ = job
  manifest = retained.directory / "request.json"
  original = manifest.read_bytes()
  changed = retained.request.model_copy(
    update={"payload": {**PAYLOAD, "stock_list": ["600000.SH"]}}
  )
  with pytest.raises(ValueError, match="identity conflict"):
    jobs.retain(changed, reserve=Mock(), release=Mock())
  assert manifest.read_bytes() == original


def test_all_files_including_orphan_and_partial_consume_budget(job):
  jobs, retained, _, _ = job
  before = retained_history_bytes(jobs.root, max_bytes=100000)
  (retained.directory / ".interrupted.tmp").write_bytes(b"a" * 30)
  assert retained_history_bytes(jobs.root, max_bytes=100000) == before + 30
  with pytest.raises(RuntimeError, match="byte limit"):
    retained_history_bytes(jobs.root, max_bytes=before + 29)
  with pytest.raises(RuntimeError, match="scan budget"):
    retained_history_bytes(jobs.root, max_bytes=100000, max_entries=1)


def test_linked_directory_is_not_followed(job, tmp_path):
  jobs, retained, _, _ = job
  (retained.directory / "linked").symlink_to(tmp_path, target_is_directory=True)
  with pytest.raises(ValueError, match="linked path"):
    retained_history_bytes(jobs.root, max_bytes=100000)


def test_missing_manifest_cannot_reassign_retained_native_results(job):
  jobs, retained, _, _ = job
  (retained.artifacts_directory / "unfinished.tmp").write_bytes(b"native result")
  (retained.directory / "request.json").unlink()
  with pytest.raises(ValueError, match="manifest missing"):
    jobs.retain(retained.request, reserve=Mock(), release=Mock())


def test_full_disk_does_not_publish_request(tmp_path):
  jobs = HistoryJobs(tmp_path, device_id=str(uuid4()))
  request = HistoryRequest(
    request_id=uuid4(), payload=PAYLOAD, unit_count=1, completed_units=0
  )
  with pytest.raises(OSError, match="full"):
    jobs.retain(request, reserve=Mock(side_effect=OSError("full")), release=Mock())
  assert not list(tmp_path.rglob("request.json"))
  assert retained_history_bytes(tmp_path, max_bytes=100000) == 0
