/**
 * 路由性能监控器
 * 监听路由变化并更新性能预算配置
 */

import { updatePerformanceBudgetsForRoute } from '@/config/performanceBudget';
import { logger } from '@/core/errors/logger';

class RoutePerformanceMonitor {
  private static instance: RoutePerformanceMonitor;
  private currentPath: string = '';
  private navigationStartTime: number = 0;

  static getInstance(): RoutePerformanceMonitor {
    if (!RoutePerformanceMonitor.instance) {
      RoutePerformanceMonitor.instance = new RoutePerformanceMonitor();
    }
    return RoutePerformanceMonitor.instance;
  }

  // 初始化路由监控
  init(): void {
    this.currentPath = window.location.pathname;
    this.setupNavigationListeners();

    logger.info('路由性能监控已初始化', {
      type: 'performance',
      action: 'route_monitor_init',
      path: this.currentPath,
    });
  }

  // 设置导航监听器
  private setupNavigationListeners(): void {
    // 监听 History API 变化
    this.wrapHistoryMethod('pushState');
    this.wrapHistoryMethod('replaceState');

    // 监听 popstate 事件 (浏览器前进/后退)
    window.addEventListener('popstate', () => {
      this.handleRouteChange();
    });

    // 监听 hashchange 事件
    window.addEventListener('hashchange', () => {
      this.handleRouteChange();
    });

    // 使用 MutationObserver 监听 DOM 变化 (适用于 SPA)
    const observer = new MutationObserver(() => {
      const newPath = window.location.pathname;
      if (newPath !== this.currentPath) {
        this.handleRouteChange();
      }
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  // 包装 History API 方法
  private wrapHistoryMethod(method: 'pushState' | 'replaceState'): void {
    const originalMethod = history[method];

    history[method] = (...args) => {
      // 记录导航开始时间
      this.navigationStartTime = performance.now();

      // 调用原方法
      originalMethod.apply(history, args);

      // 处理路由变化
      setTimeout(() => this.handleRouteChange(), 0);
    };
  }

  // 处理路由变化
  private handleRouteChange(): void {
    const newPath = window.location.pathname;
    const oldPath = this.currentPath;

    if (newPath === oldPath) return;

    // 计算导航时间
    const navigationTime =
      this.navigationStartTime > 0
        ? performance.now() - this.navigationStartTime
        : 0;

    // 记录路由变化
    logger.info('路由变化', {
      type: 'performance',
      action: 'route_change',
      from: oldPath,
      to: newPath,
      navigationTime: navigationTime > 0 ? navigationTime : undefined,
    });

    // 更新性能预算
    updatePerformanceBudgetsForRoute();

    // 更新当前路径
    this.currentPath = newPath;

    // 重置导航时间
    this.navigationStartTime = 0;

    // 在开发环境下输出路由性能信息
    if (import.meta.env.DEV) {
      logger.info(
        `🔄 路由变化: ${oldPath} → ${newPath}${navigationTime > 0 ? ` (${navigationTime.toFixed(2)}ms)` : ''}`
      );
    }
  }

  // 手动触发路由变化处理 (用于某些特殊情况)
  triggerRouteChange(): void {
    this.handleRouteChange();
  }

  // 获取当前路径
  getCurrentPath(): string {
    return this.currentPath;
  }
}

// 导出路由性能监控器实例
export const routePerformanceMonitor = RoutePerformanceMonitor.getInstance();

// 初始化路由性能监控
export function initRoutePerformanceMonitor(): void {
  routePerformanceMonitor.init();
}
