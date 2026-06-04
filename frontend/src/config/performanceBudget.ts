/**
 * 性能预算配置
 * 为 QuantX 应用定义合理的性能目标
 */
/* eslint-disable no-console */

import { logger } from '@/core/errors/logger';
import { performanceBudget } from '@/core/performance/web-vitals';

// 环境特定的性能预算配置
interface PerformanceBudgetConfig {
  // Core Web Vitals 预算 (基于 Google 建议的良好标准)
  CLS: number; // Cumulative Layout Shift - 累积布局偏移
  FCP: number; // First Contentful Paint - 首次内容绘制 (ms)
  INP: number; // Interaction to Next Paint - 交互到下次绘制 (ms)
  LCP: number; // Largest Contentful Paint - 最大内容绘制 (ms)
  TTFB: number; // Time to First Byte - 首字节时间 (ms)
}

// 不同环境的性能预算
const PERFORMANCE_BUDGETS: Record<string, PerformanceBudgetConfig> = {
  // 开发环境 - 相对宽松的预算
  development: {
    CLS: 0.15, // 允许稍微多一点的布局偏移
    FCP: 2000, // 2秒内首次内容绘制
    INP: 250, // 250ms 内交互响应
    LCP: 3000, // 3秒内最大内容绘制
    TTFB: 1000, // 1秒内首字节响应
  },

  // 测试环境 - 接近生产的预算
  staging: {
    CLS: 0.1, // Google 建议的良好标准
    FCP: 1800,
    INP: 200,
    LCP: 2500,
    TTFB: 800,
  },

  // 生产环境 - 严格的性能预算
  production: {
    CLS: 0.1, // Google 建议的良好标准
    FCP: 1500, // 更严格的标准
    INP: 200,
    LCP: 2500,
    TTFB: 600, // 更快的服务器响应要求
  },
};

// 特定页面的性能预算覆盖
const PAGE_SPECIFIC_BUDGETS: Record<
  string,
  Partial<PerformanceBudgetConfig>
> = {
  // 仪表板页面 - 数据密集型，允许稍慢的 LCP
  '/': {
    LCP: 3000,
    FCP: 2000,
  },

  // 交易页面 - 交互密集型，严格的交互指标
  '/holdings': {
    INP: 100,
    CLS: 0.05, // 交易界面需要极稳定的布局
  },

  // 股票详情页 - 内容丰富，允许稍慢的加载
  '/stock/*': {
    LCP: 3500,
    FCP: 2200,
  },

  // 策略页面 - 表格密集型
  '/strategies': {
    LCP: 2800,
    CLS: 0.08,
  },
};

interface PerformanceBudgetCompliance {
  totalViolations: number;
  violations: Array<{
    metric: string;
    current: number;
    budget: number;
    overage: number;
    severity: 'minor' | 'major' | 'critical';
  }>;
}

// 性能预算管理器
class PerformanceBudgetManager {
  private currentEnvironment: string;
  private appliedBudgets: PerformanceBudgetConfig;

  constructor() {
    this.currentEnvironment = import.meta.env.VITE_APP_ENV || 'development';
    this.appliedBudgets = this.calculateBudgets();
  }

  // 计算当前页面和环境的性能预算
  private calculateBudgets(): PerformanceBudgetConfig {
    const baseBudgets =
      PERFORMANCE_BUDGETS[this.currentEnvironment] ||
      PERFORMANCE_BUDGETS.development;
    const currentPath = window.location.pathname;

    // 检查是否有页面特定的预算覆盖
    let pageOverrides: Partial<PerformanceBudgetConfig> = {};

    Object.entries(PAGE_SPECIFIC_BUDGETS).forEach(([pattern, overrides]) => {
      if (this.matchesPath(currentPath, pattern)) {
        pageOverrides = { ...pageOverrides, ...overrides };
      }
    });

    return { ...baseBudgets, ...pageOverrides };
  }

  // 路径匹配函数
  private matchesPath(path: string, pattern: string): boolean {
    if (pattern.endsWith('*')) {
      const prefix = pattern.slice(0, -1);
      return path.startsWith(prefix);
    }
    return path === pattern;
  }

  // 初始化性能预算
  initializeBudgets(): void {
    Object.entries(this.appliedBudgets).forEach(([metric, threshold]) => {
      performanceBudget.setBudget(metric, threshold);
    });

    logger.info('性能预算已配置', {
      type: 'performance',
      action: 'budget_init',
      environment: this.currentEnvironment,
      budgets: this.appliedBudgets,
      path: window.location.pathname,
    });

    // 在开发环境下输出预算信息
    if (import.meta.env.DEV) {
      this.logBudgetInfo();
    }
  }

  // 重新计算并应用预算（路由变化时调用）
  updateBudgetsForRoute(): void {
    this.appliedBudgets = this.calculateBudgets();
    this.initializeBudgets();
  }

  // 获取当前应用的预算
  getCurrentBudgets(): PerformanceBudgetConfig {
    return { ...this.appliedBudgets };
  }

  // 检查预算合规性
  checkCompliance(): PerformanceBudgetCompliance {
    const violations = performanceBudget.getBudgetViolations();

    const formattedViolations = violations.map(violation => {
      const overage = violation.overage;
      const overagePercentage = (overage / violation.budget) * 100;

      let severity: 'minor' | 'major' | 'critical';
      if (overagePercentage <= 10) {
        severity = 'minor';
      } else if (overagePercentage <= 50) {
        severity = 'major';
      } else {
        severity = 'critical';
      }

      return {
        metric: violation.metric.name,
        current: violation.metric.value,
        budget: violation.budget,
        overage,
        severity,
      };
    });

    return {
      totalViolations: violations.length,
      violations: formattedViolations,
    };
  }

  // 生成性能报告
  generateReport(): {
    environment: string;
    path: string;
    budgets: PerformanceBudgetConfig;
    compliance: PerformanceBudgetCompliance;
    timestamp: string;
  } {
    return {
      environment: this.currentEnvironment,
      path: window.location.pathname,
      budgets: this.getCurrentBudgets(),
      compliance: this.checkCompliance(),
      timestamp: new Date().toISOString(),
    };
  }

  private logBudgetInfo(): void {
    console.group('💰 性能预算配置');
    console.log('环境:', this.currentEnvironment);
    console.log('页面:', window.location.pathname);
    console.table(
      Object.entries(this.appliedBudgets).map(([metric, threshold]) => ({
        指标: metric,
        预算: threshold,
        单位: this.getMetricUnit(metric),
        说明: this.getMetricDescription(metric),
      }))
    );
    console.groupEnd();
  }

  private getMetricUnit(metric: string): string {
    switch (metric) {
      case 'CLS':
        return '';
      case 'FCP':
      case 'INP':
      case 'LCP':
      case 'TTFB':
        return 'ms';
      default:
        return '';
    }
  }

  private getMetricDescription(metric: string): string {
    switch (metric) {
      case 'CLS':
        return '累积布局偏移';
      case 'FCP':
        return '首次内容绘制';
      case 'INP':
        return '交互到下次绘制';
      case 'LCP':
        return '最大内容绘制';
      case 'TTFB':
        return '首字节时间';
      default:
        return '';
    }
  }
}

// 导出性能预算管理器实例
export const performanceBudgetManager = new PerformanceBudgetManager();

// 初始化性能预算
export function initPerformanceBudgets(): void {
  performanceBudgetManager.initializeBudgets();
}

// 路由变化时更新预算
export function updatePerformanceBudgetsForRoute(): void {
  performanceBudgetManager.updateBudgetsForRoute();
}

// 导出配置
export { PERFORMANCE_BUDGETS, PAGE_SPECIFIC_BUDGETS };
export type { PerformanceBudgetConfig };
