from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.execution_owner import (
  register_identity_immutability,
  validate_owner_environment,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base


def _load_execution_owner_revision() -> ModuleType:
  path = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "infrastructure"
    / "alembic"
    / "versions"
    / "20260903_0046_execution_owner_persistence.py"
  )
  spec = importlib.util.spec_from_file_location(
    "quantx_test_execution_owner_revision",
    path,
  )
  assert spec is not None
  assert spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _exit_plan_projection_rows(*intents: dict) -> dict[str, list[dict]]:
  return {
    "trade_intents": list(intents),
    "auto_exit_plans": [
      {
        "plan_id": "plan-1",
        "account_id": "account-1",
        "instrument_code": "600000.SH",
        "execution_mode": "live",
        "status": "CANCELLED",
        "source_type": "MANUAL_POSITION",
        "source_id": "manual-position:600000.SH",
        "plan_state": {},
      }
    ],
    "t_trade_batches": [],
  }


def test_public_fact_models_use_authoritative_names_and_no_owner_defaults() -> None:
  assert TradeIntentRecord.__tablename__ == "trade_intents"
  assert OrderCorrelation.__tablename__ == "order_correlations"

  for model in (
    TradeIntentRecord,
    PendingTradeOrder,
    OrderCorrelation,
    TradeCommandOutbox,
    StrategyRuntimeEvent,
  ):
    for field in ("owner_type", "owner_id", "environment"):
      column = model.__table__.c[field]
      assert column.nullable is False
      assert column.default is None
      assert column.server_default is None

  intent = TradeIntentRecord.__table__
  assert any(
    constraint.name == "uq_trade_intent_owner_idempotency"
    for constraint in intent.constraints
  )


def test_pending_and_correlation_identity_constraints_match_migration() -> None:
  revision = _load_execution_owner_revision()
  expected = (
    "(owner_type = 'STRATEGY_RUN' AND intent_id IS NOT NULL "
    "AND strategy_order_id IS NOT NULL) OR "
    "(owner_type = 'EXIT_PLAN' AND intent_id IS NOT NULL "
    "AND strategy_order_id IS NULL) OR "
    "(owner_type = 'MANUAL_COMMAND' AND intent_id IS NULL "
    "AND strategy_order_id IS NULL)"
  )
  for model, table_name, constraint_name in (
    (
      PendingTradeOrder,
      "pending_trade_orders",
      "ck_pending_trade_order_strategy_identity",
    ),
    (
      OrderCorrelation,
      "order_correlations",
      "ck_order_correlation_strategy_identity",
    ),
  ):
    model_constraint = next(
      constraint
      for constraint in model.__table__.constraints
      if constraint.name == constraint_name
    )
    assert str(model_constraint.sqltext) == expected
    assert dict(revision._CHECKS[table_name])[constraint_name] == expected


def _identity_constraint_row(
  model,
  *,
  owner_type: str,
  owner_id: str,
  strategy_order_id: str | None,
  intent_id: str | None,
  client_order_id: str,
):
  values = {
    "client_order_id": client_order_id,
    "account_id": "account-1",
    "owner_type": owner_type,
    "owner_id": owner_id,
    "environment": "LIVE",
    "strategy_run_id": None,
    "strategy_order_id": strategy_order_id,
    "intent_id": intent_id,
  }
  if model is PendingTradeOrder:
    return PendingTradeOrder(
      user_id="user-1",
      instrument_code="600000.SH",
      side="SELL",
      order_type="FIX_PRICE",
      limit_price="10.50",
      volume=100,
      status="QUEUED",
      bucket="manual",
      request_metadata={},
      **values,
    )
  return OrderCorrelation(
    id="correlation-identity-1",
    bucket="manual",
    trace_id="trace-1",
    request_metadata={},
    **values,
  )


@pytest.mark.parametrize(
  "model",
  (PendingTradeOrder, OrderCorrelation),
  ids=("pending", "correlation"),
)
@pytest.mark.parametrize(
  (
    "owner_type",
    "owner_id",
    "strategy_order_id",
    "intent_id",
    "should_commit",
  ),
  (
    ("EXIT_PLAN", "plan-1", None, "intent-1", True),
    ("EXIT_PLAN", "plan-1", "strategy-order-1", "intent-1", False),
    ("T_ASSISTANT_EXECUTION", "t-assistant-1", "strategy-order-1", "intent-1", False),
    ("ENTRY_PLAN", "entry-plan-1", "strategy-order-1", "intent-1", False),
    ("BOARD_ASSISTANT_EXECUTION", "board-assistant-1", "strategy-order-1", "intent-1", False),
  ),
)
def test_pending_and_correlation_identity_constraints_enforce_owner_shapes(
  model,
  owner_type: str,
  owner_id: str,
  strategy_order_id: str | None,
  intent_id: str | None,
  should_commit: bool,
) -> None:
  engine = create_engine("sqlite:///:memory:")
  try:
    Base.metadata.create_all(
      engine,
      tables=[PendingTradeOrder.__table__, OrderCorrelation.__table__],
    )
    with Session(engine) as session:
      if model is OrderCorrelation:
        session.add(
          _identity_constraint_row(
            PendingTradeOrder,
            owner_type="MANUAL_COMMAND",
            owner_id="parent-command-1",
            strategy_order_id=None,
            intent_id=None,
            client_order_id="pending-parent-client",
          )
        )
        candidate = _identity_constraint_row(
          OrderCorrelation,
          owner_type=owner_type,
          owner_id=owner_id,
          strategy_order_id=strategy_order_id,
          intent_id=intent_id,
          client_order_id="pending-parent-client",
        )
      else:
        candidate = _identity_constraint_row(
          PendingTradeOrder,
          owner_type=owner_type,
          owner_id=owner_id,
          strategy_order_id=strategy_order_id,
          intent_id=intent_id,
          client_order_id="pending-identity-client",
        )
      session.add(candidate)
      if should_commit:
        session.commit()
      else:
        with pytest.raises(IntegrityError):
          session.commit()
  finally:
    engine.dispose()


def test_source_execution_projection_is_distinct_from_exit_plan_owner() -> None:
  assert {
    "source_execution_owner_type",
    "source_execution_owner_id",
    "source_execution_environment",
  } <= set(AutoExitPlanRecord.__table__.c.keys())
  assert "environment" in AutoExitPlanRecord.__table__.c
  assert "execution_mode" not in AutoExitPlanRecord.__table__.c
  assert "environment" in TTradeBatch.__table__.c
  assert "execution_mode" not in TTradeBatch.__table__.c


def test_owner_validation_rejects_missing_or_noncanonical_values() -> None:
  assert validate_owner_environment(
    "STRATEGY_RUN", "run-1", "PAPER"
  ) == ("STRATEGY_RUN", "run-1", "PAPER")
  with pytest.raises(ValueError, match="OWNER_ENVIRONMENT_REQUIRED"):
    validate_owner_environment("STRATEGY_RUN", "run-1", None)
  with pytest.raises(ValueError, match="EXECUTION_ENVIRONMENT_INVALID"):
    validate_owner_environment("STRATEGY_RUN", "run-1", "paper")
  with pytest.raises(ValueError, match="OWNER_TYPE_INVALID"):
    validate_owner_environment("UNKNOWN", "run-1", "PAPER")


def test_owner_migration_is_linear_and_preflights_before_ddl() -> None:
  path = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "infrastructure"
    / "alembic"
    / "versions"
    / "20260903_0046_execution_owner_persistence.py"
  )
  source = path.read_text(encoding="utf-8")
  assert 'revision = "20260903_0046"' in source
  assert 'down_revision = "20260902_0045"' in source
  assert "updates = _preflight(bind)" in source
  upgrade_source = source[source.index("def upgrade()") :]
  assert upgrade_source.index("updates = _preflight(bind)") < upgrade_source.index(
    "_rename_tables(bind)"
  )
  assert "CREATE VIEW" not in source.upper()
  assert "CREATE TRIGGER" in source
  assert "BEFORE UPDATE OF" in source
  for table_name in (
    "trade_intents",
    "pending_trade_orders",
    "order_correlations",
    "trade_command_outbox",
    "strategy_runtime_events",
    "auto_exit_plans",
    "t_trade_batches",
    "trade_confirmation_challenges",
  ):
    assert f'"{table_name}":' in source


def test_order_correlation_unique_constraint_does_not_drop_backing_index(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_execution_owner_revision()
  operations: list[tuple[str, str, str | None]] = []

  class Inspector:
    def get_unique_constraints(self, table_name: str) -> list[dict[str, str]]:
      assert table_name == "order_correlations"
      return [{"name": "uq_strategy_order_client"}]

    def get_indexes(self, table_name: str) -> list[dict[str, object]]:
      assert table_name == "order_correlations"
      return [
        {
          "name": "uq_strategy_order_client",
          "unique": True,
          "duplicates_constraint": "uq_strategy_order_client",
        }
      ]

  class Batch:
    def __enter__(self):
      return self

    def __exit__(self, *_args):
      return False

    def drop_constraint(self, name: str, *, type_: str) -> None:
      operations.append(("drop_constraint", name, type_))

    def create_unique_constraint(self, name: str, _columns: list[str]) -> None:
      operations.append(("create_unique_constraint", name, None))

  monkeypatch.setattr(revision, "inspect", lambda _bind: Inspector())
  monkeypatch.setattr(revision.op, "batch_alter_table", lambda *_args, **_kwargs: Batch())
  monkeypatch.setattr(
    revision.op,
    "drop_index",
    lambda name, table_name: operations.append(("drop_index", name, table_name)),
  )

  revision._ensure_order_correlation_client_constraint(
    SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
  )

  assert operations == [
    ("drop_constraint", "uq_strategy_order_client", "unique"),
    ("create_unique_constraint", "uq_order_correlation_client", None),
  ]


def test_table_comments_use_unbound_postgresql_ddl() -> None:
  revision = _load_execution_owner_revision()
  statements: list[tuple[str, object]] = []

  class Bind:
    dialect = postgresql.dialect()

    def execute(self, statement) -> None:
      compiled = statement.compile(dialect=self.dialect)
      statements.append((compiled.string, compiled.params))

  revision._table_comments(Bind())

  assert len(statements) == 2
  for sql, params in statements:
    assert sql.startswith("COMMENT ON TABLE ")
    assert "$1" not in sql
    assert params is None


def test_exit_plan_intent_owner_uses_plan_for_terminal_direct_projection() -> None:
  revision = _load_execution_owner_revision()
  intent = {
    "id": "terminal-intent-1",
    "owner_type": "EXIT_PLAN",
    "owner_id": "plan-1",
    "strategy_run_id": None,
    "account_id": "account-1",
    "instrument_code": "600000.sh",
    "direction": "SELL",
    "status": "CANCELLED",
    # This must not be consulted for the owner or environment proof.
    "metadata": {
      "owner_type": "STRATEGY_RUN",
      "owner_id": "metadata-run",
      "environment": "PAPER",
    },
  }

  owners = revision._exit_plan_intent_owners(
    _exit_plan_projection_rows(intent),
    {},
  )

  assert owners == {"terminal-intent-1": ("EXIT_PLAN", "plan-1", "LIVE")}


@pytest.mark.parametrize(
  ("field", "value", "message"),
  (
    ("account_id", "account-2", "account conflict"),
    ("instrument_code", "000001.SZ", "instrument conflict"),
    ("strategy_run_id", "run-1", "strategy_run_id must be absent"),
  ),
)
def test_exit_plan_direct_intent_proof_fails_closed(
  field: str,
  value: str,
  message: str,
) -> None:
  revision = _load_execution_owner_revision()
  intent = {
    "id": "terminal-intent-1",
    "owner_type": "EXIT_PLAN",
    "owner_id": "plan-1",
    "strategy_run_id": None,
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "direction": "SELL",
  }
  intent[field] = value

  with pytest.raises(RuntimeError, match=message):
    revision._exit_plan_intent_owners(
      _exit_plan_projection_rows(intent),
      {},
    )


def test_exit_plan_direct_intent_requires_exact_durable_plan() -> None:
  revision = _load_execution_owner_revision()
  intent = {
    "id": "terminal-intent-1",
    "owner_type": "EXIT_PLAN",
    "owner_id": "missing-plan",
    "strategy_run_id": None,
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "direction": "SELL",
  }

  with pytest.raises(RuntimeError, match="plan proof missing") as error:
    revision._exit_plan_intent_owners(
      _exit_plan_projection_rows(intent),
      {},
    )
  assert "trade_intents" in str(error.value)
  assert "terminal-intent-1" in str(error.value)


def test_exit_plan_pending_link_keeps_one_to_one_plan_uniqueness() -> None:
  revision = _load_execution_owner_revision()
  rows = _exit_plan_projection_rows(
    {
      "id": "pending-intent-1",
      "owner_type": None,
      "owner_id": None,
      "strategy_run_id": None,
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "direction": "SELL",
    }
  )
  rows["auto_exit_plans"].append(
    {
      **rows["auto_exit_plans"][0],
      "plan_id": "plan-2",
      "plan_state": {"pending_intent_id": "pending-intent-1"},
    }
  )
  rows["auto_exit_plans"][0]["plan_state"] = {
    "pending_intent_id": "pending-intent-1"
  }

  with pytest.raises(RuntimeError, match="multiple exit plans"):
    revision._exit_plan_intent_owners(rows, {})


def test_exit_plan_metadata_only_owner_does_not_prove_intent_owner(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_execution_owner_revision()
  intent = {
    "id": "metadata-only-intent",
    "owner_type": None,
    "owner_id": None,
    "strategy_run_id": None,
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "direction": "SELL",
    "metadata": {
      "owner_type": "EXIT_PLAN",
      "owner_id": "plan-1",
      "environment": "LIVE",
    },
  }
  rows = _exit_plan_projection_rows(intent)
  for table_name in (
    *revision.PUBLIC_FACT_TABLES,
    *revision.SOURCE_PROJECTION_TABLES,
  ):
    rows.setdefault(table_name, [])
  monkeypatch.setattr(revision, "_canonical_rows", lambda bind: (rows, {}))
  monkeypatch.setattr(revision, "_strategy_runs", lambda bind: {})

  with pytest.raises(RuntimeError, match="owner missing"):
    revision._preflight(object())


def test_pending_legacy_environment_cannot_conflict_with_exit_plan_projection() -> None:
  revision = _load_execution_owner_revision()
  intent = {
    "id": "exit-intent-1",
    "owner_type": "EXIT_PLAN",
    "owner_id": "plan-1",
    "strategy_run_id": None,
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "direction": "SELL",
  }
  rows = _exit_plan_projection_rows(intent)
  owner = revision._exit_plan_intent_owners(rows, {})["exit-intent-1"]
  projected_intent = dict(intent)
  projected_intent.update(
    {
      "owner_type": owner[0],
      "owner_id": owner[1],
      "environment": owner[2],
    }
  )
  pending = {
    "client_order_id": "pending-client-1",
    "intent_id": "exit-intent-1",
    "execution_mode": "paper",
    "strategy_run_id": None,
  }

  with pytest.raises(RuntimeError, match="environment conflict"):
    revision._resolve_owner_environment(
      pending,
      {},
      projected_intent,
      include_nested=False,
    )


def test_pending_identity_rules_allow_exit_plan_without_strategy_order(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_execution_owner_revision()
  intent = {
    "id": "exit-intent-1",
    "owner_type": "EXIT_PLAN",
    "owner_id": "plan-1",
    "strategy_run_id": None,
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "direction": "SELL",
  }
  rows = _exit_plan_projection_rows(intent)
  owner = revision._exit_plan_intent_owners(rows, {})["exit-intent-1"]
  projected_intent = dict(intent)
  projected_intent.update(
    {
      "owner_type": owner[0],
      "owner_id": owner[1],
      "environment": owner[2],
    }
  )
  pending = {
    "client_order_id": "pending-client-1",
    "intent_id": "exit-intent-1",
    "execution_mode": "live",
    "strategy_order_id": None,
    "strategy_run_id": None,
  }
  revision._resolve_owner_environment(
    pending,
    {},
    projected_intent,
    include_nested=False,
  )

  rows_by_table = {
    table_name: []
    for table_name in (
      *revision.PUBLIC_FACT_TABLES,
      *revision.SOURCE_PROJECTION_TABLES,
    )
  }
  rows_by_table["trade_intents"] = [intent]
  rows_by_table["pending_trade_orders"] = [pending]
  rows_by_table["auto_exit_plans"] = rows["auto_exit_plans"]
  monkeypatch.setattr(
    revision,
    "_canonical_rows",
    lambda bind: (rows_by_table, {}),
  )
  monkeypatch.setattr(revision, "_strategy_runs", lambda bind: {})
  updates = revision._preflight(object())
  assert updates["pending_trade_orders"][0][1]["owner_type"] == "EXIT_PLAN"


def test_pending_legacy_environment_cannot_conflict_with_strategy_projection() -> None:
  revision = _load_execution_owner_revision()
  projected_intent = {
    "id": "strategy-intent-1",
    "owner_type": "STRATEGY_RUN",
    "owner_id": "run-1",
    "environment": "LIVE",
    "strategy_run_id": "run-1",
  }
  pending = {
    "client_order_id": "pending-client-2",
    "intent_id": "strategy-intent-1",
    "strategy_order_id": "strategy-order-1",
    "strategy_run_id": "run-1",
    "execution_mode": "paper",
  }

  with pytest.raises(RuntimeError, match="environment conflict"):
    revision._resolve_owner_environment(
      pending,
      {"run-1": {"mode": "LIVE"}},
      projected_intent,
      include_nested=False,
    )


def _manual_pending_preflight_rows() -> dict[str, list[dict]]:
  pending = {
    "client_order_id": "manual-client-1",
    "user_id": "user-1",
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "side": "SELL",
    "order_type": "FIX_PRICE",
    "limit_price": "10.50",
    "volume": 100,
    "execution_mode": "live",
    "risk_decision_id": "risk-1",
    "intent_id": None,
    "strategy_order_id": None,
    "strategy_run_id": None,
    "request_metadata": {
      "challenge_id": "challenge-1",
      "owner_type": "STRATEGY_RUN",
      "owner_id": "forged-run",
      "environment": "PAPER",
    },
    "status": "CANCELLED",
  }
  challenge = {
    "id": "challenge-1",
    "action": "MANUAL_ORDER",
    "user_id": "user-1",
    "account_id": "account-1",
    "payload": {
      "action": "MANUAL_ORDER",
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "side": "SELL",
      "price_type": "LIMIT",
      "limit_price": 10.50,
      "volume": 1000,
      "final_volume": 100,
      "execution_mode": "live",
      "idempotency_key": "manual-idempotency-1",
      "risk_decision_id": "risk-1",
      "owner_type": "STRATEGY_RUN",
      "owner_id": "forged-run",
      "environment": "live",
    },
    "consumed_at": "2026-09-03T10:00:00+08:00",
    "result_reference": {
      "status": "QUEUED",
      "client_order_id": "manual-client-1",
      "message_id": "manual-message-1",
    },
  }
  outbox = {
    "message_id": "manual-message-1",
    "client_order_id": "manual-client-1",
    "account_id": "account-1",
    "payload": {
      "command_kind": "PLACE_ORDER",
      "owner_type": "STRATEGY_RUN",
      "owner_id": "forged-run",
      "execution_mode": "paper",
    },
  }
  rows = {
    table_name: []
    for table_name in (
      "trade_intents",
      "pending_trade_orders",
      "order_correlations",
      "trade_command_outbox",
      "strategy_runtime_events",
      "auto_exit_plans",
      "t_trade_batches",
      "trade_confirmation_challenges",
    )
  }
  rows["pending_trade_orders"].append(pending)
  rows["trade_command_outbox"].append(outbox)
  rows["trade_confirmation_challenges"].append(challenge)
  return rows


def test_intentless_pending_uses_consumed_challenge_and_outbox_inherits_owner(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_execution_owner_revision()
  rows = _manual_pending_preflight_rows()
  monkeypatch.setattr(revision, "_canonical_rows", lambda bind: (rows, {}))
  monkeypatch.setattr(revision, "_strategy_runs", lambda bind: {})

  updates = revision._preflight(object())

  assert updates["pending_trade_orders"] == [
    (
      "manual-client-1",
      {
        "owner_type": "MANUAL_COMMAND",
        "owner_id": "challenge-1",
        "environment": "LIVE",
        "strategy_run_id": None,
      },
    )
  ]
  assert updates["trade_command_outbox"] == [
    (
      "manual-message-1",
      {
        "owner_type": "MANUAL_COMMAND",
        "owner_id": "challenge-1",
        "environment": "LIVE",
      },
    )
  ]


@pytest.mark.parametrize(
  "mutation",
  (
    lambda rows: rows["pending_trade_orders"][0]["request_metadata"].update(
      {"challenge_id": "missing-challenge"}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0].update(
      {"action": "EXIT_PLAN_AUTHORIZATION"}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0].update(
      {"consumed_at": None}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0]["result_reference"].update(
      {"client_order_id": "other-client"}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0].update(
      {"account_id": "account-2"}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0].update(
      {"user_id": "user-2"}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0]["payload"].update(
      {"instrument_code": "000001.SZ"}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0]["payload"].update(
      {"side": "BUY"}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0]["payload"].update(
      {"final_volume": 99}
    ),
    lambda rows: rows["trade_confirmation_challenges"][0]["payload"].update(
      {"execution_mode": "paper"}
    ),
    lambda rows: rows["pending_trade_orders"][0].update(
      {"risk_decision_id": "risk-2"}
    ),
    lambda rows: rows["pending_trade_orders"][0].update(
      {"limit_price": "11.50"}
    ),
  ),
)
def test_intentless_pending_challenge_proof_fails_closed(
  monkeypatch: pytest.MonkeyPatch,
  mutation,
) -> None:
  revision = _load_execution_owner_revision()
  rows = _manual_pending_preflight_rows()
  mutation(rows)
  monkeypatch.setattr(revision, "_canonical_rows", lambda bind: (rows, {}))
  monkeypatch.setattr(revision, "_strategy_runs", lambda bind: {})

  with pytest.raises(RuntimeError, match="pending_trade_orders.*manual-client-1"):
    revision._preflight(object())


def _terminal_tombstone_rows() -> dict[str, list[dict]]:
  rows = {
    table_name: []
    for table_name in (
      "trade_intents",
      "pending_trade_orders",
      "order_correlations",
      "trade_command_outbox",
      "strategy_runtime_events",
      "auto_exit_plans",
      "t_trade_batches",
      "trade_confirmation_challenges",
    )
  }
  rows["auto_exit_plans"].append(
    {
      "plan_id": "history-plan-42",
      "status": "CANCELLED",
      "strategy_run_id": "deleted-run-42",
      "execution_mode": "live",
      "source_type": "T_TRADE_BATCH",
      "source_id": "deleted-batch-42",
      "plan_state": {},
      "last_error": "TEST_FIXTURE_LEAK_REPAIRED_20260830",
    }
  )
  return rows


def test_terminal_strategy_run_tombstone_preserves_deleted_run_identity() -> None:
  revision = _load_execution_owner_revision()
  rows = _terminal_tombstone_rows()
  updates = {"auto_exit_plans": []}

  revision._preflight_exit_plans(rows, {}, updates)

  assert updates["auto_exit_plans"] == [
    (
      "history-plan-42",
      {
        "source_execution_owner_type": "STRATEGY_RUN",
        "source_execution_owner_id": "deleted-run-42",
        "source_execution_environment": "LIVE",
        "environment": "LIVE",
        "strategy_run_id": "deleted-run-42",
      },
    )
  ]


@pytest.mark.parametrize(
  "mutation",
  (
    lambda rows: rows["auto_exit_plans"][0].update({"status": "ACTIVE"}),
    lambda rows: rows["auto_exit_plans"][0].update({"execution_mode": None}),
    lambda rows: rows["auto_exit_plans"][0]["plan_state"].update(
      {"pending_order_id": "pending-order-1"}
    ),
    lambda rows: rows["auto_exit_plans"][0].update({"strategy_run_id": None}),
    lambda rows: rows["auto_exit_plans"][0].update(
      {
        "source_execution_owner_type": "MANUAL_COMMAND",
        "source_execution_owner_id": "manual-command-1",
        "source_execution_environment": "LIVE",
      }
    ),
  ),
)
def test_terminal_strategy_run_tombstone_requires_unambiguous_direct_proof(
  mutation,
) -> None:
  revision = _load_execution_owner_revision()
  rows = _terminal_tombstone_rows()
  mutation(rows)
  updates = {"auto_exit_plans": []}

  with pytest.raises(RuntimeError, match="auto_exit_plans.*history-plan-42"):
    revision._preflight_exit_plans(rows, {}, updates)


@pytest.mark.parametrize(
  "table_name",
  ("trade_intents", "pending_trade_orders", "trade_command_outbox", "strategy_runtime_events"),
)
def test_terminal_strategy_run_tombstone_rejects_active_public_obligation(
  table_name: str,
) -> None:
  revision = _load_execution_owner_revision()
  rows = _terminal_tombstone_rows()
  rows["trade_intents"].append(
    {
      "id": "history-intent-42",
      "owner_type": "EXIT_PLAN",
      "owner_id": "history-plan-42",
      "status": "CANCELLED",
    }
  )
  rows["pending_trade_orders"].append(
    {
      "client_order_id": "history-client-42",
      "intent_id": "history-intent-42",
      "status": "CANCELLED",
    }
  )
  rows["trade_command_outbox"].append(
    {
      "message_id": "history-message-42",
      "client_order_id": "history-client-42",
      "delivery_status": "CANCELLED",
    }
  )
  rows["strategy_runtime_events"].append(
    {
      "event_id": "history-event-42",
      "client_order_id": "history-client-42",
      "application_status": "APPLIED",
    }
  )
  if table_name == "trade_intents":
    rows["trade_intents"][0]["status"] = "PENDING"
  elif table_name == "pending_trade_orders":
    rows["pending_trade_orders"][0]["status"] = "QUEUED"
  elif table_name == "trade_command_outbox":
    rows["trade_command_outbox"][0]["delivery_status"] = "QUEUED"
  else:
    rows["strategy_runtime_events"][0]["application_status"] = "PENDING"
  updates = {"auto_exit_plans": []}

  with pytest.raises(RuntimeError, match="auto_exit_plans.*history-plan-42"):
    revision._preflight_exit_plans(rows, {}, updates)


def test_orm_identity_guard_rejects_persisted_owner_changes() -> None:
  probe_base = declarative_base()

  class OwnerProbe(probe_base):
    __tablename__ = "owner_probe"

    id = Column(Integer, primary_key=True)
    owner_type = Column(String(32), nullable=False)
    owner_id = Column(String(128), nullable=False)
    environment = Column(String(16), nullable=False)

  register_identity_immutability(OwnerProbe)
  engine = create_engine("sqlite:///:memory:")
  probe_base.metadata.create_all(engine)
  with Session(engine) as session:
    session.add(
      OwnerProbe(
        id=1,
        owner_type="STRATEGY_RUN",
        owner_id="run-1",
        environment="PAPER",
      )
    )
    session.commit()
    persisted = session.get(OwnerProbe, 1)
    assert persisted is not None
    persisted.environment = "LIVE"
    with pytest.raises(ValueError, match="OWNER_ENVIRONMENT_IMMUTABLE"):
      session.flush()


def test_trade_intent_repository_requires_and_freezes_owner_projection() -> None:
  with pytest.raises(ValueError, match="OWNER_ENVIRONMENT_REQUIRED"):
    TradeIntentRepository._prepare_create_payload(
      {"owner_type": "STRATEGY_RUN", "owner_id": "run-1"}
    )
  payload = TradeIntentRepository._prepare_create_payload(
    {
      "owner_type": "STRATEGY_RUN",
      "owner_id": "run-1",
      "environment": "PAPER",
      "idempotency_key": "intent-retry-1",
    }
  )
  assert payload["environment"] == "PAPER"
  assert payload["owner_type"] == "STRATEGY_RUN"

  existing = SimpleNamespace(
    owner_type="STRATEGY_RUN",
    owner_id="run-1",
    environment="PAPER",
  )
  TradeIntentRepository._validate_immutable_owner_update(
    existing,
    {"owner_type": "STRATEGY_RUN", "owner_id": "run-1", "environment": "PAPER"},
  )
  with pytest.raises(ValueError, match="OWNER_ENVIRONMENT_IMMUTABLE"):
    TradeIntentRepository._validate_immutable_owner_update(
      existing,
      {"owner_type": "STRATEGY_RUN", "owner_id": "run-2", "environment": "PAPER"},
    )
