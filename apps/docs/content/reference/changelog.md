# 版本记录

在线文档只展示当前部署版本。历史契约和文档通过 QuantX Git tag 与 Windows
发布包保留，不在一期提供多版本切换器。

## 变更要求

当 Strawberry Schema、Resolver、REST 请求/响应模型或权限映射变化时：

1. 通过 Caddy 公共入口执行 Web GraphQL codegen。
2. 重新导出三份客户端契约。
3. 审查 GraphQL SDL、权限和 OpenAPI diff。
4. 更新受影响的客户端指南和示例。
5. 运行契约快照、前端检查、测试和生产构建。

客户端升级前应先审查发布包中的 Schema diff，再运行 Apollo iOS codegen。
