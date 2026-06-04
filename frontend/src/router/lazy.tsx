/* eslint-disable react-refresh/only-export-components */
import React, { Suspense, type ComponentType } from 'react';
import type { RouteComponentProps } from 'wouter';

import ErrorBoundary from '@/components/ErrorBoundary';

import { RouteSkeleton, type RouteSkeletonVariant } from './skeletons';

export type RouteComponent = ComponentType<RouteComponentProps>;
export type RouteImporter = () => Promise<{ default: RouteComponent }>;

const preloadCache = new WeakMap<RouteImporter, Promise<void>>();

function RouteLoadError({ routeName }: { routeName: string }) {
  return (
    <div className="flex min-h-[18rem] items-center justify-center">
      <div className="max-w-md rounded-lg border border-rose-200 bg-rose-50 px-6 py-5 text-center shadow-sm dark:border-rose-900/60 dark:bg-rose-950/20">
        <h2 className="text-base font-bold text-rose-600 dark:text-rose-300">
          {routeName}加载失败
        </h2>
        <p className="mt-2 text-sm text-rose-600/80 dark:text-rose-200/70">
          请刷新页面后重试。
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-4 rounded-md bg-rose-600 px-4 py-2 text-sm font-bold text-white transition-colors hover:bg-rose-500"
        >
          重新加载
        </button>
      </div>
    </div>
  );
}

export function preloadImporter(importer: RouteImporter): Promise<void> {
  const cached = preloadCache.get(importer);
  if (cached) return cached;

  const promise = importer()
    .then(() => undefined)
    .catch(error => {
      preloadCache.delete(importer);
      throw error;
    });
  preloadCache.set(importer, promise);
  return promise;
}

export function createLazyRoute(
  importer: RouteImporter,
  routeName: string,
  skeleton: RouteSkeletonVariant = 'default'
) {
  const LazyComponent = React.lazy(importer);

  function LazyRouteComponent(props: RouteComponentProps) {
    return (
      <ErrorBoundary fallback={<RouteLoadError routeName={routeName} />}>
        <Suspense fallback={<RouteSkeleton variant={skeleton} />}>
          <LazyComponent {...props} />
        </Suspense>
      </ErrorBoundary>
    );
  }

  LazyRouteComponent.displayName = `LazyRoute(${routeName})`;
  return LazyRouteComponent;
}
