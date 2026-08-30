# QuantX Web 工程文档

- [Web UI/UX 设计系统](UI_UX_DESIGN_SYSTEM.md)：桌面端 Studio 的颜色、交互、
  金融语义与验收规则；所有 Web 前端改动首先遵循此规范。
- Web 客户端源码：`apps/web/`。
- GraphQL 客户端入口：`apps/web/src/core/graphql/client.ts`。

Web 对外开发者文档位于 `apps/docs/`；本目录记录仓库内实现和维护规则。

## GraphQL 性能排障

开发构建会记录最近 200 次实际进入网络层的 GraphQL query/mutation。浏览器控制台
可使用：

```text
debugPerformance.graphql()                 # 请求级客户端/服务端/SQL耗时
debugPerformance.graphqlFields()           # 最近请求的字段和 SQL 指纹明细
debugPerformance.graphqlFields("req-...")  # 按 X-Request-ID 查找
debugPerformance.clearGraphql()            # 清空本地环形缓冲区
```

URQL 缓存直接命中的结果不会记为网络请求。`客户端毫秒` 包含鉴权刷新、网络和服务端
完整响应时间；`GraphQL 毫秒` 是服务端 GraphQL operation 本身；`外围毫秒` 是两者
差值，用于识别网络、context 认证、结果格式化和序列化开销。更细的传输阶段可在
Network 面板的响应 `Server-Timing` 中查看。字段明细来自
`extensions.quantxTiming`，只在已认证的开发调试请求中返回，不保存 variables 或
业务响应正文。

## 手工委托

Web 交易票据与 iOS 共用服务端手工委托用例：先读取
`orderEntryCapabilities`，再通过 `previewManualOrder` 生成带行情、风控和有效期
绑定的短时预览，最后由用户显式调用 `confirmManualOrder` 消费一次性挑战。

- 默认执行模式必须是 `PAPER`；只有 capability 明确允许当前买卖方向时才展示
  `LIVE`。
- `trade:manual` 必须显式授予，不能由 `orders:write` 或管理员身份推导；缺少权限
  时票据保持只读并展示服务端阻断原因。
- 报价类型只展示 capability 返回的 `LIMIT` 或 `BEST`，不得回退到旧的通用市价
  映射。
- Web 不得以 `placeOrder` 作为手工委托回退路径。
- 确认成功只表示交易命令进入队列；最终状态以 QMT Agent 上报的券商委托和成交
  回报为准。
