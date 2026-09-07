"""One public sequencer isolates PAPER scope and preserves allocation rank."""

from datetime import datetime, timedelta

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerType
from quantx_domain.trading.risk_increase_admission import (
  RiskIncreaseAdmissionCandidate,
  rank_risk_increase_candidates,
  risk_increase_input_fingerprint,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.paper_execution import PaperExecutionAccountRecord
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.risk_increase_admission_repository import (
  RiskIncreaseAdmissionRepository,
)
from quantx_infrastructure.services.account_risk_increase_admission import (
  AccountRiskIncreaseAdmissionSequencer,
)
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 9, 7, 2)


@pytest.fixture
async def independent_sessions(tmp_path):
  """Separate ORM sessions against a real shared SQLite file."""
  engine = create_async_engine(
    f"sqlite+aiosqlite:///{(tmp_path / 'admission.sqlite').as_posix()}"
  )
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          PaperExecutionAccountRecord.__table__,
          TradeIntentRecord.__table__,
          TAllocationBatchRecord.__table__,
          TAllocationDecisionRecord.__table__,
          AccountRiskIncreaseAdmissionBatch.__table__,
          AccountRiskIncreaseAdmissionItem.__table__,
        ],
      )
    )
  yield async_sessionmaker(engine, expire_on_commit=False)
  await engine.dispose()


@pytest.fixture
async def db():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          PaperExecutionAccountRecord.__table__,
          TradeIntentRecord.__table__,
          TAllocationBatchRecord.__table__,
          TAllocationDecisionRecord.__table__,
          AccountRiskIncreaseAdmissionBatch.__table__,
          AccountRiskIncreaseAdmissionItem.__table__,
        ],
      )
    )
  # AccountExecutionControl is deliberately absent: PAPER cannot consult it.
  async with async_sessionmaker(engine, expire_on_commit=False)() as session:
    yield session
  await engine.dispose()


async def seed_paper(db, scope="paper-a"):
  db.add(
    PaperExecutionAccountRecord(
      execution_id=scope,
      account_id="account",
      environment="PAPER",
      seed_snapshot_id="seed",
      seed_snapshot_hash="a" * 64,
      seed_as_of=NOW,
      seed_payload={},
      matching_policy_version="paper-strict-book-v1",
      broker_checkpoint={},
      bucket_checkpoint={},
      revision=0,
      snapshot_hash="a" * 64,
      initial_snapshot_hash="a" * 64,
      snapshot_as_of=NOW,
    )
  )
  batch_id = scope + "-allocation"
  db.add(
    TAllocationBatchRecord(
      allocation_batch_id=batch_id,
      execution_id=scope,
      cycle_id=scope + "-cycle",
      environment="PAPER",
      allocation_attempt=1,
      portfolio_input_fingerprint="b" * 64,
      portfolio_snapshot={},
      intent_manifest_hash="c" * 64,
      intent_manifest=[
        {"intent_id": scope + "-older"},
        {"intent_id": scope + "-newer"},
      ],
      intent_count=2,
      status="COMMITTED",
      decision_manifest_hash="d" * 64,
      created_at=NOW,
      expires_at=NOW + timedelta(seconds=60),
      committed_at=NOW,
    )
  )
  for index, label in enumerate(("older", "newer")):
    identity = scope + "-" + label
    decision_id = identity + "-decision"
    code = "600000.SH" if index == 0 else "000001.SZ"
    db.add(
      TradeIntentRecord(
        id=identity,
        owner_type="T_ASSISTANT_EXECUTION",
        owner_id=scope,
        environment="PAPER",
        idempotency_key=identity,
        account_id="account",
        instrument_code=code,
        direction="BUY",
        bucket="swing",
        reason="entry",
        status="EXECUTION_READY",
        target_amount=1000,
        allocation_cycle_id=scope + "-cycle",
        allocation_version=1,
        allocation_decision_id=decision_id,
        intent_metadata={"candidate_id": identity},
        created_at=NOW + timedelta(microseconds=index),
        updated_at=NOW,
      )
    )
    db.add(
      TAllocationDecisionRecord(
        decision_id=decision_id,
        allocation_batch_id=batch_id,
        intent_id=identity,
        intent_version=0,
        candidate_id=identity,
        instrument_code=code,
        rank=2 - index,
        action="ALLOW",
        requested_amount_ceiling=1000,
        allocated_amount_cap=1000,
        evidence={},
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
      )
    )
  await db.commit()


def sequencer(db, scope="paper-a"):
  return AccountRiskIncreaseAdmissionSequencer(
    db, environment=ExecutionEnvironment.PAPER, paper_execution_id=scope
  )


async def prepare(service, scope="paper-a", **changes):
  kwargs = dict(
    account_id="account",
    account_snapshot_id=f"paper:{scope}:0",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    now=NOW,
  )
  return await service.prepare_batch(**{**kwargs, **changes})


async def test_paper_preserves_allocation_rank_at_prepare_and_commit(db):
  await seed_paper(db)
  service = sequencer(db)
  batch = await prepare(service)
  items = await service.repository.items(batch.admission_batch_id)
  assert [item.intent_id for item in items] == ["paper-a-newer", "paper-a-older"]
  assert [item.admission_rank for item in items] == [1, 2]
  claim = await service.claim_batch(
    admission_batch_id=batch.admission_batch_id, processing_owner="paper", now=NOW
  )
  await service.renew_claim(
    admission_batch_id=batch.admission_batch_id,
    fence_token=claim.fence_token,
    now=NOW + timedelta(seconds=1),
  )
  committed = await service.commit_batch(
    admission_batch_id=batch.admission_batch_id,
    fence_token=claim.fence_token,
    account_snapshot_id="paper:paper-a:0",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    now=NOW + timedelta(seconds=2),
  )
  assert committed.status == "COMMITTED"
  assert committed.environment == "PAPER" and committed.paper_execution_id == "paper-a"


async def test_prepare_flushes_bindings_before_items_without_committing_caller_transaction(
  db,
):
  await seed_paper(db)
  inserted = []

  def verify_persisted_binding(_mapper, connection, item):
    # Query via the SQL connection so an unchanged identity-map object cannot
    # conceal an incorrect SQLAlchemy flush order.
    binding = connection.execute(
      select(
        TradeIntentRecord.admission_batch_id,
        TradeIntentRecord.admission_rank,
        TradeIntentRecord.admission_policy_version,
        TradeIntentRecord.admission_input_fingerprint,
      ).where(TradeIntentRecord.id == item.intent_id)
    ).one()
    batch = connection.execute(
      select(
        AccountRiskIncreaseAdmissionBatch.policy_version,
        AccountRiskIncreaseAdmissionBatch.input_fingerprint,
      ).where(
        AccountRiskIncreaseAdmissionBatch.admission_batch_id == item.admission_batch_id
      )
    ).one()
    assert tuple(binding) == (
      item.admission_batch_id,
      item.admission_rank,
      batch.policy_version,
      batch.input_fingerprint,
    )
    inserted.append(item.intent_id)

  event.listen(
    AccountRiskIncreaseAdmissionItem, "before_insert", verify_persisted_binding
  )
  try:
    batch = await prepare(sequencer(db), commit=False)
    assert inserted == ["paper-a-newer", "paper-a-older"]
    assert batch.status == "PREPARED"
    await db.rollback()
  finally:
    event.remove(
      AccountRiskIncreaseAdmissionItem, "before_insert", verify_persisted_binding
    )
  assert (
    await db.scalar(select(func.count()).select_from(AccountRiskIncreaseAdmissionBatch))
    == 0
  )
  assert (
    await db.scalar(select(func.count()).select_from(AccountRiskIncreaseAdmissionItem))
    == 0
  )
  bindings = list(
    (await db.scalars(select(TradeIntentRecord.admission_batch_id))).all()
  )
  assert bindings == [None, None]


async def test_live_and_paper_scopes_have_independent_attempts_and_queries(db):
  await seed_paper(db)
  await seed_paper(db, "paper-b")
  async with db.bind.begin() as connection:
    await connection.run_sync(
      lambda sync: AccountExecutionControl.__table__.create(sync)
    )
  db.add(
    AccountExecutionControl(
      account_id="account",
      last_snapshot_id="live-snapshot",
      last_snapshot_hash="a" * 64,
    )
  )
  db.add(
    TradeIntentRecord(
      id="live",
      owner_type="MANUAL_COMMAND",
      owner_id="command",
      environment="LIVE",
      idempotency_key="live",
      account_id="account",
      instrument_code="600000.SH",
      direction="BUY",
      bucket="swing",
      reason="manual",
      status="EXECUTION_READY",
      created_at=NOW,
      updated_at=NOW,
    )
  )
  await db.commit()
  a, b = (
    await prepare(sequencer(db)),
    await prepare(sequencer(db, "paper-b"), "paper-b"),
  )
  live = await AccountRiskIncreaseAdmissionSequencer(db).prepare_batch(
    account_id="account",
    account_snapshot_id="live-snapshot",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    now=NOW,
  )
  assert [a.attempt, b.attempt, live.attempt] == [1, 1, 1]
  assert len({a.input_fingerprint, b.input_fingerprint, live.input_fingerprint}) == 3
  assert await sequencer(db).repository.find_batch(b.admission_batch_id) is None
  assert (
    await RiskIncreaseAdmissionRepository(db).find_batch(a.admission_batch_id) is None
  )
  with pytest.raises(ValueError, match="BATCH_NOT_FOUND"):
    await sequencer(db).repository.items(b.admission_batch_id)
  with pytest.raises(ValueError, match="BATCH_NOT_FOUND"):
    await sequencer(db).claim_batch(
      admission_batch_id=live.admission_batch_id, processing_owner="cross", now=NOW
    )
  assert (
    await sequencer(db).repository.next_attempt(
      account_id="account", environment="PAPER"
    )
    == 2
  )
  assert (
    await RiskIncreaseAdmissionRepository(db).next_attempt(
      account_id="account", environment="LIVE"
    )
    == 2
  )


async def test_paper_scope_requires_account_and_current_snapshot_binding(db):
  await seed_paper(db)
  with pytest.raises(ValueError, match="ACCOUNT_SCOPE_CONFLICT"):
    await prepare(sequencer(db), account_id="other")
  with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
    await prepare(sequencer(db), account_snapshot_id="paper:paper-b:0")
  with pytest.raises(ValueError, match="ACCOUNT_SCOPE_CONFLICT"):
    await sequencer(db).repository.next_attempt(account_id="other", environment="PAPER")
  with pytest.raises(ValueError, match="ENVIRONMENT_CONFLICT"):
    await sequencer(db).repository.latest_for_input(
      account_id="account", environment="LIVE", input_fingerprint="a" * 64
    )


@pytest.mark.parametrize("stage", ["claim", "renew", "commit"])
async def test_updated_paper_snapshot_blocks_stale_admission_at_every_stage(db, stage):
  await seed_paper(db)
  service = sequencer(db)
  batch = await prepare(service)
  claim = None
  if stage != "claim":
    claim = await service.claim_batch(
      admission_batch_id=batch.admission_batch_id, processing_owner="paper", now=NOW
    )
  account = await db.get(PaperExecutionAccountRecord, "paper-a")
  account.revision = 1
  account.snapshot_hash = "c" * 64
  await db.commit()
  with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
    if stage == "claim":
      await service.claim_batch(
        admission_batch_id=batch.admission_batch_id, processing_owner="paper", now=NOW
      )
    elif stage == "renew":
      await service.renew_claim(
        admission_batch_id=batch.admission_batch_id,
        fence_token=claim.fence_token,
        now=NOW,
      )
    else:
      await service.commit_batch(
        admission_batch_id=batch.admission_batch_id,
        fence_token=claim.fence_token,
        account_snapshot_id="paper:paper-a:0",
        account_snapshot_hash="a" * 64,
        obligation_watermark="b" * 64,
        now=NOW,
      )


@pytest.mark.parametrize(
  "change", ["missing", "wrong_owner", "not_committed", "version", "rejected"]
)
async def test_t_ready_requires_authoritative_committed_allow_or_cap(db, change):
  await seed_paper(db)
  intent = await db.get(TradeIntentRecord, "paper-a-older")
  if change == "missing":
    intent.allocation_decision_id = None
  elif change == "wrong_owner":
    allocation = await db.get(TAllocationBatchRecord, "paper-a-allocation")
    allocation.execution_id = "other"
  elif change == "not_committed":
    allocation = await db.get(TAllocationBatchRecord, "paper-a-allocation")
    allocation.status = "PREPARED"
    allocation.committed_at = allocation.decision_manifest_hash = None
  elif change == "version":
    intent.allocation_version = 2
  else:
    decision = await db.get(TAllocationDecisionRecord, intent.allocation_decision_id)
    decision.action = "REJECT"
    decision.allocated_amount_cap = 0
  await db.commit()
  with pytest.raises(ValueError, match="T_ALLOCATION_REQUIRED"):
    await prepare(sequencer(db))


async def test_changed_allocation_material_supersedes_before_commit(db):
  await seed_paper(db)
  service = sequencer(db)
  batch = await prepare(service)
  claim = await service.claim_batch(
    admission_batch_id=batch.admission_batch_id, processing_owner="paper", now=NOW
  )
  decision = await db.get(TAllocationDecisionRecord, "paper-a-older-decision")
  decision.action, decision.allocated_amount_cap = "CAP", 500
  await db.commit()
  with pytest.raises(ValueError, match="INTENT_CHANGED"):
    await service.commit_batch(
      admission_batch_id=batch.admission_batch_id,
      fence_token=claim.fence_token,
      account_snapshot_id="paper:paper-a:0",
      account_snapshot_hash="a" * 64,
      obligation_watermark="b" * 64,
      now=NOW,
    )
  assert batch.status == "SUPERSEDED"


@pytest.mark.parametrize("stage", ["renew", "commit"])
async def test_claim_cannot_cross_paper_execution_scope(db, stage):
  await seed_paper(db)
  await seed_paper(db, "paper-b")
  service = sequencer(db)
  batch = await prepare(service)
  claim = await service.claim_batch(
    admission_batch_id=batch.admission_batch_id, processing_owner="paper", now=NOW
  )
  other = sequencer(db, "paper-b")
  with pytest.raises(ValueError, match="BATCH_NOT_FOUND"):
    if stage == "renew":
      await other.renew_claim(
        admission_batch_id=batch.admission_batch_id,
        fence_token=claim.fence_token,
        now=NOW,
      )
    else:
      await other.commit_batch(
        admission_batch_id=batch.admission_batch_id,
        fence_token=claim.fence_token,
        account_snapshot_id="paper:paper-a:0",
        account_snapshot_hash="a" * 64,
        obligation_watermark="b" * 64,
        now=NOW,
      )


@pytest.mark.parametrize("change", ["status", "request"])
async def test_commit_refreshes_intent_changed_by_an_independent_session(
  independent_sessions, change
):
  async with independent_sessions() as reader:
    await seed_paper(reader)
    held = await reader.get(TradeIntentRecord, "paper-a-older")
    service = sequencer(reader)
    batch = await prepare(service)
    claim = await service.claim_batch(
      admission_batch_id=batch.admission_batch_id, processing_owner="paper", now=NOW
    )
    async with independent_sessions() as writer:
      changed = await writer.get(TradeIntentRecord, held.id)
      if change == "status":
        changed.status = "REJECTED"
      else:
        changed.intent_metadata = {
          **changed.intent_metadata,
          "risk_increase_order_request": {"price": 12.0},
        }
      await writer.commit()
    assert held.status == "EXECUTION_READY"
    assert "risk_increase_order_request" not in held.intent_metadata
    with pytest.raises(ValueError, match="RISK_ADMISSION_INTENT_CHANGED"):
      await service.commit_batch(
        admission_batch_id=batch.admission_batch_id,
        fence_token=claim.fence_token,
        account_snapshot_id="paper:paper-a:0",
        account_snapshot_hash="a" * 64,
        obligation_watermark="b" * 64,
        now=NOW,
      )
    assert batch.status == "SUPERSEDED"


async def test_prepare_refreshes_order_material_committed_by_an_independent_session(
  independent_sessions,
):
  async with independent_sessions() as reader:
    await seed_paper(reader)
    held = await reader.get(TradeIntentRecord, "paper-a-older")
    await reader.commit()  # Retain the object, release the old read transaction.
    async with independent_sessions() as writer:
      changed = await writer.get(TradeIntentRecord, held.id)
      changed.intent_metadata = {
        **changed.intent_metadata,
        "risk_increase_order_request": {"price": 12.0},
      }
      await writer.commit()
    assert "risk_increase_order_request" not in held.intent_metadata
    service = sequencer(reader)
    batch = await prepare(service)
    assert held.intent_metadata["risk_increase_order_request"] == {"price": 12.0}
    claim = await service.claim_batch(
      admission_batch_id=batch.admission_batch_id, processing_owner="paper", now=NOW
    )
    result = await service.commit_batch(
      admission_batch_id=batch.admission_batch_id,
      fence_token=claim.fence_token,
      account_snapshot_id="paper:paper-a:0",
      account_snapshot_hash="a" * 64,
      obligation_watermark="b" * 64,
      now=NOW,
    )
    assert result.status == "COMMITTED"


def test_five_domain_priority_and_t_group_frozen_rank_are_independent():
  candidates = []
  for owner in (
    ExecutionOwnerType.STRATEGY_RUN,
    ExecutionOwnerType.T_ASSISTANT_EXECUTION,
    ExecutionOwnerType.BOARD_ASSISTANT_EXECUTION,
    ExecutionOwnerType.ENTRY_PLAN,
    ExecutionOwnerType.MANUAL_COMMAND,
  ):
    allocation = (
      dict(
        allocation_batch_id="allocation", allocation_rank=1, allocation_created_at=NOW
      )
      if owner is ExecutionOwnerType.T_ASSISTANT_EXECUTION
      else {}
    )
    candidates.append(
      RiskIncreaseAdmissionCandidate(
        owner.value, owner, owner.value, NOW, "a" * 64, **allocation
      )
    )
  assert [
    row.candidate.owner_type for row in rank_risk_increase_candidates(candidates)
  ] == [
    ExecutionOwnerType.MANUAL_COMMAND,
    ExecutionOwnerType.ENTRY_PLAN,
    ExecutionOwnerType.BOARD_ASSISTANT_EXECUTION,
    ExecutionOwnerType.T_ASSISTANT_EXECUTION,
    ExecutionOwnerType.STRATEGY_RUN,
  ]


def test_environment_and_scope_are_part_of_the_only_fingerprint():
  common = dict(
    account_id="account",
    account_snapshot_id="same",
    account_snapshot_hash="a" * 64,
    obligation_watermark="b" * 64,
    intent_manifest_hash="c" * 64,
  )
  values = [
    risk_increase_input_fingerprint(**common, environment=env, paper_execution_id=scope)
    for env, scope in (
      (ExecutionEnvironment.LIVE, None),
      (ExecutionEnvironment.PAPER, "paper-a"),
      (ExecutionEnvironment.PAPER, "paper-b"),
    )
  ]
  assert len(set(values)) == 3
  for env, scope in (
    (ExecutionEnvironment.LIVE, "paper-a"),
    (ExecutionEnvironment.PAPER, None),
  ):
    with pytest.raises(ValueError):
      risk_increase_input_fingerprint(
        **common, environment=env, paper_execution_id=scope
      )
