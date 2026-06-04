/**
 * 日志查看器工具
 * 开发环境下可以通过浏览器控制台查看和管理日志
 */
/* eslint-disable no-console */

import { getErrorHistory } from '@/core/errors/error-handler';
import { logger } from '@/core/errors/logger';

interface DebugLogTools {
  // 查看所有日志
  logs: () => void;
  // 查看错误日志
  errors: () => void;
  // 查看最近的日志
  recent: (count?: number) => void;
  // 导出日志
  export: () => void;
  // 清空日志
  clear: () => void;
  // 查看错误历史
  errorHistory: () => void;
  // 生成测试日志
  testLogs: () => void;
}

declare global {
  interface Window {
    debugLogs?: DebugLogTools;
  }
}

// 创建调试工具
const createDebugLogTools = (): DebugLogTools => {
  return {
    logs: () => {
      const logs = logger.getLogs();
      console.group('📋 所有日志');
      console.table(
        logs.map(log => ({
          时间: log.timestamp.toLocaleString(),
          级别: log.level,
          消息: log.message,
          URL: log.url?.split('/').pop() || '未知',
        }))
      );
      console.groupEnd();
    },

    errors: () => {
      const errorLogs = logger.getLogs().filter(log => log.level === 'error');
      console.group('🚨 错误日志');
      if (errorLogs.length === 0) {
        console.log('没有错误日志');
      } else {
        console.table(
          errorLogs.map(log => ({
            时间: log.timestamp.toLocaleString(),
            消息: log.message,
            上下文: log.context,
          }))
        );
      }
      console.groupEnd();
    },

    recent: (count = 10) => {
      const recentLogs = logger.getLogs().slice(-count);
      console.group(`📄 最近 ${count} 条日志`);
      recentLogs.forEach(log => {
        const icon =
          {
            debug: '🐛',
            info: 'ℹ️',
            warn: '⚠️',
            error: '🚨',
          }[log.level] || 'ℹ️';

        console.log(
          `${icon} ${log.timestamp.toLocaleTimeString()} [${log.level.toUpperCase()}] ${log.message}`,
          log.context || ''
        );
      });
      console.groupEnd();
    },

    export: () => {
      const jsonData = logger.exportLogs();
      const csvData = logger.exportLogsAsCSV();

      console.group('📦 导出日志');
      console.log('JSON 格式:');
      console.log(jsonData);
      console.log('\nCSV 格式:');
      console.log(csvData);

      // 创建下载链接
      const jsonBlob = new Blob([jsonData], { type: 'application/json' });
      const csvBlob = new Blob([csvData], { type: 'text/csv' });

      const jsonUrl = URL.createObjectURL(jsonBlob);
      const csvUrl = URL.createObjectURL(csvBlob);

      console.log('下载链接 (复制到新标签页):');
      console.log('JSON:', jsonUrl);
      console.log('CSV:', csvUrl);

      console.groupEnd();
    },

    clear: () => {
      logger.clearLogs();
      console.log('✅ 日志已清空');
    },

    errorHistory: () => {
      const errors = getErrorHistory();
      console.group('🔍 错误历史');
      if (errors.length === 0) {
        console.log('没有错误记录');
      } else {
        console.table(
          errors.map(error => ({
            ID: error.id,
            类型: error.type,
            严重程度: error.severity,
            消息: error.message,
            时间: error.timestamp.toLocaleString(),
          }))
        );
      }
      console.groupEnd();
    },

    testLogs: () => {
      console.log('🧪 生成测试日志...');

      logger.debug('这是一条调试信息', { component: 'TestComponent' });
      logger.info('用户登录成功', { userId: 'user123', timestamp: Date.now() });
      logger.warn('API 响应较慢', { endpoint: '/api/data', duration: 2500 });
      logger.error('网络请求失败', {
        url: '/api/error-endpoint',
        status: 500,
        error: 'Internal Server Error',
      });

      console.log('✅ 测试日志已生成');
    },
  };
};

// 在开发环境下将调试工具挂载到全局对象
console.log('日志调试工具环境检测:', import.meta.env.DEV);
if (import.meta.env.DEV) {
  // 挂载到全局
  window.debugLogs = createDebugLogTools();

  // 输出使用提示
  console.log(
    '%c🔧 QuantX Debug Tools',
    'color: #10b981; font-size: 14px; font-weight: bold;'
  );
  console.log(
    '%c在控制台中使用以下命令查看日志:',
    'color: #6b7280; font-size: 12px;'
  );
  console.log(
    '%cdebugLogs.logs()%c - 查看所有日志',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugLogs.errors()%c - 查看错误日志',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugLogs.recent(10)%c - 查看最近10条日志',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugLogs.export()%c - 导出日志',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugLogs.clear()%c - 清空日志',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugLogs.testLogs()%c - 生成测试日志',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
}

export { createDebugLogTools };
export type { DebugLogTools };
