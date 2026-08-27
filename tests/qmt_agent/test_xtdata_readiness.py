from __future__ import annotations

from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest
from quantx_qmt_agent.miniqmt.data import connection_discovery
from quantx_qmt_agent.miniqmt.data.connection_discovery import (
  XTDataEndpoint,
  _ProcessIdentity,
  _TcpListener,
)


def test_explicit_xtdata_port_is_loopback_and_authoritative() -> None:
  endpoint = connection_discovery.discover_xtdata_endpoint({"QMT_XTDATA_PORT": "58600"})

  assert endpoint == XTDataEndpoint(
    host="127.0.0.1",
    port=58600,
    source="QMT_XTDATA_PORT",
  )


def test_xtitclient_endpoint_requires_matching_broker_config_and_listener(
  monkeypatch,
) -> None:
  executable = Path("F:/QMT/bin.x64/XtItClient.exe")
  monkeypatch.setattr(
    connection_discovery,
    "_windows_tcp_listeners",
    lambda: (
      _TcpListener("0.0.0.0", 58600, 42),
      _TcpListener("127.0.0.1", 8086, 42),
    ),
  )
  monkeypatch.setattr(
    connection_discovery,
    "_windows_process_table",
    lambda: {42: _ProcessIdentity("XtItClient.exe", 1)},
  )
  monkeypatch.setattr(
    connection_discovery,
    "_windows_process_path",
    lambda pid: executable if pid == 42 else None,
  )
  monkeypatch.setattr(
    connection_discovery,
    "_formula_worker_port",
    lambda path: 58600 if path == executable else None,
  )

  endpoint = connection_discovery.discover_xtdata_endpoint({})

  assert endpoint.host == "127.0.0.1"
  assert endpoint.port == 58600
  assert PureWindowsPath(endpoint.source).parts[-2:] == ("config", "broker.ini")


def test_xtdata_discovery_fails_closed_on_multiple_verified_instances(
  monkeypatch,
) -> None:
  monkeypatch.setattr(
    connection_discovery,
    "_windows_tcp_listeners",
    lambda: (
      _TcpListener("0.0.0.0", 58600, 42),
      _TcpListener("0.0.0.0", 58601, 43),
    ),
  )
  monkeypatch.setattr(
    connection_discovery,
    "_windows_process_table",
    lambda: {
      42: _ProcessIdentity("XtItClient.exe", 1),
      43: _ProcessIdentity("XtItClient.exe", 1),
    },
  )
  monkeypatch.setattr(
    connection_discovery,
    "_windows_process_path",
    lambda pid: Path(f"F:/QMT-{pid}/bin.x64/XtItClient.exe"),
  )
  monkeypatch.setattr(
    connection_discovery,
    "_formula_worker_port",
    lambda path: 58600 if "42" in str(path) else 58601,
  )

  with pytest.raises(RuntimeError, match="multiple verified XTData endpoints"):
    connection_discovery.discover_xtdata_endpoint({})


def test_miniqmt_endpoint_requires_parent_config_and_listener(
  monkeypatch,
) -> None:
  quote_path = Path("F:/QMT/bin.x64/miniquote.exe")
  parent_path = Path("F:/QMT/bin.x64/XtMiniQmt.exe")
  monkeypatch.setattr(
    connection_discovery,
    "_windows_tcp_listeners",
    lambda: (_TcpListener("0.0.0.0", 58610, 42),),
  )
  monkeypatch.setattr(
    connection_discovery,
    "_windows_process_table",
    lambda: {
      42: _ProcessIdentity("miniquote.exe", 43),
      43: _ProcessIdentity("XtMiniQmt.exe", 1),
    },
  )
  monkeypatch.setattr(
    connection_discovery,
    "_windows_process_path",
    lambda pid: {42: quote_path, 43: parent_path}.get(pid),
  )
  monkeypatch.setattr(
    connection_discovery,
    "_miniquote_port",
    lambda path: 58610 if path == quote_path else None,
  )

  endpoint = connection_discovery.discover_xtdata_endpoint({})

  assert endpoint.host == "127.0.0.1"
  assert endpoint.port == 58610
  assert PureWindowsPath(endpoint.source) == PureWindowsPath(
    "F:/QMT/config/xtminiquote.lua"
  )


def test_miniqmt_toolhelp_identity_survives_unreadable_process_paths(
  monkeypatch,
) -> None:
  monkeypatch.setattr(
    connection_discovery,
    "_windows_tcp_listeners",
    lambda: (_TcpListener("0.0.0.0", 58610, 42),),
  )
  monkeypatch.setattr(
    connection_discovery,
    "_windows_process_table",
    lambda: {
      42: _ProcessIdentity("miniquote.exe", 43),
      43: _ProcessIdentity("XtMiniQmt.exe", 1),
    },
  )
  monkeypatch.setattr(
    connection_discovery,
    "_windows_process_path",
    lambda _pid: None,
  )

  endpoint = connection_discovery.discover_xtdata_endpoint({})

  assert endpoint == XTDataEndpoint(
    "127.0.0.1",
    58610,
    "Toolhelp32:miniquote.exe<-XtMiniQmt.exe",
  )


def test_miniquote_port_parser_tolerates_non_utf8_config(tmp_path: Path) -> None:
  executable = tmp_path / "bin.x64" / "miniquote.exe"
  executable.parent.mkdir()
  executable.touch()
  config_directory = tmp_path / "config"
  config_directory.mkdir()
  (config_directory / "xtminiquote.lua").write_bytes(
    b'\x81\x82-- vendor comment\r\nservice = {\r\n  address = "0.0.0.0:58610",\r\n}\r\n'
  )

  assert connection_discovery._miniquote_port(executable) == 58610


def test_xtdata_manager_connects_only_to_discovered_explicit_endpoint(
  monkeypatch,
) -> None:
  pytest.importorskip(
    "xtquant",
    reason="miniQMT SDK is only available on the QMT host",
  )
  from quantx_qmt_agent.miniqmt.data import data_manager as module

  calls = []

  class Client:
    @staticmethod
    def is_connected() -> bool:
      return True

  monkeypatch.setattr(
    module,
    "discover_xtdata_endpoint",
    lambda: XTDataEndpoint("127.0.0.1", 58600, "verified"),
  )
  monkeypatch.setattr(
    module,
    "xtdata",
    SimpleNamespace(
      connect=lambda **kwargs: calls.append(kwargs) or Client(),
    ),
  )

  manager = module.XTDataManager()

  assert manager.is_connected is True
  assert manager.connected_endpoint is not None
  assert manager.connected_endpoint.port == 58600
  assert calls == [
    {
      "ip": "127.0.0.1",
      "port": 58600,
      "remember_if_success": True,
    }
  ]


@pytest.mark.parametrize("period", ["tick", "1m"])
def test_xtdata_manager_preserves_explicit_intraday_history_bounds(
  monkeypatch,
  period: str,
) -> None:
  pytest.importorskip(
    "xtquant",
    reason="miniQMT SDK is only available on the QMT host",
  )
  from quantx_qmt_agent.miniqmt.data import data_manager as module

  download_calls: list[tuple[list[str], str, dict]] = []
  query_calls: list[dict] = []

  class Client:
    @staticmethod
    def is_connected() -> bool:
      return True

  def download_history_data2(codes, requested_period, **kwargs):
    download_calls.append((codes, requested_period, kwargs))

  def get_market_data_ex(**kwargs):
    query_calls.append(kwargs)
    return {}

  monkeypatch.setattr(
    module,
    "discover_xtdata_endpoint",
    lambda: XTDataEndpoint("127.0.0.1", 58600, "verified"),
  )
  monkeypatch.setattr(
    module,
    "xtdata",
    SimpleNamespace(
      connect=lambda **_kwargs: Client(),
      download_history_data2=download_history_data2,
      get_market_data_ex=get_market_data_ex,
    ),
  )
  manager = module.XTDataManager()

  manager.download_market_data(
    ["600000.SH"],
    period,
    start_time="20260722000000",
    end_time="20260723235959",
    incrementally=False,
  )
  manager.get_market_data(
    ["600000.SH"],
    period,
    start_time="20260722000000",
    end_time="20260723235959",
  )

  assert download_calls == [
    (
      ["600000.SH"],
      period,
      {
        "start_time": "20260722000000",
        "end_time": "20260723235959",
        "callback": None,
        "incrementally": False,
      },
    )
  ]
  assert query_calls[0]["start_time"] == "20260722000000"
  assert query_calls[0]["end_time"] == "20260723235959"


def test_xtdata_manager_connection_failure_is_not_a_legal_empty_result(
  monkeypatch,
) -> None:
  pytest.importorskip(
    "xtquant",
    reason="miniQMT SDK is only available on the QMT host",
  )
  from quantx_qmt_agent.miniqmt.data import data_manager as module

  monkeypatch.setattr(
    module,
    "discover_xtdata_endpoint",
    lambda: XTDataEndpoint("127.0.0.1", 58600, "verified"),
  )

  def fail_connect(**_kwargs):
    raise RuntimeError("service refused connection")

  monkeypatch.setattr(
    module,
    "xtdata",
    SimpleNamespace(connect=fail_connect),
  )
  manager = module.XTDataManager()

  with pytest.raises(
    module.XTDataUnavailableError,
    match="RuntimeError",
  ):
    manager.get_market_data(["600000.SH"])

  assert manager.is_connected is False
  assert manager.last_connection_error == "RuntimeError"


def test_xtdata_manager_preserves_a_legitimate_empty_sdk_result(
  monkeypatch,
) -> None:
  pytest.importorskip(
    "xtquant",
    reason="miniQMT SDK is only available on the QMT host",
  )
  from quantx_qmt_agent.miniqmt.data import data_manager as module

  class Client:
    @staticmethod
    def is_connected() -> bool:
      return True

  monkeypatch.setattr(
    module,
    "discover_xtdata_endpoint",
    lambda: XTDataEndpoint("127.0.0.1", 58600, "verified"),
  )
  monkeypatch.setattr(
    module,
    "xtdata",
    SimpleNamespace(
      connect=lambda **_kwargs: Client(),
      get_market_data_ex=lambda **_kwargs: {},
    ),
  )
  manager = module.XTDataManager()

  assert manager.get_market_data(["600000.SH"]) == {}
  assert manager.is_connected is True


def test_xtdata_manager_non_object_sdk_result_reaches_broker_validation(
  monkeypatch,
) -> None:
  pytest.importorskip(
    "xtquant",
    reason="miniQMT SDK is only available on the QMT host",
  )
  from quantx_qmt_agent.broker import _market_data_records
  from quantx_qmt_agent.miniqmt.data import data_manager as module

  class Client:
    @staticmethod
    def is_connected() -> bool:
      return True

  monkeypatch.setattr(
    module,
    "discover_xtdata_endpoint",
    lambda: XTDataEndpoint("127.0.0.1", 58600, "verified"),
  )
  monkeypatch.setattr(
    module,
    "xtdata",
    SimpleNamespace(
      connect=lambda **_kwargs: Client(),
      get_market_data_ex=lambda **_kwargs: [],
    ),
  )
  manager = module.XTDataManager()

  with pytest.raises(ValueError, match="non-object market-data result"):
    _market_data_records(
      manager,
      {
        "operation": "bars",
        "stock_list": ["600000.SH"],
        "periods": ["1d"],
        "start_time": "20250102",
        "end_time": "20250102",
        "download": False,
      },
    )

  assert manager.is_connected is True
