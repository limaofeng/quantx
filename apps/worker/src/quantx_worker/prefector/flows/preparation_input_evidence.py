"""Pre-claim evidence for the Worker input-only interruption window."""

import hashlib
import json
import os
import re
from contextlib import contextmanager

from quantx_infrastructure.training_bundle_store import reject_links
from quantx_infrastructure.training_process_evidence import (
  begin_execution,
  finish_input_preparation,
)


def input_paths(directory, owner):
  key = hashlib.sha256(owner.encode()).hexdigest()
  return directory / f"input-{key}.json", directory / f"input-{key}.request.json"


@contextmanager
def input_attempt(state_root, logger):
  prepared = []

  def prepare(job_id, owner):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", job_id):
      raise ValueError("Invalid preparation job identity")
    directory = state_root / job_id
    reject_links(directory)
    directory.mkdir(parents=True, exist_ok=True)
    record, request = input_paths(directory, owner)
    reject_links(request)
    with request.open("x", encoding="utf-8") as stream:
      json.dump({"run_id": job_id, "owner": owner}, stream)
      stream.flush()
      os.fsync(stream.fileno())
    begin_execution(record, run_id=job_id, owner=owner, request=request)
    prepared.append((job_id, owner, record, request))

  try:
    yield prepare
  finally:
    # The dispatcher joins child/transfer work before leaving this scope.
    # A compute record always excludes input-only recovery, even if incomplete.
    for job_id, owner, record, request in prepared:
      if not finish_input_preparation(record, run_id=job_id, owner=owner, request=request):
        logger.warning("WORKER_INPUT_ATTEMPT_COMPLETION_EVIDENCE_UNAVAILABLE")
