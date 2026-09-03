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
设备统一使用 `http://192.168.5.6:8080`。API 内部只监听
`127.0.0.1:18081`。常用端点包括 `/graphql`、`/health/live`、
`/health/ready`、`/health/components` 和 `/ws/agent`。原生客户端在线
文档位于 `/docs/`；FastAPI 开发 Swagger 只在内部 API 端口的
`/_dev/api-docs` 提供。QMT Agent 的交易连接使用唯一 protocol `1.2` 的 `/ws/agent`，
唯一沪深行情连接使用 `/ws/agent/market` 和 `quantx.market.v2`。统一开发者中心覆盖 Web、原生客户端与
第三方 API。

交易页面的行情水位读取使用 `/health/runtime/market-data`，只转发并校验独立
Market Gateway 的供给健康快照，不触发 API 的账户、Engine 或 Prefect 全量探测。长期
可用性、延迟和事故历史不在 API 内实现，由独立 `quantx-monitor` 通过
`/monitor/*` 提供；它不参与 API readiness 或交易门禁。

网关健康响应必须显式携带 `component=market-gateway` 与
`protocol=quantx.market.v2`；API 和 Monitor 不会为缺失字段补默认值。日历与 Redis
检查共用一次 2 秒的单调时钟截止时间，Redis 新鲜度租约仍最后读取；API 的 HTTP
等待预算为 3 秒，包含传输余量。预算定义统一位于 `quantx_contracts.market_health`。

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
  `OFFLINE` 会删除租约。`marketData` 只表示网关供给健康：本进程持有匹配的活动
  QMT 行情连接、sequence 3 确认和完整快照，交易时段还要求当前水位的新鲜度租约。
  不依赖账户交易能力或 Engine；休市时允许租约自然过期，但连接与快照仍须有效。
- Engine 的消费水位、新鲜度与收敛检查归 `engine.marketConsumption`；消费异常使
  就绪的引擎组件降级，不会让正常的行情供给变红。实盘准入仍独立校验 Agent、
  行情供给和 Engine 权威水位。常见小 DELTA 在一个 Lua 内原子更新 Hash、状态、
  freshness 与广播；大 DELTA 短暂 `APPLYING` 时，状态与租约继续指向上一个完整提交
  fence。同一 stream/generation 下 Engine 为 `READY`、租约匹配且最近 3 秒持续推进时，
  API 头部暂时领先或正在提交都保持 `PASSED`，不会因移动中的全市场 sequence 相等条件
  反复关闭增仓。sequence 3 初始屏障、重同步和恢复仍要求精确收敛；推进停滞、租约过期、
  水位回退或身份不一致继续 fail-closed 为 `FAILED`，恢复同步为 `TRANSIENT`，休市健康
  链路为 `STANDBY`。

## GraphQL 耗时跟踪

做 T 回放的信号分类、精确版本读取、归档完整性和决策追溯契约见
[做 T 回放：信号与决策审计](TTRADE_REPLAY_EVIDENCE.md)。

每个 GraphQL HTTP 请求都会分别记录 `context`、`parse`、`validate`、`execute`、
`format` 和 `serialize` 阶段。`execute` 内的 resolver 以 GraphQL schema 的
`ParentType.field` 聚合 `count`、`total`、`max` 和错误次数；列表下同一字段解析多次
只占一个聚合项。SQL 统计与当前 GraphQL operation 关联，只导出语句类型和规范化
语句的 SHA-256 短指纹，不记录 SQL 原文、绑定参数或 GraphQL variables。

浏览器 Network 面板的 GraphQL 响应包含 `Server-Timing`，其中可直接查看阶段耗时、
SQL 总耗时和总耗时最高的六个字段。已认证请求显式携带
`X-QuantX-Debug-Timing: 1` 时，响应 `extensions.quantxTiming` 还会返回最多 100 个
字段聚合和 20 个 SQL 指纹聚合；Web 开发客户端会自动携带该请求头。并发 sibling
resolver 的 `total` 会相加，因此可能大于 `execute` 墙钟耗时，定位单次长尾时应同时
查看 `max`。

慢于 1 秒的 operation 会额外输出一条聚合日志，包含 request ID、各阶段、SQL 总耗时
和最慢字段。Prometheus 提供以下低基数指标，标签只使用固定阶段、SQL 类型以及
schema 的父类型/字段名，不使用 operation name、别名、请求参数或 request ID：

- `quantx_graphql_phase_duration_seconds`
- `quantx_graphql_field_resolver_invocations_total`
- `quantx_graphql_field_resolver_request_duration_seconds`
- `quantx_graphql_sql_statements_total`
- `quantx_graphql_sql_request_duration_seconds`

## 开发认证与交易审批

开发自动登录用户在 API 启动时会与 `AUTH_BOOTSTRAP_PERMISSIONS` 做一次仅增量的
权限同步；同步只在 `development` 生效，不删除人工授予的权限。权限实际发生
变化时会追加 `DEVELOPMENT_PERMISSION_SYNC` 认证审计
事件。开发环境可通过该配置授予 `trade:approve`，但 GraphQL 的账户实盘窗口和
实盘启用 mutation 仍会逐次校验该权限，不能用较宽松的自动化就绪状态替代。

旧单体资料集中保存在
[archive/legacy-monolith](archive/legacy-monolith/README.md)，不再与当前
操作指南混放。
