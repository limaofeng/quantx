import hashlib
import json
import os
import socket
import threading
from dataclasses import replace
from pathlib import PurePosixPath
from time import monotonic, sleep

import paramiko
import pytest
from quantx_contracts.training_bundle import BundleFile, TrainingBundle
from quantx_infrastructure.training_bundle_store import BundleTransferError
from quantx_trainer.config import TrainerConfigurationError
from quantx_trainer.transfer import TransferConfig, open_store


class Filesystem(paramiko.SFTPServerInterface):
  """Local protocol server for real SSH authentication and SFTP wire tests."""

  def __init__(self, server, *, root):
    super().__init__(server)
    self.root = root

  def path(self, name):
    assert ".." not in PurePosixPath(name).parts
    return self.root / name.lstrip("/")

  def call(self, operation):
    try:
      return operation()
    except OSError as exc:
      return paramiko.SFTPServer.convert_errno(exc.errno)

  def lstat(self, path):
    return self.call(lambda: paramiko.SFTPAttributes.from_stat(self.path(path).lstat()))

  def list_folder(self, path):
    return self.call(
      lambda: [
        paramiko.SFTPAttributes.from_stat(item.stat(), filename=item.name)
        for item in self.path(path).iterdir()
      ]
    )

  def mkdir(self, path, attr):
    return self.call(lambda: self.path(path).mkdir() or paramiko.SFTP_OK)

  def remove(self, path):
    return self.call(lambda: self.path(path).unlink() or paramiko.SFTP_OK)

  def posix_rename(self, source, destination):
    return self.call(
      lambda: os.replace(self.path(source), self.path(destination)) or paramiko.SFTP_OK
    )

  def rename(self, source, destination):
    if self.path(destination).exists():
      return paramiko.SFTP_FAILURE
    return self.call(
      lambda: self.path(source).rename(self.path(destination)) and paramiko.SFTP_OK
    )

  def open(self, path, flags, attr):
    def operation():
      descriptor = os.open(self.path(path), flags, 0o600)
      mode = "r+b" if flags & os.O_RDWR else "wb" if flags & os.O_WRONLY else "rb"
      stream = os.fdopen(descriptor, mode)
      handle = paramiko.SFTPHandle(flags)
      handle.readfile = stream
      handle.writefile = stream
      return handle

    return self.call(operation)


@pytest.fixture
def server(tmp_path, monkeypatch):
  root = tmp_path.resolve()
  (root / "datasets").mkdir()
  (root / "artifacts").mkdir()
  key = paramiko.ECDSAKey.generate()
  host_key = paramiko.ECDSAKey.generate()
  private_key = root / "key"
  key.write_private_key_file(str(private_key))
  known_hosts = root / "known_hosts"
  known_hosts.write_text(
    f"[training-store]:2222 {host_key.get_name()} {host_key.get_base64()}\n"
  )
  values = dict(
    host="training-store",
    port=2222,
    username="trainer",
    private_key=str(private_key),
    known_hosts=str(known_hosts),
    datasets_root="/datasets",
    artifacts_root="/artifacts",
    timeout_seconds=2,
  )
  filename = root / "transfer.toml"
  filename.write_text(
    "\n".join(f"{name} = {json.dumps(value)}" for name, value in values.items())
  )
  config = TransferConfig.load(filename, state_root=root)
  authentications = []

  class Auth(paramiko.ServerInterface):
    def check_auth_publickey(self, username, candidate):
      authentications.append(username)
      return (
        paramiko.AUTH_SUCCESSFUL
        if username == "trainer" and candidate == key
        else paramiko.AUTH_FAILED
      )

    def get_allowed_auths(self, username):
      return "publickey"

    def check_channel_request(self, kind, chanid):
      return (
        paramiko.OPEN_SUCCEEDED
        if kind == "session"
        else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
      )

  left, right = socket.socketpair()
  transport = paramiko.Transport(right)
  transport.add_server_key(host_key)
  transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Filesystem, root=root)
  transport.start_server(event=threading.Event(), server=Auth())
  original = paramiko.SSHClient.connect

  def connect(client, **kwargs):
    assert kwargs["allow_agent"] is False and kwargs["look_for_keys"] is False
    assert "password" not in kwargs and "key_filename" not in kwargs
    return original(client, sock=left, **kwargs)

  monkeypatch.setattr(paramiko.SSHClient, "connect", connect)
  try:
    yield config, root, authentications
  finally:
    transport.close()
    left.close()
    right.close()
    transport.join(timeout=3)
    assert not transport.is_alive()


def test_authenticated_wire_transfer_publish_and_readback(server):
  config, root, authentications = server
  # Loading the explicit key must not discover an adjacent certificate.
  (root / "key-cert.pub").write_text("unconfigured certificate")
  source = root / "source"
  source.mkdir()
  content = b"complete model evidence"
  (source / "model.txt").write_bytes(content)
  bundle = TrainingBundle(
    schema_version=1,
    kind="RESULT",
    source_id="run-1",
    files=[
      BundleFile(
        path="model.txt", size=len(content), sha256=hashlib.sha256(content).hexdigest()
      )
    ],
  )
  with open_store(config) as store:
    assert store.publish(bundle, source) == bundle.bundle_id
    cached = store.fetch(bundle, root / "cache", minimum_free_bytes=0)
    assert (cached / "model.txt").read_bytes() == content
    assert store.publish(bundle, source) == bundle.bundle_id
    dataset = bundle.model_copy(update={"kind": "DATASET"})
    assert store.publish(dataset, source) == dataset.bundle_id
    assert (
      store.fetch(dataset, root / "cache", minimum_free_bytes=0) / "model.txt"
    ).read_bytes() == content
    assert (root / "datasets" / dataset.bundle_id).is_dir()
    assert (root / "artifacts" / bundle.bundle_id).is_dir()
  assert authentications == ["trainer"]


def test_stalled_sftp_version_exchange_has_a_bounded_deadline(server, monkeypatch):
  config, root, authentications = server

  def stalled(client):
    client.sock.recv(1)
    raise EOFError

  monkeypatch.setattr(paramiko.SFTPClient, "_send_version", stalled)
  start = monotonic()
  with pytest.raises(BundleTransferError):
    with open_store(replace(config, timeout_seconds=1)):
      pytest.fail("stalled server returned a store")
  assert monotonic() - start < 3


def test_authentication_failure_is_redacted(server):
  config, root, authentications = server
  with pytest.raises(BundleTransferError, match="^TRAINER_SFTP_CHANNEL_FAILED$"):
    with open_store(replace(config, username="wrong-private-identity")):
      pytest.fail("unapproved identity accepted")
  assert authentications == ["wrong-private-identity"]


def test_active_transfer_cancellation_closes_the_authenticated_channel(server):
  config, root, authentications = server
  cancel = threading.Event()
  with open_store(config, cancel=cancel) as store:
    channel = store.datasets.client.get_channel()
    cancel.set()
    deadline = monotonic() + 2
    while not channel.closed and monotonic() < deadline:
      sleep(0.01)
    assert channel.closed


@pytest.mark.parametrize("change", ["unknown", "rotated"])
def test_host_identity_failure_happens_before_authentication(server, change):
  config, root, authentications = server
  other_key = paramiko.ECDSAKey.generate()
  config.known_hosts.write_text(
    ""
    if change == "unknown"
    else f"[training-store]:2222 {other_key.get_name()} {other_key.get_base64()}\n"
  )
  with pytest.raises(BundleTransferError, match="TRAINER_SFTP_CHANNEL_FAILED"):
    with open_store(config):
      pytest.fail("unverified host accepted")
  assert authentications == []


@pytest.mark.parametrize(
  "field,value",
  [
    ("port", True),
    ("timeout_seconds", 0),
    ("timeout_seconds", 61),
    ("host", "user@server"),
    ("username", ""),
    ("datasets_root", "/"),
    ("datasets_root", "/artifacts"),
    ("datasets_root", "/artifacts/nested"),
    ("artifacts_root", "/data/../other"),
    ("artifacts_root", "relative"),
    ("private_key", "/outside/key"),
    ("known_hosts", "relative"),
  ],
)
def test_transfer_config_rejects_invalid_identity_and_paths(tmp_path, field, value):
  root = tmp_path.resolve()
  key = root / "key"
  key.write_text("private material")
  values = dict(
    host="store",
    port=22,
    username="trainer",
    private_key=str(key),
    known_hosts=str(key),
    datasets_root="/datasets",
    artifacts_root="/artifacts",
    timeout_seconds=5,
  )
  values[field] = value
  filename = root / "transfer.toml"
  filename.write_text(
    "\n".join(f"{name} = {json.dumps(item)}" for name, item in values.items())
  )
  with pytest.raises(TrainerConfigurationError) as caught:
    TransferConfig.load(filename, state_root=root)
  assert "private material" not in str(caught.value)
