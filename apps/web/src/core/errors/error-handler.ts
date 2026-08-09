/**
 * 全局错误处理系统
 * 统一处理和分类应用中的各种错误
 */
/* eslint-disable no-console */

import { logger } from './logger';

// 错误类型枚举
export enum ErrorType {
  NETWORK = 'network',
  BUSINESS = 'business',
  SYSTEM = 'system',
  VALIDATION = 'validation',
  AUTHENTICATION = 'authentication',
  AUTHORIZATION = 'authorization',
  UNKNOWN = 'unknown',
}

// 错误严重程度
export enum ErrorSeverity {
  LOW = 'low',
  MEDIUM = 'medium',
  HIGH = 'high',
  CRITICAL = 'critical',
}

// 标准化错误接口
export interface StandardError {
  id: string;
  type: ErrorType;
  severity: ErrorSeverity;
  message: string;
  originalError?: Error;
  context?: Record<string, any>;
  timestamp: Date;
  userAgent?: string;
  url?: string;
  userId?: string;
  stack?: string;
}

// 错误分类器
class ErrorClassifier {
  static classify(error: Error | any): ErrorType {
    // 网络错误
    if (
      error?.name === 'NetworkError' ||
      error?.message?.includes('fetch') ||
      error?.message?.includes('network') ||
      error?.code === 'NETWORK_ERROR'
    ) {
      return ErrorType.NETWORK;
    }

    // GraphQL 错误
    if (error?.graphQLErrors || error?.networkError) {
      return ErrorType.NETWORK;
    }

    // 认证错误
    if (
      error?.status === 401 ||
      error?.message?.includes('unauthorized') ||
      error?.message?.includes('authentication')
    ) {
      return ErrorType.AUTHENTICATION;
    }

    // 授权错误
    if (
      error?.status === 403 ||
      error?.message?.includes('forbidden') ||
      error?.message?.includes('authorization')
    ) {
      return ErrorType.AUTHORIZATION;
    }

    // 验证错误
    if (
      error?.status === 400 ||
      error?.name === 'ValidationError' ||
      error?.message?.includes('validation')
    ) {
      return ErrorType.VALIDATION;
    }

    // 业务逻辑错误
    if (error?.status >= 400 && error?.status < 500) {
      return ErrorType.BUSINESS;
    }

    // 系统错误
    if (error?.status >= 500) {
      return ErrorType.SYSTEM;
    }

    // React 组件错误
    if (error?.name === 'ChunkLoadError' || error?.name === 'TypeError') {
      return ErrorType.SYSTEM;
    }

    return ErrorType.UNKNOWN;
  }

  static getSeverity(error: Error | any, type: ErrorType): ErrorSeverity {
    // 关键系统错误
    if (type === ErrorType.SYSTEM && error?.status >= 500) {
      return ErrorSeverity.CRITICAL;
    }

    // 认证/授权错误
    if (type === ErrorType.AUTHENTICATION || type === ErrorType.AUTHORIZATION) {
      return ErrorSeverity.HIGH;
    }

    // 网络错误
    if (type === ErrorType.NETWORK) {
      return ErrorSeverity.MEDIUM;
    }

    // 业务逻辑错误
    if (type === ErrorType.BUSINESS || type === ErrorType.VALIDATION) {
      return ErrorSeverity.LOW;
    }

    return ErrorSeverity.MEDIUM;
  }
}

// 错误处理器类
export class ErrorHandler {
  private static instance: ErrorHandler;
  private errorQueue: StandardError[] = [];
  private listeners: ((error: StandardError) => void)[] = [];

  static getInstance(): ErrorHandler {
    if (!ErrorHandler.instance) {
      ErrorHandler.instance = new ErrorHandler();
    }
    return ErrorHandler.instance;
  }

  // 处理错误的主方法
  handleError(
    error: Error | any,
    context?: Record<string, any>
  ): StandardError {
    const type = ErrorClassifier.classify(error);
    const severity = ErrorClassifier.getSeverity(error, type);

    const standardError: StandardError = {
      id: this.generateErrorId(),
      type,
      severity,
      message: this.extractMessage(error),
      originalError: error instanceof Error ? error : undefined,
      context,
      timestamp: new Date(),
      userAgent:
        typeof navigator !== 'undefined' ? navigator.userAgent : undefined,
      url: typeof window !== 'undefined' ? window.location.href : undefined,
      stack: error?.stack,
    };

    // 添加到错误队列
    this.errorQueue.push(standardError);

    // 通知所有监听器
    this.notifyListeners(standardError);

    // 根据错误类型和严重程度决定处理方式
    this.processError(standardError);

    return standardError;
  }

  // 添加错误监听器
  addListener(listener: (error: StandardError) => void): () => void {
    this.listeners.push(listener);

    // 返回取消监听的函数
    return () => {
      const index = this.listeners.indexOf(listener);
      if (index > -1) {
        this.listeners.splice(index, 1);
      }
    };
  }

  // 获取错误历史
  getErrorHistory(): StandardError[] {
    return [...this.errorQueue];
  }

  // 清空错误历史
  clearErrorHistory(): void {
    this.errorQueue = [];
  }

  private generateErrorId(): string {
    return `err_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  private extractMessage(error: any): string {
    if (typeof error === 'string') {
      return error;
    }

    if (error?.message) {
      return error.message;
    }

    if (error?.error?.message) {
      return error.error.message;
    }

    if (error?.graphQLErrors?.length > 0) {
      return error.graphQLErrors[0].message;
    }

    if (error?.networkError?.message) {
      return error.networkError.message;
    }

    return '未知错误';
  }

  private notifyListeners(error: StandardError): void {
    this.listeners.forEach(listener => {
      try {
        listener(error);
      } catch (e) {
        console.error('错误监听器执行失败:', e);
      }
    });
  }

  private processError(error: StandardError): void {
    // 使用日志系统记录错误
    try {
      logger.logError(error);
    } catch (logError) {
      console.error('日志记录失败:', logError);
    }

    // 控制台输出（开发环境）
    if (import.meta.env.DEV) {
      console.group(
        `🚨 ${error.severity.toUpperCase()} - ${error.type.toUpperCase()}`
      );
      console.error('Message:', error.message);
      console.error('Context:', error.context);
      if (error.originalError) {
        console.error('Original Error:', error.originalError);
      }
      console.groupEnd();
    }

    // 生产环境错误上报
    if (import.meta.env.PROD) {
      this.reportError(error);
    }
  }

  private async reportError(error: StandardError): Promise<void> {
    try {
      // 这里可以集成第三方错误监控服务
      // 比如 Sentry、LogRocket、Bugsnag 等

      // 示例：发送到自定义错误收集端点
      // await fetch('/api/errors', {
      //   method: 'POST',
      //   headers: {
      //     'Content-Type': 'application/json',
      //   },
      //   body: JSON.stringify(error),
      // })

      console.log('错误已上报:', error.id);
    } catch (reportError) {
      console.error('错误上报失败:', reportError);
    }
  }
}

// 全局错误处理函数
export const handleError = (
  error: Error | any,
  context?: Record<string, any>
): StandardError => {
  return ErrorHandler.getInstance().handleError(error, context);
};

// 错误监听器
export const addErrorListener = (listener: (error: StandardError) => void) => {
  return ErrorHandler.getInstance().addListener(listener);
};

// 获取错误历史
export const getErrorHistory = () => {
  return ErrorHandler.getInstance().getErrorHistory();
};

// 用户友好的错误消息映射
export const getUserFriendlyMessage = (error: StandardError): string => {
  switch (error.type) {
    case ErrorType.NETWORK:
      return '网络连接异常，请检查网络设置后重试';
    case ErrorType.AUTHENTICATION:
      return '登录已过期，请重新登录';
    case ErrorType.AUTHORIZATION:
      return '您没有权限执行此操作';
    case ErrorType.VALIDATION:
      return '输入信息有误，请检查后重试';
    case ErrorType.BUSINESS:
      return error.message || '操作失败，请稍后重试';
    case ErrorType.SYSTEM:
      return '系统异常，请稍后重试或联系技术支持';
    default:
      return '操作失败，请重试';
  }
};

// 初始化全局错误监听
export const initializeGlobalErrorHandling = (): void => {
  // 捕获未处理的 Promise 错误
  window.addEventListener('unhandledrejection', event => {
    handleError(event.reason, {
      type: 'unhandledrejection',
      promise: event.promise,
    });
  });

  // 捕获未处理的 JavaScript 错误
  window.addEventListener('error', event => {
    handleError(event.error || event.message, {
      type: 'error',
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
    });
  });

  // 捕获资源加载错误
  window.addEventListener(
    'error',
    event => {
      if (event.target !== window) {
        const resource = event.target;
        const resourceUrl =
          resource instanceof HTMLImageElement ||
          resource instanceof HTMLScriptElement
            ? resource.src
            : resource instanceof HTMLLinkElement
              ? resource.href
              : 'unknown resource';
        handleError(new Error(`资源加载失败: ${resourceUrl}`), {
          type: 'resource_error',
          element: event.target,
        });
      }
    },
    true
  );
};
