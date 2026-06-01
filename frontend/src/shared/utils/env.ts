// 环境变量管理工具
import { z } from 'zod';

import { logger } from '@/core/errors/logger';

// 环境变量模式定义
const envSchema = z.object({
  // 基础配置
  NODE_ENV: z
    .enum(['development', 'production', 'test'])
    .default('development'),

  // API 配置
  VITE_GRAPHQL_HTTP_URL: z.string().default('/graphql'),
  VITE_GRAPHQL_WS_URL: z.string().default(''),

  // 应用配置
  VITE_APP_TITLE: z.string().default('QuantX'),
  VITE_APP_VERSION: z.string().default('1.0.0'),
  VITE_APP_ENV: z
    .enum(['development', 'dev', 'staging', 'prod'])
    .default('development'),

  // 功能开关
  VITE_ENABLE_DEBUG: z
    .string()
    .transform(val => val === 'true')
    .default('false'),
  VITE_ENABLE_ANALYTICS: z
    .string()
    .transform(val => val === 'true')
    .default('false'),
  VITE_ENABLE_ERROR_REPORTING: z
    .string()
    .transform(val => val === 'true')
    .default('false'),

  // 性能配置
  VITE_PERFORMANCE_BUDGET_JS: z
    .string()
    .transform(val => parseInt(val) || 500)
    .default('500'),
  VITE_PERFORMANCE_BUDGET_CSS: z
    .string()
    .transform(val => parseInt(val) || 100)
    .default('100'),
  VITE_PERFORMANCE_BUDGET_ASSETS: z
    .string()
    .transform(val => parseInt(val) || 2000)
    .default('2000'),

  // 外部服务
  VITE_SENTRY_DSN: z.string().optional(),
  VITE_GOOGLE_ANALYTICS_ID: z.string().optional(),

  // 开发配置
  VITE_HMR_PORT: z
    .string()
    .transform(val => parseInt(val) || 24678)
    .default('24678'),
  VITE_PROXY_TARGET: z.string().url().optional(),
});

// 环境变量类型
export type Env = z.infer<typeof envSchema>;

// 验证和解析环境变量
function validateEnv(): Env {
  const rawEnv = {
    NODE_ENV: import.meta.env.MODE, // 'development' | 'production' | 'test'
    ...import.meta.env,
  };

  try {
    return envSchema.parse(rawEnv);
  } catch (error) {
    logger.error('❌ 环境变量验证失败:');
    if (error instanceof z.ZodError) {
      error.errors.forEach(err => {
        logger.error(`  - ${err.path.join('.')}: ${err.message}`);
      });
    }

    // 开发环境显示详细错误，生产环境使用默认值
    if (import.meta.env.DEV) {
      throw new Error('环境变量配置错误，请检查 .env 文件');
    } else {
      logger.warn('⚠️ 使用默认环境变量配置');
      return envSchema.parse({});
    }
  }
}

// 导出验证后的环境变量
export const env = validateEnv();

// 环境判断工具
export const isDevelopment = env.NODE_ENV === 'development';
export const isProduction = env.NODE_ENV === 'production';
export const isTest = env.NODE_ENV === 'test';

// 应用环境判断
export const isDevEnv =
  env.VITE_APP_ENV === 'development' || env.VITE_APP_ENV === 'dev';
export const isStagingEnv = env.VITE_APP_ENV === 'staging';
export const isProdEnv = env.VITE_APP_ENV === 'prod';

// API URL 配置
export const apiConfig = {
  graphqlURL: env.VITE_GRAPHQL_HTTP_URL,
  wsURL: env.VITE_GRAPHQL_WS_URL,
  timeout: 10000,
} as const;

// 功能开关
export const features = {
  enableDebug: env.VITE_ENABLE_DEBUG || isDevelopment,
  enableAnalytics: env.VITE_ENABLE_ANALYTICS && isProduction,
  enableErrorReporting: env.VITE_ENABLE_ERROR_REPORTING && isProduction,
} as const;

// 性能预算
export const performanceBudget = {
  javascript: env.VITE_PERFORMANCE_BUDGET_JS, // KB
  css: env.VITE_PERFORMANCE_BUDGET_CSS, // KB
  assets: env.VITE_PERFORMANCE_BUDGET_ASSETS, // KB
} as const;

// 开发配置
export const devConfig = {
  hmrPort: env.VITE_HMR_PORT,
  proxyTarget: env.VITE_PROXY_TARGET,
} as const;

// 外部服务配置
export const externalServices = {
  sentryDsn: env.VITE_SENTRY_DSN,
  googleAnalyticsId: env.VITE_GOOGLE_ANALYTICS_ID,
} as const;

// 应用信息
export const appInfo = {
  title: env.VITE_APP_TITLE,
  version: env.VITE_APP_VERSION,
  environment: env.VITE_APP_ENV,
  buildTime: new Date().toISOString(),
} as const;

// 环境检查函数
export function checkRequiredEnvVars(): boolean {
  const requiredVars = ['VITE_GRAPHQL_HTTP_URL'];

  const missing = requiredVars.filter(varName => {
    const value = import.meta.env[varName];
    return !value || value === '';
  });

  if (missing.length > 0) {
    logger.error('❌ 缺少必需的环境变量:', missing);
    return false;
  }

  return true;
}

// 打印环境信息（仅开发环境）
export function printEnvInfo(): void {
  if (!isDevelopment) return;

  logger.info('🌍 环境配置信息', {
    应用环境: env.VITE_APP_ENV,
    Node环境: env.NODE_ENV,
    GraphQL地址: env.VITE_GRAPHQL_HTTP_URL,
    WebSocket地址: env.VITE_GRAPHQL_WS_URL,
    功能开关: features,
    性能预算: performanceBudget,
  });
}

// 获取环境特定的配置
export function getEnvSpecificConfig<T>(configs: {
  development?: T;
  staging?: T;
  production?: T;
  default: T;
}): T {
  switch (env.VITE_APP_ENV) {
    case 'dev':
      return configs.development || configs.default;
    case 'staging':
      return configs.staging || configs.default;
    case 'prod':
      return configs.production || configs.default;
    default:
      return configs.default;
  }
}

// 安全检查：确保敏感信息不会泄露到客户端
export function validateClientSideEnv(): void {
  const sensitivePatterns = [
    /password/i,
    /secret/i,
    /key/i,
    /token/i,
    /private/i,
  ];

  const envVars = import.meta.env;
  const exposedSensitive = Object.keys(envVars).filter(key => {
    // 只检查 VITE_ 开头的变量（这些会暴露到客户端）
    if (!key.startsWith('VITE_')) return false;

    return sensitivePatterns.some(pattern => pattern.test(key));
  });

  if (exposedSensitive.length > 0) {
    logger.warn('⚠️ 发现可能包含敏感信息的环境变量:', exposedSensitive);
    logger.warn('请确保这些变量不包含敏感数据，因为它们会暴露到客户端');
  }
}
