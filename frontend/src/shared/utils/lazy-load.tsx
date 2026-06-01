// 懒加载工具组件
/* eslint-disable react-refresh/only-export-components */
import React, { Suspense, type ComponentType } from 'react';

import { ErrorBoundary } from '@/components/ErrorBoundary';

// 加载中组件
export interface LoadingComponentProps {
  isLoading?: boolean;
  error?: Error | null;
  pastDelay?: boolean;
}

export const DefaultLoadingComponent: React.FC<LoadingComponentProps> = () => (
  <div className="flex items-center justify-center min-h-[200px]">
    <div className="flex flex-col items-center space-y-2">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      <p className="text-sm text-muted-foreground">加载中...</p>
    </div>
  </div>
);

// 错误回退组件
export const DefaultErrorComponent: React.FC<{ error: Error }> = ({
  error,
}) => (
  <div className="flex items-center justify-center min-h-[200px]">
    <div className="text-center space-y-2">
      <p className="text-destructive">加载失败</p>
      <p className="text-sm text-muted-foreground">{error.message}</p>
      <button
        onClick={() => window.location.reload()}
        className="text-sm text-primary underline"
      >
        重新加载
      </button>
    </div>
  </div>
);

// 懒加载选项
export interface LazyLoadOptions {
  loadingComponent?: ComponentType<LoadingComponentProps>;
  errorComponent?: ComponentType<{ error: Error }>;
  delay?: number;
  timeout?: number;
}

/**
 * 高阶组件：为懒加载组件添加加载状态和错误处理
 */
export function withLazyLoading<P extends object>(
  LazyComponent: React.LazyExoticComponent<ComponentType<P>>,
  options: LazyLoadOptions = {}
) {
  const {
    loadingComponent: LoadingComponent = DefaultLoadingComponent,
    errorComponent: ErrorComponent = DefaultErrorComponent,
  } = options;

  return function LazyLoadedComponent(props: P) {
    return (
      <ErrorBoundary
        fallback={<ErrorComponent error={new Error('组件加载失败')} />}
      >
        <Suspense fallback={<LoadingComponent isLoading={true} />}>
          <LazyComponent {...props} />
        </Suspense>
      </ErrorBoundary>
    );
  };
}

/**
 * 创建懒加载组件的便捷函数
 */
export function createLazyComponent<P extends object>(
  importFn: () => Promise<{ default: ComponentType<P> }>,
  options?: LazyLoadOptions
) {
  const LazyComponent = React.lazy(importFn);
  return withLazyLoading(LazyComponent, options);
}

/**
 * 预加载组件
 */
export function preloadComponent<P extends object>(
  importFn: () => Promise<{ default: ComponentType<P> }>
): void {
  // 延迟预加载，避免阻塞初始渲染
  setTimeout(() => {
    importFn().catch(() => {
      // 预加载失败,静默处理
    });
  }, 100);
}

/**
 * 路由级别的懒加载组件
 */
export const createLazyRoute = <P extends object>(
  importFn: () => Promise<{ default: ComponentType<P> }>,
  routeName: string
) => {
  return createLazyComponent(importFn, {
    loadingComponent: () => (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center space-y-4">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto"></div>
          <p className="text-lg font-medium">加载{routeName}中...</p>
        </div>
      </div>
    ),
    errorComponent: ({ error }) => (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center space-y-4 max-w-md">
          <h2 className="text-xl font-semibold text-destructive">
            {routeName}加载失败
          </h2>
          <p className="text-muted-foreground">{error.message}</p>
          <div className="space-x-4">
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md"
            >
              重新加载
            </button>
            <button
              onClick={() => window.history.back()}
              className="px-4 py-2 border border-border rounded-md"
            >
              返回上一页
            </button>
          </div>
        </div>
      </div>
    ),
  });
};
