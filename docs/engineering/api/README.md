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
- API 行情中继用容量 2、总计 64 MiB 原始字节上限的队列解耦 WebSocket 接收与
  严格有序的 Redis 提交；大帧在预留该预算后转到工作线程解码，不阻塞 event loop，
  解码异常会原样传播并释放预留。sequence 1 `SNAPSHOT` 分块写入 stream 专属
  staging Hash，最后
  原子执行 `RENAME + SYNCING state + binary publish`；sequence 2 收敛 `DELTA`
  是 pre-cut 连续性屏障（没有变化时可为空），通过 Redis Lua 的
  `stream_id/status/previous sequence` 校验后仍保持 `SYNCING`。Agent 收到它的
  ACK 后才启用有序捕获；强制 sequence 3 readiness-confirm 以同一 CAS 原子切换
  `READY`，真实回调从 sequence 4 起继续更新最新 tick、水位并发布原始批次。
  只有 Redis CAS commit 成功才 ACK Agent，旧连接的迟到写不能覆盖新 stream。
- 首帧非快照、序号缺口、快照外新代码、非法帧、Redis 失败或提交超时一律不
  ACK，并通过 `RESYNC` 使 stream 失效。Redis 最新 Hash 还按源时间拒绝旧 tick
  回退；不提供旧 whole JSON 双读、双写或不可靠直通降级。
- 每次批次 CAS commit 同时原子刷新 10 秒 Redis freshness lease；`SYNCING`、
  `OFFLINE` 会删除租约。`marketData=ready` 同时要求活动 Agent 行情连接、API
  完整快照、Engine 水位与 lease 的 stream/sequence 一致；交易时段租约过期立即
  关闭实时交易门禁。

## 开发认证与交易审批

开发自动登录用户在 API 启动时会与 `AUTH_BOOTSTRAP_PERMISSIONS` 做一次仅增量的
权限同步；同步只在 `development` 生效，不删除人工授予的权限，也绝不在生产
环境执行。权限实际发生变化时会追加 `DEVELOPMENT_PERMISSION_SYNC` 认证审计
事件。开发环境可通过该配置授予 `trade:approve`，但 GraphQL 的受控窗口和
实盘启用 mutation 仍会逐次校验该权限，不能用较宽松的自动化就绪状态替代。

旧单体资料集中保存在
[archive/legacy-monolith](archive/legacy-monolith/README.md)，不再与当前
操作指南混放。
