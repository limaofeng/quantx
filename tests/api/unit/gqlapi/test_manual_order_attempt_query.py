from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow
from quantx_api.gqlapi.schemas import trading_schema
from quantx_api.gqlapi.schemas.trading_schema import TradingQuery
from quantx_api.gqlapi.types.trading_types import (
  ManualOrderAttemptPhase,
  ManualOrderExecutionMode,
  ManualOrderSide,
)


def _info() -> SimpleNamespace:
  return SimpleNamespace(
    context={
      "principal": Principal(
        user_id="user-1",
        username="operator",
        display_name="Operator",
        device_session_id="session-1",
        access_token_expires_at=utcnow() + timedelta(minutes=5),
        permissions=frozenset({"orders:read"}),
        authorized_account_ids=("ACCOUNT-1",),
        is_native_session=True,
      )
    }
  )


def _pending(
  *,
  client_order_id: str = "client-order-1",
  status: str = "QUEUED",
  broker_order_id: str | None = None,
  status_reason: str | None = None,
  created_at: datetime | None = None,
  updated_at: datetime | None = None,
):
  created = created_at or datetime(2026, 9, 1, 11, 6, 43)
  return SimpleNamespace(
    account_id="ACCOUNT-1",
    broker_order_id=broker_order_id,
    bucket="manual",
    client_order_id=client_order_id,
    created_at=created,
    environment="LIVE",
    instrument_code="688577.SH",
    limit_price="12.34",
    order_type="FIX_PRICE",
    side="SELL",
    status=status,
    status_reason=status_reason,
    updated_at=updated_at or created,
    user_id="user-1",
    volume=400,
  )


def _outbox(
  *,
  delivery_status: str = "QUEUED",
  last_error: str | None = None,
):
  return SimpleNamespace(
    acknowledged_at=None,
    delivered_at=None,
    delivery_status=delivery_status,
    expires_at=datetime(2026, 9, 1, 11, 8, 43),
    last_error=last_error,
    updated_at=datetime(2026, 9, 1, 11, 6, 43),
  )


class _Result:
  def __init__(
    self,
    *,
    scalar: int | None = None,
    rows: list[tuple[object, ...]] | None = None,
  ):
    self.scalar = scalar
    self.rows = rows or []

  def scalar_one(self):
    return self.scalar

  def one(self):
    return self.rows[0]

  def all(self):
    return self.rows


class _SessionContext:
  results: list[_Result] = []

  async def __aenter__(self):
    return SimpleNamespace(execute=AsyncMock(side_effect=self.results))

  async def __aexit__(self, exc_type, exc, traceback):
    return False


def test_manual_order_projection_exposes_agent_rejection_without_broker_order():
  pending = _pending(status="REJECTED", status_reason="stale live quote")
  outbox = _outbox(delivery_status="REJECTED", last_error="stale live quote")
  projection = trading_schema._project_manual_order_attempt(pending, outbox)
  result = trading_schema._manual_order_attempt_from_rows(pending, outbox)

  assert projection.phase == ManualOrderAttemptPhase.REJECTED_BEFORE_BROKER
  assert not projection.active
  assert result.broker_order_id is None
  assert result.status == "REJECTED"
  assert result.delivery_status == "REJECTED"
  assert result.side == ManualOrderSide.SELL
  assert result.execution_mode == ManualOrderExecutionMode.LIVE
  assert result.message == "QMT Agent 下单前行情已超过 30 秒，未向券商提交"


@pytest.mark.parametrize(
  ("pending_status", "delivery_status", "broker_order_id", "expected_phase"),
  [
    ("QUEUED", "QUEUED", None, ManualOrderAttemptPhase.QUEUED),
    ("QUEUED", "DELIVERED", None, ManualOrderAttemptPhase.DELIVERED),
    (
      "QUEUED",
      "ACKNOWLEDGED",
      None,
      ManualOrderAttemptPhase.AGENT_ACKNOWLEDGED,
    ),
    (
      "SUBMITTED",
      "ACKNOWLEDGED",
      "broker-123",
      ManualOrderAttemptPhase.BROKER_ORDER_CREATED,
    ),
    (
      "REJECTED",
      "REJECTED",
      None,
      ManualOrderAttemptPhase.REJECTED_BEFORE_BROKER,
    ),
    (
      "EXPIRED",
      "EXPIRED",
      None,
      ManualOrderAttemptPhase.EXPIRED_BEFORE_BROKER,
    ),
    (
      "CANCELLED",
      "CANCELLED",
      None,
      ManualOrderAttemptPhase.CANCELLED_BEFORE_BROKER,
    ),
    (
      "CANCELED",
      "CANCELLED",
      None,
      ManualOrderAttemptPhase.CANCELLED_BEFORE_BROKER,
    ),
    (
      "RECONCILE_REQUIRED",
      "ACKNOWLEDGED",
      None,
      ManualOrderAttemptPhase.RECONCILE_REQUIRED,
    ),
    ("UNKNOWN", "QUEUED", None, ManualOrderAttemptPhase.RECONCILE_REQUIRED),
  ],
)
def test_manual_order_projection_covers_known_and_unknown_phase_inputs(
  pending_status: str,
  delivery_status: str,
  broker_order_id: str | None,
  expected_phase: ManualOrderAttemptPhase,
):
  result = trading_schema._manual_order_attempt_from_rows(
    _pending(status=pending_status, broker_order_id=broker_order_id),
    _outbox(delivery_status=delivery_status),
  )

  assert result.phase == expected_phase
  assert result.broker_order_id == (
    broker_order_id
    if expected_phase == ManualOrderAttemptPhase.BROKER_ORDER_CREATED
    else None
  )


def test_manual_order_projection_never_treats_ack_as_broker_acceptance():
  result = trading_schema._manual_order_attempt_from_rows(
    _pending(status="QUEUED"),
    _outbox(delivery_status="ACKNOWLEDGED"),
  )

  assert result.phase == ManualOrderAttemptPhase.AGENT_ACKNOWLEDGED
  assert result.active
  assert result.broker_order_id is None
  assert "券商委托回报" in result.message


def test_manual_order_projection_fails_closed_for_mismatched_terminal_rows():
  result = trading_schema._manual_order_attempt_from_rows(
    _pending(status="REJECTED"),
    _outbox(delivery_status="QUEUED"),
  )

  assert result.phase == ManualOrderAttemptPhase.RECONCILE_REQUIRED
  assert result.active
  assert result.requires_attention
  assert result.broker_order_id is None
  assert "禁止重复下单" in result.message


def test_manual_order_projection_does_not_claim_filled_without_broker_evidence():
  result = trading_schema._manual_order_attempt_from_rows(
    _pending(status="FILLED"),
    _outbox(delivery_status="ACKNOWLEDGED"),
  )

  assert result.phase == ManualOrderAttemptPhase.RECONCILE_REQUIRED
  assert result.broker_order_id is None
  assert result.requires_attention


def test_manual_order_attempt_sort_keeps_reconciliation_before_terminal_rows():
  mismatch = (
    _pending(client_order_id="client-mismatch", status="REJECTED"),
    _outbox(delivery_status="EXPIRED"),
  )
  terminal = (
    _pending(client_order_id="client-terminal", status="REJECTED"),
    _outbox(delivery_status="REJECTED"),
  )

  ordered = sorted(
    [terminal, mismatch],
    key=lambda row: trading_schema._manual_order_attempt_sort_key(*row),
  )

  assert [pending.client_order_id for pending, _ in ordered] == [
    "client-mismatch",
    "client-terminal",
  ]


@pytest.mark.asyncio
async def test_manual_order_attempts_rejects_invalid_limits():
  with pytest.raises(ValueError, match="limit"):
    await TradingQuery().manual_order_attempts(_info(), limit=0)


@pytest.mark.asyncio
async def test_manual_order_attempts_returns_bounded_feed_and_broker_association(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  broker_pending = _pending(
    client_order_id="client-order-2",
    status="SUBMITTED",
    broker_order_id="broker-123",
  )
  queued_pending = _pending(
    client_order_id="client-order-3",
    status="QUEUED",
    created_at=datetime(2026, 9, 1, 13, 5, 0),
  )
  _SessionContext.results = [
    _Result(scalar=2),
    _Result(
      rows=[
        (broker_pending, _outbox(delivery_status="ACKNOWLEDGED")),
        (queued_pending, _outbox(delivery_status="QUEUED")),
      ]
    ),
  ]
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)

  result = await TradingQuery().manual_order_attempts(
    _info(),
    account_id="ACCOUNT-1",
    limit=2,
  )

  assert result.total_count == 2
  assert not result.truncated
  assert result.active_count == 1
  assert result.requires_attention_count == 0
  assert [item.client_order_id for item in result.items] == [
    "client-order-3",
    "client-order-2",
  ]
  broker_item = result.items[1]
  assert broker_item.phase == ManualOrderAttemptPhase.BROKER_ORDER_CREATED
  assert broker_item.broker_order_id == "broker-123"
  assert broker_item.message == "券商委托已生成：broker-123；最终状态以券商回报为准"


@pytest.mark.asyncio
async def test_manual_order_attempts_uses_aggregate_counts_for_truncated_feed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  queued_pending = _pending(
    client_order_id="client-order-queued",
    created_at=datetime(2026, 9, 1, 13, 5, 0),
  )
  terminal_pending = _pending(
    client_order_id="client-order-terminal",
    status="REJECTED",
  )
  _SessionContext.results = [
    _Result(scalar=3),
    _Result(
      rows=[
        (queued_pending, _outbox(delivery_status="QUEUED")),
        (terminal_pending, _outbox(delivery_status="REJECTED")),
      ]
    ),
    _Result(rows=[(2, 1)]),
  ]
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)

  result = await TradingQuery().manual_order_attempts(
    _info(),
    account_id="ACCOUNT-1",
    limit=2,
  )

  assert result.truncated
  assert result.active_count == 2
  assert result.requires_attention_count == 1
