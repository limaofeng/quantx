# QuantX 文档中心

本目录是 QuantX 项目级文档的统一入口。后端框架和 API 的工程细节仍保留在 [`backend/docs/`](../backend/docs/README.md)，项目级架构、交易域、策略研究和实施计划统一维护在这里。

## 文档结构

```text
docs/
├── architecture/                 # 系统级架构
├── trading/                      # A 股交易域与策略文档
│   ├── contracts/                # 策略无关的公共契约
│   └── strategies/               # 具体策略设计
├── research/                     # 回测、进化和研究设计
└── plans/                        # 功能规格、迁移与实施计划
```

## 主要入口

### 架构

- [系统架构设计](architecture/系统架构设计.md)：SaaS、LocalAgent、Lab、miniQMT 和状态真源。
- [后端工程文档](../backend/docs/README.md)：FastAPI、GraphQL、测试和部署。

### A 股交易系统

- [A 股个人量化开发文档索引](trading/README.md)：推荐阅读顺序和开发入口。
- [A 股三层协作与执行契约](trading/contracts/A股三层协作与执行契约.md)
- [A 股交易域数据结构与状态机](trading/contracts/A股交易域数据结构与状态机.md)
- [A 股数据源与公司行为契约](trading/contracts/A股数据源与公司行为契约.md)
- [A 股回测 Broker 与成交撮合契约](trading/contracts/A股回测Broker与成交撮合契约.md)

### 策略设计

- [A 股单标的动态天平双仓策略](trading/strategies/dynamic-balance/A股单标的动态天平双仓策略.md)
- [环境层设计](trading/strategies/dynamic-balance/A股单标的环境层设计.md)
- [风控层设计](trading/strategies/dynamic-balance/A股单标的风控层设计.md)
- [仓位调节层设计](trading/strategies/dynamic-balance/A股单标的仓位调节层设计.md)

### 研究与实施计划

- [进化文档](research/进化文档.md)
- [A 股动态天平双仓策略实现落地规格与迁移计划](plans/A股动态天平双仓策略实现落地规格与迁移计划.md)
- [持仓做 T 助手一期实现规格](plans/持仓做T助手一期实现规格.md)
- [QuantX 原生 iOS App 开发准备与实施计划](plans/QuantX原生iOSApp开发准备与实施计划.md)
- [Portfolio Orchestration Core/Swing v1.0](plans/portfolio_orchestration_core_swing_v1.0.md)
- [Pullback Grid Optimization v1](plans/pullback_grid_optimization_v1.md)
- [Studio Visual Migration Plan](plans/studio_visual_migration_plan.md)

## 维护约定

- 项目级文档放在 `docs/`，不要再创建根级的独立业务文档目录。
- 策略无关的 A 股约束放在 `docs/trading/contracts/`。
- 具体策略公式和分层设计放在 `docs/trading/strategies/<strategy>/`。
- 一次性迁移、功能规格和分阶段实施方案放在 `docs/plans/`。
- 研究、回测方法和进化设计放在 `docs/research/`。
- 新增或移动文档时同步更新本页、相关领域索引和 `AGENTS.md`。
