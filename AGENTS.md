# QuantX Agent 记忆文件

进入本项目后先读本文件，再按任务范围阅读对应代码和文档。

## Monorepo 结构

- `apps/api/`：FastAPI、REST、Strawberry GraphQL、Agent WebSocket Hub。
- `apps/docs/`：VitePress 客户端开发文档与发布契约。
- `apps/web/`：Vite + React + TypeScript。
- `apps/engine/`：策略、做 T、条件清仓、热缓存与订单回报收敛。
- `apps/monitor/`：独立可用性、延迟、事故历史与状态页只读 API。
- `apps/worker/`：Prefect flows、tasks、部署入口。
- `apps/qmt-agent/`：唯一允许访问 XTData/XTTrading 的出站 Agent。
- `packages/contracts/`：版本化 Agent 协议、DTO 和公共枚举。
- `packages/domain/`：纯交易域、策略、风控、仓位和回测 broker。
- `packages/application/`：用例、端口接口、命令路由和状态推进。
- `packages/infrastructure/`：数据库、Repository、行情适配和持久化消息箱。
- `ops/`：Caddy、WinSW 和统一运维入口。
- `docs/engineering/`：按 API、Engine、Worker、QMT Agent、部署组织的工程文档。

Python 命名空间分别为 `quantx_contracts`、`quantx_domain`、
`quantx_application`、`quantx_infrastructure`、`quantx_engine`、
`quantx_monitor`、`quantx_worker` 和 `quantx_qmt_agent`。

## 设计与维护准则

- 所有架构、协议和数据模型必须从 QuantX 的真实需求出发，保持设计合理、
  简洁且边界清晰。项目是个人单账户系统，不得为臆想中的多账户、多租户或
  未确认的未来需求增加字段、状态、分支和抽象。
- 不为旧实现、旧协议或未部署版本默认增加兼容层、双协议、可选字段、降级分支
  或兜底逻辑。契约调整必须在代码、客户端、文档和测试中原子切换到唯一权威
  设计；只有用户明确要求兼容，并明确兼容对象和期限时，才允许引入受控兼容。

## 统一运行方式

### Windows QMT 节点硬边界

Windows 环境只负责 QMT Agent，不负责 API、Engine、Worker、Web、Docs、Caddy、
Monitor 或任何 Mac 服务。Codex、自动化脚本和人工运维在 Windows 上处理 QMT
Agent 启动、重启或验收时，必须遵守以下规则：

- 只允许从仓库根目录使用 `ops/quantx-agent.ps1` 管理 QMT Agent；不得运行
  `ops/quantx.ps1 up/down` 启停主服务栈，也不得绕过统一入口手工启动 Agent。
- 不得通过 SSH、远程命令、脚本或其他方式检查、启动、停止或重启 Mac 服务。
  Mac 或远端 API 的状态不属于 Windows QMT Agent 启动任务的交付范围；只有用户
  明确扩大任务范围时才能操作。
- Windows 启动成功只要求本机计划任务、Supervisor 和唯一 Agent 子进程正常，
  `0.0.0.0:18084` 由该 Agent 监听，且 `/health/live` 返回 HTTP 200。必须同时确认
  没有旧版或重复 Agent 进程。
- `/health/ready`、控制连接、对账、行情流和服务端实盘能力门依赖远端服务。
  它们不可用时应如实报告，但不得据此把本机启动判为失败、反复重启 Agent，
  或转而处理 Mac 服务。
- Windows 节点不得启动 Monitor；Monitor 及其他服务端组件由各自服务主机独立
  管理。

权威 Windows 命令如下：

```powershell
.\ops\quantx-agent.ps1 doctor -Environment dev
.\ops\quantx-agent.ps1 up -Environment dev
.\ops\quantx-agent.ps1 restart -Environment dev
.\ops\quantx-agent.ps1 status -Environment dev
.\ops\quantx-agent.ps1 logs -Environment dev
.\ops\quantx-agent.ps1 down -Environment dev
```

标准 Windows QMT Agent 重启与验收顺序：

```powershell
.\ops\quantx-agent.ps1 restart -Environment dev
.\ops\quantx-agent.ps1 status -Environment dev
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18084/health/live
```

QMT Agent 默认按本机登记的个人单账户 `live` 配置启动。显式 `-AccountId` 只用于
登记或切换已确认的唯一账户；不得为了通过检查静默降级模式。券商凭据、设备密钥
和 XTQuant 运行时继续只保留在 Windows 本机。

## 进程和依赖边界

- API 只管理 HTTP/GraphQL、数据库、Agent 会话与订阅桥接。
- Engine 独占策略管理器、条件清仓、全局做 T、热缓存和回报收敛，并使用
  PostgreSQL 租约保证单实例。
- Worker 独立连接 Prefect Server；API 重启不得停止 Worker。
- `quantx_domain` 禁止依赖数据库、文件、网络、FastAPI、Prefect 和 QMT。
- QMT Agent 只依赖 contracts，不导入服务端 ORM、Repository 或策略。
- `apps/api`、`apps/engine`、`apps/worker` 禁止导入 `miniqmt` 或 `xtquant`。
- Redis 只用于唤醒与广播，数据库消息箱和业务表才是状态真源。

## GraphQL 与前端

GraphQL/API schema 或前端查询变化后，必须在同一轮执行：

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT="http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
npm run lint
npm run test:run
npm run build
```

不得用 `as any` 掩盖契约不一致。前端 URQL HTTP 和 WebSocket 都默认使用
同 host 的 `/graphql`。

## 测试

根边界测试：

```powershell
python -m pytest tests/
```

API 测试可从根工作区按需运行：

```powershell
python -m pytest tests/api/unit/
python -m pytest tests/api/integration/
```

单元测试优先，集成测试谨慎。E2E/真实交易测试默认禁止。真实交易必须同时
显式满足 `ENV=testing`、`ENABLE_REAL_TRADING=true`、账户白名单和
`QMT_REAL_TRADING_ENABLED=true`，且不得在 production 运行。

Codex 生成的截图、trace 和 video 放在根目录 `.codex_screenshots/`，默认不
提交。

## 任务完成与提交

- Codex 确认任务完整达成并完成必要验证后，必须立即 `git commit` 本次改动，
  不得让已确认完成的代码继续处于未提交状态。
- 提交必须按实际功能合理拆分，并使用专业、清晰的 commit message；只有用户
  明确要求暂不提交时才允许例外。
- `git status`、`git diff`、`git show`、`git log`、`git rev-parse`、
  `git ls-files` 等只读检查可由主代理和任意子代理直接执行。除专用 Git 子代理
  本人外，主代理和实现子代理不得执行会改变 Git 状态的命令，也不得自行暂存或
  提交改动。
- 所有获授权的 Git 状态变更必须交给专用子代理执行；创建该子代理时固定使用
  `model="gpt-5.6-luna"` 和 `fork_turns="1"`。该专用子代理直接执行任务，
  不得为同一 Git 操作再次委派子代理。模型不可用或账户无权限时必须明确报告
  阻塞，不得换用其他模型，也不得由主代理代为执行。
- 常规任务完成时，主代理必须先完成最终审核与验证，再向专用 Git 子代理提供
  已批准的精确文件列表、验证结果和提交范围。该子代理只能检查差异、暂存批准
  文件并创建提交，不得修改实现、吸收无关改动、跳过 hooks、amend 或 push。
- 使用专用 Git 子代理不扩大操作权限。`push`、`merge`、`rebase`、`reset`、
  `checkout`、`restore`、`stash`、`cherry-pick`、分支/标签/worktree 变更及其他
  破坏性或远程操作，仍须任务本身明确授权并遵守现有安全规则；未获授权时禁止
  执行。

## 交易系统硬约束

- 回测与实盘调用同一个 `StrategyBase.step(StrategyInput)`。
- 禁止恢复 `Signal/on_bar/on_tick/generate_signal` 主路径。
- 策略只输出 `TradeIntent[]` 和算法状态补丁，不得访问账户、数据库、
  网络、文件或 QMT。
- 策略不得计算真实可卖量、冻结资金或最终合法订单数量。
- A 股合法性、T+1、涨跌停、停牌、资金、可卖量、整手与零股清仓由交易域、
  风控、OrderSizer、Broker 和状态流处理。
- 实盘成交真源只能来自 QMT Agent 上报的委托与成交回报。
- `command_ack` 只表示投递，不得推进成交；回报必须先持久化 inbox，再由
  Engine 收敛。
- 固定标的策略实例只绑定一个 `instrument_code`；账户级策略也不得自行
  选股或读取账户。
- 仓位归因使用 `locked_core/core/swing`，用户展示为
  “封存仓/核心仓/活跃仓”。
- 回测不得使用任何未来数据；缺失数据只能保守降级。
- 每次不买、少买、卖出、拒单、熔断都必须可审计。
- 券商账号、密码、QMT 配置和设备密钥不得进入服务端数据库、日志、异常
  堆栈或网络消息；设备密钥存 Windows Credential Manager。

## 文档阅读顺序

交易域、执行链路或策略接口：

1. `docs/plans/A股动态天平双仓策略实现落地规格与迁移计划.md`
2. `docs/trading/contracts/A股三层协作与执行契约.md`
3. `docs/trading/contracts/A股交易域数据结构与状态机.md`

实盘、QMT Agent 或多进程：

1. `docs/architecture/系统架构设计.md`
2. `docs/engineering/qmt-agent/README.md`
3. `docs/engineering/engine/README.md`
4. `docs/trading/contracts/A股三层协作与执行契约.md`

API、测试或部署：

1. `docs/engineering/api/README.md`
2. `docs/engineering/api/API.md`
3. `docs/engineering/api/TESTING_GUIDE.md`
4. `docs/engineering/deployment/README.md`

前端：

1. `docs/engineering/web/UI_UX_DESIGN_SYSTEM.md`
2. `apps/web/package.json`
3. `apps/web/src/core/graphql/client.ts`
4. 目标 feature 目录。
