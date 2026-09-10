"""Publish local service/dispatch observations without probing CPU or GPU."""

import asyncio
from pathlib import Path

from quantx_contracts.trainer_status import TrainerDispatchStatus, TrainerRuntimeStatus
from quantx_infrastructure.repositories.trainer_status_repository import TrainerStatusRepository

from quantx_trainer.admission import admission_status
from quantx_trainer.dispatch_status import read_dispatch_status
from quantx_trainer.runtime import current_config
from quantx_trainer.service_status import service_status


def snapshot(config_path: Path, reason: str | None):
  config = current_config()
  service = service_status(config.state_root, config_path)
  try:
    admission = admission_status(config.state_root / "control")["admission"]
  except (OSError, ValueError):
    admission = "UNKNOWN"

  def dispatch(kind):
    value = read_dispatch_status(config.state_root, config_path, kind)
    decision = value.get("decision") or {}
    return TrainerDispatchStatus(state=value["state"], observed_at=value.get("observed_at"),
                                 status=decision.get("status"), reason=decision.get("reason"))

  return TrainerRuntimeStatus(
    service=service["service"], phase=service.get("phase"), admission=admission,
    resource_reason=reason, training=dispatch("training"), preparation=dispatch("preparation"),
  ), service.get("instance_id", "trainer-unconfirmed")


async def publish_runtime_status(db, config_path: str, reason: str | None):
  value, instance_id = await asyncio.to_thread(snapshot, Path(config_path), reason)
  await TrainerStatusRepository(db).publish(value, instance_id=instance_id)
