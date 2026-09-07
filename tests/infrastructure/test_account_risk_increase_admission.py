from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionOwnerType
from quantx_domain.trading.risk_increase_admission import (
  RISK_INCREASE_OWNER_PRIORITY,
  RiskIncreaseAdmissionCandidate,
  rank_risk_increase_candidates,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import trade_command_service as command_module
from quantx_infrastructure.services.account_risk_increase_admission import (
  ADMISSION_LEASE_SECONDS,
  ADMISSION_RENEW_INTERVAL_SECONDS,
  ADMISSION_TTL_SECONDS,
  AccountRiskIncreaseAdmissionSequencer,
)
from quantx_infrastructure.services.trade_command_service import (
  QueuedTradeCommand,
  TradeCommandService,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def admission_db():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(
        db,
        tables=[
          AccountExecutionControl.__table__,
          TradeIntentRecord.__table__,
          AccountRiskIncreaseAdmissionBatch.__table__,
          AccountRiskIncreaseAdmissionItem.__table__,
        ],
      )
    )
  async with async_sessionmaker(engine, expire_on_commit=False)() as db:
    db.add(
      AccountExecutionControl(
        account_id="account",
        last_snapshot_id="snapshot",
        last_snapshot_hash="a" * 64,
      )
    )
    await db.commit()
    yield db
  await engine.dispose()


def intent(key: str, owner_type: str, created_at: datetime) -> TradeIntentRecord:
  return TradeIntentRecord(
    id=key,
    owner_type=owner_type,
    owner_id=f"owner-{key}",
    environment="LIVE",
    idempotency_key=f"key-{key}",
    account_id="account",
    instrument_code="600000.SH",
    direction="BUY",
    bucket="swing",
    status="EXECUTION_READY",
    created_at=created_at,
    updated_at=created_at,
  )


def test_two_domains_have_same_winner_when_arrival_order_is_reversed() -> None:
  created_at = datetime(2026, 9, 3, 10)
  candidates = [
    RiskIncreaseAdmissionCandidate(
      intent_id="strategy",
      owner_type=ExecutionOwnerType.STRATEGY_RUN,
      owner_id="run-1",
      created_at=created_at,
      material_fingerprint="a" * 64,
    ),
    RiskIncreaseAdmissionCandidate(
      intent_id="entry",
      owner_type=ExecutionOwnerType.ENTRY_PLAN,
      owner_id="plan-1",
      created_at=created_at,
      material_fingerprint="b" * 64,
    ),
  ]

  forward = rank_risk_increase_candidates(candidates)
  reverse = rank_risk_increase_candidates(reversed(candidates))

  assert [item.candidate.intent_id for item in forward] == ["entry", "strategy"]
  assert [item.candidate.intent_id for item in reverse] == ["entry", "strategy"]
  with pytest.raises(TypeError):
    RISK_INCREASE_OWNER_PRIORITY[ExecutionOwnerType.STRATEGY_RUN] = 0


async def prepared(admission_db, *, now: datetime):
  owners = [
    "STRATEGY_RUN",
    "T_ASSISTANT_EXECUTION",
    "BOARD_ASSISTANT_EXECUTION",
    "ENTRY_PLAN",
    "MANUAL_COMMAND",
  ]
  admission_db.add_all(
    [intent(str(index), owner, now) for index, owner in enumerate(owners)]
  )
  await admission_db.commit()
  return await AccountRiskIncreaseAdmissionSequencer(admission_db).prepare_batch(
    account_id="account",
    account_snapshot_id="snapshot",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    now=now,
  )


@pytest.mark.asyncio
async def test_prepare_persists_stable_cross_domain_rank(admission_db) -> None:
  now = datetime(2026, 9, 3, 10)
  batch = await prepared(admission_db, now=now)
  items = list(
    (
      await admission_db.scalars(
        select(AccountRiskIncreaseAdmissionItem)
        .where(
          AccountRiskIncreaseAdmissionItem.admission_batch_id
          == batch.admission_batch_id
        )
        .order_by(AccountRiskIncreaseAdmissionItem.admission_rank)
      )
    ).all()
  )

  assert [item.owner_type for item in items] == [
    "MANUAL_COMMAND",
    "ENTRY_PLAN",
    "BOARD_ASSISTANT_EXECUTION",
    "T_ASSISTANT_EXECUTION",
    "STRATEGY_RUN",
  ]
  assert [item.admission_rank for item in items] == [1, 2, 3, 4, 5]
  assert batch.expires_at == now + timedelta(seconds=ADMISSION_TTL_SECONDS)


@pytest.mark.asyncio
async def test_claim_fence_is_exclusive_renewable_and_committable(admission_db) -> None:
  now = datetime(2026, 9, 3, 10)
  batch = await prepared(admission_db, now=now)
  service = AccountRiskIncreaseAdmissionSequencer(admission_db)
  claim = await service.claim_batch(
    admission_batch_id=batch.admission_batch_id,
    processing_owner="engine-a",
    now=now,
  )

  assert claim.lease_until == now + timedelta(seconds=ADMISSION_LEASE_SECONDS)
  assert ADMISSION_RENEW_INTERVAL_SECONDS == 3
  with pytest.raises(ValueError, match="RISK_ADMISSION_LEASE_HELD"):
    await service.claim_batch(
      admission_batch_id=batch.admission_batch_id,
      processing_owner="engine-a",
      now=now + timedelta(seconds=1),
    )
  await admission_db.refresh(batch)
  assert batch.processing_fence_token == claim.fence_token
  with pytest.raises(ValueError, match="RISK_ADMISSION_LEASE_HELD"):
    await service.claim_batch(
      admission_batch_id=batch.admission_batch_id,
      processing_owner="engine-b",
      now=now + timedelta(seconds=1),
    )
  renewed = await service.renew_claim(
    admission_batch_id=batch.admission_batch_id,
    fence_token=claim.fence_token,
    now=now + timedelta(seconds=3),
  )
  assert renewed == now + timedelta(seconds=13)
  committed = await service.commit_batch(
    admission_batch_id=batch.admission_batch_id,
    fence_token=claim.fence_token,
    account_snapshot_id="snapshot",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    now=now + timedelta(seconds=4),
  )
  assert committed.status == "COMMITTED"


@pytest.mark.asyncio
async def test_expired_batch_cannot_be_claimed(admission_db) -> None:
  now = datetime(2026, 9, 3, 10)
  batch = await prepared(admission_db, now=now)

  with pytest.raises(ValueError, match="RISK_ADMISSION_TTL_EXPIRED"):
    await AccountRiskIncreaseAdmissionSequencer(admission_db).claim_batch(
      admission_batch_id=batch.admission_batch_id,
      processing_owner="engine",
      now=now + timedelta(seconds=31),
    )
  await admission_db.refresh(batch)
  assert batch.status == "EXPIRED"


@pytest.mark.asyncio
async def test_changed_watermark_supersedes_claim_instead_of_partial_commit(
  admission_db,
) -> None:
  now = datetime(2026, 9, 3, 10)
  batch = await prepared(admission_db, now=now)
  service = AccountRiskIncreaseAdmissionSequencer(admission_db)
  claim = await service.claim_batch(
    admission_batch_id=batch.admission_batch_id,
    processing_owner="engine",
    now=now,
  )

  with pytest.raises(ValueError, match="RISK_ADMISSION_INPUT_CHANGED"):
    await service.commit_batch(
      admission_batch_id=batch.admission_batch_id,
      fence_token=claim.fence_token,
      account_snapshot_id="snapshot",
      account_snapshot_hash="a" * 64,
      obligation_watermark="c" * 64,
      now=now + timedelta(seconds=1),
    )
  await admission_db.refresh(batch)
  assert batch.status == "SUPERSEDED"


@pytest.mark.asyncio
async def test_changed_order_material_supersedes_claim_before_commit(
  admission_db,
) -> None:
  now = datetime(2026, 9, 3, 10)
  batch = await prepared(admission_db, now=now)
  service = AccountRiskIncreaseAdmissionSequencer(admission_db)
  claim = await service.claim_batch(
    admission_batch_id=batch.admission_batch_id,
    processing_owner="engine",
    now=now,
  )
  changed = await admission_db.get(TradeIntentRecord, "0")
  changed.target_volume = 200
  await admission_db.flush()

  with pytest.raises(ValueError, match="RISK_ADMISSION_INTENT_CHANGED"):
    await service.commit_batch(
      admission_batch_id=batch.admission_batch_id,
      fence_token=claim.fence_token,
      account_snapshot_id="snapshot",
      account_snapshot_hash="a" * 64,
      obligation_watermark="b" * 64,
      now=now + timedelta(seconds=1),
    )
  await admission_db.refresh(batch)
  assert batch.status == "SUPERSEDED"


@pytest.mark.asyncio
async def test_prepare_cannot_select_a_subset_of_account_ready_intents(
  admission_db,
) -> None:
  now = datetime(2026, 9, 3, 10)
  admission_db.add_all(
    [
      intent("manual", "MANUAL_COMMAND", now),
      intent("strategy", "STRATEGY_RUN", now),
    ]
  )
  await admission_db.commit()

  with pytest.raises(ValueError, match="RISK_ADMISSION_READY_SET_CHANGED"):
    await AccountRiskIncreaseAdmissionSequencer(admission_db).prepare_batch(
      account_id="account",
      account_snapshot_id="snapshot",
      account_snapshot_hash="a" * 64,
      obligation_watermark="b" * 64,
      intent_ids=["manual"],
      now=now,
    )


@pytest.mark.asyncio
async def test_new_ready_intent_supersedes_prepared_manifest_as_one_new_attempt(
  admission_db,
) -> None:
  now = datetime(2026, 9, 3, 10)
  admission_db.add(intent("strategy", "STRATEGY_RUN", now))
  await admission_db.commit()
  service = AccountRiskIncreaseAdmissionSequencer(admission_db)
  original = await service.prepare_batch(
    account_id="account",
    account_snapshot_id="snapshot",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    now=now,
  )
  admission_db.add(intent("manual", "MANUAL_COMMAND", now + timedelta(seconds=1)))
  await admission_db.commit()

  replacement = await service.prepare_batch(
    account_id="account",
    account_snapshot_id="snapshot",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    now=now + timedelta(seconds=1),
  )

  await admission_db.refresh(original)
  assert original.status == "SUPERSEDED"
  assert original.terminal_reason == "RISK_ADMISSION_INPUT_CHANGED"
  assert replacement.attempt == original.attempt + 1
  items = list(
    (
      await admission_db.scalars(
        select(AccountRiskIncreaseAdmissionItem)
        .where(
          AccountRiskIncreaseAdmissionItem.admission_batch_id
          == replacement.admission_batch_id
        )
        .order_by(AccountRiskIncreaseAdmissionItem.admission_rank)
      )
    ).all()
  )
  assert [item.intent_id for item in items] == ["manual", "strategy"]


@pytest.mark.asyncio
async def test_expired_attempt_is_replaced_and_stale_fence_cannot_resume(
  admission_db,
) -> None:
  now = datetime(2026, 9, 3, 10)
  batch = await prepared(admission_db, now=now)
  service = AccountRiskIncreaseAdmissionSequencer(admission_db)
  stale = await service.claim_batch(
    admission_batch_id=batch.admission_batch_id,
    processing_owner="engine-a",
    now=now,
  )
  takeover = await service.claim_batch(
    admission_batch_id=batch.admission_batch_id,
    processing_owner="engine-b",
    now=now + timedelta(seconds=11),
  )

  assert takeover.fence_token != stale.fence_token
  with pytest.raises(ValueError, match="RISK_ADMISSION_FENCE_CONFLICT"):
    await service.renew_claim(
      admission_batch_id=batch.admission_batch_id,
      fence_token=stale.fence_token,
      now=now + timedelta(seconds=12),
    )

  replacement = await service.prepare_batch(
    account_id="account",
    account_snapshot_id="snapshot",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    now=now + timedelta(seconds=31),
  )
  assert replacement.admission_batch_id != batch.admission_batch_id
  assert replacement.attempt == batch.attempt + 1
  await admission_db.refresh(batch)
  assert batch.status == "EXPIRED"


@pytest.mark.asyncio
async def test_public_dispatcher_collects_four_ready_producer_domains_in_policy_order(
  admission_db,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = datetime(2026, 9, 3, 10)
  owner_rows = [
    ("strategy", "STRATEGY_RUN"),
    ("board", "BOARD_ASSISTANT_EXECUTION"),
    ("entry", "ENTRY_PLAN"),
    ("manual", "MANUAL_COMMAND"),
  ]
  for intent_id, owner_type in owner_rows:
    row = intent(intent_id, owner_type, now)
    row.intent_metadata = {
      "risk_increase_order_request": {
        "version": "risk-increase-order-request.v1",
        "user_id": "user",
        "account_id": "account",
        "instrument_code": "600000.SH",
        "owner_type": owner_type,
        "owner_id": f"owner-{intent_id}",
        "environment": "LIVE",
        "idempotency_key": f"order-{intent_id}",
        "trace_id": intent_id,
        "strategy_run_id": "",
        "strategy_order_id": "",
        "intent_id": intent_id,
        "batch_id": "",
        "bucket": "swing",
        "t_trade_role": "",
        "risk_decision_id": "",
        "substitution_plan": {},
        "policy_version": 0,
        "order_type": "FIX_PRICE",
        "limit_price": "10",
        "volume": 100,
        "request_metadata": {},
      }
    }
    admission_db.add(row)
  await admission_db.commit()
  control = await admission_db.get(AccountExecutionControl, "account")
  service = TradeCommandService(admission_db)
  service._preview_live_authorization = AsyncMock(return_value=control)

  class Capacity:
    obligation_watermark = "b" * 64

  class CapacityService:
    def __init__(self, _db):
      pass

    async def read(self, _control, *, instrument_code, lock_rows):
      assert instrument_code == "600000.SH"
      assert lock_rows is False
      return Capacity()

  monkeypatch.setattr(command_module, "AccountCapacityService", CapacityService)
  ranked_owners: list[str] = []
  committed_claims: list[tuple[str, str]] = []

  async def enqueue_ranked(*, claim, order_requests, **_kwargs):
    async with async_sessionmaker(
      admission_db.bind,
      expire_on_commit=False,
    )() as observer:
      observed = await observer.get(
        AccountRiskIncreaseAdmissionBatch,
        claim.admission_batch_id,
      )
      assert observed is not None
      committed_claims.append(
        (str(observed.status), str(observed.processing_fence_token or ""))
      )
    items = list(
      (
        await admission_db.scalars(
          select(AccountRiskIncreaseAdmissionItem)
          .where(
            AccountRiskIncreaseAdmissionItem.admission_batch_id
            == claim.admission_batch_id
          )
          .order_by(AccountRiskIncreaseAdmissionItem.admission_rank)
        )
      ).all()
    )
    by_intent = {str(request["intent_id"]): request for request in order_requests}
    ranked_owners.extend(
      by_intent[str(item.intent_id)]["execution_ref"].owner_type.value
      for item in items
    )
    return [
      QueuedTradeCommand(f"client-{item.intent_id}", f"message-{item.intent_id}", "QUEUED")
      for item in items
    ]

  service.enqueue_risk_increase_admission_batch = enqueue_ranked

  result = await service.dispatch_ready_risk_increase_orders(
    account_id="account",
    processing_owner="engine",
  )

  assert ranked_owners == [
    "MANUAL_COMMAND",
    "ENTRY_PLAN",
    "BOARD_ASSISTANT_EXECUTION",
    "STRATEGY_RUN",
  ]
  assert set(result) == {"manual", "entry", "board", "strategy"}
  assert len(committed_claims) == 1
  assert committed_claims[0][0] == "PREPARED"
  assert committed_claims[0][1]
