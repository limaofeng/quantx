# QuantX Agent 工作规则

## 执行与 token 成本

- 默认单代理完成定位、实现、验证、审核和提交。只有用户明确要求创建子代理时才使用；任务复杂、耗时长或需要审计不构成创建理由，Git 不另设代理。
- 以完成已授权任务的总 token 最少为目标：先定位目标代码与直接依赖，只读相关文档章节；已有上下文和有效证据不重复加载。搜索限定目录、模式和输出范围，避免整文件、全量日志和历史反复进入上下文。
- 优先最小完整实现；跨组件任务先确认接口、环境依赖和退出门，尽早验证最小整链，再补边界，避免组件全部完成后才发现接入缺口。不为省当轮 token 留下已知缺陷或省略必要验收。
- 小任务直接完成；长任务在原计划维护简短检查点（已完成、验证、剩余、句柄/日志），不另建轮次报告。阶段完成不等于目标完成，第一版后继续完成必要修复。
- 批量执行独立读取；实现稳定后审核和验证。只复核缺陷及受影响路径，有新证据才扩大范围；无新改动、失败或证据失效，不重复测试、审计或格式检查。
- 长进程保存句柄和日志，等待已有任务；只读新增结果或日志尾部，不反复列进程、催问、重启检查。进度只报告新证据。
- 若用户要求子代理：仅委派边界明确的独立任务，提供范围、接口、验收和证据路径，默认 fork_turns="none"，省略 model/reasoning_effort。避免频繁追加任务、换角色和互相审计；复用代理前评估历史负担。不得并发写同一范围或重复运行同一验证。

## 定位与设计

- apps/ 是 API、Web、Docs、Engine、Monitor、Worker、QMT Agent 等进程；packages/ 下 contracts 定义协议/DTO，domain 是纯交易域，application 是用例/端口，infrastructure 是数据库/适配器/消息箱。Python 包名 quantx_<组件名>；运维入口 ops/，工程文档 docs/engineering/。
- 个人单账户系统：只实现真实需求，不为多账户、多租户或未确认未来需求增加字段、状态、抽象。
- 契约变更在代码、客户端、文档、测试中原子切换；不默认添加旧协议兼容、双写、可选字段、降级或兜底。兼容必须有用户明确指定的对象和期限。
- 项目技能以 .codex/skills 为准；.agents/skills 为外部研究技能。研究不授权交易，不能作为账户、订单、成交或风控真源；.agents/vendors 和临时验证目录的 AGENTS.md 只是历史/来源快照。

## 运行与进程边界

- 生产机通过本地 SSH 密钥连接 `limao@192.168.5.6`，项目位于 `F:\Workspace\quantx`；远端 PowerShell、Codex CLI 完整路径及已知 SSH PATH 问题见 [生产机 SSH 与开发工具](docs/engineering/deployment/README.md#生产机-ssh-与开发工具)。
- Python 虚拟环境统一使用 Conda，不创建或使用 `.venv`、`venv`、virtualenv。API、Engine、Worker、macOS Research、Monitor 与验证工具使用 `quantx` Conda 环境；Windows 独立开发 Trainer 及其 Research 子进程使用 `quantx-train`，不得安装到生产环境；QMT Agent 使用 `xtquant-demo`，不得把研究依赖安装进券商环境。脚本通过明确的 Conda 环境或该环境的 Python 绝对路径运行；依赖同步工具也必须显式指向 Conda Python，不能隐式创建 `.venv`。

- Windows 主链默认为 production 实盘环境，macOS 为 dev 开发环境。Windows 可承载显式隔离的开发 Trainer：独立代码、`quantx-train`、开发配置、状态目录和开发数据权限，不继承生产或券商配置，生产 full 启停不代管 Trainer。QMT/XTData/XTTrading、券商运行时和设备密钥只在 Windows；macOS 使用独立本地数据服务、paper 执行和只读远程行情接口，不连接生产数据库、Redis、Prefect，也不启动生产 Engine 或 QMT Agent。
- 所有高资源 Research CLI（含直接训练/准备作业入口）必须通过共享主机门禁，机器策略位置与格式见 apps/trainer/README.md。缺少策略、锁状态不明或旧执行未收敛时禁止绕过；开发 ENV 不解除实盘时段保护。异常运行证据不得通过直接删除来恢复任务。
- 根目录统一入口：
  ```powershell
  .\ops\quantx.ps1 up -Environment production -Profile full
  .\ops\quantx.ps1 status
  .\ops\quantx.ps1 logs
  .\ops\quantx.ps1 down
  ```
  实盘重启顺序 down → up → status；Windows 默认 production/full/live，实盘开关必须在生产配置中显式启用。macOS 使用 ops/quantx.sh，默认 dev/full/paper，禁止 live。两端可使用 data-only。Monitor 独立管理；数据服务由运维单独管理。
- 唯一账户来自显式 -AccountId 或本机唯一配置。QMT 预检失败须在 API/Engine 启动前关闭服务端及 Agent 实盘门、清空账户允许列表、跳过 QMT 子进程；其他服务继续，显示 DEGRADED / QMT BLOCKED / liveTrading=DISABLED。可用持久化行情回测，不伪装 ready；修复 QMT 后整体重启，不静默切 data-only。
- 实盘验收：full、live、唯一账户、liveTrading=ENABLED、QMT ready、quantx_contracts.agent.PROTOCOL_VERSION 对应协议、新鲜快照 <90 秒。日常实盘使用 ENV=production；ENV=testing 仅用于单独授权的真实交易测试，普通测试关闭实盘。
- Caddy 唯一公开入口：本机 http://127.0.0.1:8080，局域网 http://192.168.5.6:8080；内部 API 18081、Vite 5250、VitePress 5251。远程开发使用运行端 Caddy 地址，127.0.0.1 只指执行命令的机器。
- PostgreSQL/InfluxDB/Redis/Prefect Server 是外部服务，只检查不自动启停。Prefect API 取 PREFECT_API_URL，默认 http://192.168.5.6:30420/api，pool=quantx-pool。生产 data-only 使用生产数据服务；macOS 开发使用本机独立 PostgreSQL、Redis、InfluxDB 和 Prefect，数据库命名以 _dev 结尾。
- 不独立启动 QMT Agent，不恢复 API 子进程管理、WinSW、Kubernetes 或 release 安装/回滚。
- API 仅负责 HTTP/GraphQL、数据库、Agent 会话和订阅桥接；Engine 独占策略管理、条件清仓、全局做 T、热缓存、回报收敛，并以 PostgreSQL 租约保证单实例；Worker 独立连接 Prefect，API 重启不得停止它。
- domain 不依赖数据库、文件、网络、FastAPI、Prefect、QMT；QMT Agent 只依赖 contracts，不导入服务端 ORM/Repository/策略；API/Engine/Worker 禁止导入 miniqmt/xtquant。Redis 仅唤醒/广播，业务表和数据库消息箱是真源。

## 交易硬约束

- 回测与实盘共用 StrategyBase.step(StrategyInput)，不得恢复 Signal/on_bar/on_tick/generate_signal 主路径。策略仅输出 TradeIntent[] 和算法状态补丁，不访问账户、数据库、网络、文件、QMT，不计算真实可卖量、冻结资金或最终合法数量。
- A 股合法性、T+1、涨跌停、停牌、资金、可卖量、整手、零股清仓由交易域、风控、OrderSizer、Broker 和状态流处理。每次不买、少买、卖出、拒单、熔断可审计；回测禁止未来数据，缺失数据保守降级。
- 实盘成交只认 QMT Agent 委托/成交回报；command_ack 仅表示投递。回报先持久化 inbox，再由 Engine 收敛。
- 固定标的实例只绑定一个 instrument_code；账户级策略也不自行选股或读账户。仓位归因 locked_core/core/swing，展示“封存仓/核心仓/活跃仓”。
- 券商账号、密码、QMT 配置、设备密钥不得进入服务端数据库、日志、异常堆栈、网络消息；设备密钥存 Windows Credential Manager。

## 验证与提交

- 先受影响单测，必要时扩大；根目录 conda run -n quantx python -m pytest tests/，API 范围为 tests/api/unit/ 或 tests/api/integration/。普通 pytest 由 tests/conftest.py 使用专用测试库并关闭实盘门，可自主修复本次引起的失败；集成测试先确认外部状态影响。
- 默认禁止 E2E/真实交易；真实交易需明确授权且同时满足 ENV=testing、ENABLE_REAL_TRADING=true、账户白名单、QMT_REAL_TRADING_ENABLED=true。普通测试授权不扩展到真实交易。
- 恢复/迁移失败先看脱敏日志和定向测试；仅恢复现场不可用或备份改变时重导整库，小规模测试不替代完整恢复验收。
- Web 保留标准/紧凑密度，遵守 docs/engineering/web/UI_UX_DESIGN_SYSTEM.md。GraphQL/schema/查询变化须使用 quantx-graphql-codegen 技能，实际 Caddy 端点验证：
  ```powershell
  $env:CODEGEN_GRAPHQL_ENDPOINT="http://127.0.0.1:8080/graphql"
  npm run codegen
  npm run check
  npm run lint
  npm run test:run
  npm run build
  ```
  不用 as any 掩盖契约错误；URQL HTTP/WebSocket 默认同 host /graphql。
- 截图、trace、video 放根目录 .codex_screenshots/，默认不提交。
- 主代理完成最终审核和必要验证后立即提交已完成改动（用户要求不提交除外）；按实际功能拆分，使用清晰的 conventional commit message。只暂存核对过的精确文件，不混入无关改动，不跳过 hooks，不另启动提交后审计流程，除非用户要求或有新缺陷证据。
- push、merge、rebase、reset、checkout、restore、stash、cherry-pick、amend、分支/标签/worktree 变更及其他破坏性/远程操作仍须任务明确授权。

## 按需文档入口

仅按任务读取相关章节，不要求通读列表：

- 交易域/策略/执行：docs/plans/A股动态天平双仓策略实现落地规格与迁移计划.md；docs/trading/contracts/ 下 A股三层协作与执行契约.md、A股交易域数据结构与状态机.md。
- 多标的做 T：docs/architecture/ 下 系统架构设计.md、多标的做T助手新架构设计.md；docs/plans/ 下 多标的做T助手新架构开发实施方案.md、持仓做T有状态机会引擎V3实施规格.md；docs/trading/contracts/A股自动退出计划与卖出策略契约.md。
- 实盘/多进程：docs/architecture/系统架构设计.md；docs/engineering/qmt-agent/README.md、engine/README.md；三层协作与执行契约。
- API/测试：docs/engineering/api/ 下 README.md、API.md、TESTING_GUIDE.md；运维：docs/engineering/deployment/README.md。
- 前端：设计系统、apps/web/package.json、apps/web/src/core/graphql/client.ts、目标 feature。
