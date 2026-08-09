/**
 * Web Vitals 性能监控系统
 * 基于 Google 的 Core Web Vitals 指标
 */

import { onCLS, onFCP, onINP, onLCP, onTTFB, type Metric } from 'web-vitals';

import { logger } from '@/core/errors/logger';

// 性能指标类型
export interface PerformanceMetric {
  name: string;
  value: number;
  rating: 'good' | 'needs-improvement' | 'poor';
  delta: number;
  id: string;
  entries: PerformanceEntry[];
  timestamp: number;
  url: string;
  userAgent?: string;
}

// 性能阈值配置 (基于 Google 建议)
const PERFORMANCE_THRESHOLDS = {
  // Cumulative Layout Shift (累积布局偏移)
  CLS: {
    good: 0.1,
    poor: 0.25,
  },
  // First Contentful Paint (首次内容绘制)
  FCP: {
    good: 1800,
    poor: 3000,
  },
  // Interaction to Next Paint (交互到下次绘制)
  INP: {
    good: 200,
    poor: 500,
  },
  // Largest Contentful Paint (最大内容绘制)
  LCP: {
    good: 2500,
    poor: 4000,
  },
  // Time to First Byte (首字节时间)
  TTFB: {
    good: 800,
    poor: 1800,
  },
};

// 性能评级函数
function getRating(
  name: string,
  value: number
): 'good' | 'needs-improvement' | 'poor' {
  const thresholds =
    PERFORMANCE_THRESHOLDS[name as keyof typeof PERFORMANCE_THRESHOLDS];
  if (!thresholds) return 'good';

  if (value <= thresholds.good) return 'good';
  if (value <= thresholds.poor) return 'needs-improvement';
  return 'poor';
}

// 性能数据处理器
class PerformanceMonitor {
  private static instance: PerformanceMonitor;
  private metrics: PerformanceMetric[] = [];
  private listeners: ((metric: PerformanceMetric) => void)[] = [];

  static getInstance(): PerformanceMonitor {
    if (!PerformanceMonitor.instance) {
      PerformanceMonitor.instance = new PerformanceMonitor();
    }
    return PerformanceMonitor.instance;
  }

  // 处理 Web Vitals 指标
  handleMetric(metric: Metric): void {
    const performanceMetric: PerformanceMetric = {
      name: metric.name,
      value: metric.value,
      rating: getRating(metric.name, metric.value),
      delta: metric.delta,
      id: metric.id,
      entries: metric.entries as PerformanceEntry[],
      timestamp: Date.now(),
      url: window.location.href,
      userAgent: navigator.userAgent,
    };

    // 添加到指标列表
    this.metrics.push(performanceMetric);

    // 记录到日志系统
    this.logMetric(performanceMetric);

    // 通知监听器
    this.notifyListeners(performanceMetric);

    // 生产环境上报
    if (import.meta.env.PROD) {
      this.reportMetric(performanceMetric);
    }
  }

  // 添加监听器
  addListener(listener: (metric: PerformanceMetric) => void): () => void {
    this.listeners.push(listener);

    return () => {
      const index = this.listeners.indexOf(listener);
      if (index > -1) {
        this.listeners.splice(index, 1);
      }
    };
  }

  // 获取性能指标
  getMetrics(): PerformanceMetric[] {
    return [...this.metrics];
  }

  // 获取指定类型的最新指标
  getLatestMetric(name: string): PerformanceMetric | undefined {
    return this.metrics
      .filter(metric => metric.name === name)
      .sort((a, b) => b.timestamp - a.timestamp)[0];
  }

  // 获取性能摘要
  getPerformanceSummary(): {
    total: number;
    good: number;
    needsImprovement: number;
    poor: number;
    metrics: Record<string, PerformanceMetric>;
  } {
    const latestMetrics: Record<string, PerformanceMetric> = {};

    // 获取每种类型的最新指标
    (['CLS', 'FCP', 'INP', 'LCP', 'TTFB'] as const).forEach(name => {
      const metric = this.getLatestMetric(name);
      if (metric) {
        latestMetrics[name] = metric;
      }
    });

    const metrics = Object.values(latestMetrics);
    const total = metrics.length;
    const good = metrics.filter(m => m.rating === 'good').length;
    const needsImprovement = metrics.filter(
      m => m.rating === 'needs-improvement'
    ).length;
    const poor = metrics.filter(m => m.rating === 'poor').length;

    return {
      total,
      good,
      needsImprovement,
      poor,
      metrics: latestMetrics,
    };
  }

  private logMetric(metric: PerformanceMetric): void {
    const logLevel = metric.rating === 'poor' ? 'warn' : 'info';
    const message = `Web Vital: ${metric.name} = ${metric.value.toFixed(2)} (${metric.rating})`;

    logger[logLevel](message, {
      type: 'performance',
      metric: metric.name,
      value: metric.value,
      rating: metric.rating,
      delta: metric.delta,
      url: metric.url,
    });
  }

  private notifyListeners(metric: PerformanceMetric): void {
    this.listeners.forEach(listener => {
      try {
        listener(metric);
      } catch (error) {
        logger.error('性能监听器执行失败:', error);
      }
    });
  }

  private async reportMetric(metric: PerformanceMetric): Promise<void> {
    try {
      // 这里可以发送到性能分析服务
      // 比如 Google Analytics, DataDog, 自建服务等

      // 示例：发送到自定义性能收集端点
      // await fetch('/api/performance', {
      //   method: 'POST',
      //   headers: {
      //     'Content-Type': 'application/json',
      //   },
      //   body: JSON.stringify(metric),
      // })

      logger.info('性能指标已上报:', {
        metric: metric.name,
        value: metric.value,
      });
    } catch (error) {
      logger.error('性能指标上报失败:', error);
    }
  }
}

// 初始化 Web Vitals 监控
export function initWebVitals(): void {
  const monitor = PerformanceMonitor.getInstance();

  // 监听所有 Core Web Vitals 指标
  onCLS(metric => monitor.handleMetric(metric));
  onFCP(metric => monitor.handleMetric(metric));
  onINP(metric => monitor.handleMetric(metric));
  onLCP(metric => monitor.handleMetric(metric));
  onTTFB(metric => monitor.handleMetric(metric));

  logger.info('Web Vitals 性能监控已初始化', {
    type: 'performance',
    action: 'init',
  });
}

// 导出性能监控实例
export const performanceMonitor = PerformanceMonitor.getInstance();

// 便捷函数
export const webVitals = {
  // 获取性能摘要
  getSummary: () => performanceMonitor.getPerformanceSummary(),

  // 获取所有指标
  getMetrics: () => performanceMonitor.getMetrics(),

  // 获取特定指标
  getMetric: (name: string) => performanceMonitor.getLatestMetric(name),

  // 添加监听器
  addListener: (listener: (metric: PerformanceMetric) => void) =>
    performanceMonitor.addListener(listener),
};

// 性能预算检查器
export class PerformanceBudgetChecker {
  private budgets: Record<string, number> = {};

  // 设置性能预算
  setBudget(metric: string, threshold: number): void {
    this.budgets[metric] = threshold;
  }

  // 检查是否超出预算
  checkBudget(metric: PerformanceMetric): {
    withinBudget: boolean;
    budget?: number;
    overage?: number;
  } {
    const budget = this.budgets[metric.name];

    if (budget === undefined) {
      return { withinBudget: true };
    }

    const withinBudget = metric.value <= budget;
    const overage = withinBudget ? 0 : metric.value - budget;

    return {
      withinBudget,
      budget,
      overage,
    };
  }

  // 获取所有预算违规
  getBudgetViolations(): Array<{
    metric: PerformanceMetric;
    budget: number;
    overage: number;
  }> {
    const violations: Array<{
      metric: PerformanceMetric;
      budget: number;
      overage: number;
    }> = [];

    const summary = performanceMonitor.getPerformanceSummary();

    Object.values(summary.metrics).forEach(metric => {
      const check = this.checkBudget(metric);
      if (!check.withinBudget && check.budget && check.overage) {
        violations.push({
          metric,
          budget: check.budget,
          overage: check.overage,
        });
      }
    });

    return violations;
  }
}

// 导出性能预算检查器实例
export const performanceBudget = new PerformanceBudgetChecker();
