"""Certification and GPU qualification owned by the isolated Trainer runtime."""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from types import SimpleNamespace

from prefect import flow, get_run_logger
from quantx_infrastructure.async_process_stop import stop_async_process
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
)
from quantx_infrastructure.services.research_preparation import (
  ResearchPreparationRepository,
)
from quantx_infrastructure.training_bundle_store import reject_links
from quantx_infrastructure.training_process_evidence import (
  begin_execution,
  inspect_execution,
  inspect_input_preparation,
  local_exit_recorded,
  record_exit,
  record_spawn,
)
from quantx_infrastructure.training_result import safe_public_details

from quantx_trainer.dataset_transfer import load_certification_input, load_dataset
from quantx_trainer.publication import publication_lock, read_object
from quantx_trainer.runtime import current_config, training_session
from quantx_trainer.training_flow import (
  _host_admission_reason,
  _input_attempt,
  _input_preparation_paths,
)


class PreparationStopUnconfirmed(RuntimeError):
  pass


class PreparationAdmissionDenied(RuntimeError):
  pass


def attempt_directory(config, job):
  if (
    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", job.job_id)
    or not job.flow_run_id
  ):
    raise ValueError("GPU_PREPARATION_IDENTITY_INVALID")
  attempt = hashlib.sha256(job.flow_run_id.encode()).hexdigest()
  directory = config.state_root / "preparation" / job.job_id / attempt
  reject_links(directory)
  return directory


async def recover_preparation_results(config, repository, datasets):
  recovered = []
  for job in await repository.running_jobs(kinds=("GPU", "CERTIFY")):
    if job.kind == "CERTIFY" and not job.request.get("certification_input"):
      continue  # Worker still owns the export phase.
    try:
      directory = attempt_directory(config, job)
      if not os.path.lexists(directory / "process.json"):
        record, request = _input_preparation_paths(
          config.state_root / "control" / job.job_id, job.flow_run_id
        )
        if (
          inspect_input_preparation(
            record, run_id=job.job_id, owner=job.flow_run_id, request=request
          )
          == "EXITED"
        ):
          await repository.requeue_trainer_inputs(
            job.job_id, expected_flow_run_id=job.flow_run_id
          )
          recovered.append(job.job_id)
        continue
      # Never manufacture missing evidence directories while inspecting jobs.
      if not directory.is_dir():
        continue
      with publication_lock(directory):
        evidence = directory / "process.json"
        identity = dict(
          run_id=job.job_id, owner=job.flow_run_id, request=directory / "request.json"
        )
        state = inspect_execution(evidence, **identity)
        if state != "EXITED" and not local_exit_recorded(evidence, **identity):
          continue
        process = read_object(evidence)
        if (
          process.get("state") != "EXITED" or type(process.get("returncode")) is not int
        ):
          continue
        if process["returncode"] == 75:
          await repository.requeue_trainer_admission(
            job.job_id, expected_flow_run_id=job.flow_run_id
          )
          recovered.append(job.job_id)
          continue
        if process["returncode"] != 0:
          await repository.progress(
            job.job_id,
            expected_flow_run_id=job.flow_run_id,
            status="FAILED",
            phase="准备进程退出",
            error="PREPARATION_PROCESS_FAILED",
          )
          recovered.append(job.job_id)
          continue
        result = read_object(directory / "result.json")
        if type(result.get("ready")) is not bool:
          continue
        await _finish_preparation_result(config, repository, datasets, job, result)
        recovered.append(job.job_id)
    except Exception:
      continue  # Retain local facts when locked, unverifiable or disconnected.
  return recovered


async def _finish_preparation_result(config, repository, datasets, job, result):
  if job.kind == "CERTIFY" and result["ready"]:
    from quantx_trainer.certification_result import finalize_certification

    return await finalize_certification(config, repository, datasets, job)
  status = "SUCCEEDED" if result["ready"] else "FAILED"
  await repository.progress(
    job.job_id, expected_flow_run_id=job.flow_run_id, status=status,
    phase="准备完成" if result["ready"] else "准备检查未通过",
    result=safe_public_details(result),
  )
  return {"job_id": job.job_id, "status": status}


async def run_gpu_job(config, job, files, check):
  directory = attempt_directory(config, job)
  directory.mkdir(parents=True, exist_ok=True)
  gpu_root = config.state_root / "gpu"
  reject_links(gpu_root)
  gpu_root.mkdir(exist_ok=True)
  payload = {
    **job.request,
    "kind": "GPU",
    "dataset_directory": str(files["directory"]),
    "build_evidence": str(
      gpu_root / "official-wheel" / "lightgbm-4.6.0-py3-none-win_amd64.whl"
    ),
    "qualification_output": str(gpu_root / "qualification.json"),
  }
  return await _run_preparation_process(config, job, payload, check)


async def run_certification_job(config, job, files, check):
  payload = {
    "kind": "CERTIFY_FROZEN",
    "dataset_version": job.request["dataset_version"],
    "certification_input": job.request["certification_input"],
    "input_directory": str(files["directory"]),
    "output_root": str(config.state_root / "datasets"),
  }
  return await _run_preparation_process(config, job, payload, check)


async def _run_preparation_process(config, job, payload, check):
  directory = attempt_directory(config, job)
  directory.mkdir(parents=True, exist_ok=True)
  request = directory / "request.json"
  label = "GPU" if payload["kind"] == "GPU" else "CERTIFICATION"
  with request.open("x", encoding="utf-8") as stream:
    json.dump(payload, stream)
    stream.flush()
    os.fsync(stream.fileno())
  evidence = directory / "process.json"
  identity = dict(run_id=job.job_id, owner=job.flow_run_id, request=request)
  begin_execution(evidence, **identity)
  try:
    process = await asyncio.create_subprocess_exec(
      sys.executable,
      "-m",
      "quantx_research.preparation_job",
      str(request),
      env=config.research_environment(os.environ),
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      creationflags=(
        subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
      )
      if sys.platform == "win32"
      else 0,
    )
  except BaseException:
    # An interrupted asynchronous spawn may have created a child without
    # returning its handle. Preserve STARTING and never make it retryable.
    raise PreparationStopUnconfirmed(f"{label}_PREPARATION_SPAWN_UNCONFIRMED") from None
  waiter = asyncio.create_task(process.wait())
  try:
    record_spawn(
      evidence,
      process=SimpleNamespace(pid=process.pid, poll=lambda: process.returncode),
      **identity,
    )
    while not waiter.done():
      done, _ = await asyncio.wait({waiter}, timeout=10)
      if not done:
        await check()
    if waiter.result() == 75:
      raise PreparationAdmissionDenied(f"{label}_PREPARATION_HOST_ADMISSION_DENIED")
    if waiter.result() != 0:
      raise ValueError(f"{label}_PREPARATION_PROCESS_FAILED")
    return read_object(directory / "result.json")
  finally:
    try:
      stopped = await stop_async_process(process)
    except Exception:
      stopped = False
    if not stopped:
      waiter.cancel()
      await asyncio.gather(waiter, return_exceptions=True)
      raise PreparationStopUnconfirmed(f"{label}_PREPARATION_STOP_UNCONFIRMED")
    await waiter
    record_exit(evidence, returncode=process.returncode, **identity)


@flow(name="trainer-preparation", retries=0)
async def trainer_preparation_flow(config_path: str):
  async with training_session(config_path) as db:
    config = current_config()
    repository = ResearchPreparationRepository(db)
    datasets = StockSelectionTrainingRepository(db)
    recovered = await recover_preparation_results(config, repository, datasets)
    reason = await asyncio.to_thread(_host_admission_reason)
    if reason:
      return {"status": "QUEUED", "reason": reason}
    with _input_attempt(get_run_logger()) as prepare:
      from quantx_trainer.admission import TrainerAdmissionClosed

      try:
        job = await repository.claim(
          str(uuid.uuid4()), kinds=("GPU", "CERTIFY"), executor="TRAINER", prepare_execution=prepare
        )
      except TrainerAdmissionClosed as exc:
        return {"status": "QUEUED", "reason": str(exc)}
      if job is None:
        return {"status": "IDLE", "recovered_job_ids": recovered}
      job_id, owner = job.job_id, job.flow_run_id

      async def check():
        await repository.progress(job_id, expected_flow_run_id=owner)

      directory = attempt_directory(config, job)
      directory.mkdir(parents=True, exist_ok=True)
      with publication_lock(directory):
        registration_started = False
        try:
          if job.kind == "CERTIFY":
            files = await load_certification_input(config, repository, job, check=check)
            await check()
            result = await run_certification_job(config, job, files, check)
          else:
            dataset = await datasets.get_dataset(job.request["dataset_version"])
            files = await load_dataset(config, repository, dataset, run_id=job_id, owner=owner, check=check)
            await check()
            result = await run_gpu_job(config, job, files, check)
          if type(result.get("ready")) is not bool:
            raise ValueError("PREPARATION_RESULT_INVALID")
          registration_started = True
          return await _finish_preparation_result(config, repository, datasets, job, result)
        except PreparationAdmissionDenied:
          await repository.requeue_trainer_admission(job_id, expected_flow_run_id=owner)
          return {
            "job_id": job_id,
            "status": "QUEUED",
            "reason": "HOST_ADMISSION_DENIED",
          }
        except PreparationStopUnconfirmed:
          return {
            "job_id": job_id,
            "status": "RUNNING",
            "reason": "PREPARATION_STOP_UNCONFIRMED",
          }
        except asyncio.CancelledError:
          raise
        except Exception:
          if registration_started:
            return {
              "job_id": job_id,
              "status": "RUNNING",
              "reason": "PREPARATION_RESULT_REGISTRATION_PENDING",
            }
          await repository.progress(
            job_id,
            expected_flow_run_id=owner,
            status="FAILED",
            phase="准备执行失败",
            error="PREPARATION_RETRY_REQUIRED",
          )
          return {"job_id": job_id, "status": "FAILED"}
