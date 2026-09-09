import hashlib
import os
from types import SimpleNamespace

import pytest
from quantx_contracts.training_bundle import BundleFile, TrainingBundle
from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  SFTPBundlePublisher,
  SFTPBundleReader,
  materialize_bundle,
)


class FileSFTP:
  """Filesystem-backed SFTP protocol fixture; never opens a network connection."""

  def __init__(self, root):
    self.root = root
    self.writes = []
    self.fail_file = None
    self.lose_commit_ack = False
    self.corrupt_upload = False

  def path(self, value):
    return self.root / value.lstrip("/")

  def get_channel(self):
    return SimpleNamespace(settimeout=lambda seconds: None)

  def lstat(self, path):
    return self.path(path).lstat()

  def mkdir(self, path):
    self.path(path).mkdir()

  def remove(self, path):
    self.path(path).unlink()

  def listdir_attr(self, path):
    return [SimpleNamespace(filename=item.name) for item in self.path(path).iterdir()]

  def open(self, path, mode):
    if mode == "wx":
      self.writes.append(path)
      if path.endswith(self.fail_file or "never"):
        raise OSError("private channel credentials")
      stream = self.path(path).open("xb")
      if self.corrupt_upload:

        class CorruptingWriter:
          def __enter__(self):
            return self

          def __exit__(self, *args):
            stream.close()

          def write(self, data):
            stream.write(b"x" * len(data))

          def flush(self):
            stream.flush()

        return CorruptingWriter()
      return stream
    return self.path(path).open(mode)

  def posix_rename(self, source, destination):
    os.replace(self.path(source), self.path(destination))

  def rename(self, source, destination):
    self.path(source).rename(self.path(destination))
    if self.lose_commit_ack:
      self.lose_commit_ack = False
      raise OSError("network interrupted after remote commit")


@pytest.fixture
def artifact(tmp_path):
  root = tmp_path.resolve()
  source = root / "source"
  source.mkdir()
  contents = {
    "model.txt": b"immutable model",
    "reports/evaluation.json": b'{"passed":true}',
  }
  for name, data in contents.items():
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
  bundle = TrainingBundle(
    schema_version=1,
    kind="RESULT",
    source_id="run-1",
    files=[
      BundleFile(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest())
      for name, data in contents.items()
    ],
  )
  (root / "remote" / "store").mkdir(parents=True)
  client = FileSFTP(root / "remote")
  publisher = SFTPBundlePublisher(client, "/store", io_timeout_seconds=5)
  return source, bundle, client, publisher


def test_publish_readback_and_download_form_a_verified_roundtrip(artifact, tmp_path):
  source, bundle, client, publisher = artifact
  assert publisher.publish(source, bundle) == bundle.bundle_id
  assert not client.path(f"/store/{bundle.bundle_id}.partial").exists()
  output = materialize_bundle(
    SFTPBundleReader(client, "/store", io_timeout_seconds=5),
    bundle,
    tmp_path.resolve() / "cache",
    reserve_bytes=0,
  )
  assert (output / "model.txt").read_bytes() == (source / "model.txt").read_bytes()
  before = list(client.writes)
  publisher.publish(source, bundle)
  assert client.writes == before


def test_failed_upload_retries_transport_and_keeps_local_artifacts(artifact):
  source, bundle, client, publisher = artifact
  client.fail_file = ".evaluation.json.transfer"
  with pytest.raises(BundleTransferError, match="^BUNDLE_PUBLISH_INTERRUPTED$"):
    publisher.publish(source, bundle)
  assert not client.path(f"/store/{bundle.bundle_id}").exists()
  assert (source / "model.txt").read_bytes() == b"immutable model"
  client.fail_file = None
  publisher.publish(source, bundle)
  assert sum(path.endswith(".model.txt.transfer") for path in client.writes) == 1


def test_lost_final_ack_converges_without_uploading_again(artifact):
  source, bundle, client, publisher = artifact
  client.lose_commit_ack = True
  with pytest.raises(BundleTransferError, match="PUBLISH_INTERRUPTED"):
    publisher.publish(source, bundle)
  assert client.path(f"/store/{bundle.bundle_id}").is_dir()
  before = list(client.writes)
  assert publisher.publish(source, bundle) == bundle.bundle_id
  assert client.writes == before


def test_successful_sftp_write_is_not_proof_of_correct_artifact_bytes(artifact):
  source, bundle, client, publisher = artifact
  client.corrupt_upload = True
  with pytest.raises(BundleTransferError, match="REMOTE_INTEGRITY_MISMATCH"):
    publisher.publish(source, bundle)
  assert not client.path(f"/store/{bundle.bundle_id}").exists()


def test_published_corruption_is_never_overwritten(artifact):
  source, bundle, client, publisher = artifact
  publisher.publish(source, bundle)
  client.path(f"/store/{bundle.bundle_id}/model.txt").write_bytes(b"wrong")
  before = list(client.writes)
  with pytest.raises(BundleTransferError, match="REMOTE_INTEGRITY_MISMATCH"):
    publisher.publish(source, bundle)
  assert client.writes == before


def test_unlisted_remote_files_prevent_publication(artifact):
  source, bundle, client, publisher = artifact
  staged = client.path(f"/store/{bundle.bundle_id}.partial")
  staged.mkdir()
  (staged / "unexpected.txt").write_bytes(b"unlisted")
  with pytest.raises(BundleTransferError, match="REMOTE_INVENTORY_MISMATCH"):
    publisher.publish(source, bundle)
  assert not client.path(f"/store/{bundle.bundle_id}").exists()


def test_remote_link_is_rejected_before_writing(artifact, tmp_path):
  source, bundle, client, publisher = artifact
  outside = tmp_path.resolve() / "outside"
  outside.mkdir()
  client.path(f"/store/{bundle.bundle_id}.partial").symlink_to(
    outside, target_is_directory=True
  )
  with pytest.raises(BundleTransferError, match="LINK_FORBIDDEN"):
    publisher.publish(source, bundle)
  assert not client.writes
  assert not list(outside.iterdir())


def test_stale_remote_temporary_hardlink_is_unlinked_without_changing_target(
  artifact, tmp_path
):
  source, bundle, client, publisher = artifact
  outside = tmp_path.resolve() / "outside.txt"
  outside.write_bytes(b"unchanged evidence")
  staged = client.path(f"/store/{bundle.bundle_id}.partial")
  staged.mkdir()
  os.link(outside, staged / ".model.txt.transfer")
  assert publisher.publish(source, bundle) == bundle.bundle_id
  assert outside.read_bytes() == b"unchanged evidence"


def test_local_source_access_failure_is_redacted(artifact, monkeypatch):
  from quantx_infrastructure import training_bundle_store

  source, bundle, client, publisher = artifact

  def denied(*args):
    raise PermissionError("private source path and credentials")

  monkeypatch.setattr(training_bundle_store, "verify_bundle", denied)
  with pytest.raises(BundleTransferError, match="^BUNDLE_PUBLISH_INTERRUPTED$"):
    publisher.publish(source, bundle)
  assert not client.writes
