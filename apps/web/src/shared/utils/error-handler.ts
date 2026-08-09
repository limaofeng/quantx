// 全局错误处理工具
import { logger } from '@/core/errors/logger';
import { toast } from '@/hooks/use-toast';

export interface ErrorContext {
  component?: string;
  componentStack?: string;
  action?: string;
  userId?: string;
  timestamp?: string;
  url?: string;
  userAgent?: string;
  additionalInfo?: Record<string, unknown>;
}

export interface StandardError {
  id: string;
  message: string;
  originalError: Error;
  context: ErrorContext;
  timestamp: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  category: 'network' | 'validation' | 'runtime' | 'unknown';
}

/**
 * 错误严重级别判断
 */
function getErrorSeverity(error: Error): StandardError['severity'] {
  const message = error.message.toLowerCase();

  if (message.includes('network') || message.includes('fetch')) {
    return 'medium';
  }

  if (message.includes('chunk') || message.includes('loading')) {
    return 'low';
  }

  if (message.includes('unauthorized') || message.includes('forbidden')) {
    return 'high';
  }

  if (message.includes('reference') || message.includes('null')) {
    return 'critical';
  }

  return 'medium';
}

/**
 * 错误分类
 */
function getErrorCategory(error: Error): StandardError['category'] {
  const message = error.message.toLowerCase();

  if (
    message.includes('network') ||
    message.includes('fetch') ||
    message.includes('cors')
  ) {
    return 'network';
  }

  if (message.includes('validation') || message.includes('invalid')) {
    return 'validation';
  }

  if (error.name === 'TypeError' || error.name === 'ReferenceError') {
    return 'runtime';
  }

  return 'unknown';
}

/**
 * 生成用户友好的错误消息
 */
export function getUserFriendlyMessage(error: Error): string {
  const category = getErrorCategory(error);

  switch (category) {
    case 'network':
      return '网络连接失败，请检查您的网络连接后重试';

    case 'validation':
      return '输入的数据格式不正确，请检查后重新提交';

    case 'runtime':
      return '程序运行出现异常，我们正在努力修复这个问题';

    default:
      return '发生了未知错误，请稍后重试或联系技术支持';
  }
}

/**
 * 主要错误处理函数
 */
export function handleError(
  error: Error,
  context: ErrorContext = {}
): StandardError {
  const standardError: StandardError = {
    id: generateErrorId(),
    message: getUserFriendlyMessage(error),
    originalError: error,
    context: {
      timestamp: new Date().toISOString(),
      url: window.location.href,
      userAgent: navigator.userAgent,
      ...context,
    },
    timestamp: new Date().toISOString(),
    severity: getErrorSeverity(error),
    category: getErrorCategory(error),
  };

  // 记录错误（开发环境在控制台，生产环境发送到错误监控服务）
  logError(standardError);

  // 根据严重程度决定是否显示用户通知
  if (shouldShowUserNotification(standardError)) {
    showErrorToUser(standardError);
  }

  return standardError;
}

/**
 * 生成错误ID
 */
function generateErrorId(): string {
  return `error_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * 错误日志记录
 */
function logError(error: StandardError): void {
  if (import.meta.env.DEV) {
    logger.error(`🚨 Error [${error.severity}] - ${error.category}`, {
      message: error.message,
      originalError: error.originalError,
      context: error.context,
      stack: error.originalError.stack,
    });
  } else {
    // 生产环境：发送到错误监控服务
    // sendToErrorService(error);
  }
}

/**
 * 判断是否应该显示用户通知
 */
function shouldShowUserNotification(error: StandardError): boolean {
  // 低严重级别的错误不显示通知
  if (error.severity === 'low') {
    return false;
  }

  // 网络错误总是显示通知
  if (error.category === 'network') {
    return true;
  }

  // 验证错误显示通知
  if (error.category === 'validation') {
    return true;
  }

  // 其他中等以上严重级别的错误显示通知
  return true;
}

/**
 * 向用户显示错误
 */
function showErrorToUser(error: StandardError): void {
  const variant =
    error.severity === 'critical' || error.severity === 'high'
      ? 'destructive'
      : 'default';

  toast({
    title: '操作失败',
    description: error.message,
    variant,
  });

  // For critical errors, suggest page reload
  if (error.severity === 'critical') {
    // eslint-disable-next-line no-console
    console.error(error);
  }
}

/**
 * 异步操作错误处理包装器
 */
export function withErrorHandling<T extends any[], R>(
  fn: (...args: T) => Promise<R>,
  context?: ErrorContext
) {
  return async (...args: T): Promise<R> => {
    try {
      return await fn(...args);
    } catch (error) {
      handleError(error as Error, context);
      throw error; // 重新抛出，让调用方决定如何处理
    }
  };
}

/**
 * React Hook 错误处理
 */
export function useErrorHandler() {
  return (error: Error, context?: ErrorContext) => {
    handleError(error, {
      component: 'useErrorHandler',
      ...context,
    });
  };
}

/**
 * 全局未捕获错误处理
 */
export function setupGlobalErrorHandlers(): void {
  // 捕获同步错误
  window.addEventListener('error', event => {
    handleError(event.error, {
      component: 'GlobalErrorHandler',
      action: 'unhandled_error',
      additionalInfo: {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
      },
    });
  });

  // 捕获Promise未处理的拒绝
  window.addEventListener('unhandledrejection', event => {
    const error =
      event.reason instanceof Error
        ? event.reason
        : new Error(String(event.reason));

    handleError(error, {
      component: 'GlobalErrorHandler',
      action: 'unhandled_promise_rejection',
    });

    // 阻止浏览器默认的控制台错误输出
    event.preventDefault();
  });
}
