from __future__ import annotations

import pytest
from quantx_qmt_agent.emergency import EmergencyStopStore
from quantx_qmt_agent.main import _require_safe_run_mode


def test_data_only_mode_does_not_require_an_account_whitelist(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.delenv("ENV", raising=False)
  monkeypatch.delenv("ENABLE_REAL_TRADING", raising=False)
  monkeypatch.delenv("QMT_REAL_TRADING_ENABLED", raising=False)

  _require_safe_run_mode("data-only", set())


def test_paper_mode_requires_an_explicit_account_whitelist() -> None:
  with pytest.raises(SystemExit, match="QMT_ACCOUNT_WHITELIST"):
    _require_safe_run_mode("paper", set())


@pytest.mark.parametrize(
  ("environment", "enable_real", "enable_qmt", "expected"),
  [
    (
      "production",
      "true",
      "true",
      "production live mode requires T_TRADE_LIVE_ENABLED",
    ),
    ("development", "true", "true", "ENV=testing"),
    ("testing", "", "true", "ENABLE_REAL_TRADING=true"),
    ("testing", "true", "", "QMT_REAL_TRADING_ENABLED=true"),
  ],
)
def test_live_mode_fails_closed_unless_every_real_trading_gate_is_present(
  monkeypatch: pytest.MonkeyPatch,
  environment: str,
  enable_real: str,
  enable_qmt: str,
  expected: str,
) -> None:
  monkeypatch.setenv("ENV", environment)
  monkeypatch.setenv("ENABLE_REAL_TRADING", enable_real)
  monkeypatch.setenv("QMT_REAL_TRADING_ENABLED", enable_qmt)

  with pytest.raises(SystemExit, match=expected):
    _require_safe_run_mode("live", {"account-1"})


def test_live_mode_accepts_explicit_testing_configuration(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("ENV", "testing")
  monkeypatch.setenv("ENABLE_REAL_TRADING", "true")
  monkeypatch.setenv("QMT_REAL_TRADING_ENABLED", "true")

  _require_safe_run_mode("live", {"account-1"})


def test_live_mode_accepts_production_only_with_t_trade_gate(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("ENV", "production")
  monkeypatch.setenv("ENABLE_REAL_TRADING", "true")
  monkeypatch.setenv("QMT_REAL_TRADING_ENABLED", "true")
  monkeypatch.setenv("T_TRADE_LIVE_ENABLED", "true")

  _require_safe_run_mode("live", {"account-1"})


def test_local_emergency_stop_is_fail_closed_and_protected(tmp_path) -> None:
  store = EmergencyStopStore(tmp_path / "emergency-stop.json")
  assert store.status()["active"] is False

  activated = store.activate("operator requested stop")
  assert activated["active"] is True
  assert store.status()["reason"] == "operator requested stop"

  with pytest.raises(ValueError, match="exact confirmation"):
    store.clear("yes")
  assert store.status()["active"] is True

  store.clear("CLEAR-LOCAL-EMERGENCY")
  assert store.status()["active"] is False


def test_unreadable_emergency_state_blocks_trading(tmp_path) -> None:
  path = tmp_path / "emergency-stop.json"
  path.write_text("{not-json", encoding="utf-8")

  status = EmergencyStopStore(path).status()

  assert status["active"] is True
  assert status["reason"] == "emergency state is unreadable"
