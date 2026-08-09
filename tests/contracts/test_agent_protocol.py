from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from quantx_contracts import (
  PROTOCOL_VERSION,
  AccountSnapshotPayload,
  AgentEnvelope,
  AgentMessageType,
  CancelCommandPayload,
  OrderReportPayload,
  ReconciliationResultPayload,
  TradeCommandPayload,
  can_transition_order_status,
)


def test_protocol_rejects_unknown_version() -> None:
  with pytest.raises(ValidationError):
    AgentEnvelope(
      protocol_version="999",
      message_type=AgentMessageType.HEARTBEAT,
    )


def test_protocol_rejects_timezone_naive_sent_at() -> None:
  with pytest.raises(ValidationError, match="sent_at must be timezone-aware"):
    AgentEnvelope(
      message_type=AgentMessageType.HEARTBEAT,
      sent_at=datetime.now(),
    )


def test_trade_command_requires_timezone_aware_expiry() -> None:
  with pytest.raises(ValidationError):
    TradeCommandPayload(
      client_order_id="client-1",
      instance_id="strategy-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      limit_price="10.50",
      volume=100,
      bucket="swing",
      risk_decision_id="risk-1",
      trace_id="trace-1",
      expires_at=datetime.now(),
    )


def test_cancel_command_round_trip() -> None:
  payload = CancelCommandPayload(
    client_order_id="cancel-1",
    account_id="account-1",
    broker_order_id="123",
    trace_id="trace-1",
    expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
  )
  envelope = AgentEnvelope(
    message_type=AgentMessageType.CANCEL_COMMAND,
    payload=payload.model_dump(mode="json"),
  )
  restored = AgentEnvelope.model_validate_json(envelope.model_dump_json())
  assert restored.message_id == envelope.message_id
  assert restored.message_type is AgentMessageType.CANCEL_COMMAND


def test_protocol_11_order_report_is_strongly_typed() -> None:
  envelope = AgentEnvelope(
    protocol_version=PROTOCOL_VERSION,
    message_type=AgentMessageType.ORDER_REPORT,
    payload={
      "account_id": "account-1",
      "client_order_id": "client-1",
      "source_sequence": 42,
      "source_event_at": datetime.now(timezone.utc).isoformat(),
      "order": {
        "account_id": "account-1",
        "broker_order_id": "broker-1",
        "stock_code": "600000.SH",
        "status": "SUBMITTED",
      },
    },
  )

  typed = envelope.validate_payload()

  assert isinstance(typed, OrderReportPayload)
  assert typed.source_sequence == 42
  assert typed.order.broker_order_id == "broker-1"


def test_complete_snapshot_requires_durable_identity() -> None:
  with pytest.raises(ValidationError, match="complete snapshot requires"):
    AccountSnapshotPayload(is_complete=True)

  snapshot = AccountSnapshotPayload(
    account_id="account-1",
    snapshot_id="snapshot-1",
    snapshot_hash="a" * 64,
    is_complete=True,
  )
  assert snapshot.snapshot_hash == "a" * 64


def test_reconciliation_result_requires_snapshot_proof() -> None:
  result = ReconciliationResultPayload(
    account_id="account-1",
    snapshot_id="snapshot-1",
    snapshot_hash="b" * 64,
    reconciled_at=datetime.now(timezone.utc),
    ready=False,
    discrepancies=[{"kind": "cash", "expected": 1, "actual": 0}],
  )

  assert not result.ready
  assert result.discrepancies[0]["kind"] == "cash"


@pytest.mark.parametrize(
  ("terminal", "late_status"),
  [
    ("FILLED", "SUBMITTED"),
    ("CANCELLED", "PENDING"),
    ("REJECTED", "PARTIAL_FILLED"),
    ("EXPIRED", "QUEUED"),
    ("KILL_SWITCHED", "SUBMITTED"),
  ],
)
def test_terminal_order_status_never_regresses(
  terminal: str,
  late_status: str,
) -> None:
  assert not can_transition_order_status(terminal, late_status)
