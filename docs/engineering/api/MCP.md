# MCP 状态说明

仓库保留 `quantx_api.quantx_mcp` 的实验性工具实现和协议测试，但当前支持的
Monorepo 运行配置没有把 `/mcp` 挂载到 FastAPI，也没有在 Caddy 公开该
路径。因此 MCP 不属于 `web` 或 `full` profile 的受支持公共接口，不能把
可选集成测试的跳过结果理解为已部署。

在重新开放 MCP 前必须同时完成：

1. 明确 Caddy 路由和 API 生命周期所有权。
2. 为所有读取及交易工具接入与 GraphQL/REST 相同的用户、账户和权限边界。
3. 下单与撤单只写入 `TradeCommand`，不得直接访问 QMT。
4. 增加公共入口集成测试、速率限制、审计和生产默认关闭策略。

旧单体时期的能力清单与启动说明保存在
[archive/legacy-monolith/MCP.md](archive/legacy-monolith/MCP.md)，仅供历史
追溯。
