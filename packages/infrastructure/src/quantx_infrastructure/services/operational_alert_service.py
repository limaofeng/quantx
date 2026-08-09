"""Persistent operational alert lifecycle and deterministic de-duplication."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from quantx_domain.clock import utcnow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import OperationalAlert


class OperationalAlertService:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  @staticmethod
  def fingerprint(
    *,
    source: str,
    code: str,
    account_id: str | None,
    business_id: str | None,
  ) -> str:
    identity = json.dumps(
      {
        "source": source.strip().upper(),
        "code": code.strip().upper(),
        "account_id": account_id or "",
        "business_id": business_id or "",
      },
      sort_keys=True,
      separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()

  async def raise_alert(
    self,
    *,
    severity: str,
    source: str,
    code: str,
    message: str,
    account_id: str | None = None,
    business_id: str | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = True,
  ) -> OperationalAlert:
    normalized_severity = severity.strip().upper()
    if normalized_severity not in {"SEV1", "SEV2", "SEV3", "SEV4"}:
      raise ValueError("告警严重级别必须是 SEV1 至 SEV4")
    value = self.fingerprint(
      source=source,
      code=code,
      account_id=account_id,
      business_id=business_id,
    )
    alert = (
      await self.db.execute(
        select(OperationalAlert)
        .where(OperationalAlert.fingerprint == value)
        .with_for_update()
      )
    ).scalar_one_or_none()
    now = utcnow()
    if alert is None:
      alert = OperationalAlert(
        id=str(uuid.uuid4()),
        fingerprint=value,
        severity=normalized_severity,
        source=source.strip().upper()[:64],
        code=code.strip().upper()[:64],
        account_id=(account_id or "")[:50] or None,
        business_id=(business_id or "")[:192] or None,
        message=message[:4000],
        details=dict(details or {}),
        status="OPEN",
        occurrences=1,
        first_seen_at=now,
        last_seen_at=now,
      )
      self.db.add(alert)
    else:
      alert.severity = normalized_severity
      alert.message = message[:4000]
      alert.details = dict(details or {})
      alert.status = "OPEN"
      alert.occurrences = int(alert.occurrences or 0) + 1
      alert.last_seen_at = now
      alert.acknowledged_by = None
      alert.acknowledged_at = None
      alert.resolved_by = None
      alert.resolved_at = None
      alert.resolution = None
    if commit:
      await self.db.commit()
      await self.db.refresh(alert)
    return alert

  async def list_alerts(
    self,
    *,
    account_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    limit: int = 100,
  ) -> list[OperationalAlert]:
    query = select(OperationalAlert)
    if account_id:
      query = query.where(
        (OperationalAlert.account_id == account_id)
        | (OperationalAlert.account_id.is_(None))
      )
    if status:
      query = query.where(OperationalAlert.status == status.strip().upper())
    if severity:
      query = query.where(
        OperationalAlert.severity == severity.strip().upper()
      )
    result = await self.db.execute(
      query.order_by(
        OperationalAlert.last_seen_at.desc(),
        OperationalAlert.id.desc(),
      ).limit(max(1, min(int(limit), 200)))
    )
    return list(result.scalars().all())

  async def acknowledge(
    self,
    alert_id: str,
    *,
    actor_id: str,
  ) -> OperationalAlert:
    alert = await self.db.get(OperationalAlert, alert_id)
    if alert is None:
      raise ValueError("告警不存在")
    if alert.status != "RESOLVED":
      alert.status = "ACKNOWLEDGED"
      alert.acknowledged_by = actor_id
      alert.acknowledged_at = utcnow()
      await self.db.commit()
      await self.db.refresh(alert)
    return alert

  async def resolve(
    self,
    alert_id: str,
    *,
    actor_id: str,
    resolution: str,
  ) -> OperationalAlert:
    if not resolution.strip():
      raise ValueError("解决告警必须填写处置记录")
    alert = await self.db.get(OperationalAlert, alert_id)
    if alert is None:
      raise ValueError("告警不存在")
    alert.status = "RESOLVED"
    alert.resolved_by = actor_id
    alert.resolved_at = utcnow()
    alert.resolution = resolution.strip()[:4000]
    await self.db.commit()
    await self.db.refresh(alert)
    return alert
