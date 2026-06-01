# GraphQL 部分 Mock 系统使用指南

## 概述

这个系统使用 MSW (Mock Service Worker) 实现了 GraphQL 查询的部分 Mock 功能，允许你选择性地 Mock 某些查询，而让其他查询继续转发到真实的后端服务器。

## 功能特性

✅ **选择性 Mock**: 只 Mock 指定的查询，其他查询转发到真实服务器
✅ **环境变量控制**: 通过环境变量配置 Mock 行为
✅ **运行时管理**: 在浏览器中动态控制哪些查询被 Mock
✅ **延迟模拟**: 可配置网络延迟以模拟真实环境
✅ **详细日志**: 可控制的 Mock 操作日志
✅ **开发友好**: 仅在开发环境启用，不影响生产环境

## 快速开始

### 1. 环境变量配置

在 `.env.development` 中配置 Mock 设置：

```bash
# 启用 Mock 功能
VITE_MOCK_ENABLED=true

# 默认要 Mock 的查询（逗号分隔）
VITE_MOCK_DEFAULT_QUERIES=portfolioSummary,GetCurrentAccount

# Mock 延迟（毫秒）
VITE_MOCK_DELAY=200

# 显示详细日志
VITE_MOCK_VERBOSE=true
```

### 2. 启动应用

```bash
npm run dev
```

应用启动后，你会在控制台看到：

```
🎭 MSW: Mock Service Worker 已启动
🎭 MSW: Mock 配置: { 环境变量控制: 'true', 延迟设置: '200ms', ... }
🎭 MSW: Mock 状态: { enabled: true, mockedQueries: 2, ... }
```

### 3. 运行时控制

在浏览器控制台中，你可以使用全局 `mockManager` 对象：

```javascript
// 查看当前状态
mockManager.getStatus();

// 启用/禁用 Mock
mockManager.setEnabled(true);

// 切换单个查询的 Mock 状态
mockManager.toggleQuery('holdings'); // 返回新状态 (true/false)

// 设置要 Mock 的查询列表
mockManager.setMockedQueries(['portfolioSummary', 'dashboardSummary']);

// 获取所有可用查询
mockManager.getAvailableQueries();

// 重置为默认配置
mockManager.resetToDefaults();
```

## Mock 数据文件

Mock 数据存储在 `src/mocks/data/` 目录中：

```
src/mocks/data/
├── portfolio.ts     # 投资组合相关 Mock 数据
└── dashboard.ts     # 仪表盘相关 Mock 数据
```

### 添加新的 Mock 数据

1. 在相应的数据文件中添加导出：

```typescript
// src/mocks/data/portfolio.ts
export const mockNewQuery = {
  // 你的 Mock 数据
};
```

2. 在 `src/mocks/handlers.ts` 中添加处理器：

```typescript
import { mockNewQuery } from './data/portfolio';

// 在 handlers 数组中添加
graphql.query('newQuery', async () => {
  if (!MOCKED_QUERIES.has('newQuery')) {
    return; // 不 Mock，转发到服务器
  }

  logMock('newQuery');
  await mockDelay();

  return HttpResponse.json({
    data: {
      newQuery: mockNewQuery
    }
  });
}),
```

3. 更新 `mockManager.ts` 中的可用查询列表：

```typescript
getAvailableQueries(): string[] {
  return [
    // 现有查询...
    'newQuery',  // 添加新查询
  ];
}
```

## 当前支持的查询

### Portfolio 模块

- ✅ `portfolioSummary` - 投资组合汇总信息
- ✅ `holdings` - 持仓列表
- ✅ `liquidatedStocks` - 已平仓股票

### Dashboard 模块

- ✅ `GetCurrentAccount` - 当前账户信息
- ✅ `dashboardSummary` - 仪表盘汇总

### Mutations

- ✅ `redeemCash` - 提现申请

## 测试 Mock 功能

1. **使用测试页面**: 打开 `mock-test.html` 在浏览器中测试各个查询

2. **查看控制台日志**:

   ```
   🎭 MSW: Mocking portfolioSummary { variables: {...} }
   ```

3. **检查网络面板**: Mock 的请求会显示状态为 200，但不会实际发送到服务器

## 常见用法场景

### 1. 前端独立开发

```javascript
// 启用所有查询的 Mock
mockManager.setMockedQueries([
  'portfolioSummary',
  'holdings',
  'GetCurrentAccount',
  'dashboardSummary',
  'liquidatedStocks',
]);
```

### 2. 特定功能测试

```javascript
// 只 Mock 投资组合相关查询
mockManager.setMockedQueries(['portfolioSummary', 'holdings']);
```

### 3. 错误场景模拟

你可以修改 handlers 来返回错误响应：

```typescript
graphql.query('portfolioSummary', async () => {
  // 模拟错误
  return HttpResponse.json({
    errors: [
      {
        message: '模拟的服务器错误',
        extensions: { code: 'INTERNAL_ERROR' },
      },
    ],
  });
});
```

## 最佳实践

1. **渐进式 Mock**: 先 Mock 一部分查询进行开发，逐步减少 Mock 查询进行集成测试

2. **数据真实性**: Mock 数据应该尽可能接近真实数据的结构和内容

3. **版本同步**: 定期更新 Mock 数据以保持与 GraphQL Schema 的同步

4. **团队协作**: 使用 localStorage 配置分享 Mock 设置

5. **生产安全**: 确保 MSW 只在开发环境加载

## 故障排除

### Mock 不工作？

1. 检查控制台是否有 MSW 启动日志
2. 确认 `VITE_MOCK_ENABLED=true`
3. 检查查询名称是否在 Mock 列表中
4. 确认 Service Worker 已注册

### 查询仍然发送到服务器？

1. 检查查询名称是否正确匹配
2. 确认 handler 中的条件判断
3. 查看控制台 Mock 日志

### 性能问题？

1. 减少 `VITE_MOCK_DELAY` 值
2. 设置 `VITE_MOCK_VERBOSE=false` 减少日志
3. 只 Mock 必要的查询

## 文件结构

```
src/mocks/
├── README.md           # 本文档
├── browser.ts          # 浏览器端 MSW 配置
├── handlers.ts         # GraphQL Mock 处理器
├── mockManager.ts      # Mock 管理工具
└── data/
    ├── portfolio.ts    # 投资组合 Mock 数据
    └── dashboard.ts    # 仪表盘 Mock 数据
```

## 更多信息

- [MSW 官方文档](https://mswjs.io/)
- [GraphQL Mock 最佳实践](https://mswjs.io/docs/recipes/graphql-query)
- [项目 GraphQL Schema](../schema.graphql)
