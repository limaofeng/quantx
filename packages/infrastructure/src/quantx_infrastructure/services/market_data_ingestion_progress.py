"""Lease-fenced persistence of ingestion progress."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol


def evidence_hash(value: Any) -> str:
  return hashlib.sha256(
    json.dumps(
      value,
      sort_keys=True,
      separators=(",", ":"),
      allow_nan=False,
    ).encode()
  ).hexdigest()


class ProgressStore(Protocol):
  async def mutate_market_data_ingestion(
    self,
    request_id: str,
    *,
    claim_token: str,
    action: str,
    values: dict[str, Any] | None = None,
  ) -> dict[str, Any]: ...


class IngestionProgress:
  def __init__(self, store: ProgressStore, request_id: str, claim_token: str):
    self.store, self.request_id, self.claim_token = store, request_id, claim_token
    self.state: dict[str, Any] = {}

  async def apply(self, action: str, **values: Any) -> dict[str, Any]:
    self.state = await self.store.mutate_market_data_ingestion(
      self.request_id,
      claim_token=self.claim_token,
      action=action,
      values=values,
    )
    return self.state

  async def confirmed(self, block: int, digest: str, rows: int) -> bool:
    await self.apply("check", block=block, sha256=digest, rows=rows)
    return str(block) in self.state["checkpoints"]

  async def confirm(self, block: int, digest: str, rows: int) -> None:
    await self.apply("checkpoint", block=block, sha256=digest, rows=rows)
