// 性能监控工具
import React from 'react';
import { onCLS, onFCP, onLCP, onTTFB, onINP } from 'web-vitals';

import { logger } from '@/core/errors/logger';

export interface PerformanceMetric {
  name: string;
  value: number;
  delta: number;
  id: string;
  rating: 'good' | 'needs-improvement' | 'poor';
  timestamp: number;
}

export interface RoutePerformance {
  route: string;
  loadTime: number;
  renderTime: number;
  timestamp: number;
}

// 性能指标收集器
class PerformanceCollector {
  private metrics: PerformanceMetric[] = [];
  private routeMetrics: RoutePerformance[] = [];
  private observers: Map<string, PerformanceObserver> = new Map();

  constructor() {
    this.initWebVitals();
    this.initRoutePerformance();
    this.initResourceObserver();
  }

  // 初始化 Web Vitals
  private initWebVitals() {
    const onMetric = (metric: PerformanceMetric) => {
      const performanceMetric: PerformanceMetric = {
        name: metric.name,
        value: metric.value,
        delta: metric.delta,
        id: metric.id,
        rating: metric.rating,
        timestamp: Date.now(),
      };

      this.metrics.push(performanceMetric);
      this.reportMetric(performanceMetric);

      // 开发环境在控制台显示
      if (import.meta.env.DEV) {
        logger.info('📊 Web Vital:', performanceMetric);
      }
    };

    onCLS(onMetric);
    onFCP(onMetric);
    onLCP(onMetric);
    onTTFB(onMetric);
    onINP(onMetric);
  }

  // 初始化路由性能监控
  private initRoutePerformance() {
    const startTime = performance.now();

    // 监听路由变化（针对 SPA）
    const originalPushState = history.pushState;
    const originalReplaceState = history.replaceState;

    history.pushState = (...args) => {
      this.recordRouteChange();
      return originalPushState.apply(history, args);
    };

    history.replaceState = (...args) => {
      this.recordRouteChange();
      return originalReplaceState.apply(history, args);
    };

    window.addEventListener('popstate', () => {
      this.recordRouteChange();
    });

    // 监听页面加载完成
    window.addEventListener('load', () => {
      const loadTime = performance.now() - startTime;
      this.recordRoutePerformance(window.location.pathname, loadTime, 0);
    });
  }

  // 记录路由变化
  private recordRouteChange() {
    // 这里可以记录路由切换的性能数据
  }

  // 记录路由性能
  private recordRoutePerformance(
    route: string,
    loadTime: number,
    renderTime: number
  ) {
    const routeMetric: RoutePerformance = {
      route,
      loadTime,
      renderTime,
      timestamp: Date.now(),
    };

    this.routeMetrics.push(routeMetric);

    if (import.meta.env.DEV) {
      logger.info('🛣️ Route Performance:', routeMetric);
    }
  }

  // 初始化资源监控
  private initResourceObserver() {
    if ('PerformanceObserver' in window) {
      // 监控资源加载
      const resourceObserver = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (entry.entryType === 'resource') {
            this.analyzeResourcePerformance(entry as PerformanceResourceTiming);
          }
        }
      });

      resourceObserver.observe({ entryTypes: ['resource'] });
      this.observers.set('resource', resourceObserver);

      // 监控长任务
      const longTaskObserver = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (entry.entryType === 'longtask') {
            this.analyzeLongTask(entry);
          }
        }
      });

      try {
        longTaskObserver.observe({ entryTypes: ['longtask'] });
        this.observers.set('longtask', longTaskObserver);
      } catch {
        // Long task API 可能不支持,静默处理
      }
    }
  }

  // 分析资源性能
  private analyzeResourcePerformance(entry: PerformanceResourceTiming) {
    const duration = entry.responseEnd - entry.startTime;

    // 在开发环境检查慢资源和大文件
    if (import.meta.env.DEV) {
      if (duration > 1000) {
        // 超过1秒的资源
        // eslint-disable-next-line no-console
        console.warn('🐌 Slow Resource:', {
          name: entry.name,
          duration: duration.toFixed(2) + 'ms',
          size: entry.transferSize || 'unknown',
          type: this.getResourceType(entry.name),
        });
      }

      if (entry.transferSize && entry.transferSize > 1024 * 1024) {
        // 超过1MB
        // eslint-disable-next-line no-console
        console.warn('📦 Large Resource:', {
          name: entry.name,
          size: (entry.transferSize / 1024 / 1024).toFixed(2) + 'MB',
          duration: duration.toFixed(2) + 'ms',
        });
      }
    }
  }

  // 分析长任务
  private analyzeLongTask(entry: PerformanceEntry) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn('⏱️ Long Task:', {
        duration: entry.duration.toFixed(2) + 'ms',
        startTime: entry.startTime.toFixed(2) + 'ms',
      });
    }
  }

  // 获取资源类型
  private getResourceType(url: string): string {
    if (url.includes('.js')) return 'script';
    if (url.includes('.css')) return 'stylesheet';
    if (url.match(/\.(png|jpg|jpeg|gif|svg|webp)$/)) return 'image';
    if (url.match(/\.(woff|woff2|ttf|eot)$/)) return 'font';
    return 'other';
  }

  // 上报指标
  private reportMetric(_metric: PerformanceMetric) {
    // 生产环境可以发送到监控服务
    if (!import.meta.env.DEV) {
      // sendToAnalytics(metric);
    }
  }

  // 获取性能报告
  public getPerformanceReport() {
    return {
      vitals: this.metrics,
      routes: this.routeMetrics,
      summary: this.generateSummary(),
    };
  }

  // 生成性能摘要
  private generateSummary() {
    const vitals = this.metrics.reduce(
      (acc, metric) => {
        acc[metric.name] = {
          value: metric.value,
          rating: metric.rating,
        };
        return acc;
      },
      {} as Record<string, { value: number; rating: string }>
    );

    const avgRouteLoadTime =
      this.routeMetrics.reduce((sum, route) => sum + route.loadTime, 0) /
        this.routeMetrics.length || 0;

    return {
      vitals,
      avgRouteLoadTime: avgRouteLoadTime.toFixed(2) + 'ms',
      routeCount: this.routeMetrics.length,
    };
  }

  // 清理监听器
  public cleanup() {
    this.observers.forEach(observer => {
      observer.disconnect();
    });
    this.observers.clear();
  }
}

// 单例实例
let performanceCollector: PerformanceCollector | null = null;

/**
 * 初始化性能监控
 */
export function initPerformanceMonitoring(): PerformanceCollector {
  if (!performanceCollector) {
    performanceCollector = new PerformanceCollector();
  }
  return performanceCollector;
}

/**
 * 获取性能报告
 */
export function getPerformanceReport() {
  return performanceCollector?.getPerformanceReport() || null;
}

/**
 * 测量函数执行时间
 */
export function measureExecutionTime<T>(name: string, fn: () => T): T {
  const start = performance.now();
  const result = fn();
  const end = performance.now();
  const duration = end - start;

  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.log(`⏱️ ${name}: ${duration.toFixed(2)}ms`);
  }

  return result;
}

/**
 * 测量异步函数执行时间
 */
export async function measureAsyncExecutionTime<T>(
  name: string,
  fn: () => Promise<T>
): Promise<T> {
  const start = performance.now();
  const result = await fn();
  const end = performance.now();
  const duration = end - start;

  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.log(`⏱️ ${name}: ${duration.toFixed(2)}ms`);
  }

  return result;
}

/**
 * React 组件性能监控 Hook
 */
export function usePerformanceMonitor(componentName: string) {
  const startTime = performance.now();

  React.useEffect(() => {
    const endTime = performance.now();
    const renderTime = endTime - startTime;

    if (import.meta.env.DEV && renderTime > 16) {
      // 超过一帧的时间
      // eslint-disable-next-line no-console
      console.warn(
        `🐌 Slow Render: ${componentName} took ${renderTime.toFixed(2)}ms`
      );
    }
  });
}
