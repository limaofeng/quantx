# QuantX API

## 公共入口

开发和本机部署只公开 Caddy：

```text
http://127.0.0.1:8080
```

API 自身仅监听 `127.0.0.1:18081`，不得作为前端、codegen 或外部客户端的
稳定地址。Caddy 转发：

- `/docs/*`（静态客户端开发文档，不转发给 API）
- `/graphql`
- `/auth/*`
- `/health`、`/health/live`、`/health/ready`、`/health/components`
- `/metrics`
- `/ws/agent`
- `/agent/market-data/*`

## 健康检查

| 路径 | 语义 |
| --- | --- |
| `/health/live` | 只证明 API 事件循环可响应 |
| `/health/ready` | 按 `web/full` profile 检查必要组件 |
| `/health/components` | API、数据库、Engine、Prefect、Worker、Agent、行情和 AI Runtime 分项状态 |
| `/health` | `/health/ready` 的兼容别名 |

`full` profile 中，Prefect Worker、QMT Agent 连接和行情 capability 也必须
ready。QMT Agent 的组件健康表示进程与会话在线；账户对账、kill switch 和
交易能力由交易就绪检查独立判定，不会把在线 Agent 误报为离线。
开发启动若以 `QMT_AGENT_LAUNCH_STATE=BLOCKED` 明确跳过本地 Agent，组件聚合
必须覆盖数据库中尚未超过 90 秒的旧心跳：`qmtAgent` 与 `marketData` 返回
`blocked`、连接/在线/ready 设备数归零并附稳定原因码，`/health/ready` 保持
非就绪；`/health/live` 与非 QMT API 仍可用。

GraphQL `liveSafetyStatus` 进一步分成 `preparationReady`（账户观察、外部活动
分类和完整快照对账链路）与 `automationReady`（可申请自动执行）两个结论。
`PREPARING` 是健康的 `SHADOW` 准备阶段，`BLOCKED` 才表示准备链路本身
未通过；`ready` 保留为 `automationReady` 的兼容别名。
当本次进程收到 QMT 启动 `BLOCKED` 标记时，GraphQL 做 T readiness 同样覆盖
旧心跳：`agentStatus=BLOCKED`、实际 `agentMode=offline`，准备与自动执行结论及
`canActivateLive` 全部为 `false`；期望的全局启动模式仍保持 `live`，不会伪装成
`data-only`。

AI Runtime 在组件健康中仅返回脱敏状态、心跳年龄和已应用配置版本。它是可选
组件，即使处于 `disabled`、`unconfigured`、`offline` 或 `unavailable`，也不会
改变 QuantX 必需组件的 readiness。

## 用户认证

Web 使用 HttpOnly refresh cookie 和短期 access token：

```text
POST   /auth/web/session
POST   /auth/web/session/refresh
DELETE /auth/web/session
```

原生客户端使用显式 token 响应：

```text
POST   /auth/session
POST   /auth/session/refresh
GET    /auth/session
DELETE /auth/session
```

GraphQL HTTP 使用 Bearer token；GraphQL WebSocket 在
`connection_init.Authorization` 中发送同一短期 token。服务端按用户权限和
账户授权再次校验，前端状态不是安全边界。

GraphQL 写权限按领域拆分为 `portfolio:write`、`market:write`、
`orders:write`、`strategy:write`、`operations:write` 和 `agent:manage`。
高风险交易确认额外要求 `trade:approve`；旧 `mutation:write` 已停用。

## QMT Agent 本机连接接口

```text
POST   /auth/agent/enrollments
POST   /auth/agent/enrollments/exchange
POST   /auth/agent/token
DELETE /auth/agent/devices/{device_id}
WS     /ws/agent
PUT    /agent/market-data/{request_id}/chunks/{chunk_index}
```

登记码一次性且十分钟过期；服务端只保存登记码和设备密钥的摘要。设备密钥
由 Agent 写入 Windows Credential Manager，换取短期 JWT 后主动建立
WebSocket。

GraphQL 使用单一 `qmtAgentConnection` 视图返回当前 Agent、五段连接链路、
行情流与本地 journal 的非敏感指标，以及折叠的历史登记。Web 通过
`createAgentEnrollment` 发起安全交接，使用 `cancelAgentHandover` 取消；
新 Agent 只有在连接并完成账户对账、达到 `READY` 后，服务端才原子撤销旧
Agent 凭据。`revokeAgentDevice` 仍用于显式撤销当前连接。

XTData/XTTrading 心跳只上传 `CONNECTED / DISCONNECTED / DISABLED` 和受控
原因码，不上传 QMT 路径、端口、设备密钥或原始异常堆栈。该 Web 页面不提供
远程启动、重连或 MiniQMT 控制能力。

## 系统设置 GraphQL

`aiRuntimeSettings` 使用 `system-status:read`，返回全局非敏感期望配置、
Runtime 已应用版本和应用状态。`updateAiRuntimeSettings` 使用独立权限
`system-config:write`，并要求客户端提交 `expectedVersion`；版本冲突时拒绝覆盖，
客户端必须刷新后重试。

可动态修改的字段仅包括启用状态、模型、最大并发、最大轮次、最大工具调用数和
运行超时。API Key 只返回“是否已配置”，Tracing 和租约只读；三者都继续由服务端
环境管理，不进入 GraphQL、数据库或审计正文。

## 交易 mutation 语义

手工下单、撤单、策略交易、条件清仓、全局做 T 和国债逆回购都必须进入
统一应用命令/`TradeCommand` 链路。mutation 返回
`clientOrderId` 与排队状态，不得把“已排队”或 `command_ack` 表示成成交。

状态推进顺序：

```text
pending Order + outbox
  -> Agent command
  -> command_ack（仅投递）
  -> order/execution/delta report
  -> inbox
  -> Engine 收敛
```

## GraphQL codegen

schema 或 Web operation 变化后，保持 `web` profile 运行并执行：

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT="http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
npm run lint
npm run test:run
npm run build
```

生成类型是契约真源，不使用 `as any` 掩盖不一致。

同一轮还必须刷新在线客户端契约：

```powershell
npm run docs:contracts
```

发布文件位于 `/docs/contracts/`，包括 GraphQL SDL、v2 operation policy、
Client OpenAPI 与 Web OpenAPI。生产环境关闭运行时 OpenAPI、Swagger、
ReDoc、GraphiQL 和 GraphQL 内省。

## 卖出管理 GraphQL

统一读取入口为 `exitPlans`、`exitPlan`、`exitPlanEvents`、
`exitPlanCapabilities`、`exitPlanHoldingCapacity` 和
`exitPlanCostBasisCandidates`。写入入口为
`createManualExitPlan`、`updateManualExitPlan`、`setExitPlanEnabled`、
`cancelExitPlan`、`evaluateExitPlanNow`、`reconcileExitPlanCapacity` 和
`liquidatePositions`。

`createManualExitPlan.costBasis` 必填。成交委托模式只提交委托 ID，Engine 会
重新读取账户、股票、方向、成交数量与成交均价并冻结成本快照；手工模式提交的
`unitCostCny` 表示已包含买入费的每股全成本。`ExitPlanView.costBasis`、授权预览
和授权指纹使用同一快照。若 `capacityStatus=RECONCILE_REQUIRED`，新的 SELL 与
自动实盘授权均被阻止，客户端应展示原因并引导用户显式重新对账。

实盘人工计划或清仓计划产生待确认 SELL 后，客户端使用 `previewExitIntent`、
`confirmExitIntent` 或 `rejectExitIntent`。确认挑战只授权该意图再次进入统一
风控，不代表委托提交或成交。旧 `liquidatePosition` 与
`liquidateAllPositions` 保留为 `AVAILABLE_NOW + UNALLOCATED_ONLY` 兼容适配器。
