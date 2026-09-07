"""Explicit seed validation and immutable restart behavior on the real ledger."""

from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta

import pytest
from quantx_engine.t_assistant_paper_seed import (
  paper_policy_blockers,
  prepare_paper_seed,
)
from quantx_infrastructure.models.paper_execution import PaperExecutionAccountRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import event, func, select

from tests.infrastructure import test_paper_execution_ledger as ledger_tests
from tests.infrastructure.test_paper_portfolio_snapshot import reference_payload

base_sessions = ledger_tests.base_sessions
allocation_sessions = ledger_tests.allocation_sessions
sessions = ledger_tests.sessions
NOW = ledger_tests.NOW


def seed_payload():
  values = ledger_tests.seed_values()
  return {
    "paper_seed": {
      "snapshot_id": values["seed_snapshot_id"],
      "as_of": values["seed_as_of"].isoformat(),
      "cash": values["cash"],
      "non_trading_asset_value": values["non_trading_asset_value"],
      "positions": {key: asdict(value) for key, value in values["positions"].items()},
      "bucket_checkpoint": values["bucket_checkpoint"],
    },
  }


async def execution(sessions):
  snapshot, _ = await ledger_tests._seed(sessions)
  async with sessions() as db:
    return await TAssistantExecutionRepository(db).get_domain(
      snapshot.cut.execution_ref.owner_id
    )


async def test_missing_seed_is_explicit_and_does_not_read_live(sessions):
  current = await execution(sessions)
  queries = []

  def observe(_conn, _cursor, statement, _params, _context, _many):
    queries.append(statement)

  event.listen(sessions.kw["bind"].sync_engine, "before_cursor_execute", observe)
  async with sessions() as db:
    assert await prepare_paper_seed(
      db, execution=current, config_payload={}, now=NOW
    ) == ("PAPER_SEED_REQUIRED",)
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionAccountRecord))
      == 0
    )
  assert all(
    "positions" not in query.lower() and "account_snapshots" not in query.lower()
    for query in queries
  )


async def test_explicit_seed_restart_does_not_rebase_inventory_or_clock(sessions):
  current = await execution(sessions)
  payload = seed_payload()
  original = deepcopy(payload)
  async with sessions() as db:
    async with db.begin():
      assert not await prepare_paper_seed(
        db, execution=current, config_payload=payload, now=NOW
      )
      row = await db.get(PaperExecutionAccountRecord, current.execution_id)
      witness = (row.seed_payload, row.initial_snapshot_hash, row.snapshot_as_of)
  async with sessions() as db:
    async with db.begin():
      assert not await prepare_paper_seed(
        db, execution=current, config_payload=payload, now=NOW + timedelta(days=1)
      )
      row = await db.get(PaperExecutionAccountRecord, current.execution_id)
      assert (row.seed_payload, row.initial_snapshot_hash) == witness[:2]
      assert row.revision == 0
      assert row.seed_payload["as_of"] == original["paper_seed"]["as_of"]
  assert payload == original
  payload["paper_seed"]["cash"] += 1
  async with sessions() as db:
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
      await prepare_paper_seed(db, execution=current, config_payload=payload, now=NOW)


@pytest.mark.parametrize(
  "mutation,reason",
  [
    (
      lambda seed: seed.update(as_of=(NOW + timedelta(seconds=1)).isoformat()),
      "FUTURE_SEED",
    ),
    (
      lambda seed: seed.update(as_of=NOW.replace(tzinfo=None).isoformat()),
      "AWARE_TIME",
    ),
    (lambda seed: seed.update(cash=True), "INVALID_NUMBER"),
    (
      lambda seed: seed["positions"]["600000.SH"].update(long_volume=True),
      "VOLUME_INVALID",
    ),
    (
      lambda seed: seed["positions"]["600000.SH"].pop("available_volume"),
      "POSITION_FIELDS",
    ),
    (lambda seed: seed.update(copy_live=True), "SEED_FIELDS"),
  ],
)
async def test_invalid_explicit_seed_leaves_no_account(sessions, mutation, reason):
  current = await execution(sessions)
  payload = seed_payload()
  mutation(payload["paper_seed"])
  async with sessions() as db:
    with pytest.raises(ValueError, match=reason):
      await prepare_paper_seed(db, execution=current, config_payload=payload, now=NOW)
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionAccountRecord))
      == 0
    )


async def test_caller_rollback_does_not_leave_seed(sessions):
  current = await execution(sessions)
  async with sessions() as db:
    await prepare_paper_seed(
      db, execution=current, config_payload=seed_payload(), now=NOW
    )
    await db.rollback()
  async with sessions() as db:
    assert await db.get(PaperExecutionAccountRecord, current.execution_id) is None


def test_complete_policies_required_before_ready():
  payload = reference_payload()
  assert paper_policy_blockers(payload, now=NOW, required_codes=("600000.SH",)) == (
    "PAPER_REVIEW_FROZEN_GATE_POLICY_REQUIRED",
  )
  payload["entry_execution_gate_policy"] = {
    "version": "gate-v1",
    "quote_max_age_ms": 1000,
    "max_price_deviation_bps": 20,
    "max_spread_bps": 30,
    "capabilities": {
      "version": "book-v1",
      "required_fields": ["price", "bid_price", "ask_price"],
    },
  }
  assert paper_policy_blockers(payload, now=NOW, required_codes=("600000.SH",)) == ()
  assert "T_PORTFOLIO_INDUSTRY_MAPPING_INCOMPLETE" in paper_policy_blockers(
    payload, now=NOW, required_codes=("999999.SH",)
  )
