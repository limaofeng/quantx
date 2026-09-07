# QuantX Agent 记忆文件

按任务范围读取代码和下列文档入口；局部修复不要求通读架构和计划。已授权范围内完成实现、必要验证和修复，不在第一版实现后提前停下。

## 代码与文档入口

- `apps/`：API、Web、Docs、Engine、Monitor、Worker、QMT Agent 等独立进程/客户端。
- `packages/contracts`：协议与 DTO；`domain`：纯交易域；`application`：用例与端口；
  `infrastructure`：数据库、适配器和消息箱。
- `ops/`：当前 Windows 统一运维入口；`docs/engineering/`：各组件工程文档。
- Python 包名为 `quantx_<组件名>`；从目标组件及其直接依赖开始定位。

## 设计与维护准则

- 所有架构、协议和数据模型必须从 QuantX 的真实需求出发，保持设计合理、
  简洁且边界清晰。项目是个人单账户系统，不得为臆想中的多账户、多租户或
  未确认的未来需求增加字段、状态、分支和抽象。
- 不为旧实现、旧协议或未部署版本默认增加兼容层、双协议、可选字段、降级分支
  或兜底逻辑。契约调整必须在代码、客户端、文档和测试中原子切换到唯一权威
  设计；只有用户明确要求兼容，并明确兼容对象和期限时，才允许引入受控兼容。

## 统一运行方式

平台约定（2026-09-07）：生产/实盘运行端为 **Windows**；当前开发环境也为
**Windows**。后续计划将开发环境迁移到 **macOS**，目前尚未启用该开发拓扑。
这里的生产指实际交易运行端；当前启动器仍只接受 `-Environment dev`，不得据此
传入不存在的 `production` 参数或改变实盘门禁。

普通开发启动不显式传 `-Mode`，必须解析为 `profile=full`、`agentMode=live`。
只有明确需要禁用实盘时，才使用 `-Mode data-only`。

未来迁移时保留以下边界，不为未来迁移提前实现双启动器或兼容层：
- QMT/XTData/XTTrading 和券商运行时始终在 Windows，设备密钥仍留在该机。
- 纯域、contracts 和不依赖 QMT 的开发工具保持平台无关；Windows 进程、路径和
  凭据管理隔离在运行适配与运维层，不散落到业务逻辑。
- macOS 开发机与 Windows 运行端的服务分布、数据隔离、启动及验收方式需在迁移
  任务中明确。不得默认让 Mac 测试访问 Windows 实盘状态或启动另一份 Engine。
- 若 Mac 使用 Windows 后端，使用该运行端的 Caddy 公共地址；`127.0.0.1` 仅指
  当前执行命令的机器，不能因换开发机就替换已确认的远端地址。

当前 Windows 命令从仓库根目录执行：
```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 status
.\ops\quantx.ps1 logs
.\ops\quantx.ps1 down
```
标准实盘重启顺序为 `down` → 上述 `up` → `status`。Monitor 独立使用
`-Component monitor` 管理，普通 up/down 不启停它。

- 普通 up 保持 full/live；唯一账户来自显式 `-AccountId` 或本机唯一账户配置。
  不得静默改为 data-only。QMT 预检失败时，在 API/Engine 启动前关闭服务端与
  Agent 实盘能力门、清空账户允许列表并跳过 QMT 子进程；非 QMT 服务继续启动，
  显示 DEGRADED / QMT BLOCKED / liveTrading=DISABLED。可用持久化行情回测，
  不得伪装 QMT ready；恢复 QMT 后整体重启。
- 完整实盘验收必须显示 Runtime profile=full、agentMode=live、唯一账户、
  liveTrading=ENABLED、QMT ready、当前 `quantx_contracts.agent.PROTOCOL_VERSION`
  定义的协议版本和小于 90 秒的新鲜快照。
- Caddy 是唯一公开入口：Windows 本机 `http://127.0.0.1:8080`，当前局域网
  `http://192.168.5.6:8080`。API 内部端口 18081，Vite 5250，VitePress 5251。
- PostgreSQL、InfluxDB、Redis、Prefect Server 是外部服务，只检查、不自动启停；
  Prefect API 由 PREFECT_API_URL 指定，当前默认 `http://192.168.5.6:30420/api`，
  Worker pool 为 quantx-pool。data-only 继续复用开发数据服务，不另建实盘套件。
- 不绕过统一入口独立启动 QMT Agent，不恢复 API 子进程管理、WinSW、Kubernetes
  或 release 安装/回滚。当前启动器只支持 dev，macOS 开发流程留待迁移任务实现。
- 运维与启动任务按需查阅 `docs/engineering/deployment/README.md`。

## 进程和依赖边界

- API 只管理 HTTP/GraphQL、数据库、Agent 会话与订阅桥接。
- Engine 独占策略管理器、条件清仓、全局做 T、热缓存和回报收敛，并使用
  PostgreSQL 租约保证单实例。
- Worker 独立连接 Prefect Server；API 重启不得停止 Worker。
- `quantx_domain` 禁止依赖数据库、文件、网络、FastAPI、Prefect 和 QMT。
- QMT Agent 只依赖 contracts，不导入服务端 ORM、Repository 或策略。
- `apps/api`、`apps/engine`、`apps/worker` 禁止导入 `miniqmt` 或 `xtquant`。
- Redis 只用于唤醒与广播，数据库消息箱和业务表才是状态真源。

## 技能与外部研究

项目维护的技能以 `.codex/skills` 为准；`.agents/skills` 是本机安装的外部研究技能。
外部研究输出不能充当 QuantX 账户、订单、成交或风控真源。研究请求不授权交易执行。
`.agents/vendors` 和临时验证目录中的 AGENTS.md 仅是来源/历史快照，不作为当前规则。

## GraphQL 与前端

Web 支持标准（默认）与紧凑两种界面密度；字体、控件和间距必须遵循
`docs/engineering/web/UI_UX_DESIGN_SYSTEM.md` 的可选界面密度规范，保留布局与交易语义。

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

## 子代理配置

- 获授权创建的实现、审计和 Git 子代理，默认继承主代理当前模型与推理强度；创建时
  省略 `model` 和 `reasoning_effort`，不固定模型名称、不强制 max。只有用户明确指定
  不同配置时才覆盖。角色分工与模型选择分开，专用 Git 代理的权限边界仍保留。
- 默认提供自包含任务包并使用 `fork_turns="none"`；确需历史时只传必要轮次，不能把
  一轮历史当作固定的小上下文。任务包包含范围、接口约束、验收方式和必要证据路径。
- 优先复用仍有效的代理；若主代理配置、权限环境已变化或代理失效，先确认旧任务
  已停止并核对进程与工作树，再按当前配置新建替代代理，交接检查点和剩余工作。
  已有代理不视为会自动跟随主代理配置变化；替换不授权并发重复操作或权限绕过。

## 长任务与协作成本

- 大目标按可验收批次推进，保留完整目标；每批记录范围、文件、通过的检查和剩余问题。
  在现有计划中更新简短检查点，不为每轮新增报告。阶段完成不等于整个目标完成。
- 获授权使用子代理时，以互不重叠的写入范围分工；提供必要接口、约束和证据路径，
  避免默认复制整段历史。实现尚在变化时，审计先核对固定契约，不持续追读共享工作树。
  实现提交待审的稳定文件集合后，再做完整审计；整改复核只覆盖缺陷与受影响路径，
  有新证据时才扩大，不以轮数限制必要的安全验证。
- 复用仍有效的测试证据；代码、依赖或环境变化使证据失效时重跑对应检查。
  全量回归在批次稳定后运行，不与多路实现同时反复运行同一套测试。
- 长进程保存任务句柄、阶段、日志路径和退出码。优先等待已有句柄或完成事件，
  不用反复列进程、发消息或读取全量日志替代等待。必要轮询在工具允许范围内退避；
  更新只描述新证据。等待不是完成，也不因正常耗时而重复启动任务。
- 恢复/迁移失败先读脱敏日志、定位失败阶段并运行定向测试；只有已有恢复现场不可用
  或备份已改变时才重新导入整库。小规模迁移测试不能替代最终完整恢复验收。

## 测试执行

全测试目录（按影响范围决定是否需要全量运行）：

```powershell
python -m pytest tests/
```

API 测试可从根工作区按需运行：

```powershell
python -m pytest tests/api/unit/
python -m pytest tests/api/integration/
```

优先运行受影响的单元测试；通过后仅在新改动或新证据需要时扩大验证。
普通 pytest 由 `tests/conftest.py` 选择专用测试库并关闭实盘门禁，可在任务范围内
自主执行和修复本次引起的失败；不得将这一授权扩展到 E2E 或真实交易。
集成测试先确认其外部状态影响。E2E/真实交易测试默认禁止。真实交易必须同时
显式满足 `ENV=testing`、`ENABLE_REAL_TRADING=true`、账户白名单和
`QMT_REAL_TRADING_ENABLED=true`。项目只在 `ENV=testing` 的 Dev 实盘门禁下运行。

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
- 所有获授权的 Git 状态变更必须交给专用 Git 子代理执行，模型与推理强度遵循下述
  统一子代理政策。该子代理直接执行任务，不得为同一 Git 操作再次委派；主代理不得
  自行暂存或提交。不可用时先判断代理会话是否失效，不能擅自换模型或扩大权限。
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

## 按需文档入口

只读与变更相关的文档和章节；已有足够上下文时不重复加载。

交易域、执行链路或策略接口：

1. `docs/plans/A股动态天平双仓策略实现落地规格与迁移计划.md`
2. `docs/trading/contracts/A股三层协作与执行契约.md`
3. `docs/trading/contracts/A股交易域数据结构与状态机.md`

多标的做 T 架构或迁移：

1. `docs/architecture/系统架构设计.md`
2. `docs/architecture/多标的做T助手新架构设计.md`
3. `docs/plans/多标的做T助手新架构开发实施方案.md`
4. `docs/plans/持仓做T有状态机会引擎V3实施规格.md`
5. `docs/trading/contracts/A股自动退出计划与卖出策略契约.md`

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
