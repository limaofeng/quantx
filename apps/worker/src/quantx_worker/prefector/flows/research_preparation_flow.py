"""Durable preparation dispatcher; research runs isolated from Worker imports."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from prefect import flow
from quantx_infrastructure.async_process_stop import stop_async_process
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.services.research_preparation import (
  ResearchPreparationRepository,
  reject_links,
  root,
)
from quantx_infrastructure.services.research_preparation_window import (
  _full_live_runtime,
  is_critical_trading_window,
)
from quantx_infrastructure.training_bundle_store import publication_lock
from quantx_infrastructure.training_process_evidence import (
  begin_execution,
  inspect_execution,
  local_exit_recorded,
  record_exit,
  record_spawn,
)

from quantx_worker.prefector.flows.certification_transfer import (
  export_transfer_config,
  publish_certification_input,
)
from quantx_worker.prefector.flows.daily_market_data_sync_flow import (
  daily_market_data_sync_flow,
)
from quantx_worker.prefector.flows.durable_agent_flows import _request_and_wait


class PreparationProcessUnconfirmed(RuntimeError):
  """The child may still exist; its job must not become retryable."""


async def update_job(job_id, *, expected_flow_run_id, **values):
  async with AsyncSessionLocal() as db:
    await ResearchPreparationRepository(db).progress(job_id, expected_flow_run_id=expected_flow_run_id, **values)


def certification_execution_paths(directory: Path, owner: str):
  if not owner:
    raise ValueError("Certification execution requires an owner")
  suffix = hashlib.sha256(owner.encode()).hexdigest()
  return directory / f"request-{suffix}.json", directory / f"process-{suffix}.json"


async def run_research(job, directory: Path):
  certification = job.kind == "CERTIFY"
  request, evidence = (
    certification_execution_paths(directory, job.flow_run_id)
    if certification else (directory / "request.json", None)
  )
  result_file = directory / "result.json"
  reject_links(request)
  reject_links(result_file)
  if certification and (os.path.lexists(request) or os.path.lexists(evidence)):
    raise FileExistsError("Certification attempt evidence already exists")
  if result_file.exists():
    result_file.unlink()
  with request.open("x" if certification else "w", encoding="utf-8") as stream:
    json.dump({"kind": job.kind, **job.request}, stream)
    stream.flush()
    os.fsync(stream.fileno())
  if certification:
    identity = dict(run_id=job.job_id, owner=job.flow_run_id, request=request)
    begin_execution(evidence, **identity)
  try:
    process = await asyncio.create_subprocess_exec(
      sys.executable,
      "-m",
      "quantx_research.preparation_job",
      str(request),
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
  except BaseException:
    raise PreparationProcessUnconfirmed("PREPARATION_PROCESS_SPAWN_UNCONFIRMED") from None
  try:
    if certification:
      record_spawn(evidence, process=SimpleNamespace(pid=process.pid, poll=lambda: process.returncode), **identity)
    await process.wait()
    if process.returncode != 0 or not result_file.is_file():
      raise RuntimeError("Research 子进程未完成，请检查运行端依赖")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
      raise RuntimeError("Research 结果格式无效")
    return result
  finally:
    try:
      stopped = await stop_async_process(process)
    except Exception:
      stopped = False
    if not stopped:
      raise PreparationProcessUnconfirmed("PREPARATION_PROCESS_STOP_UNCONFIRMED")
    if certification:
      record_exit(evidence, returncode=process.returncode, **identity)


async def keep_alive(job_id, owner):
  while True:
    await asyncio.sleep(20)
    await update_job(job_id, expected_flow_run_id=owner)


async def perform(job, directory):
  if job.kind == "CERTIFY":
    with publication_lock(directory):
      return await _perform(job, directory)
  return await _perform(job, directory)


async def _perform(job, directory):
  if job.kind == "GPU":
    raise ValueError("GPU preparation belongs to Trainer")
  transfer = export_transfer_config() if job.kind == "CERTIFY" else None
  await update_job(
    job.job_id, expected_flow_run_id=job.flow_run_id,
    phase={
      "COVERAGE": "检查覆盖",
      "DOWNLOAD": "检查下载范围",
      "CERTIFY": "检查并导出冻结输入",
      "GPU": "执行资格基准",
    }[job.kind],
  )
  if job.kind == "DOWNLOAD" and job.request.get("download_plan"):
    result = {
      "download": job.request["download_plan"],
      "stock_codes": job.request["download_codes"],
      "downloadable": True,
    }
  else:
    result = await run_research(job, directory)
  if result.get("error"):
    await update_job(
      job.job_id, expected_flow_run_id=job.flow_run_id,
      status="FAILED",
      phase="执行失败",
      error=result["error"],
      result=result,
    )
    return
  if job.kind == "DOWNLOAD":
    params = result.get("download")
    if (
      result.get("downloadable") is not True
      or not isinstance(params, dict)
      or len(params.get("stock_list", [])) < 2
    ):
      raise RuntimeError("缺少可下载股票范围，请检查证券基础数据或明确填写股票代码")
    job.request = {
      **job.request,
      "download_plan": params,
      "download_codes": result["stock_codes"],
    }
    await update_job(
      job.job_id, expected_flow_run_id=job.flow_run_id,
      phase="下载与持久化",
      result=public_result(result),
      request=job.request,
    )
    transfer = await daily_market_data_sync_flow(
      **params, idempotency_scope=f"research-preparation-{job.job_id}"
    )
    await update_job(job.job_id, expected_flow_run_id=job.flow_run_id, phase="同步复权依赖")
    codes = result["stock_codes"]
    for offset in range(0, len(codes), 100):
      await _request_and_wait(
        {
          "operation": "divid_factors",
          "source": "qmt-get-divid-factors-v1",
          "stock_list": codes[offset : offset + 100],
          "start_time": params["start_time"],
          "end_time": params["end_time"],
        },
        required_capabilities=["market-data", "divid-factors"],
        idempotency_scope=f"research-preparation-{job.job_id}-factors-{offset}",
      )
    result["download_result"] = {
      key: transfer.get(key)
      for key in ("status", "stock_count", "start_time", "end_time")
    }
    # Refresh coverage after persistence; gaps remain visible without auto-certifying.
    await update_job(job.job_id, expected_flow_run_id=job.flow_run_id, phase="下载后复查")
    refreshed = await run_research(job, directory)
    if refreshed.get("error"):
      await update_job(
        job.job_id, expected_flow_run_id=job.flow_run_id,
        status="FAILED",
        phase="复查失败",
        error=refreshed["error"],
        result=public_result(refreshed),
      )
      return
    refreshed["download_result"] = result["download_result"]
    result = refreshed
  failed = job.kind in {"CERTIFY", "GPU"} and result.get("ready") is not True
  if job.kind == "CERTIFY" and not failed:
    reference = await publish_certification_input(job, directory, result, transfer)
    async with AsyncSessionLocal() as db:
      await ResearchPreparationRepository(db).handoff_certification(
        job.job_id, expected_flow_run_id=job.flow_run_id, reference=reference,
      )
    return
  await update_job(
    job.job_id, expected_flow_run_id=job.flow_run_id,
    status="FAILED" if failed else "SUCCEEDED",
    phase="检查未通过" if failed else "完成",
    result=public_result(result),
    error="准备条件或资格门禁未通过，请查看检查项" if failed else None,
  )


def public_result(result):
  return {
    key: value
    for key, value in result.items()
    if key not in {"download", "stock_codes"}
  }


async def recover_certification_exports():
  async with AsyncSessionLocal() as db:
    jobs = await ResearchPreparationRepository(db).running_jobs(kinds=("CERTIFY",))
    for job in jobs:
      db.expunge(job)
  recovered = []
  for job in jobs:
    if job.request.get("certification_input"):
      continue  # Trainer owns the subsequent phase.
    directory = root() / ".runtime/research-preparation" / job.job_id
    try:
      reject_links(directory)
      if not directory.is_dir():
        continue
      with publication_lock(directory):
        request, evidence = certification_execution_paths(directory, job.flow_run_id)
        identity = dict(run_id=job.job_id, owner=job.flow_run_id, request=request)
        if inspect_execution(evidence, **identity) != "EXITED" and not local_exit_recorded(evidence, **identity):
          continue
        process = json.loads(evidence.read_text(encoding="utf-8"))
        if process.get("state") != "EXITED" or type(process.get("returncode")) is not int or process["returncode"] != 0:
          continue
        executed = json.loads(request.read_text(encoding="utf-8"))
        if executed != {"kind": job.kind, **job.request}:
          continue
        result_file = directory / "result.json"
        reject_links(result_file)
        if result_file.stat().st_size > 8 * 1024 * 1024:
          continue
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or result.get("ready") is not True:
          continue

        async def check():
          await update_job(job.job_id, expected_flow_run_id=job.flow_run_id)

        reference = await publish_certification_input(job, directory, result, export_transfer_config(), check=check)
        async with AsyncSessionLocal() as db:
          await ResearchPreparationRepository(db).handoff_certification(job.job_id, expected_flow_run_id=job.flow_run_id, reference=reference)
        recovered.append(job.job_id)
    except Exception:
      continue  # Unknown exit, disconnected control/store, or an active publisher.
  return recovered


@flow(name="research-preparation-dispatch", retries=0)
async def research_preparation_dispatch_flow():
  await recover_certification_exports()
  if _full_live_runtime() and await is_critical_trading_window():
    return {"status": "QUEUED", "reason": "TRADING_CRITICAL_WINDOW"}
  async with AsyncSessionLocal() as db:
    job = await ResearchPreparationRepository(db).claim(
      str(uuid.uuid4()), kinds=("COVERAGE", "DOWNLOAD", "CERTIFY"), executor="WORKER",
    )
    if job is None:
      return {"status": "IDLE"}
    db.expunge(job)
  directory = root() / ".runtime/research-preparation" / job.job_id
  try:
    reject_links(directory)
    directory.mkdir(parents=True, exist_ok=True)
  except (ValueError, OSError):
    await update_job(
      job.job_id, expected_flow_run_id=job.flow_run_id,
      status="FAILED",
      phase="目录检查失败",
      error="准备任务目录不安全或不可写，请检查运行端目录配置",
    )
    return {"job_id": job.job_id}
  heartbeat = asyncio.create_task(keep_alive(job.job_id, job.flow_run_id))
  work = asyncio.create_task(perform(job, directory))
  failure = None
  try:
    done, _ = await asyncio.wait({heartbeat, work}, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
      task.result()
  except asyncio.CancelledError:
    failure = {"phase": "执行中断", "error": "Worker 已停止，可重试此任务"}
    raise
  except Exception:
    failure = {"phase": "执行失败", "error": "执行或心跳失败，请检查运行端依赖、数据源和 Prefect 任务状态"}
  finally:
    work.cancel()
    heartbeat.cancel()
    stopped = await asyncio.gather(work, heartbeat, return_exceptions=True)
    unconfirmed = any(isinstance(value, PreparationProcessUnconfirmed) for value in stopped)
    if failure and job.kind == "CERTIFY":
      # A commit acknowledgement or heartbeat may fail after ownership moved.
      # Observe the durable handoff before trying to mark the old owner failed.
      async with AsyncSessionLocal() as db:
        handed_off = await ResearchPreparationRepository(db).certification_handoff_status(
          job.job_id, expected_flow_run_id=job.flow_run_id,
        )
      if handed_off is not None:
        failure = None
    if failure and not unconfirmed:
      await update_job(job.job_id, expected_flow_run_id=job.flow_run_id, status="FAILED", **failure)
  return {"job_id": job.job_id, **({"status": "RUNNING", "reason": "PREPARATION_PROCESS_STOP_UNCONFIRMED"} if unconfirmed else {})}
