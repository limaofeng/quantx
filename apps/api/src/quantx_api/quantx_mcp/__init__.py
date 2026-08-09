"""
MCP Server Module for QuantX

This module provides MCP (Model Context Protocol) server functionality,
allowing AI agents to interact with QuantX's trading system.
"""

from .http import create_mcp_http_app, is_http_mode_enabled
from .server import create_mcp_server, start_background_mcp_server, start_mcp_server
from .tools import (
    AccountTools,
    AnalysisTools,
    MarketDataTools,
    OrderTools,
    StrategyTools,
)

__all__ = [
    "create_mcp_server",
    "start_mcp_server",
    "start_background_mcp_server",
    "create_mcp_http_app",
    "is_http_mode_enabled",
    "MarketDataTools",
    "StrategyTools",
    "AccountTools",
    "OrderTools",
    "AnalysisTools",
]

__version__ = "1.0.0"
