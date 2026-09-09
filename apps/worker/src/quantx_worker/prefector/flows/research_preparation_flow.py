"""Durable preparation dispatcher; research runs isolated from Worker imports."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from prefect import flow
from prefect.runtime import flow_run
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
)
from quantx_infrastructure.services.research_preparation import (
  ResearchPreparationRepository,
  reject_links,
  root,
)
from quantx_infrastructure.services.research_preparation_window import (
  _full_live_runtime,
  is_critical_trading_window,
)
from quantx_infrastructure.training_dataset_store import (
  certification_values,
  resolve_dataset_directory,
)

from quantx_worker.prefector.flows.daily_market_data_sync_flow import (
  daily_market_data_sync_flow,
)
from quantx_worker.prefector.flows.durable_agent_flows import _request_and_wait


async def update_job(job_id, **values):
  async with AsyncSessionLocal() as db:
    await ResearchPreparationRepository(db).progress(job_id, **values)


async def run_research(job, directory: Path):
  request = directory / "request.json"
  result_file = directory / "result.json"
  reject_links(request)
  reject_links(result_file)
  if result_file.exists():
    result_file.unlink()
  request.write_text(json.dumps({"kind": job.kind, **job.request}), encoding="utf-8")
  process = await asyncio.create_subprocess_exec(
    sys.executable,
    "-m",
    "quantx_research.preparation_job",
    str(request),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
  )
  try:
    await process.wait()
    if process.returncode != 0 or not result_file.is_file():
      raise RuntimeError("Research 子进程未完成，请检查运行端依赖")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
      raise RuntimeError("Research 结果格式无效")
    return result
  finally:
    if process.returncode is None:
      process.terminate()
      await process.wait()


async def keep_alive(job_id):
  while True:
    await asyncio.sleep(20)
    await update_job(job_id)


async def perform(job, directory):
  if job.kind == "GPU":
    async with AsyncSessionLocal() as db:
      dataset = await StockSelectionTrainingRepository(db).get_dataset(
        job.request["dataset_version"]
      )
      await asyncio.to_thread(resolve_dataset_directory, dataset)
  await update_job(
    job.job_id,
    phase={
      "COVERAGE": "检查覆盖",
      "DOWNLOAD": "检查下载范围",
      "CERTIFY": "检查并认证",
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
      job.job_id,
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
      job.job_id,
      phase="下载与持久化",
      result=public_result(result),
      request=job.request,
    )
    transfer = await daily_market_data_sync_flow(
      **params, idempotency_scope=f"research-preparation-{job.job_id}"
    )
    await update_job(job.job_id, phase="同步复权依赖")
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
    await update_job(job.job_id, phase="下载后复查")
    refreshed = await run_research(job, directory)
    if refreshed.get("error"):
      await update_job(
        job.job_id,
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
    version = job.request["dataset_version"]
    if result.get("dataset_version") != version:
      raise ValueError("Research certification returned another dataset identity")
    values = await asyncio.to_thread(
      certification_values, dataset_version=version, manifest_sha256=result["manifest_sha256"],
    )
    async with AsyncSessionLocal() as db:
      await StockSelectionTrainingRepository(db).certify_dataset(values)
  await update_job(
    job.job_id,
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


@flow(name="research-preparation-dispatch", retries=0)
async def research_preparation_dispatch_flow():
  if _full_live_runtime() and await is_critical_trading_window():
    return {"status": "QUEUED", "reason": "TRADING_CRITICAL_WINDOW"}
  async with AsyncSessionLocal() as db:
    job = await ResearchPreparationRepository(db).claim(
      str(flow_run.id or "research-preparation-dispatch")
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
      job.job_id,
      status="FAILED",
      phase="目录检查失败",
      error="准备任务目录不安全或不可写，请检查运行端目录配置",
    )
    return {"job_id": job.job_id}
  heartbeat = asyncio.create_task(keep_alive(job.job_id))
  work = asyncio.create_task(perform(job, directory))
  try:
    done, _ = await asyncio.wait({heartbeat, work}, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
      task.result()
  except asyncio.CancelledError:
    await update_job(
      job.job_id, status="FAILED", phase="执行中断", error="Worker 已停止，可重试此任务"
    )
    raise
  except Exception:
    await update_job(
      job.job_id,
      status="FAILED",
      phase="执行失败",
      error="执行或心跳失败，请检查运行端依赖、数据源和 Prefect 任务状态",
    )
  finally:
    work.cancel()
    heartbeat.cancel()
    await asyncio.gather(work, heartbeat, return_exceptions=True)
  return {"job_id": job.job_id}
