"""GPU qualification owned by the isolated Trainer runtime."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

from prefect import flow
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
  record_exit,
  record_spawn,
)
from quantx_infrastructure.training_result import safe_public_details

from quantx_trainer.dataset_transfer import load_dataset
from quantx_trainer.publication import read_object
from quantx_trainer.runtime import current_config, training_session
from quantx_trainer.training_flow import _host_admission_reason


class PreparationStopUnconfirmed(RuntimeError):
  pass


async def run_gpu_job(config, job, files, check):
  attempt = hashlib.sha256(job.flow_run_id.encode()).hexdigest()
  directory = config.state_root / "preparation" / job.job_id / attempt
  reject_links(directory)
  directory.mkdir(parents=True, exist_ok=True)
  gpu_root = config.state_root / "gpu"
  reject_links(gpu_root)
  gpu_root.mkdir(exist_ok=True)
  request = directory / "request.json"
  payload = {
    **job.request,
    "kind": "GPU",
    "dataset_directory": str(files["directory"]),
    "build_evidence": str(
      gpu_root / "official-wheel" / "lightgbm-4.6.0-py3-none-win_amd64.whl"
    ),
    "qualification_output": str(gpu_root / "qualification.json"),
  }
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
      creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
      if sys.platform == "win32"
      else 0,
    )
  except BaseException:
    # An interrupted asynchronous spawn may have created a child without
    # returning its handle. Preserve STARTING and never make it retryable.
    raise PreparationStopUnconfirmed("GPU_PREPARATION_SPAWN_UNCONFIRMED") from None
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
    if waiter.result() != 0:
      raise ValueError("GPU_PREPARATION_PROCESS_FAILED")
    return read_object(directory / "result.json")
  finally:
    try:
      stopped = await stop_async_process(process)
    except Exception:
      stopped = False
    if not stopped:
      waiter.cancel()
      await asyncio.gather(waiter, return_exceptions=True)
      raise PreparationStopUnconfirmed("GPU_PREPARATION_STOP_UNCONFIRMED")
    await waiter
    record_exit(evidence, returncode=process.returncode, **identity)


@flow(name="trainer-gpu-preparation", retries=0)
async def trainer_gpu_preparation_flow(config_path: str):
  async with training_session(config_path) as db:
    reason = await asyncio.to_thread(_host_admission_reason)
    if reason:
      return {"status": "QUEUED", "reason": reason}
    config = current_config()
    repository = ResearchPreparationRepository(db)
    job = await repository.claim(str(uuid.uuid4()), kinds=("GPU",))
    if job is None:
      return {"status": "IDLE"}
    job_id, owner = job.job_id, job.flow_run_id

    async def check():
      await repository.progress(job_id, expected_flow_run_id=owner)

    try:
      dataset = await StockSelectionTrainingRepository(db).get_dataset(
        job.request["dataset_version"]
      )
      files = await load_dataset(
        config, repository, dataset, run_id=job_id, owner=owner, check=check
      )
      await check()
      result = await run_gpu_job(config, job, files, check)
      status = "SUCCEEDED" if result.get("ready") is True else "FAILED"
      await repository.progress(
        job_id,
        expected_flow_run_id=owner,
        status=status,
        phase="资格验证完成",
        result=safe_public_details(result),
      )
      return {"job_id": job_id, "status": status}
    except PreparationStopUnconfirmed:
      return {
        "job_id": job_id,
        "status": "RUNNING",
        "reason": "GPU_PREPARATION_STOP_UNCONFIRMED",
      }
    except asyncio.CancelledError:
      raise
    except Exception:
      await repository.progress(
        job_id,
        expected_flow_run_id=owner,
        status="FAILED",
        phase="资格验证失败",
        error="GPU_PREPARATION_RETRY_REQUIRED",
      )
      return {"job_id": job_id, "status": "FAILED"}
