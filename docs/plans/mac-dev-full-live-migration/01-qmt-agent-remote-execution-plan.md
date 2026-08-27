# 方案一：QMT Agent 远程执行端改造

> 执行节点：Windows QMT 节点；服务端联调需要可访问的 Mac API  
> 方案目标：把 QMT Agent 从 Windows 全栈启动器的本机子进程改造成独立远程执行端  
> 最终能力：`full/live`；`data-only` 和 `paper` 不构成完成证据

开始前先阅读[迁移总说明](README.md)。总说明中的网络、会话、模式、备份和文件
所有权契约优先于本文。本文可以在独立服务器和独立工作分支执行，但必须从总说明
约定的相同 `BASE_COMMIT` 开始。

## 1. 当前问题

现有 QMT Agent 的传输本身已经支持登记自定义 `api_url`，会从 HTTP/HTTPS 地址
派生 WS/WSS，并分别连接 `/ws/agent` 和 `/ws/agent/market`。因此本方案不重写
成熟的行情、命令和 journal 管线，重点解决下列本机部署假设：

- 服务端当前可以依赖 Windows 启动器注入的 `QMT_AGENT_LAUNCH_STATE`、
  `QMT_AGENT_LAUNCH_STARTED_AT` 和本机 PID 判断本轮 Agent。
- Windows 启动器同时拥有服务端与 Agent 生命周期。
- API 重启后，数据库中上一轮仍不足 90 秒的心跳可能被误认为本轮执行端。
- 本机固定地址、同主机时钟和本机预检不再适用于 Mac/Windows 两个节点。
- Agent 的完整实盘能力需要在远程网络中验证，而不只是验证能建立 WebSocket。

## 2. 目标边界

完成后，QMT Agent：

- 仍然是唯一允许导入 `xtquant` 的应用。
- 仍然只依赖 `quantx_contracts`，不依赖 ORM、Repository、Engine 或策略。
- 复用改造前已经可用的 `xtquant-demo` Conda 环境，不新建独立 QMT Agent venv，
  也不回退到服务端 Python。
- 只建立出站 HTTP(S)/WS(S)，不监听任何局域网端口。
- 在 Windows 本地保存设备密钥、QMT 配置、journal 和行情上传 spool。
- 独立启动、停止、重连和恢复，不依赖 Mac 启动器。
- 接收 Mac 的实盘命令并把 QMT 真实回报可靠收敛回服务端。

Mac API 负责：

- 认证设备并创建服务端可信的当前会话。
- 把命令投递到唯一活动 Agent 会话。
- 先持久化报告 inbox，再发送 `report_ack`。
- 把 Agent 在线、完整快照和对账状态提供给账户执行安全门。
- 在断线、撤销或会话失效时立即关闭有效实盘能力。

## 3. 不允许改变的硬边界

- 不把 `command_ack` 当作成交或订单状态推进依据。
- 不允许 API、Engine、Worker 导入 `miniqmt` 或 `xtquant`。
- 不允许 Agent 查询或修改服务端数据库。
- 不在协议中发送券商账号密码、QMT 安装路径或设备密钥。
- 不增加多账户路由；一个 Windows 执行节点只运行当前唯一账户的一个 Agent。
- 不用“连接成功”替代完整快照、对账和实盘能力门。
- 不为了迁移恢复旧协议、双协议或可选降级分支。

## 4. 设计规格

### 4.1 设备登记和地址

保留现有登记模型，登记时允许传入稳定的 Mac 公共地址：

```powershell
python -m quantx_qmt_agent.main enroll `
  --api-url <MAC_DEV_PUBLIC_URL> `
  --code <一次性登记码>
```

目标要求：

- `api_url` 必须规范化并去除末尾 `/`。
- `full/live` 最终验收接受明确登记的 HTTP 或 HTTPS 根地址；HTTP 固定派生 WS，
  HTTPS 固定派生 WSS，控制、市场和上传端点必须保持同一 authority。
- HTTP 仅用于用户明确接受明文风险的受控私有局域网；HTTPS 仍为推荐形态。
- HTTPS 使用私有 CA 时通过 `SSL_CERT_FILE` 显式加载根证书，但仍禁用系统代理、
  重定向和跨 scheme 降级。
- 设备密钥继续写入 Windows Credential Manager。
- 修改服务器地址或 scheme 必须重新执行受控登记或显式配置迁移，不能静默接受
  服务端重定向。
- 日志只显示地址和 `device_id` 的安全摘要，不显示令牌或设备密钥。

### 4.2 服务端可信会话

在不升级 Agent 线协议的前提下建立服务端可信会话：

1. API 进程启动时生成 `api_instance_id`。
2. Agent 完成设备令牌认证并连接控制 WebSocket 后，API 生成
   `agent_session_id` 和 `server_connected_at`。
3. `AgentSession` 在内存中绑定设备、唯一账户、模式、能力、API 实例和会话 ID。
4. API 写 Agent heartbeat 时，把 `apiInstanceId`、`agentSessionId`、
   `serverConnectedAt` 和 `serverReceivedAt` 写入服务端控制的 details。
5. Agent 自报 payload 中出现同名字段时必须忽略或拒绝，不能覆盖服务端值。
6. API component heartbeat 必须暴露当前 `apiInstanceId`。
7. Agent 就绪查询要求 Agent heartbeat 的 `apiInstanceId` 等于当前 API heartbeat
   的值，并且当前 Agent Hub 中仍存在对应活动会话。
8. API 重启、Agent 断开、设备撤销和重复设备连接替换时，旧会话立即失效。

优先复用 heartbeat details，避免本方案引入数据库 migration。如果实际模型不能
安全承载，应先更新总说明并评审唯一的数据设计，不能增加临时字段后再保留兼容。

### 4.3 时间与新鲜度

- `AgentEnvelope.sent_at` 必须是带时区 UTC 时间。
- API 记录独立的服务端接收时间，实盘就绪以服务端接收时间为主要 TTL 依据。
- 保留现有未来时间偏差和采集年龄检查，并把文档中的“同主机”改成“两端均有可靠
  时间同步”。
- Mac 与 Windows 均必须启用系统时间同步。
- 超过允许偏差时 Agent 可以保持连接用于诊断，但有效实盘能力必须关闭。
- 90 秒心跳 TTL 是最终兜底；控制连接断开时不能等待 TTL 才关闭能力门。

### 4.4 单实例与重复连接

Windows 本地单实例由方案三的启动器通过锁、PID 和进程启动时间保证。服务端还要
防御重复连接：

- 同一 `device_id` 新控制连接通过认证后，旧连接必须被明确撤销或拒绝新连接，行为
  必须唯一并有测试。
- 同一账户不能同时存在两个可接收交易命令的 live Agent。
- 重复连接处理期间，交易命令保持在 outbox，不得广播给两条连接。
- 市场连接必须绑定已经认证的同一设备/会话世代，不能由另一设备冒用。

### 4.5 启动与恢复顺序

Agent 每次启动或控制连接重建后按以下顺序恢复：

```text
加载 Credential Manager 与本地配置
  -> 校验 live 环境变量和唯一账户
  -> 校验 journal 完整性
  -> 获取短期设备令牌
  -> 建立控制 WebSocket
  -> 建立/恢复 XTData 与 XTTrading 会话
  -> 重放未确认报告（保持原 message_id）
  -> 上报完整账户快照
  -> 服务端完成 inbox 收敛和账户对账
  -> 建立全市场 WebSocket 并完成 readiness-confirm
  -> Agent 与服务端同时进入 READY
  -> 接收新的实盘命令
```

在完整快照、对账或市场 readiness 未完成时，不得处理新的增仓命令。已有的安全
减仓命令是否可恢复，继续服从账户级 `OBSERVE_ONLY / REDUCE_ONLY / TRADING /
KILLED` 权威状态，不在 Agent 内另造策略。

### 4.6 断线与重放

- 所有 QMT 回报先写本地 SQLite journal，再尝试发送。
- API 只有在 inbox 提交成功后才能确认报告。
- 未收到 `report_ack` 的报告使用原 `message_id` 重放。
- 同一 `command_id` 的重投必须命中已有执行记录，不得再次向 QMT 下单。
- 控制连接断开不应销毁正常工作的 XTData 全推采集器；市场 sink 按现有协议进入
  `SYNCING/STALE` 并从一致水位恢复。
- 网络恢复后先完成旧报告、完整快照和对账，再接收新命令。
- journal 损坏、命令身份冲突或不可恢复的 XTData 取消失败必须 fail-stop。

### 4.7 完整能力矩阵

| 能力 | 必须保留的行为 | 验收证据 |
| --- | --- | --- |
| 设备认证 | 一次性登记、短期 token、撤销即时生效 | 登记、重连、撤销测试 |
| 全市场行情 | 唯一 whole-quote、覆盖率门、SNAPSHOT/DELTA 收敛 | Market Gateway READY 与覆盖率报告 |
| 单标的行情 | QMT 原生周期数据，不从 tick 伪造 K 线 | 实际请求与数据库结果 |
| 历史行情上传 | 有界 spool、分块、幂等、失败恢复 | 大请求中断续传测试 |
| 复权因子 | 原生请求、上传、服务端持久化 | 指定标的核对结果 |
| 账户事实 | 资产、持仓、委托、成交完整快照 | snapshot id/hash 与对账结果 |
| 下单 | 唯一账户、白名单、命令幂等 | 经授权的 CANARY 委托生命周期 |
| 撤单 | 目标订单绑定、真实 QMT 回报 | 经授权的撤单生命周期 |
| 回报收敛 | journal -> inbox -> Engine | 数据库审计与客户端状态一致 |
| 重启恢复 | API/Agent/Engine 单点重启不重单 | 故障注入报告 |

### 4.8 状态与原因码

复用现有状态模型，只增加跨主机确有必要的稳定原因，不增加新的并行状态机：

- `REMOTE_AGENT_OFFLINE`
- `REMOTE_AGENT_SESSION_STALE`
- `REMOTE_AGENT_NOT_RECONCILED`
- `REMOTE_AGENT_ACCOUNT_MISMATCH`

具体时钟、传输/TLS 或 journal 错误放入安全的 details 和日志；它们只有在客户端
确实要做稳定分支时才提升为公共原因码。

## 5. 实施任务

### 5.1 传输和配置审计

- [ ] 确认所有控制、市场和上传 URL 都从登记的 `api_url` 派生。
- [ ] 移除仍假设 `127.0.0.1`、同主机或 Windows 服务端路径的代码。
- [ ] 验证严格使用登记 scheme；HTTPS 证书错误 fail-closed 且禁止自动退回 HTTP，
  明确登记的 HTTP 始终保持 HTTP/WS。
- [ ] 确认代理、超时、最大帧和上传大小适合跨主机局域网。
- [ ] 验证日志和异常不会泄露 token、设备密钥或账户敏感配置。

### 5.2 服务端会话改造

- [ ] 增加 API 实例身份和 Agent 服务端会话身份。
- [ ] 在 heartbeat details 中持久化服务端可信身份和接收时间。
- [ ] 替换远程场景下的本机 launch/PID 就绪依赖。
- [ ] Agent 断开、替换和撤销时立即使动态能力门失效。
- [ ] Engine/账户安全服务只接受当前 API 实例的 live Agent。
- [ ] 历史补数设备选择也只能选择当前有效会话。

### 5.3 恢复与完整能力

- [ ] 验证未确认报告按原 ID 重放。
- [ ] 验证处理中的命令重连后不会再次下单。
- [ ] 强制新会话重新发送完整账户快照。
- [ ] 强制市场连接完成现有三阶段同步后才 READY。
- [ ] 验证行情上传中断、缓存淘汰和同 request ID 参数冲突。
- [ ] 验证 Agent watchdog 和不可恢复错误的 fail-stop 行为。

### 5.4 状态和文档

- [ ] 状态端点显示远程地址摘要、当前协议、模式、快照年龄和阻断原因。
- [ ] 不显示设备密钥、token、券商配置或完整敏感账户信息。
- [ ] 更新本方案直接拥有的 Agent/API 测试。
- [ ] 把需要集成负责人更新的权威文档列入交接报告，不直接与其他服务器竞争修改。

## 6. 建议文件范围

本方案优先拥有：

```text
apps/qmt-agent/src/quantx_qmt_agent/
apps/qmt-agent/tests/
apps/api/src/quantx_api/agent_hub.py
apps/api/src/quantx_api/agent_api.py
apps/api/src/quantx_api/runtime_status.py（仅 Agent 会话部分）
packages/contracts/src/quantx_contracts/agent.py（仅确有必要时）
packages/infrastructure/.../qmt_launch_guard.py
packages/infrastructure/.../account_execution_safety_service.py（Agent 门部分）
对应 API、contracts、infrastructure 测试
```

本方案不得创建或清理 Mac/Windows 启动脚本；这些分别属于方案二、方案三。若必须
修改共享文件，应在提交说明中逐项标记，供集成负责人处理。

## 7. 验证要求

### 7.1 自动测试

至少覆盖：

- API 重启后旧 Agent heartbeat 被拒绝。
- 当前认证会话 heartbeat 被接受。
- Agent 断线立即关闭有效实盘能力。
- 同设备重复连接只保留一个命令接收者。
- 账户或模式不匹配时连接可诊断但不能执行实盘。
- 旧报告重放保持 `message_id`，同命令重投保持 `command_id` 幂等。
- 完整快照缺失、过期或未对账时拒绝实盘。
- 市场 readiness-confirm 未完成时不报告市场 READY。
- 明确登记的 HTTP 与 HTTPS 均可通过 `full/live` 传输验收，并分别严格派生 WS/WSS；
  不允许重定向、自动换址或跨 scheme 回退。

执行相关单元和集成测试后，还要运行根边界测试：

```powershell
python -m pytest tests/
```

### 7.2 跨主机验收

使用真实 Windows QMT 运行时和 Mac API，依次验证：

1. 登记、token 获取、控制连接和市场连接。
2. 账户、资产、持仓、委托、成交完整快照。
3. 全市场覆盖率和 `SNAPSHOT/DELTA/readiness-confirm`。
4. 指定标的历史行情和复权因子上传。
5. API 重启、Agent 自动重连和完整快照重建。
6. 网络中断期间产生的真实回报在恢复后收敛。
7. 经用户明确授权的 CANARY 下单和撤单。
8. 若发生成交，成交回报必须从 QMT 到达 inbox 并由 Engine 收敛。

真实交易测试必须同时满足项目规定的 `ENV=testing`、实盘开关、账户白名单和人工
授权；没有授权时只执行到 SHADOW，不得自行发送订单。

## 8. 交接物

向集成负责人和另外两个执行方案交付：

- 基线和最终提交 SHA。
- Agent 所需的公共 URL、传输 scheme 与风险确认、适用时的 TLS 信任要求，以及
  非敏感配置字段清单。
- 服务端 heartbeat details 的最终结构和稳定原因码。
- 自动测试结果和跨主机未完成项。
- Agent `status` 的脱敏样例。
- 已验证的协议仍为 `1.1`；如不得不变更，必须先更新总说明并阻断其他方案集成。

运行证据放入 `.runtime/reports/mac-dev-migration/qmt-agent/`，不得提交凭据或真实账户
敏感数据。

## 9. 回滚

- 停止新的交易命令生产并确认未决命令状态。
- 停止远程 QMT Agent，不删除 Credential Manager 或 journal。
- 回滚 Agent/API/安全门到同一个已知良好提交，不能只回滚一侧协议。
- 如果新版本未改变线协议和数据库 schema，可恢复旧 Windows 全栈运行并重新发送
  完整快照。
- 回滚后必须重新对账；旧 heartbeat 不能直接恢复有效实盘能力。

## 10. 完成定义

只有当远程 Windows Agent 通过 Mac 公共入口提供全部行情、历史数据、账户快照、
下单、撤单、回报、重放和恢复能力，并且服务端能可靠区分当前会话与历史心跳时，
本方案才算完成。WebSocket 连接成功或 `data-only/paper` 通过不算完成。
