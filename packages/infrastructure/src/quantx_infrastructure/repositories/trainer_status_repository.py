"""Independent service observations, separate from CPU/GPU certificates."""

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from quantx_contracts.trainer_status import TrainerDispatchStatus, TrainerRuntimeStatus

from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat

COMPONENT = "trainer"
MAX_AGE_SECONDS = 90


class TrainerStatusRepository:
  def __init__(self, db):
    self.db = db

  async def publish(self, snapshot: TrainerRuntimeStatus, *, instance_id: str):
    row = await self.db.get(RuntimeComponentHeartbeat, COMPONENT)
    values = dict(instance_id=instance_id, status=snapshot.service,
                  details=snapshot.model_dump(mode="json"),
                  updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
    if row is None:
      self.db.add(RuntimeComponentHeartbeat(component=COMPONENT, **values))
    else:
      for name, value in values.items():
        setattr(row, name, value)
    await self.db.commit()

  async def read(self, *, now=None):
    row = await self.db.get(RuntimeComponentHeartbeat, COMPONENT)
    updated = row.updated_at if row else None
    if updated is not None and updated.tzinfo is None:
      updated = updated.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    fresh = updated is not None and timedelta(seconds=-5) <= current - updated <= timedelta(seconds=MAX_AGE_SECONDS)
    snapshot = TrainerRuntimeStatus()
    if fresh:
      try:
        snapshot = TrainerRuntimeStatus.model_validate(row.details)
      except (ValidationError, TypeError):
        fresh = False
    for kind in ("training", "preparation"):
      dispatch = getattr(snapshot, kind)
      if dispatch.state == "FRESH" and (
        dispatch.observed_at is None or not 0 <= current.timestamp() - dispatch.observed_at <= MAX_AGE_SECONDS
      ):
        snapshot = snapshot.model_copy(update={kind: TrainerDispatchStatus(state="STALE")})
    return {**snapshot.model_dump(mode="json"), "fresh": fresh, "updated_at": updated}
