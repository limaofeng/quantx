# QuantX Web 工程文档

- [Web UI/UX 设计系统](UI_UX_DESIGN_SYSTEM.md)：桌面端 Studio 的颜色、交互、
  金融语义与验收规则；所有 Web 前端改动首先遵循此规范。
- Web 客户端源码：`apps/web/`。
- GraphQL 客户端入口：`apps/web/src/core/graphql/client.ts`。

Web 对外开发者文档位于 `apps/docs/`；本目录记录仓库内实现和维护规则。

## DEV 页面加载

开发访问仍通过 Caddy `8080`，保留 Vite HMR 和开发 source map。

- Caddy 只对前端响应中的 HTML、JavaScript、CSS、JSON 和 SVG 启用 gzip
  level 1，最小响应长度为 1024 字节。API、GraphQL、Agent、Monitor 和文档代理
  在该处理器之前分流，不改变其流式连接及响应语义；原有缓存头保留。
- Vite 在启动时仅预热 `main.tsx`、`generated/gql/graphql.ts` 和
  `TTradeGlobalPage.tsx`。纯生成目录跳过 React/Babel 插件，继续由 esbuild
  处理 TypeScript 和 source map；业务模块保留 GraphQL optimizer 与 Fast Refresh。
- 路由直接导入页面文件，不导入 feature 聚合出口。首页不自动预加载其他页面；
  导航栏、功能启动器及标签的悬停或键盘聚焦才触发预加载。预加载与真正导航共享
  同一加载 Promise，推测加载失败不会产生未处理异常，后续导航可以重试。
- 做 T 首屏只加载实时监控。回放组件及 Recharts、诊断、动态和参数编辑器按需加载，
  局部 Suspense/错误边界保留外层工具栏、导航和草稿。回放与实时查询的暂停条件、
  订阅和交易操作契约不因模块拆分改变。
- 使用 `useFragment` 时直接导入 `generated/gql/fragment-masking`，避免把完整
  GraphQL 查询查找表带入运行时；静态 `gql(...)` 继续使用现有 codegen optimizer。

验收应分别记录服务启动后的首次访问、相同缓存条件的多次刷新和已访问页面的切换。
区分“页面代码已就绪”和“业务数据已返回”，检查响应 `Content-Encoding: gzip`、
Network 中的实际传输字节数与首屏依赖，再确认 GraphQL/行情连接和 HMR 正常。
不要把开发工具控制开销或单次波动当作稳定的加载改善比例。

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

- 默认执行模式必须读取 `orderEntryCapabilities.defaultExecutionMode`：账户级
  `liveTrading` 有效且当前买卖方向允许实盘时使用 `LIVE`，否则使用 `PAPER`。
  Web 与 iOS 都不得提供 PAPER/LIVE 手工切换；最终模式只读展示，并在预览请求中
  显式携带以供服务端校验和审计。
- `trade:manual` 必须显式授予，不能由 `orders:write` 或管理员身份推导；缺少权限
  时票据保持只读并展示服务端阻断原因。
- 报价类型只展示 capability 返回的 `LIMIT` 或 `BEST`，不得回退到旧的通用市价
  映射。
- `LIMIT` 的 60 秒确认票据不因单只证券 30 秒无新行情事件而提前失效；此时服务端
  返回参考价陈旧警告，Web 继续明确展示行情时间。`BEST` 仍要求新鲜对手价。
- Web 不得以 `placeOrder` 作为手工委托回退路径。
- 确认成功只表示交易命令进入队列；最终状态以 QMT Agent 上报的券商委托和成交
  回报为准。

持仓页“委托”工作区将下单请求与券商委托分为三个页签：`下单请求`、`当日券商委托`
和`历史券商委托`。`下单请求`由 `manualOrderAttempts` 从服务端恢复
`PendingTradeOrder + TradeCommandOutbox`，不会伪造 `Order`；它展示服务端投影阶段、
原始状态、原因和完整时间线，并允许复制 `clientOrderId` / `brokerOrderId`。

请求阶段按 `QUEUED / DELIVERED / AGENT_ACKNOWLEDGED` 每 2 秒刷新；仅有
`RECONCILE_REQUIRED` 时每 10 秒刷新，全部终结后停止，页面不可见时暂停。刷新或
重新登录后仍以服务端列表为准。只有 `BROKER_ORDER_CREATED` 才能打开券商委托关联；
对账异常显示“结果待核对，禁止重复下单”，不提供自动重试、撤单或猜测性跳转。
