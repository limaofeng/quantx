"""Durable, short-lived history connection identity and read-only work delivery."""

import json
from uuid import UUID, uuid4

from quantx_contracts.collection_permit import (
  CollectionPermit,
  CollectionUnit,
  plan_historical_work_units,
)
from quantx_contracts.history_session import (
  HistoryGrant,
  HistoryHeartbeat,
  HistoryRequest,
)
from sqlalchemy import text


class HistorySessionUnavailable(RuntimeError):
  pass


class HistorySessionStore:
  def __init__(self, engine):
    self.engine = engine

  async def register(
    self, *, device_id: str, user_id: str, token_expires_at, capabilities
  ):
    session_id = str(uuid4())
    async with self.engine.begin() as connection:
      device = await connection.scalar(
        text("""
        SELECT id FROM agent_devices WHERE id=:device AND user_id=:user
          AND revoked_at IS NULL FOR UPDATE
      """),
        {"device": str(UUID(device_id)), "user": user_id},
      )
      if device is None:
        raise HistorySessionUnavailable("history device identity unavailable")
      value = await connection.scalar(
        text("""
        INSERT INTO market_data_history_session
          (device_id,session_id,user_id,expires_at,token_expires_at,heartbeat,heartbeat_at,capabilities)
        SELECT :device,:session,:user,
          LEAST(clock_timestamp()+INTERVAL '15 seconds',:expiry),:expiry,NULL,NULL,CAST(:capabilities AS jsonb)
        WHERE :expiry > clock_timestamp()
        ON CONFLICT(device_id) DO UPDATE SET session_id=EXCLUDED.session_id,
          user_id=EXCLUDED.user_id,expires_at=EXCLUDED.expires_at,
          token_expires_at=EXCLUDED.token_expires_at,heartbeat=NULL,heartbeat_at=NULL,
          capabilities=EXCLUDED.capabilities
        WHERE market_data_history_session.expires_at <= clock_timestamp()
        RETURNING session_id
      """),
        {
          "device": str(UUID(device_id)),
          "session": session_id,
          "user": user_id,
          "expiry": token_expires_at,
          "capabilities": json.dumps(sorted(set(capabilities))),
        },
      )
    if value is None:
      raise HistorySessionUnavailable("history connection active or token expired")
    return session_id

  async def heartbeat(self, session_id: str, heartbeat: HistoryHeartbeat):
    async with self.engine.begin() as connection:
      value = await connection.scalar(
        text("""
        UPDATE market_data_history_session SET heartbeat=CAST(:heartbeat AS jsonb),heartbeat_at=clock_timestamp(),
          expires_at=LEAST(clock_timestamp()+INTERVAL '15 seconds',token_expires_at)
        WHERE session_id=:session AND expires_at > clock_timestamp()
          AND token_expires_at > clock_timestamp()
        RETURNING session_id
      """),
        {"session": session_id, "heartbeat": heartbeat.model_dump_json()},
      )
    if value is None:
      raise HistorySessionUnavailable("history connection expired or replaced")

  async def close(self, session_id: str):
    async with self.engine.begin() as connection:
      await connection.execute(
        text("""
        UPDATE market_data_history_session SET expires_at=LEAST(expires_at,clock_timestamp())
        WHERE session_id=:session
      """),
        {"session": session_id},
      )

  async def work(self, session_id: str):
    """Deliver immutable identities; this read never creates or starts a grant."""
    async with self.engine.connect() as connection:
      identity = (
        await connection.execute(
          text("""
        SELECT s.device_id FROM market_data_history_session s
        JOIN agent_devices d ON d.id=s.device_id AND d.user_id=s.user_id
        WHERE s.session_id=:session AND s.expires_at > clock_timestamp()
          AND s.token_expires_at > clock_timestamp() AND d.revoked_at IS NULL
      """),
          {"session": session_id},
        )
      ).scalar_one_or_none()
      if identity is None:
        raise HistorySessionUnavailable("history identity unavailable")
      requests = (
        (
          await connection.execute(
            text("""
        SELECT request_id,request_payload FROM market_data_request
        WHERE device_id=:device AND status IN ('DELIVERED','RECEIVING')
        ORDER BY created_at,request_id LIMIT 3
      """),
            {"device": identity},
          )
        )
        .mappings()
        .all()
      )
      if len(requests) > 2:
        raise HistorySessionUnavailable("history pipeline admission invariant violated")
      grants = (
        (
          await connection.execute(
            text("""
        SELECT p.permit_payload,p.state,r.request_payload
        FROM market_data_collection_permit p
        JOIN market_data_request r ON r.request_id=p.request_id
        WHERE r.device_id=:device AND r.status IN ('DELIVERED','RECEIVING')
          AND p.state IN ('ISSUED','STARTED')
        ORDER BY p.permit_id LIMIT 2
      """),
            {"device": identity},
          )
        )
        .mappings()
        .all()
      )
    if len(grants) > 1:
      raise HistorySessionUnavailable("history native admission invariant violated")
    messages = [
      HistoryRequest(request_id=row["request_id"], payload=row["request_payload"])
      for row in requests
    ]
    for row in grants:
      permit = CollectionPermit.model_validate(row["permit_payload"])
      units = plan_historical_work_units(row["request_payload"])
      index = permit.unit.unit_index
      if (
        index >= len(units)
        or CollectionUnit.from_payload(str(permit.unit.request_id), index, units[index])
        != permit.unit
      ):
        raise HistorySessionUnavailable("history plan identity changed")
      messages.append(
        HistoryGrant(permit=permit, state=row["state"], unit_payload=units[index])
      )
    return messages
