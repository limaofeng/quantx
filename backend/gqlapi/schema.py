import strawberry

from .schemas import (
  DividFactorQuery,
  FinancialQuery,
  InstrumentQuery,
  LiquidationMutation,
  LiquidationQuery,
  MarketDataQuery,
  PortfolioQuery,
  RealtimeSubscription,
  StrategyMutation,
  StrategyQuery,
  TradingMutation,
  TradingQuery,
  WorkflowMutation,
  WorkflowQuery,
  SectorQuery,
  HolidayQuery,
  HolidayMutation,
  StockScreeningQuery,
  WatchlistMutation,
  WatchlistQuery,
)


@strawberry.type(
  description="""QuantX 量化交易系统 GraphQL API

## 主要功能模块

### 金融工具查询
- 获取金融工具基本信息和实时行情
- 支持单个金融工具和批量查询

### 持仓管理
- 查看当前持仓情况
- 获取持仓盈亏信息

### 清仓管理
- 一键清仓所有持仓
- 个股清仓操作
- 已清仓股票资金赎回

### 订单管理
- 查询历史订单记录
- 查看订单执行状态

### 策略管理
- 获取策略模板和实例信息
- 监控策略运行状态

### 历史数据
- 获取金融工具价格历史数据
- 支持不同时间周期

### 账户信息
- 查看账户资产状况
- 获取账户盈亏统计
"""
)
class Query(
  DividFactorQuery,
  FinancialQuery,
  InstrumentQuery,
  MarketDataQuery,
  TradingQuery,
  PortfolioQuery,
  LiquidationQuery,
  StrategyQuery,
  WorkflowQuery,
  SectorQuery,
  HolidayQuery,
  StockScreeningQuery,
  WatchlistQuery,
):
  pass


@strawberry.type(
  description="""QuantX 系统数据变更接口

## 主要功能

### 订单操作
- 创建买入/卖出订单
- 取消未成交订单

### 持仓管理
- 执行清仓操作

### 清仓管理
- 一键清仓所有持仓
- 个股清仓操作
- 已清仓股票资金赎回

### 策略管理
- 创建、更新、删除策略模板
- 管理策略实例的生命周期
"""
)
class Mutation(
  TradingMutation,
  LiquidationMutation,
  StrategyMutation,
  WorkflowMutation,
  HolidayMutation,
  WatchlistMutation,
):
  pass


@strawberry.type(
  description="""QuantX 系统实时数据订阅接口

## 主要功能

### 实时行情订阅
- 金融工具实时价格更新
- K线数据实时推送
- 市场深度数据订阅

### 交易数据订阅
- 订单状态变更通知
- 成交记录实时推送

### 多金融工具监控
- 同时订阅多个金融工具的价格
"""
)
class Subscription(RealtimeSubscription):
  pass


schema = strawberry.Schema(query=Query, mutation=Mutation, subscription=Subscription)
