import hashlib
import io
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from quantx_contracts.training_bundle import BundleFile, TrainingBundle
from quantx_infrastructure import training_bundle_store as module
from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  DirectoryBundleReader,
  SFTPBundleReader,
  materialize_bundle,
  verify_bundle,
)


def inventory(contents):
  return TrainingBundle(
    schema_version=1,
    kind="DATASET",
    source_id="dataset-v1",
    files=[
      BundleFile(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest())
      for name, data in contents.items()
    ],
  )


@pytest.mark.parametrize(
  "name",
  [
    "../escape",
    "/absolute",
    "C:/data",
    "a\\b",
    "a//b",
    "CON.txt",
    "aux",
    "x/LPT1.csv",
    "a.",
    "a ",
    ".hidden",
    "a/../b",
  ],
)
def test_paths_are_safe_on_both_windows_and_macos(name):
  with pytest.raises(ValidationError):
    inventory({name: b"x"})


@pytest.mark.parametrize(
  "paths", [("a", "A"), ("a/x", "A/y"), ("a", "a/b"), ("a/b", "a")]
)
def test_case_and_file_directory_collisions_are_rejected(paths):
  with pytest.raises(ValidationError):
    inventory(dict.fromkeys(paths, b"x"))


def test_inventory_id_is_order_independent_and_content_bound():
  first = inventory({"a": b"one", "nested/b": b"two"})
  second = inventory({"nested/b": b"two", "a": b"one"})
  assert first.bundle_id == second.bundle_id
  assert first.total_bytes == 6
  assert first.bundle_id != inventory({"a": b"ONE", "nested/b": b"two"}).bundle_id
  assert TrainingBundle.model_validate_json(first.canonical_bytes()) == first
  with pytest.raises(ValidationError):
    TrainingBundle.model_validate({**first.model_dump(), "schema_version": True})


class Reader:
  def __init__(self, bundle, contents):
    self.bundle = bundle
    self.contents = contents
    self.calls = []

  def open(self, key):
    self.calls.append(key)
    prefix, name = key.split("/", 1)
    assert prefix == self.bundle.bundle_id
    data = self.contents[name]
    if isinstance(data, Exception):
      raise data
    return io.BytesIO(data)


def test_success_is_atomic_and_verified_cache_does_not_transfer_again(tmp_path):
  contents = {"manifest.json": b"{}", "data/panel.parquet": b"panel"}
  bundle = inventory(contents)
  reader = Reader(bundle, contents)
  cache = tmp_path.resolve() / "cache"
  output = materialize_bundle(reader, bundle, cache, reserve_bytes=0)
  assert output.name == bundle.bundle_id
  assert verify_bundle(output, bundle) == output
  assert not (cache / (bundle.bundle_id + ".partial")).exists()
  assert len(reader.calls) == 2
  assert materialize_bundle(reader, bundle, cache, reserve_bytes=0) == output
  assert len(reader.calls) == 2


def test_interrupted_transfer_keeps_verified_files_and_resumes_without_retraining(
  tmp_path,
):
  contents = {"a": b"complete", "b": b"second"}
  bundle = inventory(contents)
  reader = Reader(bundle, {**contents, "b": OSError("private connection secret")})
  cache = tmp_path.resolve() / "cache"
  with pytest.raises(BundleTransferError, match="^BUNDLE_TRANSFER_INTERRUPTED$"):
    materialize_bundle(reader, bundle, cache, reserve_bytes=0)
  assert not (cache / bundle.bundle_id).exists()
  assert (cache / (bundle.bundle_id + ".partial") / "a").read_bytes() == contents["a"]
  reader.contents = contents
  materialize_bundle(reader, bundle, cache, reserve_bytes=0)
  assert reader.calls.count(bundle.bundle_id + "/a") == 1
  assert reader.calls.count(bundle.bundle_id + "/b") == 2


@pytest.mark.parametrize("received", [b"short", b"LONGER THAN EXPECTED", b"wrong!"])
def test_truncation_size_and_hash_failure_never_expose_completed_bundle(
  tmp_path, received
):
  bundle = inventory({"a": b"right!"})
  cache = tmp_path.resolve() / "cache"
  with pytest.raises(BundleTransferError):
    materialize_bundle(Reader(bundle, {"a": received}), bundle, cache, reserve_bytes=0)
  assert not (cache / bundle.bundle_id).exists()


def test_corrupted_completed_cache_is_not_overwritten(tmp_path):
  bundle = inventory({"a": b"right"})
  cache = tmp_path.resolve() / "cache"
  reader = Reader(bundle, {"a": b"right"})
  output = materialize_bundle(reader, bundle, cache, reserve_bytes=0)
  (output / "a").write_bytes(b"wrong")
  with pytest.raises(BundleTransferError, match="INTEGRITY_MISMATCH"):
    materialize_bundle(reader, bundle, cache, reserve_bytes=0)
  assert len(reader.calls) == 1
  assert (output / "a").read_bytes() == b"wrong"


def test_low_disk_prevents_transfer(tmp_path, monkeypatch):
  bundle = inventory({"a": b"right"})
  reader = Reader(bundle, {"a": b"right"})
  monkeypatch.setattr(module.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
  with pytest.raises(BundleTransferError, match="DISK_RESERVE"):
    materialize_bundle(reader, bundle, tmp_path.resolve(), reserve_bytes=1)
  assert not reader.calls


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_linked_transfer_file_cannot_overwrite_external_evidence(tmp_path, link):
  root = tmp_path.resolve()
  bundle = inventory({"a": b"data"})
  outside = root / "outside"
  outside.write_bytes(b"do not change")
  staging = root / "cache" / (bundle.bundle_id + ".partial")
  staging.mkdir(parents=True)
  temporary = staging / ".a.transfer"
  if link == "symlink":
    temporary.symlink_to(outside)
  else:
    os.link(outside, temporary)
  with pytest.raises(BundleTransferError, match="LINK_FORBIDDEN"):
    materialize_bundle(
      Reader(bundle, {"a": b"data"}), bundle, root / "cache", reserve_bytes=0
    )
  assert outside.read_bytes() == b"do not change"


def test_directory_channel_uses_only_identified_relative_objects(tmp_path):
  root = tmp_path.resolve()
  bundle = inventory({"a": b"data"})
  objects = root / "objects" / bundle.bundle_id
  objects.mkdir(parents=True)
  (objects / "a").write_bytes(b"data")
  reader = DirectoryBundleReader(root / "objects")
  output = materialize_bundle(reader, bundle, root / "cache", reserve_bytes=0)
  assert (output / "a").read_bytes() == b"data"
  with pytest.raises(BundleTransferError):
    reader.open("../outside")


@pytest.mark.parametrize("linked", [False, True])
def test_sftp_adapter_reads_identified_objects_with_timeout_and_rejects_links(
  tmp_path, linked
):
  root = tmp_path.resolve()
  bundle = inventory({"a": b"data"})
  objects = root / "store" / bundle.bundle_id
  objects.mkdir(parents=True)
  (objects / "a").write_bytes(b"data")
  if linked:
    (root / "linked").symlink_to(root / "store", target_is_directory=True)
  calls = []

  class Client:
    def get_channel(self):
      return SimpleNamespace(settimeout=lambda value: calls.append(value))

    def lstat(self, path):
      return (root / path.lstrip("/")).lstat()

    def open(self, path, mode):
      calls.append(path)
      return (root / path.lstrip("/")).open(mode)

  reader = SFTPBundleReader(
    Client(), "/linked" if linked else "/store", io_timeout_seconds=5
  )
  if linked:
    with pytest.raises(BundleTransferError, match="LINK_FORBIDDEN"):
      materialize_bundle(reader, bundle, root / "cache", reserve_bytes=0)
    assert calls == [5]
  else:
    output = materialize_bundle(reader, bundle, root / "cache", reserve_bytes=0)
    assert (output / "a").read_bytes() == b"data"
    assert calls == [5, f"/store/{bundle.bundle_id}/a"]
