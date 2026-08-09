import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from quantx_api import agent_api
from quantx_infrastructure.models.agent_runtime import (
  MarketDataRequest,
  MarketDataTransfer,
)
from quantx_infrastructure.runtime_store import DurableRuntimeStore
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
  async_sessionmaker,
  create_async_engine,
)
from sqlalchemy.pool import StaticPool

DEVICE_ID = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"
TRANSFER_ID = "33333333-3333-4333-8333-333333333333"


class _Request:
  def __init__(self, body: bytes) -> None:
    self.body = body
    self.headers = {
      "authorization": "Bearer agent-token",
      "content-length": str(len(body)),
    }

  async def stream(self):
    yield self.body


class _AgentAuthService:
  def __init__(self, _db) -> None:
    pass

  async def authenticate_agent(self, *, token: str):
    assert token == "agent-token"
    return SimpleNamespace(id=DEVICE_ID)


@asynccontextmanager
async def _market_data_database():
  engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
  )
  async with engine.begin() as connection:
    await connection.execute(
      text(
        """
        CREATE TABLE market_data_request (
          request_id VARCHAR(36) PRIMARY KEY,
          device_id VARCHAR(36) NOT NULL,
          idempotency_key VARCHAR(128) NOT NULL,
          request_payload JSON NOT NULL,
          status VARCHAR(24) NOT NULL,
          expected_chunks INTEGER,
          received_chunks INTEGER NOT NULL,
          completed_at DATETIME,
          processing_error TEXT,
          created_at DATETIME NOT NULL,
          updated_at DATETIME NOT NULL
        )
        """
      )
    )
    await connection.execute(
      text(
        """
        CREATE TABLE market_data_transfer (
          transfer_id VARCHAR(36) PRIMARY KEY,
          request_id VARCHAR(36) NOT NULL,
          chunk_index INTEGER NOT NULL,
          checksum_sha256 VARCHAR(64) NOT NULL,
          record_count INTEGER NOT NULL,
          compressed BOOLEAN NOT NULL,
          storage_reference VARCHAR(512) NOT NULL,
          received_at DATETIME NOT NULL,
          UNIQUE (request_id, chunk_index)
        )
        """
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  try:
    yield engine, sessions
  finally:
    await engine.dispose()


async def _seed_request(
  sessions,
  *,
  checksum: str,
  status: str = "RECEIVING",
  processing_error: str | None = None,
) -> None:
  now = datetime.now(timezone.utc).replace(tzinfo=None)
  async with sessions() as db:
    db.add(
      MarketDataRequest(
        request_id=REQUEST_ID,
        device_id=DEVICE_ID,
        idempotency_key="market-upload-conflict-test",
        request_payload={"operation": "bars"},
        status=status,
        expected_chunks=2,
        received_chunks=1,
        completed_at=now if status in {"COMPLETED", "FAILED"} else None,
        processing_error=processing_error,
        created_at=now,
        updated_at=now,
      )
    )
    db.add(
      MarketDataTransfer(
        transfer_id=TRANSFER_ID,
        request_id=REQUEST_ID,
        chunk_index=0,
        checksum_sha256=checksum,
        record_count=1,
        compressed=True,
        storage_reference="retained-audit-chunk.json.gz",
        received_at=now,
      )
    )
    await db.commit()


async def _upload(
  body: bytes,
  *,
  chunk_index: int = 0,
  record_count: int = 1,
  total_chunks: int = 2,
):
  return await agent_api.upload_market_data_chunk(
    request_id=REQUEST_ID,
    chunk_index=chunk_index,
    request=_Request(body),
    x_content_sha256=hashlib.sha256(body).hexdigest(),
    x_record_count=record_count,
    x_total_chunks=total_chunks,
    content_encoding="gzip",
  )


def _configure_api(monkeypatch, sessions, market_data_root) -> None:
  monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(agent_api, "AgentAuthService", _AgentAuthService)
  monkeypatch.setattr(agent_api, "MARKET_DATA_ROOT", market_data_root)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_duplicate_chunk_with_same_digest_is_idempotent(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"original chunk"
  digest = hashlib.sha256(body).hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(sessions, checksum=digest)
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    result = await _upload(body)

    assert result == {"accepted": True, "duplicate": True}
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer_count = await db.scalar(
        select(func.count()).select_from(MarketDataTransfer)
      )
    assert request is not None
    assert request.status == "RECEIVING"
    assert request.processing_error is None
    assert transfer_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_checksum_conflict_returns_409_and_atomically_fails_request(
  monkeypatch,
  tmp_path,
) -> None:
  original_digest = hashlib.sha256(b"original chunk").hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(sessions, checksum=original_digest)
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    with pytest.raises(HTTPException) as error:
      await _upload(b"conflicting chunk")

    assert error.value.status_code == 409
    assert error.value.detail == "重复批次内容不一致"
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfers = (
        await db.execute(select(MarketDataTransfer))
      ).scalars().all()
    assert request is not None
    assert request.status == "FAILED"
    assert request.processing_error == "chunk 0 checksum mismatch"
    assert request.completed_at is not None
    assert len(transfers) == 1
    assert transfers[0].checksum_sha256 == original_digest
    assert transfers[0].storage_reference == "retained-audit-chunk.json.gz"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_total_chunks_conflict_fails_request_without_losing_audit_chunks(
  monkeypatch,
  tmp_path,
) -> None:
  original_digest = hashlib.sha256(b"original chunk").hexdigest()
  market_data_root = tmp_path / "market-data"
  async with _market_data_database() as (engine, sessions):
    await _seed_request(sessions, checksum=original_digest)
    _configure_api(monkeypatch, sessions, market_data_root)

    with pytest.raises(HTTPException) as error:
      await _upload(b"new chunk", chunk_index=1, total_chunks=3)

    assert error.value.status_code == 409
    assert error.value.detail == "行情批次总数与首次上传不一致"
    await agent_api._requeue_incomplete_market_requests(DEVICE_ID)
    store = DurableRuntimeStore.__new__(DurableRuntimeStore)
    store.engine = engine
    with pytest.raises(
      RuntimeError,
      match="existing=FAILED requested=COMPLETED",
    ):
      await store.finish_market_data_request(
        REQUEST_ID,
        status="COMPLETED",
      )
    with pytest.raises(HTTPException) as retry_error:
      await _upload(b"new chunk", chunk_index=1)

    assert retry_error.value.status_code == 409
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfers = (
        await db.execute(select(MarketDataTransfer))
      ).scalars().all()
    assert request is not None
    assert request.status == "FAILED"
    assert request.processing_error == "chunk 1 total_chunks mismatch"
    assert request.completed_at is not None
    assert len(transfers) == 1
    assert transfers[0].checksum_sha256 == original_digest
    assert transfers[0].storage_reference == "retained-audit-chunk.json.gz"
    assert not market_data_root.exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_record_count_conflict_fails_request_and_preserves_first_metadata(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"original chunk"
  original_digest = hashlib.sha256(body).hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(sessions, checksum=original_digest)
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    with pytest.raises(HTTPException) as error:
      await _upload(body, record_count=2)

    assert error.value.status_code == 409
    assert error.value.detail == "重复批次记录数不一致"
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer = await db.get(MarketDataTransfer, TRANSFER_ID)
    assert request is not None
    assert request.status == "FAILED"
    assert request.processing_error == "chunk 0 record_count mismatch"
    assert request.completed_at is not None
    assert transfer is not None
    assert transfer.checksum_sha256 == original_digest
    assert transfer.record_count == 1
    assert transfer.storage_reference == "retained-audit-chunk.json.gz"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_completed_request_conflict_preserves_completed_state(
  monkeypatch,
  tmp_path,
) -> None:
  original_digest = hashlib.sha256(b"original chunk").hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=original_digest,
      status="COMPLETED",
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")
    async with sessions() as db:
      request_before = await db.get(MarketDataRequest, REQUEST_ID)
      assert request_before is not None
      completed_at = request_before.completed_at

    with pytest.raises(HTTPException) as error:
      await _upload(b"conflicting chunk")

    assert error.value.status_code == 409
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
    assert request is not None
    assert request.status == "COMPLETED"
    assert request.processing_error is None
    assert request.completed_at == completed_at


@pytest.mark.asyncio
@pytest.mark.integration
async def test_completed_request_total_chunks_conflict_preserves_terminal_state(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"original chunk"
  original_digest = hashlib.sha256(body).hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=original_digest,
      status="COMPLETED",
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")
    async with sessions() as db:
      request_before = await db.get(MarketDataRequest, REQUEST_ID)
      assert request_before is not None
      completed_at = request_before.completed_at

    with pytest.raises(HTTPException) as error:
      await _upload(body, total_chunks=3)

    assert error.value.status_code == 409
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer_count = await db.scalar(
        select(func.count()).select_from(MarketDataTransfer)
      )
    assert request is not None
    assert request.status == "COMPLETED"
    assert request.processing_error is None
    assert request.completed_at == completed_at
    assert transfer_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_completed_request_record_count_conflict_preserves_terminal_state(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"original chunk"
  original_digest = hashlib.sha256(body).hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=original_digest,
      status="COMPLETED",
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")
    async with sessions() as db:
      request_before = await db.get(MarketDataRequest, REQUEST_ID)
      assert request_before is not None
      completed_at = request_before.completed_at

    with pytest.raises(HTTPException) as error:
      await _upload(body, record_count=2)

    assert error.value.status_code == 409
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer = await db.get(MarketDataTransfer, TRANSFER_ID)
    assert request is not None
    assert request.status == "COMPLETED"
    assert request.processing_error is None
    assert request.completed_at == completed_at
    assert transfer is not None
    assert transfer.record_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_failed_request_rejects_upload_and_cannot_be_completed(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"original chunk"
  digest = hashlib.sha256(body).hexdigest()
  market_data_root = tmp_path / "market-data"
  async with _market_data_database() as (engine, sessions):
    await _seed_request(
      sessions,
      checksum=digest,
      status="FAILED",
      processing_error="chunk 0 checksum mismatch",
    )
    _configure_api(monkeypatch, sessions, market_data_root)

    with pytest.raises(HTTPException) as duplicate_error:
      await _upload(body)
    with pytest.raises(HTTPException) as new_chunk_error:
      await _upload(b"new chunk", chunk_index=1)

    assert duplicate_error.value.status_code == 409
    assert new_chunk_error.value.status_code == 409
    await agent_api._requeue_incomplete_market_requests(DEVICE_ID)
    store = DurableRuntimeStore.__new__(DurableRuntimeStore)
    store.engine = engine
    with pytest.raises(
      RuntimeError,
      match="existing=FAILED requested=COMPLETED",
    ):
      await store.finish_market_data_request(
        REQUEST_ID,
        status="COMPLETED",
      )
    await store.finish_market_data_request(
      REQUEST_ID,
      status="FAILED",
      error="worker retry must not overwrite checksum conflict",
    )

    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer_count = await db.scalar(
        select(func.count()).select_from(MarketDataTransfer)
      )
    assert request is not None
    assert request.status == "FAILED"
    assert request.processing_error == "chunk 0 checksum mismatch"
    assert transfer_count == 1
    assert not market_data_root.exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_can_fail_an_unexecutable_market_request(
  monkeypatch,
  tmp_path,
) -> None:
  digest = hashlib.sha256(b"original chunk").hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(sessions, checksum=digest)
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")
    body = b'{"reason":"ValueError: instrument count limit"}'

    result = await agent_api.fail_market_data_request(
      request_id=REQUEST_ID,
      request=_Request(body),
    )

    assert result == {"accepted": True}
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer = await db.get(MarketDataTransfer, TRANSFER_ID)
    assert request is not None
    assert request.status == "FAILED"
    assert request.processing_error == (
      "Agent rejected request: ValueError: instrument count limit"
    )
    assert request.completed_at is not None
    assert transfer is not None
