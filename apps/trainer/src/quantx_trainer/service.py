"""Foreground Trainer service, entered only after explicit runtime validation."""

import asyncio
import os
import sys
import time
from pathlib import Path

from quantx_infrastructure.training_bundle_store import publication_lock, reject_links


async def _run_worker(config, config_path: Path, report=None):
  # Import Prefect only after the fresh CLI process has isolated its environment.
  from prefect.client.schemas.objects import ConcurrencyLimitConfig
  from prefect.client.schemas.schedules import CronSchedule
  from prefect.workers.process import ProcessWorker

  from quantx_trainer.preparation_flow import trainer_preparation_flow
  from quantx_trainer.training_flow import (
    stock_selection_training_capability_flow,
    stock_selection_training_dispatch_flow,
  )

  deployments = (
    ("trainer-preparation", trainer_preparation_flow),
    ("stock-selection-training-dispatch", stock_selection_training_dispatch_flow),
    ("stock-selection-training-capability", stock_selection_training_capability_flow),
  )
  for name, flow in deployments:
    deployment = await flow.to_deployment(
      name=name,
      schedules=[
        {"schedule": CronSchedule(cron="* * * * *", timezone="Asia/Shanghai")}
      ],
      parameters={"config_path": str(config_path)},
      work_pool_name=config.prefect_pool,
      work_queue_name="default",
      concurrency_limit=ConcurrencyLimitConfig(
        limit=1, collision_strategy="CANCEL_NEW"
      ),
      job_variables={"working_dir": str(config.code_root)},
    )
    await deployment.apply()
  worker = ProcessWorker(
    work_pool_name=config.prefect_pool,
    work_queues=["default"],
    name="quantx-trainer",
    create_pool_if_not_found=False,
    limit=3,
  )
  if report is not None:
    report("WORKER_LOOP")
  await worker.start()


def serve(config, config_path: Path) -> None:
  """Hold one local service lease; never inherit production/Python settings."""
  from quantx_trainer.preflight import preflight
  from quantx_trainer.service_status import ServiceReporter
  from quantx_trainer.service_stop import stop_requested

  config_path = config_path.resolve(strict=True)
  root = config.state_root / "service"
  reject_links(root)
  root.mkdir(parents=True, exist_ok=True)
  with publication_lock(root):
    reporter = ServiceReporter(root, config_path)
    reporter.write()
    original_environment = dict(os.environ)
    original_directory = Path.cwd()
    environment = config.child_environment(original_environment)
    # Worker tasks load the explicit local config; never store credentials in
    # Prefect deployment job variables or command-line arguments.
    environment.pop("DATABASE_URL", None)
    environment.update(
      {
        "QUANTX_TRAINER_CONFIG": str(config_path),
        "PREFECT_HOME": str(config.state_root / "prefect"),
        "PREFECT_PROFILES_PATH": str(config.state_root / "prefect" / "profiles.toml"),
        "PREFECT_SERVER_ALLOW_EPHEMERAL_MODE": "false",
        "PREFECT_LOGGING_TO_API_ENABLED": "false",
      }
    )
    try:
      os.environ.clear()
      os.environ.update(environment)
      os.chdir(config.code_root)
      if sys.platform == "win32":
        from quantx_trainer import contained_process
        from quantx_trainer.windows_service_job import service_job_name

        contained_process._JOB_HANDLE = contained_process._enter_job(
          name=service_job_name(reporter.identity["instance_id"])
        )

      async def run():
        async def heartbeat():
          next_heartbeat = time.monotonic() + 10
          while True:
            if stop_requested(root, reporter.identity["instance_id"]):
              reporter.write("STOPPING")
              return
            if time.monotonic() >= next_heartbeat:
              reporter.write()
              next_heartbeat = time.monotonic() + 10
            await asyncio.sleep(1)

        async def work():
          await preflight(config)
          reporter.write("REGISTERING")
          await _run_worker(config, config_path, reporter.write)

        task = asyncio.create_task(work())
        monitor = asyncio.create_task(heartbeat())
        try:
          done, _ = await asyncio.wait(
            {task, monitor}, return_when=asyncio.FIRST_COMPLETED
          )
          for completed in done:
            await completed
        finally:
          task.cancel()
          monitor.cancel()
          await asyncio.gather(task, monitor, return_exceptions=True)

      asyncio.run(run())
    except BaseException as exc:
      reporter.event(
        "SERVICE_INTERRUPTED"
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))
        else "SERVICE_FAILED"
      )
      raise
    finally:
      os.chdir(original_directory)
      os.environ.clear()
      os.environ.update(original_environment)
      reporter.write("EXITING")
