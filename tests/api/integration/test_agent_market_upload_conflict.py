import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from quantx_api import agent_api
from quantx_infrastructure.models.agent_runtime import (
  MarketDataRequest,
  MarketDataTransfer,
)
from quantx_infrastructure.services import market_data_staging_cleanup as cleanup
from quantx_market_data import agent_upload as upload_api
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
        "CREATE TABLE market_data_history_session (device_id VARCHAR(36), expires_at DATETIME)"
      )
    )
    await connection.execute(
      text(
        """
        CREATE TABLE agent_devices (
          id VARCHAR(36) PRIMARY KEY,
          revoked_at DATETIME
        )
        """
      )
    )
    await connection.execute(
      text(
        """
        CREATE TABLE runtime_component_heartbeats (
          component VARCHAR(48) PRIMARY KEY,
          instance_id VARCHAR(64) NOT NULL,
          status VARCHAR(32) NOT NULL,
          details JSON NOT NULL,
          updated_at DATETIME NOT NULL
        )
        """
      )
    )
    await connection.execute(
      text(
        """
        CREATE TABLE market_data_request (
          request_id VARCHAR(36) PRIMARY KEY,
          device_id VARCHAR(36) NOT NULL,
          idempotency_key VARCHAR(128) NOT NULL,
          request_payload JSON NOT NULL,
          development_only BOOLEAN NOT NULL DEFAULT FALSE,
          status VARCHAR(24) NOT NULL,
          expected_chunks INTEGER,
          received_chunks INTEGER NOT NULL,
          completed_at DATETIME,
          processing_error TEXT,
          ingestion_result JSON,
          processing_claim_token VARCHAR(36),
          processing_worker_epoch BIGINT,
          ingestion_progress JSON,
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
          compressed_bytes BIGINT NOT NULL,
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
  expected_chunks: int = 2,
  received_chunks: int = 1,
  compressed_bytes: int = 0,
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
        expected_chunks=expected_chunks,
        received_chunks=received_chunks,
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
        compressed_bytes=compressed_bytes,
        compressed=True,
        storage_reference="retained-audit-chunk.json.gz",
        received_at=now,
      )
    )
    await db.commit()


async def _seed_dispatch_request(
  sessions,
  *,
  request_id: str,
  status: str,
  now: datetime,
) -> None:
  async with sessions() as db:
    db.add(
      MarketDataRequest(
        request_id=request_id,
        device_id=DEVICE_ID,
        idempotency_key=f"dispatch-{request_id}",
        request_payload={"operation": "bars", "stock_list": ["600000.SH"]},
        status=status,
        expected_chunks=None,
        received_chunks=0,
        completed_at=None,
        created_at=now,
        updated_at=now,
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
  return await upload_api.upload_market_data_chunk(
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

  async def authenticate(db, settings, *, token, history):
    assert history is True
    device = await _AgentAuthService(db).authenticate_agent(token=token)
    return SimpleNamespace(device=device)

  monkeypatch.setattr(upload_api, "authenticate_agent_session", authenticate)
  monkeypatch.setattr(upload_api, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(cleanup, "AsyncSessionLocal", sessions)

  async def check_owner(owner, connection=None):
    assert owner == "test-owner"

  monkeypatch.setattr(cleanup, "_check_owner", check_owner)
  monkeypatch.setattr(cleanup, "MARKET_DATA_ROOT", market_data_root)
  monkeypatch.setattr(upload_api, "MARKET_DATA_ROOT", market_data_root)
  monkeypatch.setattr(upload_api, "MIN_MARKET_DATA_STAGING_FREE_BYTES", 0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_busy_requeues_dispatched_market_request_without_failing_it(
  monkeypatch,
  tmp_path,
) -> None:
  digest = hashlib.sha256(b"original chunk").hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(sessions, checksum=digest, status="DELIVERED")
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    result = await upload_api.fail_market_data_request(
      request_id=REQUEST_ID,
      request=_Request(b'{"reason":"MARKET_DATA_AGENT_BUSY"}'),
    )

    assert result == {"accepted": True, "retryable": True}
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
    assert request is not None
    assert request.status == "QUEUED"
    assert request.completed_at is None
    assert request.processing_error == "Agent busy: market-data request queue full"


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
async def test_provisional_chunks_are_frozen_only_after_manifest_completion(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"provisional chunk"
  now = datetime.now(timezone.utc).replace(tzinfo=None)
  async with _market_data_database() as (_, sessions):
    await _seed_dispatch_request(
      sessions,
      request_id=REQUEST_ID,
      status="RECEIVING",
      now=now,
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    upload_result = await _upload(body, total_chunks=0)

    async with sessions() as db:
      receiving = await db.get(MarketDataRequest, REQUEST_ID)
    assert upload_result == {"accepted": True, "duplicate": False}
    assert receiving is not None
    assert receiving.status == "RECEIVING"
    assert receiving.expected_chunks is None
    assert receiving.received_chunks == 1

    completion_result = await upload_api.complete_market_data_upload(
      request_id=REQUEST_ID,
      request=_Request(b""),
      x_total_chunks=1,
    )

    assert completion_result == {"accepted": True, "duplicate": False}
    async with sessions() as db:
      completed = await db.get(MarketDataRequest, REQUEST_ID)
    assert completed is not None
    assert completed.status == "UPLOADED"
    assert completed.expected_chunks == 1
    assert completed.received_chunks == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_manifest_completion_keeps_incomplete_provisional_upload_retryable(
  monkeypatch,
  tmp_path,
) -> None:
  now = datetime.now(timezone.utc).replace(tzinfo=None)
  async with _market_data_database() as (_, sessions):
    await _seed_dispatch_request(
      sessions,
      request_id=REQUEST_ID,
      status="RECEIVING",
      now=now,
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    with pytest.raises(HTTPException) as error:
      await upload_api.complete_market_data_upload(
        request_id=REQUEST_ID,
        request=_Request(b""),
        x_total_chunks=2,
      )

    assert error.value.status_code == 409
    assert error.value.detail == "行情 manifest 仍有缺失批次"
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
    assert request is not None
    assert request.status == "RECEIVING"
    assert request.expected_chunks is None
    assert request.completed_at is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tiny_chunks_cannot_reserve_an_oversized_manifest(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"original chunk"
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(body).hexdigest(),
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    with pytest.raises(HTTPException) as error:
      await _upload(
        b"",
        record_count=0,
        total_chunks=upload_api.MAX_MARKET_DATA_CHUNKS + 1,
      )

    assert error.value.status_code == 400
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer_count = await db.scalar(
        select(func.count()).select_from(MarketDataTransfer)
      )
    assert request is not None
    assert request.status == "RECEIVING"
    assert request.expected_chunks == 2
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
      transfers = (await db.execute(select(MarketDataTransfer))).scalars().all()
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
    with pytest.raises(HTTPException) as retry_error:
      await _upload(b"new chunk", chunk_index=1)

    assert retry_error.value.status_code == 409
    async with sessions() as db:
      request = await db.get(MarketDataRequest, REQUEST_ID)
      transfers = (await db.execute(select(MarketDataTransfer))).scalars().all()
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
@pytest.mark.parametrize("status", ["QUEUED", "DELIVERED", "RECEIVING"])
async def test_agent_can_fail_an_unexecutable_market_request(
  monkeypatch,
  tmp_path,
  status: str,
) -> None:
  digest = hashlib.sha256(b"original chunk").hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(sessions, checksum=digest, status=status)
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")
    body = b'{"reason":"ValueError: instrument count limit"}'

    result = await upload_api.fail_market_data_request(
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


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("status", ["UPLOADED", "PROCESSING", "COMPLETED"])
async def test_late_agent_failure_cannot_revert_frozen_manifest(
  monkeypatch,
  tmp_path,
  status: str,
) -> None:
  body = b"complete chunk"
  digest = hashlib.sha256(body).hexdigest()
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=digest,
      status=status,
      expected_chunks=1,
      received_chunks=1,
      compressed_bytes=len(body),
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    result = await upload_api.fail_market_data_request(
      request_id=REQUEST_ID,
      request=_Request(b'{"reason":"ReadTimeout: response was lost"}'),
    )

    assert result == {"accepted": True}
    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
    assert market_request is not None
    assert market_request.status == status
    assert market_request.processing_error is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_late_agent_failure_cannot_fail_complete_receiving_manifest(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"complete chunk"
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(body).hexdigest(),
      status="RECEIVING",
      expected_chunks=1,
      received_chunks=0,
      compressed_bytes=len(body),
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    await upload_api.fail_market_data_request(
      request_id=REQUEST_ID,
      request=_Request(b'{"reason":"late failure"}'),
    )

    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
    assert market_request is not None
    assert market_request.status == "RECEIVING"
    assert market_request.processing_error is None


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("status", ["UPLOADED", "PROCESSING"])
@pytest.mark.parametrize("conflict_kind", ["checksum", "total_chunks", "record_count"])
async def test_late_chunk_conflict_preserves_frozen_manifest(
  monkeypatch,
  tmp_path,
  status: str,
  conflict_kind: str,
) -> None:
  original = b"complete chunk"
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(original).hexdigest(),
      status=status,
      expected_chunks=1,
      received_chunks=1,
      compressed_bytes=len(original),
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    with pytest.raises(HTTPException) as error:
      if conflict_kind == "checksum":
        await _upload(b"conflicting retry", total_chunks=1)
      elif conflict_kind == "total_chunks":
        await _upload(original, total_chunks=2)
      else:
        await _upload(original, record_count=2, total_chunks=1)

    assert error.value.status_code == 409
    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer = await db.get(MarketDataTransfer, TRANSFER_ID)
    assert market_request is not None
    assert market_request.status == status
    assert market_request.processing_error is None
    assert transfer is not None
    assert transfer.checksum_sha256 == hashlib.sha256(original).hexdigest()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_chunk_persists_compressed_bytes_and_freezes_complete_manifest(
  monkeypatch,
  tmp_path,
) -> None:
  first = b"first chunk"
  second = b"second chunk"
  accepted_at = datetime(2026, 8, 29, 5, 0, 0)
  market_data_root = tmp_path / "market-data"
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(first).hexdigest(),
      compressed_bytes=len(first),
    )
    _configure_api(monkeypatch, sessions, market_data_root)
    monkeypatch.setattr(upload_api, "utcnow", lambda: accepted_at)

    result = await _upload(second, chunk_index=1)

    assert result == {"accepted": True, "duplicate": False}
    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer = await db.scalar(
        select(MarketDataTransfer).where(MarketDataTransfer.chunk_index == 1)
      )
    assert market_request is not None
    assert market_request.status == "UPLOADED"
    assert market_request.received_chunks == 2
    assert market_request.updated_at == accepted_at
    assert transfer is not None
    assert transfer.received_at == accepted_at
    assert transfer.compressed_bytes == len(second)
    assert transfer.storage_reference == f"{REQUEST_ID}/00000001.json.gz"
    assert (market_data_root / REQUEST_ID / "00000001.json.gz").read_bytes() == second


@pytest.mark.asyncio
@pytest.mark.integration
async def test_request_compressed_quota_is_terminal_contract_failure(
  monkeypatch,
  tmp_path,
) -> None:
  first = b"first chunk"
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(first).hexdigest(),
      compressed_bytes=upload_api.MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES - 1,
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")

    with pytest.raises(HTTPException) as error:
      await _upload(b"too large", chunk_index=1)

    assert error.value.status_code == 413
    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer_count = await db.scalar(
        select(func.count()).select_from(MarketDataTransfer)
      )
    assert market_request is not None
    assert market_request.status == "FAILED"
    assert "compressed byte limit" in str(market_request.processing_error)
    assert transfer_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_request_quota_counts_legacy_files_when_compressed_bytes_is_zero(
  monkeypatch,
  tmp_path,
) -> None:
  first = b"12345678"
  market_data_root = tmp_path / "market-data"
  request_directory = market_data_root / REQUEST_ID
  request_directory.mkdir(parents=True)
  retained = request_directory / "00000000.json.gz"
  retained.write_bytes(first)
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(first).hexdigest(),
      compressed_bytes=0,
    )
    _configure_api(monkeypatch, sessions, market_data_root)
    monkeypatch.setattr(upload_api, "MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES", 10)

    with pytest.raises(HTTPException) as error:
      await _upload(b"four", chunk_index=1)

    assert error.value.status_code == 413
    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer_count = await db.scalar(
        select(func.count()).select_from(MarketDataTransfer)
      )
    assert market_request is not None
    assert market_request.status == "FAILED"
    assert transfer_count == 1
    assert retained.read_bytes() == first


@pytest.mark.asyncio
@pytest.mark.integration
async def test_global_staging_quota_is_retryable_and_preserves_request(
  monkeypatch,
  tmp_path,
) -> None:
  first = b"first chunk"
  market_data_root = tmp_path / "market-data"
  other = market_data_root / "44444444-4444-4444-8444-444444444444"
  other.mkdir(parents=True)
  (other / "retained.json.gz").write_bytes(b"x" * 8)
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(first).hexdigest(),
      compressed_bytes=len(first),
    )
    _configure_api(monkeypatch, sessions, market_data_root)
    monkeypatch.setattr(upload_api, "MAX_MARKET_DATA_STAGING_BYTES", 10)

    with pytest.raises(HTTPException) as error:
      await _upload(b"four", chunk_index=1)

    assert error.value.status_code == 507
    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer_count = await db.scalar(
        select(func.count()).select_from(MarketDataTransfer)
      )
    assert market_request is not None
    assert market_request.status == "RECEIVING"
    assert market_request.processing_error is None
    assert transfer_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_disk_reserve_rejection_is_retryable_and_preserves_request(
  monkeypatch,
  tmp_path,
) -> None:
  first = b"first chunk"
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(first).hexdigest(),
      compressed_bytes=len(first),
    )
    _configure_api(monkeypatch, sessions, tmp_path / "market-data")
    monkeypatch.setattr(upload_api, "MIN_MARKET_DATA_STAGING_FREE_BYTES", 1)
    monkeypatch.setattr(upload_api, "_market_data_staging_free_bytes", lambda _root: 0)

    with pytest.raises(HTTPException) as error:
      await _upload(b"second", chunk_index=1)

    assert error.value.status_code == 507
    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
    assert market_request is not None
    assert market_request.status == "RECEIVING"
    assert market_request.processing_error is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_staging_sweep_preserves_completed_evidence_and_removes_old_orphans(
  monkeypatch,
  tmp_path,
) -> None:
  body = b"complete chunk"
  market_data_root = tmp_path / "market-data"
  completed = market_data_root / REQUEST_ID
  orphan = market_data_root / "55555555-5555-4555-8555-555555555555"
  completed.mkdir(parents=True)
  orphan.mkdir()
  (completed / "00000000.json.gz").write_bytes(body)
  (orphan / "orphan.json.gz").write_bytes(body)
  started = datetime.now(timezone.utc)
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(body).hexdigest(),
      status="COMPLETED",
      expected_chunks=1,
      received_chunks=1,
      compressed_bytes=len(body),
    )
    _configure_api(monkeypatch, sessions, market_data_root)
    sweep_now = started.replace(microsecond=0) + timedelta(
      seconds=cleanup.MARKET_DATA_STAGING_ORPHAN_GRACE_SECONDS + 1
    )
    monkeypatch.setattr(
      cleanup,
      "utcnow",
      lambda: sweep_now.replace(tzinfo=None),
    )

    removed = await cleanup.sweep_market_data_staging_once(owner="test-owner")

    assert removed["directories"] == 1
    assert (completed / "00000000.json.gz").read_bytes() == body
    assert not orphan.exists()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("complete", [True, False])
async def test_staging_sweep_retains_active_and_recent_failed_data(
  monkeypatch,
  tmp_path,
  complete,
) -> None:
  body = b"complete chunk"
  market_data_root = tmp_path / "market-data"
  failed = market_data_root / REQUEST_ID
  failed.mkdir(parents=True)
  (failed / "00000000.json.gz").write_bytes(body)
  started = datetime.now(timezone.utc)
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(body).hexdigest(),
      status="FAILED",
      expected_chunks=1 if complete else 2,
      received_chunks=1,
      compressed_bytes=len(body),
    )
    _configure_api(monkeypatch, sessions, market_data_root)

    recent = await cleanup.sweep_market_data_staging_once(
      owner="test-owner", now=started
    )
    expired = await cleanup.sweep_market_data_staging_once(
      owner="test-owner",
      now=started + timedelta(days=30),
    )

    assert recent["directories"] == 0
    assert expired["directories"] == 0
    assert (failed / "00000000.json.gz").read_bytes() == body
    async with sessions() as db:
      market_request = await db.get(MarketDataRequest, REQUEST_ID)
      transfer_count = await db.scalar(
        select(func.count()).select_from(MarketDataTransfer)
      )
    assert market_request is not None
    assert market_request.expected_chunks == (1 if complete else 2)
    assert market_request.received_chunks == 1
    assert transfer_count == 1


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("status", ["RECEIVING", "UPLOADED", "PROCESSING"])
async def test_staging_sweep_never_removes_nonterminal_request_data(
  monkeypatch,
  tmp_path,
  status: str,
) -> None:
  body = b"active chunk"
  market_data_root = tmp_path / "market-data"
  active = market_data_root / REQUEST_ID
  active.mkdir(parents=True)
  retained = active / "00000000.json.gz"
  retained.write_bytes(body)
  started = datetime.now(timezone.utc)
  async with _market_data_database() as (_, sessions):
    await _seed_request(
      sessions,
      checksum=hashlib.sha256(body).hexdigest(),
      status=status,
      expected_chunks=1 if status != "RECEIVING" else 2,
      received_chunks=1,
      compressed_bytes=len(body),
    )
    _configure_api(monkeypatch, sessions, market_data_root)

    removed = await cleanup.sweep_market_data_staging_once(
      owner="test-owner",
      now=started + timedelta(days=30),
    )

    assert removed["directories"] == 0
    assert retained.read_bytes() == body
