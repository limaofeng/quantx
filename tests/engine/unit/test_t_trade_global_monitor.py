"""Engine-owned account-level T-trade monitor tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine.t_trade_global_monitor import (
  TTradeGlobalMonitorService,
)


def test_snapshot_sequence_is_exposed_without_graphql_int_overflow():
  sequence = 1784082686848005
  data = TTradeGlobalMonitorService._snapshot_data({"sequence": sequence})
  assert data["position_snapshot_sequence"] == str(sequence)


def position(
  stock_code: str,
  *,
  volume: int = 1000,
  available: int = 1000,
  name: str = "测试股票",
):
  return SimpleNamespace(
    stock_code=stock_code,
    instrument_name=name,
    volume=volume,
    can_use_volume=available,
  )


def config(
  *,
  enabled: bool = True,
  ignored=None,
  version: int = 2,
  run_id: str = "run-global",
):
  return SimpleNamespace(
    id="global-1",
    account_id="account-1",
    enabled=enabled,
    mode="paper",
    auto_exit_acknowledged=False,
    ignored_stock_codes=list(ignored or []),
    settings={},
    config_version=version,
    strategy_run_id=run_id,
    universe_revision=3,
    last_reconciled_at=None,
    last_error=None,
    created_at=None,
    updated_at=None,
  )


def session(
  code: str,
  *,
  active: int = 0,
  pending: str = "",
  run_status: str = "running",
):
  return {
    "run_id": "run-global",
    "stock_code": code,
    "mode": "paper",
    "run_status": run_status,
    "active_volume": active,
    "pending_entry_intent_id": pending or None,
    "pending_exit_intent_id": None,
    "global_config_version": 2,
    "completed_cycles": 0,
    "last_net_profit_pct": 0.0,
  }


def test_ignored_codes_are_normalized_and_deduplicated():
  service = TTradeGlobalMonitorService()
  assert service._normalize_ignored_codes(
    ["600000", "600000.SH", " 000001 ", "830001"]
  ) == ["000001.SZ", "600000.SH", "830001.BJ"]
  with pytest.raises(ValueError, match="无效股票代码"):
    service._normalize_ignored_codes(["not-a-stock"])


@pytest.mark.asyncio
async def test_monitor_projects_multiple_holdings_from_one_run():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config(ignored=["000001.SZ"]))
  service.position_service.get_snapshot_status = AsyncMock(return_value=None)
  service.position_service.get_positions = AsyncMock(
    return_value=[
      position("600000.SH", name="浦发银行"),
      position("000001.SZ", name="平安银行"),
      position("300001.SZ", available=0, name="特锐德"),
    ]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", pending="intent-1")]
  )

  result = await service.get_monitor("account-1")

  assert result["strategy_run_id"] == "run-global"
  assert result["holding_count"] == 3
  assert result["eligible_count"] == 1
  assert result["ignored_count"] == 1
  assert result["monitored_count"] == 1
  assert result["pending_signal_count"] == 1
  assert {item["status"] for item in result["holdings"]} == {
    "IGNORED",
    "INELIGIBLE",
    "MONITORED",
  }
  ineligible = next(
    item for item in result["holdings"] if item["stock_code"] == "300001.SZ"
  )
  assert ineligible["reason"] == "昨日可用库存 0 股，不足一手（100 股）"


@pytest.mark.asyncio
async def test_four_lot_holding_is_eligible_without_a_percentage_gate():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config(run_id=None))
  service.position_service.get_snapshot_status = AsyncMock(return_value=None)
  service.position_service.get_positions = AsyncMock(
    return_value=[position("300917.SZ", volume=400, available=400)]
  )

  result = await service.get_monitor("account-1")

  assert result["eligible_count"] == 1
  assert result["holdings"][0]["eligible"] is True
  assert result["holdings"][0]["status"] == "PENDING_START"


def test_disabled_monitor_reports_stopped_before_inventory_eligibility():
  service = TTradeGlobalMonitorService()

  status, reason = service._holding_status(
    config_data={"enabled": False},
    stock_code="600000.SH",
    volume=1000,
    available=1000,
    is_eligible=False,
    is_ignored=False,
    session=None,
  )

  assert status == "STOPPED"
  assert reason == "全局监控未启动"


@pytest.mark.asyncio
async def test_monitor_uses_instrument_master_when_snapshot_name_is_code():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service._load_instrument_names = AsyncMock(
    return_value={"001248.SZ": "华润新能"}
  )
  service.position_service.get_snapshot_status = AsyncMock(return_value=None)
  service.position_service.get_positions = AsyncMock(
    return_value=[position("001248.SZ", name="001248.SZ")]
  )
  service.session_service.get_run_sessions = AsyncMock(return_value=[])

  result = await service.get_monitor("account-1")

  assert result["holdings"][0]["instrument_name"] == "华润新能"
  service._load_instrument_names.assert_awaited_once_with(["001248.SZ"])


@pytest.mark.asyncio
async def test_reconcile_creates_one_run_for_dynamic_holdings_universe():
  service = TTradeGlobalMonitorService()
  current = config(ignored=["000001.SZ"], run_id=None)
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(return_value={})
  service.position_service.get_positions = AsyncMock(
    return_value=[
      position("600000.SH"),
      position("000001.SZ"),
      position("300001.SZ", available=99),
      position("830001.BJ"),
    ]
  )
  service.session_service.start_account_strategy = AsyncMock(return_value="run-new")
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.start_account_strategy.assert_awaited_once()
  payload, instruments, metadata = (
    service.session_service.start_account_strategy.await_args.args
  )
  assert payload["global_config_version"] == 2
  assert instruments == ["300001.SZ", "600000.SH", "830001.BJ"]
  assert metadata["300001.SZ"]["eligible"] is False
  assert metadata["600000.SH"]["eligible"] is True
  assert current.strategy_run_id == "run-new"


@pytest.mark.asyncio
async def test_reconcile_updates_existing_run_once_for_all_codes():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service.position_service.read_agent_snapshot = AsyncMock(return_value={})
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH"), position("000001.SZ")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH"), session("000001.SZ")]
  )
  service.session_service.update_account_strategy = AsyncMock(
    return_value={"added": [], "removed": [], "instruments": []}
  )
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.update_account_strategy.assert_awaited_once()
  assert set(service.session_service.update_account_strategy.await_args.args[2]) == {
    "000001.SZ",
    "600000.SH",
  }


@pytest.mark.asyncio
async def test_disabled_monitor_keeps_only_active_code_in_draining_state():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config(enabled=False))
  service.position_service.read_agent_snapshot = AsyncMock(return_value={})
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH"), position("000001.SZ")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", active=100), session("000001.SZ")]
  )
  service.session_service.update_account_strategy = AsyncMock(return_value={})
  service.session_service.stop_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_not_awaited()
  args = service.session_service.update_account_strategy.await_args.args
  assert args[2] == ["600000.SH"]
  assert args[3]["600000.SH"]["draining"] is True


@pytest.mark.asyncio
async def test_failed_agent_snapshot_does_not_reconcile_or_clear_universe():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service.position_service.read_agent_snapshot = AsyncMock(
    side_effect=RuntimeError("disconnected")
  )
  service.position_service.get_positions = AsyncMock()
  service.session_service.update_account_strategy = AsyncMock()
  service._record_reconcile_result = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.position_service.get_positions.assert_not_awaited()
  service.session_service.update_account_strategy.assert_not_awaited()
  service._record_reconcile_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_run_without_active_batch_is_replaced():
  service = TTradeGlobalMonitorService()
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(return_value={})
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", run_status="error")]
  )
  service.session_service.stop_account_strategy = AsyncMock(
    return_value={"success": True, "message": "stopped"}
  )
  service.session_service.start_account_strategy = AsyncMock(return_value="run-new")
  service.session_service.update_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_awaited_once_with("run-global")
  service.session_service.start_account_strategy.assert_awaited_once()
  service.session_service.update_account_strategy.assert_not_awaited()
  assert current.strategy_run_id == "run-new"


@pytest.mark.asyncio
async def test_terminal_run_with_active_batch_blocks_replacement():
  service = TTradeGlobalMonitorService()
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(return_value={})
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", active=100, run_status="error")]
  )
  service.session_service.stop_account_strategy = AsyncMock()
  service.session_service.start_account_strategy = AsyncMock()
  service.session_service.update_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_not_awaited()
  service.session_service.start_account_strategy.assert_not_awaited()
  service.session_service.update_account_strategy.assert_not_awaited()
  errors = service._save_reconcile_config.await_args.args[1]
  assert "策略运行状态异常" in errors[0]
  assert current.strategy_run_id == "run-global"
