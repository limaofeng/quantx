# QuantX Agent 记忆文件

本文档给后续 Codex / Agent 使用。进入本项目后，先读本文件，再按任务范围阅读对应代码和文档。

## 项目概览

- `backend/` 是 QuantX 后端，技术栈为 FastAPI + Strawberry GraphQL，主入口是 `backend/main.py`。
- `frontend/` 是 QuantX 前端，技术栈为 Vite + React + TypeScript。
- `交易系统文档/` 是 A 股交易域、策略契约、miniQMT 执行端和回测/进化设计的主要来源。
- `backend/docs/` 是后端工程文档，覆盖架构、API、测试、部署、策略系统和编码规范。
- `backend/core/strategies/base.py` 当前主策略接口是 `StrategyInput -> StrategyBase.step() -> StrategyOutput / TradeIntent`。
- 当前策略主线包括 `ashare_dynamic_balance_dual_bucket`、`ashare_supermarket`、`pullback_grid`，实现位于 `backend/core/strategies/`。

## 运行命令

后端开发运行使用当前项目 Python 环境；如本机通过 Conda 管理，请先激活实际环境：

```powershell
cd backend
python main.py
```

后端生产脚本：

```powershell
cd backend
.\start.bat
```

注意：`backend/start.bat` 会使用当前 Python 或 `QUANTX_PYTHON_EXE` 指定的解释器，创建 `quantx_service_runner.bat`，并通过 Windows Scheduled Task 启动 `main.py`。不要为了普通验证随意运行它。

前端开发运行：

```powershell
cd frontend
npm run dev
```

常用地址：

- 后端健康检查：`http://localhost:8080/health`
- GraphQL：`http://localhost:8080/graphql`
- Prometheus 指标：按实际部署环境查看（如有 `/metrics` 再补充准确地址）

## 开发约束

- Python 代码遵循 `backend/pyproject.toml`：2 空格缩进、双引号、Ruff、目标 Python 3.9。
- 后端配置从 `.env` 和 `.env.{ENV}` 读取；`ENV` 默认是 `development`。
- 不要恢复旧的 `Signal / on_bar / on_tick / generate_signal` 主路径。新策略决策入口是 `StrategyBase.step(input: StrategyInput)`。
- 策略只能输出 `TradeIntent[]` 和算法状态补丁，不得读写真实账户、数据库、网络、文件或 miniQMT。
- 策略不得计算真实可卖量、冻结资金、最终合法订单数量，不得把信号、下单成功或已报状态当成成交。
- A 股合法性、T+1、涨跌停、停牌、资金、可卖量、100 股整数倍、零股清仓由交易域、风控、OrderSizer、Broker 和状态流处理。
- 实盘成交真源只能来自 miniQMT 委托回报与成交回报。
- 券商账号、交易密码、资金密码、QMT 配置和任何可直接控制交易账户的凭证只允许保留在本地，不得写入 SaaS、数据库、日志、异常堆栈或网络消息。
- Redis 只作缓存，不作为交易状态、订单状态或信号传递真源。
- GraphQL/API 后端变更后，必须在同一轮内执行 `npm run codegen`，并同步更新受影响前端查询与类型映射，任何字段不一致问题禁止直接提交。
- `codegen` 校验失败只允许由 schema 与前端文档/查询不一致引起，必须先修文档与契约，再修业务代码，不得先通过 `as any` 等临时类型掩盖契约错误。
- `frontend/codegen.ts` 默认 schema 地址改为可配置：优先读取 `CODEGEN_GRAPHQL_ENDPOINT`，其次 `GRAPHQL_ENDPOINT`、`VITE_GRAPHQL_ENDPOINT`，最终回退到 `http://localhost:8080/graphql`，避免单机 IP 漂移导致生成失败。

## 测试规则

后端常用测试命令：

```powershell
cd backend
python -m pytest tests/
```

也可按范围运行：

```powershell
python -m pytest tests/unit/
python -m pytest tests/integration/
python run_tests.py unit
python run_tests.py integration
```

前端常用验证命令：

```powershell
cd frontend
npm run check
npm run lint
npm run test:run
```

测试安全要求：

- 单元测试优先，集成测试谨慎。
- E2E / 真实交易测试默认禁止。
- 涉及真实交易前，必须显式确认 `ENABLE_REAL_TRADING=true` 且 `ENV=testing`。
- 不得在 `production` 环境运行真实交易测试。
- 不要把真实交易测试加入默认测试、CI 或普通验证命令。
- 前端 GraphQL/API 变更场景下，新增/修改接口后必须执行 `npm run codegen`，再执行 `npm run check` 与关键交易查询页面（今日成交、历史成交、订单交易明细）冒烟验证，方可进入提交流程。

## 文档阅读顺序

做策略接口、交易域迁移或执行链路改造，先读：

1. `交易系统文档/A股动态天平双仓策略实现落地规格与迁移计划.md`
2. `交易系统文档/A股三层协作与执行契约.md`
3. `交易系统文档/A股交易域数据结构与状态机.md`

做策略公式或 A 股双仓策略，先读：

1. `交易系统文档/A股单标的动态天平双仓策略.md`
2. `交易系统文档/A股单标的仓位调节层设计.md`
3. `交易系统文档/A股单标的环境层设计.md`

做实盘、miniQMT 或 LocalAgent 相关工作，先读：

1. `交易系统文档/系统架构设计.md`
2. `交易系统文档/A股交易域数据结构与状态机.md`
3. `交易系统文档/A股三层协作与执行契约.md`

做后端架构、API 或测试，先读：

1. `backend/docs/README.md`
2. `backend/docs/ARCHITECTURE.md`
3. `backend/docs/API.md`
4. `backend/docs/TESTING_GUIDE.md`

做前端改动，先看：

1. `frontend/package.json`
2. `frontend/src/core/graphql/client.ts`
3. 目标 feature 目录，例如 `frontend/src/features/trading/`、`frontend/src/features/strategies/`、`frontend/src/features/portfolio/`

## 交易系统硬约束

- 回测与实盘必须调用同一个 `StrategyBase.step()` 实现。
- 策略内部禁止 `if isBacktest` 这类破坏同构的分支。
- 一个 A 股实例只绑定一个 `instrument_code`，不得在运行中自行换股。
- 仓位归因使用 `locked_core / core / swing`；面向用户展示时应使用“封存仓 / 核心仓 / 活跃仓”。
- `locked_core` 默认不能方向性卖出；如用于 T+1 同标的库存置换，必须可审计、可回滚。
- 回测不得使用未来复权因子、未来公司行为、未来停牌、未来涨跌停或未来订单结果。
- 数据缺失时只能保守降级，不能输出更激进的交易结论。
- 每次不买、少买、卖出、拒单、熔断，都应能从 `DecisionTrace` 或等价审计记录中找到原因。

## 不要做

- 不绕过 A 股交易规则。
- 不恢复旧 `Signal/on_bar/on_tick` 主路径。
- 不把 Redis、内存变量或前端状态作为交易状态真源。
- 不把策略信号、pending、accepted、下单成功写成成交。
- 不在 LocalAgent 中运行策略代码。
- 不上传、记录或持久化券商凭证。
- 不运行会真实下单的测试，除非用户明确要求并满足安全环境变量。
- 不为了验证文档改动运行 `backend/start.bat`。

## 当前实现提示

- `backend/main.py` 启动时会初始化数据库、市场数据服务、实时管理器、策略管理器、Prefect 服务和可选 MCP Server。
- `backend/config/settings.py` 中 `conda_env_name` 默认读取当前 Conda 环境；为空时使用当前 Python 解释器。
- `backend/core/strategy_executor.py` 已经消费 `StrategyInput`、`TradeIntent`、`OrderSizer`、`OrderRiskLayer`、`OrderRiskDecision` 和 broker 状态流。
- `backend/core/trading/` 包含 A 股交易域组件，例如 `market_rules`、`risk_checker`、`order_sizer`、`bucket_ledger`、`environment`、`position_adjustment`、`decision_trace`。
- `frontend/src/core/graphql/client.ts` 使用 URQL，HTTP 默认 `/graphql`，WebSocket 默认同 host 的 `/graphql`。
