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
`/_dev/api-docs` 提供，生产环境关闭。QMT Agent 的交易连接使用 `/ws/agent`，
唯一沪深行情连接使用 `/ws/agent/market` 和 `quantx.market.v1`。统一开发者中心覆盖 Web、原生客户端与
第三方 API。

## 代码边界

- API mutation 只创建应用命令或持久化消息，不同步宣称成交。
- Agent 回报先进入 `agent_report_inbox`；Engine 消费后才推进订单和持仓。
- API 源码禁止导入 `miniqmt`、`xtquant`、`quantx_engine` 或 `quantx_worker`。
- GraphQL 契约变化后，通过 Caddy 公共入口运行前端 codegen。
- API 行情中继在 Redis 最新 tick Hash、stream 状态和二进制 Pub/Sub 原子提交
  后才 ACK Agent。首帧非快照、序号缺口、非法帧或 Redis 失败一律不 ACK，
  将 stream 置为失效并要求全量重建；不提供旧 whole JSON 双读或直通降级。
- `marketData=ready` 同时要求活动 Agent 行情连接、API 完整快照和 Engine
  水位一致；交易时段还要求两端最近 10 秒内收到并应用行情。

## 开发认证与交易审批

开发自动登录用户在 API 启动时会与 `AUTH_BOOTSTRAP_PERMISSIONS` 做一次仅增量的
权限同步；同步只在 `development` 生效，不删除人工授予的权限，也绝不在生产
环境执行。权限实际发生变化时会追加 `DEVELOPMENT_PERMISSION_SYNC` 认证审计
事件。开发环境可通过该配置授予 `trade:approve`，但 GraphQL 的受控窗口和
实盘启用 mutation 仍会逐次校验该权限，不能用较宽松的自动化就绪状态替代。

旧单体资料集中保存在
[archive/legacy-monolith](archive/legacy-monolith/README.md)，不再与当前
操作指南混放。
