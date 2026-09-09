"""Retained files and remaining upload allowance both consume admission budget."""

from uuid import uuid4

import pytest
from quantx_infrastructure.services import market_data_capacity as capacity


@pytest.fixture
def limits(monkeypatch):
  monkeypatch.setattr(capacity, "MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES", 100)
  monkeypatch.setattr(capacity, "MAX_MARKET_DATA_STAGING_BYTES", 400)
  monkeypatch.setattr(capacity, "MIN_MARKET_DATA_STAGING_FREE_BYTES", 50)
  monkeypatch.setattr(capacity, "staging_free_bytes", lambda root: 1000)


def write(root, request, size):
  directory = root / request
  directory.mkdir(exist_ok=True)
  (directory / "chunk").write_bytes(b"x" * size)


def test_existing_bytes_are_not_double_reserved_and_orphans_still_count(
  tmp_path, limits
):
  first, second, orphan = (str(uuid4()) for _ in range(3))
  write(tmp_path, first, 80)
  write(tmp_path, orphan, 200)
  assert capacity.collection_has_capacity(tmp_path, {first, second})
  write(tmp_path, orphan, 201)
  assert not capacity.collection_has_capacity(tmp_path, {first, second})
  assert capacity.staging_usage_bytes(tmp_path) == 281


def test_free_disk_reserves_remaining_bytes_before_collection(
  tmp_path, limits, monkeypatch
):
  request = str(uuid4())
  write(tmp_path, request, 80)
  monkeypatch.setattr(capacity, "staging_free_bytes", lambda root: 70)
  assert capacity.collection_has_capacity(tmp_path, {request})
  monkeypatch.setattr(capacity, "staging_free_bytes", lambda root: 69)
  assert not capacity.collection_has_capacity(tmp_path, {request})


def test_symlink_and_unknown_capacity_reject_admission(tmp_path, limits, monkeypatch):
  (tmp_path / "link").symlink_to(tmp_path / "missing")
  assert not capacity.collection_has_capacity(tmp_path, {str(uuid4())})
  (tmp_path / "link").unlink()

  def unavailable(root):
    raise OSError("unavailable")

  monkeypatch.setattr(capacity, "staging_free_bytes", unavailable)
  assert not capacity.collection_has_capacity(tmp_path, {str(uuid4())})


def test_nonexistent_staging_root_reads_parent_volume_without_creating_it(tmp_path):
  missing = tmp_path / "runtime" / "market-data"
  assert capacity.staging_usage_bytes(missing) == 0
  assert capacity.staging_free_bytes(missing) >= 0
  assert not missing.exists()


def test_an_already_oversized_request_cannot_start_more_native_work(tmp_path, limits):
  request = str(uuid4())
  write(tmp_path, request, 101)
  assert not capacity.collection_has_capacity(tmp_path, {request})
