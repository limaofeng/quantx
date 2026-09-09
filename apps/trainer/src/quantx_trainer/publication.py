"""Recover publication from a completed local Research run, without training again."""

import asyncio
import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager, suppress
from pathlib import Path

from quantx_contracts.training_bundle import BundleFile, TrainingBundle
from quantx_infrastructure.training_bundle_store import reject_links, verify_bundle
from quantx_infrastructure.training_process_evidence import (
  inspect_execution,
  local_success_recorded,
)
from quantx_infrastructure.training_result import safe_public_details

from quantx_trainer.transfer import TransferConfig, open_store


class PublicationError(RuntimeError):
  """Stable public publication failure; local evidence remains intact."""


PUBLICATION_HEARTBEAT_SECONDS = 10.0


async def _publication_heartbeat(repository, run_id: str, owner: str) -> None:
  row = await repository.get_run(run_id)
  if (
    row is None
    or row.prefect_flow_run_id != owner
    or row.status not in {"RUNNING", "SUCCEEDED"}
  ):
    raise PublicationError("PUBLICATION_OWNERSHIP_LOST")
  if row.status == "RUNNING":
    row = await repository.heartbeat_execution(run_id, expected_flow_run_id=owner)
  if row.prefect_flow_run_id != owner or row.status not in {"RUNNING", "SUCCEEDED"}:
    raise PublicationError("PUBLICATION_OWNERSHIP_LOST")
  if row.cancel_requested_at is not None:
    raise PublicationError("PUBLICATION_CANCEL_REQUESTED")


def read_object(path: Path) -> dict:
  reject_links(path)
  if path.stat().st_size > 8 * 1024 * 1024:
    raise PublicationError("RESULT_METADATA_TOO_LARGE")
  value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
  if not isinstance(value, dict):
    raise PublicationError("RESULT_METADATA_INVALID")
  return value


def _reject_constant(value):
  raise ValueError("non-finite result metadata")


def result_bundle(
  directory: Path,
  *,
  run_id: str,
  run_kind: str,
  cancel: threading.Event | None = None,
) -> TrainingBundle:
  """Validate the Research inventory, including the final manifest itself."""
  manifest_path = directory / "manifest.json"
  manifest = read_object(manifest_path)
  if (
    type(manifest.get("schema_version")) is not int
    or manifest.get("schema_version") != 2
    or manifest.get("run_id") != run_id
    or manifest.get("run_kind") != run_kind
    or manifest.get("status") != "SUCCEEDED"
    or manifest.get("study_id") != "next-day-selection"
    or manifest.get("version") != "v1"
  ):
    raise PublicationError("RESULT_IDENTITY_MISMATCH")
  artifacts = manifest.get("artifacts")
  if not isinstance(artifacts, list) or not artifacts:
    raise PublicationError("RESULT_INVENTORY_MISSING")
  entries = []
  for item in artifacts:
    if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
      raise PublicationError("RESULT_INVENTORY_INVALID")
    entries.append(
      BundleFile(path=item["path"], size=item["bytes"], sha256=item["sha256"])
    )
  required = {
    "metrics.json",
    "data-quality.json",
    "model-runtime.json",
    "preprocessing.json",
    "calibrators.json",
    "factor-schema.json",
    "logistic.json",
    "lightgbm.txt",
  }
  if run_kind == "DEVELOPMENT":
    required.add("development-lock.json")
  if not required.issubset({entry.path for entry in entries}):
    raise PublicationError("RESULT_REQUIRED_ARTIFACT_MISSING")
  data = manifest_path.read_bytes()
  entries.append(
    BundleFile(
      path="manifest.json", size=len(data), sha256=hashlib.sha256(data).hexdigest()
    )
  )
  bundle = TrainingBundle(
    schema_version=1, kind="RESULT", source_id=run_id, files=entries
  )
  verify_bundle(directory, bundle, cancel=cancel)
  return bundle


async def _supervised_io(operation, repository, run_id: str, owner: str, cancel):
  """Keep control-plane supervision active until the local/network worker exits."""
  await _publication_heartbeat(repository, run_id, owner)
  task = asyncio.create_task(asyncio.to_thread(operation))
  try:
    while not task.done():
      done, _ = await asyncio.wait({task}, timeout=PUBLICATION_HEARTBEAT_SECONDS)
      if not done:
        await _publication_heartbeat(repository, run_id, owner)
    return task.result()
  except BaseException:
    cancel.set()
    # Hold the publication lock until the actual worker has stopped.
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


@contextmanager
def publication_lock(directory: Path):
  """Serialize a run's transfers across processes; the OS releases on exit."""
  path = directory / "publication.lock"
  reject_links(path)
  with path.open("a+b") as stream:
    if os.name == "nt":
      import msvcrt

      if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
      stream.seek(0)
      msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
      import fcntl

      fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield


def freeze_publication(path: Path, bundle: TrainingBundle, owner: str) -> None:
  payload = {
    "schema_version": 1,
    "owner": owner,
    "bundle": bundle.model_dump(mode="json"),
  }
  reject_links(path)
  if path.exists():
    if read_object(path) != payload:
      raise PublicationError("PUBLICATION_EVIDENCE_CONFLICT")
    return
  descriptor, temporary = tempfile.mkstemp(prefix=".publication-", dir=path.parent)
  try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
      json.dump(payload, stream, sort_keys=True, allow_nan=False)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)  # Caller holds the per-run publication OS lock.
  finally:
    Path(temporary).unlink(missing_ok=True)


async def publish_result(config, repository, *, run_id: str, owner: str) -> dict:
  return await _publish_result(
    config, repository, run_id=run_id, owner=owner, local_supervisor=False
  )


async def publish_generated_result(
  config, repository, *, run_id: str, owner: str
) -> dict:
  return await _publish_result(
    config, repository, run_id=run_id, owner=owner, local_supervisor=True
  )


async def _publish_result(
  config, repository, *, run_id: str, owner: str, local_supervisor: bool
) -> dict:
  """Resume transfer/registration only after the recorded execution has exited."""
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id) or not owner:
    raise PublicationError("PUBLICATION_IDENTITY_INVALID")
  directory = config.state_root / "runs" / run_id
  control = config.state_root / "control" / run_id
  try:
    with publication_lock(control):
      row = await repository.get_run(run_id)
      if (
        row is None
        or row.prefect_flow_run_id != owner
        or row.status not in {"RUNNING", "SUCCEEDED"}
        or row.cancel_requested_at is not None
      ):
        raise PublicationError("PUBLICATION_OWNERSHIP_LOST")
      state = await asyncio.to_thread(
        local_success_recorded if local_supervisor else inspect_execution,
        control / "process.json",
        run_id=run_id,
        owner=owner,
        request=control / "request.json",
      )
      ready = state is True if local_supervisor else state == "EXITED"
      if not ready:
        raise PublicationError("PUBLICATION_EXECUTION_NOT_STOPPED")
      cancel = threading.Event()
      bundle = await _supervised_io(
        lambda: result_bundle(
          directory, run_id=run_id, run_kind=row.run_kind, cancel=cancel
        ),
        repository,
        run_id,
        owner,
        cancel,
      )
      manifest = read_object(directory / "manifest.json")
      spec = await repository.get_spec(row.spec_id)
      for key in ("spec_hash", "coordinate_hash", "environment_requirement_hash"):
        if (
          spec is None
          or manifest.get(key) != getattr(spec, key, None)
          or not manifest.get(key)
        ):
          raise PublicationError("RESULT_SPEC_MISMATCH")
      freeze_publication(control / "publication.json", bundle, owner)
      transfer = TransferConfig.load(
        config.transfer_config, state_root=config.state_root
      )

      def upload():
        with open_store(transfer, cancel=cancel) as store:
          if store.publish(bundle, directory) != bundle.bundle_id:
            raise PublicationError("PUBLICATION_REMOTE_IDENTITY_MISMATCH")

      await _supervised_io(upload, repository, run_id, owner, cancel)
      await repository.record_artifact_bundle(
        run_id, expected_flow_run_id=owner, bundle=bundle
      )
      manifest_hash = next(
        entry.sha256 for entry in bundle.files if entry.path == "manifest.json"
      )
      run_key = hashlib.sha256(f"next-day-selection\0v1\0{run_id}".encode()).hexdigest()
      await repository.complete_run(
        run_id,
        expected_flow_run_id=owner,
        run_key=run_key,
        artifact_manifest_sha256=manifest_hash,
        environment_evidence=safe_public_details(
          {
            "data_quality": read_object(directory / "data-quality.json"),
            "runtime": manifest.get("environment", {}),
          }
        ),
        metrics_summary=read_object(directory / "metrics.json"),
        gate_summary=manifest.get("gates", {}),
      )
      return {"status": "SUCCEEDED", "run_id": run_id, "bundle_id": bundle.bundle_id}
  except PublicationError:
    raise
  except Exception:
    raise PublicationError("PUBLICATION_RETRY_REQUIRED") from None


async def run_publication(config, *, run_id: str, owner: str) -> dict:
  from quantx_infrastructure.repositories.stock_selection_training_repository import (
    StockSelectionTrainingRepository,
  )
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

  engine = None
  try:
    engine = create_async_engine(
      config.database_url, connect_args={"timeout": 10, "command_timeout": 30}
    )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
      return await publish_result(
        config, StockSelectionTrainingRepository(session), run_id=run_id, owner=owner
      )
  except PublicationError:
    raise
  except Exception:
    raise PublicationError("PUBLICATION_DATABASE_UNAVAILABLE") from None
  finally:
    if engine is not None:
      with suppress(Exception):
        await engine.dispose()
