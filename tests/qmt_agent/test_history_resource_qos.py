"""Independent history resource health without importing Windows SDK modules."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from quantx_qmt_agent.runtime import AgentRuntime


@pytest.fixture
def runtime():
  value = AgentRuntime.__new__(AgentRuntime)
  value.mode = "live"
  value._control_session_authenticated = False
  value._market_stream_status = "READY"
  value._trading_account_waiting = False
  value._requires_trading_reconciliation = lambda: False
  value._is_trading_ready = lambda: True
  value._is_market_data_ready = lambda: True
  value._last_complete_account_snapshot_monotonic = time.monotonic()
  value.journal = SimpleNamespace(stats=lambda: {})
  return value


def test_control_authentication_and_heartbeat_do_not_define_resource_health(runtime):
  runtime._heartbeat_sent_monotonic = {"old": time.monotonic() - 100}
  assert runtime._history_resource_block_reason() == ""
  assert runtime._history_qos_block_reason() == "CONTROL_CONNECTION_UNHEALTHY"
  runtime._control_session_authenticated = True
  assert runtime._history_qos_block_reason() == "CONTROL_HEARTBEAT_DELAYED"


@pytest.mark.parametrize(
  "condition,reason",
  [
    ("command", "TRADE_COMMAND_PENDING"),
    ("report", "BROKER_REPORT_PENDING"),
    ("snapshot", "ACCOUNT_SNAPSHOT_STALE"),
    ("market", "MARKET_STREAM_NOT_READY"),
    ("xtdata", "XTDATA_UNSTABLE"),
    ("reconcile", "TRADING_RECONCILING"),
  ],
)
def test_independent_resource_health_preserves_live_protection(
  runtime, condition, reason
):
  if condition == "command":
    runtime._command_requests = asyncio.Queue()
    runtime._command_requests.put_nowait(object())
  elif condition == "report":
    runtime.journal.stats = lambda: {"pending_reports": 1}
  elif condition == "snapshot":
    runtime._last_complete_account_snapshot_monotonic = 0
  elif condition == "market":
    runtime._market_stream_status = "OFFLINE"
  elif condition == "xtdata":
    runtime._is_market_data_ready = lambda: False
  else:
    runtime._requires_trading_reconciliation = lambda: True
  assert runtime._history_resource_block_reason() == reason
