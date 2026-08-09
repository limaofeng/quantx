# QuantX API 工程指南

`apps/api` 是无状态的 FastAPI/Strawberry 边界进程。它只负责 HTTP、
GraphQL、Agent WebSocket Hub、认证、数据库连接和订阅桥接，不拥有策略
Engine、Prefect Worker 或 QMT SDK 生命周期。

## 本地运行

从仓库根目录统一启动：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
```

开发公开入口为 Caddy 的 `8080`：本机使用 `http://127.0.0.1:8080`，局域网
设备使用 `http://<开发机局域网 IP>:8080`。API 内部只监听
`127.0.0.1:18081`。常用端点包括 `/graphql`、`/health/live`、
`/health/ready`、`/health/components` 和 `/ws/agent`。原生客户端在线
文档位于 `/docs/`；FastAPI 开发 Swagger 只在内部 API 端口的
`/_dev/api-docs` 提供，生产环境关闭。

## 代码边界

- API mutation 只创建应用命令或持久化消息，不同步宣称成交。
- Agent 回报先进入 `agent_report_inbox`；Engine 消费后才推进订单和持仓。
- API 源码禁止导入 `miniqmt`、`xtquant`、`quantx_engine` 或 `quantx_worker`。
- GraphQL 契约变化后，通过 Caddy 公共入口运行前端 codegen。

旧单体资料集中保存在
[archive/legacy-monolith](archive/legacy-monolith/README.md)，不再与当前
操作指南混放。
