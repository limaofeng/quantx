# QuantX MCP Server 指南

本文档说明 QuantX MCP Server 的用途、配置、启动方式与排错建议。能力清单请参阅 [MCP_CAPABILITIES.md](./MCP_CAPABILITIES.md)。

## 什么是 MCP

MCP (Model Context Protocol) 是一种开放协议，允许 AI 应用（如 Claude、ChatGPT）通过标准化接口调用外部工具与数据。QuantX MCP Server 将 QuantX 的行情、策略、账户与订单等能力以 MCP 形式暴露给 AI 客户端。

## 快速开始

### 方式 1：随主应用自动启动（推荐）

```bash
cd F:\workspace\quantx\backend
python main.py
```

MCP Server 默认启用，会作为后台任务随主应用启动与关闭。

### 方式 2：HTTP 模式（与 FastAPI 同端口）

> 本项目已移除独立 `run_mcp.py`，HTTP 模式会挂载在主 FastAPI 端口。

在 `.env` 中配置：

```bash
MCP_MODE=streamable-http
MCP_TRANSPORT__PATH=/mcp
```

启动主服务：

```bash
cd F:\workspace\quantx\backend
python main.py
```

HTTP 模式默认挂载到：
- `POST/GET/DELETE http://<host>:<port>/mcp`（Streamable HTTP）

如需使用 SSE 兼容模式：

```bash
MCP_MODE=sse
MCP_TRANSPORT__PATH=/mcp
```

对应端点：
- `GET  /mcp/sse`
- `POST /mcp/messages/`

## 安装依赖

```bash
cd F:\workspace\quantx\backend
pip install mcp
```

## 配置

### 环境变量（.env）

```bash
MCP_ENABLED=true
MCP_MODE=streamable-http
MCP_LOG_LEVEL=INFO
```

### 工具权限与速率限制

在 `config/mcp_config.py` 中配置各工具的启用与权限：

```python
tools = {
    "market_data": MCPToolConfig(
        enabled=True,
        rate_limit="100/min"
    ),
    "order": MCPToolConfig(
        enabled=True,
        require_auth=True,
        rate_limit="10/min"
    ),
}
```

### 安全建议（生产环境）

```python
MCP_SECURITY_AUTHENTICATION = "token"
MCP_SECURITY_AUTHORIZATION = "rbac"
MCP_SECURITY_AUDIT_LOGGING = True
```

## Claude Desktop / 外部客户端集成（HTTP）

若使用 HTTP 方式接入 MCP（推荐）：

- MCP 端点：`http://<host>:<port>/mcp`
- 若启用 SSE 模式：`/mcp/sse` + `/mcp/messages/`

外部客户端只需将 MCP 地址指向 FastAPI 服务端口即可。

## Claude Desktop 集成（stdio，仅本地）

Claude Desktop 主要通过 stdio 方式连接 MCP。当前项目已推荐使用 HTTP 模式，
如需 stdio 连接，请自行编写启动脚本并确保 MCP_MODE=stdio。 

## 验证 MCP Server

推荐使用内置脚本检查集成状态：

```bash
cd F:\workspace\quantx\backend
python verify_mcp.py
```

## 故障排查

### 1) ImportError: No module named 'mcp'

```bash
pip install mcp
```

### 2) MCP Server 未启动

检查日志中是否包含：
- `启动 MCP Server...`
- `MCP Server 已启动`

如需临时禁用：
```bash
MCP_ENABLED=false
```

### 3) Claude Desktop 无法连接

请确认：
1. 配置文件路径是否正确
2. `command`/`args` 是否为真实路径
3. `PYTHONPATH` 是否正确
4. 重新启动 Claude Desktop

## 相关文档

- [MCP_CAPABILITIES.md](./MCP_CAPABILITIES.md) - MCP 能力清单
- [API.md](./API.md) - GraphQL API 文档
- [ARCHITECTURE.md](./ARCHITECTURE.md) - 系统架构
