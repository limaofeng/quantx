# QuantX 前端基础设施文档

这份文档描述了 QuantX 前端应用的完整基础设施配置，包括错误处理、性能监控、环境配置和安全保护等系统。

## 📁 项目结构

```
src/
├── lib/
│   ├── errors/                 # 错误处理系统
│   │   ├── errorHandler.ts     # 全局错误处理器
│   │   └── logger.ts          # 日志系统
│   ├── performance/           # 性能监控系统
│   │   ├── webVitals.ts       # Web Vitals 监控
│   │   └── routePerformanceMonitor.ts  # 路由性能监控
│   ├── security/              # 安全保护系统
│   │   └── dataProtection.ts  # 敏感信息保护
│   ├── debug/                 # 调试工具
│   │   ├── logViewer.ts       # 日志查看器
│   │   └── performanceViewer.ts # 性能调试工具
│   └── env.ts                 # 环境变量验证
├── config/
│   └── performanceBudget.ts   # 性能预算配置
├── components/
│   └── ErrorBoundary.tsx     # React 错误边界
└── main.tsx                  # 应用入口点
```

## 🛡️ 错误处理系统

### 功能特性

- **全局错误捕获**: 自动捕获未处理的 JavaScript 错误和 Promise 拒绝
- **错误分类**: 按类型（网络、业务、系统、验证等）和严重程度分类
- **React 错误边界**: 捕获组件渲染错误并显示友好界面
- **错误上报**: 生产环境自动上报错误到监控服务

### 使用方法

```typescript
import { handleError } from '@/lib/errors/errorHandler';

// 手动处理错误
try {
  // 可能出错的代码
} catch (error) {
  handleError(error, { component: 'MyComponent' });
}
```

### 调试工具

开发环境下可通过控制台使用：

```javascript
// 查看所有日志
debugLogs.logs();

// 查看错误日志
debugLogs.errors();

// 查看最近 10 条日志
debugLogs.recent(10);

// 导出日志
debugLogs.export();

// 清空日志
debugLogs.clear();
```

## 📊 性能监控系统

### Web Vitals 监控

监控以下 Core Web Vitals 指标：

- **CLS** (Cumulative Layout Shift): 累积布局偏移
- **FCP** (First Contentful Paint): 首次内容绘制
- **FID** (First Input Delay): 首次输入延迟
- **INP** (Interaction to Next Paint): 交互到下次绘制
- **LCP** (Largest Contentful Paint): 最大内容绘制
- **TTFB** (Time to First Byte): 首字节时间

### 性能预算

为不同环境和页面配置了性能预算：

```typescript
// 开发环境预算 (相对宽松)
{
  CLS: 0.15,
  FCP: 2000,
  LCP: 3000,
  // ...
}

// 生产环境预算 (严格标准)
{
  CLS: 0.1,
  FCP: 1500,
  LCP: 2500,
  // ...
}
```

### 调试工具

开发环境下可通过控制台使用：

```javascript
// 查看性能摘要
debugPerformance.summary();

// 查看所有指标
debugPerformance.metrics();

// 查看特定指标
debugPerformance.metric('LCP');

// 设置性能预算
debugPerformance.setBudget('LCP', 2500);

// 启用实时监控
debugPerformance.monitor();
```

## 🌍 环境配置

### 支持的环境

- **development**: 开发环境
- **staging**: 测试/预发布环境
- **production**: 生产环境

### 配置文件

- `.env.example`: 配置示例文件
- `.env.development`: 开发环境配置
- `.env.staging`: 测试环境配置
- `.env.production`: 生产环境配置

### 配置项分类

#### 应用基础配置

```env
VITE_APP_ENV=development
VITE_DEBUG=true
VITE_APP_TITLE=QuantX
```

#### API 配置

```env
VITE_GRAPHQL_HTTP_URL=/graphql
VITE_GRAPHQL_WS_URL=ws://192.168.5.6:8080/graphql
```

#### 功能开关

```env
VITE_ENABLE_PERFORMANCE_MONITORING=true
VITE_ENABLE_ERROR_REPORTING=true
VITE_ENABLE_ANALYTICS=false
```

#### 开发工具配置

```env
VITE_ENABLE_DEV_TOOLS=true
VITE_ENABLE_REACT_QUERY_DEVTOOLS=true
VITE_ENABLE_APOLLO_DEVTOOLS=true
```

### 环境变量验证

使用 Zod 进行运行时验证，确保配置的类型安全和有效性。

## 🔒 安全保护系统

### 敏感信息保护

自动检测和脱敏以下类型的敏感信息：

- API Keys 和 Token
- 密码和密钥
- 个人身份信息 (邮箱、身份证号、手机号等)
- 信用卡号和金融信息

### 保护范围

- **日志记录**: 自动脱敏日志中的敏感信息
- **错误报告**: 脱敏错误堆栈和上下文中的敏感数据
- **控制台输出**: 生产环境下自动脱敏控制台输出
- **网络请求**: 脱敏 URL 参数和请求体中的敏感信息

### 使用方法

```typescript
import { sanitize, secureLogger } from '@/lib/security/dataProtection';

// 脱敏字符串
const safe = sanitize.string(unsafeString);

// 脱敏对象
const safeObject = sanitize.object(unsafeObject);

// 安全地记录日志
secureLogger.secureLog('info', 'User action', userData);
```

## 🚀 启动流程

应用启动时会依次初始化以下系统：

1. **环境变量验证**: 验证配置的有效性
2. **全局错误处理**: 设置错误监听器
3. **日志系统**: 初始化日志记录
4. **Web Vitals 监控**: 开始性能监控
5. **性能预算**: 配置性能目标
6. **路由性能监控**: 监听路由变化
7. **敏感信息保护**: 启用安全保护
8. **调试工具**: 开发环境下加载调试工具

## 📈 监控和调试

### 开发环境

- 完整的调试工具套件
- 详细的日志输出
- 性能指标实时监控
- 错误详情展示

### 生产环境

- 自动错误上报
- 性能指标收集
- 敏感信息保护
- 最小化日志输出

## 🔧 最佳实践

### 错误处理

1. 使用 `handleError` 函数统一处理错误
2. 为错误提供足够的上下文信息
3. 在关键操作周围使用 ErrorBoundary
4. 定期检查错误历史和趋势

### 性能优化

1. 监控 Core Web Vitals 指标
2. 设置合理的性能预算
3. 定期审查性能违规
4. 针对不同页面调整预算标准

### 安全最佳实践

1. 避免在客户端存储敏感信息
2. 使用安全的日志记录方法
3. 定期审查日志输出
4. 在生产环境启用完整的安全保护

## 📚 参考资料

- [Web Vitals](https://web.dev/vitals/)
- [React Error Boundaries](https://reactjs.org/docs/error-boundaries.html)
- [Content Security Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP)
- [Vite Environment Variables](https://vitejs.dev/guide/env-and-mode.html)

---

这套基础设施为 QuantX 前端应用提供了企业级的可靠性、性能和安全保障。所有系统都经过精心设计，确保在开发和生产环境下都能稳定运行。
