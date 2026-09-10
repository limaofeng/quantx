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
- `/health/runtime/market-data`
- `/metrics`
- `/ws/agent`
- `/agent/market-data/*`

## 健康检查

| 路径                          | 语义                                                                    |
| ----------------------------- | ----------------------------------------------------------------------- |
| `/health/live`                | 只证明 API 事件循环可响应                                               |
| `/health/ready`               | 按 `web/full` profile 检查必要组件                                      |
| `/health/components`          | API、数据库、Engine、Prefect、Worker、Agent、行情和 AI Runtime 分项状态 |
| `/health/runtime/market-data` | 网关供给健康与水位的校验投影；不包含 Engine 消费或账户交易健康          |
| `/health`                     | `/health/ready` 的兼容别名                                              |

`full` profile 中，Prefect Worker、QMT Agent 连接和行情 capability 也必须
ready。QMT Agent 的组件健康表示进程与会话在线；账户对账、kill switch 和
交易能力由交易就绪检查独立判定，不会把在线 Agent 误报为离线。
开发启动若以 `QMT_AGENT_LAUNCH_STATE=BLOCKED` 明确跳过本地 Agent，组件聚合
必须覆盖数据库中尚未超过 90 秒的旧心跳：`qmtAgent` 返回 `blocked`，
连接/在线/ready 设备数归零并附稳定原因码；无活动行情连接的网关供给返回
`marketData.status=unavailable`，不得把 Redis 遗留 READY 当成在线，`/health/ready` 保持
非就绪；`/health/live` 与非 QMT API 仍可用。

`marketData` 是独立 Market Gateway `/health/ready` 的脱敏投影，不再重复返回
`marketGateway`。网关的 `/health/live` 只检查进程响应，`/health/ready` 检查本进程
QMT 行情连接、Redis 已提交快照及交易时段新鲜度；XTTrading/账户故障不影响正常
行情供给。健康响应契约位于 `quantx_contracts.market_health`，HTTP 200 表示 ready，
503 附固定 `reasonCode`，不含账户、连接 ID 或异常文本。

Engine 消费状态归 `engine.marketConsumption`；引擎心跳为 ready 但消费未就绪时，
组件返回 `degraded / ENGINE_MARKET_NOT_READY`。交易准入仍要求引擎与权威行情水位
收敛，不因供给健康拆分而放宽。Monitor 直接探测网关，HTTP RTT 不是行情传输延迟。

GraphQL `accountExecutionSafety` 是账户级实盘执行能力真源，以
`healthStatus=HEALTHY/BLOCKED/KILLED` 表示账户事实链路，以
`executionMode=OBSERVE_ONLY/REDUCE_ONLY/TRADING/KILLED` 表示当前可执行动作，
并分别返回 `canIncreaseRisk` 与 `canReduceRisk`。该状态不消费
`T_TRADE_LIVE_ENABLED`，不得因某个助手未启用而把账户健康误报为故障。
每个账户检查使用 `status=PASSED/STANDBY/FAILED`：`STANDBY` 只表示休市期间
权威行情水位已收敛但无需维持 10 秒新鲜度租约，不计入失败项；交易时段缺少
新鲜租约、任意时段水位不一致或 Agent 离线仍为 `FAILED`。风险增加命令在入队、
实际投递和 Agent 执行前继续要求交易时段内的新鲜权威行情。
`healthStatus` 是只包含上述三值的封闭枚举；查询、轮询或检查过程不得进入该枚举。
`validateTTradeLiveReadiness` 只负责做 T 的 `SHADOW/CANARY/LIVE` 灰度、助手开关
和自动确认能力；其 `PREPARING` 不再作为全局状态栏文案。
当本次进程收到 QMT 启动 `BLOCKED` 标记时，GraphQL 做 T readiness 同样覆盖
旧心跳：`agentStatus=BLOCKED`、实际 `agentMode=offline`，准备与自动执行结论及
`canActivateLive` 全部为 `false`；期望的全局启动模式仍保持 `live`，不会伪装成
`data-only`。

AI Runtime 在组件健康中仅返回脱敏状态、心跳年龄和已应用配置版本。它是可选
组件，即使处于 `disabled`、`unconfigured`、`offline` 或 `unavailable`，也不会
改变 QuantX 必需组件的 readiness。

长期状态历史由独立 `quantx-monitor` 记录。Caddy 公开的
`/monitor/api/v1/summary`、目标 history 和 incidents 仅返回固定目标 ID、状态、
延迟统计和稳定原因码，不返回连接串、凭据、内部地址或原始异常。该服务是观测面，
不反向控制 API readiness、QMT 状态或交易能力。

### 单指标状态与事故历史

Web 的 `/settings/status/:targetId/history` 复用以下只读接口：

| 接口                                             | 参数                                                                                 | 返回与用途                                                                                       |
| ------------------------------------------------ | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `GET /monitor/api/v1/summary`                    | `window=24h`                                                                         | 当前状态及明确标注的近 24 小时可用率、覆盖率和延迟分位数；支持顶部指标切换                       |
| `GET /monitor/api/v1/targets/{targetId}/history` | `range=24h\|7d\|30d\|90d\|1y`                                                        | 时间桶状态与 P50/P95 曲线；按范围聚合，不伪造无延迟采样组件的延迟                                |
| `GET /monitor/api/v1/incidents`                  | 同上 `range`，可选 `targetId`、`page`、`pageSize`，成对可选 `asOf` / `maxIncidentId` | `{range, page, pageSize, total, asOf, maxIncidentId, incidents}`，服务端分页读取完整范围内的事故 |

事故按 `opened_at DESC, id DESC` 稳定排序；匹配条件是开始时间不晚于查询截止，
且尚未恢复或恢复时间不早于范围起点，因此不会漏掉跨越范围边界的长事故。
不再把结果静默截断到 200 条。`page` 从 1 开始（上限 1,000,000），
`pageSize` 默认为 20（允许 1–100）；Web 提供 10/20/50 条选择。
超出末页返回空数组但保留真实 `total`；没有记录时 `total=0`。
未知目标返回 404，非法范围或分页参数返回 422。

首次响应给出带时区的 ISO `asOf` 和已入库事故 ID 上限 `maxIncidentId`（空库为 0）。
后续翻页必须成对回传，固定时间范围和 `id <= maxIncidentId` 的记录集合；上限、
总数和分页内容在同一写锁内读取。即使探测早于截止点开始、结果稍后才入库，
新事故也不会挤入已有分页。只传其中一个字段返回 422；`maxIncidentId` 为
0–9,007,199,254,740,991 的整数，`asOf` 不接受未来时间或无时区时间。
该边界不是历史状态快照：既有事故的恢复时间、最近原因仍可更新，
保留期清理也可能减少总数。刷新或切换目标/范围会开始新的查询截止点。
Web 将目标、范围、页码和每页条数保存在 URL；页面刷新后重新获取查询截止点。
局部失败重试只重发对应请求，保留页码和事故分页边界；顶部刷新回到第一页并
重新查询全部资源。切换指标或范围同时刷新当前概览，事故持续时间以事故响应的
`asOf` 计算，不借用概览生成时间。

“完整历史”指所选范围与实际保留期内的全部事故；默认事故保留期是一年。
范围切换作用于状态带、曲线和事故列表；概览指标及切换面板始终明确标注近 24 小时，
不将短期采样统计冒充全年统计。页面只读，不发起交易、对账或重启命令。

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
POST   /auth/agent/history-token
DELETE /auth/agent/devices/{device_id}
WS     /ws/agent
PUT    /agent/market-data/{request_id}/chunks/{chunk_index}
POST   /agent/market-data/{request_id}/complete
POST   /agent/market-data/{request_id}/fail
```

登记码一次性且十分钟过期；服务端只保存登记码和设备密钥的摘要。设备密钥
由 Agent 写入 Windows Credential Manager，换取短期 JWT 后主动建立
WebSocket。

历史上传、manifest 完成和失败通知由 Data API 18085 承接，使用
`/auth/agent/history-token` 签发的 `agent:history` 令牌；交易控制令牌不能用于这些端点。
Agent 独立缓存和续期历史凭证，交易会话换 token 不影响在途上传。历史任务派发目前
仍通过原控制会话，专用历史 WS 与采集许可尚待迁移；不得据此声称已消除全部业务 API 依赖。

开发环境的历史分区通过本机 Caddy 提交和查询：

```text
POST /market-data/internal/v1/demands
GET  /market-data/internal/v1/demands/{demand_id}
GET  /market-data/internal/v1/demands/{demand_id}/result
```

三个端点使用内部服务 Bearer token。`result` 返回 `HistoryDemandResult`：原需求和交付
ID、分区、源版本、存储版本、内容 SHA256、验证行数及验证时间。仅 REMOTE 需求关联的
LOCAL_VERIFIED 交付、VERIFIED 阶段和相符的发布证明可返回结果；尚不可用返回 404，
证明不一致或数据库不可用返回 503。该接口有 3 秒查询期限，不读取分片或重新扫描 Influx，
不返回 manifest、参考数据或本地文件路径。

开发范围请求以 `partition_proofs` 保留逐分区的结果证据，汇总 `records_received`、
`records_saved`、`records_verified` 与源 `data_versions`。`code_summaries` 在该范围结果
中仅包含标的、周期、行数；它不是 Agent 传输协议的 `bar_summary`，不构造无法由分区
摘要合并得到的整段键哈希。Agent 上传协议保持原有完整键摘要约束。

开发年度日历和独立因子参考导入使用同一内部鉴权边界：

```text
POST /market-data/internal/v1/reference-requests
GET  /market-data/internal/v1/reference-requests/{request_id}
```

提交只在 development 开放。请求为 `calendar`（SH、年度）或 `divid_factors`
（标的、起止日期），状态为 QUEUED、WAITING、VERIFIED、BLOCKED。接口只持久化请求并
返回状态，Data Worker 获取最多 2 MiB 的源快照、固定源内容，再在同一事务中导入、
回读和提交结果。每次网络期限 30 秒，累计最多 4 次执行，重启与取消不返还尝试。
永久无效来源直接阻塞；短暂网络故障持久化退避；提交相同请求不清除预算或终态。

空开发库的范围请求先提交年度日历，未完成时返回 DEVELOPMENT_REFERENCE_PENDING
及持久化引用；不会在 CLI 或 Prefect Flow 内下载、写入日历或独立因子。日历完成后
按完整返回的年度快照拆分分区。VERIFIED 结果为严格校验的日历快照，或带内容摘要的
因子验证行数；有明确覆盖证明的零因子结果可以成功。GET 不返回固定源对象，状态
查询不会重复导入或消耗尝试。此机制证明本地内容与源快照一致，不代替源日历的维护。

实时分钟归档使用内部服务 token，接收和验证分开：

```text
POST /market-data/internal/v1/archives
GET  /market-data/internal/v1/archives/{request_id}
```

POST 接收单标的、单分钟的一次修订，请求体最多 4096 字节、读取期限 3 秒；返回
202 / ACCEPTED 只表示 PostgreSQL 待办已提交。身份由持久化 Engine generation、
continuity_generation、stream_id、sequence、分钟和 sealed 构成，同身份不同内容返回
409。旧代次、新提交的倒序修订、同连续性代次换流、封口后同源继续修改均拒绝。
相同内容重放不重置预算，原 Engine 退出后仍可确认已接收身份，但不能新增归档。
待处理修订最多 20000 条，满时 429；超大请求 413；非法字段和时间 422。

GET 返回固定请求、WRITE / READBACK / VERIFIED / BLOCKED、写入和回读累计次数、
下一次重试时间、原因及证明。独立 Data Worker 在每次 IO 前持久化尝试，写入与回读
分别最多 4 次；正常写完只推进 READBACK，逐字段核验通过后才提交 VERIFIED。
Influx 使用独立 storage_version，迟到旧写入不覆盖新修订；最新接收修订尚未验证时，
发布选择不退回旧证明。阶段超时触发取消后仍等待实际 SDK 操作退出，不能将 30 秒
取消期限理解为强制终止外部写入的保证。

默认 `/market-data/internal/v1/history` 的 1m 读取已接入归档目录。开发端优先读取已
发布的固定历史版本；没有历史版本而有归档目录时，只读取最新且 VERIFIED 的分钟
版本，不读取无版本本地表。生产端具有整日源请求、目标日正覆盖、相同行数和内容
摘要的历史收据时，优先按既有 canonical 路径读取；否则归档覆盖其声明的分钟，
其余分钟仍按原历史路径读取。新修订未验证时同时隐藏该分钟的旧归档和旧原表值。

归档目录最多 1440 个分钟键和 4 MiB；存储合并前在 SQL 中排除被归档声明的分钟，
再取每路有界页，保证 keyset 分页不漏行。两路共享一个读取槽、10 秒期限及 4 MiB
响应批次预算。已发布归档缺行、版本或字段不符时拒绝返回，不降级到旧原表值。
这些页不证明全天完整；既有 canonical 表的外部写入隔离仍需随 writer 迁移完成。
Engine 队列、缺口对账和其他历史调用方仍待接入，现有 Engine 直接 writer 尚未删除。

GraphQL 使用单一 `qmtAgentConnection` 视图返回当前 Agent、五段连接链路、
行情流与本地 journal 的非敏感指标，以及折叠的历史登记。Web 通过
`createAgentEnrollment` 发起安全交接，使用 `cancelAgentHandover` 取消；
新 Agent 只有在连接并完成账户对账、达到 `READY` 后，服务端才原子撤销旧
Agent 凭据。`revokeAgentDevice` 仍用于显式撤销当前连接。

XTData/XTTrading 心跳只上传 `CONNECTED / DISCONNECTED / DISABLED` 和受控
原因码，不上传 QMT 路径、端口、设备密钥或原始异常堆栈。该 Web 页面不提供
本机进程启动、重连或 MiniQMT 控制能力。

`/ws/agent` 当前只接受 Agent 控制协议 `1.2`，不保留 1.1/1.2 双协议或
metadata-only owner 旁路。业务 owner 不进入 Agent wire：API/Engine 在持久化的
intent、pending、correlation、`trade_command_outbox` 和 runtime event 中保存并校验
`ExecutionOwnerRef(owner_type, owner_id)` 与 execution environment；`auto_exit_plans`
和 `TTradeBatch` 保存不可变 source execution owner。当前可路由的 runtime owner 仅为
`STRATEGY_RUN`、`EXIT_PLAN`、`MANUAL_COMMAND`，未知、未注册或 owner/environment 冲突
均 fail-closed。

Agent 命令 payload 是封闭契约：`PLACE_ORDER` 固定 10 个字段
（`command_kind`、`client_order_id`、`account_id`、`execution_mode`、`instrument_code`、
`side`、`price_type`、`limit_price`、`volume`、`expires_at`）；`CANCEL_ORDER` 固定 6 个
字段（`command_kind`、`client_order_id`、`account_id`、`execution_mode`、
`broker_order_id`、`expires_at`）。PLACE 只接受 `BUY/SELL`、`FIX_PRICE` 和有限正数
`limit_price`。QMT 原生调用使用空 `strategy_name`，remark 为 `qx:` 加
`client_order_id` 前 20 个字符。`command_ack` 只表示投递或 Agent 本地前置处理结果，
不表示券商受理或成交；ORDER/EXECUTION/DELTA 报告先进入持久化
`agent_report_inbox`，再由 Engine 依据 durable correlation 和 owner/environment 收敛。

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

手工委托请求的可恢复读取入口是 `manualOrderAttempts(accountId, limit)`，只返回当前
用户、解析后的唯一账户和 `bucket = manual` 的 `PendingTradeOrder`，并与同账户的
`TradeCommandOutbox` 做只读投影。它返回 `ManualOrderAttemptFeed`：`items`、
`totalCount`、`activeCount`、`requiresAttentionCount`、`truncated` 和 `asOf`；
`limit` 范围为 1–100，默认 50，排序为需核对、活动请求、已终结请求，再按入队时间
倒序。超过上限时必须依据 `truncated` 展示截断提示。

`ManualOrderAttempt.phase` 是服务端唯一阶段投影：`QUEUED`、`DELIVERED`、
`AGENT_ACKNOWLEDGED`、`BROKER_ORDER_CREATED`、`REJECTED_BEFORE_BROKER`、
`EXPIRED_BEFORE_BROKER`、`CANCELLED_BEFORE_BROKER` 和
`RECONCILE_REQUIRED`。排队、投递和 ACK 均不代表券商接受；只有无对账异常且已有
`brokerOrderId` 时才可显示券商委托已生成。状态矛盾、未知状态、无券商 ID 却出现
成交态，或明确需要对账的记录统一进入 `RECONCILE_REQUIRED`，前端不得自动重试或
把它猜测关联到券商委托。该查询不修改 Pending、Outbox、Order 或账户事实。

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
Client OpenAPI 与 Web OpenAPI。运行时只在 Dev 内部端口提供调试文档。

## 次日概率训练 GraphQL

研究工作台的公开训练边界为以下只读查询和研究写入 mutation：

```text
stockSelectionTrainingCapabilities
stockSelectionDatasetVersions(limit, offset)
previewStockSelectionTraining(input)
stockSelectionTrainingRuns(status, runKind, limit, offset)
stockSelectionTrainingRun(runId)
stockSelectionTrainingComparison(runIds)       # 2–5 个同坐标运行

startStockSelectionDevelopmentTraining(input, previewFingerprint, idempotencyKey)
startStockSelectionFinalEvaluation(parentRunId, idempotencyKey)
cancelStockSelectionTrainingRun(runId, expectedVersion, idempotencyKey)
```

`StockSelectionTrainingInput` 只接受 typed dataset/date/universe、backend 与 resource
字段；模型族、时间切分和效果门禁是服务端冻结配置。提交前必须使用当前输入得到的
`previewFingerprint`，预检 `blockers` 非空或后端 capability 不满足时，服务端和
Web 都拒绝提交。所有创建请求的 `created_by` 从认证 principal 的 `user_id`
派生，不能由客户端传入。

运行列表和详情只投影阶段、进度、hash、脱敏环境、指标/门禁摘要、队列原因和受控
错误。`source_reference`、文件路径、secret、raw exception 和 artifact directory
不属于公共 schema。活动运行由 Web 每 5 秒轮询；取消带状态版本和幂等键。

登记有双重门禁：数据库 `run_key` 是唯一真源，且对应安全目录中的严格 schema-v2
产物必须是 `FINAL_EVALUATION`、`SUCCEEDED`、hash/identity 一致、`registerable`
为真，结论只能是 `SHADOW_ELIGIBLE` 或 `ACTIVE_ELIGIBLE`。`DEVELOPMENT`、
`BLOCKED`、失败、重复或不完整证据均不能作为模型 bundle；人工 registry/stage
mutation 仍需 `ADMIN`。训练查询的 operation policy 为 `market:read` +
`WEB_ONLY`/`web-internal`，训练 mutation 为 `operations:write` +
`WEB_ONLY`/`web-internal`/`NON_TRADING_WRITE`，这些写入不会创建交易意图。

## 卖出管理 GraphQL

统一读取入口为 `exitPlans`、`exitPlan`、`exitPlanEvents`、
`exitPlanCapabilities`、`exitPlanHoldingCapacity` 和
`exitPlanCostBasisCandidates`。写入入口为
`createManualExitPlan`、`updateManualExitPlan`、`setExitPlanEnabled`、
`cancelExitPlan`、`evaluateExitPlanNow`、`reconcileExitPlanCapacity` 和
`previewLiquidation`、`confirmLiquidation`。清仓必须先预览固定持仓、可卖量、冲突和
执行模式，再用 `challengeId + confirmationToken` 二次确认；旧
`liquidatePositions` 不再属于公开 schema。

`deleteExitPlanHistory(planId)` 使用 `orders:write` 非交易写权限，仅允许
`COMPLETED` / `CANCELLED` 且无待成交委托的计划。删除通过幂等的
`PLAN_HISTORY_DELETED` 事件从 `exitPlans` 历史列表移除记录（过滤发生在分页前），
不删除底层计划、委托、成交、回放引用或审计事件，不改变 Engine 的计划状态和
按 ID / 策略运行读取。若计划重新进入非终态，列表仍必须展示，不能因历史删除
掩盖执行中计划。`exitPlan` 与 `exitPlanEvents` 继续支持按 ID 审计读取。

`createManualExitPlan.costBasis` 必填。成交委托模式只提交委托 ID，Engine 会
重新读取账户、股票、方向、成交数量与成交均价并冻结成本快照；手工模式提交的
`unitCostCny` 表示已包含买入费的每股全成本。`ExitPlanView.costBasis`、授权预览
和授权指纹使用同一快照。若 `capacityStatus=RECONCILE_REQUIRED`，新的 SELL 与
自动实盘授权均被阻止，客户端应展示原因并引导用户显式重新对账。

实盘人工计划或清仓计划产生待确认 SELL 后，客户端使用 `previewExitIntent`、
`confirmExitIntent` 或 `rejectExitIntent`。确认挑战只授权该意图再次进入统一
风控，不代表委托提交或成交。旧 `liquidatePosition` 与
`liquidateAllPositions` 保留为 `AVAILABLE_NOW + UNALLOCATED_ONLY` 兼容适配器。

## 独立做 T CANARY 发布确认

`previewTAssistantLiveRelease(request)` 与 `confirmTAssistantLiveRelease(challengeId,
confirmationToken)` 仅面向原生设备会话，要求唯一账户、`t-trade:control` 和
`trade:approve`，签发及消费时均复验会话。预览绑定原 PAPER execution、目标配置 hash、
已审核 P5 报告/policy hash、评估 UUID、配置头版本及维护窗口；不接受文件路径或审核人参数。
凭据有效期最多 60 秒。确认与 Engine outbox 入队同事务，重复确认返回原命令 ID。
`RELEASE_QUEUED` 仅表示入队。原设备通过 `tAssistantLiveReleaseStatus(challengeId)`
读取 AWAITING_CONFIRMATION、EXPIRED、PENDING、PROCESSING、FAILED 或 SUCCEEDED。
查询重新校验会话及挑战归属；SUCCEEDED 必须匹配持久化审批、目标执行与创建事件，
同时返回 executionId/executionStatus。证据冲突返回 UNKNOWN，失败只返回脱敏 reasonCode。
SUCCEEDED 表示发布命令完成，不表示预热完成或已经允许入场。

Engine 使用显式 `T_ASSISTANT_EVALUATION_ROOT/<evaluationId>`，重新核验 P5 文件、事实链、
指标和目标交易策略后创建 WARMING 执行。该根目录须在部署端配置且只放已审核的评估产物。
预览本身不代表 P5 通过；服务不启用实盘开关，预热、账户与 Agent 就绪仍需后续检查。

原生客户端可通过 `tAssistantLiveReleaseOperations(accountId, limit)` 找回当前用户、账户、
设备最近的发布操作（默认 20 条，最多 50 条，按创建时间倒序）。输出仅包含挑战 ID、账户、
目标配置 ID 和带时区的创建时间，不返回 token、完整挑战 payload 或凭据摘要。
该接口复验当前会话和每条请求的签名；恢复后仍须调用状态查询核验原命令结果，不能再次确认。

开发环境可生成便于原生导入的发布请求文件：

```sh
conda run -n quantx python ops/prepare_t_assistant_release.py \
  --environment development --account-id '<账户ID>' \
  --source-execution-id '<当前PAPER执行ID>' --config-version-id '<目标配置版本ID>' \
  --expected-config-hash '<已核对配置摘要>' \
  --expected-report-hash '<已审核P5报告摘要>' --expected-policy-hash '<已审核准入规则摘要>' \
  --evidence-directory '<评估UUID目录>' \
  --window-start '<带时区ISO时间>' --window-end '<带时区ISO时间>' \
  --output '<新文件路径.json>'
```

工具只读取本地开发配置/数据库并重新核验证据，文件已存在时拒绝覆盖；不写审批和命令。
格式为 `quantx.t-assistant-release-request.v1`，只包含公开发布请求的十项参数。
原生控制页使用“导入发布请求”选择该文件（最多 16 KiB）；导入后获取服务端预览，
核对配置、证据和窗口，再单独进行生物确认。文件不是发布凭据，篡改、过期或配置头变化
仍由服务端拒绝；Engine 在消费确认时再次核验正式证据。该 CLI 当前仅提供开发环境入口。

原生做 T 控制页的“账户执行窗口”和“紧急熔断”调用账户级
`accountExecutionSafety` → `previewAccountExecutionControl` → `confirmAccountExecutionControl`。
账户动作绑定 `stateVersion`，独立于做 T 灰度 `policyVersion`；建立窗口绑定最新快照，
熔断只提交处置原因。需要 `strategy:read`、`account-execution:control`、`trade:approve`，
且确认前后均检查原设备会话。仅 APPLIED 且账户安全结果符合动作时展示已生效。
