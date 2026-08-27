from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from quantx_qmt_agent import main as main_module
from quantx_qmt_agent.emergency import EmergencyStopStore
from quantx_qmt_agent.main import _require_safe_run_mode
from quantx_qmt_agent.runtime import (
  _FatalMarketDataPreparationError,
  _FatalTradingRecoveryError,
)


class _FakeProcessWatchdog:
  def __init__(self) -> None:
    self.start_count = 0
    self.close_count = 0

  def start(self) -> None:
    self.start_count += 1
    if self.start_count > 1:
      raise AssertionError("watchdog started more than once")

  async def heartbeat_loop(self) -> None:
    await asyncio.Event().wait()

  def close(self) -> None:
    self.close_count += 1
    if self.close_count > 1:
      raise AssertionError("watchdog closed more than once")


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


def test_live_mode_rejects_multiple_accounts(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("ENV", "testing")
  monkeypatch.setenv("ENABLE_REAL_TRADING", "true")
  monkeypatch.setenv("QMT_REAL_TRADING_ENABLED", "true")

  with pytest.raises(SystemExit, match="exactly one"):
    _require_safe_run_mode("live", {"account-1", "account-2"})


def test_enroll_accepts_http_without_redirects_or_system_proxy(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  client_options: dict[str, object] = {}
  request: dict[str, object] = {}
  saved: dict[str, str] = {}

  class Response:
    @staticmethod
    def raise_for_status() -> None:
      return None

    @staticmethod
    def json() -> dict[str, str]:
      return {
        "deviceId": "device-12345678",
        "deviceSecret": "device-secret",
      }

  class Client:
    def __init__(self, **kwargs: object) -> None:
      client_options.update(kwargs)

    def __enter__(self):
      return self

    def __exit__(self, *_args: object) -> None:
      return None

    @staticmethod
    def post(url: str, *, json: dict[str, str]) -> Response:
      request.update(url=url, json=json)
      return Response()

  class CredentialStore:
    @staticmethod
    def save(**kwargs: str) -> None:
      saved.update(kwargs)

  monkeypatch.setattr(main_module.httpx, "Client", Client)
  monkeypatch.setattr(main_module, "DeviceCredentialStore", CredentialStore)

  main_module._enroll("HTTP://API.TEST:8080/", "one-time-code")

  assert client_options["follow_redirects"] is False
  assert client_options["trust_env"] is False
  assert client_options["verify"] is True
  assert request == {
    "url": "http://api.test:8080/auth/agent/enrollments/exchange",
    "json": {"enrollmentCode": "one-time-code"},
  }
  assert saved == {
    "api_url": "http://api.test:8080",
    "device_id": "device-12345678",
    "device_secret": "device-secret",
  }
  output = capsys.readouterr().out
  assert "one-time-code" not in output
  assert "device-secret" not in output
  assert "device-12345678" not in output


def test_enroll_rejects_missing_https_ca_before_network(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-root.crt"))

  class Client:
    def __init__(self, **_kwargs: object) -> None:
      raise AssertionError("network client must not be created")

  monkeypatch.setattr(main_module.httpx, "Client", Client)

  with pytest.raises(SystemExit, match="SSL_CERT_FILE"):
    main_module._enroll("https://api.test:8080", "one-time-code")


def test_live_run_accepts_explicit_http_endpoint(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  watchdog = _FakeProcessWatchdog()
  runtime_kwargs: dict[str, object] = {}

  class CredentialStore:
    @staticmethod
    def load():
      return (
        SimpleNamespace(device_id="device-1", api_url="http://api.test"),
        "secret",
      )

  class Runtime:
    @staticmethod
    async def run_forever() -> None:
      return None

  def create_runtime(**kwargs):
    runtime_kwargs.update(kwargs)
    return Runtime()

  monkeypatch.setenv("ENV", "testing")
  monkeypatch.setenv("ENABLE_REAL_TRADING", "true")
  monkeypatch.setenv("QMT_REAL_TRADING_ENABLED", "true")
  monkeypatch.setattr(main_module, "_accounts", lambda: {"account-1"})
  monkeypatch.setattr(main_module, "state_directory", lambda: tmp_path)
  monkeypatch.setattr(
    main_module.AgentProcessWatchdog,
    "create",
    lambda _base_directory: watchdog,
  )
  monkeypatch.setattr(main_module, "DeviceCredentialStore", CredentialStore)
  monkeypatch.setattr(
    main_module,
    "LocalJournal",
    lambda _path: SimpleNamespace(integrity_check=lambda: "ok"),
  )
  monkeypatch.setattr(main_module, "EmergencyStopStore", lambda _path: object())
  monkeypatch.setattr(main_module, "AgentRuntime", create_runtime)

  main_module._run("live")

  assert watchdog.start_count == 1
  assert watchdog.close_count == 1
  assert runtime_kwargs["configuration"].api_url == "http://api.test"
  assert runtime_kwargs["mode"] == "live"


def test_live_mode_does_not_depend_on_t_trade_feature_gate(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("ENV", "production")
  monkeypatch.setenv("ENABLE_REAL_TRADING", "true")
  monkeypatch.setenv("QMT_REAL_TRADING_ENABLED", "true")
  monkeypatch.setenv("T_TRADE_LIVE_ENABLED", "false")

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


def test_fatal_market_data_runtime_uses_process_level_fail_stop(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  exit_codes: list[int] = []

  class Runtime:
    @staticmethod
    async def run_forever() -> None:
      raise _FatalMarketDataPreparationError("native XTData is stuck")

  monkeypatch.setattr(
    main_module,
    "_hard_exit_for_fatal_market_data",
    exit_codes.append,
  )
  watchdog = _FakeProcessWatchdog()
  watchdog.start()

  with pytest.raises(
    _FatalMarketDataPreparationError,
    match="native XTData is stuck",
  ):
    main_module._run_runtime(Runtime(), watchdog)

  assert exit_codes == [main_module.FATAL_MARKET_DATA_EXIT_CODE]
  assert watchdog.start_count == 1
  assert watchdog.close_count == 1


def test_fatal_trading_runtime_uses_process_level_fail_stop(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  exit_codes: list[int] = []

  class Runtime:
    @staticmethod
    async def run_forever() -> None:
      raise _FatalTradingRecoveryError("native XTTrading is stuck")

  monkeypatch.setattr(
    main_module,
    "_hard_exit_for_fatal_market_data",
    exit_codes.append,
  )
  watchdog = _FakeProcessWatchdog()
  watchdog.start()

  with pytest.raises(
    _FatalTradingRecoveryError,
    match="native XTTrading is stuck",
  ):
    main_module._run_runtime(Runtime(), watchdog)

  assert exit_codes == [main_module.FATAL_MARKET_DATA_EXIT_CODE]
  assert watchdog.start_count == 1
  assert watchdog.close_count == 1


def test_normal_runtime_completion_does_not_hard_exit(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  exit_codes: list[int] = []

  class Runtime:
    @staticmethod
    async def run_forever() -> None:
      return None

  monkeypatch.setattr(
    main_module,
    "_hard_exit_for_fatal_market_data",
    exit_codes.append,
  )
  watchdog = _FakeProcessWatchdog()
  watchdog.start()

  main_module._run_runtime(Runtime(), watchdog)

  assert exit_codes == []
  assert watchdog.start_count == 1
  assert watchdog.close_count == 1


def test_runtime_starts_and_closes_only_the_watchdog_it_creates(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  watchdog = _FakeProcessWatchdog()

  class Runtime:
    @staticmethod
    async def run_forever() -> None:
      return None

  monkeypatch.setattr(main_module, "state_directory", lambda: tmp_path)
  monkeypatch.setattr(
    main_module.AgentProcessWatchdog,
    "create",
    lambda _base_directory: watchdog,
  )

  main_module._run_runtime(Runtime())

  assert watchdog.start_count == 1
  assert watchdog.close_count == 1


def test_run_transfers_one_started_watchdog_to_runtime_once(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  watchdog = _FakeProcessWatchdog()

  class CredentialStore:
    @staticmethod
    def load():
      return (
        SimpleNamespace(device_id="device-1", api_url="http://api.test"),
        "secret-not-forwarded-to-watchdog",
      )

  class Runtime:
    @staticmethod
    async def run_forever() -> None:
      return None

  monkeypatch.setattr(main_module, "_accounts", lambda: set())
  monkeypatch.setattr(main_module, "state_directory", lambda: tmp_path)
  monkeypatch.setattr(
    main_module.AgentProcessWatchdog,
    "create",
    lambda _base_directory: watchdog,
  )
  monkeypatch.setattr(main_module, "DeviceCredentialStore", CredentialStore)
  monkeypatch.setattr(
    main_module,
    "LocalJournal",
    lambda _path: SimpleNamespace(integrity_check=lambda: "ok"),
  )
  monkeypatch.setattr(
    main_module,
    "QmtDataBroker",
    lambda _accounts, *, data_only: SimpleNamespace(data_only=data_only),
  )
  monkeypatch.setattr(main_module, "EmergencyStopStore", lambda _path: object())
  monkeypatch.setattr(main_module, "AgentRuntime", lambda **_kwargs: Runtime())

  main_module._run("data-only")

  assert watchdog.start_count == 1
  assert watchdog.close_count == 1


def test_run_closes_started_watchdog_once_when_setup_fails(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  watchdog = _FakeProcessWatchdog()

  class CredentialStore:
    @staticmethod
    def load():
      return (
        SimpleNamespace(device_id="device-1", api_url="http://api.test"),
        "secret-not-forwarded-to-watchdog",
      )

  monkeypatch.setattr(main_module, "_accounts", lambda: set())
  monkeypatch.setattr(main_module, "state_directory", lambda: tmp_path)
  monkeypatch.setattr(
    main_module.AgentProcessWatchdog,
    "create",
    lambda _base_directory: watchdog,
  )
  monkeypatch.setattr(main_module, "DeviceCredentialStore", CredentialStore)

  def fail_journal(_path):
    raise ValueError("journal setup failed")

  monkeypatch.setattr(main_module, "LocalJournal", fail_journal)

  with pytest.raises(ValueError, match="journal setup failed"):
    main_module._run("data-only")

  assert watchdog.start_count == 1
  assert watchdog.close_count == 1


def test_corrupt_journal_blocks_runtime_before_native_or_network_initialization(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  watchdog = _FakeProcessWatchdog()
  runtime_created = False

  class CredentialStore:
    @staticmethod
    def load():
      return (
        SimpleNamespace(device_id="device-1", api_url="http://api.test"),
        "secret",
      )

  def create_runtime(**_kwargs):
    nonlocal runtime_created
    runtime_created = True
    raise AssertionError("runtime must not be initialized with a corrupt journal")

  monkeypatch.setattr(main_module, "_accounts", lambda: set())
  monkeypatch.setattr(main_module, "state_directory", lambda: tmp_path)
  monkeypatch.setattr(
    main_module.AgentProcessWatchdog,
    "create",
    lambda _base_directory: watchdog,
  )
  monkeypatch.setattr(main_module, "DeviceCredentialStore", CredentialStore)
  monkeypatch.setattr(
    main_module,
    "LocalJournal",
    lambda _path: SimpleNamespace(integrity_check=lambda: "corrupt"),
  )
  monkeypatch.setattr(main_module, "AgentRuntime", create_runtime)

  with pytest.raises(SystemExit, match="journal 完整性检查失败"):
    main_module._run("data-only")

  assert runtime_created is False
  assert watchdog.start_count == 1
  assert watchdog.close_count == 1
