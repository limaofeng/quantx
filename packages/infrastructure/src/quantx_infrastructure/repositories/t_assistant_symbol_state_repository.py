"""CAS persistence for independent T-assistant per-symbol state."""

from __future__ import annotations

import uuid
from typing import Iterable, Optional

from quantx_domain.trading.t_assistant_market_state import TAssistantSymbolState
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantSymbolStateRecord,
)


class TAssistantSymbolStateConflict(RuntimeError):
  pass


class TAssistantSymbolStateRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def get(
    self,
    *,
    execution_id: str,
    instrument_code: str,
    for_update: bool = False,
  ) -> Optional[TAssistantSymbolStateRecord]:
    statement = select(TAssistantSymbolStateRecord).where(
      TAssistantSymbolStateRecord.execution_id == execution_id,
      TAssistantSymbolStateRecord.instrument_code
      == str(instrument_code or "").strip().upper(),
    )
    if for_update:
      statement = statement.with_for_update()
    result = await self.db.execute(statement)
    return result.scalar_one_or_none()

  async def list_for_execution(
    self,
    execution_id: str,
  ) -> list[TAssistantSymbolStateRecord]:
    result = await self.db.execute(
      select(TAssistantSymbolStateRecord)
      .where(TAssistantSymbolStateRecord.execution_id == execution_id)
      .order_by(TAssistantSymbolStateRecord.instrument_code.asc())
    )
    return list(result.scalars().all())

  async def load_domains(
    self,
    execution_id: str,
  ) -> dict[str, TAssistantSymbolState]:
    rows = await self.list_for_execution(execution_id)
    return {
      row.instrument_code: TAssistantSymbolState.from_dict(row.state_payload)
      for row in rows
    }

  async def apply_material_states(
    self,
    states: Iterable[TAssistantSymbolState],
    *,
    expected_revisions: dict[str, int],
  ) -> tuple[TAssistantSymbolStateRecord, ...]:
    records: list[TAssistantSymbolStateRecord] = []
    for state in sorted(states, key=lambda item: item.instrument_code):
      expected = expected_revisions.get(state.instrument_code)
      if expected is None or state.revision != expected + 1:
        raise TAssistantSymbolStateConflict("T_SYMBOL_STATE_REVISION_INVALID")
      existing = await self.get(
        execution_id=state.execution_id,
        instrument_code=state.instrument_code,
        for_update=True,
      )
      values = _state_values(state)
      if existing is None:
        if expected != 0:
          raise TAssistantSymbolStateConflict("T_SYMBOL_STATE_REVISION_CONFLICT")
        existing = TAssistantSymbolStateRecord(
          state_id=str(uuid.uuid4()),
          execution_id=state.execution_id,
          instrument_code=state.instrument_code,
          **values,
        )
        self.db.add(existing)
      else:
        result = await self.db.execute(
          update(TAssistantSymbolStateRecord)
          .where(
            TAssistantSymbolStateRecord.state_id == existing.state_id,
            TAssistantSymbolStateRecord.revision == expected,
          )
          .values(**values)
        )
        if int(result.rowcount or 0) != 1:
          raise TAssistantSymbolStateConflict("T_SYMBOL_STATE_REVISION_CONFLICT")
      records.append(existing)
    await self.db.flush()
    return tuple(records)


def _state_values(state: TAssistantSymbolState) -> dict:
  cursor = state.cursor
  identity = cursor.source_identity if cursor else None
  return {
    "revision": state.revision,
    "lifecycle": state.lifecycle.value,
    "stream_id": cursor.stream_id if cursor else None,
    "continuity_generation": cursor.continuity_generation if cursor else None,
    "ring_generation": cursor.ring_generation if cursor else 0,
    "last_accepted_sequence": cursor.accepted_sequence if cursor else 0,
    "last_source_time_ms": identity.source_time_ms if identity else 0,
    "last_tick_ordinal": identity.tick_ordinal if identity else 0,
    "policy_version": state.policy_version,
    "feature_schema_version": state.feature_schema_version,
    "state_payload": state.to_dict(),
    "material_manifest_hash": state.material_manifest_hash,
    "rewarm_reason": state.rewarm_reason,
  }


__all__ = [
  "TAssistantSymbolStateConflict",
  "TAssistantSymbolStateRepository",
]
