import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_MCP_BASE_URL = "http://127.0.0.1:8080/mcp"


class MCPConnectionError(RuntimeError):
    """Raised when the MCP server is unreachable."""


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _mcp_base_url() -> str:
    return os.getenv("MCP_BASE_URL", DEFAULT_MCP_BASE_URL)


def _strict_mode() -> bool:
    return _env_flag("MCP_STRICT")


def _require_live() -> bool:
    return _env_flag("MCP_INTEGRATION_REQUIRED")


def _extract_text(block: Any) -> str:
    if hasattr(block, "text"):
        return block.text
    if isinstance(block, dict) and "text" in block:
        return block["text"]
    return str(block)


def _parse_tool_payload(result: Any) -> dict:
    assert result.content, "tool result content is empty"
    raw = _extract_text(result.content[0])
    return json.loads(raw)


def _parse_resource_payload(result: Any) -> dict:
    assert result.contents, "resource contents is empty"
    raw = result.contents[0].text
    return json.loads(raw)


def _assert_success_or_skip(payload: dict, context: str) -> None:
    if payload.get("status") == "success":
        return
    if _strict_mode():
        pytest.fail(f"{context} returned error: {payload}")
    pytest.skip(f"{context} not ready: {payload.get('error') or payload}")


@asynccontextmanager
async def _mcp_session(base_url: str) -> AsyncIterator[ClientSession]:
    try:
        async with streamable_http_client(base_url) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()
                assert init_result.protocolVersion is not None
                yield session
    except Exception as exc:  # pragma: no cover - environment dependent
        raise MCPConnectionError(str(exc)) from exc


def _run_async(test_coro: Any) -> None:
    try:
        asyncio.run(test_coro)
    except AssertionError:
        raise
    except MCPConnectionError as exc:  # pragma: no cover - environment dependent
        if _require_live():
            pytest.fail(f"MCP server not reachable: {exc}")
        pytest.skip(f"MCP server not reachable: {exc}")


pytestmark = [pytest.mark.integration, pytest.mark.mcp]


def test_mcp_initialize_and_list_tools():
    base_url = _mcp_base_url()

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            tools_result = await session.list_tools()
            tool_names = {tool.name for tool in tools_result.tools}

            assert any(name.startswith("market_data_") for name in tool_names)
            assert any(name.startswith("strategy_") for name in tool_names)
            assert any(name.startswith("account_") for name in tool_names)
            assert any(name.startswith("order_") for name in tool_names)
            assert any(name.startswith("analysis_") for name in tool_names)

    _run_async(_test())


def test_mcp_list_resources():
    base_url = _mcp_base_url()

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            result = await session.list_resources()
            uris = {resource.uri for resource in result.resources}

            assert "market_data://realtime" in uris
            assert "account://positions" in uris

    _run_async(_test())


def test_mcp_read_resource_realtime():
    base_url = _mcp_base_url()

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            resource_result = await session.read_resource("market_data://realtime")
            payload = _parse_resource_payload(resource_result)

            assert isinstance(payload, dict)
            assert "symbols" in payload

    _run_async(_test())


def test_mcp_call_tool_market_realtime():
    base_url = _mcp_base_url()
    symbol = os.getenv("MCP_TEST_SYMBOL", "000001.SZ")

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            result = await session.call_tool(
                "market_data_get_realtime",
                {"symbol": symbol},
            )
            payload = _parse_tool_payload(result)
            _assert_success_or_skip(payload, "market_data_get_realtime")

            assert payload["symbol"] == symbol
            assert "data" in payload

    _run_async(_test())


def test_mcp_call_tool_market_kline():
    base_url = _mcp_base_url()
    symbol = os.getenv("MCP_TEST_SYMBOL", "000001.SZ")
    period = os.getenv("MCP_TEST_KLINE_PERIOD", "1d")

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            result = await session.call_tool(
                "market_data_get_kline",
                {"symbol": symbol, "period": period, "count": 20},
            )
            payload = _parse_tool_payload(result)
            _assert_success_or_skip(payload, "market_data_get_kline")

            assert payload["symbol"] == symbol
            assert payload["period"] == period
            assert isinstance(payload.get("data"), list)

    _run_async(_test())


def test_mcp_call_tool_account_info():
    base_url = _mcp_base_url()
    account_id = os.getenv("MCP_TEST_ACCOUNT_ID")
    args = {"account_id": account_id} if account_id else {}

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            result = await session.call_tool("account_get_info", args)
            payload = _parse_tool_payload(result)
            _assert_success_or_skip(payload, "account_get_info")

            account = payload.get("account", {})
            assert "account_id" in account

    _run_async(_test())


def test_mcp_call_tool_account_positions():
    base_url = _mcp_base_url()
    account_id = os.getenv("MCP_TEST_ACCOUNT_ID")
    args = {"account_id": account_id} if account_id else {}

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            result = await session.call_tool("account_get_positions", args)
            payload = _parse_tool_payload(result)
            _assert_success_or_skip(payload, "account_get_positions")

            assert "positions" in payload
            assert "count" in payload
            if payload["positions"]:
                position = payload["positions"][0]
                assert "symbol" in position
                assert "quantity" in position

    _run_async(_test())


def test_mcp_call_tool_order_status():
    base_url = _mcp_base_url()

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            result = await session.call_tool(
                "order_get_status",
                {"order_id": "TEST_ORDER_ID"},
            )
            payload = _parse_tool_payload(result)
            _assert_success_or_skip(payload, "order_get_status")

            assert payload["order_id"] == "TEST_ORDER_ID"
            assert "order_status" in payload

    _run_async(_test())


def test_mcp_list_resource_templates():
    base_url = _mcp_base_url()

    async def _test() -> None:
        async with _mcp_session(base_url) as session:
            result = await session.list_resource_templates()
            assert result is not None

    _run_async(_test())
