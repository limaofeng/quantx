# 版本记录

在线文档只展示当前部署版本。历史契约和文档通过 QuantX Git tag 与 Windows
发布包保留，不在一期提供多版本切换器。

## 2026-08-15：iOS 产品目标重置

- iOS 从只读监控端调整为个人 A 股量化移动控制中心。
- 固定“今日、行情、交易、量化、资产”五 Tab，并建立完整产品与验收文档。
- 已部署的助手 `trade:approve` 预览确认继续作为安全基线。
- 手动交易、清仓、策略控制、设备 scope 和 APNs 在目标公共契约发布前保持关闭，
  不允许通过现有宽泛 Mutation 回退。

## 变更要求

当 Strawberry Schema、Resolver、REST 请求/响应模型或权限映射变化时：

1. 通过 Caddy 公共入口执行 Web GraphQL codegen。
2. 重新导出三份客户端契约。
3. 审查 GraphQL SDL、权限和 OpenAPI diff。
4. 更新受影响的客户端指南和示例。
5. 运行契约快照、前端检查、测试和生产构建。

客户端升级前应先审查发布包中的 Schema diff，再运行 Apollo iOS codegen。
