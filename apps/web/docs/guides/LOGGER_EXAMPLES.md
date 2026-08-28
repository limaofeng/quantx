# Logger 实际使用示例

本文档展示了 QuantX 项目中 Logger 的实际使用案例。

## 目录

1. [Mock Manager 示例](#mock-manager-示例)
2. [应用启动示例](#应用启动示例)
3. [策略管理示例](#策略管理示例)
4. [环境变量验证示例](#环境变量验证示例)
5. [性能监控示例](#性能监控示例)
6. [错误处理示例](#错误处理示例)

---

## Mock Manager 示例

### 文件: `src/mocks/mockManager.ts`

#### 1. 配置加载错误

```typescript
private loadConfig(): MockConfig {
  try {
    const saved = localStorage.getItem(this.storageKey);
    if (saved) {
      return JSON.parse(saved);
    }
  } catch (error) {
    // ✅ 结构化记录错误
    logger.warn('Failed to load mock config from localStorage', { error });
  }
  return { ...DEFAULT_MOCK_CONFIG };
}
```

**日志输出**:

```
[2025-10-05T12:34:56.789Z] [WARN] Failed to load mock config from localStorage {
  error: SyntaxError: Unexpected token...
}
```

#### 2. Mock 状态变更

```typescript
setEnabled(enabled: boolean): void {
  this.config.enabled = enabled;
  this.applyConfig();
  this.saveConfig();

  // ✅ 记录状态变更,包含上下文
  logger.info('🎭 Mock Manager: Mock status changed', { enabled });
}
```

**日志输出**:

```
[2025-10-05T12:34:57.123Z] [INFO] 🎭 Mock Manager: Mock status changed { enabled: true }
```

#### 3. 操作切换

```typescript
toggleOperation(operationName: string): boolean {
  if (!isValidOperation(operationName)) {
    // ✅ 记录警告,包含操作名称
    logger.warn('🎭 Mock Manager: Unknown operation', { operationName });
    return false;
  }

  // ... 切换逻辑

  logger.info('🎭 Mock Manager: Toggle operation mock', {
    operationName,
    enabled: !isCurrentlyMocked,
  });

  return !isCurrentlyMocked;
}
```

**日志输出**:

```
[2025-10-05T12:35:00.456Z] [INFO] 🎭 Mock Manager: Toggle operation mock {
  operationName: 'GetStocks',
  enabled: true
}
```

---

## 应用启动示例

### 文件: `src/main.tsx`

#### 1. 环境变量检查

```typescript
// ✅ 记录Vite环境变量
logger.info('Vite 环境变量', {
  NODE_ENV: import.meta.env.MODE,
  VITE_APP_ENV: import.meta.env.VITE_APP_ENV,
  DEV: import.meta.env.DEV,
  PROD: import.meta.env.PROD,
});
```

**日志输出**:

```
[2025-10-05T12:30:00.000Z] [INFO] Vite 环境变量 {
  NODE_ENV: 'development',
  VITE_APP_ENV: 'dev',
  DEV: true,
  PROD: false
}
```

#### 2. MSW 启动流程

```typescript
async function startApp() {
  if (import.meta.env.DEV) {
    logger.info('🔧 加载调试工具...');

    const mockEnabled = import.meta.env.VITE_MOCK_ENABLED === 'true';

    if (mockEnabled) {
      try {
        const { startWorker } = await import('@/mocks/browser');
        const { mockManager } = await import('@/mocks/mockManager');

        await startWorker();

        // ✅ 记录MSW启动成功
        logger.info('🎭 MSW: Mock Service Worker 已启动');

        // ✅ 记录详细配置
        logger.info('🎭 MSW: Mock 配置', {
          环境变量控制: import.meta.env.VITE_MOCK_ENABLED,
          延迟设置: import.meta.env.VITE_MOCK_DELAY + 'ms',
          详细日志: import.meta.env.VITE_MOCK_VERBOSE,
          默认查询: import.meta.env.VITE_MOCK_DEFAULT_QUERIES,
        });

        // ✅ 记录当前状态
        logger.info('🎭 MSW: Mock 状态', mockManager.getStatus());
        logger.info('🎭 MSW: Mock Manager 已暴露到 window.mockManager');

      } catch (error) {
        // ✅ 记录启动失败
        logger.warn('⚠️ MSW: 启动 Mock Service Worker 失败', { error });
      }
    } else {
      logger.info('🎭 MSW: Mock 功能已禁用 (VITE_MOCK_ENABLED=false)');
    }

    // ✅ 记录工具加载
    import('@/core/debug/log-viewer').then(() => {
      logger.info('✅ 日志查看器已加载');
    });

    import('@/core/debug/performance-viewer').then(() => {
      logger.info('✅ 性能调试工具已加载');
    });
  }

  createRoot(document.getElementById('root')!).render(<App />);
}
```

**日志输出**:

```
[2025-10-05T12:30:01.000Z] [INFO] 🔧 加载调试工具...
[2025-10-05T12:30:01.500Z] [INFO] 🎭 MSW: Mock Service Worker 已启动
[2025-10-05T12:30:01.501Z] [INFO] 🎭 MSW: Mock 配置 {
  环境变量控制: 'true',
  延迟设置: '500ms',
  详细日志: 'true',
  默认查询: 'Stocks,Portfolio'
}
[2025-10-05T12:30:01.502Z] [INFO] 🎭 MSW: Mock 状态 {
  enabled: true,
  totalOperations: 50,
  mockedOperations: 12,
  mockedQueries: 10,
  mockedMutations: 2
}
[2025-10-05T12:30:02.000Z] [INFO] ✅ 日志查看器已加载
[2025-10-05T12:30:02.100Z] [INFO] ✅ 性能调试工具已加载
```

---

## 策略管理示例

### 文件: `src/features/strategies/pages/StrategiesPage.tsx`

#### 策略创建错误处理

```typescript
const handleCreateStrategy = async (formData: StrategyFormData) => {
  try {
    await addStrategy({
      variables: {
        name: formData.name,
        description: formData.description,
        category: formData.category,
        riskLevel: formData.riskLevel,
        stockCodes: formData.stockCodeArray,
        ...formData.config,
      },
    });
    dialog.closeDialog();
  } catch (error) {
    // ✅ 记录创建失败,包含错误详情
    logger.error('Strategy creation failed', { error, formData });
  }
};
```

**日志输出**:

```
[2025-10-05T13:00:00.000Z] [ERROR] Strategy creation failed {
  error: Error: Network request failed,
  formData: {
    name: 'My Strategy',
    description: 'Test strategy',
    category: 'growth',
    riskLevel: 'medium',
    stockCodeArray: ['600519', '000001']
  }
}
```

---

## 环境变量验证示例

### 文件: `src/shared/utils/env.ts`

#### 1. 环境变量验证失败

```typescript
function validateEnv(): Env {
  try {
    return envSchema.parse(rawEnv);
  } catch (error) {
    // ✅ 记录验证失败
    logger.error('❌ 环境变量验证失败:');

    if (error instanceof z.ZodError) {
      error.errors.forEach(err => {
        // ✅ 记录每个具体错误
        logger.error(`  - ${err.path.join('.')}: ${err.message}`);
      });
    }

    if (import.meta.env.DEV) {
      throw new Error('环境变量配置错误，请检查 .env 文件');
    } else {
      logger.warn('⚠️ 使用默认环境变量配置');
      return envSchema.parse({});
    }
  }
}
```

**日志输出**:

```
[2025-10-05T12:00:00.000Z] [ERROR] ❌ 环境变量验证失败:
[2025-10-05T12:00:00.001Z] [ERROR]   - VITE_GRAPHQL_HTTP_URL: Required
[2025-10-05T12:00:00.002Z] [ERROR]   - VITE_APP_ENV: Invalid enum value
```

#### 2. 缺少必需环境变量

```typescript
export function checkRequiredEnvVars(requiredVars: string[]): boolean {
  const missing = requiredVars.filter(varName => {
    const value = import.meta.env[varName];
    return !value || value === '';
  });

  if (missing.length > 0) {
    // ✅ 记录缺少的变量
    logger.error('❌ 缺少必需的环境变量', { missing });
    return false;
  }

  return true;
}
```

**日志输出**:

```
[2025-10-05T12:00:01.000Z] [ERROR] ❌ 缺少必需的环境变量 {
  missing: ['VITE_API_URL', 'VITE_GRAPHQL_WS_URL']
}
```

#### 3. 打印环境信息

```typescript
export function printEnvInfo(): void {
  if (!isDevelopment) return;

  // ✅ 使用结构化日志
  logger.info('🌍 环境配置信息', {
    应用环境: env.VITE_APP_ENV,
    Node环境: env.NODE_ENV,
    GraphQL地址: env.VITE_GRAPHQL_HTTP_URL,
    WebSocket地址: env.VITE_GRAPHQL_WS_URL,
    功能开关: features,
    性能预算: performanceBudget,
  });
}
```

**日志输出**:

```
[2025-10-05T12:00:02.000Z] [INFO] 🌍 环境配置信息 {
  应用环境: 'dev',
  Node环境: 'development',
  GraphQL地址: 'http://localhost:8000/graphql',
  WebSocket地址: 'ws://localhost:8000/graphql',
  功能开关: { debug: true, analytics: false },
  性能预算: { maxBundleSize: 1000 }
}
```

---

## 性能监控示例

### 文件: `src/core/performance/route-monitor.ts`

#### 路由变化监控

```typescript
private handleRouteChange(): void {
  const newPath = window.location.pathname;
  const oldPath = this.currentPath;

  if (newPath === oldPath) return;

  const navigationTime = this.navigationStartTime > 0
    ? performance.now() - this.navigationStartTime
    : 0;

  // ✅ 记录路由变化,包含性能数据
  logger.info('路由变化', {
    type: 'performance',
    action: 'route_change',
    from: oldPath,
    to: newPath,
    navigationTime: navigationTime > 0 ? navigationTime : undefined,
  });

  if (import.meta.env.DEV) {
    // ✅ 开发环境额外的格式化日志
    logger.info(
      `🔄 路由变化: ${oldPath} → ${newPath}${navigationTime > 0 ? ` (${navigationTime.toFixed(2)}ms)` : ''}`
    );
  }
}
```

**日志输出**:

```
[2025-10-05T14:00:00.000Z] [INFO] 路由变化 {
  type: 'performance',
  action: 'route_change',
  from: '/dashboard',
  to: '/portfolio',
  navigationTime: 45.23
}
[2025-10-05T14:00:00.001Z] [INFO] 🔄 路由变化: /dashboard → /portfolio (45.23ms)
```

### 文件: `src/core/performance/web-vitals.ts`

#### Web Vitals 性能指标

```typescript
handleMetric(metric: Metric): void {
  const performanceMetric: PerformanceMetric = {
    name: metric.name,
    value: metric.value,
    rating: getRating(metric.name, metric.value),
    delta: metric.delta,
    id: metric.id,
    entries: metric.entries as PerformanceEntry[],
    timestamp: Date.now(),
    url: window.location.href,
  };

  // ✅ 记录性能指标
  const logLevel = performanceMetric.rating === 'poor' ? 'warn' : 'info';
  logger[logLevel](`Web Vital: ${performanceMetric.name}`, {
    type: 'performance',
    metric: performanceMetric.name,
    value: performanceMetric.value,
    rating: performanceMetric.rating,
    delta: performanceMetric.delta,
  });

  logger.info('性能指标已记录', {
    metric: performanceMetric.name,
    value: performanceMetric.value
  });
}
```

**日志输出**:

```
[2025-10-05T14:01:00.000Z] [INFO] Web Vital: LCP {
  type: 'performance',
  metric: 'LCP',
  value: 1250.5,
  rating: 'good',
  delta: 0
}
[2025-10-05T14:01:00.001Z] [WARN] Web Vital: CLS {
  type: 'performance',
  metric: 'CLS',
  value: 0.35,
  rating: 'poor',
  delta: 0.05
}
```

---

## 错误处理示例

### 文件: `src/shared/utils/error-handler.ts`

#### 全局错误处理

```typescript
function logError(error: StandardError): void {
  if (import.meta.env.DEV) {
    // ✅ 使用结构化日志替代 console.group
    logger.error(`🚨 Error [${error.severity}] - ${error.category}`, {
      message: error.message,
      originalError: error.originalError,
      context: error.context,
      stack: error.originalError.stack,
    });
  }
}
```

**日志输出**:

```
[2025-10-05T15:00:00.000Z] [ERROR] 🚨 Error [high] - runtime {
  message: '程序运行出现异常，我们正在努力修复这个问题',
  originalError: TypeError: Cannot read property 'map' of undefined,
  context: {
    component: 'StockList',
    action: 'render',
    url: '/stocks',
    timestamp: '2025-10-05T15:00:00.000Z'
  },
  stack: 'TypeError: Cannot read property...'
}
```

---

## 组合使用示例

### 复杂业务流程

```typescript
async function processUserOrder(orderId: string) {
  // 1. 开始处理
  logger.info('开始处理订单', { orderId, timestamp: Date.now() });

  try {
    // 2. 验证订单
    const order = await fetchOrder(orderId);
    logger.debug('订单详情', { order });

    // 3. 检查库存
    const hasStock = await checkStock(order.items);
    if (!hasStock) {
      logger.warn('库存不足', { orderId, items: order.items });
      return { success: false, reason: 'out_of_stock' };
    }

    // 4. 处理支付
    const payment = await processPayment(order.total);
    logger.info('支付成功', {
      orderId,
      paymentId: payment.id,
      amount: order.total,
    });

    // 5. 创建发货单
    const shipment = await createShipment(order);
    logger.info('发货单已创建', { orderId, shipmentId: shipment.id });

    // 6. 完成
    logger.info('订单处理完成', {
      orderId,
      paymentId: payment.id,
      shipmentId: shipment.id,
      duration: Date.now() - startTime,
    });

    return { success: true };
  } catch (error) {
    // 7. 错误处理
    logger.error('订单处理失败', {
      orderId,
      error,
      stack: error.stack,
      timestamp: Date.now(),
    });

    throw error;
  }
}
```

**完整日志流程**:

```
[2025-10-05T16:00:00.000Z] [INFO] 开始处理订单 { orderId: 'ORD-123', timestamp: 1696514400000 }
[2025-10-05T16:00:00.100Z] [DEBUG] 订单详情 { order: {...} }
[2025-10-05T16:00:00.200Z] [INFO] 支付成功 { orderId: 'ORD-123', paymentId: 'PAY-456', amount: 299.99 }
[2025-10-05T16:00:00.300Z] [INFO] 发货单已创建 { orderId: 'ORD-123', shipmentId: 'SHIP-789' }
[2025-10-05T16:00:00.400Z] [INFO] 订单处理完成 {
  orderId: 'ORD-123',
  paymentId: 'PAY-456',
  shipmentId: 'SHIP-789',
  duration: 400
}
```

---

## 总结

Logger 系统提供了:

1. **统一的日志格式**: 所有日志都使用相同的结构
2. **丰富的上下文**: 每条日志包含时间戳、会话ID、URL等元数据
3. **易于调试**: 开发环境直接输出到控制台,格式清晰
4. **生产就绪**: 支持远程日志上报和日志级别过滤
5. **可追溯性**: 保存历史日志,支持导出为JSON/CSV

使用 Logger 替代 console,让您的应用更专业、更易维护!

---

**相关文档**:

- [Logger 使用指南](./LOGGER_GUIDE.md)
- Logger 源码: `src/core/errors/logger.ts`
- Log Viewer: `src/core/debug/log-viewer.ts`
