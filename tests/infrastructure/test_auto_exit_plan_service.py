from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.trading.exit_plan import (
  ExitCostBasisMode,
  ExitDecision,
  ExitEvaluationContext,
  ExitPlan,
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
from quantx_infrastructure.services.exit_plan_scope_lock import LockedExitPlanScope


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


class CapacityDb:
  def __init__(self, position):
    self.position = position

  async def scalar(self, _statement):
    return self.position


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


def locked_scope_for(
  record,
  *,
  position=None,
  plans=None,
):
  scope_plans = list(plans or [record])
  return LockedExitPlanScope(
    position=position or liquidation_position(volume=1000, available=1000),
    plans=scope_plans,
    target_plan=record,
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


@pytest.mark.asyncio
async def test_manual_cost_basis_uses_all_in_unit_cost():
  result = await AutoExitPlanService._resolve_manual_cost_basis(
    FakeDb([]),
    payload={
      "cost_basis": {
        "mode": "MANUAL_UNIT_COST",
        "unit_cost_cny": 12.3456,
      }
    },
    account_id="account-a",
    instrument_code="600000.SH",
    requested_volume=300,
  )

  assert result.mode == ExitCostBasisMode.MANUAL_UNIT_COST
  assert result.unit_cost_cny == pytest.approx(12.3456)
  assert result.basis_volume == 300
  assert result.includes_buy_fees


@pytest.mark.asyncio
async def test_capacity_restore_requires_explicit_reconciliation(monkeypatch):
  record = active_record(volume=200)
  record.capacity_status = service_module.CAPACITY_RECONCILE_REQUIRED
  db = CapacityDb(liquidation_position(volume=500))
  monkeypatch.setattr(
    service_module,
    "AutoExitPlanRepository",
    lambda _db: FakePlanRepository(_db, [record]),
  )

  async def ignore_event(*_args, **_kwargs):
    return None

  monkeypatch.setattr(AutoExitPlanService, "_append_event", ignore_event)

  guarded = await AutoExitPlanService()._reconcile_capacity_locked(
    db,
    account_id="account-a",
    instrument_code="600000.SH",
    locked_scope=LockedExitPlanScope(
      position=db.position,
      plans=[record],
    ),
  )

  assert guarded["ready"] is False
  assert record.capacity_status == service_module.CAPACITY_RECONCILE_REQUIRED
  assert "显式重新对账" in guarded["capacity_error"]

  reconciled = await AutoExitPlanService()._reconcile_capacity_locked(
    db,
    account_id="account-a",
    instrument_code="600000.SH",
    allow_restore=True,
    locked_scope=LockedExitPlanScope(
      position=db.position,
      plans=[record],
    ),
  )

  assert reconciled["ready"] is True
  assert record.capacity_status == service_module.CAPACITY_READY
  assert reconciled["capacity_error"] is None


@pytest.mark.asyncio
async def test_capacity_shortfall_revokes_authority_and_emits_audit_events(
  monkeypatch,
):
  first = active_record(plan_id="capacity-plan-a", volume=300)
  second = active_record(plan_id="capacity-plan-b", volume=300)
  for record in (first, second):
    record.execution_mode = "live"
    record.auto_exit_authorized = True
    record.auto_exit_authorization_fingerprint = "f" * 64
    record.auto_exit_authorization_config_version = 1
    record.auto_exit_authorized_at = datetime.now()
    record.auto_exit_authorization_expires_at = datetime.now() + timedelta(days=1)
    record.auto_exit_authorization_challenge_id = "challenge-1"
    record.auto_exit_authorization_user_id = "user-1"
    record.auto_exit_authorization_device_session_id = "session-1"
    state = dict(record.plan_state)
    template = dict(state["template"])
    template["auto_exit_authorized"] = True
    state["template"] = template
    record.plan_state = state
  events = []

  async def append_event(_db, **kwargs):
    events.append(kwargs)

  monkeypatch.setattr(
    AutoExitPlanService,
    "_append_event",
    staticmethod(append_event),
  )
  scope = LockedExitPlanScope(
    position=liquidation_position(volume=500, available=500),
    plans=[first, second],
    target_plan=first,
  )

  result = await AutoExitPlanService()._reconcile_capacity_locked(
    SimpleNamespace(),
    account_id="account-a",
    instrument_code="600000.SH",
    locked_scope=scope,
  )

  assert result["ready"] is False
  assert result["protected_volume"] == 600
  assert [item.capacity_status for item in (first, second)] == [
    service_module.CAPACITY_RECONCILE_REQUIRED,
    service_module.CAPACITY_RECONCILE_REQUIRED,
  ]
  for record in (first, second):
    assert record.auto_exit_authorized is False
    assert record.auto_exit_authorization_fingerprint is None
    assert record.auto_exit_authorization_config_version is None
    assert record.auto_exit_authorization_challenge_id is None
    assert record.plan_state["template"]["auto_exit_authorized"] is False
  assert [item["event_type"] for item in events] == [
    "HOLDING_CAPACITY_RECONCILIATION_REQUIRED",
    "HOLDING_CAPACITY_RECONCILIATION_REQUIRED",
  ]


@pytest.mark.asyncio
async def test_buy_order_cost_basis_is_weighted_and_requires_coverage():
  orders = [
    SimpleNamespace(
      id=1,
      time=datetime(2026, 8, 20, 10, 0),
      traded_volume=100,
      traded_price=10.0,
    ),
    SimpleNamespace(
      id=2,
      time=datetime(2026, 8, 21, 10, 0),
      traded_volume=200,
      traded_price=11.0,
    ),
  ]
  result = await AutoExitPlanService._resolve_manual_cost_basis(
    FakeDb(orders),
    payload={
      "cost_basis": {
        "mode": "BROKER_BUY_ORDERS",
        "order_ids": ["1", "2"],
      }
    },
    account_id="account-a",
    instrument_code="600000.SH",
    requested_volume=300,
  )

  assert result.mode == ExitCostBasisMode.BROKER_BUY_ORDERS
  assert result.basis_volume == 300
  assert result.unit_cost_cny == pytest.approx(
    sum(
      item.traded_price * item.traded_volume + item.estimated_buy_fee_cny
      for item in result.selected_orders
    )
    / 300
  )
  assert [item.order_id for item in result.selected_orders] == ["1", "2"]

  with pytest.raises(ValueError, match="少于计划卖出"):
    await AutoExitPlanService._resolve_manual_cost_basis(
      FakeDb(orders),
      payload={
        "cost_basis": {
          "mode": "BROKER_BUY_ORDERS",
          "order_ids": ["1", "2"],
        }
      },
      account_id="account-a",
      instrument_code="600000.SH",
      requested_volume=400,
    )


@pytest.mark.asyncio
async def test_buy_order_cost_basis_cannot_be_claimed_by_two_active_plans():
  claimed = active_record(plan_id="claimed-plan", volume=100)
  claimed.cost_basis_snapshot = {
    "mode": "BROKER_BUY_ORDERS",
    "selected_orders": [{"order_id": "1"}],
  }
  order = SimpleNamespace(
    id=1,
    time=datetime(2026, 8, 20, 10, 0),
    traded_volume=100,
    traded_price=10.0,
  )

  with pytest.raises(ValueError, match="已被其他有效卖出计划"):
    await AutoExitPlanService._resolve_manual_cost_basis(
      FakeDb([order]),
      payload={
        "cost_basis": {
          "mode": "BROKER_BUY_ORDERS",
          "order_ids": ["1"],
        }
      },
      account_id="account-a",
      instrument_code="600000.SH",
      requested_volume=100,
      reserving_plans=[claimed],
    )


@pytest.mark.asyncio
async def test_cost_basis_candidates_hide_orders_claimed_by_active_plans(
  monkeypatch,
):
  claimed = active_record(plan_id="claimed-plan", volume=100)
  claimed.cost_basis_snapshot = {
    "mode": "BROKER_BUY_ORDERS",
    "selected_orders": [{"order_id": "1"}],
  }
  orders = [
    SimpleNamespace(
      id=1,
      time=datetime(2026, 8, 20, 10, 0),
      traded_volume=100,
      traded_price=10.0,
      strategy_name="first",
      remark=None,
    ),
    SimpleNamespace(
      id=2,
      time=datetime(2026, 8, 21, 10, 0),
      traded_volume=100,
      traded_price=11.0,
      strategy_name="second",
      remark=None,
    ),
  ]
  session = FakeSession(orders)
  monkeypatch.setattr(service_module, "AsyncSessionLocal", lambda: session)
  monkeypatch.setattr(
    service_module,
    "AutoExitPlanRepository",
    lambda db: FakePlanRepository(db, [claimed]),
  )

  result = await AutoExitPlanService().list_cost_basis_candidates(
    account_id="account-a",
    instrument_code="600000.SH",
  )

  assert [item["order_id"] for item in result] == ["2"]


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


def strategy_exit_template(
  *,
  config_version: int = 1,
  auto_exit_authorized: bool = True,
) -> ExitPlanTemplate:
  return ExitPlanTemplate(
    plan_id="entry:managed-plan:slice:stage-1",
    source_type="ENTRY_PLAN",
    source_id="stage-1",
    account_id="account-a",
    instrument_code="600000.SH",
    bucket="core",
    run_id="managed-plan",
    config_version=config_version,
    auto_exit_authorized=auto_exit_authorized,
    rules=[
      ExitRuleSpec(
        rule_id="stage-1:target",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 20},
      )
    ],
  )


def strategy_exit_book(
  *fills: tuple[int, float],
  config_version: int = 1,
) -> ExitPlanBook:
  book = ExitPlanBook()
  template = strategy_exit_template(
    config_version=config_version,
    auto_exit_authorized=False,
  )
  for volume, price in fills:
    book.register_entry_fill(template, volume=volume, price=price)
  return book


def strategy_exit_record(
  plan: ExitPlan,
  *,
  config_version: int = 1,
  enabled: bool = True,
  authorized: bool = True,
) -> AutoExitPlanRecord:
  if authorized:
    plan.template = strategy_exit_template(
      config_version=config_version,
      auto_exit_authorized=True,
    )
  now = datetime.now()
  return AutoExitPlanRecord(
    plan_id=plan.plan_id,
    account_id=plan.template.account_id,
    instrument_code=plan.template.instrument_code,
    bucket=plan.template.bucket,
    source_type=plan.template.source_type,
    source_id=plan.template.source_id,
    strategy_run_id="managed-plan",
    enabled=enabled,
    status=plan.status.value,
    execution_mode="live",
    auto_exit_authorized=authorized,
    auto_exit_authorization_fingerprint="fingerprint" if authorized else None,
    auto_exit_authorization_config_version=(config_version if authorized else None),
    auto_exit_authorized_at=now if authorized else None,
    auto_exit_authorization_expires_at=(
      now + timedelta(minutes=5) if authorized else None
    ),
    auto_exit_authorization_challenge_id=("challenge-1" if authorized else None),
    auto_exit_authorization_user_id="user-1" if authorized else None,
    auto_exit_authorization_device_session_id=(
      "device-session-1" if authorized else None
    ),
    config_version=config_version,
    protected_volume=plan.entry_filled_volume,
    exited_volume=plan.exited_volume,
    remaining_volume=plan.remaining_volume,
    entry_avg_price=plan.entry_avg_price,
    plan_state=plan.to_dict(),
    pending_client_order_id=plan.pending_order_id or None,
  )


def install_strategy_sync_fakes(monkeypatch, record):
  class Session:
    def __init__(self):
      self.added = []
      self.commits = 0

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    def add(self, value):
      self.added.append(value)

    async def commit(self):
      self.commits += 1

  session = Session()
  lock_requests = []

  class Repository:
    def __init__(self, _db):
      pass

    async def find_by_id(self, plan_id, *, for_update=False):
      lock_requests.append((plan_id, for_update))
      return record

  events = []

  async def append_event(_db, **kwargs):
    events.append(dict(kwargs))

  monkeypatch.setattr(service_module, "AsyncSessionLocal", lambda: session)
  monkeypatch.setattr(service_module, "AutoExitPlanRepository", Repository)
  monkeypatch.setattr(
    AutoExitPlanService,
    "_append_event",
    staticmethod(append_event),
  )
  return session, lock_requests, events


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
  monkeypatch.setattr(
    service_module,
    "lock_exit_plan_scope_for_plan",
    AsyncMock(return_value=locked_scope_for(record)),
  )
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


@pytest.mark.asyncio
async def test_capacity_shortfall_blocks_evaluation_before_submission(
  monkeypatch,
):
  record = active_record(plan_id="capacity-evaluate", volume=300)
  other = active_record(plan_id="capacity-other", volume=300)
  scope = locked_scope_for(
    record,
    position=liquidation_position(volume=500, available=500),
    plans=[record, other],
  )

  class Session:
    committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def commit(self):
      self.committed = True

  session = Session()
  submit = AsyncMock()
  monkeypatch.setattr(service_module, "AsyncSessionLocal", lambda: session)
  monkeypatch.setattr(
    service_module,
    "lock_exit_plan_scope_for_plan",
    AsyncMock(return_value=scope),
  )
  monkeypatch.setattr(AutoExitPlanService, "_submit_decision", submit)
  monkeypatch.setattr(AutoExitPlanService, "_append_event", AsyncMock())

  result = await AutoExitPlanService().evaluate_and_submit(
    plan_id=record.plan_id,
    context=ExitEvaluationContext(
      timestamp=datetime.now(),
      current_price=21.0,
      market_data_age_seconds=0.0,
      source="QMT_WHOLE_QUOTE",
    ),
    position=scope.position,
    market_ready=lambda: True,
  )

  assert result is None
  assert session.committed
  assert record.capacity_status == service_module.CAPACITY_RECONCILE_REQUIRED
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
    "lock_exit_plan_scope_for_plan",
    AsyncMock(return_value=locked_scope_for(record)),
  )
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
  assert intent.intent_metadata["market_data_gate"] == ("MARKET_DATA_STREAM_NOT_READY")
  assert record.plan_state["pending_intent_id"] == ""
  assert session.committed
  processor.process_approved_exit_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_capacity_shortfall_blocks_manual_confirmation_before_processor(
  monkeypatch,
):
  plan = pending_plan()
  record = active_record(plan_id=plan.plan_id, volume=300)
  record.plan_state = plan.to_dict()
  record.status = plan.status.value
  other = active_record(plan_id="capacity-confirm-other", volume=300)
  intent = SimpleNamespace(
    id="intent-1",
    status="AWAITING_APPROVAL",
    notes=None,
    intent_metadata={},
  )
  scope = locked_scope_for(
    record,
    position=liquidation_position(volume=500, available=500),
    plans=[record, other],
  )

  class Session:
    committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, _model, key):
      return intent if key == intent.id else None

    async def commit(self):
      self.committed = True

  session = Session()
  processor = SimpleNamespace(process_approved_exit_intent=AsyncMock())
  monkeypatch.setattr(service_module, "AsyncSessionLocal", lambda: session)
  monkeypatch.setattr(
    service_module,
    "lock_exit_plan_scope_for_plan",
    AsyncMock(return_value=scope),
  )
  monkeypatch.setattr(service_module, "TradeIntentProcessor", lambda: processor)
  monkeypatch.setattr(AutoExitPlanService, "_append_event", AsyncMock())

  with pytest.raises(ValueError, match="持仓少于计划认领数量"):
    await AutoExitPlanService().confirm_exit_intent(
      plan_id=record.plan_id,
      intent_id=intent.id,
      context=ExitEvaluationContext(
        timestamp=datetime.now(),
        current_price=10.0,
        market_data_age_seconds=0.0,
        source="QMT_WHOLE_QUOTE",
      ),
      position=scope.position,
      market_ready=lambda: True,
    )

  assert session.committed
  assert record.capacity_status == service_module.CAPACITY_RECONCILE_REQUIRED
  processor.process_approved_exit_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_strategy_plan_sync_expands_same_version_cumulative_fill(
  monkeypatch,
):
  initial_plan = next(iter(strategy_exit_book((100, 10)).plans.values()))
  record = strategy_exit_record(initial_plan)
  _session, lock_requests, events = install_strategy_sync_fakes(
    monkeypatch,
    record,
  )
  incoming = strategy_exit_book((100, 10), (100, 12))

  synced = await AutoExitPlanService().sync_strategy_plan_book(
    strategy_run_id="managed-plan",
    book_state=incoming.to_dict(),
    execution_mode="live",
  )

  stored = ExitPlan.from_dict(record.plan_state)
  assert synced == 1
  assert lock_requests == [(record.plan_id, True)]
  assert record.config_version == 1
  assert record.protected_volume == 200
  assert record.remaining_volume == 200
  assert record.entry_avg_price == pytest.approx(11)
  assert stored.entry_filled_volume == 200
  assert stored.entry_avg_price == pytest.approx(11)
  assert not stored.template.auto_exit_authorized
  assert record.auto_exit_authorized is False
  assert record.auto_exit_authorization_fingerprint is None
  assert record.auto_exit_authorization_config_version is None
  assert record.auto_exit_authorization_challenge_id is None
  assert events == [
    {
      "business_key": (f"strategy-plan-entry-expanded:{record.plan_id}:1:200"),
      "plan_id": record.plan_id,
      "event_type": "STRATEGY_PLAN_ENTRY_EXPANDED",
      "payload": {
        "strategy_run_id": "managed-plan",
        "source_type": "ENTRY_PLAN",
        "config_version": 1,
        "incoming_template_version": 1,
        "previous_entry_filled_volume": 100,
        "entry_filled_volume": 200,
        "previous_entry_avg_price": 10,
        "entry_avg_price": pytest.approx(11),
        "reactivated_from_completed": False,
        "status": "ACTIVE",
        "monitor_enabled": True,
        "unprotected_terminal": False,
      },
    }
  ]


@pytest.mark.asyncio
async def test_strategy_plan_sync_replay_and_smaller_snapshot_are_noops(
  monkeypatch,
):
  cumulative = strategy_exit_book((100, 10), (100, 12))
  record = strategy_exit_record(next(iter(cumulative.plans.values())))
  _session, lock_requests, events = install_strategy_sync_fakes(
    monkeypatch,
    record,
  )

  replayed = await AutoExitPlanService().sync_strategy_plan_book(
    strategy_run_id="managed-plan",
    book_state=cumulative.to_dict(),
    execution_mode="live",
  )
  stale = await AutoExitPlanService().sync_strategy_plan_book(
    strategy_run_id="managed-plan",
    book_state=strategy_exit_book((100, 10)).to_dict(),
    execution_mode="live",
  )

  assert replayed == 0
  assert stale == 0
  assert lock_requests == [(record.plan_id, True), (record.plan_id, True)]
  assert events == []
  assert record.protected_volume == 200
  assert record.entry_avg_price == pytest.approx(11)
  assert record.auto_exit_authorized is True
  assert record.auto_exit_authorization_fingerprint == "fingerprint"


@pytest.mark.asyncio
async def test_strategy_plan_sync_old_template_expands_newer_persistent_policy(
  monkeypatch,
):
  initial = strategy_exit_book((100, 10), config_version=2)
  record = strategy_exit_record(
    next(iter(initial.plans.values())),
    config_version=2,
  )
  _session, _lock_requests, events = install_strategy_sync_fakes(
    monkeypatch,
    record,
  )

  synced = await AutoExitPlanService().sync_strategy_plan_book(
    strategy_run_id="managed-plan",
    book_state=strategy_exit_book(
      (100, 10),
      (100, 12),
      config_version=1,
    ).to_dict(),
    execution_mode="live",
  )

  stored = ExitPlan.from_dict(record.plan_state)
  assert synced == 1
  assert record.config_version == 2
  assert stored.template.config_version == 2
  assert stored.entry_filled_volume == 200
  assert stored.entry_avg_price == pytest.approx(11)
  assert record.auto_exit_authorized is False
  assert events[0]["payload"]["config_version"] == 2
  assert events[0]["payload"]["incoming_template_version"] == 1


@pytest.mark.asyncio
async def test_strategy_plan_sync_preserves_exit_and_pending_runtime_facts(
  monkeypatch,
):
  plan = next(iter(strategy_exit_book((100, 10)).plans.values()))
  plan.exited_volume = 40
  plan.exit_avg_price = 13
  plan.status = ExitPlanStatus.EXIT_PENDING
  plan.peak_price = 15
  plan.trailing_floor_pct = 4.5
  plan.pending_intent_id = "exit-intent-1"
  plan.pending_order_id = "exit-order-1"
  plan.pending_rule_id = "stage-1:target"
  plan.pending_requested_volume = 60
  plan.pending_filled_volume = 20
  plan.pending_order_terminal = False
  plan.rule_state = {"stage-1:target": {"armed": True}}
  plan.rule_filled_volumes = {"stage-1:target": 40}
  record = strategy_exit_record(plan)
  record.last_error = "keep-me"
  _session, _lock_requests, _events = install_strategy_sync_fakes(
    monkeypatch,
    record,
  )

  synced = await AutoExitPlanService().sync_strategy_plan_book(
    strategy_run_id="managed-plan",
    book_state=strategy_exit_book((100, 10), (100, 12)).to_dict(),
    execution_mode="live",
  )

  stored = ExitPlan.from_dict(record.plan_state)
  assert synced == 1
  assert stored.entry_filled_volume == 200
  assert stored.exited_volume == 40
  assert stored.exit_avg_price == 13
  assert stored.remaining_volume == 160
  assert stored.status == ExitPlanStatus.EXIT_PENDING
  assert stored.peak_price == 15
  assert stored.trailing_floor_pct == 4.5
  assert stored.pending_intent_id == "exit-intent-1"
  assert stored.pending_order_id == "exit-order-1"
  assert stored.pending_rule_id == "stage-1:target"
  assert stored.pending_requested_volume == 60
  assert stored.pending_filled_volume == 20
  assert stored.rule_state == {"stage-1:target": {"armed": True}}
  assert stored.rule_filled_volumes == {"stage-1:target": 40}
  assert record.pending_client_order_id == "exit-order-1"
  assert record.last_error == "keep-me"


@pytest.mark.asyncio
async def test_strategy_plan_sync_late_fill_reopens_completed_plan(monkeypatch):
  plan = next(iter(strategy_exit_book((100, 10)).plans.values()))
  plan.exited_volume = 100
  plan.exit_avg_price = 13
  plan.status = ExitPlanStatus.COMPLETED
  record = strategy_exit_record(plan, enabled=False)
  _session, _lock_requests, events = install_strategy_sync_fakes(
    monkeypatch,
    record,
  )

  incoming = strategy_exit_book((100, 10), (100, 12))
  next(iter(incoming.plans.values())).status = ExitPlanStatus.COMPLETED
  synced = await AutoExitPlanService().sync_strategy_plan_book(
    strategy_run_id="managed-plan",
    book_state=incoming.to_dict(),
    execution_mode="live",
  )

  stored = ExitPlan.from_dict(record.plan_state)
  assert synced == 1
  assert stored.entry_filled_volume == 200
  assert stored.exited_volume == 100
  assert stored.remaining_volume == 100
  assert stored.status == ExitPlanStatus.PARTIALLY_EXITED
  assert record.enabled is True
  assert events[0]["payload"]["reactivated_from_completed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "status",
  [ExitPlanStatus.PAUSED, ExitPlanStatus.CANCELLED],
)
async def test_strategy_plan_sync_late_fill_preserves_user_terminal_intent(
  monkeypatch,
  status,
):
  plan = next(iter(strategy_exit_book((100, 10)).plans.values()))
  plan.status = status
  record = strategy_exit_record(plan, enabled=False)
  _session, _lock_requests, events = install_strategy_sync_fakes(
    monkeypatch,
    record,
  )

  incoming = strategy_exit_book((100, 10), (100, 12))
  next(iter(incoming.plans.values())).status = status
  synced = await AutoExitPlanService().sync_strategy_plan_book(
    strategy_run_id="managed-plan",
    book_state=incoming.to_dict(),
    execution_mode="live",
  )

  stored = ExitPlan.from_dict(record.plan_state)
  assert synced == 1
  assert stored.entry_filled_volume == 200
  assert stored.status == status
  assert record.enabled is False
  assert events[0]["payload"]["monitor_enabled"] is False
  assert events[0]["payload"]["unprotected_terminal"] is (
    status == ExitPlanStatus.CANCELLED
  )


@pytest.mark.asyncio
async def test_strategy_plan_sync_rejects_cross_binding_snapshot(monkeypatch):
  initial_plan = next(iter(strategy_exit_book((100, 10)).plans.values()))
  record = strategy_exit_record(initial_plan)
  record.instrument_code = "000001.SZ"
  _session, _lock_requests, events = install_strategy_sync_fakes(
    monkeypatch,
    record,
  )

  with pytest.raises(ValueError, match="instrument_code binding mismatch"):
    await AutoExitPlanService().sync_strategy_plan_book(
      strategy_run_id="managed-plan",
      book_state=strategy_exit_book((100, 10), (100, 12)).to_dict(),
      execution_mode="live",
    )

  assert record.protected_volume == 100
  assert record.auto_exit_authorized is True
  assert events == []
