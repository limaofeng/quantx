"""Explicit authenticated SFTP channel for the Trainer's bundle adapters."""

from __future__ import annotations

import re
import threading
import tomllib
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, fields
from pathlib import Path, PurePosixPath

import paramiko
from quantx_contracts.training_bundle import TrainingBundle
from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  SFTPBundlePublisher,
  materialize_bundle,
  reject_links,
)

from quantx_trainer.config import TrainerConfigurationError


@dataclass(frozen=True, repr=False)
class TransferConfig:
  host: str
  port: int
  username: str
  private_key: Path
  known_hosts: Path
  datasets_root: str
  artifacts_root: str
  timeout_seconds: int

  @classmethod
  def load(cls, filename: Path, *, state_root: Path) -> TransferConfig:
    try:
      reject_links(filename)
      if state_root.resolve() not in filename.resolve().parents:
        raise ValueError
      with filename.open("rb") as stream:
        values = tomllib.load(stream)
      if set(values) != {item.name for item in fields(cls)}:
        raise ValueError
      for name in ("port", "timeout_seconds"):
        upper = 65535 if name == "port" else 60
        if type(values[name]) is not int or not 1 <= values[name] <= upper:
          raise ValueError
      for name in set(values) - {"port", "timeout_seconds"}:
        if not isinstance(values[name], str) or not values[name]:
          raise ValueError
      if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", values["host"]):
        raise ValueError
      if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", values["username"]):
        raise ValueError
      for name in ("private_key", "known_hosts"):
        path = Path(values[name])
        reject_links(path)
        if (
          not path.is_absolute()
          or state_root.resolve() not in path.resolve().parents
          or not path.is_file()
        ):
          raise ValueError
        values[name] = path
      roots = []
      for name in ("datasets_root", "artifacts_root"):
        root = PurePosixPath(values[name])
        if (
          not root.is_absolute()
          or root == PurePosixPath("/")
          or str(root) != values[name]
          or ".." in root.parts
          or any(ord(c) < 32 for c in values[name])
          or "\\" in values[name]
        ):
          raise ValueError
        roots.append(root)
      if (
        roots[0] == roots[1]
        or roots[0] in roots[1].parents
        or roots[1] in roots[0].parents
      ):
        raise ValueError
      return cls(**values)
    except Exception:
      raise TrainerConfigurationError(
        "Trainer SFTP configuration is invalid or its identity files are unavailable"
      ) from None


@dataclass(repr=False)
class TrainingStore:
  datasets: SFTPBundlePublisher = field(repr=False)
  artifacts: SFTPBundlePublisher = field(repr=False)
  cancel: threading.Event | None = field(default=None, repr=False)

  def fetch(
    self, bundle: TrainingBundle, cache_root: Path, *, minimum_free_bytes: int
  ) -> Path:
    reader = self.datasets if bundle.kind == "DATASET" else self.artifacts
    return materialize_bundle(
      reader, bundle, cache_root, reserve_bytes=minimum_free_bytes, cancel=self.cancel
    )

  def publish(self, bundle: TrainingBundle, directory: Path) -> str:
    publisher = self.datasets if bundle.kind == "DATASET" else self.artifacts
    return publisher.publish(directory, bundle, cancel=self.cancel)


@contextmanager
def open_store(config: TransferConfig, *, cancel: threading.Event | None = None):
  """Use one dedicated key and known-hosts file; never ambient SSH credentials."""
  client = paramiko.SSHClient()
  channel = None
  closed = threading.Event()

  def watch_cancel():
    assert cancel is not None
    while not closed.wait(0.1):
      if cancel.is_set():
        with suppress(Exception):
          client.close()

  watcher = None
  if cancel is not None:
    watcher = threading.Thread(target=watch_cancel, daemon=True)
    watcher.start()
  try:
    if cancel is not None and cancel.is_set():
      raise BundleTransferError("TRAINER_SFTP_CANCELLED")
    reject_links(config.known_hosts)
    reject_links(config.private_key)
    client.load_host_keys(str(config.known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    key = _load_private_key(config.private_key)
    client.connect(
      hostname=config.host,
      port=config.port,
      username=config.username,
      pkey=key,
      allow_agent=False,
      look_for_keys=False,
      timeout=config.timeout_seconds,
      banner_timeout=config.timeout_seconds,
      auth_timeout=config.timeout_seconds,
      channel_timeout=config.timeout_seconds,
    )
    channel = _open_sftp(client, config.timeout_seconds)
    channel.get_channel().settimeout(config.timeout_seconds)
    readers = [
      SFTPBundlePublisher(
        channel, config.datasets_root, io_timeout_seconds=config.timeout_seconds
      ),
      SFTPBundlePublisher(
        channel, config.artifacts_root, io_timeout_seconds=config.timeout_seconds
      ),
    ]
    for reader in readers:
      reader._check_path(reader.root, file=False)
    yield TrainingStore(*readers, cancel=cancel)
  except BundleTransferError:
    raise
  except Exception:
    raise BundleTransferError("TRAINER_SFTP_CHANNEL_FAILED") from None
  finally:
    closed.set()
    with suppress(Exception):
      if channel is not None:
        channel.close()
    with suppress(Exception):
      client.close()
    if watcher is not None:
      watcher.join(timeout=1)


def _open_sftp(client, timeout_seconds: int):
  # Paramiko's subsystem acknowledgement and initial SFTP version exchange
  # precede channel.settimeout and may otherwise wait indefinitely.
  expired = threading.Event()

  def abort():
    expired.set()
    with suppress(Exception):
      client.close()

  timer = threading.Timer(timeout_seconds, abort)
  timer.daemon = True
  timer.start()
  try:
    channel = client.open_sftp()
    if expired.is_set():
      raise BundleTransferError("TRAINER_SFTP_HANDSHAKE_TIMEOUT")
    return channel
  finally:
    timer.cancel()


def _load_private_key(path: Path):
  # PKey.from_path also discovers an adjacent OpenSSH certificate. Load only
  # the explicitly configured private key instead of adopting ambient identity.
  for kind in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
    try:
      return kind.from_private_key_file(str(path))
    except (paramiko.SSHException, ValueError):
      continue
  raise BundleTransferError("TRAINER_SFTP_KEY_UNAVAILABLE")


def check_store(config: TransferConfig) -> None:
  """Read-only connection check; server-side account restrictions are a deployment gate."""
  with open_store(config):
    pass
