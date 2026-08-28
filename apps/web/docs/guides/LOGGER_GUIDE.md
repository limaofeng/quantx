# Logger 使用指南

## 简介

QuantX 项目使用结构化日志系统 (`@/core/errors/logger`) 替代原始的 `console`
调用，以提高 Dev 环境中的可追溯性和可维护性。

## 为什么使用 Logger?

### ❌ 不使用 console 的问题

```typescript
// 格式不统一,难以过滤
console.log('User clicked button', userId);
console.error('API call failed:', error);

// 缺少上下文信息
console.log('Mock enabled');

// 无法追溯和导出
console.warn('Network timeout');
```

### ✅ 使用 Logger 的优势

```typescript
import { logger } from '@/core/errors/logger';

// 结构化,易于查询和分析
logger.info('User interaction', { action: 'button_click', userId });
logger.error('API call failed', { error, endpoint: '/api/users' });

// 丰富的上下文
logger.info('Mock status changed', { enabled: true });

// 可追溯,可导出
logger.warn('Network timeout', { url, timeout: 5000 });
```

## 快速开始

### 1. 导入 Logger

```typescript
import { logger } from '@/core/errors/logger';
```

### 2. 使用日志级别

Logger 提供4个日志级别:

```typescript
// DEBUG - 调试信息
logger.debug('Component rendered', { props });

// INFO - 一般信息
logger.info('User logged in', { userId, timestamp });

// WARN - 警告信息
logger.warn('API rate limit approaching', { remaining: 10 });

// ERROR - 错误信息
logger.error('Failed to save data', { error, data });
```

## 实际使用示例

### 示例 1: Mock Manager

```typescript
// ❌ 原始 console 用法
console.log(`Mock ${enabled ? 'enabled' : 'disabled'}`);
console.warn(`Unknown operation ${operationName}`);

// ✅ Logger 用法
logger.info('Mock status changed', { enabled });
logger.warn('Unknown operation', { operationName });
```

### 示例 2: 应用启动 (main.tsx)

```typescript
// ❌ 原始 console 用法
console.log('🔧 加载调试工具...');
console.log('🎭 MSW: Mock Service Worker 已启动');
console.warn('⚠️ MSW: 启动失败:', error);

// ✅ Logger 用法
logger.info('🔧 加载调试工具...');
logger.info('🎭 MSW: Mock Service Worker 已启动');
logger.warn('⚠️ MSW: 启动失败', { error });
```

### 示例 3: 错误处理

```typescript
// ❌ 原始 console 用法
try {
  await createStrategy(data);
} catch (error) {
  console.error('Strategy creation failed:', error);
}

// ✅ Logger 用法
try {
  await createStrategy(data);
} catch (error) {
  logger.error('Strategy creation failed', { error, data });
}
```

### 示例 4: 环境变量验证

```typescript
// ❌ 原始 console 用法
if (missing.length > 0) {
  console.error('❌ 缺少必需的环境变量:', missing);
  return false;
}

// ✅ Logger 用法
if (missing.length > 0) {
  logger.error('❌ 缺少必需的环境变量', { missing });
  return false;
}
```

## Logger 功能特性

### 1. 自动元数据

每条日志自动包含:

- **timestamp**: 时间戳
- **level**: 日志级别
- **sessionId**: 会话ID
- **url**: 当前页面URL
- **userAgent**: 浏览器信息

### 2. 结构化上下文

```typescript
logger.info('User action', {
  action: 'purchase',
  productId: '123',
  quantity: 2,
  price: 99.99,
  timestamp: Date.now(),
});
```

日志输出格式:

```
[2025-10-05T12:34:56.789Z] [INFO] User action {
  action: 'purchase',
  productId: '123',
  quantity: 2,
  price: 99.99,
  timestamp: 1696512896789
}
```

### 3. 日志持久化

Logger 在内存中保存最近 1000 条日志:

```typescript
// 获取所有日志
const allLogs = logger.getLogs();

// 获取特定级别的日志
const errors = logger.getLogs(LogLevel.ERROR);

// 获取最近 50 条日志
const recentLogs = logger.getLogs(undefined, 50);

// 清空日志
logger.clearLogs();
```

### 4. 日志导出

```typescript
// 导出为 JSON
const jsonLogs = logger.exportLogs();
console.log(jsonLogs);

// 导出为 CSV
const csvLogs = logger.exportLogsAsCSV();
downloadFile(csvLogs, 'logs.csv');
```

## 开发环境调试

### 在浏览器控制台查看日志

Logger 在开发环境会输出到控制台,格式规范:

```
[2025-10-05T12:34:56.789Z] [INFO] Mock status changed { enabled: true }
[2025-10-05T12:34:57.123Z] [WARN] Unknown operation { operationName: 'InvalidQuery' }
[2025-10-05T12:34:58.456Z] [ERROR] API call failed { error: Error, endpoint: '/api/users' }
```

### 使用 Log Viewer

项目内置了日志查看器:

```typescript
// 在开发环境自动加载
import('@/core/debug/log-viewer');

// 在浏览器控制台访问
window.logViewer.showLogs();
window.logViewer.filterByLevel('error');
window.logViewer.exportLogs();
```

## 最佳实践

### 1. 使用结构化上下文

```typescript
// ❌ 不好 - 字符串拼接
logger.info(`User ${userId} logged in at ${timestamp}`);

// ✅ 好 - 结构化对象
logger.info('User logged in', { userId, timestamp });
```

### 2. 选择合适的日志级别

```typescript
// DEBUG - 仅开发环境需要的详细信息
logger.debug('Component props', { props });

// INFO - 正常的业务流程
logger.info('Order created', { orderId });

// WARN - 潜在问题,但不影响功能
logger.warn('API slow response', { duration: 5000 });

// ERROR - 错误,影响功能
logger.error('Failed to load data', { error });
```

### 3. 包含足够的上下文

```typescript
// ❌ 不好 - 上下文不足
logger.error('Save failed', { error });

// ✅ 好 - 足够的上下文
logger.error('Save failed', {
  error,
  userId,
  documentId,
  operation: 'update',
  timestamp: Date.now(),
});
```

### 4. 避免敏感信息

```typescript
// ❌ 不好 - 包含密码
logger.info('User login', { username, password });

// ✅ 好 - 不包含敏感信息
logger.info('User login', { username, timestamp });
```

## 何时仍然使用 console?

只有在以下特殊情况下才使用原始 console:

### 1. 底层工具(需要添加 eslint-disable)

```typescript
// data-protection.ts - 包装 console 对象本身
// eslint-disable-next-line no-console
if (typeof console[method] === 'function') {
  // eslint-disable-next-line no-console
  const originalMethod = console[method];
  // eslint-disable-next-line no-console
  console[method] = (...args) => {
    // 包装逻辑
    originalMethod.apply(console, args);
  };
}
```

### 2. Logger 本身的实现

```typescript
// logger.ts - 已有 eslint-disable 注释
/* eslint-disable no-console */
private outputToConsole(logEntry: LogEntry): void {
  switch (logEntry.level) {
    case LogLevel.DEBUG:
      console.debug(prefix, logEntry.message, logEntry.context);
      break;
    // ...
  }
}
```

## 迁移指南

### 从 console 迁移到 logger

1. **添加导入**:

   ```typescript
   import { logger } from '@/core/errors/logger';
   ```

2. **替换调用**:

   ```typescript
   // console.log → logger.info
   // console.warn → logger.warn
   // console.error → logger.error
   // console.debug → logger.debug
   ```

3. **结构化参数**:

   ```typescript
   // 从: console.log('Message:', value)
   // 到: logger.info('Message', { value })
   ```

4. **测试验证**:
   - 运行 `npm run lint` 确保没有 `no-console` 警告
   - 在开发环境测试日志输出
   - 检查日志格式和内容

## 项目修复统计

在本次Logger迁移中,我们完成了:

- ✅ 修复了 **45个 console 警告**
- ✅ 修改了 **4个文件**:
  - `src/mocks/mockManager.ts` (12处)
  - `src/main.tsx` (9处)
  - `src/features/strategies/pages/StrategiesPage.tsx` (1处)
  - `src/core/security/data-protection.ts` (添加eslint-disable)
- ✅ ESLint 警告从 **128个** 降低到 **70个** (减少45%)
- ✅ 所有业务代码使用结构化日志

## 相关资源

- Logger 源码: `src/core/errors/logger.ts`
- Log Viewer: `src/core/debug/log-viewer.ts`
- 使用示例: 本文档
- 项目文档: `CLAUDE.md`

## 常见问题

### Q: Logger 会影响性能吗?

A: Logger 设计为轻量级，对性能影响很小。

### Q: 日志太多怎么办?

A: 可以调整 `maxLogs` 参数：

```typescript
private maxLogs: number = 1000;
```

### Q: 如何查看历史日志?

A: 使用 `logger.getLogs()` 或在浏览器控制台使用 `window.logViewer`。

---

**最后更新**: 2025-10-05
**维护者**: QuantX Team
