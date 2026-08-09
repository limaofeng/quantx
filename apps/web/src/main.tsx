import { createRoot } from 'react-dom/client';

import { logger } from '@/core/errors/logger';
import {
  checkRequiredEnvVars,
  printEnvInfo,
  validateClientSideEnv,
} from '@/shared/utils/env';
import { setupGlobalErrorHandlers } from '@/shared/utils/error-handler';

import App from './App';

import './index.css';

// 验证环境变量
if (!checkRequiredEnvVars()) {
  throw new Error('环境变量配置不完整，应用无法启动');
}

// 验证客户端环境变量安全性
validateClientSideEnv();

// 打印环境信息（仅开发环境）
printEnvInfo();

// 初始化全局错误处理
setupGlobalErrorHandlers();

// 初始化 Web Vitals 性能监控
import('@/core/performance/web-vitals').then(({ initWebVitals }) => {
  initWebVitals();
});

// 初始化性能预算
import('@/config/performanceBudget').then(({ initPerformanceBudgets }) => {
  initPerformanceBudgets();
});

// 初始化路由性能监控
import('@/core/performance/route-monitor').then(
  ({ initRoutePerformanceMonitor }) => {
    initRoutePerformanceMonitor();
  }
);

// 初始化敏感信息保护
import('@/core/security/data-protection').then(({ initDataProtection }) => {
  initDataProtection();
});

// 开发环境加载调试工具
logger.info('Vite 环境变量', {
  NODE_ENV: import.meta.env.MODE,
  VITE_APP_ENV: import.meta.env.VITE_APP_ENV,
  DEV: import.meta.env.DEV,
  PROD: import.meta.env.PROD,
});

// 异步函数用于启动应用
// 异步函数用于启动应用
async function startApp() {
  // 开发环境加载调试工具
  if (import.meta.env.DEV) {
    logger.info('🔧 加载调试工具...');

    import('@/core/debug/log-viewer').then(() => {
      logger.info('✅ 日志查看器已加载');
    });
    import('@/core/debug/performance-viewer').then(() => {
      logger.info('✅ 性能调试工具已加载');
    });
  }

  // 启动 React 应用
  createRoot(document.getElementById('root')!).render(<App />);
}

// 启动应用
startApp();
