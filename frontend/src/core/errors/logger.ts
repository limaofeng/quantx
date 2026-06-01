/**
 * 前端日志系统
 * 统一管理应用中的各种日志记录
 */
/* eslint-disable no-console */

import { type StandardError, ErrorSeverity } from './error-handler';

// 日志级别枚举
export enum LogLevel {
  DEBUG = 'debug',
  INFO = 'info',
  WARN = 'warn',
  ERROR = 'error',
}

// 日志接口
export interface LogEntry {
  id: string;
  level: LogLevel;
  message: string;
  timestamp: Date;
  context?: Record<string, any>;
  userAgent?: string;
  url?: string;
  userId?: string;
  sessionId?: string;
}

// 日志记录器类
export class Logger {
  private static instance: Logger;
  private logs: LogEntry[] = [];
  private maxLogs: number = 1000;
  private sessionId: string;

  private constructor() {
    this.sessionId = this.generateSessionId();
  }

  static getInstance(): Logger {
    if (!Logger.instance) {
      Logger.instance = new Logger();
    }
    return Logger.instance;
  }

  // 调试日志
  debug(message: string, context?: Record<string, any>): void {
    this.log(LogLevel.DEBUG, message, context);
  }

  // 信息日志
  info(message: string, context?: Record<string, any>): void {
    this.log(LogLevel.INFO, message, context);
  }

  // 警告日志
  warn(message: string, context?: Record<string, any>): void {
    this.log(LogLevel.WARN, message, context);
  }

  // 错误日志
  error(message: string, context?: Record<string, any>): void {
    this.log(LogLevel.ERROR, message, context);
  }

  // 记录标准化错误
  logError(standardError: StandardError): void {
    const level = this.mapSeverityToLogLevel(standardError.severity);
    this.log(level, standardError.message, {
      errorId: standardError.id,
      errorType: standardError.type,
      severity: standardError.severity,
      originalError: standardError.originalError?.message,
      stack: standardError.stack,
      ...standardError.context,
    });
  }

  // 核心日志记录方法
  private log(
    level: LogLevel,
    message: string,
    context?: Record<string, any>
  ): void {
    const logEntry: LogEntry = {
      id: this.generateLogId(),
      level,
      message,
      timestamp: new Date(),
      context,
      userAgent:
        typeof navigator !== 'undefined' ? navigator.userAgent : undefined,
      url: typeof window !== 'undefined' ? window.location.href : undefined,
      sessionId: this.sessionId,
    };

    // 添加到日志队列
    this.logs.push(logEntry);

    // 维护日志队列大小
    if (this.logs.length > this.maxLogs) {
      this.logs.shift();
    }

    // 控制台输出
    this.outputToConsole(logEntry);

    // 生产环境可以发送到远程日志服务
    if (import.meta.env.PROD) {
      this.sendToRemoteLogger(logEntry);
    }
  }

  // 获取日志历史
  getLogs(level?: LogLevel, limit?: number): LogEntry[] {
    let filteredLogs = this.logs;

    if (level) {
      filteredLogs = this.logs.filter(log => log.level === level);
    }

    if (limit) {
      return filteredLogs.slice(-limit);
    }

    return [...filteredLogs];
  }

  // 清空日志
  clearLogs(): void {
    this.logs = [];
  }

  // 导出日志为JSON
  exportLogs(): string {
    return JSON.stringify(this.logs, null, 2);
  }

  // 导出日志为CSV
  exportLogsAsCSV(): string {
    if (this.logs.length === 0) {
      return 'No logs to export';
    }

    const headers = ['timestamp', 'level', 'message', 'url', 'sessionId'];
    const csvContent = [
      headers.join(','),
      ...this.logs.map(log =>
        [
          log.timestamp.toISOString(),
          log.level,
          `"${log.message.replace(/"/g, '""')}"`,
          log.url || '',
          log.sessionId,
        ].join(',')
      ),
    ].join('\n');

    return csvContent;
  }

  private generateLogId(): string {
    return `log_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  private generateSessionId(): string {
    return `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  private mapSeverityToLogLevel(severity: ErrorSeverity): LogLevel {
    switch (severity) {
      case ErrorSeverity.LOW:
        return LogLevel.INFO;
      case ErrorSeverity.MEDIUM:
        return LogLevel.WARN;
      case ErrorSeverity.HIGH:
      case ErrorSeverity.CRITICAL:
        return LogLevel.ERROR;
      default:
        return LogLevel.INFO;
    }
  }

  private outputToConsole(logEntry: LogEntry): void {
    const timestamp = logEntry.timestamp.toISOString();
    const prefix = `[${timestamp}] [${logEntry.level.toUpperCase()}]`;

    switch (logEntry.level) {
      case LogLevel.DEBUG:
        console.debug(prefix, logEntry.message, logEntry.context);
        break;
      case LogLevel.INFO:
        console.info(prefix, logEntry.message, logEntry.context);
        break;
      case LogLevel.WARN:
        console.warn(prefix, logEntry.message, logEntry.context);
        break;
      case LogLevel.ERROR:
        console.error(prefix, logEntry.message, logEntry.context);
        break;
    }
  }

  private async sendToRemoteLogger(logEntry: LogEntry): Promise<void> {
    try {
      // 这里可以集成远程日志服务
      // 比如：Logtail、Papertrail、自建日志服务等

      // 示例：发送到自定义日志收集端点
      // await fetch('/api/logs', {
      //   method: 'POST',
      //   headers: {
      //     'Content-Type': 'application/json',
      //   },
      //   body: JSON.stringify(logEntry),
      // })

      // 临时输出
      console.log('日志已记录:', logEntry.id);
    } catch (error) {
      console.error('远程日志发送失败:', error);
    }
  }
}

// 导出便捷函数
export const logger = Logger.getInstance();

// 便捷日志函数
export const log = {
  debug: (message: string, context?: Record<string, any>) =>
    logger.debug(message, context),
  info: (message: string, context?: Record<string, any>) =>
    logger.info(message, context),
  warn: (message: string, context?: Record<string, any>) =>
    logger.warn(message, context),
  error: (message: string, context?: Record<string, any>) =>
    logger.error(message, context),
  logError: (standardError: StandardError) => logger.logError(standardError),
};

// 性能监控日志
export const performanceLogger = {
  // 记录页面加载时间
  logPageLoad: (loadTime: number, route: string) => {
    logger.info('页面加载完成', {
      loadTime,
      route,
      type: 'performance',
      metric: 'page_load',
    });
  },

  // 记录API请求时间
  logApiRequest: (
    url: string,
    method: string,
    duration: number,
    status?: number
  ) => {
    const level = status && status >= 400 ? LogLevel.ERROR : LogLevel.INFO;
    logger.log(level, `API请求完成: ${method} ${url}`, {
      url,
      method,
      duration,
      status,
      type: 'performance',
      metric: 'api_request',
    });
  },

  // 记录组件渲染时间
  logComponentRender: (componentName: string, renderTime: number) => {
    logger.debug(`组件渲染: ${componentName}`, {
      componentName,
      renderTime,
      type: 'performance',
      metric: 'component_render',
    });
  },
};

// 用户行为日志
export const userActionLogger = {
  // 记录用户点击
  logClick: (element: string, context?: Record<string, any>) => {
    logger.info(`用户点击: ${element}`, {
      element,
      type: 'user_action',
      action: 'click',
      ...context,
    });
  },

  // 记录页面访问
  logPageView: (route: string, referrer?: string) => {
    logger.info(`页面访问: ${route}`, {
      route,
      referrer,
      type: 'user_action',
      action: 'page_view',
    });
  },

  // 记录搜索行为
  logSearch: (query: string, results?: number) => {
    logger.info(`用户搜索: ${query}`, {
      query,
      results,
      type: 'user_action',
      action: 'search',
    });
  },
};
