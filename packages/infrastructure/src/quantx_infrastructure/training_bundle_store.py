"""Verified, atomic local staging for frozen training inputs and outputs.

A transfer channel supplies relative object keys; host paths never enter the
bundle contract. Partial transfers retain diagnostics and can resume by file.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from quantx_contracts.training_bundle import BundleFile, TrainingBundle

CHUNK_BYTES = 1024 * 1024


class BundleTransferError(RuntimeError):
  """Redacted transfer/integrity failure."""


class BundleReader(Protocol):
  def open(self, key: str) -> BinaryIO: ...


def validate_object_key(key: str) -> None:
  bundle_id, separator, name = key.partition("/")
  if not separator or not re.fullmatch(r"[a-f0-9]{64}", bundle_id):
    raise BundleTransferError("BUNDLE_OBJECT_KEY_INVALID")
  BundleFile(path=name, size=0, sha256="0" * 64)


def reject_links(path: Path) -> None:
  for item in (path, *path.parents):
    if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
      raise BundleTransferError("BUNDLE_LINK_FORBIDDEN")
  if path.exists() and path.is_file() and path.stat().st_nlink > 1:
    raise BundleTransferError("BUNDLE_HARDLINK_FORBIDDEN")


def verify_file(path: Path, entry: BundleFile) -> bool:
  reject_links(path)
  if not path.is_file() or path.stat().st_size != entry.size:
    return False
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    while block := stream.read(CHUNK_BYTES):
      digest.update(block)
  return digest.hexdigest() == entry.sha256


def verify_bundle(directory: Path, bundle: TrainingBundle) -> Path:
  reject_links(directory)
  if not directory.is_dir():
    raise BundleTransferError("BUNDLE_INCOMPLETE")
  expected = {entry.path for entry in bundle.files}
  actual = set()
  for path in directory.rglob("*"):
    reject_links(path)
    if path.is_file():
      actual.add(path.relative_to(directory).as_posix())
    elif not path.is_dir():
      raise BundleTransferError("BUNDLE_SPECIAL_FILE_FORBIDDEN")
  if actual != expected or not all(
    verify_file(directory / entry.path, entry) for entry in bundle.files
  ):
    raise BundleTransferError("BUNDLE_INTEGRITY_MISMATCH")
  return directory


class DirectoryBundleReader:
  """Local filesystem channel, also usable with an explicitly mounted store."""

  def __init__(self, root: Path):
    self.root = root.absolute()
    reject_links(self.root)

  def open(self, key: str) -> BinaryIO:
    # Reuse the versioned portable path rules for every caller-provided key.
    validate_object_key(key)
    path = self.root / key
    reject_links(path)
    if not path.is_file():
      raise BundleTransferError("BUNDLE_OBJECT_UNAVAILABLE")
    return path.open("rb")


class SFTPBundleReader:
  """Read via an already authenticated SFTP client with verified host identity.

  The deployment layer owns SSH keys, known_hosts and the restricted server
  account. This adapter never enables auto-acceptance or opens a shell channel.
  The client uses the standard Paramiko SFTPClient interface.
  """

  def __init__(self, client, root: str, *, io_timeout_seconds: int):
    self.client = client
    self.root = PurePosixPath(root)
    if not self.root.is_absolute() or ".." in self.root.parts or "\\" in root:
      raise BundleTransferError("BUNDLE_REMOTE_ROOT_INVALID")
    if type(io_timeout_seconds) is not int or not 1 <= io_timeout_seconds <= 60:
      raise ValueError("SFTP timeout must be between 1 and 60 seconds")
    try:
      self.client.get_channel().settimeout(io_timeout_seconds)
    except Exception:
      raise BundleTransferError("BUNDLE_CHANNEL_UNAVAILABLE") from None

  def open(self, key: str) -> BinaryIO:
    validate_object_key(key)
    target = self.root / key
    try:
      current = PurePosixPath("/")
      for component in target.parts[1:]:
        current /= component
        mode = self.client.lstat(str(current)).st_mode
        if stat.S_ISLNK(mode):
          raise BundleTransferError("BUNDLE_LINK_FORBIDDEN")
        if current != target and not stat.S_ISDIR(mode):
          raise BundleTransferError("BUNDLE_REMOTE_DIRECTORY_INVALID")
        if current == target and not stat.S_ISREG(mode):
          raise BundleTransferError("BUNDLE_SPECIAL_FILE_FORBIDDEN")
      return self.client.open(str(target), "rb")
    except BundleTransferError:
      raise
    except Exception:
      raise BundleTransferError("BUNDLE_OBJECT_UNAVAILABLE") from None


def materialize_bundle(
  source: BundleReader,
  bundle: TrainingBundle,
  cache_root: Path,
  *,
  reserve_bytes: int,
) -> Path:
  """Read by inventory hash; expose content only after every file verifies.

  The caller must hold host admission for this cache. Completed bundles are
  immutable: corruption is reported, never silently overwritten or retrained.
  """
  if type(reserve_bytes) is not int or reserve_bytes < 0:
    raise ValueError("reserve_bytes must be a non-negative integer")
  cache_root = cache_root.absolute()
  reject_links(cache_root)
  cache_root.mkdir(parents=True, exist_ok=True)
  complete = cache_root / bundle.bundle_id
  reject_links(complete)
  if complete.exists():
    return verify_bundle(complete, bundle)
  staging = cache_root / (bundle.bundle_id + ".partial")
  reject_links(staging)
  staging.mkdir(exist_ok=True)
  try:
    for entry in bundle.files:
      destination = staging / entry.path
      reject_links(destination)
      if verify_file(destination, entry):
        continue
      if shutil.disk_usage(cache_root).free < entry.size + reserve_bytes:
        raise BundleTransferError("BUNDLE_DISK_RESERVE")
      destination.parent.mkdir(parents=True, exist_ok=True)
      # Temporary bytes have a reserved suffix outside the manifest namespace.
      temporary = destination.with_name("." + destination.name + ".transfer")
      reject_links(temporary)
      if temporary.exists() and not temporary.is_file():
        raise BundleTransferError("BUNDLE_SPECIAL_FILE_FORBIDDEN")
      digest = hashlib.sha256()
      count = 0
      with (
        source.open(f"{bundle.bundle_id}/{entry.path}") as incoming,
        temporary.open("wb") as output,
      ):
        while block := incoming.read(CHUNK_BYTES):
          count += len(block)
          if count > entry.size:
            raise BundleTransferError("BUNDLE_OBJECT_SIZE_MISMATCH")
          if shutil.disk_usage(cache_root).free < len(block) + reserve_bytes:
            raise BundleTransferError("BUNDLE_DISK_RESERVE")
          output.write(block)
          digest.update(block)
        output.flush()
        os.fsync(output.fileno())
      if count != entry.size or digest.hexdigest() != entry.sha256:
        raise BundleTransferError("BUNDLE_OBJECT_INTEGRITY_MISMATCH")
      os.replace(temporary, destination)
    verify_bundle(staging, bundle)
    # Never merge into an existing result directory or expose partial contents.
    staging.rename(complete)
    return complete
  except BundleTransferError:
    raise
  except Exception:
    raise BundleTransferError("BUNDLE_TRANSFER_INTERRUPTED") from None
