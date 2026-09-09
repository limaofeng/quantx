"""Local, offline admission control shared by all Trainer claim transactions."""

import os
from contextlib import contextmanager
from pathlib import Path

from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  publication_lock,
  reject_links,
)


class TrainerAdmissionClosed(RuntimeError):
  """No claim may commit while draining or local admission is unavailable."""


def _directory(control_root: Path) -> Path:
  directory = control_root / "admission"
  reject_links(directory)
  directory.mkdir(parents=True, exist_ok=True)
  return directory


@contextmanager
def claim_admission(control_root: Path):
  """Linearize a claim's precommit evidence callback against drain/resume."""
  try:
    directory = _directory(control_root)
    with publication_lock(directory):
      if os.path.lexists(directory / "draining"):
        raise TrainerAdmissionClosed("TRAINER_DRAINING")
      yield
  except (OSError, ValueError, BundleTransferError) as exc:
    raise TrainerAdmissionClosed("TRAINER_ADMISSION_UNAVAILABLE") from exc


def set_admission(control_root: Path, *, draining: bool) -> dict[str, str]:
  directory = _directory(control_root)
  marker = directory / "draining"
  reject_links(marker)
  with publication_lock(directory):
    if draining and not os.path.lexists(marker):
      # Existence is the durable fail-closed signal, including interrupted writes.
      with marker.open("xb") as stream:
        stream.write(b"DRAIN_REQUESTED\n")
        stream.flush()
        os.fsync(stream.fileno())
    elif not draining:
      marker.unlink(missing_ok=True)
  return admission_status(control_root)


def admission_status(control_root: Path) -> dict[str, str]:
  directory = _directory(control_root)
  return {
    "admission": "DRAINING" if os.path.lexists(directory / "draining") else "OPEN",
    "execution_state": "NOT_INSPECTED",
  }
