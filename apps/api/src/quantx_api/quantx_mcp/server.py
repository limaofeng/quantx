"""
MCP Server implementation for QuantX

This module implements the MCP server that exposes QuantX's capabilities
as MCP tools for AI agents to use.
"""

import asyncio
import json
import logging

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
)

from .tools import (
    AccountTools,
    AnalysisTools,
    MarketDataTools,
    OrderTools,
    StrategyTools,
)

logger = logging.getLogger(__name__)


class QuantXMCPServer:
    """
    QuantX MCP Server

    Exposes QuantX trading system capabilities as MCP tools.
    """

    def __init__(self):
        self.market_data_tools = MarketDataTools()
        self.strategy_tools = StrategyTools()
        self.account_tools = AccountTools()
        self.order_tools = OrderTools()
        self.analysis_tools = AnalysisTools()
        self.app = Server(
            "quantx-server",
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_resources=self._list_resources,
            on_read_resource=self._read_resource,
        )

    async def _list_tools(
        self,
        _context: ServerRequestContext,
        _params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        tools = []
        for category in (
            self.market_data_tools,
            self.strategy_tools,
            self.account_tools,
            self.order_tools,
            self.analysis_tools,
        ):
            tools.extend(category.get_tools())
        logger.info("Listed %s tools", len(tools))
        return ListToolsResult(tools=tools)

    async def _call_tool(
        self,
        _context: ServerRequestContext,
        params: CallToolRequestParams,
    ) -> CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        try:
            logger.info("Tool called: %s", name)
            if name.startswith("market_data_"):
                result = await self.market_data_tools.handle(name, arguments)
            elif name.startswith("strategy_"):
                result = await self.strategy_tools.handle(name, arguments)
            elif name.startswith("account_"):
                result = await self.account_tools.handle(name, arguments)
            elif name.startswith("order_"):
                result = await self.order_tools.handle(name, arguments)
            elif name.startswith("analysis_"):
                result = await self.analysis_tools.handle(name, arguments)
            else:
                result = {"error": f"Unknown tool: {name}", "status": "error"}
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(result, ensure_ascii=False, indent=2),
                    )
                ],
                structuredContent=result,
                isError=result.get("status") == "error",
            )
        except Exception as exc:
            logger.error("Error calling tool %s: %s", name, exc, exc_info=True)
            result = {"error": str(exc), "status": "error", "tool": name}
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(result, ensure_ascii=False, indent=2),
                    )
                ],
                structuredContent=result,
                isError=True,
            )

    async def _list_resources(
        self,
        _context: ServerRequestContext,
        _params: PaginatedRequestParams | None,
    ) -> ListResourcesResult:
        descriptions = {
            "market_data://realtime": "Realtime market data summary",
            "market_data://historical": "Historical data summary",
            "strategies://active": "Active strategies",
            "strategies://available": "Available strategies",
            "orders://pending": "Pending orders",
            "orders://history": "Order history",
            "account://info": "Account info",
            "account://positions": "Account positions",
        }
        return ListResourcesResult(
            resources=[
                Resource(name=uri, uri=uri, description=description)
                for uri, description in descriptions.items()
            ]
        )

    async def _read_resource(
        self,
        _context: ServerRequestContext,
        params: ReadResourceRequestParams,
    ) -> ReadResourceResult:
        uri = str(params.uri)
        try:
            if uri == "market_data://realtime":
                from quantx_infrastructure.database.connection import get_async_db
                from quantx_infrastructure.models.agent_runtime import (
                    RuntimeComponentHeartbeat,
                )

                heartbeat = None
                async for db in get_async_db():
                    heartbeat = await db.get(RuntimeComponentHeartbeat, "engine")
                    break
                content = json.dumps(
                    {
                        "status": heartbeat.status if heartbeat else "offline",
                        "engine": heartbeat.details if heartbeat else {},
                    },
                    ensure_ascii=False,
                )
            elif uri == "strategies://active":
                from quantx_infrastructure.database.connection import get_async_db
                from quantx_infrastructure.repositories.strategy_run_repository import (
                    StrategyRunRepository,
                )

                active = []
                async for db in get_async_db():
                    rows = await StrategyRunRepository(db).find_all_active_runs()
                    active = [row.to_dict() for row in rows]
                    break
                content = json.dumps(active, ensure_ascii=False, indent=2)
            elif uri == "account://info":
                content = json.dumps(
                    {"message": "Account info retrieval not yet implemented"},
                    ensure_ascii=False,
                )
            else:
                content = json.dumps(
                    {"error": f"Unknown resource: {uri}"},
                    ensure_ascii=False,
                )
        except Exception as exc:
            logger.error("Error getting resource %s: %s", uri, exc)
            content = json.dumps({"error": str(exc)}, ensure_ascii=False)
        return ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri=uri,
                    text=content,
                    mimeType="application/json",
                )
            ]
        )


def create_mcp_server() -> QuantXMCPServer:
    """Create and return MCP server instance"""
    return QuantXMCPServer()


async def start_mcp_server(transport: str = "stdio"):
    """
    Start MCP server

    Args:
        transport: Transport protocol ("stdio" or "sse")
    """
    logger.info("Starting QuantX MCP Server...")

    server = create_mcp_server()

    if transport == "stdio":
        # Run with stdio transport
        async with stdio_server() as (read_stream, write_stream):
            await server.app.run(
                read_stream,
                write_stream,
                server.app.create_initialization_options()
            )
    else:
        raise ValueError(f"Unsupported transport: {transport}")

    logger.info("QuantX MCP Server started")


async def start_background_mcp_server():
    """Start MCP server in background task"""
    logger.info("Starting background MCP server task")
    task = asyncio.create_task(start_mcp_server())
    return task


# Convenience function for CLI usage
async def main():
    """Main entry point for running MCP server"""
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    await start_mcp_server(transport)


if __name__ == "__main__":
    asyncio.run(main())
