"""Converge stopped executions without opening admission or starting computation."""

from pathlib import Path

from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
)
from quantx_infrastructure.services.research_preparation import (
  ResearchPreparationRepository,
)
from quantx_infrastructure.training_bundle_store import publication_lock

from quantx_trainer.admission import admission_status
from quantx_trainer.config import TrainerConfig
from quantx_trainer.preparation_flow import recover_preparation_results
from quantx_trainer.runtime import current_config, training_session
from quantx_trainer.service_exit import confirmed_group_exit
from quantx_trainer.training_flow import recover_lost_training_runs


async def reconcile_stopped_service(config_path: Path):
  """Uncertain evidence remains RUNNING and explicitly blocks reconciliation."""
  pending = {"database_state": "PENDING", "reason": "TRAINER_RECONCILIATION_PENDING"}
  try:
    config = TrainerConfig.load(config_path)
    # Same order as up/down. Also exclude manual foreground starts and resume
    # for the duration of the recovery, including publication I/O.
    with publication_lock(config.state_root / "service-launches"):
      with publication_lock(config.state_root / "service"):
        with publication_lock(config.state_root / "control" / "admission"):
          if admission_status(config.state_root / "control")[
            "admission"
          ] != "DRAINING" or not confirmed_group_exit(config.state_root, config_path):
            return pending
          async with training_session(str(config_path)) as db:
            if current_config() != config:
              return pending
            training = StockSelectionTrainingRepository(db)
            preparation = ResearchPreparationRepository(db)
            recovered_runs = await recover_lost_training_runs(training)
            recovered_jobs = await recover_preparation_results(
              config, preparation, training
            )
            remaining_runs = await training.list_runs(status="RUNNING")
            remaining_jobs = [
              job
              for job in await preparation.running_jobs(kinds=("GPU", "CERTIFY"))
              if job.kind == "GPU" or job.request.get("certification_input")
            ]
            return {
              "database_state": "PENDING"
              if remaining_runs or remaining_jobs
              else "RECONCILED",
              "recovered_run_ids": recovered_runs,
              "recovered_job_ids": recovered_jobs,
              "pending_run_ids": [row.run_id for row in remaining_runs],
              "pending_job_ids": [row.job_id for row in remaining_jobs],
            }
  except Exception:
    # Files and ownership remain authoritative when the control plane is down.
    return pending
