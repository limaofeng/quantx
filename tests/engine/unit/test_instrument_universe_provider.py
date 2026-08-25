import pytest
from quantx_domain.enums import StrategyInstrumentUniverseMode
from quantx_engine.instrument_universe_provider import (
  AccountHoldingPosition,
  AccountHoldingsUniverseRequest,
  AccountInstrumentWork,
  InstrumentUniverseProviderRegistry,
  StaticUniverseRequest,
)


def test_static_provider_normalizes_snapshot_and_metadata() -> None:
  registry = InstrumentUniverseProviderRegistry.with_defaults()

  snapshot = registry.resolve(
    StrategyInstrumentUniverseMode.STATIC,
    StaticUniverseRequest(
      instruments=("000002.sz", "000001.SZ", "000002.SZ", ""),
      metadata={
        "000001.sz": {"name": "A"},
        "999999.SZ": {"name": "not selected"},
      },
    ),
  )

  assert snapshot.mode == StrategyInstrumentUniverseMode.STATIC
  assert snapshot.instruments == ("000001.SZ", "000002.SZ")
  assert snapshot.metadata == {
    "000001.SZ": {"name": "A"},
    "000002.SZ": {},
  }


def test_account_holdings_provider_retains_open_work_as_draining() -> None:
  registry = InstrumentUniverseProviderRegistry.with_defaults()

  snapshot = registry.resolve(
    StrategyInstrumentUniverseMode.ACCOUNT_HOLDINGS,
    AccountHoldingsUniverseRequest(
      enabled=True,
      ignored_instruments=("600000.sh",),
      positions=(
        AccountHoldingPosition(
          instrument_code="000001.sz",
          instrument_name="平安银行",
          volume=1_000,
          available_volume=800,
          frozen_volume=200,
          average_price=10.5,
          market_value=11_000,
        ),
        AccountHoldingPosition(
          instrument_code="000002.SZ",
          volume=100,
          available_volume=99,
        ),
        AccountHoldingPosition(
          instrument_code="600000.SH",
          volume=500,
          available_volume=500,
        ),
        AccountHoldingPosition(
          instrument_code="00700.HK",
          volume=100,
          available_volume=100,
        ),
      ),
      instrument_work=(
        AccountInstrumentWork(instrument_code="600000.SH", active_volume=100),
        AccountInstrumentWork(
          instrument_code="000003.SZ",
          pending_exit_intent_id="exit-1",
        ),
      ),
    ),
  )

  assert snapshot.instruments == (
    "000001.SZ",
    "000002.SZ",
    "000003.SZ",
    "600000.SH",
  )
  assert snapshot.metadata["000001.SZ"] == {
    "eligible": True,
    "reason": "ELIGIBLE",
    "draining": False,
    "instrument_name": "平安银行",
    "position_shares": 1_000,
    "position_available_shares": 800,
    "position_frozen_shares": 200,
    "position_avg_price": 10.5,
    "position_market_value": 11_000.0,
  }
  assert snapshot.metadata["000002.SZ"]["reason"] == ("AVAILABLE_VOLUME_BELOW_100")
  assert snapshot.metadata["000002.SZ"]["eligible"] is False
  assert snapshot.metadata["600000.SH"]["reason"] == ("DRAINING_EXISTING_T_BATCH")
  assert snapshot.metadata["600000.SH"]["position_shares"] == 500
  assert snapshot.metadata["000003.SZ"]["draining"] is True
  assert "00700.HK" not in snapshot.metadata


def test_registry_rejects_request_for_a_different_universe_mode() -> None:
  registry = InstrumentUniverseProviderRegistry.with_defaults()

  with pytest.raises(TypeError, match="StaticUniverseRequest"):
    registry.resolve(
      StrategyInstrumentUniverseMode.STATIC,
      AccountHoldingsUniverseRequest(enabled=True, positions=()),
    )
