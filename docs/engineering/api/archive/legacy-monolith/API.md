# 📡 QuantX GraphQL API

QuantX 提供完整的 GraphQL API，支持策略管理、市场数据查询、交易操作和实时数据订阅。

## 📋 目录

- [API 概览](#api-概览)
- [认证授权](#认证授权)
- [查询接口 (Query)](#查询接口-query)
- [变更接口 (Mutation)](#变更接口-mutation)
- [订阅接口 (Subscription)](#订阅接口-subscription)
- [数据类型](#数据类型)
- [错误处理](#错误处理)
- [使用示例](#使用示例)

## 🌐 API 概览

### 接口地址
- **开发环境**: `http://localhost:8000/graphql`
- **生产环境**: `https://api.quantx.com/graphql`

### 特性支持
- ✅ **查询优化**: DataLoader 批量查询
- ✅ **实时订阅**: WebSocket 支持
- ✅ **分页查询**: Cursor-based 分页
- ✅ **字段选择**: GraphQL 按需查询
- ✅ **类型安全**: 完整的类型定义
- ✅ **文档自省**: 内置 API 文档

### GraphQL Playground
访问 `/graphql` 可以使用交互式查询工具，支持：
- 查询编写和测试
- Schema 浏览
- 实时订阅测试
- 查询历史记录

## 🔐 认证授权

### JWT Token 认证
```http
Authorization: Bearer <jwt_token>
```

### 原生客户端 Token
原生客户端会话使用独立 REST 接口，不通过 GraphQL Mutation 传递密码：

```http
POST /auth/session
Content-Type: application/json

{"username":"<local-user>","password":"<password>","deviceName":"iPhone"}
```

成功响应包含短期 `accessToken`、一次性轮换的 `refreshToken`、两个过期时间、设备会话 ID、只读权限和授权账户集合。刷新、查询和撤销接口为：

```text
POST   /auth/session/refresh
GET    /auth/session
DELETE /auth/session?allDevices=false
```

### Web 会话

Web 前端使用独立端点，Refresh Token 只写入 `HttpOnly`、`SameSite=Strict` Cookie，不会出现在 JSON 响应或浏览器存储中：

```text
POST   /auth/web/session
POST   /auth/web/session/refresh
DELETE /auth/web/session
```

Web 登录与刷新响应只包含短期 `accessToken`、过期时间、设备会话 ID 和用户信息。Web 会话端点校验浏览器 `Origin`，生产环境强制使用 Secure Cookie。GraphQL HTTP 继续使用 Bearer Header，WebSocket 在 `connection_init.Authorization` 中发送同一 Access Token。

开发环境可以显式设置 `AUTH_DEVELOPMENT_AUTO_LOGIN=True` 与
`AUTH_DEVELOPMENT_USERNAME=<database-user>`，由前端调用
`POST /auth/web/session/development` 为该数据库用户建立 Web 会话。此接口不接收或返回密码，
同时要求后端 `ENV=development`、来源通过 Web Origin 校验；在非开发环境或未启用时返回
`404 DEVELOPMENT_LOGIN_DISABLED`。前端还必须显式设置
`VITE_AUTH_DEVELOPMENT_AUTO_LOGIN=true`，生产构建不会尝试该接口。

Refresh Token 只以 HMAC 摘要保存；每次刷新都会轮换 Token，并保留已消费摘要用于检测重放。访问 Token 不携带资金账号。单用户本地部署的引导权限包含 `mutation:write`，实际账户访问和交易合法性仍由后端账户授权与交易风控校验。

运行前端 GraphQL codegen 时，需通过 `CODEGEN_GRAPHQL_TOKEN` 注入短期 Access Token；生成配置只从进程环境读取该值，不得将 Token 写入代码、`.env` 示例或生成产物。

## 🔍 查询接口 (Query)

### 策略管理

#### 1. 获取策略列表
```graphql
query GetStrategies {
  strategies {
    id
    name
    description
    filePath
    className
    createTime
    defaultParameters
  }
}
```

#### 2. 获取策略详情
```graphql
query GetStrategy($id: Int!) {
  strategy(id: $id) {
    id
    name
    description
    filePath
    className
    defaultParameters
    runs {
      id
      mode
      status
      instruments
      parameters
      startTime
      metrics
    }
  }
}
```

#### 3. 获取策略运行实例
```graphql
query GetStrategyRuns($status: StrategyStatus) {
  strategyRuns(status: $status) {
    id
    strategyId
    strategyName
    mode
    instruments
    status
    startTime
    stopTime
    metrics
    errorMessage
  }
}
```

### 市场数据

#### 1. 获取金融工具信息
```graphql
query GetInstruments($symbols: [String!]) {
  instruments(symbols: $symbols) {
    symbol
    name
    market
    type
    status
    lotSize
    tickSize
    currentPrice
    changePercent
  }
}
```

#### 2. 获取历史 K线数据
```graphql
query GetKlines(
  $stockCode: String!
  $period: String!
  $startTime: DateTime
  $endTime: DateTime
  $limit: Int
  $order: String
) {
  klines(
    stockCode: $stockCode
    period: $period
    startTime: $startTime
    endTime: $endTime
    limit: $limit
    order: $order
  ) {
    stockCode
    period
    timestamp
    open
    high
    low
    close
    volume
    amount
  }
}
```

参数说明：
- order: 返回排序方向，可选 `asc` 或 `desc`，默认 `desc`。


#### 3. 获取历史 Tick 数据
```graphql
query GetTicks(
  $stockCode: String!
  $startTime: DateTime
  $endTime: DateTime
  $limit: Int
  $order: String
) {
  ticks(
    stockCode: $stockCode
    startTime: $startTime
    endTime: $endTime
    limit: $limit
    order: $order
  ) {
    stockCode
    period
    time
    lastPrice
    open
    high
    low
    preClose
    volume
    amount
  }
}
```

参数说明：
- order: 返回排序方向，可选 `asc` 或 `desc`，默认 `desc`。

#### 4. 获取实时市场数据
```graphql
query GetMarketData($symbols: [String!]!) {
  marketData(symbols: $symbols) {
    symbol
    price
    change
    changePercent
    volume
    turnover
    timestamp
    bid
    ask
    high
    low
  }
}
```

### 交易管理

#### 1. 获取订单列表
```graphql
query GetOrders($status: OrderStatus, $limit: Int) {
  orders(status: $status, limit: $limit) {
    id
    symbol
    side
    type
    quantity
    price
    filledQuantity
    status
    createTime
    updateTime
  }
}
```

#### 2. 获取持仓信息
```graphql
query GetPositions {
  positions {
    symbol
    quantity
    availableQuantity
    averagePrice
    marketValue
    unrealizedPnl
    realizedPnl
    updateTime
  }
}
```

#### 3. 获取账户信息
```graphql
query GetAccount {
  account {
    id
    totalValue
    availableCash
    frozenCash
    marketValue
    totalPnl
    dayPnl
    positions {
      symbol
      quantity
      marketValue
    }
  }
}
```

### 工作流管理

#### 1. 获取工作流列表
```graphql
query GetWorkflows {
  workflows {
    id
    name
    status
    lastRun
    nextRun
    description
  }
}
```

#### 2. 获取工作流执行历史
```graphql
query GetWorkflowRuns($workflowId: String!, $limit: Int) {
  workflowRuns(workflowId: $workflowId, limit: $limit) {
    id
    status
    startTime
    endTime
    duration
    logs
  }
}
```

## ✏️ 变更接口 (Mutation)

### 策略管理

#### 1. 创建策略模板
```graphql
mutation CreateStrategy($input: StrategyInput!) {
  createStrategy(input: $input) {
    success
    message
    strategy {
      id
      name
      description
    }
  }
}
```

#### 2. 启动策略实例
```graphql
mutation StartStrategy($input: StartStrategyInput!) {
  startStrategy(input: $input) {
    success
    message
    runId
  }
}
```

**输入参数**:
```graphql
input StartStrategyInput {
  strategyId: Int!
  mode: StrategyRunMode!
  instruments: [String!]!
  parameters: String
}
```

#### 3. 停止策略实例
```graphql
mutation StopStrategy($runId: String!) {
  stopStrategy(runId: $runId) {
    success
    message
  }
}
```

### 交易操作

#### 1. 下单
```graphql
mutation PlaceOrder($input: OrderInput!) {
  placeOrder(input: $input) {
    success
    message
    order {
      id
      symbol
      side
      quantity
      price
      status
    }
  }
}
```

**输入参数**:
```graphql
input OrderInput {
  symbol: String!
  side: OrderSide!
  type: OrderType!
  quantity: Float!
  price: Float
  timeInForce: TimeInForce
}
```

#### 2. 撤单
```graphql
mutation CancelOrder($orderId: String!) {
  cancelOrder(orderId: $orderId) {
    success
    message
  }
}
```

#### 3. 清仓操作
```graphql
mutation ClearPosition($symbol: String!) {
  clearPosition(symbol: $symbol) {
    success
    message
    orderId
  }
}
```

### 工作流操作

#### 1. 触发工作流
```graphql
mutation TriggerWorkflow($workflowId: String!, $parameters: String) {
  triggerWorkflow(workflowId: $workflowId, parameters: $parameters) {
    success
    message
    runId
  }
}
```

## 📡 订阅接口 (Subscription)

### 实时市场数据

#### 1. 订阅实时行情
```graphql
subscription MarketQuotes($stockList: [String!]!) {
  marketQuotes(stockList: $stockList) {
    stockCode
    currentPrice
    change
    changePercent
    volume
    amount
    timestamp
  }
}
```

#### 2. 订阅 K线 数据
```graphql
subscription MarketKlines($stockList: [String!]!, $periods: [String!]!) {
  marketKlines(stockList: $stockList, periods: $periods) {
    stockCode
    period
    timestamp
    open
    high
    low
    close
    volume
    amount
  }
}
```

#### 3. 订阅 Tick 数据
```graphql
subscription MarketTicks($stockList: [String!]!) {
  marketTicks(stockList: $stockList) {
    stockCode
    period
    time
    lastPrice
    open
    high
    low
    preClose
    volume
    amount
  }
}
```

### 交易数据订阅

#### 1. 订单状态更新
```graphql
subscription OrderUpdates {
  orderUpdates {
    id
    symbol
    status
    filledQuantity
    updateTime
  }
}
```

#### 2. 成交记录推送
```graphql
subscription TradeStream {
  tradeStream {
    id
    orderId
    symbol
    side
    quantity
    price
    timestamp
  }
}
```

### 策略状态订阅

#### 1. 策略运行状态
```graphql
subscription StrategyUpdates($runId: String) {
  strategyUpdates(runId: $runId) {
    runId
    status
    metrics
    positions
    updateTime
  }
}
```

## 📊 数据类型

### 基础枚举类型

#### StrategyRunMode (策略运行模式)
```graphql
enum StrategyRunMode {
  BACKTEST  # 回测模式
  PAPER     # 模拟盘
  LIVE      # 实盘
}
```

#### StrategyStatus (策略状态)
```graphql
enum StrategyStatus {
  CREATED   # 已创建
  RUNNING   # 运行中
  STOPPED   # 已停止
  ERROR     # 错误状态
}
```

#### OrderSide (订单方向)
```graphql
enum OrderSide {
  BUY   # 买入
  SELL  # 卖出
}
```

#### OrderType (订单类型)
```graphql
enum OrderType {
  MARKET  # 市价单
  LIMIT   # 限价单
  STOP    # 止损单
}
```

#### OrderStatus (订单状态)
```graphql
enum OrderStatus {
  PENDING    # 待成交
  FILLED     # 已成交
  CANCELLED  # 已取消
  REJECTED   # 已拒绝
}
```

### 复合类型

#### Strategy (策略模板)
```graphql
type Strategy {
  id: Int!
  name: String!
  description: String!
  filePath: String!
  className: String!
  defaultParameters: String!
  createTime: DateTime!
  updateTime: DateTime!
}
```

#### StrategyRun (策略实例)
```graphql
type StrategyRun {
  id: String!
  strategyId: Int!
  strategyName: String!
  mode: StrategyRunMode!
  instruments: [String!]!
  parameters: String!
  status: StrategyStatus!
  startTime: DateTime
  stopTime: DateTime
  metrics: String
  errorMessage: String
  createTime: DateTime!
}
```

#### Order (订单)
```graphql
type Order {
  id: String!
  symbol: String!
  side: OrderSide!
  type: OrderType!
  quantity: Float!
  price: Float
  filledQuantity: Float!
  status: OrderStatus!
  createTime: DateTime!
  updateTime: DateTime!
}
```

#### Position (持仓)
```graphql
type Position {
  symbol: String!
  quantity: Float!
  availableQuantity: Float!
  averagePrice: Float!
  marketValue: Float!
  unrealizedPnl: Float!
  realizedPnl: Float!
  updateTime: DateTime!
}
```

#### MarketData (市场数据)
```graphql
type MarketData {
  symbol: String!
  price: Float!
  change: Float!
  changePercent: Float!
  volume: Float!
  turnover: Float!
  timestamp: DateTime!
  bid: Float
  ask: Float
  high: Float
  low: Float
}
```

## ⚠️ 错误处理

### 错误响应格式
```json
{
  "errors": [
    {
      "message": "策略未找到",
      "locations": [{"line": 2, "column": 3}],
      "path": ["strategy"],
      "extensions": {
        "code": "STRATEGY_NOT_FOUND",
        "requestId": "req-example",
        "retryable": false,
        "details": {
          "strategyId": 123
        }
      }
    }
  ],
  "data": null
}
```

### 常见错误代码

| 错误代码 | 说明 | 解决方案 |
|---------|------|---------|
| `UNAUTHENTICATED` | 未认证或会话已过期 | 提供有效的 Access Token 或刷新会话 |
| `FORBIDDEN` | 权限不足 | 检查用户权限 |
| `VALIDATION_ERROR` | 参数验证失败 | 检查输入参数格式 |
| `STRATEGY_NOT_FOUND` | 策略未找到 | 检查策略 ID |
| `STRATEGY_RUNNING` | 策略正在运行 | 先停止策略 |
| `INSUFFICIENT_BALANCE` | 余额不足 | 检查账户资金 |
| `MARKET_CLOSED` | 市场关闭 | 等待市场开放 |

## 💡 使用示例

### 完整的策略启动流程

```graphql
# 1. 查询策略模板
query {
  strategies {
    id
    name
    description
  }
}

# 2. 启动策略实例
mutation {
  startStrategy(input: {
    strategyId: 1
    mode: PAPER
    instruments: ["000001.SZ", "000002.SZ"]
    parameters: "{\"fast_period\": 10, \"slow_period\": 20}"
  }) {
    success
    message
    runId
  }
}

# 3. 订阅策略状态更新
subscription {
  strategyUpdates(runId: "run_123") {
    runId
    status
    metrics
    updateTime
  }
}
```

### 交易流程示例

```graphql
# 1. 查看账户信息
query {
  account {
    totalValue
    availableCash
    positions {
      symbol
      quantity
      marketValue
    }
  }
}

# 2. 下单
mutation {
  placeOrder(input: {
    symbol: "000001.SZ"
    side: BUY
    type: LIMIT
    quantity: 1000
    price: 10.5
  }) {
    success
    message
    order {
      id
      status
    }
  }
}

# 3. 监控订单状态
subscription {
  orderUpdates {
    id
    symbol
    status
    filledQuantity
  }
}
```

## 🔧 最佳实践

### 1. 查询优化
- 只查询需要的字段
- 使用分页参数避免大量数据
- 利用 DataLoader 批量查询

### 2. 订阅管理
- 及时取消不需要的订阅
- 使用连接心跳检测
- 处理连接断开重连

### 3. 错误处理
- 检查响应中的 errors 字段
- 根据错误代码进行相应处理
- 实现重试机制

### 4. 性能建议
- 使用 HTTP/2 提升性能
- 启用 GZIP 压缩
- 合理设置缓存策略

---

**相关文档**：
- [系统架构](./ARCHITECTURE.md)
- [功能模块](./MODULES.md)
- [使用示例](./EXAMPLES.md)
- [故障排查](./TROUBLESHOOTING.md)
