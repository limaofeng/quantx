# QuantX MCP Server - 能力列表

本文档详细列出了 QuantX MCP Server 提供的所有工具和能力。

## 📊 目录

- [市场数据工具](#市场数据工具) - 11个工具
- [策略工具](#策略工具) - 7个工具
- [账户工具](#账户工具) - 5个工具
- [订单工具](#订单工具) - 4个工具
- [分析工具](#分析工具) - 5个工具

---

## 📈 市场数据工具 (11个)

### 1. market_data_list_instruments
**列出所有市场标的**

- **描述**: 获取所有可交易的金融标的（股票、指数、ETF、基金、期货等）
- **参数**:
  - `instrument_type` (可选): 标的类型过滤 (stock, index, etf, fund, futures, bond)
  - `market` (可选): 市场过滤 (SH, SZ等)
  - `limit` (可选): 返回数量，默认100
  - `offset` (可选): 跳过数量，用于分页
- **返回**: 分页的标的列表
- **用途**: 浏览市场标的，构建股票池

### 2. market_data_get_instrument_info
**获取标的信息**

- **描述**: 获取指定标的的详细信息（代码、名称、类型、上市日期、股本等）
- **参数**:
  - `symbol` (必需): 标的代码 (例如 000001.SZ)
- **返回**: 标的详细信息
- **用途**: 查看标的详细信息

### 3. market_data_search_instruments
**搜索市场标的**

- **描述**: 根据关键词搜索标的（支持中文、拼音、代码）
- **参数**:
  - `keyword` (必需): 搜索关键词
  - `instrument_type` (可选): 类型过滤
  - `limit` (可选): 返回数量，默认50
- **返回**: 匹配的标的列表
- **用途**: 快速查找股票、指数、ETF等

### 4. market_data_get_index_constituents
**获取指数成分股**

- **描述**: 获取指定指数的所有成分股
- **参数**:
  - `index_symbol` (必需): 指数代码 (例如 000300.SH)
- **返回**: 成分股列表
- **用途**: 查看指数包含的股票，构建指数增强策略

### 5. market_data_get_sector_stocks
**获取行业股票**

- **描述**: 获取指定行业或板块的股票列表
- **参数**:
  - `sector_name` (必需): 行业名称 (银行、科技、医药等)
  - `market` (可选): 市场过滤
  - `limit` (可选): 返回数量，默认100
- **返回**: 行业股票列表
- **用途**: 按行业选股，板块轮动

### 6. market_data_get_realtime
**获取实时行情数据**

- **描述**: 获取指定股票的实时市场数据
- **参数**:
  - `symbol` (必需): 股票代码，例如 "000001.SZ" (平安银行)
  - `fields` (可选): 要获取的字段列表，默认获取所有字段
- **返回**: 实时价格、成交量、买卖盘等信息
- **用途**: 实时行情监控、价格提醒

### 2. market_data_get_historical
**获取历史数据**

- **描述**: 获取指定时间范围的历史K线数据
- **参数**:
  - `symbol` (必需): 股票代码
  - `start_date` (必需): 开始日期 (YYYY-MM-DD)
  - `end_date` (必需): 结束日期 (YYYY-MM-DD)
  - `interval` (可选): 数据周期 (1d, 1h, 30m, 15m, 5m, 1m)，默认 "1d"
- **返回**: 历史K线数据数组
- **用途**: 历史数据分析、回测数据准备

### 3. market_data_get_kline
**获取K线数据**

- **描述**: 获取最新的K线（蜡烛图）数据
- **参数**:
  - `symbol` (必需): 股票代码
  - `period` (必需): K线周期 (daily, weekly, monthly, 1min, 5min等)
  - `count` (可选): 获取根数，默认100根
- **返回**: K线数据，包含开高低收量
- **用途**: 技术分析、图表绘制

### 4. market_data_subscribe
**订阅实时行情**

- **描述**: 订阅指定股票的实时行情推送
- **参数**:
  - `symbols` (必需): 要订阅的股票代码列表
- **返回**: 订阅状态和已订阅股票列表
- **用途**: 实时监控多只股票

### 5. market_data_unsubscribe
**取消订阅**

- **描述**: 取消订阅指定股票的实时行情
- **参数**:
  - `symbols` (必需): 要取消订阅的股票代码列表
- **返回**: 取消订阅状态
- **用途**: 停止不需要的实时数据推送

### 6. market_data_search_symbols
**搜索股票代码**

- **描述**: 根据股票名称或拼音搜索股票代码
- **参数**:
  - `keyword` (必需): 搜索关键词（支持股票名称、拼音）
  - `market` (可选): 市场过滤 (SZ/SH/全部)
- **返回**: 匹配的股票列表
- **用途**: 股票代码查询、股票搜索

---

## 🤖 策略工具

### 1. strategy_list
**列出所有策略**

- **描述**: 获取系统中所有可用的交易策略列表
- **参数**:
  - `status` (可选): 过滤状态 (active, inactive, all)，默认 "all"
- **返回**: 策略列表，包含名称、描述、版本等信息
- **用途**: 浏览可用策略

### 2. strategy_get_info
**获取策略详情**

- **描述**: 获取指定策略的详细信息和参数说明
- **参数**:
  - `strategy_name` (必需): 策略名称或ID
- **返回**: 策略详细信息、参数定义、使用说明
- **用途**: 了解策略如何使用

### 3. strategy_execute
**执行策略**

- **描述**: 立即执行一次策略，生成交易意图
- **参数**:
  - `strategy_name` (必需): 要执行的策略
  - `parameters` (必需): 策略参数
    - `symbol`: 股票代码
    - `interval`: 时间周期
    - `capital`: 资金量
    - 其他策略特定参数
  - `mode` (可选): 执行模式 (backtest, paper, live)，默认 "paper"
- **返回**: 执行结果、生成的交易意图
- **用途**: 单次策略执行、意图生成

### 4. strategy_start
**启动自动策略**

- **描述**: 启动一个持续运行的自动交易策略
- **参数**:
  - `strategy_name` (必需): 要启动的策略
  - `parameters` (可选): 策略配置参数
- **返回**: 策略实例ID和状态
- **用途**: 启动自动化交易

### 5. strategy_stop
**停止策略**

- **描述**: 停止一个正在运行的策略
- **参数**:
  - `strategy_id` (必需): 策略实例ID
- **返回**: 停止状态确认
- **用途**: 停止自动交易策略

### 6. strategy_get_performance
**获取策略绩效**

- **描述**: 获取策略的绩效指标和分析
- **参数**:
  - `strategy_id` (必需): 策略实例ID
  - `metrics` (可选): 指定要获取的指标，默认获取全部
- **返回**: 绩效指标（收益率、夏普比率、最大回撤等）
- **用途**: 评估策略表现

### 7. strategy_backtest
**策略回测**

- **描述**: 在历史数据上回测策略
- **参数**:
  - `strategy_name` (必需): 要回测的策略
  - `parameters` (可选): 策略参数
  - `start_date` (必需): 回测开始日期 (YYYY-MM-DD)
  - `end_date` (必需): 回测结束日期 (YYYY-MM-DD)
  - `initial_capital` (可选): 初始资金，默认 1,000,000
- **返回**: 回测结果、交易记录、资金曲线
- **用途**: 历史表现验证、参数优化

---

## 💰 账户工具

### 1. account_get_info
**获取账户信息**

- **描述**: 获取账户的资金和资产信息
- **参数**:
  - `account_id` (可选): 账户ID，默认当前账户
- **返回**: 总资产、可用资金、持仓市值、盈亏等
- **用途**: 查看账户状态

### 2. account_get_positions
**获取当前持仓**

- **描述**: 获取账户当前的所有持仓
- **参数**:
  - `account_id` (可选): 账户ID
- **返回**: 持仓列表，包含股票、数量、成本价、市值等
- **用途**: 查看持仓详情

### 3. account_get_orders
**获取订单历史**

- **描述**: 获取账户的历史订单记录
- **参数**:
  - `account_id` (可选): 账户ID
  - `status` (可选): 订单状态过滤 (filled, pending, cancelled, all)
  - `limit` (可选): 返回数量，默认100
- **返回**: 订单列表
- **用途**: 查看交易历史

### 4. account_get_trades
**获取成交记录**

- **描述**: 获取账户的历史成交记录
- **参数**:
  - `account_id` (可选): 账户ID
  - `start_date` (可选): 开始日期
  - `end_date` (可选): 结束日期
- **返回**: 成交记录列表
- **用途**: 查看成交详情

### 5. account_get_pnl
**获取盈亏汇总**

- **描述**: 获取账户的盈亏统计
- **参数**:
  - `account_id` (可选): 账户ID
  - `period` (可选): 时间周期 (today, week, month, year, all)
- **返回**: 已实现盈亏、浮动盈亏、收益率等
- **用途**: 评估交易表现

---

## 📋 订单工具

### 1. order_create
**创建订单**

- **描述**: 创建并提交一个新的交易订单
- **参数**:
  - `symbol` (必需): 股票代码
  - `side` (必需): 买卖方向 (buy/sell)
  - `quantity` (必需): 数量
  - `type` (可选): 订单类型 (market/limit)，默认 limit
  - `price` (可选): 限价（限价单必需）
  - `account_id` (可选): 账户ID
- **返回**: 订单ID和状态
- **用途**: 下单交易

### 2. order_cancel
**撤销订单**

- **描述**: 撤销一个未成交的订单
- **参数**:
  - `order_id` (必需): 要撤销的订单ID
- **返回**: 撤销状态
- **用途**: 撤单操作

### 3. order_modify
**修改订单**

- **描述**: 修改订单的价格或数量
- **参数**:
  - `order_id` (必需): 要修改的订单ID
  - `quantity` (可选): 新的数量
  - `price` (可选): 新的价格
- **返回**: 修改状态
- **用途**: 调整订单参数

### 4. order_get_status
**查询订单状态**

- **描述**: 查询订单的当前状态
- **参数**:
  - `order_id` (必需): 订单ID
- **返回**: 订单状态、成交情况
- **用途**: 订单状态查询

---

## 🔍 分析工具

### 1. analysis_calculate_indicators
**计算技术指标**

- **描述**: 计算指定股票的技术指标
- **参数**:
  - `symbol` (必需): 股票代码
  - `indicators` (必需): 要计算的指标列表 (MA, EMA, MACD, RSI, Bollinger等)
  - `period` (可选): 数据周期，默认 "1d"
  - `limit` (可选): 数据点数，默认100
- **返回**: 技术指标数值
- **用途**: 技术分析

### 2. analysis_scan_market
**市场扫描**

- **描述**: 扫描市场寻找符合条件的交易机会
- **参数**:
  - `market` (可选): 扫描市场 (SZ, SH, all)，默认 "all"
  - `criteria` (可选): 扫描条件（放量突破、价格异动等）
  - `limit` (可选): 最大返回数，默认50
- **返回**: 符合条件的股票列表
- **用途**: 选股、发现交易机会

### 3. analysis_backtest
**回测分析**

- **描述**: 对策略进行回测分析
- **参数**:
  - `strategy` (必需): 策略名称或参数
  - `symbol` (必需): 回测股票
  - `start_date` (必需): 开始日期
  - `end_date` (必需): 结束日期
- **返回**: 回测结果报告
- **用途**: 验证策略有效性

### 4. analysis_get_research_report
**生成研究报告**

- **描述**: 为指定股票生成研究报告
- **参数**:
  - `symbol` (必需): 股票代码
  - `report_type` (可选): 报告类型 (technical, fundamental, comprehensive)
- **返回**: 研究报告内容
- **用途**: 股票分析、投资参考

### 5. analysis_compare_symbols
**对比股票**

- **描述**: 对比多只股票的指标和表现
- **参数**:
  - `symbols` (必需): 要对比的股票列表
  - `metrics` (可选): 要对比的指标 (市盈率、市值、技术指标等)
- **返回**: 对比结果表格
- **用途**: 选股对比、相对分析

---

## 📚 资源列表 (Resources)

MCP Server 还提供以下资源访问：

1. **market_data://realtime** - 实时行情状态
2. **market_data://historical** - 历史数据概况
3. **strategies://active** - 活跃策略列表
4. **strategies://available** - 可用策略列表
5. **orders://pending** - 待成交订单
6. **orders://history** - 历史订单
7. **account://info** - 账户信息
8. **account://positions** - 持仓信息

---

## 🔒 安全特性

- **认证支持**: Token-based 认证
- **权限控制**: RBAC 权限管理
- **速率限制**: 防止滥用
- **审计日志**: 记录所有操作

---

## 📊 总计

- **工具总数**: 32个 (新增5个市场标的查询工具)
- **资源总数**: 8个
- **类别**: 5大类别

**覆盖功能**:
- ✅ 实时行情获取
- ✅ 历史数据查询
- ✅ 策略执行和管理
- ✅ 自动化交易
- ✅ 账户管理
- ✅ 订单操作
- ✅ 技术分析
- ✅ 市场扫描
- ✅ 回测分析

---

## 🚀 使用示例

### Python 客户端
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    # 连接到 QuantX MCP Server
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "mcp.server"]
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化
            await session.initialize()
            
            # 获取实时行情
            result = await session.call_tool(
                "market_data_get_realtime",
                arguments={"symbol": "000001.SZ"}
            )
            
            # 执行策略
            result = await session.call_tool(
                "strategy_execute",
                arguments={
                    "strategy_name": "DualThrust",
                    "parameters": {
                        "symbol": "000001.SZ",
                        "interval": "1d"
                    }
                }
            )
```

### Claude Desktop 配置
```json
{
  "mcpServers": {
    "quantx": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "cwd": "/path/to/quantx/backend"
    }
  }
}
```

---

## 📖 文档

- 完整 API 文档: [API.md](./API.md)
- 策略开发: [STRATEGY.md](./STRATEGY.md)
- 系统架构: [ARCHITECTURE.md](./ARCHITECTURE.md)
