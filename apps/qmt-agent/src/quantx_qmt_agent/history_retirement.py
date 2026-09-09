"""Delete only history files whose retirement is already durable in journal."""

import os
import shutil
import time
from pathlib import Path
from uuid import UUID

from .history_jobs import HISTORY_JOBS_DIRECTORY
from .native_unit_artifact import _ordinary


def remove_retired_history_files(runtime, request_id):
  from .runtime import _market_data_spool_request_directory

  request_id = str(UUID(str(request_id)))
  if not runtime._historical_worker_lock.locked():
    raise ValueError("history cleanup requires the shared preparation lock")
  if not runtime.journal.history_upload_retired(
    runtime.configuration.device_id, request_id
  ):
    raise ValueError("history cleanup requires durable retirement")
  root = runtime._market_spool_root
  _ordinary(root, directory=True)
  jobs = root / HISTORY_JOBS_DIRECTORY
  if jobs.exists():
    _ordinary(jobs, directory=True)
  # Retain the job directory as the recovery discovery handle until upload bytes
  # have gone. An interrupted deletion is resumed using the journal tombstone.
  candidates = [
    _market_data_spool_request_directory(root, request_id),
    jobs / request_id,
  ]
  retained, entries, deadline = [], 0, time.monotonic() + 2
  for candidate in candidates:
    try:
      _ordinary(candidate, directory=True)
    except FileNotFoundError:
      continue
    pending = [candidate]
    while pending:
      with os.scandir(pending.pop()) as children:
        for child in children:
          entries += 1
          if entries > 10000 or time.monotonic() >= deadline:
            raise ValueError("history cleanup scan budget exceeded")
          path = Path(child.path)
          if child.is_dir(follow_symlinks=False):
            _ordinary(path, directory=True)
            pending.append(path)
          else:
            _ordinary(path)
    retained.append(candidate)
  for candidate in retained:
    try:
      shutil.rmtree(candidate)
    except FileNotFoundError:
      pass
