"""Bounded history-token confirmation client for the native-unit executor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import httpx
from quantx_contracts.collection_permit import CollectionPermit
from quantx_contracts.collection_receipt import (
  CollectionCompletion,
  CollectionReceipt,
  CollectionReceiptStatus,
)

from .native_unit_artifact import NativeUnitArtifact


class CollectionReceiptRejected(RuntimeError):
  reason_code = "COLLECTION_RECEIPT_REJECTED"


class HistoryReceiptClient:
  def __init__(
    self,
    client: httpx.AsyncClient,
    *,
    api_url: str,
    history_token: Callable[[], Awaitable[str]],
  ):
    self.client = client
    self.api_url = api_url.rstrip("/")
    self.history_token = history_token

  async def start(self, permit: CollectionPermit) -> None:
    remaining = (permit.expires_at - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
      raise ValueError("collection start permit expired")
    await self._confirm(permit, CollectionReceipt(event="START"), timeout=remaining)

  async def finish(
    self, permit: CollectionPermit, artifact: NativeUnitArtifact
  ) -> None:
    if artifact.unit != permit.unit:
      raise ValueError("collection result does not match permit")
    await self._confirm(
      permit,
      CollectionReceipt(
        event="FINISH",
        completion=CollectionCompletion(
          unit=artifact.unit,
          sha256=artifact.sha256,
          byte_count=artifact.byte_count,
          record_count=artifact.record_count,
        ),
      ),
      timeout=30,
    )

  async def _confirm(self, permit, receipt, *, timeout):
    path = f"{self.api_url}/agent/market-data/collection/{permit.permit_id}/receipts"
    # This is one bounded confirmation attempt, including token refresh. A
    # transport error returns to the session; it does not create another unit,
    # permit, request, or stage attempt. Recovery resends the original fact.
    async with asyncio.timeout(timeout):
      headers = {"Authorization": "Bearer " + await self.history_token()}
      response = await self.client.post(
        path,
        json=receipt.model_dump(mode="json"),
        headers=headers,
        timeout=5,
      )
      response.raise_for_status()
      if response.status_code != 202:
        raise ValueError("unexpected collection receipt response")
      while True:
        status = CollectionReceiptStatus.model_validate_json(response.content)
        if status.permit_id != permit.permit_id or status.event != receipt.event:
          raise ValueError("collection receipt acknowledgement identity mismatch")
        if status.status == "ACCEPTED":
          return
        if status.status == "REJECTED":
          raise CollectionReceiptRejected("collection receipt rejected by Worker")
        await asyncio.sleep(0.25)
        response = await self.client.get(
          path + "/" + receipt.event,
          headers=headers,
          timeout=5,
        )
        response.raise_for_status()
        if response.status_code != 200:
          raise ValueError("unexpected collection receipt status response")
