# QuantX 文档中心

本目录是 QuantX 的统一文档入口。

- 在线客户端文档：运行 `web` profile 后访问 `/docs/`，源码位于
  `apps/docs/`，只发布 iOS/第三方客户端需要的内容。
- [工程文档](engineering/README.md)：API、Engine、Worker、QMT Agent 与部署。
- [系统架构设计](architecture/系统架构设计.md)
- [A 股个人量化开发文档索引](trading/README.md)
- [A 股三层协作与执行契约](trading/contracts/A股三层协作与执行契约.md)
- [A 股交易域数据结构与状态机](trading/contracts/A股交易域数据结构与状态机.md)
- [A 股动态天平双仓策略实施计划](plans/A股动态天平双仓策略实现落地规格与迁移计划.md)
- [离线量价事件研究与 Web 查阅方案](plans/离线量价事件研究应用实现方案.md)
- [进化研究](research/进化文档.md)

```text
docs/
├── engineering/       # API、Engine、Worker、QMT Agent、部署
├── architecture/      # 系统级架构
├── trading/           # A 股公共契约与策略设计
├── research/          # 回测、进化和研究设计
└── plans/             # 功能规格、迁移与实施计划
```

新增或移动文档时同步更新本页、相关领域索引和根目录 `AGENTS.md`。
