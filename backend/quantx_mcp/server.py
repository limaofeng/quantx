"""
MCP Server implementation for QuantX

This module implements the MCP server that exposes QuantX's capabilities
as MCP tools for AI agents to use.
"""

import asyncio
import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource, Tool, TextContent

from .tools import (
    MarketDataTools,
    StrategyTools,
    AccountTools,
    OrderTools,
    AnalysisTools
)

logger = logging.getLogger(__name__)


class QuantXMCPServer:
    """
    QuantX MCP Server
    
    Exposes QuantX trading system capabilities as MCP tools.
    """
    
    def __init__(self):
        self.app = Server("quantx-server")
        self.market_data_tools = MarketDataTools()
        self.strategy_tools = StrategyTools()
        self.account_tools = AccountTools()
        self.order_tools = OrderTools()
        self.analysis_tools = AnalysisTools()
        
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Setup MCP server handlers"""
        
        @self.app.list_tools()
        async def list_tools() -> list[Tool]:
            """List all available QuantX tools"""
            tools = []
            
            # Market data tools
            tools.extend(self.market_data_tools.get_tools())
            
            # Strategy tools
            tools.extend(self.strategy_tools.get_tools())
            
            # Account tools
            tools.extend(self.account_tools.get_tools())
            
            # Order tools
            tools.extend(self.order_tools.get_tools())
            
            # Analysis tools
            tools.extend(self.analysis_tools.get_tools())
            
            logger.info(f"Listed {len(tools)} tools")
            return tools
        
        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            """Handle tool calls"""
            try:
                logger.info(f"Tool called: {name} with args: {arguments}")
                
                # Route to appropriate tool handler
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
                    result = {
                        "error": f"Unknown tool: {name}",
                        "status": "error"
                    }
                
                # Convert result to MCP response
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(result, ensure_ascii=False, indent=2)
                    )
                ]
                
            except Exception as e:
                logger.error(f"Error calling tool {name}: {e}", exc_info=True)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "error": str(e),
                            "status": "error",
                            "tool": name
                        }, ensure_ascii=False, indent=2)
                    )
                ]
        
        @self.app.list_resources()
        async def list_resources() -> list[Resource]:
            """List available resources"""
            return [
                Resource(uri="market_data://realtime", description="Realtime market data summary"),
                Resource(uri="market_data://historical", description="Historical data summary"),
                Resource(uri="strategies://active", description="Active strategies"),
                Resource(uri="strategies://available", description="Available strategies"),
                Resource(uri="orders://pending", description="Pending orders"),
                Resource(uri="orders://history", description="Order history"),
                Resource(uri="account://info", description="Account info"),
                Resource(uri="account://positions", description="Account positions"),
            ]
        
        @self.app.read_resource()
        async def read_resource(uri: str) -> list[ReadResourceContents]:
            """Read a resource by URI"""
            try:
                if uri == "market_data://realtime":
                    # Get realtime market data summary
                    from core.realtime_manager import realtime_manager
                    content = json.dumps({
                        "status": "active",
                        "connections": len(realtime_manager.active_connections),
                        "symbols": list(realtime_manager.subscribed_symbols)
                    }, ensure_ascii=False)
                    return [ReadResourceContents(content=content, mime_type="application/json")]
                
                elif uri == "strategies://active":
                    # Get active strategies
                    from core.strategy_manager import strategy_manager
                    active = await strategy_manager.get_active_strategies()
                    return [ReadResourceContents(content=json.dumps(active, ensure_ascii=False, indent=2),
                                                 mime_type="application/json")]
                
                elif uri == "account://info":
                    # Get account information
                    # TODO: Implement account info retrieval
                    content = json.dumps({
                        "message": "Account info retrieval not yet implemented"
                    }, ensure_ascii=False)
                    return [ReadResourceContents(content=content, mime_type="application/json")]
                
                else:
                    content = json.dumps({
                        "error": f"Unknown resource: {uri}"
                    }, ensure_ascii=False)
                    return [ReadResourceContents(content=content, mime_type="application/json")]
                    
            except Exception as e:
                logger.error(f"Error getting resource {uri}: {e}")
                return [ReadResourceContents(content=json.dumps({"error": str(e)}, ensure_ascii=False),
                                             mime_type="application/json")]


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
