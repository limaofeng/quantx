# 方案二：Mac full/live 运行环境改造

> 执行节点：Mac 开发服务节点  
> 方案目标：在 Mac 运行除 QMT Agent 外的全部 Dev 服务  
> 最终能力：默认 `profile=full`、`agentMode=live`，远程 Windows Agent 就绪后提供完整实盘

开始前先阅读[迁移总说明](README.md)。本文只负责 Mac 平台、进程和服务运行环境；
QMT Agent 会话语义由方案一负责，Windows PowerShell 生命周期由方案三负责。

## 1. 当前问题

服务端 Python、React、Caddy 反向代理和外部基础设施协议总体可跨平台，但现有 Dev
运维入口是 Windows PowerShell，并包含以下平台耦合：

- `.venv\Scripts\python.exe` 和 `caddy.exe` 固定路径。
- Windows PID、Job Object、WinSW、ScheduledTasks 和防火墙行为。
- 同一启动器同时创建 API、Engine、Worker、Web、Docs、Caddy 和 QMT Agent。
- 启动前通过本机 QMT 预检决定是否注入实盘环境变量。
- `ops/tools.lock.json` 只锁定 Windows Caddy 和 WinSW。
- 开发状态、日志和精确停止规则由大型 PowerShell 脚本集中实现。

本方案不是安装一套只能做回测或 `data-only` 的 Mac 环境，而是建立完整服务端
`full/live` 运行能力。QMT SDK 仍只在 Windows。

## 2. 目标服务拓扑

Mac 普通 Dev `web` profile 拥有：

```text
Caddy
  ├── API / GraphQL / Agent control WS        127.0.0.1:18081
  ├── Market Gateway /ws/agent/market         127.0.0.1:18082
  ├── Monitor                                 127.0.0.1:18083
  ├── Vite                                    127.0.0.1:5250
  └── VitePress                               127.0.0.1:5251

Engine               无公开监听端口，持有 PostgreSQL 单实例租约
Prefect Worker       连接外部 Prefect Server / quantx-pool
```

PostgreSQL、Redis、InfluxDB 和 Prefect Server 继续作为外部服务，只做连通和契约
检查，不由 Mac 启动器安装、启动或停止。

Monitor 继续使用独立状态文件、SQLite 历史库和生命周期。普通 `up/down` 不拥有
Monitor。

## 3. 权威命令契约

新增 Unix 可执行薄入口和 Python 编排器，目标命令为：

```bash
./ops/quantx up --environment dev --profile web
./ops/quantx status
./ops/quantx logs
./ops/quantx down
./ops/quantx up --environment dev --component monitor
./ops/quantx status --environment dev --component monitor
./ops/quantx logs --environment dev --component monitor
./ops/quantx down --environment dev --component monitor
```

显式非实盘入口可以保留：

```bash
./ops/quantx up --environment dev --profile web --mode data-only
```

但普通 `up` 必须解析为 `full/live`，不能为了 Mac 兼容而自动降级。目标命令没有
完成实现、测试和权威文档切换前，不得替换当前 Windows 命令。

## 4. 设计规格

### 4.1 跨平台编排器

建议新增：

```text
ops/quantx             Unix shell 薄包装，只定位仓库根和 Python
ops/quantx.py          跨平台 Dev 编排器
```

`ops/quantx.py` 负责 Dev 运行；不承接 production 安装、WinSW 或生产回滚。其职责：

- 解析 `up/status/logs/down`、profile、mode 和 component。
- 解析仓库物理路径，不因符号链接产生两套运行根。
- 创建 `.runtime/state`、`.runtime/logs` 和组件运行目录。
- 检查 Python、Node、npm、Caddy 和外部依赖。
- 按依赖顺序启动并等待 readiness。
- 记录 PID、进程启动标识、命令、工作目录和非敏感运行元数据。
- 端口冲突只报告，不终止未被本次状态文件拥有的进程。
- `down` 反向停止且验证 PID/启动标识，不能误杀 PID 复用后的进程。
- 部分启动失败时只清理本轮已经创建的进程。
- `status` 区分进程存活、服务 readiness、QMT 远程状态和有效实盘能力。

不要逐行移植现有 PowerShell。可以抽取其中与平台无关的 profile、组件图和状态
schema；Windows 专属行为留给方案三或现有生产入口。

### 4.2 macOS 进程模型

- 每个组件在独立进程组中启动。
- 启动器保存父 PID、进程组 ID 和可验证的启动时间/命令摘要。
- 优雅停止先发送 `SIGTERM`，有界等待后才对本次进程组发送 `SIGKILL`。
- 不使用模糊命令行搜索或全局 `pkill`。
- 监督重启需要复用 `1/2/5/10/30` 秒退避，并在稳定运行后重置退避。
- `ops/supervise_process.py` 的 Unix 分支必须实现文件锁或等价单实例；当前仅 Windows
  锁不能满足目标。
- 日志采用追加写入和受控轮转，不能把环境变量整体输出到日志。
- 第一阶段使用仓库内监督器，不引入 `launchd` 常驻服务；开发机器重启后的自动
  恢复可以在后续单独评审。

### 4.3 启动和停止顺序

建议启动顺序：

```text
检查外部 PostgreSQL / Redis / InfluxDB / Prefect
  -> Market Gateway
  -> API
  -> Engine
  -> Prefect Worker
  -> Vite / VitePress
  -> Caddy
  -> 通过 Caddy 做最终公共端点验收
```

Caddy 也可以更早启动以提供统一等待页，但最终实现必须有唯一顺序和对应测试。
Agent 不在此进程图中，Mac 启动器既不启动它，也不远程停止它。

建议停止顺序：

```text
停止新的客户端/策略入口
  -> Engine 停止产生新命令并释放租约
  -> Worker
  -> API / Market Gateway
  -> Vite / VitePress
  -> Caddy
```

具体顺序应以避免新命令产生、保证 inbox/outbox 提交和精确进程所有权为准，并在
进程契约测试中固定。

### 4.4 full/live 动态能力

Mac `full/live` 启动时必须注入完整的静态实盘配置和唯一账户白名单。不能像当前
本机预检失败路径一样，在 API/Engine 启动前永久把实盘环境变量改为 `false`，否则
远程 Agent 恢复后仍需要重启服务。

目标分成两层：

- 配置能力：本次运行是否明确按 `full/live` 启动，配置是否允许实盘。
- 有效能力：当前远程 Agent、快照、对账、账户窗口、备份、策略授权是否全部通过。

只有有效能力可以决定下单。远程 Agent 未连接时，Mac 应成功启动非 QMT 服务并
显示：

```text
Runtime profile=full
agentMode=live
System=DEGRADED
QMT Agent=BLOCKED / REMOTE_AGENT_OFFLINE
liveTrading=DISABLED
```

Agent 当前会话完成方案一规定的全部门后，同一批 API/Engine 进程动态变为：

```text
System=READY
QMT Agent=READY
liveTrading=ENABLED
```

断线时必须反向动态关闭。禁止用重启服务、修改环境变量或切换 `data-only` 完成
日常 Agent 恢复。

### 4.5 依赖和虚拟环境

- 支持项目声明的 Python `>=3.11,<3.14`，首个迁移版本固定一个经过验证的小版本。
- Node 固定 `20.x`，npm `>=10`。
- 继续使用同一 `uv.lock`，为服务端 Dev 和 QMT Agent 建立清晰的安装选择；Mac
  安装不能要求 Windows QMT SDK 可用。
- Mac 环境不得导入 `quantx_qmt_agent`、`xtquant` 或 `miniqmt` 作为服务端运行依赖。
- 检查所有含原生扩展的依赖在目标 Mac 架构上有锁定 wheel；缺失时显式失败，不在
  启动时临时编译未知版本。
- 支持目标 Mac 的实际架构（`arm64` 或 `x86_64`），不为未使用架构引入额外分支。

建议由本方案独占 `pyproject.toml`、`uv.lock` 和工具锁的相关修改，避免方案一在
另一服务器同时编辑这些共享文件。

### 4.6 Caddy、传输和网络

- 增加目标 Mac 架构的 Caddy 版本、下载地址和 SHA-256 锁定信息。
- Caddy 是唯一监听非回环地址的进程。
- API、Market Gateway、Monitor、Vite 和 VitePress 继续绑定 `127.0.0.1`。
- 公共地址必须稳定；Windows Credential Manager 中保存的 `api_url` 不应随 DHCP
  频繁变化。
- 最终 `full/live` 接受明确登记的 HTTP/WS 或 HTTPS/WSS；HTTP 仅用于用户明确接受
  明文风险的受控私有局域网，HTTPS 使用时必须验证 Windows 证书信任链。
- Caddy 保持现有大 WebSocket 和行情上传所需的连接、超时和请求体预算。
- 从 Windows 实测控制 WS、市场 WS、token 获取和行情分块上传，不能只在 Mac
  本机 `curl`。

### 4.7 路径和本地运行数据

- 所有路径使用 `pathlib.Path` 或平台中立拼接，不保存 `C:\...` 到新的共享业务
  记录。
- `.venv/bin/python`、无 `.exe` 的 Caddy 和 Unix 可执行权限必须被正确识别。
- API、Engine 和 Worker 都在同一 Mac，因此行情上传 staging 可以继续使用 Mac
  本地 `QUANTX_RUNTIME_DIR/market-data`，本轮不增加对象存储。
- Monitor 的 SQLite 历史库继续独立，普通服务 `down` 不移动或删除它。
- 审计数据库中已有的 Windows 绝对路径；需要保留的产物迁移到 Mac 后改为逻辑 ID
  或受控相对路径，不能批量猜测替换。
- 临时目录和清理必须验证解析后的绝对路径位于指定 runtime 根内。

### 4.8 Mac 运行安全

- `full/live` 期间关闭自动睡眠，或由状态检查明确阻断即将休眠的机器。
- 优先使用有线网络；Wi-Fi 切换必须按网络分区处理并关闭有效实盘能力。
- 开启系统时间同步。
- FileVault、用户登录和密钥链策略不能导致后台开发进程读取不到必要的服务端配置。
- 服务端配置不得包含 Windows 设备密钥或券商凭据。
- Mac 只允许可信 Windows 地址访问 Caddy；不把开发服务直接暴露到公网。

## 5. 实施任务

### 5.1 建立启动器骨架

- [x] 新增 `ops/quantx` 和 `ops/quantx.py`。
- [x] 固定命令、退出码、状态文件 schema 和日志目录。
- [x] 实现物理仓库根解析和运行目录初始化。
- [x] 实现外部依赖只读检查。
- [x] 实现组件依赖图、启动等待和反向停止。
- [x] 实现端口所有者报告，不自动结束未知进程。

### 5.2 完成 Unix 进程管理

- [x] 为 supervisor 增加 Unix 文件锁和单实例。
- [x] 记录并验证 PID、进程组和启动标识。
- [x] 实现 SIGTERM/SIGKILL 有界退出。
- [x] 验证崩溃退避、日志追加和 supervisor 自身退出后的子进程回收。
- [x] 验证符号链接工作区不会产生两份 runtime。

### 5.3 适配依赖和工具

- [x] 固定目标 Python、Node/npm 和 Caddy 版本。
- [x] 添加 Darwin Caddy 工具锁和校验安装。
- [x] 建立不需要 QMT SDK 的服务端安装选择。
- [x] 审计所有 Windows 路径、`.exe`、PowerShell 和 Win32 分支。
- [x] 完成 Apple Silicon 或目标 Intel Mac 的原生依赖验证。

### 5.4 接入完整服务

- [ ] 启动 API、Market Gateway、Engine、Worker、Web、Docs 和 Caddy。
- [x] 保持 Monitor 独立启停。
- [ ] 通过 Caddy 验证 GraphQL HTTP/WS、Agent WS、市场 WS 和 `/monitor/*`。
- [ ] 接入方案一的远程 Agent 动态安全门。
- [ ] 确认 Engine PostgreSQL 租约始终只有一个持有者。
- [ ] 确认 Worker 使用外部 `PREFECT_API_URL` 和 `quantx-pool`。

### 5.5 状态和操作体验

- [x] `status` 同时显示配置模式、有效实盘能力和远程 Agent 状态。
- [x] `logs` 支持全部主组件和单个 component，不读取 Windows 日志。
- [x] `down` 不联系或停止 Windows Agent。
- [x] 部分组件失败时显示稳定原因和下一步，不伪装整体 READY。
- [x] 普通 `up/down` 不影响 Monitor。

## 6. 建议文件范围

本方案优先拥有：

```text
ops/quantx
ops/quantx.py
ops/supervise_process.py（跨平台进程部分）
ops/caddy/Caddyfile.dev
ops/tools.lock.json（或目标平台拆分后的唯一工具锁）
pyproject.toml
uv.lock
服务端中确有必要的路径和平台适配
Mac 启动器与跨平台契约测试
```

本方案不修改 `apps/qmt-agent` 的交易、行情或 journal 语义，不创建 Windows
`quantx-agent.ps1`。如果 `runtime_status.py` 同时被方案一修改，本方案只提交启动器
所需的状态消费变化，由集成负责人解决唯一实现，禁止保留两套状态来源。

## 7. 验证要求

### 7.1 静态和自动验证

- Mac 安装不解析或加载 QMT SDK。
- 启动器命令解析、profile 提升和显式 `data-only` 有契约测试。
- PID 复用、状态文件损坏、端口冲突、部分启动失败和重复 `up/down` 有测试。
- Monitor 独立生命周期有测试。
- Caddy 配置使用目标 Mac 二进制校验。
- 根 Python、前端 check/lint/test/build 全部通过。

### 7.2 无 Agent 的 full/live 验收

直接执行默认命令，而不是 `data-only`：

```bash
./ops/quantx up --environment dev --profile web
./ops/quantx status
```

必须确认：

- API、Market Gateway、Engine、Worker、Web、Docs 和 Caddy 正常运行。
- `profile=full`、`agentMode=live` 保持不变。
- `liveTrading=DISABLED`。
- QMT 为 `BLOCKED / REMOTE_AGENT_OFFLINE`，不能显示 READY。
- 已持久化历史行情回测仍可运行。

### 7.3 远程 Agent 的完整验收

连接方案一交付的 Windows Agent 后，必须验证：

- Agent 控制和市场连接都经 Mac Caddy。
- 完整账户快照和 Engine 对账完成。
- 全市场流 READY、关键指数和覆盖率达标。
- 历史行情上传经过 Mac staging 并由 Worker 完成持久化。
- 动态有效实盘能力从 DISABLED 变为 ENABLED，无需重启 Mac 服务。
- 经明确授权的委托、撤单或成交由真实 QMT 回报收敛。
- 断开 Windows 网络后能力自动关闭；恢复并重新对账后动态恢复。

### 7.4 运行稳定性

至少完成：

- API、Market Gateway、Engine、Worker 各自单独重启。
- Mac Caddy 重启和证书续用检查。
- Mac 网络短暂中断和恢复。
- Mac 睡眠阻断验证。
- 全市场非交易压测。
- 连续一个完整交易时段 SHADOW 观察；真实 LIVE 时长由用户另行授权。

## 8. 交接物

向集成负责人和其他方案交付：

- 实际命令、配置、TLS、状态 schema、退出码和剩余跨主机验收见
  [macOS Dev full/live 运行手册](../../engineering/deployment/MACOS_DEV_RUNTIME.md)。
- 基线和最终提交 SHA。
- 目标 Mac 架构、OS、Python、Node/npm、Caddy 版本。
- `<MAC_DEV_PUBLIC_URL>`、传输 scheme 与 HTTP 风险确认，或适用时的 CA 安装说明；
  不包含私钥。
- 权威 Mac 命令和退出码。
- 状态文件 schema、组件依赖图和端口表。
- 自动测试、无 Agent full/live 和跨主机验收结果。
- 尚未完成的真实交易授权步骤。

运行证据放入 `.runtime/reports/mac-dev-migration/macos-runtime/`。

## 9. 回滚

清理 Windows 旧 Dev 启动路径前：

1. 停止新策略和命令生产。
2. 执行 Mac `down` 并确认 Engine 释放租约。
3. 停止远程 Windows Agent，并保留 journal。
4. 回到同一已知良好提交，恢复旧 Windows 全栈。
5. 重新发送完整快照和对账后才能恢复实盘。

禁止在旧 Windows Engine 运行时继续保留 Mac Engine。清理方案合并后，回滚必须以
完整提交为单位恢复，不新增长期兼容命令。

## 10. 完成定义

只有当 Mac 能通过唯一入口启动全部非 QMT 服务，以默认 `full/live` 等待远程
Agent，并在 Agent 完成快照和对账后动态提供真实行情与完整实盘链路时，本方案才
算完成。仅在 Mac 上运行 Web/API、仅能回测或仅通过 `data-only` 不算完成。
