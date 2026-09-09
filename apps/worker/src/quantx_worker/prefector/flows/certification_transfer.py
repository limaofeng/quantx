"""Publish frozen certification inputs before relinquishing Worker ownership."""

import asyncio
import os
import threading
from contextlib import suppress
from pathlib import Path

from quantx_contracts.research_preparation import CertificationInputReference
from quantx_infrastructure.services.research_preparation import (
  require_development_export,
  root,
)
from quantx_infrastructure.training_bundle_store import (
  publication_lock,
  reject_links,
  verify_bundle,
)
from quantx_infrastructure.training_transfer import TransferConfig, open_store


def export_transfer_config():
  require_development_export()
  path = os.environ.get("QUANTX_RESEARCH_TRANSFER_CONFIG", "")
  if not path or not Path(path).is_absolute():
    raise ValueError("QUANTX_RESEARCH_TRANSFER_CONFIG must be an explicit absolute file")
  return TransferConfig.load(Path(path), state_root=root() / ".runtime/research-preparation")


async def publish_certification_input(job, directory, result, transfer):
  reference = CertificationInputReference.model_validate(result["certification_input"])
  if (result.get("ready") is not True or result.get("dataset_version") != job.request["dataset_version"]
      or reference.bundle.source_id != job.request["dataset_version"]):
    raise ValueError("Certification export identity mismatch")
  cancel = threading.Event()

  def upload():
    reject_links(directory)
    with publication_lock(directory):
      inputs = directory / "certification-inputs"
      verify_bundle(inputs, reference.bundle, cancel=cancel)
      with open_store(transfer, cancel=cancel) as store:
        if store.publish(reference.bundle, inputs) != reference.bundle.bundle_id:
          raise ValueError("Certification input remote identity mismatch")
    return reference

  task = asyncio.create_task(asyncio.to_thread(upload))
  try:
    return await asyncio.shield(task)
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
