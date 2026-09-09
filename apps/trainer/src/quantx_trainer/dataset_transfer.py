"""Publish certified inputs and materialize them by immutable bundle identity."""

import asyncio
import hashlib
import threading
from contextlib import suppress

from quantx_contracts.training_bundle import BundleFile, TrainingBundle
from quantx_infrastructure.training_bundle_store import reject_links, verify_bundle
from quantx_infrastructure.training_dataset_store import (
  _value,
  resolve_dataset_directory,
)
from quantx_infrastructure.training_host_guard import HostPolicy, host_guard_root

from quantx_trainer.publication import _supervised_io, publication_lock
from quantx_trainer.transfer import TransferConfig, open_store


def dataset_bundle(dataset, *, root, cancel=None):
  files = resolve_dataset_directory(dataset, root=root, cancel=cancel)
  directory = files["directory"]
  # File inventory derives from the already verified certification manifest.
  manifest = files["manifest"]
  entries = [
    BundleFile(path=name, size=value["bytes"], sha256=value["sha256"])
    for name, value in manifest["files"].items()
  ]
  data = (directory / "manifest.json").read_bytes()
  entries.append(
    BundleFile(
      path="manifest.json", size=len(data), sha256=hashlib.sha256(data).hexdigest()
    )
  )
  bundle = TrainingBundle(
    schema_version=1,
    kind="DATASET",
    source_id=_value(dataset, "dataset_version"),
    files=entries,
  )
  verify_bundle(directory, bundle, cancel=cancel)
  return directory, bundle


async def publish_dataset(config, repository, *, dataset_version):
  dataset = await repository.get_dataset(dataset_version)
  if dataset is None:
    raise ValueError("DATASET_NOT_FOUND")
  transfer = TransferConfig.load(config.transfer_config, state_root=config.state_root)
  cancel = threading.Event()

  def upload():
    directory, bundle = dataset_bundle(
      dataset, root=config.state_root / "datasets", cancel=cancel
    )
    lock = config.state_root / "control" / ("dataset-" + bundle.bundle_id)
    reject_links(lock)
    lock.mkdir(parents=True, exist_ok=True)
    with publication_lock(lock), open_store(transfer, cancel=cancel) as store:
      if store.publish(bundle, directory) != bundle.bundle_id:
        raise ValueError("DATASET_REMOTE_IDENTITY_MISMATCH")
    return bundle

  task = asyncio.create_task(asyncio.to_thread(upload))
  try:
    bundle = await asyncio.shield(task)
  except BaseException:
    cancel.set()
    while not task.done():
      try:
        await asyncio.shield(task)
      except asyncio.CancelledError:
        continue
      except Exception:
        break
    with suppress(Exception, asyncio.CancelledError):
      task.result()
    raise
  await repository.record_dataset_bundle(dataset_version, bundle=bundle)
  return {
    "status": "PUBLISHED",
    "dataset_version": dataset_version,
    "bundle_id": bundle.bundle_id,
  }


async def load_dataset(config, repository, dataset, *, run_id, owner):
  bundle = TrainingBundle.model_validate(_value(dataset, "source_bundle"))
  if bundle.kind != "DATASET" or bundle.source_id != _value(dataset, "dataset_version"):
    raise ValueError("DATASET_BUNDLE_IDENTITY_MISMATCH")
  policy = HostPolicy.load(host_guard_root())
  transfer = TransferConfig.load(config.transfer_config, state_root=config.state_root)
  cache = config.state_root / "dataset-cache"
  reject_links(cache)
  cache.mkdir(parents=True, exist_ok=True)
  cancel = threading.Event()

  def fetch():
    # Both complete and partial directories are shared across run retries.
    with open_store(transfer, cancel=cancel) as store:
      directory = store.fetch(
        bundle, cache, minimum_free_bytes=policy.minimum_free_disk_mib * 1024 * 1024
      )
    return resolve_dataset_directory(
      dataset, root=cache, directory=directory, cancel=cancel
    )

  lock = config.state_root / "control" / ("dataset-" + bundle.bundle_id)
  reject_links(lock)
  lock.mkdir(parents=True, exist_ok=True)
  with publication_lock(lock):
    return await _supervised_io(fetch, repository, run_id, owner, cancel)
