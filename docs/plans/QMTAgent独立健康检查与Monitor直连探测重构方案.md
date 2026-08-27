# QMT Agent 独立健康检查与 Monitor 直连探测重构方案

> 状态：待实现
>
> 编写日期：2026-08-28
>
> 接续基线：`main@0814db55ad65`
>
> 主要实施环境：Windows QMT 主机；Monitor、API 与 Web 联调仍在 Mac QuantX
> 主机完成

## 1. 目标与结论

当前 QMT Agent 是独立运行在 Windows 上的远程执行进程，但 QuantX Monitor
没有直接访问该进程。Monitor 只从 Mac API 的 `/health/components` 读取
QMT Agent 聚合状态，因此能显示会话、心跳和对账语义，却不能获得
Mac Monitor 到 Windows Agent 的独立探测延迟，状态页只能显示 `N/A`。

本次重构在 Windows QMT Agent 内增加一个专用只读 HTTP 健康监听器，由 Mac
Monitor 直接访问并采集真实 HTTP RTT。QMT Agent 最终状态仍同时使用服务端会话
语义，不能用一个 HTTP 200 替代 API/Engine 对连接、心跳和完整快照对账的权威
判断。

最终证据链固定为：

```text
Windows QMT Agent /health/ready 可达及其 HTTP RTT
                         +
Mac API 对 WebSocket 会话、心跳、账户快照和对账的语义状态
                         |
                         v
             Monitor 中唯一的 QMT Agent 状态与延迟样本
```

本次调整后的边界是：

- QMT Agent 的交易控制、行情、历史上传和报告链路继续只建立出站连接。
- QMT Agent 不开放订单、撤单、配置、凭据、日志或通用管理接口。
- QMT Agent 允许开放专用、只读、固定响应契约的健康检查接口。
- 健康检查只服务于观测，不得参与打开实盘能力门或推进任何交易状态。

## 2. 已确认决策

| 项目             | 决策                                                                    |
| ---------------- | ----------------------------------------------------------------------- |
| 健康监听         | 由现有 QMT Agent 进程承载，不新增独立 Windows 服务                      |
| 端口             | 固定使用 TCP `18084`，与 Mac Monitor 的 `18083` 区分                    |
| 路径             | 只提供 `GET /health/live` 和 `GET /health/ready`                        |
| 网络范围         | 不按 Mac IP 或 CIDR 做来源限制，不增加 `QMT_AGENT_HEALTH_ALLOWED_CIDRS` |
| Windows Firewall | 仅在 Windows `Private` 网络配置文件开放 `18084`，不限定单一远端 IP      |
| 响应内容         | 只返回规范化状态、版本、运行时间和稳定原因码，不返回敏感明细            |
| 延迟定义         | Monitor 请求 Windows `/health/ready` 的端到端 HTTP RTT                  |
| 状态定义         | Windows 本地健康与 API 服务端语义取更差结果                             |
| 交易门禁         | 完全不读取 Monitor 结果，继续使用既有 API/Engine 权威状态               |
| 历史数据         | 不回填旧延迟；部署后的新样本自然形成趋势                                |
| 兼容策略         | 原子切换唯一新契约，不保留 `derived` 旧字段或伪延迟降级分支             |

`18084` 是独立监听端口，但不是独立监控进程。健康监听器必须与 Agent 主运行时
共用生命周期和只读状态；如果 XTData 调用长期占用 GIL、Agent 事件循环失去响应，
健康请求应自然超时，从而真实反映该执行进程不可用。

## 3. 非目标

- 不允许 Monitor 下发交易、重连、重启、对账或配置命令。
- 不新增远程 Windows 进程管理、WinRM 或通用 `/metrics` 接口。
- 不把健康端点变成 QMT、XTData 或 XTTrading 的代理。
- 不上传券商账号、QMT 路径、Windows 用户信息、设备密钥或原始异常。
- 不改变 QMT Agent 协议 `1.1`、交易命令、报告 inbox/outbox 或成交真源。
- 不改变 Market Gateway `quantx.market.v2` 行情协议。
- 不将直接健康探测结果写入任何交易门禁、订单路由或风险决策。
- 不为多 Agent、多账户或公网探测增加配置和抽象。

## 4. 当前实现基线

### 4.1 QMT Agent

- `apps/qmt-agent` 当前只依赖 `quantx-contracts` 和客户端类库，不启动 HTTP
  服务器。
- 控制面通过 `/ws/agent` 建立 Agent 到 API 的出站 WebSocket。
- Agent 每 30 秒发送一次 `heartbeat`，API 返回 `heartbeat_ack`。
- Windows 侧已有独立进程 watchdog；它解决本机僵尸进程回收，不提供跨主机
  应用健康接口。
- 行情链已经采集 `market_stream_ack_latency_ms`，但该值是行情批次 ACK 延迟，
  不能冒充控制面或健康接口 RTT。

### 4.2 Monitor

- `apps/monitor` 每 30 秒执行固定目标探测，并将样本写入独立 SQLite。
- Market Gateway 等目标使用直接 HTTP probe，因此存在真实 `latency_ms`。
- Engine、Worker、QMT Agent、行情服务和 AI Runtime 由一次
  `/health/components` 快照派生。
- `RuntimeSnapshotProbe` 为派生结果只写状态，不写延迟。
- 目标公共契约只有 `derived: boolean`，无法表达“状态是组合语义、延迟来自直接
  探测”的 QMT Agent。

### 4.3 Web

- `/settings/status` 对 `latencyMs=null` 显示 `N/A`。
- 选中 `derived=true` 的目标时，页面明确不绘制独立延迟趋势。
- QMT Agent 需要从纯派生目标改为组合目标，Engine、Worker、行情服务和
  AI Runtime 继续保留 `N/A`。

## 5. 健康接口契约

### 5.1 共享 DTO

在 `packages/contracts/src/quantx_contracts/` 新增 QMT Agent 健康 DTO。建议文件：

```text
packages/contracts/src/quantx_contracts/agent_health.py
```

唯一响应版本使用 `schema_version=1`。建议字段：

```json
{
  "schema_version": 1,
  "status": "ready",
  "reason_code": null,
  "agent_version": "0.1.0",
  "protocol_version": "1.1",
  "mode": "live",
  "uptime_seconds": 1234.5,
  "control_connection_status": "connected",
  "reconciliation_status": "ready",
  "xtdata_status": "connected",
  "xttrading_status": "connected",
  "market_stream_status": "ready",
  "observed_at": "2026-08-28T01:23:45.678Z"
}
```

固定状态词汇：

- 顶层：`ready / degraded / unavailable`；
- 控制连接：`connected / disconnected`；
- 对账：`ready / reconciling`；
- XTData、XTTrading：`connected / disconnected / disabled`；
- 行情流：复用当前 `READY / SYNCING / STALE / OFFLINE` 语义，在 JSON 中统一
  输出小写。

稳定本地原因码至少覆盖：

- `CONTROL_CONNECTION_OFFLINE`
- `TRADING_RECONCILING`
- `XTDATA_UNAVAILABLE`
- `XTTRADING_UNAVAILABLE`
- `MARKET_STREAM_NOT_READY`

不得把 Python 异常类名、异常文本或 XTQuant 原始错误写入响应。现有 API 侧稳定
原因码 `REMOTE_AGENT_OFFLINE`、`REMOTE_AGENT_SESSION_STALE`、
`REMOTE_AGENT_NOT_RECONCILED` 和 `REMOTE_AGENT_ACCOUNT_MISMATCH` 继续用于
服务端语义，两组原因码不能混写。

### 5.2 `GET /health/live`

只证明 QMT Agent 主事件循环能够接收并响应 HTTP 请求：

- 正常返回 HTTP 200；
- 响应只含 `status=alive`、`component=qmt-agent`、`schema_version` 和
  `observed_at`；
- 不因为 WebSocket 未连接、非交易时段或行情未就绪而返回失败。

### 5.3 `GET /health/ready`

返回完整但脱敏的 `QmtAgentHealthSnapshot`：

- 本地状态为 `ready` 时返回 HTTP 200；
- 本地状态为 `degraded` 或 `unavailable` 时返回 HTTP 503；
- 连接可达但 503 的请求仍是有效 RTT 样本；
- 只允许 `GET`，其他方法返回 405；
- 不启用 OpenAPI、Swagger、目录浏览或任意查询目标。

本地 `ready` 只表示 Agent 自己报告的运行条件成立。服务端是否接受该会话为
READY，仍由 API/Engine 的完整快照应用、API instance、session 和账户匹配状态
决定。

readiness 必须按 Agent 当前模式解释：`live` 要求 XTTrading 已连接；
`data-only` 中 XTTrading 为 `disabled` 是合法状态，不能因此把健康接口判为失败；
`paper` 只检查该模式真实使用的本地执行能力。禁止为了统一响应而臆造一个所有模式
都必须满足的 XTTrading 条件。

## 6. QMT Agent 实现计划

### 6.1 新增健康状态投影

在 `AgentRuntime` 外建立只读的 `AgentHealthState` 或等价投影。运行时只在明确
状态转换点更新它：

- 控制 WebSocket 鉴权完成、断开和重连；
- 完整账户快照进入或完成对账；
- XTData、XTTrading 初始化成功或失败；
- 行情流进入 `SYNCING/READY/STALE/OFFLINE`；

主动 emergency stop、账户增仓授权和 kill switch 属于交易安全状态，不是进程或
通信健康，不能单独把 `/health/ready` 判为失败。相关能力继续由现有交易安全入口
展示和裁决。

健康 HTTP handler 只能读取一次不可变快照，不得调用 broker、XTData、XTTrading、
数据库或网络，也不得触发一次新的对账。

### 6.2 新增 ASGI 健康监听器

建议新增：

```text
apps/qmt-agent/src/quantx_qmt_agent/health_server.py
```

实现约束：

- 使用最小 ASGI 应用；QMT Agent 只需增加 `uvicorn` 运行依赖，不必引入完整
  FastAPI 应用层。
- 默认监听 `0.0.0.0:18084`，允许通过 `QMT_AGENT_HEALTH_HOST` 和
  `QMT_AGENT_HEALTH_PORT` 显式覆盖。
- 不读取系统代理，不发起任何外部请求。
- 禁用访问日志中的查询内容；正常健康采样不得每 30 秒污染 Agent 日志。
- 监听器绑定失败、任务意外退出或端口被占用时，使 Agent 主进程退出，由现有
  supervisor 重启；禁止静默关闭观测后继续运行实盘 Agent。
- 正常关闭 Agent 时同步关闭 Uvicorn，不能留下端口或孤立任务。

修改 `_run_runtime_guarded`，把 Agent runtime、process watchdog heartbeat 和健康
server 纳入同一结构化并发边界。任一关键任务意外结束都应取消其余任务并回到统一
监督器，不得创建后台悬空 task。

### 6.3 CLI 与 Windows 服务配置

需要修改：

- `apps/qmt-agent/src/quantx_qmt_agent/main.py`
- `apps/qmt-agent/pyproject.toml`
- `ops/windows/quantx-qmt-agent.xml`
- Windows 安装/模板渲染所在的 `ops/quantx.ps1`

新增运行配置：

```text
QMT_AGENT_HEALTH_HOST=0.0.0.0
QMT_AGENT_HEALTH_PORT=18084
```

WinSW 服务描述不再使用绝对的 `Outbound-only`，应改成“交易与行情只出站，另有
只读健康监听”。安装逻辑为 Windows `Private` 网络配置文件开放 TCP `18084`，
不增加 RemoteAddress/Mac IP 限制。不能在普通代码验证中执行
`ops/quantx.ps1 install`。

## 7. Monitor 实现计划

### 7.1 配置

在 `MonitorSettings` 增加唯一目标地址：

```text
MONITOR_QMT_AGENT_HEALTH_URL=http://<windows-host>:18084
```

要求：

- 必须是无凭据、无查询、无 fragment 的绝对 HTTP(S) 根地址；
- 不接受重定向；
- 不从 WebSocket remote address 自动推测 URL；
- 不在公共 Monitor API、日志或异常文本中输出完整内部 URL；
- Windows 主机应使用稳定 DNS 名或 DHCP 保留地址。

### 7.2 专用直接探测器

建议新增：

```text
apps/monitor/src/quantx_monitor/probes/qmt_agent.py
```

`QmtAgentHealthProbe` 请求 `/health/ready` 并：

- 使用 Monitor 现有 `httpx.AsyncClient(trust_env=False)`；
- 禁止 redirect；
- 使用现有 HTTP timeout；
- 记录请求开始到响应完成的单调时钟 RTT；
- 对 HTTP 200 和 503 都解析共享 DTO；
- 对连接错误、超时、HTTP 403/404、schema 不匹配和非法 JSON 使用稳定原因码；
- 只有收到合法健康响应时写 RTT，连接失败时保持 `latency_ms=null`。

Monitor 侧原因码建议固定为：

- `QMT_HEALTH_CONNECT_ERROR`
- `QMT_HEALTH_TIMEOUT`
- `QMT_HEALTH_HTTP_STATUS`
- `QMT_HEALTH_PROTOCOL_ERROR`
- `QMT_HEALTH_SCHEMA_MISMATCH`

### 7.3 与 API 语义快照组合

不能让直接 probe 和 `RuntimeSnapshotProbe` 各自写一条 `qmt-agent` 样本。建议：

1. 同一轮并发执行 Windows 直接探测和现有直接目标。
2. 调用 API `/health/components` 得到服务端 QMT 语义状态。
3. 使用纯函数 `combine_qmt_agent_probe(direct, semantic)` 生成唯一结果。
4. 只把组合结果传给 `MonitorStorage.record_results()`。

组合规则：

| Windows 直接探测   | API 语义              | 最终状态      | 延迟     |
| ------------------ | --------------------- | ------------- | -------- |
| 连接/协议失败      | 任意                  | `unavailable` | `null`   |
| 本地 `unavailable` | 任意                  | `unavailable` | 直接 RTT |
| 本地 `degraded`    | `healthy`             | `degraded`    | 直接 RTT |
| 本地 `ready`       | `degraded`            | `degraded`    | 直接 RTT |
| 本地 `ready`       | `unavailable/unknown` | `unavailable` | 直接 RTT |
| 本地 `ready`       | `healthy`             | `healthy`     | 直接 RTT |

原因码优先级：

1. Windows 健康端点的传输或协议错误；
2. API 服务端会话、心跳和对账原因；
3. Windows 本地 readiness 原因。

API 语义缺失时不得因 Windows HTTP 200 把 QMT Agent 提升为健康。反过来，API
仍保留旧心跳但 Windows 健康端点已经不可达时，也必须立即显示不可用。现有
“连续两次失败打开事故、连续两次成功关闭事故”的存储防抖继续适用。

### 7.4 目标来源契约

把 `TargetDefinition.derived: bool` 原子替换为明确枚举：

```text
probe_kind = direct | derived | composite
```

映射为：

| 目标                                                                 | `probe_kind` |
| -------------------------------------------------------------------- | ------------ |
| PostgreSQL、Redis、InfluxDB、Prefect、Web、文档、API、Market Gateway | `direct`     |
| Engine、Worker、行情服务、AI Runtime                                 | `derived`    |
| QMT Agent                                                            | `composite`  |

Monitor 公共 API 同步将 `derived` 替换成 `probeKind`，不保留两个字段并存的兼容
期。SQLite 不必因为该展示元数据迁移 schema；目标定义仍来自代码中的固定列表。

## 8. Web 实现计划

修改：

- `apps/web/src/features/system/monitor-api.ts`
- `apps/web/src/features/settings/components/ServiceStatusPanel.tsx`
- 对应组件测试

要求：

- TypeScript 契约使用 `probeKind: 'direct' | 'derived' | 'composite'`。
- QMT Agent 卡片显示 Windows 健康端点 RTT，不再显示 `N/A`。
- QMT Agent 详情显示 P50/P95 和延迟趋势。
- QMT Agent 说明改为“状态综合 Windows 健康端点与服务端会话/对账语义；延迟为
  Monitor 到 Windows Agent 的健康探测 RTT”。
- Engine、Worker、行情服务和 AI Runtime 继续显示“来自语义快照，不生成虚假的
  独立延迟”。
- 历史窗口中部署前的 `null` 样本保持空白，不连接或补造历史数据。
- `N/A` 仍用于确实没有独立测量值的目标，不能全局替换为 0。

本次不修改 GraphQL schema 或查询，因此不需要运行 GraphQL codegen；如果实际实现
意外触及 GraphQL，必须按仓库规则通过 Caddy 公共入口重新 codegen 并完成全套前端
验证。

## 9. 测试计划

### 9.1 Contracts

在 `tests/contracts/` 覆盖：

- v1 正常 DTO；
- 非法状态和 schema version 拒绝；
- JSON 字段中不存在账户、路径、凭据和原始异常；
- 时间、运行时长和枚举边界。

### 9.2 QMT Agent

建议新增 `tests/qmt_agent/test_health_server.py`，覆盖：

- `/health/live` 返回 200；
- 本地完全就绪时 `/health/ready` 返回 200；
- 未连接、对账中、XTData/XTTrading 异常和行情未就绪返回 503 与稳定原因码；
- 只允许 GET；不存在 OpenAPI 和任意管理路径；
- handler 不调用 broker 或 XTQuant；
- 响应不泄漏账户、设备 ID、remote address、QMT 路径或异常；
- 健康 server 意外退出会结束主运行边界；
- 正常关闭不残留 task。

继续运行依赖边界测试，确保 QMT Agent 仍不导入服务端 ORM、Repository、API 或
Engine。

### 9.3 Monitor

建议新增 `tests/monitor/test_qmt_agent_probe.py`，并扩展 scheduler、snapshot、storage
和 API 测试：

- 200/503 都记录真实 RTT；
- timeout/connect/schema/JSON/HTTP 错误映射稳定；
- 所有组合状态矩阵；
- 每轮只保存一个 QMT Agent 样本；
- 防抖、事故打开和恢复仍正确；
- summary 返回 `probeKind=composite` 和非空 `latencyMs`；
- history 生成 QMT Agent P50/P95；
- 公共响应不包含 Windows 健康 URL。

### 9.4 Web

扩展 Service Status 测试：

- `composite` QMT Agent 显示当前 RTT；
- 有历史样本时显示趋势和 P50/P95；
- QMT Agent 显示组合证据说明；
- `derived` 目标继续显示 `N/A`；
- 部署前空样本不会被补成 0；
- unavailable/degraded 状态与延迟展示互不覆盖。

## 10. 验证命令

先执行定向测试：

```powershell
python -m pytest tests/contracts/test_agent_protocol.py
python -m pytest tests/qmt_agent/test_health_server.py
python -m pytest tests/qmt_agent/test_dependency_boundary.py
python -m pytest tests/monitor/
python -m pytest tests/api/unit/
```

再从仓库根目录执行边界与前端验证：

```powershell
python -m pytest tests/
npm run check
npm run lint
npm run test:run
npm run build
```

禁止事项：

- 不运行真实交易、真实下单或券商 E2E。
- 不手工并行启动第二个 QMT Agent。
- 不用 `ops/quantx.ps1 install` 做普通代码验证。
- 不用 `as any` 或兼容字段绕过前后端契约切换。

## 11. Windows 联调与验收顺序

代码和自动化测试通过后，只有在用户明确授权部署验证时才执行以下步骤：

1. 确认 Windows 网络配置文件为 `Private`，并确认 `18084` 未被其他进程占用。
2. 通过统一运维入口停止现有 QMT Agent，禁止手工并行运行。
3. 更新依赖和服务配置；创建允许 Private profile 入站 TCP `18084` 的防火墙规则，
   不限定 RemoteAddress。
4. 启动唯一 QMT Agent。
5. 在 Windows 本机验证：

   ```powershell
   Invoke-RestMethod http://127.0.0.1:18084/health/live
   Invoke-WebRequest http://127.0.0.1:18084/health/ready
   ```

6. 在 Mac 主机验证 `http://<windows-host>:18084/health/ready` 可达，并记录 HTTP
   状态与耗时。
7. 单独重启 Monitor，使其读取 `MONITOR_QMT_AGENT_HEALTH_URL`。
8. 等待至少两个 30 秒采样周期，确认防抖状态收敛。
9. 检查 `/monitor/api/v1/summary?window=24h`：QMT Agent 为
   `probeKind=composite`，`latencyMs` 为有限正数。
10. 打开 `/settings/status`，确认 QMT Agent 当前延迟和趋势出现，其他派生组件仍为
    `N/A`。
11. 断开或停止 Windows Agent 做一次受控故障演练，确认直接探测在两个周期内打开
    事故；恢复后连续两个成功周期关闭事故。
12. 完整 Dev 实盘验收仍必须确认唯一账户、协议 `1.1`、QMT `ready`、快照新鲜度和
    `liveTrading` 门状态，不能只检查 HTTP 健康端点。

## 12. 验收标准

- Windows QMT Agent 由原进程提供 `18084` 健康端点，没有新增独立服务。
- 局域网监控客户端不受固定 Mac IP 限制，可访问只读健康端点。
- `/health/live` 与 `/health/ready` 不包含敏感信息或业务操作能力。
- Monitor 每周期只生成一条 QMT Agent 样本。
- QMT Agent 的 `latencyMs` 是 Mac Monitor 到 Windows Agent 的真实 HTTP RTT。
- Windows endpoint 不可达时，即使 API 尚保留旧心跳，QMT Agent 最终也不会显示
  `healthy`。
- Windows endpoint 就绪但 API 未完成对账时，QMT Agent 仍显示
  `degraded/unavailable`。
- QMT Agent 页面显示当前延迟、P50/P95 与趋势；纯派生组件仍显示 `N/A`。
- Monitor 结果不参与实盘能力、订单路由、风控或成交状态推进。
- Contracts、QMT Agent、Monitor、Web、运维配置和文档在同一轮原子切换。
- 所有要求的自动化验证通过，最终改动按功能合理提交，工作树干净。

## 13. 建议实施拆分

为降低跨 Windows/Mac 联调风险，按以下顺序实现，但最终部署必须一次切换到唯一新
设计：

1. **共享契约与 Windows 健康服务**：DTO、状态投影、ASGI server、生命周期、
   QMT Agent 单元测试。
2. **Monitor 组合探测**：配置、直接 probe、状态组合、唯一持久化样本、API 契约和
   Monitor 测试。
3. **Web 状态页**：`probeKind` 原子切换、QMT RTT 与趋势、组件测试。
4. **Windows 运维与文档**：WinSW、端口、防火墙、QMT/Monitor/部署工程文档。
5. **跨主机验收**：受控部署、两个采样周期、事故演练、完整 Dev 实盘状态检查。

建议提交边界与上述实现拆分一致。任何阶段都不得引入旧 `derived` 与新
`probeKind` 长期并存、API 快照耗时冒充 QMT RTT、直接探测失败时回退为旧健康状态
等兼容逻辑。

## 14. Windows 接续开发清单

Windows 上接手此任务的 Agent 在修改代码前必须：

1. 阅读仓库根 `AGENTS.md`。
2. 按顺序阅读：
   - `docs/architecture/系统架构设计.md`
   - `docs/engineering/qmt-agent/README.md`
   - `docs/engineering/engine/README.md`
   - `docs/trading/contracts/A股三层协作与执行契约.md`
   - `docs/engineering/monitor/README.md`
   - 本方案
3. 执行 `git status --short`，保留并避开用户已有改动。
4. 核对当前分支和方案基线；如主线已有后续提交，先确认相关文件是否已变化，不要
   机械套用旧行号。
5. 先完成纯单元测试，不连接 XTQuant、不启动第二个 Agent、不运行真实交易。
6. 只有在实现、审核和自动化验证完成后，才申请进行 Windows 服务和跨主机联调。

任务完成后必须同步更新：

- `docs/architecture/系统架构设计.md`
- `docs/engineering/qmt-agent/README.md`
- `docs/engineering/monitor/README.md`
- `docs/engineering/deployment/README.md`
- 相关 Windows 运维说明

本方案在实现完成后应更新状态和验收记录；它不能替代上述长期权威工程文档。
