"""
MCP Configuration for QuantX

This module contains MCP server configuration settings.
"""

from typing import Optional, List, Dict, Any
from pydantic import Field, BaseModel
from pydantic_settings import SettingsConfigDict
from config.settings import BaseSettings


class MCPToolConfig(BaseModel):
    """Individual tool configuration"""
    enabled: bool = True
    require_auth: bool = False
    rate_limit: Optional[str] = None
    max_concurrent: int = 10


class MCPSecurityConfig(BaseModel):
    """Security configuration for MCP"""
    enabled: bool = True
    authentication: str = "token"  # token, api_key, oauth
    authorization: str = "rbac"  # rbac, acl, none
    rate_limiting: bool = True
    audit_logging: bool = True
    allowed_tools: Optional[List[str]] = None  # Null = all tools
    denied_tools: List[str] = []


class MCPTransportConfig(BaseModel):
    """Transport configuration"""
    type: str = "stdio"  # stdio, sse, streamable-http
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/mcp"


class MCPSettings(BaseSettings):
    """MCP Server Settings"""
    
    # Basic settings
    server_name: str = "quantx-mcp-server"
    version: str = "1.0.0"
    enabled: bool = True
    
    # Server mode
    mode: str = "streamable-http"  # stdio (local), sse (HTTP), streamable-http
    
    # Transport
    transport: MCPTransportConfig = MCPTransportConfig()
    
    # Security
    security: MCPSecurityConfig = MCPSecurityConfig()
    
    # Tool configurations
    tools: Dict[str, MCPToolConfig] = {
        "market_data": MCPToolConfig(enabled=True, rate_limit="100/min"),
        "strategy": MCPToolConfig(enabled=True, require_auth=True, rate_limit="20/min"),
        "account": MCPToolConfig(enabled=True, require_auth=True, rate_limit="50/min"),
        "order": MCPToolConfig(enabled=True, require_auth=True, rate_limit="10/min"),
        "analysis": MCPToolConfig(enabled=True, rate_limit="30/min"),
    }
    
    # Performance
    max_concurrent_requests: int = 100
    request_timeout: int = 30  # seconds
    
    # Logging
    log_level: str = "INFO"
    log_requests: bool = True
    log_responses: bool = False
    
    # Features
    enable_resources: bool = True
    enable_prompts: bool = False  # Future feature
    
    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


# Global settings instance
mcp_settings = MCPSettings()


def get_mcp_settings() -> MCPSettings:
    """Get MCP settings instance"""
    return mcp_settings


def is_tool_enabled(tool_name: str) -> bool:
    """Check if a tool is enabled"""
    # Extract tool category from tool name
    category = tool_name.split("_")[0] if "_" in tool_name else tool_name
    
    if category in mcp_settings.tools:
        tool_config = mcp_settings.tools[category]
        return tool_config.enabled
    return True  # Default to enabled if not configured


def get_tool_config(tool_name: str) -> Optional[MCPToolConfig]:
    """Get configuration for a specific tool"""
    category = tool_name.split("_")[0] if "_" in tool_name else tool_name
    
    return mcp_settings.tools.get(category)
