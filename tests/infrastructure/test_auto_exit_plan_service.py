from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.trading.exit_plan import (
  ExitDecision,
  ExitEvaluationContext,
  ExitPlanBook,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.services import auto_exit_plan_service as service_module
from quantx_infrastructure.services.auto_exit_plan_service import (
  AVAILABLE_NOW,
  REPLACE_CANCELLABLE,
  UNALLOCATED_ONLY,
  UNTIL_SNAPSHOT_CLEARED,
  AutoExitPlanService,
)


class ScalarResult:
  def __init__(self, value):
    self.value = value

  def scalar_one_or_none(self):
    return self.value

  def scalars(self):
    return self

  def all(self):
    return list(self.value or [])


class FakeDb:
  def __init__(self, value):
    self.value = value

  async def execute(self, _statement):
    return ScalarResult(self.value)


class FakeSession:
  def __init__(self, positions):
    self.positions = positions
    self.added = []
    self.committed = False

  async def __aenter__(self):
    return self

  async def __aexit__(self, *_args):
    return None

  async def execute(self, _statement):
    return ScalarResult(self.positions)

  def add(self, value):
    self.added.append(value)

  async def commit(self):
    self.committed = True


class FakePlanRepository:
  def __init__(self, _db, reserving):
    self.reserving = reserving

  async def find_reserving(self, **_kwargs):
    return self.reserving


def active_record(*, plan_id="existing-plan", volume=200, pending=False):
  plan = ExitPlanBook().register_entry_fill(
    ExitPlanTemplate(
      plan_id=plan_id,
      source_type="MANUAL_POSITION",
      source_id=plan_id,
      account_id="account-a",
      instrument_code="600000.SH",
      bucket="manual",
      rules=[
        ExitRuleSpec(
          rule_id=f"{plan_id}:target",
          strategy=ExitRuleType.TARGET_PRICE,
          parameters={"target_price": 20},
        )
      ],
    ),
    volume=volume,
    price=10,
  )
  if pending:
    plan.pending_order_id = "order-pending"
    plan.status = ExitPlanStatus.EXIT_PENDING
  return AutoExitPlanRecord(
    plan_id=plan_id,
    account_id="account-a",
    instrument_code="600000.SH",
    bucket="manual",
    source_type="MANUAL_POSITION",
    source_id=plan_id,
    enabled=True,
    status=plan.status.value,
    execution_mode="paper",
    auto_exit_authorized=False,
    config_version=1,
    protected_volume=volume,
    exited_volume=0,
    remaining_volume=volume,
    entry_avg_price=10,
    plan_state=plan.to_dict(),
    pending_client_order_id="order-pending" if pending else None,
  )


def liquidation_position(*, volume=500, available=300):
  return SimpleNamespace(
    account_id="account-a",
    stock_code="600000.SH",
    volume=volume,
    can_use_volume=available,
    avg_price=10,
    created_at=None,
  )


def install_liquidation_fakes(monkeypatch, *, position, reserving=None):
  session = FakeSession([position])
  records = list(reserving or [])
  monkeypatch.setattr(service_module, "AsyncSessionLocal", lambda: session)
  monkeypatch.setattr(
    service_module,
    "AutoExitPlanRepository",
    lambda db: FakePlanRepository(db, records),
  )

  async def ignore_event(*_args, **_kwargs):
    return None

  monkeypatch.setattr(AutoExitPlanService, "_append_event", ignore_event)
  return session


def liquidation_payload(completion, conflict=UNALLOCATED_ONLY):
  return {
    "account_id": "account-a",
    "completion_strategy": completion,
    "conflict_strategy": conflict,
    "confirm": True,
    "scope": "SELECTED",
    "instrument_codes": ["600000.SH"],
    "execution_mode": "paper",
  }


def pending_plan():
  book = ExitPlanBook()
  plan = book.register_entry_fill(
    ExitPlanTemplate(
      plan_id="manual-position:condition-1",
      source_type="MANUAL_POSITION",
      source_id="condition-1",
      account_id="account-a",
      instrument_code="600000.SH",
      bucket="manual",
      rules=[
        ExitRuleSpec(
          rule_id="adaptive-volume-price",
          strategy=ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING,
        )
      ],
    ),
    volume=300,
    price=10,
  )
  book.mark_intent(
    ExitDecision(
      plan_id=plan.plan_id,
      rule_id="adaptive-volume-price",
      rule_type=ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING,
      reason="test",
      volume=300,
      priority=750,
    ),
    "intent-1",
  )
  return plan


@pytest.mark.asyncio
async def test_submit_decision_without_sellable_volume_records_audit_error(
  monkeypatch,
):
  recorded = []

  async def record_error(_service, plan_id, error):
    recorded.append((plan_id, error))

  monkeypatch.setattr(AutoExitPlanService, "_record_error", record_error)
  decision = ExitDecision(
    plan_id="plan-no-volume",
    rule_id="hard-stop",
    rule_type=ExitRuleType.HARD_STOP,
    reason="HARD_STOP",
    volume=100,
    priority=1000,
  )

  result = await AutoExitPlanService()._submit_decision(
    plan_id=decision.plan_id,
    decision=decision,
    context=ExitEvaluationContext(
      timestamp=datetime.now(),
      current_price=10.0,
    ),
    position=None,
  )

  assert result is None
  assert recorded == [("plan-no-volume", "no_legal_sell_volume")]


@pytest.mark.asyncio
async def test_pending_submission_is_recovered_from_durable_command():
  plan = pending_plan()
  pending = PendingTradeOrder(
    client_order_id="client-1",
    account_id="account-a",
    intent_id="intent-1",
    status="PENDING",
  )
  record = SimpleNamespace(account_id="account-a", last_error="previous")

  recovered = await AutoExitPlanService._recover_pending_submission(
    FakeDb(pending), record, plan
  )

  assert recovered
  assert plan.pending_order_id == "client-1"
  assert plan.status == ExitPlanStatus.EXIT_PENDING
  assert record.last_error is None


@pytest.mark.asyncio
async def test_orphaned_intent_is_released_for_retry_after_timeout():
  plan = pending_plan()
  plan.rule_state["__runtime__"] = {
    "pending_marked_at": (datetime.now() - timedelta(seconds=11)).isoformat()
  }
  record = SimpleNamespace(account_id="account-a", last_error=None)

  recovered = await AutoExitPlanService._recover_pending_submission(
    FakeDb(None), record, plan
  )

  assert not recovered
  assert plan.pending_intent_id == ""
  assert plan.status == ExitPlanStatus.ACTIVE
  assert record.last_error == "orphaned_exit_intent_released"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("completion", "expected_volume"),
  [(AVAILABLE_NOW, 300), (UNTIL_SNAPSHOT_CLEARED, 500)],
)
async def test_liquidation_completion_strategy_protects_fixed_snapshot(
  monkeypatch, completion, expected_volume
):
  session = install_liquidation_fakes(
    monkeypatch,
    position=liquidation_position(),
  )

  result = await AutoExitPlanService().create_liquidation_group(
    liquidation_payload(completion)
  )

  created = session.added[0]
  assert result["success"]
  assert created.completion_strategy == completion
  assert created.protected_volume == expected_volume
  assert created.remaining_volume == expected_volume
  assert created.plan_state["entry_filled_volume"] == expected_volume
  assert created.plan_state["template"]["metadata"]["position_volume_snapshot"] == 500


@pytest.mark.asyncio
async def test_liquidation_unallocated_only_preserves_existing_plan(monkeypatch):
  existing = active_record(volume=200)
  session = install_liquidation_fakes(
    monkeypatch,
    position=liquidation_position(volume=500, available=500),
    reserving=[existing],
  )

  result = await AutoExitPlanService().create_liquidation_group(
    liquidation_payload(UNTIL_SNAPSHOT_CLEARED)
  )

  assert result["success"]
  assert existing.enabled
  assert existing.status == ExitPlanStatus.ACTIVE.value
  assert session.added[0].protected_volume == 300
  assert result["items"][0]["conflict_plan_ids"] == [existing.plan_id]


@pytest.mark.asyncio
async def test_liquidation_replace_cancels_cancellable_conflict(monkeypatch):
  existing = active_record(volume=200)
  session = install_liquidation_fakes(
    monkeypatch,
    position=liquidation_position(volume=500, available=500),
    reserving=[existing],
  )

  result = await AutoExitPlanService().create_liquidation_group(
    liquidation_payload(UNTIL_SNAPSHOT_CLEARED, REPLACE_CANCELLABLE)
  )

  assert result["success"]
  assert not existing.enabled
  assert existing.status == ExitPlanStatus.CANCELLED.value
  assert session.added[0].protected_volume == 500


@pytest.mark.asyncio
async def test_liquidation_pending_sell_blocks_duplicate_plan(monkeypatch):
  existing = active_record(volume=200, pending=True)
  session = install_liquidation_fakes(
    monkeypatch,
    position=liquidation_position(volume=500, available=500),
    reserving=[existing],
  )

  result = await AutoExitPlanService().create_liquidation_group(
    liquidation_payload(UNTIL_SNAPSHOT_CLEARED, REPLACE_CANCELLABLE)
  )

  assert not result["success"]
  assert session.added == []
  assert "待成交卖单" in result["items"][0]["error"]
  assert existing.enabled


@pytest.mark.asyncio
async def test_stale_market_context_is_persisted_without_exit_submit(
  monkeypatch,
):
  record = active_record(plan_id="stale-evaluate")

  class Session:
    committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, _model, _key):
      return None

    async def commit(self):
      self.committed = True

  session = Session()

  class Repository:
    def __init__(self, _db):
      pass

    async def find_by_id(self, _plan_id, for_update=False):
      del for_update
      return record

  submit = AsyncMock()
  monkeypatch.setattr(service_module, "AsyncSessionLocal", lambda: session)
  monkeypatch.setattr(service_module, "AutoExitPlanRepository", Repository)
  monkeypatch.setattr(AutoExitPlanService, "_submit_decision", submit)

  result = await AutoExitPlanService().evaluate_and_submit(
    plan_id=record.plan_id,
    context=ExitEvaluationContext(
      timestamp=datetime.now(),
      current_price=0.0,
      market_data_age_seconds=999.0,
      source="WHOLE_QUOTE_UNAVAILABLE",
    ),
    position=liquidation_position(),
  )

  assert result is None
  assert record.data_quality == "MARKET_DATA_STALE"
  assert record.last_error == "market_data_stale"
  assert session.committed
  submit.assert_not_awaited()


def test_market_context_allows_normal_miniqmt_tick_jitter() -> None:
  context = ExitEvaluationContext(
    timestamp=datetime.now(),
    current_price=10.0,
    market_data_age_seconds=3.5,
    source="WHOLE_QUOTE",
  )

  assert AutoExitPlanService._market_context_error(context, lambda: True) == ""
  stale_context = ExitEvaluationContext(
    timestamp=datetime.now(),
    current_price=10.0,
    market_data_age_seconds=10.001,
    source="WHOLE_QUOTE",
  )
  assert (
    AutoExitPlanService._market_context_error(stale_context, lambda: True)
    == "market_data_stale"
  )


@pytest.mark.asyncio
async def test_confirm_intent_rejects_nonready_stream_without_processor_submit(
  monkeypatch,
):
  plan = pending_plan()
  record = active_record(plan_id=plan.plan_id, volume=300)
  record.plan_state = plan.to_dict()
  record.status = plan.status.value
  intent = SimpleNamespace(
    id="intent-1",
    status="AWAITING_APPROVAL",
    notes=None,
    intent_metadata={},
  )

  class Session:
    committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, _model, key):
      return intent if key == "intent-1" else None

    async def commit(self):
      self.committed = True

  session = Session()

  class Repository:
    def __init__(self, _db):
      pass

    async def find_by_id(self, _plan_id, for_update=False):
      del for_update
      return record

  processor = SimpleNamespace(process_approved_exit_intent=AsyncMock())
  monkeypatch.setattr(service_module, "AsyncSessionLocal", lambda: session)
  monkeypatch.setattr(service_module, "AutoExitPlanRepository", Repository)
  monkeypatch.setattr(
    service_module,
    "TradeIntentProcessor",
    lambda: processor,
  )

  result = await AutoExitPlanService().confirm_exit_intent(
    plan_id=record.plan_id,
    intent_id="intent-1",
    context=ExitEvaluationContext(
      timestamp=datetime.now(),
      current_price=10.0,
      market_data_age_seconds=0.0,
      source="QMT_WHOLE_QUOTE",
    ),
    position=liquidation_position(),
    market_ready=lambda: False,
  )

  assert result == {
    "success": False,
    "code": "MARKET_DATA_STREAM_NOT_READY",
    "error": "MARKET_DATA_STREAM_NOT_READY",
  }
  assert record.data_quality == "MARKET_DATA_STALE"
  assert record.last_error == "MARKET_DATA_STREAM_NOT_READY"
  assert intent.status == "REJECTED"
  assert intent.notes == "MARKET_DATA_STREAM_NOT_READY"
  assert intent.intent_metadata["market_data_gate"] == (
    "MARKET_DATA_STREAM_NOT_READY"
  )
  assert record.plan_state["pending_intent_id"] == ""
  assert session.committed
  processor.process_approved_exit_intent.assert_not_awaited()
