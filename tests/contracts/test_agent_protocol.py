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
  QmtAgentControlConnectionStatus,
  QmtAgentDependencyStatus,
  QmtAgentHealthReason,
  QmtAgentHealthSnapshot,
  QmtAgentHealthStatus,
  QmtAgentLiveResponse,
  QmtAgentMarketStreamStatus,
  QmtAgentMode,
  QmtAgentReconciliationStatus,
  ReconciliationResultPayload,
  TradeCommandPayload,
  can_transition_order_status,
)


def _ready_agent_health(**overrides) -> QmtAgentHealthSnapshot:
  values = {
    "status": QmtAgentHealthStatus.READY,
    "reason_code": None,
    "agent_version": "0.1.0",
    "protocol_version": "1.1",
    "mode": QmtAgentMode.LIVE,
    "uptime_seconds": 1234.5,
    "control_connection_status": QmtAgentControlConnectionStatus.CONNECTED,
    "reconciliation_status": QmtAgentReconciliationStatus.READY,
    "xtdata_status": QmtAgentDependencyStatus.CONNECTED,
    "xttrading_status": QmtAgentDependencyStatus.CONNECTED,
    "market_stream_status": QmtAgentMarketStreamStatus.READY,
    "observed_at": datetime.now(timezone.utc),
  }
  values.update(overrides)
  return QmtAgentHealthSnapshot(**values)


def test_qmt_agent_health_v1_round_trip_is_sanitized() -> None:
  snapshot = _ready_agent_health()
  restored = QmtAgentHealthSnapshot.model_validate_json(snapshot.model_dump_json())

  assert restored == snapshot
  assert restored.schema_version == 1
  serialized = snapshot.model_dump_json().lower()
  for forbidden in (
    "account",
    "credential",
    "device",
    "remote_address",
    "userdata",
    "exception",
    "traceback",
  ):
    assert forbidden not in serialized


def test_qmt_agent_live_response_has_only_fixed_fields() -> None:
  response = QmtAgentLiveResponse(observed_at=datetime.now(timezone.utc))

  assert set(response.model_dump()) == {
    "schema_version",
    "status",
    "component",
    "observed_at",
  }


@pytest.mark.parametrize(
  "overrides",
  [
    {"schema_version": 2},
    {"status": "invalid"},
    {"uptime_seconds": -1},
    {"uptime_seconds": float("nan")},
    {"observed_at": datetime.now()},
    {"status": "ready", "reason_code": "MARKET_STREAM_NOT_READY"},
    {"status": "degraded", "reason_code": None},
    {
      "status": "unavailable",
      "reason_code": "XTDATA_UNAVAILABLE",
      "xtdata_status": "connected",
    },
    {"xtdata_status": "unknown"},
  ],
)
def test_qmt_agent_health_rejects_invalid_contract(overrides) -> None:
  with pytest.raises(ValidationError):
    _ready_agent_health(**overrides)


def test_qmt_agent_health_applies_mode_specific_trading_requirement() -> None:
  data_only = _ready_agent_health(
    mode=QmtAgentMode.DATA_ONLY,
    xttrading_status=QmtAgentDependencyStatus.DISABLED,
  )
  assert data_only.xttrading_status is QmtAgentDependencyStatus.DISABLED

  with pytest.raises(ValidationError, match="live health snapshot cannot disable"):
    _ready_agent_health(xttrading_status=QmtAgentDependencyStatus.DISABLED)

  degraded = _ready_agent_health(
    status=QmtAgentHealthStatus.DEGRADED,
    reason_code=QmtAgentHealthReason.TRADING_RECONCILING,
    reconciliation_status=QmtAgentReconciliationStatus.RECONCILING,
  )
  assert degraded.reason_code is QmtAgentHealthReason.TRADING_RECONCILING


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
        "order_status": 50,
        "effective_order_status": "EXPIRED",
        "can_cancel": False,
        "session_expired": True,
        "effective_status_reason": "MARKET_SESSION_CLOSED",
        "order_session_date": "2026-08-13",
      },
    },
  )

  typed = envelope.validate_payload()

  assert isinstance(typed, OrderReportPayload)
  assert typed.source_sequence == 42
  assert typed.order.broker_order_id == "broker-1"
  assert typed.order.order_status == 50
  assert typed.order.effective_order_status == "EXPIRED"
  assert typed.order.can_cancel is False
  assert typed.order.session_expired is True
  assert typed.order.effective_status_reason == "MARKET_SESSION_CLOSED"


def test_complete_snapshot_requires_durable_identity() -> None:
  with pytest.raises(ValidationError, match="complete snapshot requires"):
    AccountSnapshotPayload(is_complete=True)

  snapshot = AccountSnapshotPayload(
    account_id="account-1",
    snapshot_id="snapshot-1",
    snapshot_hash="a" * 64,
    is_complete=True,
    accounts=[{"account_id": "account-1"}],
    positions_by_account={"account-1": []},
    section_completeness_by_account={
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
  )
  assert snapshot.snapshot_hash == "a" * 64


def test_complete_snapshot_requires_explicit_section_completeness() -> None:
  with pytest.raises(ValidationError, match="every account section"):
    AccountSnapshotPayload(
      account_id="account-1",
      snapshot_id="snapshot-1",
      snapshot_hash="a" * 64,
      is_complete=True,
      accounts=[{"account_id": "account-1"}],
      positions_by_account={"account-1": []},
    )


def test_complete_snapshot_requires_sha256_hash() -> None:
  with pytest.raises(ValidationError, match="requires id and hash"):
    AccountSnapshotPayload(
      snapshot_id="snapshot-1",
      snapshot_hash="a" * 16,
      is_complete=True,
    )


def test_complete_snapshot_requires_covered_account() -> None:
  with pytest.raises(ValidationError, match="covered account"):
    AccountSnapshotPayload(
      snapshot_id="snapshot-1",
      snapshot_hash="a" * 64,
      is_complete=True,
    )


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
