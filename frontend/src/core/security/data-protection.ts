/**
 * 敏感信息保护系统
 * 防止敏感数据泄露到日志、控制台或错误报告中
 */

import { logger } from '@/core/errors/logger';

// 敏感数据模式定义
const SENSITIVE_PATTERNS = [
  // API Keys 和 Token
  /([a-zA-Z0-9]{20,})/g, // 通用长字符串
  /(sk_[a-zA-Z0-9]+)/gi, // Stripe secret keys
  /(pk_[a-zA-Z0-9]+)/gi, // Stripe public keys
  /(Bearer\s+[a-zA-Z0-9._-]+)/gi, // Bearer tokens
  /(Authorization:\s*[a-zA-Z0-9._-]+)/gi, // Authorization headers

  // 密码和敏感字段
  /(password[=:]\s*["']?[^"'\s]+)/gi,
  /(passwd[=:]\s*["']?[^"'\s]+)/gi,
  /(secret[=:]\s*["']?[^"'\s]+)/gi,
  /(apikey[=:]\s*["']?[^"'\s]+)/gi,
  /(api_key[=:]\s*["']?[^"'\s]+)/gi,

  // 个人身份信息
  /(\d{15,19})/g, // 信用卡号
  /(\d{3}-\d{2}-\d{4})/g, // SSN
  /([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/g, // 邮箱地址

  // 中国身份证号
  /(\d{17}[\dXx])/g,
  // 中国手机号
  /(1[3-9]\d{9})/g,
];

// 敏感字段名称列表
const SENSITIVE_FIELD_NAMES = [
  'password',
  'passwd',
  'secret',
  'token',
  'apikey',
  'api_key',
  'access_token',
  'refresh_token',
  'private_key',
  'credential',
  'authorization',
  'ssn',
  'social_security',
  'credit_card',
  'card_number',
  'cvv',
  'pin',
  'account_number',
  'routing_number',
];

// 敏感数据脱敏器
export class DataSanitizer {
  private static instance: DataSanitizer;

  static getInstance(): DataSanitizer {
    if (!DataSanitizer.instance) {
      DataSanitizer.instance = new DataSanitizer();
    }
    return DataSanitizer.instance;
  }

  // 脱敏字符串中的敏感信息
  sanitizeString(input: string): string {
    if (!input || typeof input !== 'string') {
      return input;
    }

    let sanitized = input;

    // 应用所有敏感数据模式
    SENSITIVE_PATTERNS.forEach(pattern => {
      sanitized = sanitized.replace(pattern, match => {
        // 保留前几个字符，其余用 * 替换
        const visibleChars = Math.min(3, Math.floor(match.length * 0.3));
        const hiddenChars = match.length - visibleChars;
        return match.substring(0, visibleChars) + '*'.repeat(hiddenChars);
      });
    });

    return sanitized;
  }

  // 脱敏对象中的敏感字段
  sanitizeObject(obj: any, maxDepth: number = 10): any {
    if (maxDepth <= 0) {
      return '[Max Depth Reached]';
    }

    if (obj === null || obj === undefined) {
      return obj;
    }

    if (typeof obj === 'string') {
      return this.sanitizeString(obj);
    }

    if (typeof obj === 'number' || typeof obj === 'boolean') {
      return obj;
    }

    if (obj instanceof Date) {
      return obj;
    }

    if (Array.isArray(obj)) {
      return obj.map(item => this.sanitizeObject(item, maxDepth - 1));
    }

    if (typeof obj === 'object') {
      const sanitized: any = {};

      Object.keys(obj).forEach(key => {
        const lowerKey = key.toLowerCase();
        const isSensitiveField = SENSITIVE_FIELD_NAMES.some(sensitiveField =>
          lowerKey.includes(sensitiveField)
        );

        if (isSensitiveField) {
          // 完全脱敏敏感字段
          sanitized[key] = '[REDACTED]';
        } else {
          // 递归处理嵌套对象
          sanitized[key] = this.sanitizeObject(obj[key], maxDepth - 1);
        }
      });

      return sanitized;
    }

    // 其他类型的数据，转换为字符串后脱敏
    return this.sanitizeString(String(obj));
  }

  // 脱敏 URL 中的敏感参数
  sanitizeUrl(url: string): string {
    if (!url || typeof url !== 'string') {
      return url;
    }

    try {
      const urlObj = new URL(url);
      const params = new URLSearchParams(urlObj.search);

      // 脱敏敏感的查询参数
      for (const [key] of params.entries()) {
        const lowerKey = key.toLowerCase();
        const isSensitiveParam = SENSITIVE_FIELD_NAMES.some(sensitiveField =>
          lowerKey.includes(sensitiveField)
        );

        if (isSensitiveParam) {
          params.set(key, '[REDACTED]');
        }
      }

      urlObj.search = params.toString();
      return urlObj.toString();
    } catch (_error) {
      // 如果不是有效 URL，直接返回脱敏后的字符串
      return this.sanitizeString(url);
    }
  }

  // 脱敏错误堆栈中的敏感信息
  sanitizeErrorStack(stack: string): string {
    if (!stack || typeof stack !== 'string') {
      return stack;
    }

    // 移除可能包含敏感信息的文件路径
    let sanitized = stack.replace(
      /file:\/\/\/[^\s)]+/g,
      'file:///[PATH_REDACTED]'
    );

    // 移除本地文件路径
    sanitized = sanitized.replace(
      /[A-Za-z]:\\[^\s)]+/g,
      '[LOCAL_PATH_REDACTED]'
    );

    // 移除用户目录路径
    sanitized = sanitized.replace(
      /\/Users\/[^/\s)]+/g,
      '/Users/[USER_REDACTED]'
    );

    sanitized = sanitized.replace(/\/home\/[^/\s)]+/g, '/home/[USER_REDACTED]');

    return this.sanitizeString(sanitized);
  }
}

// 导出脱敏器实例
export const dataSanitizer = DataSanitizer.getInstance();

// 安全日志记录器
export class SecureLogger {
  private sanitizer: DataSanitizer;

  constructor() {
    this.sanitizer = DataSanitizer.getInstance();
  }

  // 安全地记录信息
  secureLog(
    level: 'debug' | 'info' | 'warn' | 'error',
    message: string,
    context?: any
  ): void {
    const sanitizedMessage = this.sanitizer.sanitizeString(message);
    const sanitizedContext = context
      ? this.sanitizer.sanitizeObject(context)
      : undefined;

    logger[level](sanitizedMessage, sanitizedContext);
  }

  // 安全地记录错误
  secureLogError(error: Error, context?: any): void {
    const sanitizedError = {
      name: error.name,
      message: this.sanitizer.sanitizeString(error.message),
      stack: error.stack
        ? this.sanitizer.sanitizeErrorStack(error.stack)
        : undefined,
    };

    const sanitizedContext = context
      ? this.sanitizer.sanitizeObject(context)
      : undefined;

    logger.error('错误发生', {
      error: sanitizedError,
      context: sanitizedContext,
    });
  }

  // 安全地记录网络请求
  secureLogRequest(
    url: string,
    method: string,
    headers?: any,
    body?: any
  ): void {
    const sanitizedUrl = this.sanitizer.sanitizeUrl(url);
    const sanitizedHeaders = headers
      ? this.sanitizer.sanitizeObject(headers)
      : undefined;
    const sanitizedBody = body
      ? this.sanitizer.sanitizeObject(body)
      : undefined;

    logger.info('网络请求', {
      url: sanitizedUrl,
      method,
      headers: sanitizedHeaders,
      body: sanitizedBody,
    });
  }
}

// 导出安全日志记录器实例
export const secureLogger = new SecureLogger();

// 控制台重写保护
export class ConsoleProtection {
  private originalConsole: typeof console;
  private sanitizer: DataSanitizer;

  constructor() {
    this.originalConsole = { ...console };
    this.sanitizer = DataSanitizer.getInstance();
  }

  // 启用控制台保护
  enable(): void {
    const sanitizer = this.sanitizer;

    // 重写 console 方法
    const protectedMethods = ['log', 'info', 'warn', 'error', 'debug'] as const;
    const writableConsole = console as unknown as Record<
      (typeof protectedMethods)[number],
      (...args: any[]) => void
    >;

    protectedMethods.forEach(method => {
      // eslint-disable-next-line no-console
      if (typeof console[method] === 'function') {
        // eslint-disable-next-line no-console
        const originalMethod = console[method] as (...args: any[]) => void;

        writableConsole[method] = (...args: any[]) => {
          // 脱敏所有参数
          const sanitizedArgs = args.map(arg => {
            if (typeof arg === 'string') {
              return sanitizer.sanitizeString(arg);
            } else if (typeof arg === 'object') {
              return sanitizer.sanitizeObject(arg);
            }
            return arg;
          });

          // 调用原始方法
          originalMethod.apply(console, sanitizedArgs);
        };
      }
    });

    logger.info('控制台保护已启用');
  }

  // 禁用控制台保护
  disable(): void {
    // 恢复原始 console 方法
    Object.assign(console, this.originalConsole);
    logger.info('控制台保护已禁用');
  }
}

// 导出控制台保护实例
export const consoleProtection = new ConsoleProtection();

// 初始化敏感信息保护
export function initDataProtection(): void {
  // 在生产环境启用控制台保护
  if (import.meta.env.PROD) {
    consoleProtection.enable();
  }

  logger.info('敏感信息保护系统已初始化', {
    environment: import.meta.env.VITE_APP_ENV,
    consoleProtectionEnabled: import.meta.env.PROD,
  });
}

// 便捷函数
export const sanitize = {
  string: (input: string) => dataSanitizer.sanitizeString(input),
  object: (obj: any) => dataSanitizer.sanitizeObject(obj),
  url: (url: string) => dataSanitizer.sanitizeUrl(url),
  errorStack: (stack: string) => dataSanitizer.sanitizeErrorStack(stack),
};
