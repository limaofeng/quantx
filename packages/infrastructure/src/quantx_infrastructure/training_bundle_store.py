"""Verified, atomic local staging for frozen training inputs and outputs.

A transfer channel supplies relative object keys; host paths never enter the
bundle contract. Partial transfers retain diagnostics and can resume by file.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import stat
import threading
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


def _check_cancel(cancel: threading.Event | None) -> None:
  if cancel is not None and cancel.is_set():
    raise BundleTransferError("BUNDLE_CANCELLED")


def verify_file(
  path: Path,
  entry: BundleFile,
  *,
  cancel: threading.Event | None = None,
) -> bool:
  _check_cancel(cancel)
  reject_links(path)
  if not path.is_file() or path.stat().st_size != entry.size:
    return False
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    while block := stream.read(CHUNK_BYTES):
      _check_cancel(cancel)
      digest.update(block)
  _check_cancel(cancel)
  return digest.hexdigest() == entry.sha256


def verify_bundle(
  directory: Path,
  bundle: TrainingBundle,
  *,
  cancel: threading.Event | None = None,
) -> Path:
  _check_cancel(cancel)
  reject_links(directory)
  if not directory.is_dir():
    raise BundleTransferError("BUNDLE_INCOMPLETE")
  expected = {entry.path for entry in bundle.files}
  actual = set()
  for path in directory.rglob("*"):
    _check_cancel(cancel)
    reject_links(path)
    if path.is_file():
      actual.add(path.relative_to(directory).as_posix())
    elif not path.is_dir():
      raise BundleTransferError("BUNDLE_SPECIAL_FILE_FORBIDDEN")
  if actual != expected or not all(
    verify_file(directory / entry.path, entry, cancel=cancel) for entry in bundle.files
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
      self._check_path(target, file=True)
      return self.client.open(str(target), "rb")
    except BundleTransferError:
      raise
    except Exception:
      raise BundleTransferError("BUNDLE_OBJECT_UNAVAILABLE") from None

  def _check_path(self, target: PurePosixPath, *, file: bool) -> None:
    current = PurePosixPath("/")
    for component in target.parts[1:]:
      current /= component
      mode = self.client.lstat(str(current)).st_mode
      if stat.S_ISLNK(mode):
        raise BundleTransferError("BUNDLE_LINK_FORBIDDEN")
      expected_file = current == target and file
      if not (stat.S_ISREG(mode) if expected_file else stat.S_ISDIR(mode)):
        raise BundleTransferError("BUNDLE_REMOTE_OBJECT_TYPE_INVALID")


class SFTPBundlePublisher(SFTPBundleReader):
  """Publish complete local artifacts; retry transport without repeating work.

  Publication uses OpenSSH's posix-rename extension for staged files and the
  standard non-overwriting rename for the final directory. The caller must
  serialize publication of a given bundle and keep local artifacts until the
  returned identifier has been durably registered by the application.
  """

  def _exists(self, path: PurePosixPath) -> bool:
    try:
      self.client.lstat(str(path))
      return True
    except OSError as exc:
      if exc.errno == errno.ENOENT:
        return False
      raise

  def _mkdir(self, path: PurePosixPath) -> None:
    # The configured store is provisioned separately; never create its parents.
    relative = path.relative_to(self.root)
    self._check_path(self.root, file=False)
    current = self.root
    for component in relative.parts:
      current /= component
      if not self._exists(current):
        self.client.mkdir(str(current))
      self._check_path(current, file=False)

  def _matches(self, path: PurePosixPath, entry: BundleFile) -> bool:
    if not self._exists(path):
      return False
    self._check_path(path, file=True)
    if self.client.lstat(str(path)).st_size != entry.size:
      return False
    digest = hashlib.sha256()
    count = 0
    with self.client.open(str(path), "rb") as stream:
      while block := stream.read(CHUNK_BYTES):
        count += len(block)
        if count > entry.size:
          return False
        digest.update(block)
    return count == entry.size and digest.hexdigest() == entry.sha256

  def _verify_published(self, directory: PurePosixPath, bundle: TrainingBundle) -> None:
    self._check_path(directory, file=False)
    expected_files = {entry.path for entry in bundle.files}
    expected_dirs = {
      str(parent)
      for entry in bundle.files
      for parent in PurePosixPath(entry.path).parents
      if str(parent) != "."
    }
    pending = [directory]
    found = set()
    seen = set()
    while pending:
      parent = pending.pop()
      for child in self.client.listdir_attr(str(parent)):
        # Compare before touching the reported path; filenames are server input.
        if (
          "/" in child.filename
          or "\\" in child.filename
          or child.filename in {".", ".."}
        ):
          raise BundleTransferError("BUNDLE_REMOTE_INVENTORY_MISMATCH")
        path = parent / child.filename
        name = str(path.relative_to(directory))
        if name in seen:
          raise BundleTransferError("BUNDLE_REMOTE_INVENTORY_MISMATCH")
        seen.add(name)
        if name in expected_dirs:
          self._check_path(path, file=False)
          pending.append(path)
        elif name in expected_files:
          self._check_path(path, file=True)
          found.add(name)
        else:
          raise BundleTransferError("BUNDLE_REMOTE_INVENTORY_MISMATCH")
    if found != expected_files or not all(
      self._matches(directory / entry.path, entry) for entry in bundle.files
    ):
      raise BundleTransferError("BUNDLE_REMOTE_INTEGRITY_MISMATCH")

  def publish(
    self,
    directory: Path,
    bundle: TrainingBundle,
    *,
    cancel: threading.Event | None = None,
  ) -> str:
    complete = self.root / bundle.bundle_id
    staging = self.root / (bundle.bundle_id + ".partial")
    try:
      verify_bundle(directory, bundle, cancel=cancel)
      self._check_path(self.root, file=False)
      if self._exists(complete):
        self._verify_published(complete, bundle)
        return bundle.bundle_id
      self._mkdir(staging)
      for entry in bundle.files:
        _check_cancel(cancel)
        target = staging / entry.path
        if self._matches(target, entry):
          continue
        self._mkdir(target.parent)
        temporary = target.with_name("." + target.name + ".transfer")
        if self._exists(temporary):
          self._check_path(temporary, file=True)
          self.client.remove(str(temporary))
        source = directory / entry.path
        reject_links(source)
        digest = hashlib.sha256()
        count = 0
        with (
          source.open("rb") as incoming,
          self.client.open(str(temporary), "wx") as output,
        ):
          while block := incoming.read(CHUNK_BYTES):
            _check_cancel(cancel)
            count += len(block)
            if count > entry.size:
              raise BundleTransferError("BUNDLE_LOCAL_SOURCE_CHANGED")
            output.write(block)
            digest.update(block)
          output.flush()
        if count != entry.size or digest.hexdigest() != entry.sha256:
          raise BundleTransferError("BUNDLE_LOCAL_SOURCE_CHANGED")
        # Read the bytes back before acknowledging upload, not just SFTP success.
        if not self._matches(temporary, entry):
          raise BundleTransferError("BUNDLE_REMOTE_INTEGRITY_MISMATCH")
        self.client.posix_rename(str(temporary), str(target))
      self._verify_published(staging, bundle)
      self.client.rename(str(staging), str(complete))
      self._verify_published(complete, bundle)
      return bundle.bundle_id
    except BundleTransferError:
      raise
    except Exception:
      raise BundleTransferError("BUNDLE_PUBLISH_INTERRUPTED") from None


def materialize_bundle(
  source: BundleReader,
  bundle: TrainingBundle,
  cache_root: Path,
  *,
  reserve_bytes: int,
  cancel: threading.Event | None = None,
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
    return verify_bundle(complete, bundle, cancel=cancel)
  staging = cache_root / (bundle.bundle_id + ".partial")
  reject_links(staging)
  staging.mkdir(exist_ok=True)
  try:
    for entry in bundle.files:
      _check_cancel(cancel)
      destination = staging / entry.path
      reject_links(destination)
      if verify_file(destination, entry, cancel=cancel):
        continue
      if shutil.disk_usage(cache_root).free < entry.size + reserve_bytes:
        raise BundleTransferError("BUNDLE_DISK_RESERVE")
      destination.parent.mkdir(parents=True, exist_ok=True)
      # Temporary bytes have a reserved suffix outside the manifest namespace.
      temporary = destination.with_name("." + destination.name + ".transfer")
      reject_links(temporary)
      if temporary.exists() and not temporary.is_file():
        raise BundleTransferError("BUNDLE_SPECIAL_FILE_FORBIDDEN")
      if temporary.exists():
        temporary.unlink()
      digest = hashlib.sha256()
      count = 0
      with (
        source.open(f"{bundle.bundle_id}/{entry.path}") as incoming,
        temporary.open("xb") as output,
      ):
        while block := incoming.read(CHUNK_BYTES):
          _check_cancel(cancel)
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
    verify_bundle(staging, bundle, cancel=cancel)
    # Never merge into an existing result directory or expose partial contents.
    staging.rename(complete)
    return complete
  except BundleTransferError:
    raise
  except Exception:
    raise BundleTransferError("BUNDLE_TRANSFER_INTERRUPTED") from None
