# 方案三：Windows 启动器清理

> 执行节点：Windows QMT 执行节点  
> 方案目标：把 Windows Dev 运维入口收敛为只管理 QMT Agent 和联合备份  
> 切换条件：方案一、方案二的跨主机 `full/live` 集成验收已经通过

开始前先阅读[迁移总说明](README.md)。本文可以提前开发和测试新的 Agent 启动器，
但删除或禁用旧 Windows Dev 全栈路径必须最后执行。生产部署本轮冻结，不在本文中
重新设计。

## 1. 当前问题

现有 `ops/quantx.ps1` 同时承担：

- Dev Caddy、API、Market Gateway、Engine、Worker、Web、Docs 和 QMT Agent。
- Monitor 独立生命周期。
- Windows 开发依赖、端口、PID、状态和日志管理。
- QMT 登记与运行时预检。
- Dev 每日 PostgreSQL 与 QMT journal 联合备份。
- Production bootstrap、install、WinSW、backup、restore、migrate 和 rollback。

Mac 迁移后，如果继续让该脚本保留 Windows Dev 全栈能力，会形成两个权威入口，
增加重复 Engine、重复 Agent、端口冲突和误操作风险。本方案只清理 Dev 责任；生产
命令保持当前语义，等待未来独立生产部署方案。

## 2. 目标职责

Windows Dev 节点最终只拥有：

- QMT 安装与改造前 `xtquant-demo` Conda 运行时预检。
- 设备登记状态检查。
- 唯一账户解析和 live 环境安全门。
- QMT Agent 单实例启动、停止、状态、日志和重启。
- Windows Credential Manager、Agent journal 和行情 spool。
- PostgreSQL 与 QMT journal 的 Dev 联合备份、校验和备份登记。
- 可诊断但不泄密的远程 Mac 连接状态。

Windows Dev 节点不再拥有：

- Caddy、API、Market Gateway、Engine、Worker、Web、Docs、Monitor。
- 这些服务的端口、进程、日志和环境变量。
- Mac 的启动、停止、重启或部署。
- 服务端有效实盘能力的最终判断。

## 3. 权威命令契约

新增唯一 Windows Dev Agent 入口：

```powershell
.\ops\quantx-agent.ps1 enroll `
  -ApiUrl <MAC_DEV_PUBLIC_URL> `
  -Code <一次性登记码>

.\ops\quantx-agent.ps1 up -Environment dev -AccountId <账户>
.\ops\quantx-agent.ps1 status -Environment dev
.\ops\quantx-agent.ps1 logs -Environment dev
.\ops\quantx-agent.ps1 restart -Environment dev -AccountId <账户>
.\ops\quantx-agent.ps1 down -Environment dev
.\ops\quantx-agent.ps1 backup -Environment dev
.\ops\quantx-agent.ps1 doctor -Environment dev
```

命令必须非交互、可重复执行并返回稳定退出码。不得要求管理员权限完成普通
`up/status/logs/down`；需要系统级计划任务或生产服务的操作继续遵循原有显式权限
边界。

不提供从该脚本启动 Mac 服务的命令，也不增加 `-RemoteHost`、SSH 或 WinRM 参数。

## 4. 设计规格

### 4.1 文件和状态边界

建议目标结构：

```text
ops/quantx-agent.ps1
.runtime/
├── state/qmt-agent.json
├── state/qmt-agent-supervisor.json
├── logs/qmt-agent.stdout.log
├── logs/qmt-agent.stderr.log
├── qmt-agent/idempotency.sqlite3
├── qmt-agent/market-data-spool/
└── backups/
```

状态必须记录并校验：

- supervisor PID 和启动时间。
- Agent child PID 和启动时间。
- 解析后的物理仓库根或安装根。
- 模式、非敏感账户摘要、登记设备 ID 摘要。
- Mac API 地址摘要、最近连接状态和协议版本。
- journal 完整性、大小、待确认报告和处理中命令数量。

状态文件不得保存设备密钥、短期 token、券商账号密码或完整环境变量。

### 4.2 Agent 单实例

- `up` 前先读取状态文件并验证 PID 与启动时间。
- 已存在同一受管 Agent 时，重复 `up` 幂等返回当前状态。
- 状态文件陈旧时只清理状态记录，不能按名称模糊结束未知进程。
- 使用 Windows 文件锁和 Job Object 保证唯一 supervisor 及子进程回收。
- 继续使用 `1/2/5/10/30` 秒异常重启退避。
- QMT 原生不可恢复错误必须由 Agent 专用退出码触发 fail-stop/restart 策略。
- 禁止手工绕过入口运行第二个 `python -m quantx_qmt_agent.main run`。

### 4.3 up 预检

`up` 必须依次检查：

1. 操作系统是受支持的 Windows。
2. QMT 客户端、userdata 目录和 XTData/XTTrading 运行时可用。
3. QMT Agent 默认解析到改造前的 `xtquant-demo` Conda Python，且可导入 `xtquant`；
   `QUANTX_QMT_PYTHON_EXE` 仅作为显式路径覆盖，不得回退到服务端 Python 或另建 venv。
4. 设备已登记且 Credential Manager 项可读取。
5. 登记的 `api_url` 等于批准的 `<MAC_DEV_PUBLIC_URL>`。
6. token 端点和控制/市场 WS 按登记 scheme 可达；HTTPS 的证书链必须可信，HTTP
   必须是已批准地址，且两者都禁止重定向或跨 scheme 回退。
7. `ENV=testing`、`ENABLE_REAL_TRADING=true`、
   `QMT_REAL_TRADING_ENABLED=true` 和唯一 `QMT_ACCOUNT_WHITELIST` 满足 live 条件。
8. journal 完整性通过，没有无法解释的冲突命令。
9. 本地没有另一个受管或已知的 QMT Agent。

预检失败时不得改成 `data-only` 或 `paper`。应保持目标为 `live`、不启动 Agent、
返回非零退出码和稳定原因。Mac 会独立保持 `DEGRADED / BLOCKED`。

### 4.4 status

`status` 至少显示：

```text
managed=true|false
process=RUNNING|RECOVERING|STOPPED|STALE
mode=live
device=<脱敏摘要>
account=<脱敏摘要>
apiUrl=<不含凭据的地址>
controlChannel=CONNECTED|DISCONNECTED|UNKNOWN
marketChannel=READY|SYNCING|STALE|OFFLINE|UNKNOWN
protocol=1.1|unknown
heartbeatAge=<秒或 unknown>
journalIntegrity=ok|failed|unknown
pendingReports=<数量>
processingCommands=<数量>
lastBackupAt=<时间或 unknown>
```

本地 CLI 无法从本地事实证明的服务端状态必须显示 `UNKNOWN`，不能把进程存活等同
于服务端 READY。允许通过认证的只读服务端状态端点补充信息，但不得因此把服务端
令牌写入日志。

### 4.5 down 和 restart

- `down` 只停止状态文件记录且启动时间匹配的 supervisor/Agent。
- 先请求有界优雅退出，让 journal 和 QMT 会话收尾；超时后才终止受管 Job Object。
- `down` 不访问 Mac，不停止服务端，也不修改服务端静态实盘配置。
- Agent 断开后服务端方案一负责立即关闭有效实盘能力。
- `restart` 必须等旧 PID 和原生子线程彻底退出后再启动新世代，禁止两个 Agent
  重叠。

### 4.6 联合备份

把现有 Dev 联合备份责任移入新的 Windows Agent 运维入口，但不改变 Agent 代码的
依赖边界。

`backup` 顺序固定为：

1. 验证目标备份目录是受控绝对路径。
2. 使用受控 PostgreSQL 工具生成服务端数据库归档。
3. 校验 PostgreSQL 归档可读；按现有策略执行隔离恢复验证。
4. 调用 QMT Agent `backup-state` 生成 journal 一致副本。
5. 对 journal 执行 SQLite 完整性检查。
6. 备份 Monitor 历史只在明确指定时执行；Monitor 不参与账户实盘门。
7. 写入包含数据库和 journal 文件、大小、时间与 SHA-256 的单一 manifest。
8. manifest 完成后调用服务端备份登记逻辑更新 `last_backup_at`。
9. 备份成功后再执行受控 journal retention；retention 失败只告警，不破坏已完成
   备份。

任一步失败时：

- 命令返回非零。
- 不写成功 manifest。
- 不更新 `last_backup_at`。
- 不删除上一个已知良好备份。
- `full/live` 的 24 小时备份门按既有规则 fail-closed。

计划任务仍可每天 16:30 运行，但注册计划任务和权限策略应与普通启动分离。首次
切换前必须手工执行并验证一次新命令。

### 4.7 旧 `ops/quantx.ps1` 清理

清理分两步执行，并形成两个可独立审核、按不同阶段合并的提交。第一个提交可以在
完整链路联调前合并；第二个提交只能在联调通过后合并。

#### 步骤一：抽取并验证

- 把 QMT 登记、预检、单实例、日志、状态和 Dev 联合备份移动到
  `ops/quantx-agent.ps1`。
- 保持旧 Dev 全栈入口暂时可回滚，但文档明确它尚未切换。
- 完成方案一、方案二的完整跨主机验收。

该步骤作为“抽取提交”交付，不删除任何整体回滚必需的旧 Dev 路径。

#### 步骤二：原子切换

- 从 `ops/quantx.ps1` 删除或禁用 Dev Caddy/API/Market Gateway/Engine/Worker/
  Web/Docs/Monitor/QMT 进程编排。
- `-Environment dev` 的旧全栈命令应明确失败并指向 Mac 权威入口，不能静默只启动
  部分服务。
- 移除 Dev QMT launch 环境注入和本机 QMT readiness 逻辑。
- 移除只服务于 Windows Dev 全栈的端口、状态和日志分支。
- 保留 production install/WinSW/backup/restore/migrate/rollback 当前行为，不在
  本方案顺手重构。
- 权威文档由集成负责人一次切换，不长期记录两套默认 Dev 命令。

该步骤作为单独的“清理提交”交付，提交说明必须引用完整链路验收证据。

最终实现不能保留隐藏参数重新开启旧 Windows Dev 全栈。

## 5. 实施任务

### 5.1 新启动器

- [ ] 建立 `ops/quantx-agent.ps1` 参数、帮助和稳定退出码。
- [ ] 抽取设备登记和 QMT 运行时预检。
- [ ] 抽取 Agent supervisor、单实例和状态管理。
- [ ] 实现 `up/status/logs/restart/down/doctor`。
- [ ] 确保普通生命周期不需要管理员权限。

### 5.2 远程配置

- [ ] 检查 Credential Manager 中保存的 Mac `api_url`。
- [ ] 验证登记的 HTTP(S) scheme、token、控制 WS 和市场 WS；使用 HTTPS 时验证 CA。
- [ ] 日志中隐藏 token、设备密钥和敏感账户信息。
- [ ] Agent 配置中不出现数据库 ORM 或 Mac 本地路径。

### 5.3 联合备份

- [ ] 抽取 PostgreSQL 备份、校验和隔离恢复验证。
- [ ] 接入 QMT journal `backup-state` 和完整性检查。
- [ ] 生成单一 manifest 并仅在成功后登记。
- [ ] 验证任一部分失败不会刷新备份门。
- [ ] 注册并验证新的 Dev 备份计划任务。

### 5.4 清理旧入口

- [ ] 列出旧 PowerShell 中所有 Dev 专属函数和调用图。
- [ ] 在完整集成验收前不删除旧回滚入口。
- [ ] 验收后移除 Windows Dev 全栈组件管理。
- [ ] 保证 production 分支测试不因 Dev 清理退化。
- [ ] 删除已无调用者的 Dev 专属函数、常量和状态 schema。
- [ ] 更新 Windows 启动器契约测试，禁止旧 Dev 全栈重新出现。

## 6. 建议文件范围

本方案优先拥有：

```text
ops/quantx-agent.ps1
ops/quantx.ps1（仅 Windows Dev 清理与必要的 production 回归保护）
ops/windows/quantx-qmt-agent.xml（如果开发/生产入口需要明确拆分）
Windows Agent 启动器契约测试
Dev 联合备份契约测试
```

本方案不修改 Mac 编排器、Agent 线协议、Agent journal 语义或账户执行安全服务。
发现这些接口不足时向对应方案提交变更请求。

## 7. 验证要求

### 7.1 启动器契约测试

- `up` 只创建 supervisor 和一个 QMT Agent。
- 重复 `up` 不创建第二实例。
- 端口和进程检查不结束未知进程。
- 状态文件 PID 复用时不误杀新进程。
- `down` 不访问或关闭 Mac。
- `restart` 不产生两个重叠 QMT 原生会话。
- 预检失败保持 live 目标并返回失败，不降级模式。
- `status` 不把 PID 存活伪装成服务端 READY。
- 日志和状态不含凭据。

### 7.2 备份测试

- 数据库备份成功、journal 失败：不登记成功。
- journal 成功、数据库失败：不登记成功。
- manifest 写入失败：不登记成功。
- 备份登记失败：保留备份文件并报告失败。
- journal retention 失败：备份仍可验证，但明确告警。
- 恢复验证使用受控 scratch 数据库，清理目标经过绝对路径/名称校验。

### 7.3 清理回归测试

切换后执行 Windows `up`，进程表中不得由 Dev 启动器创建：

```text
caddy
quantx_api
quantx_market_gateway
quantx_engine
quantx_worker
vite
vitepress
quantx_monitor
```

只允许受管 QMT Agent 及其 supervisor。还要验证：

- 旧 Windows Dev 全栈命令明确失败并给出 Mac 命令指引。
- production 命令解析和已有契约测试保持通过。
- `ops/quantx.ps1 install` 不用于普通验证。
- 根测试和 Windows ops 契约测试通过。

### 7.4 跨主机 full/live 验收

- 启动 Mac 默认 `full/live`，确认 Agent 离线时服务端 BLOCKED。
- 使用新 Windows 入口启动 Agent。
- 等待完整快照、对账、市场 READY 和新联合备份。
- 确认 Mac 动态开启有效实盘能力。
- 经用户明确授权完成 CANARY 委托生命周期。
- Windows `restart` 后重新快照、对账并恢复，无重复订单。
- Windows `down` 后 Mac 立即关闭有效能力，但非 QMT 服务继续运行。

## 8. 交接物

向集成负责人交付：

- 基线和最终提交 SHA。
- 新命令帮助输出、退出码和状态 schema。
- Windows/QMT/原 `xtquant-demo` Conda Python 版本及适用时的 Mac CA 信任说明。
- 旧 `ops/quantx.ps1` 删除的 Dev 职责清单。
- production 回归测试结果。
- 联合备份 manifest 的脱敏样例及恢复验证结果。
- 跨主机 `full/live` 状态证据。

运行证据放入 `.runtime/reports/mac-dev-migration/windows-launcher/`。

## 9. 回滚

在旧 Dev 路径清理前，可停止新 Agent 和 Mac 服务，恢复同一版本的旧 Windows
全栈。清理提交合并后：

1. 先关闭有效实盘能力并处置未决命令。
2. 停止新 Windows Agent，并保留 Credential Manager 与 journal。
3. 停止 Mac Engine 并确认租约释放。
4. 整体回滚到已知良好提交，不能通过隐藏开关恢复已删除路径。
5. 使用匹配 manifest 的数据库和 journal；需要恢复数据时先完成隔离验证。
6. 重新发送完整快照、重新对账后才允许实盘。

## 10. 完成定义

只有当 Windows Dev 的唯一权威入口只管理 QMT Agent 和联合备份，旧 Windows
Dev 全栈启动路径已被清理，而且通过新入口能够支撑 Mac 上的完整 `full/live`
行情与实盘链路时，本方案才算完成。只新增脚本但仍保留两套默认入口不算完成。
